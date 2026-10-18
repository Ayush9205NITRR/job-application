import emails
from test_tasks import Fake

CO = {"_id": "c1", "Company": "Acme AI", "Status": "Needs review", "Shortlist": True, "Funding stage": "Seed", "Website": "https://acme.ai"}
J = [{"Job URL": "https://www.linkedin.com/posts/acme", "Company LinkedIn URL": ["c1"]}]


def person(i, email="a@acme.ai", status="verified", stage="New", first="Asha Rao"):
    return {"_id": i, "Email": email, "Email status": status, "Stage": stage, "First name": first, "Company": ["c1"]}


def run(contacts, companies=None, existing=None):
    e, upd = Fake(existing or []), []
    n = emails.build(contacts, companies or [CO], J, e, lambda u: upd.extend(u))
    return n, e.rows, upd


def test_only_verified_contacts_of_shortlisted_companies():
    n, rows, upd = run([person("p1"), person("p2", "b@acme.ai", status="unknown"), person("p3", "c@acme.ai", stage="Sent")])
    assert n == 1 and rows[0]["To"] == "a@acme.ai" and rows[0]["First name"] == "Asha" and rows[0]["Status"] == "Ready for Gmail draft"
    assert "Hi Asha" in rows[0]["Body"] and "Acme AI" in rows[0]["Subject"] and "Seed" in rows[0]["Context"] and "—" not in rows[0]["Body"]
    assert upd == [("p1", {"Stage": "Drafted"})]


def test_no_shortlist_no_email_and_never_twice():
    assert run([person("p1")], [dict(CO, Shortlist=False)])[0] == 0
    assert run([person("p1")], [dict(CO, Status="Excluded")])[0] == 0
    assert run([person("p1")], [dict(CO, Company="Kwan Executive Search")])[0] == 0
    assert run([person("p1")], existing=[{"_id": "e1", "Contact record": "p1", "To": "a@acme.ai"}])[0] == 0
    n, rows, _ = run([person("p1"), person("p9", "A@acme.ai")])  # same address written twice
    assert n == 1


def test_auto_shortlist_ticks_only_early_stage_verified_companies():
    ok = {"_id": "a", "Company": "A", "Status": "Needs review", "Apollo match": "agent", "Funding stage": "Pre-Series A", "Employees (Apollo)": "11-50"}
    big = dict(ok, _id="b", **{"Funding stage": "Series C", "Employees (Apollo)": "900"})
    unverified = {"_id": "c", "Company": "C", "Status": "Needs review", "Funding stage": "Seed", "Employees (Apollo)": "5"}
    excluded = dict(ok, _id="d", Status="Excluded")
    upd = []
    assert emails.auto_shortlist([ok, big, unverified, excluded], upd.extend) == 1 and upd == [("a", {"Shortlist": True})]
    n, rows, _ = run([person("p1")], [dict(CO, Shortlist=False, **{"Apollo match": "agent", "Funding stage": "Seed", "Employees (Apollo)": "10"})])
    assert n == 0  # build() only shortlists when asked to


def test_taskplan_round_calls_build_with_a_real_argument_name():
    import inspect
    import re
    src = open("taskplan.py").read()
    kw = re.search(r"emails\.build\((\w+)=", src).group(1)
    assert kw in inspect.signature(emails.build).parameters


# ---- follow-ups ------------------------------------------------------------------------------
def test_followups_wait_for_days_and_stop_on_reply():
    from datetime import datetime, timedelta, timezone
    import followups
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    sent = lambda d: (now - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    base = {"To": "a@x.io", "First name": "A", "Company": "X", "Subject": "Hi", "Contact record": "c1"}
    one = lambda d, **k: [dict(base, Step="Email 1", Status="Sent", **{"Sent at": sent(d)}, **k)]
    assert followups.due(one(3), now) == []
    assert [s for _, s in followups.due(one(5), now)] == ["Follow-up 1"]
    assert followups.due(one(6, Replied=True), now) == []
    both = one(13) + [dict(base, Step="Follow-up 1", Status="Sent")]
    assert [s for _, s in followups.due(both, now)] == ["Follow-up 2"]
    assert followups.due(one(13) + [dict(base, Step="Follow-up 1", Status="In Gmail drafts")], now) == []


def test_preflight_names_each_known_mistake():
    import preflight
    ok = {t: list(c) for t, c in preflight.NEED_COLUMNS.items()}
    good = {"AIRTABLE_TOKEN": "x", "AIRTABLE_BASE_ID": "appms9ERcB5Ws3r0w"}
    assert preflight.problems(good, ok) == []
    assert [k for k, _ in preflight.problems(dict(good, AIRTABLE_BASE_ID="app40Dtij4xm0M2aW"), ok)] == ["wrong_base"]
    assert [k for k, _ in preflight.problems({}, None)] == ["missing_secret", "missing_secret"]
    short = dict(ok, Companies=["Status"])
    assert {k for k, _ in preflight.problems(good, short)} == {"missing_column"}
    assert [k for k, _ in preflight.problems(good, {"Tasks": ok["Tasks"]})].count("missing_table") == 5


def test_scorecard_flags_only_big_weak_sources():
    import scorecard
    rows = [{"Source": "a", "Status": "Excluded"}] * 25 + [{"Source": "a", "Status": "Needs review"}] * 2 + [{"Source": "b", "Status": "Excluded"}] * 5
    card = scorecard.score(rows)
    assert scorecard.weak(card) == ["a"] and card["b"]["rate"] == 0


def test_dashboard_marks_stuck_stage():
    import dashboard, tasks as T
    from datetime import timedelta
    old = T.stamp(T.now() - timedelta(hours=30))
    out = dashboard.build([{"_id": "c1", "Company": "Acme", "Shortlist": True}], [],
                          [{"_id": "p", "LinkedIn URL": "https://linkedin.com/in/a", "Company": ["c1"]}],
                          [{"Type": "classify_post", "Status": "Open", "Created at": old}], [], [], [{"Run": "r1", "Keyword": "a", "Raw": 9, "New": 2}])
    st = {f["step"]: f for f in out["funnel"]}
    assert st[3]["state"] == "stuck" and st[1]["done"] == 9 and st[2]["done"] == 2 and st[8]["waiting"] == 1
    assert out["accounts"][0]["next"] == "add emails"
