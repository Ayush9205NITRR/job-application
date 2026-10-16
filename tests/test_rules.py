from datetime import date, datetime, timedelta, timezone

import pytest

from fo import rules
from fo import templates as T


def test_company_url_normalised_and_deduped():
    jobs = [
        {"job_id": "1", "company_url": "https://in.linkedin.com/company/Acme/?trk=x"},
        {"job_id": "2", "company_url": "https://www.linkedin.com/company/acme"},
        {"job_id": "2", "company_url": "https://www.linkedin.com/company/acme"},
        {"job_id": "3", "company_url": None},
    ]
    g = rules.group_by_company(jobs)
    assert list(g) == ["https://www.linkedin.com/company/acme"]
    assert len(g["https://www.linkedin.com/company/acme"]) == 2


@pytest.mark.parametrize("name,title", [
    ("Dream MICE Solutions", "Founder's Office"),
    ("TalentBridge Recruitment", "Founder's Office"),
    ("Acme", "Executive Assistant to CEO"),
    ("Manpower Group", "Associate"),
])
def test_hard_excludes(name, title):
    assert rules.hard_exclude(name, title)


def test_clean_company_not_excluded():
    assert rules.hard_exclude("Acme SaaS", "Founder's Office Associate", "Software") is None


def test_icp_caps_and_threshold():
    assert rules.icp_total({"b2b": 99, "sales_motion": 99, "role": 99, "stage_size": 99, "location": 99}) == 100
    assert rules.status_for_score(60) == "Shortlisted"
    assert rules.status_for_score(59) == "Needs review"
    assert rules.status_for_score(10) == "Excluded"


def test_daily_cap_ramp():
    s = date(2026, 10, 1)
    assert rules.daily_cap(s, date(2026, 10, 3)) == 5
    assert rules.daily_cap(s, date(2026, 10, 9)) == 10
    assert rules.daily_cap(s, date(2026, 10, 16)) == 15
    assert rules.daily_cap(s, date(2026, 11, 1)) == 20
    assert rules.daily_cap(None, date(2026, 11, 1)) == 5


def test_bounce_halt():
    assert not rules.bounce_halt(5, 0)
    assert rules.bounce_halt(10, 1)
    assert not rules.bounce_halt(40, 1)
    assert rules.bounce_halt(100, 4)
    assert rules.bounce_halt(6, 2)


def test_followup_schedule():
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    d = lambda n: t0 + timedelta(days=n)
    assert rules.followup_step_due(t0, d(4), {1}) is None
    assert rules.followup_step_due(t0, d(5), {1}) == 2
    assert rules.followup_step_due(t0, d(12), {1}) == 2  # never skip step 2
    assert rules.followup_step_due(t0, d(11), {1, 2}) is None
    assert rules.followup_step_due(t0, d(12), {1, 2}) == 3
    assert rules.followup_step_due(t0, d(30), {1, 2, 3}) is None


def test_first_line_rules():
    assert rules.check_first_line("Acme builds invoicing software for Indian logistics firms.") is None
    assert rules.check_first_line("Acme builds software — for logistics.")
    assert rules.check_first_line("I love what you are building at Acme.")
    assert rules.check_first_line(" ".join(["word"] * 20))
    assert rules.check_first_line("")


def test_health_lint():
    assert rules.has_health_terms("Open to remote because of a medical condition") == ["medical"]
    assert rules.has_health_terms("B2B sales and BD") == []


def test_render_and_nested_proof():
    v = {"firstName": "Riya", "companyName": "Acme", "roleTitle": "Founder's Office", "jobUrl": "u",
         "firstLine": "Acme sells X.", "proofLine": T.PROOFS[1]}
    out = T.render(T.BODIES[("A", 1)], v)
    assert "{{" not in out and "for Acme." in out and "—" not in out


def test_render_missing_var_raises():
    with pytest.raises(KeyError):
        T.render(T.BODIES[("A", 3)], {"firstName": "Riya", "resumeUrl": ""})


def test_proof_defaults_and_second_proof():
    assert T.pick_proof("Founder") == 1 and T.pick_proof("Job poster") == 3
    assert T.second_proof_id(3) != 3 and T.second_proof_id(1) != 1


def test_tag_roundtrip_and_subjects():
    assert T.parse_tag(T.tag("A", 3, 1)) == ("A", 3, 1)
    assert T.subject_id("C", "x@y.com") == 2
    assert T.subject_id("A", "x@y.com") in (1, 3)
    assert T.subject_id("A", "x@y.com") == T.subject_id("A", "X@y.com")


def test_all_copy_free_of_dashes_and_health():
    texts = list(T.PROOFS.values()) + list(T.SUBJECTS.values()) + list(T.BODIES.values())
    for t in texts:
        assert "—" not in t and not rules.has_health_terms(t)


def test_linkedin_invites_fit_300_chars_for_all_proofs():
    for pid, proof in T.PROOFS.items():
        v = {"firstName": "Priyanka", "companyName": "Acme Logistics Technologies", "roleTitle": "Founder's Office Associate", "proofLine": proof}
        assert len(T.render(T.LINKEDIN_INVITE, v)) <= 300, pid


def test_translate_jobs_rewrites_company_links():
    import copy_to_new_base as c
    old_c = [{"_id": "recA", "Company LinkedIn URL": "https://www.linkedin.com/company/a"}]
    old_j = [{"_id": "j1", "Job ID": "1", "Title": "FO", "Company LinkedIn URL": ["recA"], "Applied": False},
             {"_id": "j2", "Job ID": "2", "Title": "X", "Company LinkedIn URL": ["recGone"]}]
    rows, orphans = c.translate_jobs(old_j, old_c, {"https://www.linkedin.com/company/a": "recNEW"})
    assert rows[0]["Company LinkedIn URL"] == ["recNEW"] and rows[0]["Job ID"] == "1" and "_id" not in rows[0]
    assert "Company LinkedIn URL" not in rows[1] and orphans == 1
