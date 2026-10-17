import json

import tasks


class Fake:
    """Minimal in-memory stand-in for fo.airtable.Table."""
    def __init__(self, rows=None):
        self.rows = rows or []

    def all(self, formula=None):
        if formula and "Status" in formula:
            want = formula.split('"')[1]
            return [r for r in self.rows if r.get("Status") == want]
        return list(self.rows)

    def create(self, rows):
        out = [{"_id": f"rec{len(self.rows) + i}", **r} for i, r in enumerate(rows)]
        self.rows += out
        return out

    def update(self, ups):
        for i, f in ups:
            next(r for r in self.rows if r["_id"] == i).update(f)


def done(kind, result, conf="High", rid="c1"):
    return {"_id": "t1", "Type": kind, "Status": "Done", "Company record": rid, "Confidence": conf,
            "Result": result if isinstance(result, str) else json.dumps(result)}


def run(task):
    t, comps, cons = Fake([task]), Fake([{"_id": "c1"}]), Fake()
    out = tasks.apply_done(t, {"Companies": comps, "Contacts": cons})
    return out, t.rows[0], comps.rows[0], cons.rows


def test_create_is_idempotent():
    t = Fake()
    assert tasks.create_task("verify_company", "Acme", "c1", {"x": 1}, t)
    assert tasks.create_task("verify_company", "Acme", "c1", {"x": 1}, t) is None
    assert len(t.rows) == 1 and t.rows[0]["Status"] == "Open"


def test_verify_applies_only_when_high_and_valid():
    out, task, comp, _ = run(done("verify_company", {"same_company": True, "website": "https://acme.io", "stage": "Seed"}))
    assert out["Applied"] == 1 and comp["Website"] == "https://acme.io" and comp["Funding stage"] == "Seed"
    out, task, comp, _ = run(done("verify_company", {"same_company": True}, conf="Low"))
    assert out["Needs human"] == 1 and "Website" not in comp
    out, task, *_ = run(done("verify_company", {"same_company": "yes"}))
    assert out["Failed"] == 1 and "true or false" in task["Note"]
    out, task, *_ = run(done("verify_company", "not json"))
    assert out["Failed"] == 1 and "unreadable" in task["Note"]


def test_decision_makers_create_contacts_and_reject_bad_roles():
    good = {"people": [{"name": "Asha Rao", "title": "Co-founder", "role": "Founder", "linkedin": "https://www.linkedin.com/in/asha"}]}
    out, _, _, cons = run(done("pick_decision_makers", good))
    assert out["Applied"] == 1 and cons[0]["First name"] == "Asha" and cons[0]["Last name"] == "Rao" and cons[0]["Company"] == ["c1"]
    out, task, _, cons = run(done("pick_decision_makers", {"people": [{"name": "X", "role": "CEO"}]}))
    assert out["Failed"] == 1 and not cons


def test_classify_sets_status():
    _, _, comp, _ = run(done("classify_post", {"kind": "recruiter"}))
    assert comp["Status"] == "Excluded"
    _, _, comp, _ = run(done("classify_post", {"kind": "startup"}))
    assert comp["Status"] == "Needs review"


def test_requeue_stale_then_fail():
    old = tasks.stamp(tasks.now() - tasks.STALE * 2)
    t = Fake([{"_id": "t1", "Status": "Working", "Started at": old, "Tries": 0},
              {"_id": "t2", "Status": "Working", "Started at": tasks.stamp(), "Tries": 0},
              {"_id": "t3", "Status": "Working", "Started at": old, "Tries": 2}])
    assert tasks.requeue_stale(t) == 2
    by = {r["_id"]: r for r in t.rows}
    assert by["t1"]["Status"] == "Open" and by["t2"]["Status"] == "Working" and by["t3"]["Status"] == "Failed"
