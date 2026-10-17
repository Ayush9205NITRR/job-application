"""Clicks through the real page in headless Chromium: ICP filter, overlap, contacts, approve. Skipped if Playwright or Chromium is missing."""
import json
import os
import threading
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")
CHROMIUM = "/opt/pw-browsers/chromium"

import companies as co
import outbound as ob
import overlap as ov
import ui


def comp(i, name, stage, emp, site):
    return {"_id": f"rec{i}", "Company": name, "Company LinkedIn URL": f"https://www.linkedin.com/company/{name.lower().replace(' ', '-')}",
            "Apollo match": "linkedin", "Funding stage": stage, "Total funding": "$2M", "Annual revenue (Apollo)": "$1M",
            "Employees (Apollo)": emp, "Status": "Needs review", "Website": site,
            "jobs": [{"Title": "Founder's Office Associate", "Job URL": f"https://example.com/j{i}"}]}


@pytest.fixture
def page_url(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "QUEUE", tmp_path / "queue.csv")
    for k, v in dict(AIRTABLE_TOKEN="t", AIRTABLE_BASE_ID="appXXXXXXXXXXXXXX", GMAIL_USER="me@gmail.com", GMAIL_APP_PASSWORD="x").items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(co, "_cache", {"rows": [comp(1, "Acme Logistics", "Seed", "12", "https://acme.io"), comp(2, "Beta Labs", "Series B", "300", "https://beta.io"),
                                                comp(3, "Gamma AI", "Pre Seed", "6", "https://gamma.io")]})
    monkeypatch.setattr(co, "save_shortlist", lambda sel, shown: len(sel))
    pages = {"acme.io": "Our customers: MongoDB and Sprinto", "gamma.io": "nothing relevant"}
    monkeypatch.setattr(ov, "default_fetch", lambda url: (404, "") if url.endswith("robots.txt") else
                        (200, "<body>" + next((v for k, v in pages.items() if k in url), "x") + "</body>"))
    monkeypatch.setattr(ui, "cache_dir", lambda: tmp_path / "cache")
    (tmp_path / "contacts.csv").write_text("Company Linkedin,POC First Name,POC Email,POC Contact Detail\n"
                                           "https://www.linkedin.com/company/acme-logistics,Riya,riya@acme.io,+91 98765 43210\n"
                                           "https://www.linkedin.com/company/gamma-ai,Sam,sam@gamma.io,\n"
                                           "https://www.linkedin.com/company/beta-labs,Tom,tom@beta.io,\n")
    s = ui.make_server(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}", tmp_path
    s.shutdown()


@pytest.mark.skipif(not os.path.exists(CHROMIUM), reason="no Chromium")
def test_full_flow_in_browser(page_url):
    url, tmp = page_url
    errors = []
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM)
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url)
        pg.click(".step >> text=ICP and overlap")
        pg.wait_for_selector("text=2 of 3 match")                                   # Seed + Pre Seed, not Series B
        pg.fill("[data-icp=emp_max]", "10"); pg.dispatch_event("[data-icp=emp_max]", "change")
        pg.wait_for_selector("text=1 of 3 match")                                   # live re-filter, no reload
        pg.fill("[data-icp=emp_max]", "50"); pg.dispatch_event("[data-icp=emp_max]", "change")
        pg.wait_for_selector("text=2 of 3 match")
        pg.click("text=Check overlap with my clients")
        pg.wait_for_selector(".chip.tog[data-id]", timeout=10000)
        assert pg.inner_text("tr.hi >> nth=0").count("Mongo DB") == 1
        pg.click(".step >> text=Contacts")
        pg.click("[data-act=ver][data-v=yes]")
        pg.set_input_files("#file4", str(tmp / "contacts.csv"))
        pg.wait_for_selector("text=Added")
        assert "2</b> drafts: 1 pitch, 1 generic" in pg.inner_html(".note.ok").replace("\n", "")      # Beta not selected
        pg.click("text=Review the drafts"); pg.wait_for_selector(".q")
        pg.click("text=Approve all pitch")
        pg.wait_for_selector("[data-act=qf][data-t=approved]:has-text('approved 1')")
        pg.keyboard.press("a")                                                       # keyboard: approve focused generic draft
        pg.wait_for_selector("[data-act=qf][data-t=approved]:has-text('approved 2')")
        b.close()
    rows = ob.load_queue()
    assert [(r["first"], r["approved"], r["pitch"]) for r in rows] == [("Riya", "y", "Mongo DB; Sprinto"), ("Sam", "y", "")]
    assert "Mongo DB and Sprinto" in rows[0]["line"] and rows[1]["line"] == ob.DEFAULT_LINE.format(company="Gamma AI")
    assert errors == []
