"""Stage 4 of Canvas A: the numbers that show whether the agent is performing. Pure counting on serialized companies."""
import re

SEED, PRESEED = re.compile(r"^seed$|\bseed\b", re.I), re.compile(r"pre.?seed|angel", re.I)


def report(rows, icp_pass=None):
    """rows: companies.serialize() dicts. icp_pass: optional callable(row)->bool (default: every row counts)."""
    ok = [r for r in rows if (icp_pass(r) if icp_pass else True)]
    stage = lambda r: r.get("stage") or ""
    out = {
        "unique accounts": len(rows),
        "verified (Apollo)": sum(1 for r in rows if r.get("matched")),
        "not verified": sum(1 for r in rows if not r.get("matched")),
        "ICP pass": len(ok),
        "funded": sum(1 for r in ok if (r.get("funding_n") or 0) > 0),
        "unfunded or unknown": sum(1 for r in ok if not (r.get("funding_n") or 0)),
        "seed": sum(1 for r in ok if SEED.search(stage(r)) and not PRESEED.search(stage(r))),
        "pre-seed": sum(1 for r in ok if PRESEED.search(stage(r))),
        "from job posts": sum(1 for r in rows if any(not j["url"].startswith("https://www.linkedin.com/posts/") for j in r.get("jobs", []))),
        "from hiring posts": sum(1 for r in rows if any("/posts/" in j["url"] for j in r.get("jobs", []))),
    }
    return out
