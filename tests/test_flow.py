import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

import pytest

import companies as co
import outbound as ob
import overlap as ov
import ui

PROOF = [{"client": "Delhivery", "industry": "logistics", "persona": "VP Sales", "deal": "ran their outbound pipeline and closed Rs 2.7 Cr", "name_usable": True},
         {"client": "Zepto", "industry": "quick commerce", "persona": "Head of Growth", "deal": "built the BD desk", "name_usable": False}]


# ---- ICP ---------------------------------------------------------------------
@pytest.mark.parametrize("stage,emp,ok", [("Seed", "12", True), ("Pre Seed", "3", True), ("Angel", "", True), ("Series A", "20", False),
                                           ("Seed", "120", False), ("Seed", "1", False), ("", "10", False)])
def test_icp(stage, emp, ok):
    assert co.icp({"Funding stage": stage, "Employees (Apollo)": emp})[0] is ok


# ---- calendar ------------------------------------------------------------------
def comp(i, **kw):
    c = {"_id": f"rec{i}", "Company": f"Co{i}", "Shortlist": True, "Status": "Shortlisted", "jobs": [{"Title": f"FO {i}", "Job URL": f"https://x.io/{i}"}]}
    c.update(kw)
    return c


def test_schedule_blocks_skip_weekends_and_done():
    cs = [comp(i) for i in range(1, 8)] + [comp(8, Status="Applied"), comp(9, Shortlist=False), comp(10, jobs=[])]
    blocks = co.schedule(cs, per_block=5, today=date(2026, 10, 15))          # Thursday
    assert [len(b["items"]) for b in blocks] == [5, 2]
    assert blocks[0]["start"].date() == date(2026, 10, 16)                   # Friday
    assert blocks[1]["start"].date() == date(2026, 10, 19)                   # Monday, weekend skipped
    assert blocks[0]["start"].hour == 11 and blocks[0]["start"].utcoffset().total_seconds() == 19800


def test_ics_valid_and_stable():
    blocks = co.schedule([comp(1), comp(2, Company="Café, Inc; \"x\"")], today=date(2026, 10, 15))
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    a, b = co.make_ics(blocks, now), co.make_ics(blocks, now)
    assert a == b and a.startswith("BEGIN:VCALENDAR\r\n") and a.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261016T053000Z" in a and "TRIGGER:-PT10M" in a and "SUMMARY:Apply to 2 Founder's Office jobs" in a
    assert all(len(l.encode()) <= 75 for l in a.split("\r\n"))
    assert "https://x.io/1" in a.replace("\r\n ", "")                       # unfolded content intact
    assert a.count("BEGIN:VEVENT") == 1 and "\\," in a and "\\;" in a


# ---- overlap -------------------------------------------------------------------
def test_overlap_levels_and_name_privacy():
    hi = ov.find_overlap("Acme", "", "Our customers include Delhivery and others", PROOF)
    assert hi["level"] == "High" and "Delhivery" in hi["line"] and hi["line"].startswith("I saw Acme works with")
    priv = ov.find_overlap("Acme", "", "trusted by Zepto and many more", PROOF)
    assert priv["level"] == "High" and "Zepto" not in priv["line"] + priv["note"] and "quick commerce" in priv["line"]
    med = ov.find_overlap("Acme", "", "We build software for logistics teams", PROOF)
    assert med["level"] == "Medium" and "logistics" in med["line"]
    assert ov.find_overlap("Acme", "Logistics", "nothing relevant", PROOF)["level"] == "Medium"   # industry from Apollo
    assert ov.find_overlap("Acme", "", "nothing", PROOF)["level"] == ""
    assert ov.find_overlap("Acme", "", "Delhivery", [])["note"] == "no client list uploaded"
    assert ov.find_overlap("Acme", "", "xDelhiveryx", PROOF)["level"] == ""                         # whole words only


def test_overlap_line_rules():
    long = [dict(PROOF[0], deal="did " + "very " * 40 + "well")]
    r = ov.find_overlap("Acme", "", "Delhivery", long)
    assert r["level"] == "High" and r["line"] == "" and "too long" in r["note"]
    dash = [dict(PROOF[0], deal="built — a desk")]
    assert ov.find_overlap("Acme", "", "Delhivery", dash)["line"] == ""


def test_load_proof(tmp_path):
    p = tmp_path / "proof.csv"
    p.write_text("Client,Industry,Persona,Deal,Name Usable\nDelhivery,logistics,VP,did x,y\nZepto,qc,H,did y,n\n,,,,\n")
    rows = ov.load_proof(p)
    assert [r["name_usable"] for r in rows] == [True, False] and ov.load_proof(tmp_path / "none.csv") == []


def test_private_addresses_never_fetched(monkeypatch):
    for u in ("http://127.0.0.1/", "http://localhost/x", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd", "ftp://a.com/"):
        assert not ov.safe_url(u)
    assert ov.default_fetch("http://127.0.0.1:1/") == (0, "")


def test_site_text_robots_pages_and_cache(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return 200, "User-agent: *\nDisallow: /customers\n"
        return 200, "<html><script>x()</script><body><h1>About us</h1><img alt='Delhivery'></body></html>"
    t = ov.site_text("https://www.acme.io/", tmp_path, fetch)
    assert "About us" in t and "Delhivery" in t and "x()" not in t
    assert not any(u.endswith("/customers") for u in calls)                  # robots.txt respected
    n = len(calls)
    assert ov.site_text("acme.io", tmp_path, fetch) == t and len(calls) == n    # cached, no refetch
    assert ov.site_text("blocked.io", tmp_path, lambda u: (500, "")) == ""      # cannot read robots -> read nothing


# ---- POC import ------------------------------------------------------------------
@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "QUEUE", tmp_path / "queue.csv")
    return tmp_path


def poc_csv(tmp_path, rows, header="Company Linkedin Profile,POC First Name,POC Contact No,POC Email"):
    p = tmp_path / "poc.csv"
    p.write_text(header + "\n" + "\n".join(rows) + "\n")
    return p


COMPS = [{"_id": "r1", "Company": "Acme", "Company LinkedIn URL": "https://www.linkedin.com/company/acme", "Shortlist": True,
          "Website": "https://acme.io", "Industry (Apollo)": "Software", "About (Apollo)": "We build freight tools"},
         {"_id": "r2", "Company": "Nope", "Company LinkedIn URL": "https://www.linkedin.com/company/nope", "Shortlist": False}]


def fake_fetch(url):
    if url.endswith("robots.txt"):
        return 404, ""
    return 200, "<body>Customers: Delhivery, Blue Dart</body>"


def test_poc_import_shortlist_verified_and_overlap(q):
    p = poc_csv(q, ["http://linkedin.com/company/Acme/,Riya,+91 98765 43210,riya@acme.io",
                    "https://www.linkedin.com/company/nope,Tom,,tom@nope.io",
                    "https://www.linkedin.com/company/acme,Sam,,riya@acme.io",
                    "https://www.linkedin.com/company/acme,,,x@acme.io"])
    added, skipped = co.import_pocs(p, True, COMPS, PROOF, q / "cache", fake_fetch, log=lambda *a: None)
    rows = ob.load_queue()
    assert added == 1 and skipped["company not shortlisted"] == 1 and skipped["duplicate"] == 1 and skipped["missing name/email"] == 1
    r = rows[0]
    assert r["email"] == "riya@acme.io" and r["company"] == "Acme" and r["phone"] == "919876543210" and r["approved"] == ""
    assert "Delhivery" in r["line"] and "Delhivery" in r["body"] and r["overlap_note"].startswith("same client")
    assert r["company_li"] == "https://www.linkedin.com/company/acme"


def test_poc_import_unverified_skipped_unless_confirmed(q):
    row = ["https://www.linkedin.com/company/acme,Riya,,riya@acme.io"]
    assert co.import_pocs(poc_csv(q, row), False, COMPS, PROOF, q / "c", fake_fetch, log=lambda *a: None)[0] == 0
    assert ob.load_queue() == []
    assert co.import_pocs(poc_csv(q, row), True, COMPS, PROOF, q / "c", fake_fetch, log=lambda *a: None)[0] == 1


def test_poc_status_column_overrides_confirmation(q):
    p = poc_csv(q, ["https://www.linkedin.com/company/acme,Riya,,riya@acme.io,Guessed"],
                header="Company Linkedin Profile,POC First Name,POC Contact No,POC Email,Email Status")
    assert co.import_pocs(p, True, COMPS, PROOF, q / "c", fake_fetch, log=lambda *a: None)[0] == 0   # per-row status wins


def test_medium_overlap_is_only_a_suggestion(q):
    comps = [dict(COMPS[0], Website="https://m.io")]
    f = lambda u: (404, "") if u.endswith("robots.txt") else (200, "<body>freight for logistics firms</body>")
    co.import_pocs(poc_csv(q, ["https://www.linkedin.com/company/acme,Riya,,riya@acme.io"]), True, comps, PROOF, q / "c", f, log=lambda *a: None)
    r = ob.load_queue()[0]
    assert r["line"] == ob.DEFAULT_LINE.format(company="Acme") and "logistics" in r["suggested_line"] and r["overlap_note"].startswith("same industry")


# ---- UI flow with Airtable mocked ----------------------------------------------------
@pytest.fixture
def srv(q, monkeypatch):
    monkeypatch.setenv("AIRTABLE_TOKEN", "t")
    monkeypatch.setenv("AIRTABLE_BASE_ID", "appXXXXXXXXXXXXXX")
    s = ui.make_server(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def post(url, **kw):
    data = urllib.parse.urlencode(kw, doseq=True).encode()
    return urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST"))


def multipart(url, fields, filename, content):
    b = "BBB"
    body = "".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items()).encode()
    body += f'--{b}\r\nContent-Disposition: form-data; name="csv"; filename="{filename}"\r\n\r\n'.encode() + content + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"}, method="POST")
    return urllib.request.urlopen(req).read().decode()


def test_shortlist_page_highlights_icp_and_auto_ticks(srv, monkeypatch):
    comps = [dict(comp(1), **{"Apollo match": "linkedin", "Funding stage": "Seed", "Employees (Apollo)": "10", "Shortlist": False}),
             dict(comp(2), **{"Apollo match": "domain", "Funding stage": "Series B", "Employees (Apollo)": "300", "Shortlist": False}),
             dict(comp(3), **{"Funding stage": "Seed"})]                                    # no Apollo match: not listed
    monkeypatch.setattr(co, "fetch", lambda: comps)
    page = urllib.request.urlopen(srv + "/?tab=shortlist").read().decode()
    assert "1 fit the ICP" in page and "<tr class='icp'>" in page and "Co3" not in page and "checked" not in page
    auto = urllib.request.urlopen(srv + "/?tab=shortlist&auto=1").read().decode()
    assert auto.count(" checked") == 1                                                   # only the ICP row is pre-ticked


def test_shortlist_save_ics_and_applied(srv, monkeypatch):
    saved = {}
    monkeypatch.setattr(co, "save_shortlist", lambda sel, shown: saved.update(sel=sel, shown=shown) or 2)
    monkeypatch.setattr(co, "fetch", lambda: [comp(1), comp(2)])
    monkeypatch.setattr(co, "mark_applied", lambda ids: saved.update(applied=ids) or len(ids))
    r = post(srv + "/shortlist", token=ui.TOKEN, do="ics", rows=["rec1", "rec2"], shown="rec1,rec2,rec3", per="1", time="10:30")
    assert r.headers["Content-Type"].startswith("text/calendar") and "attachment" in r.headers["Content-Disposition"]
    ics = r.read().decode()
    assert ics.count("BEGIN:VEVENT") == 2 and "T050000Z" in ics            # 10:30 IST = 05:00 UTC, one company per block
    assert saved["sel"] == ["rec1", "rec2"] and saved["shown"] == ["rec1", "rec2", "rec3"]
    post(srv + "/shortlist", token=ui.TOKEN, do="applied", rows=["rec1"], shown="rec1")
    assert saved["applied"] == ["rec1"]
    with pytest.raises(urllib.error.HTTPError):
        post(srv + "/shortlist", token="bad", do="save")


def test_company_upload_and_poc_upload_through_ui(srv, q, monkeypatch):
    monkeypatch.setattr(co, "import_apollo_companies", lambda path: {"apollo": 3, "matched": 2, "unmatched": 1, "methods": {"linkedin": 2}})
    page = multipart(srv + "/companies/upload", {"token": ui.TOKEN}, "a.csv", b"Company Name\nX\n")
    assert "matched 2" in page and "pick your shortlist" in page
    monkeypatch.setattr(co, "fetch", lambda: COMPS)
    monkeypatch.setattr(ov, "default_fetch", fake_fetch)
    (q / "proof.csv").write_text("client,industry,persona,deal,name_usable\nDelhivery,logistics,VP,ran their outbound pipeline,y\n")
    monkeypatch.setattr(ui, "cache_dir", lambda: q / "cache")
    multipart(srv + "/poc/upload", {"token": ui.TOKEN, "verified": "yes"}, "p.csv",
              b"Company Linkedin Profile,POC First Name,POC Contact No,POC Email\nhttps://www.linkedin.com/company/acme,Riya,,riya@acme.io\n")
    for _ in range(100):
        if not ui.JOB["running"]:
            break
        time.sleep(0.05)
    r = ob.load_queue()
    assert len(r) == 1 and r[0]["approved"] == "" and "Delhivery" in r[0]["line"]
    page = urllib.request.urlopen(srv + "/?tab=review").read().decode()
    assert "Overlap: same client" in page


def test_unverified_poc_upload_adds_nothing_via_ui(srv, q, monkeypatch):
    monkeypatch.setattr(co, "fetch", lambda: COMPS)
    monkeypatch.setattr(ui, "cache_dir", lambda: q / "cache")
    multipart(srv + "/poc/upload", {"token": ui.TOKEN, "verified": "no"}, "p.csv",
              b"Company Linkedin Profile,POC First Name,POC Contact No,POC Email\nhttps://www.linkedin.com/company/acme,Riya,,riya@acme.io\n")
    for _ in range(100):
        if not ui.JOB["running"]:
            break
        time.sleep(0.05)
    assert ob.load_queue() == []


def test_people_csv_hint_and_empty_queue_lands_on_companies(srv, q):
    page = multipart(srv + "/upload", {"token": ui.TOKEN}, "c.csv", b"Company Name,Industry\nAcme,SaaS\nBeta,SaaS\n")
    assert "company-only file" in page
    assert "Apollo company CSV" in urllib.request.urlopen(srv + "/").read().decode()      # empty queue -> starts at step 1
