import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import outbound as ob
import ui

CSV = ("First Name,Company Name,Email,Email Status,Industry,Mobile Phone,Title\n"
       'Riya,"Acme <script>alert(1)</script>",riya@acme.com,Verified,SaaS,9876543210,CEO\n'
       "Tom,Dream MICE Co,tom@mice.com,Verified,Events,,CEO\n"
       "Ann,Beta,ann@beta.com,Guessed,SaaS,,CEO\n").encode()


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "QUEUE", tmp_path / "queue.csv")
    s = ui.make_server(0)
    port = s.server_address[1]
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    s.shutdown()


def get(url, host=None):
    req = urllib.request.Request(url, headers={"Host": host} if host else {})
    return urllib.request.urlopen(req).read().decode()


def post(url, data, ctype="application/x-www-form-urlencoded"):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype}, method="POST")
    return urllib.request.urlopen(req).read().decode()


def form(url, **kw):
    return post(url, urllib.parse.urlencode(kw, doseq=True).encode())


def upload(base, content=CSV, token=None):
    b = "XBOUNDARYX"
    body = (f'--{b}\r\nContent-Disposition: form-data; name="token"\r\n\r\n{token or ui.TOKEN}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="csv"; filename="a.csv"\r\nContent-Type: text/csv\r\n\r\n').encode() \
        + content + f"\r\n--{b}--\r\n".encode()
    return post(base + "/upload", body, f"multipart/form-data; boundary={b}")


def test_upload_drafts_only_relevant_rows_and_escapes_html(srv):
    page = upload(srv)
    rows = ob.load_queue()
    assert [r["email"] for r in rows] == ["riya@acme.com"] and rows[0]["approved"] == ""
    assert "Added 1 relevant rows" in page and "unverified 1" in page and "excluded 1" in page
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page      # no HTML injection from CSV content
    assert "needs review" in page and "Approve ticked" in page


def test_uploaded_csv_not_kept(srv, tmp_path):
    import glob, tempfile
    before = set(glob.glob(tempfile.gettempdir() + "/*.csv"))
    upload(srv)
    assert set(glob.glob(tempfile.gettempdir() + "/*.csv")) == before


def test_approve_and_edit_line_and_skip(srv):
    upload(srv)
    form(srv + "/line", token=ui.TOKEN, row=1, text="Acme builds freight tools")
    assert ob.load_queue()[0]["line"] == "Acme builds freight tools"
    assert "Acme builds freight tools" in ob.load_queue()[0]["body"]
    form(srv + "/line", token=ui.TOKEN, row=1, text="bad — dash")
    assert "dash" not in ob.load_queue()[0]["line"]
    form(srv + "/bulk", token=ui.TOKEN, action="approve", rows=["1"])
    assert ob.load_queue()[0]["approved"] == "y"
    assert "approved, waiting" in get(srv + "/?view=approved")
    form(srv + "/bulk", token=ui.TOKEN, action="skip", rows=["1"])
    assert ob.load_queue()[0]["replied"] == "skip"               # an approved row can still be dropped
    assert ob.due_first(ob.load_queue()) == []


def test_approve_all_only_touches_review_rows(srv):
    upload(srv)
    upload(srv, CSV.replace(b"riya@acme.com", b"riya2@acme.com"))
    form(srv + "/bulk", token=ui.TOKEN, action="approve_all")
    assert [r["approved"] for r in ob.load_queue()] == ["y", "y"]


def test_bad_token_and_bad_host_rejected(srv):
    with pytest.raises(urllib.error.HTTPError) as e:
        form(srv + "/bulk", token="nope", action="approve_all")
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        upload(srv, token="nope")
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        get(srv + "/", host="evil.example.com")
    assert e.value.code == 403
    assert ob.load_queue() == []


def test_nothing_sent_without_approval_via_ui(srv, monkeypatch):
    sent = []

    class S:
        def __init__(self, *a): pass
        def login(self, *a): pass
        def send_message(self, m): sent.append(m)
        def quit(self): pass
    monkeypatch.setattr(ob.smtplib, "SMTP_SSL", S)
    monkeypatch.setenv("GMAIL_USER", "a@b.c")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    upload(srv)
    form(srv + "/run", token=ui.TOKEN, what="send")
    for _ in range(50):
        if not ui.JOB["running"]:
            break
        time.sleep(0.1)
    assert sent == [] and "sent today 0" in get(srv + "/")


def test_job_runs_one_at_a_time_and_captures_log():
    ui.JOB.update(running=False, log=[])
    gate = threading.Event()
    assert ui.run_job("a", lambda: (ob.log("hello"), gate.wait(2)))
    assert not ui.run_job("b", lambda: None)            # second job refused while one runs
    gate.set()
    for _ in range(50):
        if not ui.JOB["running"]:
            break
        time.sleep(0.05)
    assert any("hello" in l for l in ui.JOB["log"])


def test_edits_blocked_while_job_running(srv):
    upload(srv)
    ui.JOB.update(running=True, name="x", log=[])
    try:
        form(srv + "/bulk", token=ui.TOKEN, action="approve_all")
        assert ob.load_queue()[0]["approved"] == ""
    finally:
        ui.JOB["running"] = False
