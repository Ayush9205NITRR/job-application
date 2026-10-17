import json
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

import pytest

import companies as co
import outbound as ob
import overlap as ov
import ui

CLIENTS = ov.parse_clients("Raphe\nMongo DB | MongoDB\nSprinto\nGFI\n# Hidden\nTeam Computers")


# ---- money / numbers ------------------------------------------------------------
@pytest.mark.parametrize("raw,n", [("$2.5M", 2.5e6), ("1,200,000", 1.2e6), ("5 Cr", 5e7), ("750K", 7.5e5), ("USD 3B", 3e9), ("10-50", 10), ("", None), ("n/a", None)])
def test_parse_money(raw, n):
    assert co.parse_money(raw) == n


def test_serialize_numbers_for_live_filtering():
    c = {"_id": "r1", "Company": "Acme", "Funding stage": " Seed ", "Total funding": "$2M", "Annual revenue (Apollo)": "1.5M",
         "Employees (Apollo)": "12", "Apollo match": "linkedin", "jobs": [{"Title": "FO", "Job URL": "u"}]}
    s = co.serialize(c, {"r1": {"names": ["X"], "pick": ["X"]}})
    assert (s["stage"], s["funding_n"], s["revenue_n"], s["employees_n"], s["matched"]) == ("Seed", 2e6, 1.5e6, 12, True)
    assert s["jobs"][0]["url"] == "u" and s["inter"]["pick"] == ["X"]
    assert co.serialize({"_id": "r2", "Company": "B"})["matched"] is False
    assert co.serialize({"_id": "r3", "Company": "C", "Status": "Excluded"})["excluded"] is True


# ---- calendar ------------------------------------------------------------------------
def comp(i, **kw):
    c = {"_id": f"rec{i}", "Company": f"Co{i}", "Shortlist": True, "Status": "Shortlisted", "jobs": [{"Title": f"FO {i}", "Job URL": f"https://x.io/{i}"}]}
    c.update(kw)
    return c


def test_schedule_blocks_skip_weekends_and_done():
    cs = [comp(i) for i in range(1, 8)] + [comp(8, Status="Applied"), comp(9, Shortlist=False), comp(10, jobs=[])]
    blocks = co.schedule(cs, per_block=5, today=date(2026, 10, 15))          # Thursday
    assert [len(b["items"]) for b in blocks] == [5, 2]
    assert blocks[0]["start"].date() == date(2026, 10, 16) and blocks[1]["start"].date() == date(2026, 10, 19)
    assert blocks[0]["start"].hour == 11 and blocks[0]["start"].utcoffset().total_seconds() == 19800


def test_ics_valid_and_stable():
    blocks = co.schedule([comp(1), comp(2, Company="Café, Inc; \"x\"")], today=date(2026, 10, 15))
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    a = co.make_ics(blocks, now)
    assert a == co.make_ics(blocks, now) and a.startswith("BEGIN:VCALENDAR\r\n") and a.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261016T053000Z" in a and "TRIGGER:-PT10M" in a and "SUMMARY:Apply to 2 Founder's Office jobs" in a
    assert all(len(l.encode()) <= 75 for l in a.split("\r\n")) and "https://x.io/1" in a.replace("\r\n ", "")
    assert "\\," in a and "\\;" in a


# ---- my clients vs their site --------------------------------------------------------
def test_parse_clients_and_matching():
    assert [c["name"] for c in CLIENTS] == ["Raphe", "Mongo DB", "Sprinto", "GFI", "Team Computers"]       # '# Hidden' left out
    assert ov.match_clients("Trusted by MongoDB, sprinto and Team  Computers", CLIENTS) == ["Mongo DB", "Sprinto", "Team Computers"]
    assert ov.match_clients("we like GFIs and xSprintox", CLIENTS) == []                                    # whole words only
    assert ov.match_clients("Hidden gem", CLIENTS) == [] and ov.match_clients("", CLIENTS) == []
    assert ov.match_clients("Mongo DB Atlas", CLIENTS) == ["Mongo DB"]


def test_pitch_line_is_truthful_and_short():
    assert ov.pitch_line("Acme", ["Sprinto"]) == "I saw Acme works with Sprinto. I have worked with Sprinto too."
    assert ov.pitch_line("Acme", ["Sprinto", "GFI"]) == "I saw Acme works with Sprinto and GFI. I have worked with them too."
    assert ov.pitch_line("Acme", ["A", "B", "C"]).startswith("I saw Acme works with A, B and C.")
    assert ov.pitch_line("Acme", []) == ""
    assert all(len(ov.pitch_line("Acme", ["A", "B", "C"]).split()) <= 30 for _ in [0])


def test_private_addresses_never_fetched():
    for u in ("http://127.0.0.1/", "http://localhost/x", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd", "ftp://a.com/"):
        assert not ov.safe_url(u)
    assert ov.default_fetch("http://127.0.0.1:1/") == (0, "")


def test_site_text_robots_pages_and_cache(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return 200, "User-agent: *\nDisallow: /customers\n"
        return 200, "<html><script>x()</script><body><h1>About us</h1><img alt='Sprinto'></body></html>"
    t = ov.site_text("https://www.acme.io/", tmp_path, fetch)
    assert "About us" in t and "Sprinto" in t and "x()" not in t and not any(u.endswith("/customers") for u in calls)
    n = len(calls)
    assert ov.site_text("acme.io", tmp_path, fetch) == t and len(calls) == n
    assert ov.site_text("blocked.io", tmp_path, lambda u: (500, "")) == ""


def test_find_intersections_parallel_and_notes(tmp_path):
    def fetch(url):
        if url.endswith("robots.txt"):
            return 404, ""
        return 200, "<body>customers: Sprinto, GFI</body>" if "has.io" in url else "<body>nothing here</body>"
    cs = [{"_id": "a", "Website": "https://has.io"}, {"_id": "b", "Website": "https://none.io"}, {"_id": "c", "Website": ""}]
    seen = []
    res = co.find_intersections(cs, CLIENTS, tmp_path, fetch, progress=lambda d, t: seen.append((d, t)))
    assert res["a"]["names"] == ["Sprinto", "GFI"] and res["a"]["pick"] == ["Sprinto", "GFI"]
    assert res["b"]["note"] == "no overlap found" and res["c"]["note"] == "no website known" and seen[-1] == (3, 3)


# ---- contacts import ----------------------------------------------------------------------
@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "QUEUE", tmp_path / "queue.csv")
    return tmp_path


def contacts_csv(tmp_path, rows, header="Company Linkedin,POC First Name,POC Email,POC Contact Detail"):
    p = tmp_path / "poc.csv"
    p.write_text(header + "\n" + "\n".join(rows) + "\n")
    return p


COMPS = [{"_id": "r1", "Company": "Acme", "Company LinkedIn URL": "https://www.linkedin.com/company/acme", "Shortlist": True},
         {"_id": "r2", "Company": "Beta", "Company LinkedIn URL": "https://www.linkedin.com/company/beta", "Shortlist": True},
         {"_id": "r3", "Company": "Nope", "Company LinkedIn URL": "https://www.linkedin.com/company/nope", "Shortlist": False}]


def test_contacts_pitch_vs_generic_and_skips(q):
    p = contacts_csv(q, ["http://linkedin.com/company/Acme/,Riya,riya@acme.io,+91 98765 43210",
                         "https://www.linkedin.com/company/beta,Sam Lee,sam@beta.io,",
                         "https://www.linkedin.com/company/nope,Tom,tom@nope.io,",
                         "https://www.linkedin.com/company/acme,Dup,riya@acme.io,",
                         "https://www.linkedin.com/company/acme,,x@acme.io,"])
    added, skipped, pitched = co.import_pocs(p, True, COMPS, {"r1": ["Sprinto", "GFI"]}, log=lambda *a: None)
    rows = ob.load_queue()
    assert (added, pitched) == (2, 1) and skipped["company not selected"] == 1 and skipped["duplicate"] == 1 and skipped["missing name/email"] == 1
    a, b = rows
    assert a["first"] == "Riya" and a["phone"] == "919876543210" and a["approved"] == "" and a["pitch"] == "Sprinto; GFI"
    assert a["line"] == "I saw Acme works with Sprinto and GFI. I have worked with them too." and a["line"] in a["body"] and a["body"].startswith("Hi Riya,")
    assert b["first"] == "Sam" and b["pitch"] == "" and b["line"] == ob.DEFAULT_LINE.format(company="Beta") and b["overlap_note"] == "generic"


def test_contacts_unverified_skipped_unless_confirmed_or_status_column(q):
    row = ["https://www.linkedin.com/company/acme,Riya,riya@acme.io,"]
    assert co.import_pocs(contacts_csv(q, row), False, COMPS, log=lambda *a: None)[0] == 0 and ob.load_queue() == []
    assert co.import_pocs(contacts_csv(q, row), True, COMPS, log=lambda *a: None)[0] == 1
    p = contacts_csv(q, ["https://www.linkedin.com/company/beta,Sam,sam@beta.io,,Guessed"],
                     header="Company Linkedin,POC First Name,POC Email,POC Contact Detail,Email Status")
    assert co.import_pocs(p, True, COMPS, log=lambda *a: None)[0] == 0            # per-row status wins over the confirmation


# ---- API -------------------------------------------------------------------------------------
@pytest.fixture
def srv(q, monkeypatch):
    for k in ("AIRTABLE_TOKEN", "AIRTABLE_BASE_ID"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(co, "_cache", {"rows": [dict(comp(1), **{"Apollo match": "linkedin", "Funding stage": "Seed", "Employees (Apollo)": "10",
                                                                  "Company LinkedIn URL": "https://www.linkedin.com/company/acme", "Website": "https://acme.io"}),
                                                dict(comp(2), **{"Apollo match": "domain", "Funding stage": "Series B", "Employees (Apollo)": "300"})]})
    monkeypatch.setattr(co, "airtable_ready", lambda: True)
    monkeypatch.setattr(co, "save_shortlist", lambda sel, shown: len(sel))
    s = ui.make_server(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def call(base, path, body=None, raw=None, headers=None, token=None):
    h = {"X-Token": ui.TOKEN if token is None else token, **(headers or {})}
    data = None
    if raw is not None:
        data, h["Content-Type"] = raw, "text/csv"
    elif body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=h, method="POST" if data is not None else "GET")).read())
    except urllib.error.HTTPError as e:
        return {"_code": e.code, **json.loads(e.read())}


def wait_job():
    for _ in range(200):
        if not ui.JOB["running"]:
            return
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def test_index_has_token_and_is_not_cached(srv):
    r = urllib.request.urlopen(srv + "/")
    page = r.read().decode()
    assert ui.TOKEN in page and "__TOKEN__" not in page and r.headers["Cache-Control"] == "no-store"


def test_bad_token_and_bad_host_rejected(srv):
    assert call(srv, "/api/state", token="nope")["_code"] == 403
    assert call(srv, "/api/queue/approve", {"rows": "all"}, token="nope")["_code"] == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(urllib.request.Request(srv + "/", headers={"Host": "evil.example.com"}))
    assert e.value.code == 403


def test_allowed_host_rules():
    env = {"CODESPACE_NAME": "ayush-abc123", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev"}
    assert ui.allowed_host("127.0.0.1:8765", {}, 8765) and ui.allowed_host("ayush-abc123-8765.app.github.dev", env, 8765)
    assert not ui.allowed_host("ayush-abc123-8765.app.github.dev", {}, 8765) and not ui.allowed_host("other-8765.app.github.dev", env, 8765)
    assert not ui.allowed_host("ayush-abc123-9999.app.github.dev", env, 8765) and not ui.allowed_host("evil.com", env, 8765)


def test_companies_endpoint_serialised_and_cached(srv):
    r = call(srv, "/api/companies")
    assert r["error"] == "" and [c["stage"] for c in r["companies"]] == ["Seed", "Series B"] and r["companies"][0]["employees_n"] == 10


def test_selection_and_settings_persist(srv):
    call(srv, "/api/select", {"selected": ["rec1"], "shown": ["rec1", "rec2"]})
    call(srv, "/api/icp", {"icp": {"stages": ["Seed"], "emp_min": 5, "emp_max": 20, "rev_max": None, "funding_max": 1e7}})
    assert call(srv, "/api/clients", {"text": "A\nB | b"})["n"] == 2
    st = call(srv, "/api/state")
    assert st["selected"] == ["rec1"] and st["icp"]["emp_max"] == 20 and st["clients"] == "A\nB | b"
    assert st["env"]["airtable"] is True and st["stats"]["cap"] == 5


def test_intersect_job_then_pick_then_contacts_pitch(srv, monkeypatch):
    ov_fetch = lambda url: (404, "") if url.endswith("robots.txt") else (200, "<body>our customers: Sprinto and GFI</body>")
    monkeypatch.setattr(ov, "default_fetch", ov_fetch)
    monkeypatch.setattr(ui, "cache_dir", lambda: ob.QUEUE.with_name("cache"))
    call(srv, "/api/clients", {"text": "Sprinto\nGFI\nRaphe"})
    assert call(srv, "/api/intersect", {"ids": ["rec1"]})["started"] is True
    wait_job()
    c = call(srv, "/api/companies")["companies"][0]
    assert c["inter"]["names"] == ["Sprinto", "GFI"] and c["inter"]["pick"] == ["Sprinto", "GFI"]
    call(srv, "/api/pick", {"id": "rec1", "names": ["GFI", "Nope"]})                   # unknown names are ignored
    assert call(srv, "/api/companies")["companies"][0]["inter"]["pick"] == ["GFI"]
    call(srv, "/api/select", {"selected": ["rec1"], "shown": ["rec1"]})
    r = call(srv, "/api/contacts", raw=b"Company Linkedin,POC First Name,POC Email,POC Contact Detail\nhttps://www.linkedin.com/company/acme,Riya,riya@acme.io,9876543210\n",
             headers={"X-Verified": "yes"})
    assert (r["added"], r["pitched"], r["generic"]) == (1, 1, 0)
    row = call(srv, "/api/state")["queue"][0]
    assert row["pitch"] == ["GFI"] and "GFI" in row["line"] and "Sprinto" not in row["line"] and row["status"] == "review"


def test_contacts_for_unselected_company_not_added(srv):
    call(srv, "/api/select", {"selected": [], "shown": ["rec1"]})
    r = call(srv, "/api/contacts", raw=b"Company Linkedin,POC First Name,POC Email,POC Contact Detail\nhttps://www.linkedin.com/company/acme,Riya,riya@acme.io,\n", headers={"X-Verified": "yes"})
    assert r["added"] == 0 and r["skipped"]["company not selected"] == 1
    r = call(srv, "/api/contacts", raw=b"Company Linkedin,POC First Name,POC Email,POC Contact Detail\nhttps://www.linkedin.com/company/acme,Riya,riya@acme.io,\n")
    assert r["added"] == 0                                                          # no X-Verified header = not verified


def test_queue_edit_approve_unapprove_skip_and_send_guard(srv, monkeypatch):
    rows = []
    for i in (1, 2, 3):
        r = {c: "" for c in ob.COLUMNS}
        r.update(email=f"p{i}@a.io", first=f"P{i}", company=f"A{i}", pitch="X" if i == 1 else "", **ob.build(f"P{i}", f"A{i}", f"p{i}@a.io"))
        rows.append(r)
    ob.save_queue(rows)
    assert call(srv, "/api/queue/approve", {"rows": "pitch"})["changed"] == 1
    assert [r["status"] for r in call(srv, "/api/state")["queue"]] == ["approved", "review", "review"]
    assert call(srv, "/api/queue/unapprove", {"rows": [1]})["changed"] == 1
    r = call(srv, "/api/queue/line", {"row": 2, "text": "Fresh line"})
    assert r["queue"][1]["line"] == "Fresh line" and "Fresh line" in r["queue"][1]["body"]
    assert call(srv, "/api/queue/line", {"row": 2, "text": "bad — dash"})["errors"]
    assert call(srv, "/api/queue/skip", {"rows": [3]})["queue"][2]["status"] == "closed"
    assert call(srv, "/api/queue/approve", {"rows": "all"})["changed"] == 2
    monkeypatch.delenv("GMAIL_USER", raising=False)
    assert call(srv, "/api/send", {"what": "send"})["_code"] == 400                  # no Gmail secrets: refused, nothing started


def test_nothing_sent_without_approval_via_api(srv, monkeypatch):
    sent = []

    class S:
        def __init__(self, *a): pass
        def login(self, *a): pass
        def send_message(self, m): sent.append(m)
        def quit(self): pass
    monkeypatch.setattr(ob.smtplib, "SMTP_SSL", S)
    monkeypatch.setenv("GMAIL_USER", "a@b.c")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    r = {c: "" for c in ob.COLUMNS}
    r.update(email="p@a.io", first="P", company="A", **ob.build("P", "A", "p@a.io"))
    ob.save_queue([r])
    assert call(srv, "/api/send", {"what": "send"})["started"] is True
    wait_job()
    assert sent == [] and call(srv, "/api/state")["stats"]["sent_today"] == 0


def test_queue_locked_while_send_runs(srv):
    r = {c: "" for c in ob.COLUMNS}
    r.update(email="p@a.io", first="P", company="A", **ob.build("P", "A", "p@a.io"))
    ob.save_queue([r])
    ui.JOB.update(running=True, locks_queue=True)
    try:
        assert call(srv, "/api/queue/approve", {"rows": "all"})["_code"] == 409
        assert ob.load_queue()[0]["approved"] == ""
    finally:
        ui.JOB.update(running=False, locks_queue=False)


def test_ics_endpoint(srv):
    r = call(srv, "/api/ics", {"ids": ["rec1"], "per": 5, "hour": 10, "minute": 30})
    assert r["blocks"] == 1 and "T050000Z" in r["ics"] and "https://x.io/1" in r["ics"].replace("\r\n ", "")
    assert call(srv, "/api/ics", {"ids": []})["_code"] == 400


def test_apollo_preview_writes_nothing_then_commit_writes(srv, monkeypatch, tmp_path):
    wrote = []
    monkeypatch.setattr(co, "import_apollo_companies", lambda path: wrote.append(open(path).read()) or {"apollo": 2, "matched": 2, "unmatched": 0, "methods": {"linkedin": 2}})
    csv = (b"Company Name,Company Linkedin Url,Funding Stage,Funding,Revenue,# Employees\n"
           b"Acme,http://www.linkedin.com/company/acme/,Seed,$2M,$1M,12\nBeta,https://linkedin.com/company/Beta-Labs,Series B,$40M,$20M,300\nGhost,https://linkedin.com/company/ghost,Seed,,,5\n")
    p = call(srv, "/api/apollo", raw=csv)
    assert (p["csv_rows"], p["apollo"], p["unmatched"], p["airtable_companies"]) == (3, 3, 2, 2)   # Acme matches the cached company by LinkedIn slug, the other two do not
    assert wrote == []                                                                              # preview never saves
    assert call(srv, "/api/apollo/commit", {})["matched"] == 2 and wrote and "Acme" in wrote[0]
    assert call(srv, "/api/apollo/commit", {})["_code"] == 400                                      # nothing pending any more
    call(srv, "/api/apollo", raw=csv)
    assert call(srv, "/api/apollo/discard", {})["ok"] and call(srv, "/api/apollo/commit", {})["_code"] == 400


def test_preview_never_calls_a_hard_excluded_company_icp(tmp_path):
    comps = [{"_id": "r1", "Company": "Staffing Co", "Company LinkedIn URL": "https://www.linkedin.com/company/staffco", "Status": "Excluded"},
             {"_id": "r2", "Company": "Real Startup", "Company LinkedIn URL": "https://www.linkedin.com/company/real", "Status": "Needs review"}]
    f = tmp_path / "a.csv"
    f.write_text("Company Name,Company Linkedin Url,Funding Stage,# Employees\nStaffing Co,linkedin.com/company/staffco,Seed,10\nReal Startup,linkedin.com/company/real,Seed,10\n")
    p = co.preview_apollo(f, comps)
    assert p["icp"] == 1 and {r["company"]: r["icp"] for r in p["rows"]} == {"Staffing Co": False, "Real Startup": True}
    assert "hard-excluded" in next(r["why"] for r in p["rows"] if r["company"] == "Staffing Co")


def test_preview_apollo_matches_icp_and_reports_missing_columns(tmp_path):
    comps = [{"_id": "r1", "Company": "Acme Pvt Ltd", "Company LinkedIn URL": "https://www.linkedin.com/company/acme"},
             {"_id": "r2", "Company": "Beta", "Company LinkedIn URL": "https://www.linkedin.com/company/beta-labs"},
             {"_id": "r3", "Company": "Gamma", "Company LinkedIn URL": "https://www.linkedin.com/company/gamma", "Website": "https://gamma.io"}]
    f = tmp_path / "a.csv"
    f.write_text("Company Name,Company Linkedin Url,Website,Funding Stage,Funding,# Employees\n"
                 "Acme,http://www.linkedin.com/company/ACME/?trk=x,,Seed,$2M,12\n"
                 "Beta,https://linkedin.com/company/beta-labs,,Series B,$40M,300\n"
                 "Gamma Inc,,https://www.gamma.io/about,Pre Seed,$1M,6\n"
                 "Ghost,https://linkedin.com/company/ghost,,Seed,,5\n")
    p = co.preview_apollo(f, comps)
    assert (p["apollo"], p["matched"], p["unmatched"], p["icp"]) == (4, 3, 1, 2)
    assert p["methods"] == {"linkedin": 2, "domain": 1} and p["unmatched_sample"] == ["Ghost"]
    assert p["fields_found"]["Annual revenue (Apollo)"] == 0 and p["fields_found"]["Funding stage"] == 4     # revenue column absent: flagged
    assert [r["company"] for r in p["rows"]][:2] in (["Acme Pvt Ltd", "Gamma"], ["Gamma", "Acme Pvt Ltd"]) and p["rows"][-1]["icp"] is False


def test_scrape_job_runs_pipeline_and_records_summary(srv, monkeypatch):
    import pipeline
    monkeypatch.setenv("APIFY_TOKEN", "t")
    seen = {}
    monkeypatch.setattr(pipeline, "scrape", lambda kws, loc, lim, dp=None, log=print: seen.update(kws=kws, loc=loc, lim=lim, dp=dp) or [{"id": "1"}] * 3)
    monkeypatch.setattr(pipeline, "run", lambda raw, log=print: {"companies": 2, "jobs": 3, "new": 2})
    assert call(srv, "/api/scrape", {"keywords": "Founder's Office\nChief of Staff", "location": "India", "limit": 50, "date_posted": "pastMonth"})["started"] is True
    wait_job()
    assert seen == {"kws": ["Founder's Office", "Chief of Staff"], "loc": "India", "lim": 50, "dp": "pastMonth"}
    st = call(srv, "/api/state")
    assert st["last_scrape"]["jobs"] == 3 and st["keywords"].startswith("Founder's Office")
    call(srv, "/api/scrape", {"keywords": "X", "date_posted": "drop table"}); wait_job()
    assert seen["dp"] is None                                                    # unknown date window is ignored, not passed on
    assert call(srv, "/api/scrape", {"keywords": ""})["_code"] == 400


def test_verify_data_analyse_and_roundtrip():
    import verify_data as v
    comps = [{"_id": "1", "Company": "Acme", "Company LinkedIn URL": "https://www.linkedin.com/company/acme", "Status": "Needs review", "jobs": [{}], "Website": "https://acme.io"},
             {"_id": "2", "Company": "Beta", "Company LinkedIn URL": "https://www.linkedin.com/company/12345", "Status": "Excluded", "jobs": []},
             {"_id": "3", "Company": "Gamma", "Company LinkedIn URL": "https://www.linkedin.com/company/gamma", "jobs": [{}, {}]},
             {"_id": "4", "Company": "No Url", "jobs": []}]
    r = v.analyse(comps)
    assert (r["companies"], r["jobs"], r["no_job"], r["valid_slug"], r["numeric_slug"], r["website"]) == (4, 3, 2, 3, 1, 1)
    assert r["status"] == {"Needs review": 1, "Excluded": 1, "(blank)": 2}
    rt = v.roundtrip(comps)
    assert rt == {"rows": 3, "matched": 3, "by_linkedin": 3, "unmatched": 0}                 # http/https, upper case, ?trk, trailing slash all match
    text = v.report(None, r, rt)
    assert "numeric LinkedIn id" in text and "no Apollo data yet" in text


def test_verify_listing_lines():
    import verify_data as v
    out = v.listing([{"Company": "A|B", "Company LinkedIn URL": "https://www.linkedin.com/company/ab", "Website": "https://www.ab.io/x", "Status": "Excluded"}, {"Company": "N"}])
    assert out == ["ab|A B|ab.io|Excluded", "-|N|-|-"]


# ---- ICP with the unknown-funding toggle ------------------------------------------------------------------
def sc(stage="", emp=None, **kw):
    return {"stage": stage, "employees_n": emp, "revenue_n": None, "funding_n": None, "excluded": False, **kw}


@pytest.mark.parametrize("c,f,ok", [
    (sc("Seed", 12), {}, True), (sc("Series B", 12), {}, False), (sc("Seed", 80), {}, False), (sc("Seed", None), {}, True),
    (sc("", 12), {}, False),                                           # unknown stage excluded by default
    (sc("", 12), {"unknown_ok": True}, True),                          # toggle on: unknown stage + headcount in range
    (sc("", 80), {"unknown_ok": True}, False),                         # ...but headcount must be in range
    (sc("", None), {"unknown_ok": True}, False),                       # ...and must be known
    (sc("Series A", 12), {"unknown_ok": True}, False),                 # toggle only affects companies with NO stage
    (sc("", 12, excluded=True), {"unknown_ok": True}, False),          # hard excludes always lose
    (sc("Series A", 12), {"stages": ["Series A"]}, True),              # custom stage list
    (sc("Seed", 12, revenue_n=9e6), {"rev_max": 5e6}, False), (sc("Seed", 12, revenue_n=None), {"rev_max": 5e6}, True),
    (sc("Seed", 12, funding_n=2e7), {"funding_max": 1e7}, False), (sc("Seed", 12), {"emp_min": 20}, False)])
def test_icp_check(c, f, ok):
    assert co.icp_check(c, f)[0] is ok


def test_preview_honours_unknown_toggle_and_numbers(tmp_path):
    comps = [{"_id": "r1", "Company": "Known", "Company LinkedIn URL": "https://www.linkedin.com/company/known"},
             {"_id": "r2", "Company": "Tiny Unfunded", "Company LinkedIn URL": "https://www.linkedin.com/company/tiny"},
             {"_id": "r3", "Company": "Big Unfunded", "Company LinkedIn URL": "https://www.linkedin.com/company/big"}]
    f = tmp_path / "a.csv"
    f.write_text("Company Name,Company Linkedin Url,Latest Funding,Total Funding,Annual Revenue,# Employees\n"
                 "Known,linkedin.com/company/known,Seed,2000000,,12\nTiny Unfunded,linkedin.com/company/tiny,,,,9\nBig Unfunded,linkedin.com/company/big,,,,900\n")
    assert co.preview_apollo(f, comps)["icp"] == 1
    p = co.preview_apollo(f, comps, {"unknown_ok": True})
    assert p["icp"] == 2 and "no funding data" in next(r["why"] for r in p["rows"] if r["company"] == "Tiny Unfunded")


def test_icp_setting_persists_and_reaches_preview(srv, monkeypatch):
    seen = {}
    monkeypatch.setattr(co, "preview_apollo", lambda path, comps=None, icp_settings=None: seen.update(s=icp_settings) or {"csv_rows": 0, "apollo": 0, "matched": 0, "unmatched": 0,
                        "methods": {}, "icp": 0, "fields_found": {}, "airtable_companies": 0, "rows": [], "unmatched_sample": []})
    call(srv, "/api/icp", {"icp": {"stages": None, "emp_min": 2, "emp_max": 50, "unknown_ok": True}})
    call(srv, "/api/apollo", raw=b"Company Name\nX\n")
    assert seen["s"]["unknown_ok"] is True


# ---- scrape runs in parallel and tolerates one failing keyword ---------------------------------------------------
def test_scrape_runs_keywords_in_parallel_and_survives_a_failure(monkeypatch):
    import pipeline
    monkeypatch.setenv("APIFY_TOKEN", "t")
    monkeypatch.setattr(pipeline, "ROOT", pipeline.ROOT / "nonexistent_dir")          # no apify_input.json override
    posted, lock = [], threading.Lock()

    class R:
        def __init__(self, n): self.n = n
        def json(self): return [{"id": f"j{self.n}-{i}"} for i in range(self.n)]

    def fake(method, url, **kw):
        with lock:
            posted.append(kw["json"])
        if kw["json"]["keywords"] == "bad":
            raise RuntimeError("actor failed")
        time.sleep(0.05)
        return R(2)
    monkeypatch.setattr(pipeline, "request", fake)
    logs = []
    out = pipeline.scrape(["a", "b", "bad", "c"], "India", 300, "pastMonth", log=logs.append)
    assert len(out) == 6 and sorted(p["keywords"] for p in posted) == ["a", "b", "bad", "c"]
    assert all(p["limitPerSource"] == 300 and p["datePosted"] == "pastMonth" and p["location"] == "India" and p["scrapeCompany"] for p in posted)
    assert any("FAILED" in l and "bad" in l for l in logs)
    monkeypatch.setattr(pipeline, "request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(SystemExit):
        pipeline.scrape(["a", "b"], log=lambda *a: None)


# ---- LinkedIn hiring posts (harvestapi/linkedin-post-search) ---------------------------------------------------
def post(i, name="Acme Labs", slug="acme-labs", typ="company", text="We are hiring: Founder's Office Associate\n\nWork with the CEO on GTM.", url=None):
    return {"type": "post", "id": str(i), "linkedinUrl": url or f"https://www.linkedin.com/posts/{slug}_hiring-activity-{i}", "content": text,
            "author": {"type": typ, "name": name, "universalName": slug, "linkedinUrl": f"https://www.linkedin.com/company/{slug}/posts"},
            "postedAt": {"date": "2026-10-17T12:10:38.109Z"}}


def test_posts_to_jobs_keeps_only_company_posts_about_our_roles():
    import pipeline
    items = [post(1),                                                                                    # kept
             post(2, typ="profile", name="Riya Shah", slug="riya"),                                      # a person: no company link
             post(3, name="Dubai jobs, UAE", slug="dubai-jobs", text="#hiring Founder's Office manager"),  # job board
             post(4, name="Beta", slug="beta", text="We are hiring a Graphic Designer"),                 # not our roles
             post(5, name="Gamma", slug="gamma", text="Hiring: Executive Assistant to the CEO, strategy support"),  # assistant role
             post(1),                                                                                    # duplicate id
             post(6, name="Delta", slug="delta", text="Chief of Staff wanted"),                          # kept
             {**post(7, name="Epsilon", slug="eps"), "author": {"type": "company", "name": "Epsilon", "universalName": "eps", "linkedinUrl": "https://www.linkedin.com/showcase/eps/posts"}},
             {"id": "", "content": "x"}]
    jobs, st = pipeline.posts_to_jobs(items)
    assert [j["job_id"] for j in jobs] == ["post-1", "post-6"]
    assert st == {"total": 9, "kept": 2, "person": 1, "job board or recruiter": 1, "not our roles": 1, "hard excluded": 1, "duplicate": 2, "showcase page": 1}
    j = jobs[0]
    assert j["company_url"] == "https://www.linkedin.com/company/acme-labs" and j["company"] == "Acme Labs"
    assert j["title"] == "[Post] We are hiring: Founder's Office Associate" and j["job_url"].startswith("https://www.linkedin.com/posts/acme-labs")
    assert j["posted"] == "2026-10-17T12:10:38.109Z"
    assert pipeline.rules.group_by_company(jobs).keys() == {"https://www.linkedin.com/company/acme-labs", "https://www.linkedin.com/company/delta"}


def test_scrape_posts_sends_one_run_with_all_queries(monkeypatch):
    import pipeline
    monkeypatch.setenv("APIFY_TOKEN", "t")
    sent = {}

    class R:
        def json(self): return [{"id": "1"}, {"id": "2"}]
    monkeypatch.setattr(pipeline, "request", lambda m, url, **kw: sent.update(url=url, body=kw["json"]) or R())
    out = pipeline.scrape_posts(["a", "b"], 25, "week", log=lambda *a: None)
    assert len(out) == 2 and "harvestapi~linkedin-post-search" in sent["url"]
    assert sent["body"] == {"searchQueries": ["a", "b"], "maxPosts": 25, "postedLimit": "week", "sortBy": "date"}


def test_scrape_posts_endpoint_validates_and_records_summary(srv, monkeypatch):
    import pipeline
    monkeypatch.setenv("APIFY_TOKEN", "t")
    seen = {}
    monkeypatch.setattr(pipeline, "scrape_posts", lambda qs, per, posted, sort, log=print: seen.update(qs=qs, per=per, posted=posted) or [{"id": "1"}] * 4)
    monkeypatch.setattr(pipeline, "run_posts", lambda items, log=print: {"companies": 1, "jobs": 1, "new": 1, "posts": {"total": 4, "kept": 1, "person": 3}})
    assert call(srv, "/api/scrape_posts", {"queries": ""})["_code"] == 400
    assert call(srv, "/api/scrape_posts", {"queries": "hiring founder's office\nhiring chief of staff", "max_posts": 9999, "posted_limit": "bogus"})["started"] is True
    wait_job()
    assert seen == {"qs": ["hiring founder's office", "hiring chief of staff"], "per": 500, "posted": "month"}   # cap on cost, bad value ignored
    st = call(srv, "/api/state")
    assert st["last_posts"]["posts"] == 4 and st["last_posts"]["dropped"]["person"] == 3 and st["post_queries"].startswith("hiring founder's office")
