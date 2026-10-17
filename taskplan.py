"""Decides which tasks the agent needs (code filters first, so the agent only sees real work), and reports on how it is doing.

    python taskplan.py plan [--max 20] [--cap 100] [--dry]   create Open tasks, with per-type and daily caps
    python taskplan.py stats                                  counts, low-confidence rate, audit accuracy
    python taskplan.py audit                                  every 10th Applied task, for a human spot check
    python taskplan.py step [--max 10]                        one full round: requeue, apply Done, plan next tasks, stats
    python taskplan.py cycle                                  requeue stuck, apply Done, then stats (safe to run on a schedule)

Env TASK_LANES (default 1): tasks are spread round-robin over Lane 1..N so several agents never share a task."""
import hashlib
import json
import os
import sys
from collections import Counter

import companies
import tasks as T


def classify_ok(task):
    """True when a classify_post task finished as startup."""
    try:
        return task.get("Status") == "Applied" and json.loads(task.get("Result") or "{}").get("kind") == "startup"
    except ValueError:
        return False


BIG = 200  # LinkedIn headcount above this is not a seed/pre-seed target: no agent time spent


def too_big(c):
    n = companies.employees(c.get("Headcount"))
    return n is not None and n > BIG


def select(comps, existing):
    """comps: Airtable Companies with jobs. existing: {Task ID: task}. -> {type: [(company row, serialized, payload)]}"""
    out = {k: [] for k in T.TYPES}
    for c in comps:
        s = companies.serialize(c)
        if s["excluded"] or s["done"] or not s["li"]:
            continue
        base = {"company": s["name"], "linkedin": s["li"], "website": s["website"]}
        cls = existing.get(f"classify_post:{c['_id']}")
        if not s["matched"]:
            if too_big(c):
                continue
            first = next(iter(c.get("jobs") or []), {})
            title = first.get("Title") or ""
            title = title[7:] if title.startswith("[Post] ") else title
            if s["status"] == "Needs review" and not cls:
                out["classify_post"].append((c, s, {**base, "post": title, "post_url": first.get("Job URL", "")}))
            if cls and classify_ok(cls):  # recruiters, services firms and non-hiring posts never reach verify
                out["verify_company"].append((c, s, {**base, "search_results": []}))
        elif companies.icp_check(s)[0]:
            out["pick_decision_makers"].append((c, s, {**base, "people": []}))
    return out


def plan(max_per_type=20, cap=100, dry=False, tasks=None, comps=None):
    tasks = tasks or T.Tasks
    comps = comps if comps is not None else companies.fetch()
    rows = tasks.all()
    existing = {t.get("Task ID"): t for t in rows}
    today = T.stamp()[:10]
    budget = max(0, cap - sum(1 for t in rows if str(t.get("Created at", ""))[:10] == today))
    lanes = max(1, int(os.environ.get("TASK_LANES", "1") or 1))
    made, n = Counter(), len(rows)
    for kind, items in select(comps, existing).items():
        for c, s, payload in items[:max_per_type]:
            if budget <= 0:
                return dict(made, capped=True)
            if f"{kind}:{c['_id']}" in existing:
                continue
            if not dry:
                T.create_task(kind, s["name"], c["_id"], payload, tasks, lane=n % lanes + 1)
            existing[f"{kind}:{c['_id']}"] = {}
            made[kind] += 1
            n += 1
            budget -= 1
    return dict(made)


def in_audit(task_id):
    return int(hashlib.sha1(str(task_id).encode()).hexdigest(), 16) % 10 == 0


def stats(tasks=None):
    rows = (tasks or T.Tasks).all()
    by = Counter((t.get("Type"), t.get("Status")) for t in rows)
    done = [t for t in rows if t.get("Status") in ("Done", "Applied", "Needs human", "Failed")]
    low = sum(1 for t in done if t.get("Confidence") == "Low")
    audited = [t for t in rows if t.get("Audit")]
    wrong = sum(1 for t in audited if t.get("Audit") == "Wrong")
    return {"tasks": len(rows), "by_type_status": {f"{k[0]}/{k[1]}": v for k, v in sorted(by.items(), key=str)},
            "failed": sum(v for k, v in by.items() if k[1] == "Failed"),
            "needs_human": sum(v for k, v in by.items() if k[1] == "Needs human"),
            "low_confidence_pct": round(100 * low / len(done)) if done else 0,
            "audited": len(audited), "audit_wrong": wrong,
            "audit_accuracy_pct": round(100 * (len(audited) - wrong) / len(audited)) if audited else None}


def audit(tasks=None):
    pick = [t for t in (tasks or T.Tasks).all() if t.get("Status") == "Applied" and in_audit(t.get("Task ID")) and not t.get("Audit")]
    return pick


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    arg = lambda name, d: int(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else d
    if cmd == "plan":
        print("PLAN", plan(arg("--max", 20), arg("--cap", 100), "--dry" in sys.argv))
    elif cmd == "stats":
        print("STATS", json.dumps(stats(), indent=1))
    elif cmd == "audit":
        for t in audit():
            print(f"- {t['Company']} | {t['Type']} | {t.get('Result', '')[:200]} -> set Audit to OK or Wrong")
    elif cmd == "step":  # one round: free stuck tasks, apply what is Done, create the next tasks, report
        print("requeued", T.requeue_stale())
        print("applied", T.apply_done())
        print("PLAN", plan(arg("--max", 10), arg("--cap", 100)))
        print("STATS", json.dumps(stats(), indent=1))
    elif cmd == "cycle":
        print("requeued", T.requeue_stale())
        print("applied", T.apply_done())
        print("STATS", json.dumps(stats(), indent=1))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
