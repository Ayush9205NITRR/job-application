#!/usr/bin/env python3
"""Stages 1-3: scrape LinkedIn jobs (Apify), dedupe by company, classify + score (Gemini), upsert to Airtable.

    python pipeline.py                      # full weekly run
    python pipeline.py --dry-run            # no Airtable writes
    python pipeline.py --dump data/raw.json # save raw Apify items (inspect field names on first run)
    python pipeline.py --from-file data/raw.json   # skip the scrape, reuse a dump
"""
import argparse
import json
import re
from datetime import date

from fo import airtable as at
from fo import llm, rules
from fo.config import ROOT, env
from fo.http import request

# Input names follow the actor's published schema (keywords, location, datePosted, limitPerSource,
# scrapeCompany). The actor takes ONE keyword string per run, so "keywords" here may be a list and we run each.
# Override any of this with apify_input.json in the repo root.
DEFAULT_INPUT = {
    "keywords": ["Founder's Office", "Founders Office", "Chief of Staff"],
    "location": "India",
    "datePosted": "pastWeek",
    "limitPerSource": 100,
    "scrapeCompany": True,
}


def scrape(keywords=None, location=None, limit=None, date_posted=None, log=print, workers=3) -> list[dict]:
    inp_file = ROOT / "apify_input.json"
    inp = dict(json.loads(inp_file.read_text()) if inp_file.exists() else DEFAULT_INPUT)
    if location:
        inp["location"] = location
    if limit:
        inp["limitPerSource"] = int(limit)
    if date_posted:
        inp["datePosted"] = date_posted
    kws = keywords or inp.pop("keywords")
    inp.pop("keywords", None)
    kws = [kws] if isinstance(kws, str) else kws
    actor = env("APIFY_ACTOR", "meka.im~linkedin-jobs-scraper", required=False)
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
    items, errors = [], []

    def one(kw):  # the actor takes one keyword per run, so run several at once
        try:
            got = request("POST", url, params={"token": env("APIFY_TOKEN")}, json={**inp, "keywords": kw}, timeout=900).json()
            return kw, [dict(g, _query=kw) for g in got], None
        except RuntimeError as e:
            return kw, [], e

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(kws)))) as ex:
        for kw, got, err in ex.map(one, kws):
            if err:
                log(f"  {kw!r}: FAILED {err}")
                errors.append(err)
            else:
                log(f"  {kw!r}: {len(got)} jobs")
                items += got
    if errors and not items:
        raise RuntimeError(f"every scrape run failed: {errors[0]}")  # names the wall (token, credit) for alerts.guard
    return items


# ---- LinkedIn hiring POSTS (Apify harvestapi/linkedin-post-search) ------------------------------------------------
POST_QUERIES = ["hiring founder's office", "hiring chief of staff", "hiring business development associate",
                "founder's office hiring startup", "we are hiring associate to the CEO"]
POST_ROLE = re.compile(r"founder['\u2019]?s?\s+office|chief of staff|business development|\bbd\b|sales|growth|strategy|"
                       r"associate to (the )?(ceo|founder)|ceo['\u2019]?s office", re.I)
JOB_BOARD = re.compile(r"jobs?\b|vacanc|recruit|hiring|career|placement|staffing|manpower|talent|remote(?!ly)|ajira|\bhub\b|chakri|tempjobs", re.I)


def scrape_posts(queries=None, max_posts=50, posted_limit="month", sort_by="date", log=print, workers=3) -> list[dict]:
    """One actor run per query (same cost, about $0.002 per post), so each post carries its keyword in `_query`."""
    from concurrent.futures import ThreadPoolExecutor
    actor = env("APIFY_POSTS_ACTOR", "harvestapi~linkedin-post-search", required=False)
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
    qs = queries or POST_QUERIES

    def one(q):
        inp = {"searchQueries": [q], "maxPosts": int(max_posts), "postedLimit": posted_limit, "sortBy": sort_by}
        try:
            return q, request("POST", url, params={"token": env("APIFY_TOKEN")}, json=inp, timeout=1800).json(), None
        except RuntimeError as e:
            return q, [], e

    items, errors = [], []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(qs)))) as ex:
        for q, got, err in ex.map(one, qs):
            log(f"  {q!r}: " + (f"FAILED {err}" if err else f"{len(got)} posts"))
            errors += [err] if err else []
            items += [dict(g, _query=q) for g in got]
    if errors and not items:
        raise RuntimeError(str(errors[0]))  # the first error names the wall (token, credit) for alerts.guard
    return items


def posts_to_jobs(items):
    """Hiring posts -> job dicts (normalise() shape) plus counts of what was dropped and why.
    Kept: posts by a COMPANY page, about a role we care about, not from a job board or recruiter, not an assistant role.
    Posts by people are counted but dropped: they carry no company link to match on."""
    from collections import Counter
    jobs, seen, st, by_q = [], set(), Counter(), {}
    for it in items:
        q = it.get("_query") or ""
        before = Counter(st)
        st["total"] += 1
        a = it.get("author") or {}
        text = (it.get("content") or "").strip()
        title = re.sub(r"\s+", " ", next((l for l in text.splitlines() if l.strip()), "")).strip()[:120]
        pid = str(it.get("id") or it.get("entityId") or "")
        if not pid or pid in seen:
            st["duplicate"] += 1
        elif a.get("type") != "company":
            st["person"] += 1
        elif "/showcase/" in (a.get("linkedinUrl") or "") or not a.get("universalName"):
            st["showcase page"] += 1
        elif JOB_BOARD.search(a.get("name") or ""):
            st["job board or recruiter"] += 1
        elif not POST_ROLE.search(text[:600]):
            st["not our roles"] += 1
        elif rules.hard_exclude(a.get("name") or "", title):
            st["hard excluded"] += 1
        else:
            seen.add(pid)
            st["kept"] += 1
            jobs.append({"job_id": "post-" + pid, "title": "[Post] " + title, "job_url": it.get("linkedinUrl") or it.get("shareLinkedinUrl") or "",
                         "posted": (it.get("postedAt") or {}).get("date"), "location": "", "poster": "", "poster_url": "",
                         "company": a.get("name") or "", "company_url": f"https://www.linkedin.com/company/{a['universalName']}",
                         "website": "", "headcount": None, "industry": "", "description": text[:1000], "about": "", "query": q})
        by_q.setdefault(q, Counter()).update(st - before)
    st = dict(st)
    st["by_query"] = {q: dict(c) for q, c in by_q.items()}
    return jobs, st


def run_posts(items, log=print, only_new=False):
    jobs, st = posts_to_jobs(items)
    log(f"posts: {st}")
    if only_new and jobs:
        listed = {c.get("Company LinkedIn URL") for c in at.Companies.all()}
        st["dedupe"] = dedupe_stats(jobs, listed)
        keep = [j for j in jobs if rules.norm_company_url(j.get("company_url")) not in listed]
        log(f"only new: {len(keep)} of {len(jobs)} posts are from companies not in Airtable yet")
        jobs = keep
    if not jobs:
        return {"companies": 0, "jobs": 0, "new": 0, "posts": st}
    return {**run_normalised(jobs, log, source="linkedin_posts"), "posts": st}


def dedupe_stats(jobs, listed):
    """Per keyword: unique companies found, how many are new, how many were already listed (dedupe on company LinkedIn URL)."""
    out, seen = {}, set()
    for j in jobs:
        u = rules.norm_company_url(j.get("company_url"))
        if not u:
            continue
        d = out.setdefault(j.get("query") or "", {"unique companies": 0, "new": 0, "already listed": 0, "seen in another keyword": 0})
        if u in seen:
            d["seen in another keyword"] += 1
            continue
        seen.add(u)
        d["unique companies"] += 1
        d["already listed" if u in listed else "new"] += 1
    return out


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
        "about": pick(item, "companyDescription", default=""),
        "query": item.get("_query") or "",
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
    if not llm.available():  # no Gemini key: Claude (via Airtable MCP) or Ayush scores these by hand
        for u in to_score:
            result[u] = {"Status": "Needs review", "ICP reason": "Awaiting manual scoring (no GEMINI_API_KEY)"}
        to_score = []
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


def run(raw, log=print):
    """Dedupe scraped jobs by company, classify, upsert into Airtable. Returns {companies, jobs, new}."""
    return run_normalised([normalise(r) for r in raw], log, source="linkedin_jobs")


def run_normalised(jobs, log=print, source=None):
    """Same, for job dicts that are already in normalise()'s shape (LinkedIn jobs and hiring posts share it)."""
    companies = rules.group_by_company(jobs)
    log(f"{len(companies)} unique companies ({len(jobs) - sum(map(len, companies.values()))} jobs dropped: no company URL / duplicate)")

    existing = {c.get("Company LinkedIn URL"): c for c in at.Companies.all()}
    # Without Gemini, only brand-new companies get a placeholder; never overwrite a status set by hand.
    new_urls = {u: j for u, j in companies.items()
                if u not in existing or (llm.available() and existing[u].get("ICP score") is None)}
    decisions = classify_new(new_urls)

    today = date.today().isoformat()
    rows = []
    for url, js in companies.items():
        j = js[0]
        row = {"Company LinkedIn URL": url, "Company": j["company"], "Website": j["website"], "Last seen": today}
        if j["headcount"]:
            row["Headcount"] = str(j["headcount"])
        if source and url not in existing:  # tag only the first source that found it
            row["Source"] = source
        if url in decisions:
            row.update(decisions[url])
            row["About"] = (j["about"] or j["description"] or "")[:1000]
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
    log(f"classified {len(decisions)} new companies: {counts}; upserted {len(rows)} companies, {len(job_rows)} jobs")

    return {"companies": len(rows), "jobs": len(job_rows), "new": len(decisions)}


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
    run(raw)


if __name__ == "__main__":
    main()
