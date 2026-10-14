#!/usr/bin/env python3
"""Stages 1-3: scrape LinkedIn jobs (Apify), dedupe by company, classify + score (Gemini), upsert to Airtable.

    python pipeline.py                      # full weekly run
    python pipeline.py --dry-run            # no Airtable writes
    python pipeline.py --dump data/raw.json # save raw Apify items (inspect field names on first run)
    python pipeline.py --from-file data/raw.json   # skip the scrape, reuse a dump
"""
import argparse
import json
from datetime import date

from fo import airtable as at
from fo import llm, rules
from fo.config import ROOT, env
from fo.http import request

# Edit apify_input.json to change keywords/location. Input names depend on the actor's schema:
# check https://apify.com/meka.im/linkedin-jobs-scraper/input-schema on the first run.
DEFAULT_INPUT = {
    "queries": ["Founder's Office", "Founders Office", "Chief of Staff to CEO", "Founder Office Associate"],
    "location": "India",
    "maxItems": 300,
    "datePosted": "week",
}


def scrape() -> list[dict]:
    inp_file = ROOT / "apify_input.json"
    inp = json.loads(inp_file.read_text()) if inp_file.exists() else DEFAULT_INPUT
    actor = env("APIFY_ACTOR", "meka.im~linkedin-jobs-scraper", required=False)
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
    return request("POST", url, params={"token": env("APIFY_TOKEN")}, json=inp, timeout=900).json()


def pick(item: dict, *keys, default=None):
    """First non-empty value among candidate field names (actor output names vary)."""
    for k in keys:
        v = item.get(k)
        if v not in (None, "", []):
            return v
    return default


def normalise(item: dict) -> dict:
    return {
        "job_id": str(pick(item, "id", "jobId", "job_id", "trackingId", default=pick(item, "link", "jobUrl", "url", default=""))),
        "title": pick(item, "title", "jobTitle", default=""),
        "job_url": pick(item, "link", "jobUrl", "url", default=""),
        "posted": pick(item, "postedAt", "postedDate", "publishedAt", "listedAt"),
        "location": pick(item, "location", "jobLocation", default=""),
        "poster": pick(item, "jobPosterName", "posterName"),
        "poster_url": pick(item, "jobPosterProfileUrl", "posterProfileUrl"),
        "company": pick(item, "companyName", "company", default=""),
        "company_url": pick(item, "companyLinkedinUrl", "companyUrl", "companyLink", "company_url"),
        "website": pick(item, "companyWebsite", "website"),
        "headcount": pick(item, "companyEmployeesCount", "employeeCount", "companySize", "companyStaffCount"),
        "industry": pick(item, "industries", "industry", "companyIndustry", default=""),
        "description": pick(item, "descriptionText", "description", "jobDescription", default=""),
    }


def classify_new(companies: dict[str, list[dict]]) -> dict[str, dict]:
    """Return {company_url: fields} for companies needing a decision. Hard excludes skip Gemini."""
    result, to_score = {}, []
    for url, jobs in companies.items():
        j = jobs[0]
        reason = rules.hard_exclude(j["company"], " / ".join(x["title"] for x in jobs), str(j["industry"]))
        if reason:
            result[url] = {"ICP score": 0, "ICP reason": f"Hard exclude: {reason}", "Status": "Excluded"}
        else:
            to_score.append(url)
    for i in range(0, len(to_score), 20):  # batches of 20
        chunk = to_score[i : i + 20]
        batch = [
            {
                "id": u, "name": companies[u][0]["company"], "website": companies[u][0]["website"],
                "headcount": companies[u][0]["headcount"], "industry": companies[u][0]["industry"],
                "description": (companies[u][0]["description"] or "")[:700],
                "titles": [x["title"] for x in companies[u]], "locations": [x["location"] for x in companies[u]][:3],
            }
            for u in chunk
        ]
        scored = llm.classify_companies(batch)
        for u in chunk:
            c = scored.get(u)
            if not c:
                result[u] = {"Status": "Needs review", "ICP reason": "Gemini returned no result"}
                continue
            score = 0 if c.get("excluded") else rules.icp_total(c.get("scores", {}))
            result[u] = {
                "Startup type": c.get("startup_type"), "B2B": bool(c.get("b2b")),
                "Sales motion": c.get("sales_motion"), "Stage (guess)": c.get("stage_guess"),
                "ICP score": score, "ICP reason": c.get("reason", ""),
                "Status": "Excluded" if c.get("excluded") else rules.status_for_score(score),
            }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dump", help="write raw scraper items to this file")
    ap.add_argument("--from-file", help="read raw items from file instead of scraping")
    args = ap.parse_args()
    at.DRY_RUN = args.dry_run

    raw = json.loads(open(args.from_file).read()) if args.from_file else scrape()
    if args.dump:
        ROOT.joinpath(args.dump).parent.mkdir(parents=True, exist_ok=True)
        ROOT.joinpath(args.dump).write_text(json.dumps(raw, indent=1))
    print(f"scraped {len(raw)} jobs")

    jobs = [normalise(r) for r in raw]
    companies = rules.group_by_company(jobs)
    print(f"{len(companies)} unique companies ({len(jobs) - sum(map(len, companies.values()))} jobs dropped: no company URL / duplicate)")

    existing = {c.get("Company LinkedIn URL"): c for c in at.Companies.all()}
    new_urls = {u: j for u, j in companies.items() if existing.get(u, {}).get("ICP score") is None}
    decisions = classify_new(new_urls)

    today = date.today().isoformat()
    rows = []
    for url, js in companies.items():
        j = js[0]
        row = {"Company LinkedIn URL": url, "Company": j["company"], "Website": j["website"], "Last seen": today}
        if j["headcount"]:
            row["Headcount"] = str(j["headcount"])
        if url in decisions:
            row.update(decisions[url])
            row["About"] = (j["description"] or "")[:1000]
        # Never regress a company that has moved past classification.
        row = {k: v for k, v in row.items() if v not in (None, "")}
        if existing.get(url, {}).get("Status") in rules.ADVANCED:
            row.pop("Status", None)
        rows.append(row)
    saved = at.Companies.upsert(rows, ["Company LinkedIn URL"])
    id_of = {r["Company LinkedIn URL"]: r["_id"] for r in saved}

    job_rows = []
    for url, js in companies.items():
        for j in js:
            row = {
                "Job ID": j["job_id"], "Title": j["title"], "Job URL": j["job_url"], "Posted": j["posted"],
                "Location": j["location"], "Poster": j["poster"], "Poster LinkedIn URL": j["poster_url"],
                "Company LinkedIn URL": [id_of[url]],
            }
            job_rows.append({k: v for k, v in row.items() if v not in (None, "")})
    at.Jobs.upsert(job_rows, ["Job ID"])

    counts = {}
    for d in decisions.values():
        counts[d["Status"]] = counts.get(d["Status"], 0) + 1
    print(f"classified {len(decisions)} new companies: {counts}; upserted {len(rows)} companies, {len(job_rows)} jobs")


if __name__ == "__main__":
    main()
