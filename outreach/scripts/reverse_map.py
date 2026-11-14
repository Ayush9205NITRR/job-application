#!/usr/bin/env python3
"""Reverse-map events back onto each founder:  Sent -> Delivered -> Viewed -> Replied  (or Bounced).

Sources (all matched back to a founder by NORMALISED EMAIL / Gmail thread):
  Gmail (read-only API)  : Sent_At, Thread_ID, Mails_Sent, Last_Sent, Replied_At, Bounced
  views CSV (optional)   : any tracker export with an email column (+ optional time column), e.g. imports/views*.csv

What is and is NOT knowable (be honest about it):
  Sent       real  : a mail exists in your Sent folder
  Bounced    real  : a mailer-daemon / postmaster message arrived in the same thread
  Replied    real  : someone other than you wrote in the thread after your mail (auto-replies / OOO ignored)
  Delivered  INFERRED: Gmail has no delivery receipt. Sent + no bounce after DELIVERED_AFTER_SEC (1 hour) = Delivered
  Viewed     only with a source: Gmail itself cannot tell. You get it from a views CSV (e.g. a Papermark / any tracker export).
             A reply also implies it was read, so Replied always wins over Viewed.

Usage:
  python3 scripts/reverse_map.py --dry-run                    # Gmail + views CSVs, report only
  python3 scripts/reverse_map.py --apply                      # write into Grist
  python3 scripts/reverse_map.py --apply --skip-gmail         # only apply views CSVs + recompute Stage (no Google login needed)
  python3 scripts/reverse_map.py --apply --views-csv path.csv
"""
import argparse
import csv
import datetime
import glob
import json
import pathlib
import re
import sys
import time
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import formulas as F  # noqa: E402
import grist_client as G  # noqa: E402

DELIVERED_AFTER_SEC = 3600
PRE_SENT = {"", "New", "Contact-ready", "Ready", "Drafted-1"}
PRE_SENT2 = PRE_SENT | {"Sent-1", "Drafted-2"}

EMAIL_HEADERS = ["email", "e mail", "email id", "viewer email", "viewer", "recipient", "recipient email", "to", "contact email"]
TIME_HEADERS = ["viewed at", "viewed_at", "first viewed", "last viewed", "opened at", "date", "time", "timestamp", "created at"]


# ---------------------------------------------------------------- pure logic (unit tested)
def derive_stage(sent_ts, bounced, replied_ts, viewed_ts, now_ts, delivered_after=DELIVERED_AFTER_SEC):
    if not sent_ts:
        return "Not sent"
    if bounced:
        return "Bounced"
    if replied_ts:
        return "Replied"
    if viewed_ts:
        return "Viewed"
    if now_ts - sent_ts >= delivered_after:
        return "Delivered"
    return "Sent"


def norm_header(h):
    return re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()


def parse_when(s):
    """'2026-11-14 10:22', '14 Nov 2026', ISO with T/Z, unix seconds -> unix seconds, or None."""
    s = (s or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{10}", s):
        return int(s)
    if re.fullmatch(r"\d{13}", s):
        return int(s) // 1000
    t = s.replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(t)
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        return int(d.timestamp())
    except ValueError:
        pass
    for fmt in ["%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%d %b %Y %H:%M", "%d %b %Y", "%b %d, %Y", "%d-%m-%Y", "%d/%m/%Y"]:
        try:
            d = datetime.datetime.strptime(s, fmt).replace(tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
            return int(d.timestamp())
        except ValueError:
            pass
    return None


def load_views(paths, now_ts=None):
    """-> {email_norm: earliest viewed unix ts}. If a row has no time column, uses now_ts."""
    emailn = F.compile_formula(F.F_EMAIL_NORM, "Email")
    now_ts = now_ts or int(time.time())
    out = {}
    for p in paths:
        with open(p, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        by = {norm_header(h): h for h in rows[0].keys()}
        ecol = next((by[h] for h in EMAIL_HEADERS if h in by), None)
        tcol = next((by[h] for h in TIME_HEADERS if h in by), None)
        if not ecol:
            print(f"  views file {p}: no email column found (headers: {list(rows[0].keys())})", file=sys.stderr)
            continue
        for r in rows:
            e = emailn(Email=r.get(ecol))
            if not e:
                continue
            ts = parse_when(r.get(tcol)) if tcol else None
            ts = ts or now_ts
            out[e] = min(ts, out.get(e, ts))
    return out


def decide_status(status, sent_dates, replied, bounced):
    """Mirror of gmail_sync rules. Returns (new_status, extra_fields)."""
    extra, new = {}, status
    if replied:
        new, extra["Reply"] = "Replied", "Replied"
    elif bounced:
        new, extra["Reply"] = "Bounced", "Bounced"
    elif len(sent_dates) >= 2 and status in PRE_SENT2:
        new = "Sent-2"
    elif len(sent_dates) >= 1 and status in PRE_SENT:
        new = "Sent-1"
    return new, extra


# ---------------------------------------------------------------- Gmail (read-only)
def gmail_info(rows):
    from gmail_sync import gmail_service, search, addrs_for, q_any  # reuse
    svc = gmail_service()
    me = svc.users().getProfile(userId="me").execute()["emailAddress"].lower()
    info = {}
    for n, r in enumerate(rows, 1):
        f, a = r["fields"], addrs_for(r["fields"])
        ids = search(svc, q_any("to", a, "in:sent"))
        msgs = []
        for i in ids:
            m = svc.users().messages().get(userId="me", id=i, format="minimal").execute()
            msgs.append((int(m["internalDate"]) // 1000, m["threadId"]))
        msgs.sort()
        rec = {"sent_ts": msgs[0][0] if msgs else None, "sent_all": [m[0] for m in msgs],
               "thread": msgs[0][1] if msgs else "", "replied_ts": None, "bounced": False}
        if msgs:
            for th in {m[1] for m in msgs}:
                t = svc.users().threads().get(userId="me", id=th, format="metadata",
                                              metadataHeaders=["From", "Auto-Submitted"]).execute()
                for m in t.get("messages", []):
                    ts = int(m["internalDate"]) // 1000
                    h = {x["name"].lower(): x["value"].lower() for x in m["payload"].get("headers", [])}
                    frm = h.get("from", "")
                    if ts <= rec["sent_ts"] or me in frm:
                        continue
                    if "mailer-daemon" in frm or "postmaster" in frm:
                        rec["bounced"] = True
                    elif h.get("auto-submitted", "no") not in ("no", ""):
                        continue  # out-of-office / auto reply
                    else:
                        rec["replied_ts"] = min(ts, rec["replied_ts"] or ts)
            if not rec["replied_ts"]:  # fresh mail from them outside the thread
                day = datetime.datetime.fromtimestamp(rec["sent_ts"], datetime.timezone.utc).strftime("%Y/%m/%d")
                hit = search(svc, q_any("from", a, f"-in:sent after:{day}"), cap=1)
                if hit:
                    m = svc.users().messages().get(userId="me", id=hit[0], format="minimal").execute()
                    rec["replied_ts"] = int(m["internalDate"]) // 1000
            if not rec["bounced"]:
                rec["bounced"] = bool(search(svc, 'from:(mailer-daemon OR postmaster) (' + " OR ".join(f'"{x}"' for x in a) + ")", cap=1))
        info[r["id"]] = rec
        if n % 25 == 0:
            print(f"  ...{n}/{len(rows)}", file=sys.stderr)
        time.sleep(0.05)
    return info


# ---------------------------------------------------------------- main
def build_updates(rows, ginfo, views, now_ts):
    emailn = F.compile_formula(F.F_EMAIL_NORM, "Email")
    updates, stats = [], Counter()
    for r in rows:
        f = r["fields"]
        status = f.get("Status") or ""
        sent_ts, sent_all, thread = f.get("Sent_At"), [], f.get("Thread_ID") or ""
        replied_ts = f.get("Replied_At")
        bounced = (f.get("Reply") == "Bounced") or status == "Bounced"
        upd = {}
        g = ginfo.get(r["id"])
        if g:
            sent_ts, sent_all, thread = g["sent_ts"], g["sent_all"], g["thread"]
            replied_ts, bounced = g["replied_ts"], g["bounced"]
            if sent_ts:
                upd.update({"Sent_At": sent_ts, "Thread_ID": thread, "Mails_Sent": len(sent_all),
                            "Last_Sent": G.ts_date(datetime.datetime.fromtimestamp(sent_all[-1], datetime.timezone.utc).date().isoformat())})
                upd["Sent_1"] = G.ts_date(datetime.datetime.fromtimestamp(sent_all[0], datetime.timezone.utc).date().isoformat())
                if len(sent_all) >= 2:
                    upd["Sent_2"] = G.ts_date(datetime.datetime.fromtimestamp(sent_all[1], datetime.timezone.utc).date().isoformat())
            if replied_ts:
                upd["Replied_At"] = replied_ts
            new_status, extra = decide_status(status, sent_all, bool(replied_ts), bounced)
            upd.update(extra)
            if new_status != status:
                upd["Status"] = new_status
        else:
            replied_ts = replied_ts or (1 if f.get("Reply") == "Replied" else None)
        viewed_ts = views.get(f.get("Email_norm") or emailn(Email=f.get("Email")))
        if viewed_ts:
            upd["Viewed_At"] = viewed_ts
        viewed_ts = viewed_ts or f.get("Viewed_At")
        stage = derive_stage(sent_ts, bounced, replied_ts, viewed_ts, now_ts)
        if stage != f.get("Stage"):
            upd["Stage"] = stage
        stats[stage] += 1
        if upd:
            updates.append({"id": r["id"], "fields": upd})
    return updates, stats


def run(apply=False, skip_gmail=False, views_csv=(), limit=None, quiet=False):
    """Map Gmail (and any views CSV) back onto Founders. Returns a stats dict.

    refresh.py calls this directly, so that its Grist calls land in the same budget.
    """
    rows = [r for r in G.fetch("Founders") if not r["fields"].get("Is_Dup")]
    if limit:
        rows = rows[:limit]
    now_ts = int(time.time())
    vpaths = list(views_csv) or sorted(glob.glob(str(ROOT / "imports" / "views*.csv")))
    views = load_views(vpaths, now_ts)
    ginfo = {}
    if not skip_gmail:
        from gmail_sync import addrs_for
        ginfo = gmail_info([r for r in rows if addrs_for(r["fields"])])
    updates, stats = build_updates(rows, ginfo, views, now_ts)
    out = {"founders": len(rows),
           "views_rows_matched": sum(1 for r in rows if r["fields"].get("Email_norm") in views),
           "stage_counts": dict(stats), "updated": len(updates), "gmail_checked": not skip_gmail}
    if apply:
        G.update("Founders", updates)
    else:
        out["updated"] = 0
    if not quiet:
        print(json.dumps(out, indent=1))
        print(f"wrote {len(updates)} rows to Grist" if apply else "(dry run, nothing written)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-gmail", action="store_true")
    ap.add_argument("--views-csv", action="append", default=[])
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if not (a.apply or a.dry_run):
        sys.exit("choose --dry-run or --apply")
    run(apply=a.apply, skip_gmail=a.skip_gmail, views_csv=a.views_csv, limit=a.limit)


if __name__ == "__main__":
    main()
