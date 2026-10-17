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
