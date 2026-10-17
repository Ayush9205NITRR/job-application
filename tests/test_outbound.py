import csv
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

import outbound as ob

NOW = datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc)  # 09:30 IST


class FakeSMTP:
    sent = []

    def __init__(self, *a, **k):
        pass

    def login(self, *a):
        pass

    def send_message(self, m):
        FakeSMTP.sent.append(m)

    def quit(self):
        pass


class FakeIMAP:
    def __init__(self, msgs):
        self.msgs = [m.as_bytes() for m in msgs]

    def select(self, box):
        return "OK", [b"1"]

    def search(self, *a):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.msgs)))]

    def fetch(self, num, spec):
        return "OK", [(b"1 (RFC822)", self.msgs[int(num) - 1]), b")"]

    def logout(self):
        pass


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "QUEUE", tmp_path / "queue.csv")
    monkeypatch.setenv("GMAIL_USER", "ayush@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    monkeypatch.setattr(ob.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(ob.random, "uniform", lambda a, b: 3.0)
    FakeSMTP.sent = []
    sleeps = []
    monkeypatch.setattr(ob.time, "sleep", sleeps.append)
    return sleeps


def mkrow(i, **kw):
    r = {c: "" for c in ob.COLUMNS}
    em = f"p{i}@acme{i}.com"
    r.update(email=em, first=f"P{i}", company=f"Acme{i}", **ob.build(f"P{i}", f"Acme{i}", em, "919876543210"))
    r.update(kw)
    return r


def apollo(tmp_path, rows):
    p = tmp_path / "apollo.csv"
    hdr = ["Company Name", "First Name", "Last Name", "Title", "Email", "Email Status", "Mobile Phone",
           "Industry", "Short Description"]
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        w.writerows(rows)
    return p


# ---- draft -----------------------------------------------------------------
def test_draft_dedupe_excludes_and_phones(tmp_path):
    p = apollo(tmp_path, [
        ["Acme", "Riya", "S", "CEO", "Riya@acme.com", "Verified", "+91 98765-43210", "Software", "We build X"],
        ["Acme", "Riya", "S", "CEO", "riya@acme.com", "Verified", "", "", ""],                 # duplicate
        ["Beta", "Tom", "T", "CEO", "tom@beta.com", "Guessed", "", "", ""],                    # unverified
        ["Dream MICE Co", "Sam", "S", "CEO", "sam@mice.com", "Verified", "", "", ""],          # MICE
        ["Gamma", "Ann", "A", "CEO", "ann@gamma.com", "Verified", "", "Staffing & Recruiting", ""],  # staffing
        ["Delta", "Raj", "R", "Founder", "raj@delta.com", "Verified", "09876543211", "SaaS", ""],
        ["Eps", "", "", "CEO", "x@eps.com", "Verified", "", "", ""],                           # no first name
    ])
    added, skipped = ob.draft(p)
    rows = ob.load_queue()
    assert added == 2 and [r["email"] for r in rows] == ["riya@acme.com", "raj@delta.com"]
    assert skipped == {"unverified": 1, "duplicate": 1, "excluded": 2, "no_email_or_name": 1}
    assert rows[0]["phone"] == "919876543210" and rows[1]["phone"] == "919876543211"
    assert all(r["approved"] == "" for r in rows)
    ob.draft(p)  # re-running appends nothing, row numbers stay stable
    assert [r["email"] for r in ob.load_queue()] == ["riya@acme.com", "raj@delta.com"]


@pytest.mark.parametrize("raw,out", [("+91 98765 43210", "919876543210"), ("9876543210", "919876543210"),
                                      ("0 9876543210", "919876543210"), ("12345", ""), ("+1 415 555 0100", ""), ("", "")])
def test_clean_phone(raw, out):
    assert ob.clean_phone(raw) == out


def test_build_copy_rules():
    b = ob.build("Riya", "Acme", "riya@acme.com", "919876543210")
    assert b == ob.build("Riya", "Acme", "riya@acme.com", "919876543210")  # deterministic
    for k in ("line", "subject", "body", "fu1", "fu2", "wa_link"):
        assert "—" not in b[k] and "{" not in b[k]
    assert "tinyurl" in b["fu2"] and "http" not in b["body"] and "http" not in b["fu1"]
    assert b["wa_link"].startswith("https://wa.me/919876543210?text=")
    assert ob.build("R", "A", "r@a.com", "")["wa_link"] == ""


# ---- reply grammar ----------------------------------------------------------
def test_parse_commands():
    text = "Approve 1 3, 5\nSKIP 4\nreplied 6\nline 2: Loved your launch last week\napprove all\nthanks!\n> approve 9\nOn Mon, X wrote:\napprove 10"
    assert ob.parse_commands(text) == [("approve", [1, 3, 5]), ("skip", [4]), ("replied", [6]),
                                       ("line", 2, "Loved your launch last week"), ("approve_all",)]


def test_apply_commands():
    rows = [mkrow(i) for i in range(1, 6)]
    rows[3]["sent_at"] = NOW.isoformat()
    ch, er = ob.apply_commands(rows, [("approve", [1, 4, 9]), ("line", 2, "Fresh line"), ("line", 3, "bad — dash"),
                                      ("skip", [5])])
    assert rows[0]["approved"] == "y" and rows[3]["approved"] == ""
    assert "Fresh line" in rows[1]["body"] and rows[1]["line"] == "Fresh line"
    assert rows[4]["replied"] == "skip"
    assert any("row 4" in e for e in er) and any("row 9" in e for e in er) and any("dash" in e for e in er)


def test_approve_all_limited_to_digest_window():
    rows = [mkrow(i) for i in range(1, 6)]
    ob.apply_commands(rows, [("approve_all",)], window=(1, 3))
    assert [r["approved"] for r in rows] == ["y", "y", "y", "", ""]
    ch, er = ob.apply_commands(rows, [("approve_all",)], window=None)
    assert er and rows[3]["approved"] == ""


# ---- follow-up age logic ----------------------------------------------------
def test_followup_due():
    sent = (NOW - timedelta(days=5)).isoformat()
    r = mkrow(1, approved="y", sent_at=sent, message_id="<m1@x>")
    assert ob.due_followups([r], NOW) == [(0, "fu1")]
    r["sent_at"] = (NOW - timedelta(days=4, hours=23)).isoformat()
    assert ob.due_followups([r], NOW) == []
    r.update(sent_at=(NOW - timedelta(days=12)).isoformat())
    assert ob.due_followups([r], NOW) == [(0, "fu1")]          # fu2 never before fu1
    r["fu1_at"] = NOW.isoformat()
    assert ob.due_followups([r], NOW) == [(0, "fu2")]
    r["fu2_at"] = NOW.isoformat()
    assert ob.due_followups([r], NOW) == []
    for stop in ("y", "skip", "bounced"):
        r2 = mkrow(2, approved="y", sent_at=sent, message_id="<m>", replied=stop)
        assert ob.due_followups([r2], NOW) == []
    assert ob.due_followups([mkrow(3, sent_at=sent, message_id="<m>")], NOW) == []  # not approved


def test_wa_due_day3():
    r = mkrow(1, sent_at=(NOW - timedelta(days=3)).isoformat())
    assert ob.due_wa([r], NOW) == [0]
    r["sent_at"] = (NOW - timedelta(days=2)).isoformat()
    assert ob.due_wa([r], NOW) == []
    r.update(sent_at=(NOW - timedelta(days=3)).isoformat(), wa_shown="x")
    assert ob.due_wa([r], NOW) == []
    r.update(wa_shown="", replied="y")
    assert ob.due_wa([r], NOW) == []


# ---- cap, gap, threading ----------------------------------------------------
def test_ramp_cap_and_gap(env):
    ob.save_queue([mkrow(i, approved="y") for i in range(1, 41)])
    assert ob.cmd_send(now=NOW) == 5                       # nothing sent yet: ramp cap
    assert len(FakeSMTP.sent) == 5 and len(env) == 4       # random gap between sends, not before the first
    assert ob.cmd_send(now=NOW) == 0                       # cap used up today
    rows = ob.load_queue()
    assert sum(1 for r in rows if r["sent_at"]) == 5 and all(r["message_id"] for r in rows[:5])
    assert ob.cmd_send(now=NOW + timedelta(days=1)) == 5   # still inside the 3 day ramp
    later = NOW + timedelta(days=4)                        # past the ramp
    assert ob.daily_cap(ob.load_queue(), later) == 20
    assert ob.cmd_send(now=later) == 20


def test_unapproved_never_sent():
    ob.save_queue([mkrow(1), mkrow(2, approved="y", replied="skip"), mkrow(3, approved="y", replied="y")])
    assert ob.cmd_send(now=NOW) == 0 and FakeSMTP.sent == []


def test_plain_text_and_followup_threads_to_first_message():
    ob.save_queue([mkrow(1, approved="y")])
    ob.cmd_send(now=NOW)
    first = FakeSMTP.sent[0]
    assert not first.is_multipart() and first.get_content_type() == "text/plain"
    assert first["Subject"] == ob.load_queue()[0]["subject"] and first["In-Reply-To"] is None
    mid = ob.load_queue()[0]["message_id"]
    assert mid == first["Message-ID"]
    ob.cmd_followup(now=NOW + timedelta(days=5, hours=1))
    fu = FakeSMTP.sent[1]
    assert fu["Subject"] == "Re: " + first["Subject"] and fu["In-Reply-To"] == mid and fu["References"] == mid
    assert ob.load_queue()[0]["fu1_at"]
    ob.cmd_followup(now=NOW + timedelta(days=12, hours=1))
    assert "closing the loop" in FakeSMTP.sent[2].get_content() and "tinyurl" in FakeSMTP.sent[2].get_content()


def test_followups_respect_shared_daily_cap():
    base = NOW - timedelta(days=30)
    rows = [mkrow(i, approved="y", sent_at=base.isoformat(), message_id=f"<m{i}@x>") for i in range(1, 26)]
    ob.save_queue(rows)
    assert ob.cmd_followup(now=NOW) == 20


def test_dry_run_sends_and_writes_nothing():
    ob.save_queue([mkrow(1, approved="y")])
    assert ob.cmd_send(dry=True, now=NOW) == 0 and FakeSMTP.sent == []
    assert ob.load_queue()[0]["sent_at"] == ""


def test_bounce_halt_blocks_sending():
    rows = [mkrow(i, sent_at=NOW.isoformat()) for i in range(1, 11)]
    rows[0]["replied"] = rows[1]["replied"] = "bounced"
    rows.append(mkrow(11, approved="y"))
    ob.save_queue(rows)
    assert ob.halted(rows) and ob.cmd_send(now=NOW) == 0 and FakeSMTP.sent == []
    assert not ob.halted([mkrow(i, sent_at=NOW.isoformat()) for i in range(1, 6)])
    one = [mkrow(i, sent_at=NOW.isoformat()) for i in range(1, 41)]
    one[0]["replied"] = "bounced"
    assert not ob.halted(one)                  # 2.5%
    one[1]["replied"] = "bounced"
    assert ob.halted(one)


def test_refused_recipient_marked_bounced(monkeypatch):
    class Refuse(FakeSMTP):
        def send_message(self, m):
            raise ob.smtplib.SMTPRecipientsRefused({"x": (550, b"no")})
    monkeypatch.setattr(ob.smtplib, "SMTP_SSL", Refuse)
    ob.save_queue([mkrow(1, approved="y")])
    assert ob.cmd_send(now=NOW) == 0
    r = ob.load_queue()[0]
    assert r["replied"] == "bounced" and r["sent_at"] == ""


# ---- digest ------------------------------------------------------------------
def test_digest_contents_and_wa_marking():
    rows = [mkrow(1), mkrow(2), mkrow(3, approved="y", sent_at=(NOW - timedelta(days=3)).isoformat(), message_id="<m>")]
    ob.save_queue(rows)
    subject, body, wa = ob.build_digest(ob.load_queue(), NOW)
    assert subject.startswith(ob.DIGEST_PREFIX) and "Rows covered: 1-2" in body
    assert "wa.me" in body and "Export next Apollo batch" in body and wa == [2]
    assert ob.cmd_notify(now=NOW) is True
    assert FakeSMTP.sent[0]["To"] == "ayush@gmail.com" and ob.load_queue()[2]["wa_shown"]
    assert "wa.me" not in ob.build_digest(ob.load_queue(), NOW)[1]       # shown once only


def test_digest_silent_when_nothing_to_say():
    ob.save_queue([mkrow(i, approved="y") for i in range(1, 15)])   # approved, unsent, plenty left: nothing to say
    assert ob.build_digest(ob.load_queue(), NOW) is None


def test_digest_nudges_refill_on_empty_queue():
    ob.save_queue([])
    assert "Export next Apollo batch" in ob.build_digest([], NOW)[1]


# ---- inbox ---------------------------------------------------------------------
def mail(frm, subject, body, date=NOW, mid=None, extra=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, "ayush@gmail.com", subject
    m["Date"] = date.strftime("%a, %d %b %Y %H:%M:%S +0000")
    m["Message-ID"] = mid or f"<{abs(hash((frm, subject, body)))}@t>"
    for k, v in (extra or {}).items():
        m[k] = v
    m.set_content(body)
    return m


def test_inbox_applies_digest_reply_once(monkeypatch):
    ob.save_queue([mkrow(1), mkrow(2), mkrow(3)])
    reply = mail("Ayush <ayush@gmail.com>", "Re: [Outbound] Action needed: 3 to approve",
                 "approve 1 3\nskip 2\n\n> Rows covered: 1-3\n", mid="<r1@t>")
    monkeypatch.setattr(ob.imaplib, "IMAP4_SSL", lambda h: type("I", (FakeIMAP,), {"login": lambda s, *a: None})([reply]))
    notices = ob.cmd_inbox(now=NOW + timedelta(hours=1))
    rows = ob.load_queue()
    assert [r["approved"] for r in rows] == ["y", "", "y"] and rows[1]["replied"] == "skip"
    assert notices and notices[0][0] == "[Outbound] Applied"
    ob.save_queue([mkrow(i) for i in range(1, 4)])           # new rows arrive; same reply is still in the inbox
    ob.cmd_inbox(now=NOW + timedelta(hours=2))
    assert [r["approved"] for r in ob.load_queue()] == ["", "", ""]   # not re-applied


def test_inbox_approve_all_uses_digest_window(monkeypatch):
    ob.save_queue([mkrow(i) for i in range(1, 6)])
    reply = mail("ayush@gmail.com", "Re: [Outbound] Action needed: 3 to approve",
                 "approve all\n\n> Rows covered: 1-3\n", mid="<r2@t>")
    monkeypatch.setattr(ob.imaplib, "IMAP4_SSL", lambda h: type("I", (FakeIMAP,), {"login": lambda s, *a: None})([reply]))
    ob.cmd_inbox(now=NOW + timedelta(hours=1))
    assert [r["approved"] for r in ob.load_queue()] == ["y", "y", "y", "", ""]


def test_inbox_ignores_spoofed_or_foreign_commands(monkeypatch):
    ob.save_queue([mkrow(1)])
    spoof = mail("Ayush <ayush@gmail.com>", "Re: [Outbound] Action needed", "approve all\n> Rows covered: 1-1",
                 mid="<s1@t>", extra={"Authentication-Results": "mx.google.com; spf=fail; dkim=none"})
    stranger = mail("evil@x.com", "Re: [Outbound] Action needed", "approve 1", mid="<s2@t>")
    monkeypatch.setattr(ob.imaplib, "IMAP4_SSL", lambda h: type("I", (FakeIMAP,), {"login": lambda s, *a: None})([spoof, stranger]))
    ob.cmd_inbox(now=NOW + timedelta(hours=1))
    assert ob.load_queue()[0]["approved"] == ""


def test_inbox_detects_prospect_reply_and_bounce(monkeypatch):
    sent = (NOW - timedelta(days=2)).isoformat()
    ob.save_queue([mkrow(1, approved="y", sent_at=sent, message_id="<a>"),
                   mkrow(2, approved="y", sent_at=sent, message_id="<b>"),
                   mkrow(3, approved="y", sent_at=sent, message_id="<c>")])
    reply = mail("P1 <p1@acme1.com>", "Re: Founder's Office at Acme1", "Sure, call Thursday?", date=NOW - timedelta(days=1))
    old = mail("p3@acme3.com", "Older mail", "before we emailed", date=NOW - timedelta(days=9))
    bounce = mail("Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "Delivery Status Notification (Failure)",
                  "Your message wasn't delivered to p2@acme2.com because the address couldn't be found.",
                  date=NOW - timedelta(days=1))
    monkeypatch.setattr(ob.imaplib, "IMAP4_SSL", lambda h: type("I", (FakeIMAP,), {"login": lambda s, *a: None})([reply, old, bounce]))
    notices = ob.cmd_inbox(now=NOW)
    rows = ob.load_queue()
    assert [r["replied"] for r in rows] == ["y", "bounced", ""]
    assert {n[0] for n in notices} == {"[Outbound] REPLY from P1 (Acme1)", "[Outbound] BOUNCE p2@acme2.com"}
    assert ob.due_followups(rows, NOW + timedelta(days=10)) == [(2, "fu1")]   # replied and bounced rows stop
    assert len(FakeSMTP.sent) == 2                                              # notices emailed to self


def test_dotenv_loaded_but_real_env_wins(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# comment\nGMAIL_USER="me@gmail.com"\nexport GMAIL_APP_PASSWORD=abcd efgh\nEMPTY\nOTHER_X = 1\n')
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "from-real-env")
    monkeypatch.delenv("OTHER_X", raising=False)
    ob.load_dotenv(f)
    import os
    assert os.environ["GMAIL_USER"] == "me@gmail.com"            # quotes stripped
    assert os.environ["GMAIL_APP_PASSWORD"] == "from-real-env"   # existing env not overridden
    assert os.environ["OTHER_X"] == "1"
    monkeypatch.delenv("OTHER_X")


def test_check_logs_in_without_sending(monkeypatch, capsys):
    monkeypatch.setattr(ob.imaplib, "IMAP4_SSL", lambda h: type("I", (FakeIMAP,), {"login": lambda s, *a: None})([]))
    ob.cmd_check()
    assert FakeSMTP.sent == [] and "OK: SMTP and IMAP login work" in capsys.readouterr().out


def test_missing_credentials_message(monkeypatch):
    monkeypatch.delenv("GMAIL_APP_PASSWORD")
    with pytest.raises(SystemExit) as e:
        ob.creds()
    assert "GMAIL_APP_PASSWORD" in str(e.value)


def test_creds_strip_hidden_spaces(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", " me@gmail.com ")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop\n")
    assert ob.creds() == ("me@gmail.com", "abcdefghijklmnop")


def test_smtp_falls_back_to_starttls_when_ssl_drops(monkeypatch):
    log = []

    class Drop(FakeSMTP):
        def __init__(self, *a, **k):
            raise ob.smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    class Plain(FakeSMTP):
        def __init__(self, *a, **k): log.append(("plain", a))
        def starttls(self): log.append("starttls")
    monkeypatch.setattr(ob.smtplib, "SMTP_SSL", Drop)
    monkeypatch.setattr(ob.smtplib, "SMTP", Plain)
    ob.smtp_connect().quit()
    assert log == [("plain", ("smtp.gmail.com", 587)), "starttls"]


def test_bad_password_not_retried_and_check_explains(monkeypatch):
    tries = []

    class Reject(FakeSMTP):
        def __init__(self, *a, **k): tries.append(a)
        def login(self, *a): raise ob.smtplib.SMTPAuthenticationError(535, b"bad")
    monkeypatch.setattr(ob.smtplib, "SMTP_SSL", Reject)
    with pytest.raises(SystemExit) as e:
        ob.cmd_check()
    assert len(tries) == 1 and "NEW app password" in str(e.value)


def test_check_explains_dropped_connection(monkeypatch):
    class Drop(FakeSMTP):
        def __init__(self, *a, **k): raise ob.smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
    monkeypatch.setattr(ob.smtplib, "SMTP_SSL", Drop)
    monkeypatch.setattr(ob.smtplib, "SMTP", Drop)
    with pytest.raises(SystemExit) as e:
        ob.cmd_check()
    assert "Critical security alert" in str(e.value) and "SMTPServerDisconnected" in str(e.value)


def test_env_command_shows_names_and_lengths_never_values(monkeypatch, capsys):
    for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "AIRTABLE_TOKEN", "AIRTABLE_BASE_ID", "APIFY_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "supersecretvalue1")
    monkeypatch.setenv("CODESPACES", "true")
    missing = ob.cmd_env()
    out = capsys.readouterr().out
    assert missing == ["AIRTABLE_TOKEN", "AIRTABLE_BASE_ID", "APIFY_TOKEN"]
    assert "supersecretvalue1" not in out and "me@gmail.com" not in out and "17 chars" in out
    assert "Stop Current Codespace" in out and "MISSING  AIRTABLE_TOKEN" in out


def test_env_command_reports_dotenv_source(tmp_path, monkeypatch, capsys):
    f = tmp_path / ".env"
    f.write_text("AIRTABLE_TOKEN=abc123\n")
    monkeypatch.delenv("AIRTABLE_TOKEN", raising=False)
    monkeypatch.delenv("CODESPACES", raising=False)
    ob.FROM_DOTENV.discard("AIRTABLE_TOKEN")
    ob.load_dotenv(f)
    ob.cmd_env()
    assert "AIRTABLE_TOKEN" in capsys.readouterr().out
    assert "AIRTABLE_TOKEN" in ob.FROM_DOTENV
    monkeypatch.delenv("AIRTABLE_TOKEN")
    ob.FROM_DOTENV.discard("AIRTABLE_TOKEN")
