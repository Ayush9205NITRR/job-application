import json

import taskplan
from test_tasks import Fake


def comp(i, name, status="Needs review", jobs=None, **kw):
    return {"_id": i, "Company": name, "Company LinkedIn URL": f"https://www.linkedin.com/company/{name.lower()}", "Status": status,
            "jobs": jobs if jobs is not None else [{"Job ID": "77", "Title": "BD", "Job URL": "u"}], **kw}


POST = [{"Job ID": "post-1", "Title": "[Post] hiring FO", "Job URL": "p"}]


def test_select_gates_work_in_the_right_order():
    comps = [
        comp("a", "PostCo", jobs=POST),                                                      # unclassified post company
        comp("b", "UnmatchedJobCo"),                                                          # job company, not in Apollo yet
        comp("c", "IcpCo", **{"Apollo match": True, "Funding stage": "Seed", "Employees (Apollo)": "12"}),
        comp("d", "BigCo", **{"Apollo match": True, "Funding stage": "Series C", "Employees (Apollo)": "900"}),
        comp("e", "Gone", status="Excluded"),
    ]
    out = taskplan.select(comps, {})
    assert [x[1]["name"] for x in out["classify_post"]] == ["PostCo"]
    assert [x[1]["name"] for x in out["verify_company"]] == ["UnmatchedJobCo"]
    assert [x[1]["name"] for x in out["pick_decision_makers"]] == ["IcpCo"]


def test_post_company_waits_for_startup_verdict():
    c = comp("a", "PostCo", jobs=POST)
    waiting = {"classify_post:a": {"Status": "Done"}}
    assert taskplan.select([c], waiting)["verify_company"] == []
    startup = {"classify_post:a": {"Status": "Applied", "Result": json.dumps({"kind": "startup"})}}
    assert [x[1]["name"] for x in taskplan.select([c], startup)["verify_company"]] == ["PostCo"]
    recruiter = {"classify_post:a": {"Status": "Applied", "Result": json.dumps({"kind": "recruiter"})}}
    assert taskplan.select([c], recruiter)["verify_company"] == []


def test_plan_caps_lanes_and_is_idempotent(monkeypatch):
    monkeypatch.setenv("TASK_LANES", "2")
    comps = [comp(str(i), f"Co{i}") for i in range(5)]
    t = Fake()
    assert taskplan.plan(max_per_type=3, cap=100, tasks=t, comps=comps) == {"verify_company": 3}
    assert sorted(r["Lane"] for r in t.rows) == [1, 1, 2]
    assert taskplan.plan(max_per_type=10, cap=100, tasks=t, comps=comps) == {"verify_company": 2}
    assert len(t.rows) == 5
    assert taskplan.plan(max_per_type=10, cap=100, tasks=t, comps=comps) == {}
    t2 = Fake()
    out = taskplan.plan(max_per_type=10, cap=2, tasks=t2, comps=comps)
    assert out["verify_company"] == 2 and out["capped"] and len(t2.rows) == 2


def test_dry_run_writes_nothing():
    t = Fake()
    assert taskplan.plan(tasks=t, comps=[comp("a", "X")], dry=True) == {"verify_company": 1} and t.rows == []


def test_stats_accuracy():
    rows = [{"Type": "classify_post", "Status": "Applied", "Confidence": "High", "Audit": "OK"},
            {"Type": "classify_post", "Status": "Applied", "Confidence": "High", "Audit": "Wrong"},
            {"Type": "classify_post", "Status": "Needs human", "Confidence": "Low"}]
    s = taskplan.stats(Fake(rows))
    assert s["audit_accuracy_pct"] == 50 and s["low_confidence_pct"] == 33 and s["needs_human"] == 1
