#!/usr/bin/env python3
"""Local web UI for outbound.py. stdlib only, binds to 127.0.0.1 (never exposed).

    python ui.py            # then open http://127.0.0.1:8765   (secrets come from .env or the environment)

Flow, one tab per step:
  1 Companies  upload the Apollo COMPANY csv -> matched onto the Airtable companies (funding, employees, industry)
  2 Shortlist  ICP matches (seed, 2 to 50 employees) are highlighted and pre-tickable; save; download apply-calendar .ics
  3 POCs       upload your clients (once) and the POC csv you built; drafts are created, overlap is looked up
  4 Review     edit the opening line, approve, send. Nothing is sent without approved=y.
Uploaded CSVs are deleted right after they are read.
"""
import argparse
import html
import os
import secrets
import tempfile
import threading
from email import message_from_bytes
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import companies
import outbound as ob
import overlap

TOKEN = secrets.token_urlsafe(16)  # CSRF: every POST must carry it, other websites cannot know it
JOB = {"name": "", "running": False, "log": []}
JOB_LOCK = threading.Lock()
VIEWS = ["review", "approved", "sent", "closed", "all"]
TABS = [("companies", "1 Companies"), ("shortlist", "2 Shortlist"), ("pocs", "3 POCs"), ("review", "4 Review and send")]


def esc(s):
    return html.escape(str(s or ""), quote=True)


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
    """Only localhost, plus this exact Codespaces forwarded address when running inside a Codespace.
    Anything else (DNS rebinding, other sites) is refused."""
    host = host_header.split(":")[0].lower()
    if host in ("127.0.0.1", "localhost"):
        return True
    name, domain = environ.get("CODESPACE_NAME"), environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    return bool(name and domain and host == f"{name}-{port}.{domain}".lower())


def parse_multipart(content_type, body):
    """-> {field: (filename|None, bytes)} using the stdlib email parser (cgi is gone in new Pythons)."""
    msg = message_from_bytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body, policy=email_policy)
    out = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            out[name] = (part.get_filename(), part.get_payload(decode=True) or b"")
    return out


def run_job(name, fn):
    """Run fn in a thread; its log lines are captured for the page. One job at a time."""
    with JOB_LOCK:
        if JOB["running"]:
            return False
        JOB.update(name=name, running=True, log=[])

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


def stats(rows):
    now = ob.now_utc()
    counts = {v: 0 for v in VIEWS[:4]}
    for r in rows:
        counts[status(r)] += 1
    cap = ob.daily_cap(rows, now)
    return counts, ob.sent_today(rows, now), cap, ob.halted(rows)


CSS = """body{font:14px/1.45 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1b1f24}
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 4px}
.card{background:#fff;border:1px solid #dde1e6;border-radius:8px;padding:14px;margin:12px 0}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.pill{background:#eef1f5;border-radius:99px;padding:2px 10px}
.warn{background:#fff4e5;border-color:#f0c36d}.bad{background:#fdecea;border-color:#e5978f}.ok{background:#e9f7ef;border-color:#8fd0a8}
table{width:100%;border-collapse:collapse}td,th{padding:6px 8px;border-bottom:1px solid #eceff3;vertical-align:top;text-align:left}
input[type=text]{width:100%;box-sizing:border-box;padding:5px}button{padding:6px 12px;border-radius:6px;border:1px solid #9aa4b2;background:#fff;cursor:pointer}
button.primary{background:#1d4ed8;color:#fff;border-color:#1d4ed8}pre{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:10px;border-radius:6px;max-height:260px;overflow:auto}
tr.icp{background:#e9f7ef}a.tab{padding:4px 10px;border-radius:6px;text-decoration:none;color:#1b1f24}a.tab.on{background:#1d4ed8;color:#fff}details{margin-top:4px}"""


def proof_path():
    return ob.QUEUE.with_name("proof.csv")


def cache_dir():
    return ob.QUEUE.with_name("site_cache")


def header(rows, tab, msg):
    counts, today, cap, halted = stats(rows)
    running = JOB["running"]
    h = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>Outbound</title>{'<meta http-equiv=refresh content=4>' if running else ''}<style>{CSS}</style><main>"
         f"<h1>Founder's Office outbound</h1><div class=row><span class=pill>sent today {today}/{cap}</span>"
         + "".join(f"<span class=pill>{k} {v}</span>" for k, v in counts.items()) + "</div>"
         "<div class=row style='margin:10px 0'>" + "".join(
             f"<a class='tab{' on' if k == tab else ''}' href='/?tab={k}'>{esc(label)}</a>" for k, label in TABS) + "</div>"]
    if msg:
        h.append(f"<div class='card ok'>{esc(msg)}</div>")
    if halted:
        h.append("<div class='card bad'><b>Sending is halted:</b> bounce rate is too high. Review rows marked bounced.</div>")
    if JOB["log"] or running:
        h.append(f"<div class=card><b>{esc(JOB['name'])}</b> {'(running, page refreshes)' if running else '(finished)'}"
                 f"<pre>{esc(chr(10).join(JOB['log'][-60:]) or '...')}</pre></div>")
    return h


def tok():
    return f"<input type=hidden name=token value={TOKEN}>"


def tab_companies():
    ready = companies.airtable_ready()
    h = ["<div class=card><b>1. Apollo company CSV</b><p>Export your Apollo <b>companies</b> (it must have Company Linkedin Url, "
         "Latest Funding, # Employees, Industry). Each Apollo company is matched onto the Airtable companies from the job scrape "
         "(LinkedIn slug, then website, then exact name). Funding and other details are written into Airtable.</p>"]
    if not ready:
        h.append("<div class='card warn'>AIRTABLE_TOKEN and AIRTABLE_BASE_ID are not set. Add them to <code>.env</code> (or as Codespaces "
                 "secrets) and restart. The PAT needs data.records read/write and schema.bases read/write on that base.</div>")
    h.append(f"<form method=post action=/companies/upload enctype=multipart/form-data class=row>{tok()}"
             f"<input type=file name=csv accept=.csv required><button class=primary{'' if ready else ' disabled'}>Upload and match</button></form>"
             "<small>A file with people (emails) also works here, only the company columns are used. Nothing is emailed from this step.</small></div>")
    return h


def tab_shortlist(auto):
    if not companies.airtable_ready():
        return ["<div class='card warn'>Connect Airtable first (see tab 1).</div>"]
    try:
        comps = companies.fetch()
    except Exception as e:
        return [f"<div class='card bad'>Could not read Airtable: {esc(e)}</div>"]
    rows = [c for c in comps if c.get("Apollo match")]
    flagged = {c["_id"]: companies.icp(c) for c in rows}
    rows.sort(key=lambda c: (not flagged[c["_id"]][0], c.get("Company") or ""))
    h = [f"<div class=card><b>2. Shortlist</b> {len(rows)} companies matched with Apollo; "
         f"{sum(1 for v in flagged.values() if v[0])} fit the ICP (seed or pre-seed, 2 to 50 employees, highlighted green). "
         f"<a href='/?tab=shortlist&auto=1'>Tick all ICP matches</a></div>"]
    if not rows:
        return h + ["<div class=card>Nothing matched yet. Upload the Apollo company CSV in tab 1.</div>"]
    h.append(f"<form method=post action=/shortlist class='card'>{tok()}<input type=hidden name=shown value='{','.join(c['_id'] for c in rows)}'>"
             "<div class=row><button name=do value=save class=primary>Save shortlist</button>"
             "<span>Apply blocks: <input type=number name=per value=5 min=1 max=20 style='width:60px'> companies per block, at "
             "<input type=text name=time value=11:00 style='width:70px'> IST, one block per weekday</span>"
             "<button name=do value=ics>Save and download calendar (.ics)</button>"
             "<button name=do value=applied>Mark ticked as applied</button></div>"
             "<small>The .ics has one 30 minute block per day listing company, job title and link, with a 10 minute reminder. "
             "Open the downloaded file once and your calendar adds the blocks (re-importing updates, it does not duplicate).</small>"
             "<table><tr><th></th><th>Company</th><th>Apollo</th><th>ICP</th><th>Jobs to apply</th><th>Status</th></tr>")
    for c in rows:
        ok, why = flagged[c["_id"]]
        ticked = bool(c.get("Shortlist")) or (auto and ok)
        jobs = "<br>".join(f"<a href='{esc(j.get('Job URL'))}' target=_blank rel=noopener>{esc(j.get('Title') or 'job')}</a>" for j in c["jobs"][:3]) or "<small>no job</small>"
        name = esc(c.get("Company"))
        if c.get("Company LinkedIn URL"):
            name = f"<a href='{esc(c['Company LinkedIn URL'])}' target=_blank rel=noopener>{name}</a>"
        h.append(f"<tr class='{'icp' if ok else ''}'><td><input type=checkbox name=rows value={esc(c['_id'])}{' checked' if ticked else ''}></td>"
                 f"<td><b>{name}</b></td><td><small>{esc(c.get('Funding stage'))} | {esc(c.get('Total funding'))} | "
                 f"{esc(c.get('Employees (Apollo)'))} emp<br>{esc(c.get('Industry (Apollo)'))}</small></td>"
                 f"<td>{'yes' if ok else 'no'}<br><small>{esc(why)}</small></td><td>{jobs}</td><td>{esc(c.get('Status'))}</td></tr>")
    h.append("</table></form>")
    return h


def tab_pocs():
    proof = overlap.load_proof(proof_path())
    h = [f"<div class=card><b>3a. Your clients (once)</b> {len(proof)} loaded"
         f"<p><small>CSV columns: <code>client, industry, persona, deal, name_usable</code>. <code>deal</code> continues the sentence "
         f"\"I ...\", for example <i>ran their outbound pipeline and closed Rs 2.7 Cr</i>. If name_usable is n, the client name is never used in an email, "
         f"only the industry.</small></p><form method=post action=/proof/upload enctype=multipart/form-data class=row>{tok()}"
         f"<input type=file name=csv accept=.csv required><button>Save my clients</button></form></div>",
         f"<div class=card><b>3b. POC CSV</b><p><small>Columns: <code>Company Linkedin Profile, POC First Name, POC Contact No, POC Email</code> "
         f"(optional: <code>Email Status</code>). Only POCs of <b>shortlisted</b> companies are added. For each company the public website "
         f"(home, customers, case studies, about; robots.txt respected) is read and compared with your clients. Same client becomes the "
         f"opening line; same industry is only suggested. Drafts are never approved automatically.</small></p>"
         f"<form method=post action=/poc/upload enctype=multipart/form-data class=row>{tok()}<input type=file name=csv accept=.csv required>"
         f"<label>If the file has no Email Status column, these emails are "
         f"<select name=verified><option value=no>NOT verified (skip them)</option><option value=yes>verified</option></select></label>"
         f"<button class=primary>Add POCs and find overlap</button></form></div>",
         f"<div class=card><details><summary>Already have an Apollo people CSV? (skips shortlist and overlap)</summary>"
         f"<form method=post action=/upload enctype=multipart/form-data class=row style='margin-top:8px'>{tok()}"
         f"<input type=file name=csv accept=.csv required><button>Upload and draft relevant rows</button></form>"
         f"<small>Only verified emails and non-excluded companies are added.</small></details></div>"]
    return h


def tab_review(rows, view):
    running = JOB["running"]
    creds_ok = bool(os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD"))  # outbound loads .env at import
    h = []
    if not creds_ok:
        h.append("<div class='card warn'>GMAIL_USER / GMAIL_APP_PASSWORD are not set (put them in .env or export them). "
                 "You can review, but sending and inbox checks will fail until you set them and restart.</div>")
    dis = " disabled" if running else ""
    h.append(f"<div class=card><div class=row><b>Send</b>"
             f"<form method=post action=/run>{tok()}"
             f"<button name=what value=send{dis} class=primary>Send approved + follow-ups</button> "
             f"<button name=what value=cycle{dis}>Full cycle (inbox, send, follow-ups, digest)</button> "
             f"<button name=what value=dry{dis}>Dry run</button></form>"
             f"<small>Caps, gaps (2 to 5 min) and bounce stop apply. Unapproved rows are never sent.</small></div></div>")
    h.append("<div class=card><div class=row><b>Review</b> " + "".join(
        f"<a class='tab{' on' if v == view else ''}' href='/?tab=review&view={v}'>{v}</a>" for v in VIEWS) + "</div>")
    shown = [(i, r) for i, r in enumerate(rows) if view == "all" or status(r) == view]
    if view == "review" and shown and not running:
        h.append(f"<form id=bulk method=post action=/bulk class=row style='margin:8px 0'>{tok()}"
                 f"<button name=action value=approve class=primary>Approve ticked</button>"
                 f"<button name=action value=approve_all>Approve all listed</button>"
                 f"<button name=action value=skip>Skip ticked</button></form>")
    h.append("<table><tr><th></th><th>#</th><th>Who</th><th>Opening line</th><th>State</th></tr>")
    for i, r in shown:
        n = i + 1
        tick = (f"<input type=checkbox name=rows value={n} form=bulk>" if view == "review" and not running else "")
        editable = status(r) in ("review", "approved") and not running
        if editable:
            line = (f"<form method=post action=/line>{tok()}"
                    f"<input type=hidden name=row value={n}><input type=text name=text value='{esc(r['line'])}'>"
                    f"<button>Save line</button></form>")
        else:
            line = esc(r["line"])
        extra = f"<br><small>Overlap: {esc(r['overlap_note'])}</small>" if r["overlap_note"] else ""
        if r["suggested_line"] and editable and r["suggested_line"] != r["line"]:
            extra += (f"<form method=post action=/line>{tok()}<input type=hidden name=row value={n}>"
                      f"<input type=hidden name=text value='{esc(r['suggested_line'])}'><small>Suggested: {esc(r['suggested_line'])}</small> "
                      f"<button>Use suggested line</button></form>")
        h.append(f"<tr><td>{tick}</td><td>{n}</td><td><b>{esc(r['first'])}</b> @ {esc(r['company'])}<br>"
                 f"<small>{esc(r['email'])}<br>{esc(r['title'])}</small></td><td>{line}{extra}"
                 f"<details><summary>Email</summary><b>{esc(r['subject'])}</b><pre>{esc(r['body'])}</pre></details>"
                 f"{('<a href=' + chr(34) + esc(r['wa_link']) + chr(34) + ' target=_blank>WhatsApp link</a>') if r['wa_link'] and r['sent_at'] else ''}"
                 f"</td><td>{esc(state_label(r))}</td></tr>")
    if not shown:
        h.append("<tr><td colspan=5>Nothing here.</td></tr>")
    h.append("</table></div>")
    return h


def page(view="review", msg="", tab=None, auto=False):
    rows = ob.load_queue()
    tab = tab if tab in dict(TABS) else ("review" if rows else "companies")
    h = header(rows, tab, msg)
    if tab == "companies":
        h += tab_companies()
    elif tab == "shortlist":
        h += tab_shortlist(auto)
    elif tab == "pocs":
        h += tab_pocs()
    else:
        h += tab_review(rows, view)
    h.append("</main>")
    return "".join(h)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ok_host(self):
        return allowed_host(self.headers.get("Host") or "", os.environ, self.server.server_address[1])

    def _send(self, code, body="", ctype="text/html; charset=utf-8", location=None, headers=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if location:
            self.send_header("Location", location)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, msg="", view="review", tab=None):
        t = f"&tab={tab}" if tab else ""
        self._send(303, location=f"/?view={view}{t}&msg={quote(msg)}")

    def do_GET(self):
        if not self._ok_host():
            return self._send(403, "forbidden")
        u = urlparse(self.path)
        if u.path != "/":
            return self._send(404, "not found")
        q = parse_qs(u.query)
        view = q.get("view", ["review"])[0]
        self._send(200, page(view if view in VIEWS else "review", q.get("msg", [""])[0], q.get("tab", [None])[0], "auto" in q))

    def do_POST(self):
        if not self._ok_host():
            return self._send(403, "forbidden")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/"):
            parts = parse_multipart(ctype, body)
            form = {k: [v[1].decode("utf-8", "replace")] for k, v in parts.items() if v[0] is None}
            upload = parts.get("csv")
        else:
            form, upload = parse_qs(body.decode("utf-8", "replace")), None
        if not secrets.compare_digest(form.get("token", [""])[0], TOKEN):
            return self._send(403, "bad token, reload the page")
        route = urlparse(self.path).path
        if route != "/upload" and JOB["running"]:
            return self._redirect("A job is running, wait for it to finish before editing.")
        if route == "/upload":
            return self._upload(upload)
        if route == "/companies/upload":
            return self._companies_upload(upload)
        if route == "/proof/upload":
            return self._proof_upload(upload)
        if route == "/poc/upload":
            return self._poc_upload(upload, form.get("verified", ["no"])[0] == "yes")
        if route == "/shortlist":
            return self._shortlist(form)
        if route == "/run":
            what = form.get("what", [""])[0]
            fns = {"send": lambda: (ob.cmd_send(), ob.cmd_followup()), "cycle": ob.cmd_cycle,
                   "dry": lambda: ob.cmd_cycle(dry=True)}
            if what in fns and run_job(what, fns[what]):
                return self._redirect(f"Started: {what}", "all")
            return self._redirect("Nothing started.")
        rows = ob.load_queue()
        if route == "/bulk":
            action, picked = form.get("action", [""])[0], [int(x) for x in form.get("rows", []) if x.isdigit()]
            if action == "approve_all":
                picked = [i + 1 for i, r in enumerate(rows) if status(r) == "review"]
                action = "approve"
            cmd = ("skip", picked) if action == "skip" else ("approve", picked)
            changes, errors = ob.apply_commands(rows, [cmd])
        elif route == "/line":
            row, text = form.get("row", ["0"])[0], form.get("text", [""])[0].replace("\n", " ")
            changes, errors = ob.apply_commands(rows, [("line", int(row) if row.isdigit() else 0, text)])
        else:
            return self._send(404, "not found")
        ob.save_queue(rows)
        self._redirect(f"{len(changes)} changed" + (f"; problems: {'; '.join(errors)}" if errors else ""))

    def _tmp_csv(self, upload):
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "wb") as f:
            f.write(upload[1])
        return path

    def _upload(self, upload):
        if not upload or not upload[1]:
            return self._redirect("No file received.")
        path = self._tmp_csv(upload)
        try:
            added, skipped = ob.draft(path)
        except Exception as e:
            return self._redirect(f"Could not read that CSV: {e}")
        finally:
            os.unlink(path)  # personal data: do not keep the upload
        why = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in skipped.items() if v)
        hint = ""
        if not added and skipped.get("no_email_or_name"):
            hint = " This looks like a company-only file (no emails). Use tab 1 Companies for that."
        self._redirect(f"Added {added} relevant rows to the queue" + (f" (skipped: {why})" if why else "") + "." + (hint or " Review and approve below."))

    def _companies_upload(self, upload):
        if not upload or not upload[1]:
            return self._redirect("No file received.", tab="companies")
        if not companies.airtable_ready():
            return self._redirect("Airtable is not connected (AIRTABLE_TOKEN / AIRTABLE_BASE_ID).", tab="companies")
        path = self._tmp_csv(upload)
        try:
            r = companies.import_apollo_companies(path)
        except Exception as e:
            return self._redirect(f"Could not match: {e}", tab="companies")
        finally:
            os.unlink(path)
        self._redirect(f"{r['apollo']} Apollo companies: matched {r['matched']} {r['methods']}, not in Airtable {r['unmatched']}. "
                       f"Now pick your shortlist.", tab="shortlist")

    def _proof_upload(self, upload):
        if not upload or not upload[1]:
            return self._redirect("No file received.", tab="pocs")
        path = self._tmp_csv(upload)
        try:
            rows = overlap.load_proof(path)
            if not rows:
                return self._redirect("No usable rows. Need columns client, industry, persona, deal, name_usable.", tab="pocs")
            proof_path().write_bytes(upload[1])
        finally:
            os.unlink(path)
        self._redirect(f"Saved {len(rows)} of your clients.", tab="pocs")

    def _poc_upload(self, upload, verified):
        if not upload or not upload[1]:
            return self._redirect("No file received.", tab="pocs")
        if not companies.airtable_ready():
            return self._redirect("Airtable is not connected, shortlist is needed.", tab="pocs")
        path = self._tmp_csv(upload)

        def job():
            try:
                comps = companies.fetch()
                companies.import_pocs(path, verified, comps, overlap.load_proof(proof_path()), cache_dir(), log=lambda *a: ob.log(*a))
            finally:
                os.unlink(path)
        if not run_job("add POCs", job):
            os.unlink(path)
            return self._redirect("A job is already running.", tab="pocs")
        self._redirect("Started: adding POCs and reading company websites (see the log below). Then open tab 4.", tab="pocs")

    def _shortlist(self, form):
        do = form.get("do", ["save"])[0]
        selected, shown = form.get("rows", []), [x for x in form.get("shown", [""])[0].split(",") if x]
        try:
            n = companies.save_shortlist(selected, shown) if do in ("save", "ics") else 0
            if do == "applied":
                n = companies.mark_applied(selected)
                return self._redirect(f"Marked {n} companies as applied.", tab="shortlist")
            if do == "ics":
                per = form.get("per", ["5"])[0]
                hh, _, mm = form.get("time", ["11:00"])[0].partition(":")
                comps = companies.fetch()
                blocks = companies.schedule(comps, int(per) if per.isdigit() else 5, int(hh) if hh.isdigit() else 11, int(mm) if mm.isdigit() else 0)
                if not blocks:
                    return self._redirect("Nothing to schedule: tick companies that have a job and are not applied yet.", tab="shortlist")
                return self._send(200, companies.make_ics(blocks), "text/calendar; charset=utf-8",
                                  headers={"Content-Disposition": "attachment; filename=apply-blocks.ics"})
        except Exception as e:
            return self._redirect(f"Airtable error: {e}", tab="shortlist")
        self._redirect(f"Shortlist saved ({n} changed).", tab="shortlist")


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
