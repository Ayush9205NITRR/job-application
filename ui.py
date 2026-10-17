#!/usr/bin/env python3
"""Local web UI for the outbound flow. stdlib only. One page (web/index.html) + a small JSON API.

    python ui.py            # open http://127.0.0.1:8765   (secrets come from .env or the environment)

Flow: 1 Scrape jobs (Apify) -> 2 Apollo account CSV -> 3 ICP filter + overlap with my clients -> 4 Contacts CSV
      -> 5 Review, approve, send.  Nothing is sent without approved=y and an explicit Send click.

Why it is fast: the page never reloads, Airtable is read once and cached in memory, edits apply instantly in the
browser and are saved in the background, website checks run in parallel.
"""
import argparse
import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import companies
import outbound as ob
import overlap

TOKEN = secrets.token_urlsafe(16)  # every API call must carry it, other websites cannot know it
WEB = Path(__file__).resolve().parent / "web" / "index.html"
MAX_BODY = 25 * 1024 * 1024
JOB = {"id": 0, "name": "", "running": False, "log": [], "done": 0, "total": 0, "locks_queue": False}
JOB_LOCK = threading.Lock()
QLOCK = threading.RLock()      # queue.csv read-modify-write
SLOCK = threading.RLock()      # ui_state.json
PENDING = {}                   # previewed Apollo CSV waiting for 'Save to Airtable' (memory only, never on disk)

DEFAULT_ICP = companies.DEFAULT_ICP
DEFAULT_CLIENTS = ("Raphe\nMongo DB | MongoDB\nSprinto\nAnarock\nMyhq | myHQ\nAugmont\nMaruti RND | Maruti R&D\nClueso\n"
                   "Dangle Ads\nGFI\nTeam Computers\nSMB Microfinance\nAdmattic")
DEFAULT_KEYWORDS = ("Founder's Office\nFounders Office Associate\nChief of Staff\nAssociate to the CEO\nBusiness Development Associate\n"
                    "Growth Associate\nStrategy and Operations Associate\nSales Strategy Associate")
DATE_POSTED = {"pastWeek", "pastMonth", "anyTime", "past24Hours"}
POSTED_LIMIT = {"24h", "week", "month", "3months", "any"}
DEFAULT_POST_QUERIES = ("hiring founder's office\nhiring chief of staff\nhiring business development associate\n"
                        "founder's office hiring startup\nwe are hiring associate to the CEO")


# ---- persisted UI state (settings, selection, overlap results) -----------------------------------------------
def state_path():
    return ob.QUEUE.with_name("ui_state.json")


def load_state():
    with SLOCK:
        try:
            st = json.loads(state_path().read_text())
        except (OSError, ValueError):
            st = {}
    st.setdefault("icp", dict(DEFAULT_ICP))
    st.setdefault("clients", DEFAULT_CLIENTS)
    st.setdefault("selected", [])
    st.setdefault("inter", {})
    st.setdefault("last_scrape", None)
    st.setdefault("keywords", DEFAULT_KEYWORDS)
    st.setdefault("post_queries", DEFAULT_POST_QUERIES)
    st.setdefault("last_posts", None)
    st.setdefault("warn", "")
    return st


def save_state(st):
    with SLOCK:
        tmp = state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        os.replace(tmp, state_path())


def update_state(**kw):
    with SLOCK:
        st = load_state()
        st.update(kw)
        save_state(st)
        return st


def cache_dir():
    return ob.QUEUE.with_name("site_cache")


# ---- helpers shared with the old UI -----------------------------------------------------------------------------
def status(r):
    if r["replied"] in ob.CLOSED:
        return "closed"
    if r["fu2_at"] or r["fu1_at"] or r["sent_at"]:
        return "sent"
    return "approved" if r["approved"] == "y" else "review"


def state_label(r):
    if r["replied"] == "y":
        return "replied"
    if r["replied"]:
        return r["replied"]
    if r["fu2_at"]:
        return "fu2 sent"
    if r["fu1_at"]:
        return "fu1 sent"
    if r["sent_at"]:
        return "sent " + r["sent_at"][:10]
    return "approved, waiting for next send" if r["approved"] == "y" else "needs review"


def allowed_host(host_header, environ, port):
    """Only localhost, plus this exact Codespaces forwarded address when running inside a Codespace."""
    host = host_header.split(":")[0].lower()
    if host in ("127.0.0.1", "localhost"):
        return True
    name, domain = environ.get("CODESPACE_NAME"), environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    return bool(name and domain and host == f"{name}-{port}.{domain}".lower())


# ---- background jobs ---------------------------------------------------------------------------------------------
def run_job(name, fn, locks_queue=False):
    """One background job at a time; its log lines and progress are polled by the page."""
    with JOB_LOCK:
        if JOB["running"]:
            return False
        JOB.update(id=JOB["id"] + 1, name=name, running=True, log=[], done=0, total=0, locks_queue=locks_queue)

    def target():
        def cap(*a):
            line = " ".join(str(x) for x in a)
            JOB["log"].append(line)
            print(line, flush=True)
        original = ob.log
        ob.log = cap
        try:
            fn()
        except BaseException as e:  # SystemExit from missing credentials too
            JOB["log"].append(f"STOPPED: {e}")
        finally:
            ob.log = original
            JOB["running"] = False
    threading.Thread(target=target, daemon=True).start()
    return True


def progress(done, total):
    JOB["done"], JOB["total"] = done, total


def queue_locked():
    return JOB["running"] and JOB["locks_queue"]


# ---- API ------------------------------------------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def row_json(i, r):
    return {"n": i + 1, "first": r["first"], "company": r["company"], "email": r["email"], "title": r["title"],
            "subject": r["subject"], "line": r["line"], "body": r["body"], "status": status(r), "state": state_label(r),
            "pitch": [x for x in r["pitch"].split("; ") if x], "note": r["overlap_note"], "wa": r["wa_link"] if r["sent_at"] else ""}


def api_state():
    st = load_state()
    with QLOCK:
        rows = ob.load_queue()
    counts = {k: 0 for k in ("review", "approved", "sent", "closed")}
    for r in rows:
        counts[status(r)] += 1
    now = ob.now_utc()
    return {"stats": {"sent_today": ob.sent_today(rows, now), "cap": ob.daily_cap(rows, now), "halted": ob.halted(rows), "counts": counts},
            "queue": [row_json(i, r) for i, r in enumerate(rows)], "icp": st["icp"], "clients": st["clients"],
            "selected": st["selected"], "keywords": st["keywords"], "last_scrape": st["last_scrape"], "warn": st["warn"],
            "post_queries": st["post_queries"], "last_posts": st["last_posts"],
            "env": {"airtable": companies.airtable_ready(), "apify": bool(os.environ.get("APIFY_TOKEN")),
                    "gmail": bool(os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD"))}}


def api_companies(refresh=False):
    if not companies.airtable_ready():
        return {"companies": [], "error": "Airtable is not connected (AIRTABLE_TOKEN / AIRTABLE_BASE_ID)."}
    try:
        comps = companies.get(refresh)
    except Exception as e:
        return {"companies": [], "error": f"Could not read Airtable: {e}"}
    inter = load_state()["inter"]
    return {"companies": [companies.serialize(c, inter) for c in comps], "error": ""}


def selected_comps(ids=None):
    """Cached companies the user has selected (the page's ticks, saved by /api/select), flagged Shortlist=True for the
    helpers that look at it. The local selection is the only truth; Airtable's flag is a copy and can be stale."""
    want = set(ids if ids is not None else load_state()["selected"])
    return [dict(c, Shortlist=True) for c in companies.get() if c["_id"] in want]


def save_selection(selected, shown):
    """Local selection is instant; Airtable is updated in the background."""
    update_state(selected=list(selected))
    if not companies.airtable_ready():
        return

    def bg():
        try:
            companies.save_shortlist(selected, shown)
            update_state(warn="")
        except Exception as e:
            update_state(warn=f"Airtable save failed: {e}")
    threading.Thread(target=bg, daemon=True).start()


def edit_queue(fn):
    if queue_locked():
        raise ApiError(409, "A send is running, wait for it to finish before editing.")
    with QLOCK:
        rows = ob.load_queue()
        changes, errors = fn(rows)
        ob.save_queue(rows)
        return {"changed": len(changes), "errors": errors, "queue": [row_json(i, r) for i, r in enumerate(rows)]}


def tmp_csv(raw):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    return path


def post(path, body, raw, headers):
    """Returns (json-able, extra) or raises ApiError. `body` is parsed JSON ({} for raw uploads)."""
    if path == "/api/icp":
        update_state(icp=body.get("icp") or dict(DEFAULT_ICP))
        return {"ok": True}
    if path == "/api/clients":
        update_state(clients=str(body.get("text", ""))[:20000])
        return {"ok": True, "n": len(overlap.parse_clients(body.get("text", "")))}
    if path == "/api/select":
        save_selection(body.get("selected", []), body.get("shown", []))
        return {"ok": True}
    if path == "/api/pick":
        with SLOCK:
            st = load_state()
            e = st["inter"].setdefault(str(body.get("id")), {"names": [], "pick": [], "note": "", "at": ""})
            e["pick"] = [n for n in body.get("names", []) if n in e["names"]]
            save_state(st)
        return {"ok": True}
    if path == "/api/scrape":
        kws = [k.strip() for k in str(body.get("keywords", "")).splitlines() if k.strip()]
        if not kws:
            raise ApiError(400, "Add at least one keyword.")
        if not os.environ.get("APIFY_TOKEN") or not companies.airtable_ready():
            raise ApiError(400, "APIFY_TOKEN and the Airtable settings are needed for scraping.")
        date_posted = body.get("date_posted") if body.get("date_posted") in DATE_POSTED else None
        update_state(keywords="\n".join(kws))

        def job():
            import pipeline
            from fo import airtable as at
            at.DRY_RUN = False
            raw_jobs = pipeline.scrape(kws, body.get("location") or None, body.get("limit") or None, date_posted, log=lambda *a: ob.log(*a))
            ob.log(f"scraped {len(raw_jobs)} jobs")
            r = pipeline.run(raw_jobs, log=lambda *a: ob.log(*a))
            companies.invalidate()
            update_state(last_scrape={"at": datetime.now(timezone.utc).isoformat(), "jobs": len(raw_jobs), **r})
        return {"started": run_job("scrape jobs", job)}
    if path == "/api/scrape_posts":
        qs = [k.strip() for k in str(body.get("queries", "")).splitlines() if k.strip()]
        if not qs:
            raise ApiError(400, "Add at least one search.")
        if not os.environ.get("APIFY_TOKEN") or not companies.airtable_ready():
            raise ApiError(400, "APIFY_TOKEN and the Airtable settings are needed for scraping.")
        posted = body.get("posted_limit") if body.get("posted_limit") in POSTED_LIMIT else "month"
        per = max(1, min(int(body.get("max_posts") or 50), 500))
        update_state(post_queries="\n".join(qs))

        def job():
            import pipeline
            from fo import airtable as at
            at.DRY_RUN = False
            items = pipeline.scrape_posts(qs, per, posted, "date", log=lambda *a: ob.log(*a))
            r = pipeline.run_posts(items, log=lambda *a: ob.log(*a))
            companies.invalidate()
            update_state(last_posts={"at": datetime.now(timezone.utc).isoformat(), "posts": len(items), **{k: v for k, v in r.items() if k != "posts"}, "dropped": r["posts"]})
        return {"started": run_job("scrape posts", job)}
    if path == "/api/apollo":      # step 1 of 2: look, do not write
        if not companies.airtable_ready():
            raise ApiError(400, "Airtable is not connected.")
        csv_path = tmp_csv(raw)
        try:
            r = companies.preview_apollo(csv_path, icp_settings=load_state()["icp"])
        except Exception as e:
            raise ApiError(400, f"Could not read that file: {e}")
        finally:
            os.unlink(csv_path)
        PENDING["apollo"] = raw
        return r
    if path == "/api/apollo/commit":   # step 2 of 2: write the previewed file into Airtable
        raw = PENDING.get("apollo")
        if not raw:
            raise ApiError(400, "Nothing to save. Upload the Apollo CSV again.")
        csv_path = tmp_csv(raw)
        try:
            r = companies.import_apollo_companies(csv_path)
        except Exception as e:
            raise ApiError(400, f"Could not save to Airtable: {e}")
        finally:
            os.unlink(csv_path)
        PENDING.pop("apollo", None)
        companies.invalidate()
        return r
    if path == "/api/apollo/discard":
        PENDING.pop("apollo", None)
        return {"ok": True}
    if path == "/api/intersect":
        ids = body.get("ids", [])
        comps = [c for c in selected_comps(ids)]
        clients = [c for c in overlap.parse_clients(load_state()["clients"])]
        if not clients:
            raise ApiError(400, "Add your clients first (one per line).")

        def job():
            ob.log(f"checking {len(comps)} websites against {len(clients)} of your clients")
            res = companies.find_intersections(comps, clients, cache_dir(), progress=progress)
            with SLOCK:
                st = load_state()
                st["inter"].update(res)
                save_state(st)
            ob.log(f"done: {sum(1 for v in res.values() if v['names'])} companies share a client with you")
        return {"started": run_job("find overlap", job)}
    if path == "/api/ics":
        blocks = companies.schedule(selected_comps(body.get("ids", [])), int(body.get("per") or 5), int(body.get("hour") or 11), int(body.get("minute") or 0))
        if not blocks:
            raise ApiError(400, "Nothing to schedule: select companies that have a job and are not applied yet.")
        return {"ics": companies.make_ics(blocks), "blocks": len(blocks)}
    if path == "/api/applied":
        return {"n": companies.mark_applied(body.get("ids", []))}
    if path == "/api/contacts":
        if not companies.airtable_ready():
            raise ApiError(400, "Airtable is not connected, the selected companies are needed.")
        st = load_state()
        csv_path = tmp_csv(raw)
        try:
            with QLOCK:
                added, skipped, pitched = companies.import_pocs(
                    csv_path, headers.get("x-verified") == "yes", selected_comps(),
                    {k: v["pick"] for k, v in st["inter"].items()}, log=lambda *a: ob.log(*a))
        except Exception as e:
            raise ApiError(400, f"Could not read that file: {e}")
        finally:
            os.unlink(csv_path)
        return {"added": added, "pitched": pitched, "generic": added - pitched, "skipped": skipped}
    if path == "/api/queue/line":
        return edit_queue(lambda rows: ob.apply_commands(rows, [("line", int(body.get("row", 0)), str(body.get("text", "")).replace("\n", " "))]))
    if path == "/api/queue/approve":
        which = body.get("rows")

        def fn(rows):
            if which in ("all", "pitch", "generic"):
                picked = [i + 1 for i, r in enumerate(rows) if status(r) == "review"
                          and (which == "all" or bool(r["pitch"]) == (which == "pitch"))]
            else:
                picked = [int(x) for x in which or []]
            return ob.apply_commands(rows, [("approve", picked)])
        return edit_queue(fn)
    if path == "/api/queue/unapprove":
        def fn(rows):
            n = 0
            for x in body.get("rows", []):
                r = rows[int(x) - 1] if 1 <= int(x) <= len(rows) else None
                if r and not r["sent_at"] and r["replied"] not in ob.CLOSED and r["approved"] == "y":
                    r["approved"] = ""
                    n += 1
            return [None] * n, []
        return edit_queue(fn)
    if path == "/api/queue/skip":
        return edit_queue(lambda rows: ob.apply_commands(rows, [("skip", [int(x) for x in body.get("rows", [])])]))
    if path == "/api/send":
        what = body.get("what")
        fns = {"send": lambda: (ob.cmd_send(), ob.cmd_followup()), "cycle": ob.cmd_cycle, "dry": lambda: ob.cmd_cycle(dry=True)}
        if what not in fns:
            raise ApiError(400, "unknown action")
        if not os.environ.get("GMAIL_USER") or not os.environ.get("GMAIL_APP_PASSWORD"):
            if what != "dry":
                raise ApiError(400, "GMAIL_USER / GMAIL_APP_PASSWORD are not set.")
        return {"started": run_job(what, fns[what], locks_queue=True)}
    raise ApiError(404, "not found")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ok_host(self):
        return allowed_host(self.headers.get("Host") or "", os.environ, self.server.server_address[1])

    def _send(self, code, body=b"", ctype="application/json", headers=None):
        if not isinstance(body, bytes):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def do_GET(self):
        if not self._ok_host():
            return self._send(403, "forbidden", "text/plain")
        u = urlparse(self.path)
        if u.path == "/":
            return self._send(200, WEB.read_text(encoding="utf-8").replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
        if not u.path.startswith("/api/"):
            return self._send(404, "not found", "text/plain")
        if not secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            return self._json({"error": "bad token, reload the page"}, 403)
        if u.path == "/api/state":
            return self._json(api_state())
        if u.path == "/api/companies":
            return self._json(api_companies("refresh" in parse_qs(u.query)))
        if u.path == "/api/job":
            return self._json({k: (v[-200:] if k == "log" else v) for k, v in JOB.items()})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._ok_host():
            return self._send(403, "forbidden", "text/plain")
        if not secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            return self._json({"error": "bad token, reload the page"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._json({"error": "file too large"}, 413)
        raw = self.rfile.read(n)
        headers = {k.lower(): v for k, v in self.headers.items()}
        is_json = self.headers.get("Content-Type", "").startswith("application/json")
        try:
            body = json.loads(raw or b"{}") if is_json else {}
            out = post(urlparse(self.path).path, body, raw, headers)
        except ApiError as e:
            return self._json({"error": e.msg}, e.code)
        except (ValueError, TypeError, KeyError) as e:
            return self._json({"error": f"bad request: {e}"}, 400)
        self._json(out)


def make_server(port=8765):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv = make_server(a.port)
    print(f"Open http://127.0.0.1:{a.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
