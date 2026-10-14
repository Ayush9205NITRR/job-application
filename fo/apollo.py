"""Apollo REST client (personal free-plan key, not Enout's)."""
from .config import FOUNDER_TITLES, env
from .http import request

_API = "https://api.apollo.io/api/v1"


def _h():
    return {"x-api-key": env("APOLLO_API_KEY"), "Content-Type": "application/json", "Cache-Control": "no-cache"}


def enrich_org(domain: str) -> dict:
    r = request("GET", f"{_API}/organizations/enrich", headers=_h(), params={"domain": domain}).json()
    return r.get("organization") or {}


def search_people(domain: str, limit: int = 5) -> list[dict]:
    body = {"q_organization_domains_list": [domain], "person_titles": FOUNDER_TITLES, "per_page": limit, "page": 1}
    r = request("POST", f"{_API}/mixed_people/api_search", headers=_h(), json=body).json()
    return r.get("people") or []


def match_person(*, person_id: str | None = None, linkedin_url: str | None = None, domain: str | None = None) -> dict | None:
    """Reveals the email (spends an Apollo credit). Caller filters on email_status == 'verified'."""
    body = {"reveal_personal_emails": False}
    if person_id:
        body["id"] = person_id
    if linkedin_url:
        body["linkedin_url"] = linkedin_url
    if domain:
        body["domain"] = domain
    r = request("POST", f"{_API}/people/match", headers=_h(), json=body).json()
    return r.get("person")
