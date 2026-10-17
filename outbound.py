#!/usr/bin/env python3
"""Founder's Office outbound. One file, stdlib only. State lives in queue.csv. See REQUIREMENTS.md.

    python outbound.py draft apollo.csv   # Apollo People export -> queue.csv
    python outbound.py cycle [--dry]      # inbox + send + followup + notify (schedule this twice a day)
    python outbound.py send|followup|notify|inbox [--dry]
    python outbound.py wa                 # print WhatsApp links that are due

Env: GMAIL_USER, GMAIL_APP_PASSWORD (Gmail IMAP enabled). Nothing is sent without approved=y.
"""
import argparse
import csv
import email
import imaplib
import os
import random
import re
import smtplib
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

# ---- Config ----------------------------------------------------------------
DAILY_CAP = 20                 # max sends per day once warmed up
RAMP_DAYS, RAMP_CAP = 3, 5     # first 3 days (counted from first send): 5 per day
FU1_DAYS, FU2_DAYS, WA_DAYS = 5, 12, 3
GAP_SECONDS = (120, 300)       # random gap between sends
BOUNCE_RATE, BOUNCE_MIN_SAMPLE = 0.03, 10   # stop above 3% (or on 2+ bounces) once sample is meaningful
REFILL_BELOW = 10
RESUME = "https://tinyurl.com/at-nitrr-resume"
IST = timezone(timedelta(hours=5, minutes=30))
DIGEST_PREFIX = "[Outbound] Action needed"
QUEUE = Path(os.environ.get("OUTBOUND_QUEUE", Path(__file__).resolve().parent / "queue.csv"))

COLUMNS = ["approved", "email", "first", "company", "title", "phone", "about", "line", "subject", "body",
           "fu1", "fu2", "wa_link", "sent_at", "fu1_at", "fu2_at", "wa_shown", "message_id", "replied",
           "company_li", "overlap_note", "suggested_line", "pitch"]  # last four: added for the shortlist / overlap flow
CLOSED = {"y", "skip", "bounced"}  # values of `replied` that stop the sequence

# ---- Copy (REQUIREMENTS section 7). No em dashes. Rs not the rupee sign, to stay plain ASCII-safe. ----
ACHIEVEMENTS = [
    "I built a BD desk from zero to a Rs 40 Cr GMV quarter and took conversion from 7% to 20%.",
    "At AlgoUniversity (YC S21) I took a vertical to Rs 9 Cr and personally closed Rs 2.7 Cr at 40% conversion.",
    "I turn messy pipelines into revenue systems: Rs 9 Cr at AlgoUniversity, Rs 40 Cr GMV at Enout.",
]
SUBJECTS = ["Founder's Office at {company}", "{company} and a BD system that runs itself"]
DEFAULT_LINE = "I came across {company} and wanted to reach out directly."
BODY = ("Hi {first},\n\n{line}\n\n{achievement}\n\n"
        "Most early teams need someone to own revenue and the system behind it before hiring a full desk. "
        "Open to a 15-minute call to see if that is useful for {company}?\n\nAyush")
FU1 = "Hi {first}, one more thing that may be useful for {company}: {achievement2}\n\nHappy to walk you through how.\n\nAyush"
FU2 = ("Hi {first}, closing the loop. My resume is here: {resume}. "
       "If a revenue or Founder's Office role opens up at {company}, I would love to be considered.\n\nAyush")
WA = "Hi {first}, Ayush here. I emailed about {company}. {achievement} Happy to share details here if easier."

# Hard excludes: checked on company name + industry only (descriptions mention these words too often).
EXCLUDE = re.compile(r"\bMICE\b|event (management|planning|company|agency)|events? & exhibitions?|exhibitions?\b|"
                     r"staffing|recruit(ment|ing|er)s?\b|manpower|headhunt|executive search|placement", re.I)


# ---- .env (stdlib): KEY=VALUE lines. Real environment variables win over the file. ------------------
FROM_DOTENV = set()   # names that came from the .env file (not from the real environment)


def load_dotenv(path=None):
    path = Path(path or os.environ.get("OUTBOUND_ENV") or Path(__file__).resolve().parent / ".env")
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export ").strip(), v.strip().strip("\"'")
        if k and k not in os.environ:
            os.environ[k] = v
            FROM_DOTENV.add(k)


load_dotenv()


# ---- Small helpers ---------------------------------------------------------
def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(s):
    return datetime.fromisoformat(s)


def log(*a):
    print(datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"), *a, flush=True)


def h(s, salt=""):
    return zlib.crc32((s.lower() + salt).encode())


def clean_phone(s):
    """-> '91XXXXXXXXXX' or ''."""
    d = re.sub(r"\D", "", s or "")
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10:
        d = "91" + d
    return d if re.fullmatch(r"91[6-9]\d{9}", d) else ""


def build(first, company, em, phone="", line=None):
    """Everything derived from the row's identity. Deterministic per email."""
    i = h(em) % len(ACHIEVEMENTS)
    ach, ach2 = ACHIEVEMENTS[i], ACHIEVEMENTS[(i + 1) % len(ACHIEVEMENTS)]
    line = (line or DEFAULT_LINE.format(company=company)).strip()
    v = dict(first=first, company=company, line=line, achievement=ach, achievement2=ach2, resume=RESUME)
    wa = ""
    if phone:
        wa = f"https://wa.me/{phone}?text=" + quote(WA.format(first=first, company=company, achievement=ACHIEVEMENTS[0]))
    return {"line": line, "subject": SUBJECTS[h(em, "s") % 2].format(**v), "body": BODY.format(**v),
            "fu1": FU1.format(**v), "fu2": FU2.format(**v), "wa_link": wa}


# ---- Queue -----------------------------------------------------------------
def load_queue():
    if not QUEUE.exists():
        return []
    with open(QUEUE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [{c: (r.get(c) or "") for c in COLUMNS} for r in rows]


def save_queue(rows):
    tmp = QUEUE.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, QUEUE)


# ---- draft: Apollo CSV -> queue.csv ----------------------------------------
ALIASES = {
    "company": ["companyname", "company", "organization"], "email": ["email", "emailaddress"],
    "status": ["emailstatus"], "first": ["firstname"], "title": ["title", "jobtitle"],
    "phone": ["mobilephone", "workdirectphone", "directphone", "corporatephone", "phone"],
    "about": ["shortdescription", "seodescription", "keywords"], "industry": ["industry"],
    "li": ["companylinkedinurl", "companylinkedin"],
}


def _norm(header):
    return re.sub(r"[^a-z0-9]", "", header.lower())


def draft(csv_path):
    rows = load_queue()
    seen = {r["email"].lower() for r in rows}
    skipped = {"unverified": 0, "duplicate": 0, "excluded": 0, "no_email_or_name": 0}
    added = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            r = {_norm(k): (v or "").strip() for k, v in raw.items() if k}

            def get(key):
                return next((r[a] for a in ALIASES[key] if r.get(a)), "")

            em, first, company = get("email").lower(), get("first"), get("company")
            if not em or not first or not company:
                skipped["no_email_or_name"] += 1
            elif get("status").lower() != "verified":
                skipped["unverified"] += 1
            elif em in seen:
                skipped["duplicate"] += 1
            elif EXCLUDE.search(f"{company} | {get('industry')}"):
                skipped["excluded"] += 1
            else:
                phone = next((p for p in (clean_phone(r.get(a, "")) for a in ALIASES["phone"]) if p), "")
                row = {c: "" for c in COLUMNS}
                row.update(email=em, first=first, company=company, title=get("title"), phone=phone,
                           about=re.sub(r"\s+", " ", get("about"))[:300], company_li=get("li"), **build(first, company, em, phone))
                rows.append(row)
                seen.add(em)
                added += 1
    save_queue(rows)
    log(f"draft: added {added}, skipped {skipped}, queue now {len(rows)} rows")
    return added, skipped


# ---- Cap, bounce stop, what is due -----------------------------------------
def _ist_date(ts):
    return parse_ts(ts).astimezone(IST).date()


def sent_today(rows, now):
    today = now.astimezone(IST).date()
    return sum(1 for r in rows for k in ("sent_at", "fu1_at", "fu2_at") if r[k] and _ist_date(r[k]) == today)


def daily_cap(rows, now):
    firsts = [_ist_date(r["sent_at"]) for r in rows if r["sent_at"]]
    if not firsts or (now.astimezone(IST).date() - min(firsts)).days < RAMP_DAYS:
        return RAMP_CAP
    return DAILY_CAP


def halted(rows):
    sent = sum(1 for r in rows if r["sent_at"])
    bounced = sum(1 for r in rows if r["replied"] == "bounced")
    return bounced >= 2 or (sent >= BOUNCE_MIN_SAMPLE and bounced / sent > BOUNCE_RATE)


def due_first(rows):
    return [(i, "first") for i, r in enumerate(rows) if r["approved"] == "y" and not r["sent_at"] and r["replied"] not in CLOSED]


def due_followups(rows, now):
    out = []
    for i, r in enumerate(rows):
        if r["approved"] != "y" or not r["sent_at"] or r["replied"] in CLOSED or not r["message_id"]:
            continue
        age = now - parse_ts(r["sent_at"])
        if not r["fu1_at"] and age >= timedelta(days=FU1_DAYS):
            out.append((i, "fu1"))
        elif r["fu1_at"] and not r["fu2_at"] and age >= timedelta(days=FU2_DAYS):
            out.append((i, "fu2"))
    return out


def due_wa(rows, now):
    return [i for i, r in enumerate(rows) if r["sent_at"] and r["wa_link"] and not r["wa_shown"]
            and r["replied"] not in CLOSED and now - parse_ts(r["sent_at"]) >= timedelta(days=WA_DAYS)]


# ---- SMTP / IMAP -----------------------------------------------------------
def creds():
    u, p = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not u or not p:
        sys.exit("Set GMAIL_USER and GMAIL_APP_PASSWORD (Gmail app password, IMAP enabled).")
    # App passwords are shown as 4 groups of 4; pasted copies often carry spaces or a non-breaking space.
    return u.strip(), re.sub(r"\s+", "", p).strip("\"'")


def smtp_connect():
    """Gmail over SSL (465); if the server drops the connection, retry over STARTTLS (587).
    A rejected password (535) is raised straight away, retrying cannot fix it."""
    u, p = creds()
    last = None
    for attempt in ("ssl", "starttls"):
        try:
            if attempt == "ssl":
                s = smtplib.SMTP_SSL("smtp.gmail.com", 465)
            else:
                s = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
                s.starttls()
            s.login(u, p)
            return s
        except smtplib.SMTPAuthenticationError:
            raise
        except (smtplib.SMTPException, OSError) as e:
            last = e
    raise last


def imap_connect():
    u, p = creds()
    m = imaplib.IMAP4_SSL("imap.gmail.com")
    m.login(u, p)
    return m


ENV_NAMES = [("GMAIL_USER", "sending"), ("GMAIL_APP_PASSWORD", "sending"), ("AIRTABLE_TOKEN", "Airtable"),
             ("AIRTABLE_BASE_ID", "Airtable"), ("APIFY_TOKEN", "scraping")]


def cmd_env():
    """Which secrets does this process see? Names and lengths only, never the values."""
    in_codespace = bool(os.environ.get("CODESPACES"))
    missing = []
    for name, used_for in ENV_NAMES:
        v = os.environ.get(name, "")
        if v:
            src = ".env file" if name in FROM_DOTENV else ("Codespaces secret / environment" if in_codespace else "environment")
            print(f"  ok       {name:20} {len(v):3} chars   from {src}   (needed for {used_for})")
        else:
            missing.append(name)
            print(f"  MISSING  {name:20}                  (needed for {used_for})")
    if missing and in_codespace:
        print("\nIn a Codespace, secrets are read only when the Codespace STARTS. If you added them after it was created or while it was running:\n"
              "  1. github.com/settings/codespaces: each secret's 'Repository access' must include this repo, names must match exactly.\n"
              "  2. Stop the codespace (Ctrl+Shift+P, 'Codespaces: Stop Current Codespace') and open it again from github.com/codespaces.\n"
              "  3. Open a NEW terminal (old ones keep the old environment) and run this command again.\n"
              "Quick workaround: put the values in a .env file here (it is gitignored and never leaves this codespace).")
    elif missing:
        print("\nSet the missing ones in .env or export them, then run this again.")
    return missing


def cmd_check():
    """Log in to SMTP and IMAP without sending anything. Explains the usual failures instead of a traceback."""
    u, p = creds()
    log(f"trying {u} with a {len(p)} character app password (Google app passwords have 16)")
    try:
        smtp_connect().quit()
        log("SMTP login OK")
        imap_connect().logout()
    except smtplib.SMTPAuthenticationError:
        sys.exit("FAILED: Gmail rejected the username or app password. Make a NEW app password at myaccount.google.com/apppasswords "
                 "(2-Step Verification must be on), paste it again, and check GMAIL_USER is the same account.")
    except (smtplib.SMTPException, imaplib.IMAP4.error, OSError) as e:
        sys.exit(f"FAILED: {type(e).__name__}: {e}\nGmail closed or refused the login. Usual causes: (1) the app password was copied wrongly, "
                 "(2) Google blocked this server: open Gmail, look for a 'Critical security alert', press 'Yes, it was me', wait 10 minutes and retry, "
                 "(3) too many tries in a row, wait 10 minutes. Run this on your laptop as a comparison.")
    log(f"OK: SMTP and IMAP login work for {u}")


def make_message(r, step):
    u, _ = creds()
    m = EmailMessage()  # plain text only, no tracking, no extra headers
    m["From"], m["To"] = u, r["email"]
    m["Message-ID"] = make_msgid(domain=u.split("@")[-1])
    if step == "first":
        m["Subject"], text = r["subject"], r["body"]
    else:
        m["Subject"], text = "Re: " + r["subject"], r[step]
        m["In-Reply-To"] = m["References"] = r["message_id"]
    m.set_content(text)
    return m


def send_self(subject, body, smtp=None):
    u, _ = creds()
    m = EmailMessage()
    m["From"] = m["To"] = u
    m["Subject"] = subject
    m.set_content(body)
    if smtp:
        smtp.send_message(m)
        return
    s = smtp_connect()
    try:
        s.send_message(m)
    finally:
        s.quit()


def deliver(rows, jobs, now, dry):
    """Send `jobs` [(row_idx, step)] under the daily cap with a random gap. Saves after every send."""
    left = max(daily_cap(rows, now) - sent_today(rows, now), 0)
    todo = jobs[:left]
    log(f"{len(jobs)} due, cap allows {left} more today, sending {len(todo)}")
    if dry or not todo:
        for i, step in todo:
            log(f"[dry] {step} -> {rows[i]['email']}")
        return 0
    smtp = smtp_connect()
    n, t0 = 0, now_utc()
    try:
        for k, (i, step) in enumerate(todo):
            if k:
                time.sleep(random.uniform(*GAP_SECONDS))
            r, msg = rows[i], make_message(rows[i], step)
            try:
                smtp.send_message(msg)
            except smtplib.SMTPRecipientsRefused:
                r["replied"] = "bounced"
                save_queue(rows)
                log(f"refused: {r['email']} marked bounced")
                continue
            ts = (now + (now_utc() - t0)).isoformat()  # `now` plus real elapsed time (gaps included)
            if step == "first":
                r["sent_at"], r["message_id"] = ts, msg["Message-ID"]
            else:
                r[step + "_at"] = ts
            save_queue(rows)
            n += 1
            log(f"sent {step} -> {r['email']}")
    finally:
        smtp.quit()
    return n


def cmd_send(dry=False, now=None):
    rows, now = load_queue(), now or now_utc()
    if halted(rows):
        log("STOP: bounce rate too high, review bounced rows before sending")
        return 0
    return deliver(rows, due_first(rows), now, dry)


def cmd_followup(dry=False, now=None):
    rows, now = load_queue(), now or now_utc()
    if halted(rows):
        log("STOP: bounce rate too high, review bounced rows before sending")
        return 0
    return deliver(rows, due_followups(rows, now), now, dry)


def cmd_wa(now=None, save=True):
    rows, now = load_queue(), now or now_utc()
    idx = due_wa(rows, now)
    for i in idx:
        log(f"WhatsApp row {i + 1} {rows[i]['first']} @ {rows[i]['company']}: {rows[i]['wa_link']}")
        rows[i]["wa_shown"] = now.isoformat()
    if idx and save:
        save_queue(rows)
    return idx


# ---- notify: digest to Ayush -----------------------------------------------
def build_digest(rows, now):
    pending = [i for i, r in enumerate(rows) if r["approved"] != "y" and not r["sent_at"] and r["replied"] not in CLOSED]
    fus = due_followups(rows, now)
    wa = due_wa(rows, now)
    unsent = sum(1 for r in rows if not r["sent_at"] and r["replied"] not in CLOSED)
    refill = unsent < REFILL_BELOW
    if not (pending or fus or wa or refill):
        return None
    out = []
    if pending:
        out.append(f"Rows covered: {pending[0] + 1}-{pending[-1] + 1}\n\nDRAFTS TO APPROVE")
        for i in pending:
            r = rows[i]
            out.append(f"\n{i + 1}. {r['first']} @ {r['company']} <{r['email']}>\nSubject: {r['subject']}\n\n{r['body']}\n")
    if fus:
        out.append("FOLLOW-UPS DUE (approved rows, go out on the next cycle)")
        out += [f"{i + 1}. {rows[i]['first']} @ {rows[i]['company']}: {step}" for i, step in fus]
    if wa:
        out.append("\nWHATSAPP (tap to open, then send)")
        out += [f"{i + 1}. {rows[i]['first']} @ {rows[i]['company']}: {rows[i]['wa_link']}" for i in wa]
    if refill:
        out.append(f"\nOnly {unsent} unsent rows left. Export next Apollo batch.")
    out.append("\nReply with one command per line:\napprove 1 3 5 | approve all | line 2: custom opening line | skip 4 | replied 6")
    return f"{DIGEST_PREFIX}: {len(pending)} to approve", "\n".join(out), wa


def cmd_notify(dry=False, now=None):
    rows, now = load_queue(), now or now_utc()
    d = build_digest(rows, now)
    if not d:
        log("notify: nothing to report")
        return False
    subject, body, wa = d
    if dry:
        print(f"--- {subject}\n{body}")
        return True
    send_self(subject, body)
    for i in wa:
        rows[i]["wa_shown"] = now.isoformat()
    if wa:
        save_queue(rows)
    log(f"notify: digest sent ({subject})")
    return True


# ---- inbox: Ayush's replies + prospect replies + bounces --------------------
CMD = re.compile(r"^(approve|skip|replied)\s+([\d\s,]+)$|^approve\s+all$|^line\s+(\d+)\s*:\s*(.+)$", re.I)


def parse_commands(text):
    """One command per line, case-insensitive. Quoted ('>') lines ignored."""
    cmds = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith(">"):
            continue
        if re.match(r"^on .+wrote:?$", s, re.I):  # start of the quoted original
            break
        m = CMD.match(s)
        if not m:
            continue
        if s.lower() == "approve all":
            cmds.append(("approve_all",))
        elif m.group(3):
            cmds.append(("line", int(m.group(3)), m.group(4).strip()))
        else:
            cmds.append((m.group(1).lower(), [int(x) for x in re.findall(r"\d+", m.group(2))]))
    return cmds


def apply_commands(rows, cmds, window=None, now=None):
    """Apply to rows in place. Returns (changes, errors). `window`=(lo,hi) 1-based rows 'approve all' may touch."""
    changes, errors = [], []
    now = now or now_utc()

    def open_row(n):
        if not 1 <= n <= len(rows):
            errors.append(f"row {n}: no such row")
            return None
        r = rows[n - 1]
        if r["sent_at"] or r["replied"] in CLOSED:
            errors.append(f"row {n}: already sent or closed")
            return None
        return r

    for c in cmds:
        if c[0] == "approve":
            for n in c[1]:
                if (r := open_row(n)):
                    r["approved"] = "y"
                    changes.append(f"approved {n} ({r['email']})")
        elif c[0] == "approve_all":
            if not window:
                errors.append("approve all: digest row range not found in the quoted reply, use 'approve N N N'")
                continue
            for n in range(window[0], window[1] + 1):
                r = rows[n - 1] if 1 <= n <= len(rows) else None
                if r and r["approved"] != "y" and not r["sent_at"] and r["replied"] not in CLOSED:
                    r["approved"] = "y"
                    changes.append(f"approved {n} ({r['email']})")
        elif c[0] in ("skip", "replied"):
            for n in c[1]:
                if 1 <= n <= len(rows):
                    rows[n - 1]["replied"] = "skip" if c[0] == "skip" else "y"
                    changes.append(f"{c[0]} {n} ({rows[n - 1]['email']})")
                else:
                    errors.append(f"row {n}: no such row")
        elif c[0] == "line":
            n, text = c[1], c[2]
            if "—" in text or "–" in text:
                errors.append(f"line {n}: no em or en dashes in copy")
            elif (r := open_row(n)):
                r.update(build(r["first"], r["company"], r["email"], r["phone"], line=text))
                changes.append(f"line {n} updated")
    return changes, errors


def body_text(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
    return ""


def authentic(msg):
    ar = " ".join(msg.get_all("Authentication-Results") or [])
    return not re.search(r"\b(spf|dkim|dmarc)=fail", ar, re.I)


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fetch_since(imap, since):
    imap.select("INBOX")
    typ, data = imap.search(None, "SINCE", f"{since.day:02d}-{MONTHS[since.month - 1]}-{since.year}")
    out = []
    for num in (data[0] or b"").split():
        typ, d = imap.fetch(num, "(RFC822)")
        out.append(email.message_from_bytes(d[0][1]))
    return out


def msg_date(msg, default):
    try:
        dt = parsedate_to_datetime(msg["Date"])
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return default


def seen_path():
    return QUEUE.with_name("inbox_seen.txt")


def process_inbox(rows, msgs, me, now):
    """Pure-ish core of `inbox`: mutates rows, returns (notices[(subject, body)], seen_ids_added)."""
    seen_file = seen_path()
    seen = set(seen_file.read_text().split()) if seen_file.exists() else set()
    notices, new_seen = [], []
    me = me.lower()
    for msg in sorted(msgs, key=lambda m: msg_date(m, now)):
        sender = parseaddr(msg.get("From", ""))[1].lower()
        subj, mid = msg.get("Subject", "") or "", (msg.get("Message-ID") or "").strip()
        text = body_text(msg)
        if sender == me:
            if re.match(r"(re|fwd?):", subj.strip(), re.I) and "[outbound] action needed" in subj.lower() and mid not in seen:
                if not authentic(msg):
                    notices.append(("[Outbound] Ignored a reply that failed authentication", f"Subject: {subj}"))
                else:
                    m = re.search(r"Rows covered:\s*(\d+)-(\d+)", text)
                    window = (int(m.group(1)), int(m.group(2))) if m else None
                    changes, errors = apply_commands(rows, parse_commands(text), window, now)
                    if changes or errors:
                        notices.append(("[Outbound] Applied", "\n".join(changes + (["", "PROBLEMS"] + errors if errors else []))))
                new_seen.append(mid)
            continue
        low = text.lower()
        if "mailer-daemon" in sender or sender.startswith("postmaster"):
            for r in rows:
                if r["sent_at"] and r["replied"] not in CLOSED and r["email"] in low and msg_date(msg, now) >= parse_ts(r["sent_at"]):
                    r["replied"] = "bounced"
                    notices.append((f"[Outbound] BOUNCE {r['email']}", "Marked bounced and removed from the sequence."
                                    + ("\nSending is now halted: bounce rate too high." if halted(rows) else "")))
            continue
        for r in rows:
            if r["sent_at"] and r["email"] == sender and r["replied"] not in CLOSED and msg_date(msg, now) >= parse_ts(r["sent_at"]):
                r["replied"] = "y"
                notices.append((f"[Outbound] REPLY from {r['first']} ({r['company']})", f"{sender}\nSubject: {subj}\n\n{text.strip()[:1500]}"))
    return notices, new_seen


def cmd_inbox(dry=False, now=None):
    rows, now = load_queue(), now or now_utc()
    u, _ = creds()
    open_sent = [parse_ts(r["sent_at"]) for r in rows if r["sent_at"] and r["replied"] not in CLOSED]
    since = max(min(open_sent + [now - timedelta(days=14)]), now - timedelta(days=60))
    imap = imap_connect()
    try:
        msgs = fetch_since(imap, since)
    finally:
        imap.logout()
    notices, new_seen = process_inbox(rows, msgs, u, now)
    if dry:
        for s, b in notices:
            print(f"--- {s}\n{b}")
        return notices
    save_queue(rows)
    if new_seen:
        with open(seen_path(), "a") as f:
            f.write("\n".join(new_seen) + "\n")
    if notices:
        smtp = smtp_connect()
        try:
            for s, b in notices:
                send_self(s, b, smtp)
        finally:
            smtp.quit()
    log(f"inbox: {len(msgs)} messages scanned, {len(notices)} notices")
    return notices


def cmd_cycle(dry=False):
    cmd_inbox(dry)
    cmd_send(dry)
    cmd_followup(dry)
    cmd_notify(dry)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("draft").add_argument("csv")
    for name in ("send", "followup", "notify", "inbox", "cycle"):
        sub.add_parser(name).add_argument("--dry", action="store_true")
    sub.add_parser("wa")
    sub.add_parser("check")
    sub.add_parser("env")
    a = ap.parse_args(argv)
    if a.cmd == "draft":
        draft(a.csv)
    elif a.cmd == "wa":
        cmd_wa()
    elif a.cmd == "check":
        cmd_check()
    elif a.cmd == "env":
        cmd_env()
    else:
        {"send": cmd_send, "followup": cmd_followup, "notify": cmd_notify, "inbox": cmd_inbox, "cycle": cmd_cycle}[a.cmd](a.dry)


if __name__ == "__main__":
    main()
