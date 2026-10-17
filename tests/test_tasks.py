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


def test_seed_picks_named_post_companies_once():
    comps = Fake([{"_id": "c1", "Company": "NextDimension AI", "Company LinkedIn URL": "u1"},
                  {"_id": "c2", "Company": "SabiHR", "Company LinkedIn URL": "u2"},
                  {"_id": "c3", "Company": "Old Co", "Company LinkedIn URL": "u3"}])
    jobs = Fake([{"_id": "j1", "Job ID": "post-1", "Title": "[Post] hiring FO", "Job URL": "p1", "Company LinkedIn URL": ["c1"]},
                 {"_id": "j2", "Job ID": "post-2", "Title": "[Post] hiring BD", "Job URL": "p2", "Company LinkedIn URL": ["c2"]},
                 {"_id": "j3", "Job ID": "999", "Title": "Real job", "Job URL": "p3", "Company LinkedIn URL": ["c3"]}])
    t = Fake()
    assert sorted(tasks.seed_classify_post(["nextdimension", "sabihr", "old co"], tasks=t, companies=comps, jobs=jobs)) == ["NextDimension AI", "SabiHR"]
    assert tasks.seed_classify_post(["nextdimension"], tasks=t, companies=comps, jobs=jobs) == []
    assert json.loads(t.rows[0]["Input"])["post"] in ("hiring FO", "hiring BD")


def test_services_firm_and_reason():
    _, task, comp, _ = run(done("classify_post", {"kind": "services_firm", "reason": "IT consultancy hiring for itself"}))
    assert comp["Status"] == "Excluded" and "IT consultancy" in task["Note"]
    out, task, *_ = run(done("classify_post", {"kind": "maybe"}))
    assert out["Failed"] == 1


def test_reopen_clears_old_answer():
    t = Fake([{"_id": "t1", "Company": "Aelum Consulting", "Status": "Done", "Result": "{}", "Confidence": "High", "Started at": "x"},
              {"_id": "t2", "Company": "SabiHR", "Status": "Done", "Result": "{}"}])
    assert tasks.reopen(["aelum"], t) == 1
    assert t.rows[0]["Status"] == "Open" and t.rows[0]["Result"] == "" and t.rows[1]["Status"] == "Done"


def test_review_flags_real_problems():
    good = {"Type": "classify_post", "Confidence": "High", "Started at": "2020-01-01T00:00:00.000Z",
            "Result": json.dumps({"kind": "startup", "reason": "10 person AI product company hiring its own FO"})}
    assert tasks.review(good)[0] == []
    future = dict(good, **{"Started at": tasks.stamp(tasks.now() + tasks.timedelta(hours=5))})
    assert any("future" in p for p in tasks.review(future)[0])
    lazy = dict(good, Result=json.dumps({"kind": "startup"}))
    assert any("reason" in p for p in tasks.review(lazy)[0])
    assert tasks.review(dict(good, Result="oops"))[0] == ["Result is not valid JSON"]
    v = {"Type": "verify_company", "Confidence": "High", "Started at": "2020-01-01T00:00:00.000Z",
         "Result": json.dumps({"same_company": True, "reason": "website and LinkedIn match the name"})}
    assert any("website" in p for p in tasks.review(v)[0])


class NoStageColumn(Fake):
    def update(self, ups):
        if any("Funding stage" in f for _, f in ups):
            raise RuntimeError('PATCH x -> 422: {"error":{"type":"UNKNOWN_FIELD_NAME","message":"Unknown field name: \\"Funding stage\\""}}')
        super().update(ups)


def test_missing_stage_column_still_saves_website_and_one_failure_does_not_stop_others():
    t, comps = Fake([done("verify_company", {"same_company": True, "website": "https://a.io", "stage": "Seed", "reason": "x" * 20}),
                     dict(done("classify_post", {"kind": "recruiter", "reason": "x" * 20}), _id="t2", **{"Company record": "c2"})]), NoStageColumn([{"_id": "c1"}, {"_id": "c2"}])
    out = tasks.apply_done(t, {"Companies": comps, "Contacts": Fake()})
    assert out["Applied"] == 2
    assert comps.rows[0]["Website"] == "https://a.io" and "not saved" in t.rows[0]["Note"]
    assert comps.rows[1]["Status"] == "Excluded"
