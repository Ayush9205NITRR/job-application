#!/usr/bin/env python3
"""Local web UI for outbound.py. stdlib only, binds to 127.0.0.1 (never exposed).

    export GMAIL_USER=you@gmail.com GMAIL_APP_PASSWORD=xxxx
    python ui.py            # then open http://127.0.0.1:8765

Flow: upload the Apollo CSV -> relevant rows are drafted into queue.csv automatically -> you review, edit the
opening line, approve -> "Send approved" / "Run cycle". Nothing is sent without approved=y, same as the CLI.
The uploaded CSV is deleted right after it is read.
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

import outbound as ob

TOKEN = secrets.token_urlsafe(16)  # CSRF: every POST must carry it, other websites cannot know it
JOB = {"name": "", "running": False, "log": []}
JOB_LOCK = threading.Lock()
VIEWS = ["review", "approved", "sent", "closed", "all"]


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
a.tab{padding:4px 10px;border-radius:6px;text-decoration:none;color:#1b1f24}a.tab.on{background:#1d4ed8;color:#fff}details{margin-top:4px}"""


def page(view="review", msg=""):
    rows = ob.load_queue()
    counts, today, cap, halted = stats(rows)
    running = JOB["running"]
    creds_ok = bool(os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD"))  # outbound loads .env at import
    h = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>Outbound</title>{'<meta http-equiv=refresh content=4>' if running else ''}<style>{CSS}</style><main>"
         f"<h1>Founder's Office outbound</h1><div class=row><span class=pill>sent today {today}/{cap}</span>"
         + "".join(f"<span class=pill>{k} {v}</span>" for k, v in counts.items()) + "</div>"]
    if msg:
        h.append(f"<div class='card ok'>{esc(msg)}</div>")
    if halted:
        h.append("<div class='card bad'><b>Sending is halted:</b> bounce rate is too high. Review rows marked bounced.</div>")
    if not creds_ok:
        h.append("<div class='card warn'>GMAIL_USER / GMAIL_APP_PASSWORD are not set (put them in .env or export them). "
                 "You can upload and review, but sending and inbox checks will fail until you set them and restart.</div>")
    h.append(f"<div class=card><form method=post action=/upload enctype=multipart/form-data class=row>"
             f"<input type=hidden name=token value={TOKEN}><b>1. Apollo CSV</b>"
             f"<input type=file name=csv accept=.csv required><button class=primary>Upload and draft relevant rows</button></form>"
             f"<small>Only verified emails and non-excluded companies (MICE, events, staffing, manpower) are added. "
             f"Duplicates are skipped. The file is deleted after reading.</small></div>")
    dis = " disabled" if running else ""
    h.append(f"<div class=card><div class=row><b>2. Send</b>"
             f"<form method=post action=/run><input type=hidden name=token value={TOKEN}>"
             f"<button name=what value=send{dis} class=primary>Send approved + follow-ups</button> "
             f"<button name=what value=cycle{dis}>Full cycle (inbox, send, follow-ups, digest)</button> "
             f"<button name=what value=dry{dis}>Dry run</button></form>"
             f"<small>Caps, gaps (2 to 5 min) and bounce stop apply. Unapproved rows are never sent.</small></div></div>")
    if JOB["log"] or running:
        h.append(f"<div class=card><b>{esc(JOB['name'])}</b> {'(running, page refreshes)' if running else '(finished)'}"
                 f"<pre>{esc(chr(10).join(JOB['log'][-60:]) or '...')}</pre></div>")
    h.append("<div class=card><div class=row><b>3. Review</b> " + "".join(
        f"<a class='tab{' on' if v == view else ''}' href='/?view={v}'>{v}</a>" for v in VIEWS) + "</div>")
    shown = [(i, r) for i, r in enumerate(rows) if view == "all" or status(r) == view]
    if view == "review" and shown and not running:
        h.append(f"<form id=bulk method=post action=/bulk class=row style='margin:8px 0'><input type=hidden name=token value={TOKEN}>"
                 f"<button name=action value=approve class=primary>Approve ticked</button>"
                 f"<button name=action value=approve_all>Approve all listed</button>"
                 f"<button name=action value=skip>Skip ticked</button></form>")
    h.append("<table><tr><th></th><th>#</th><th>Who</th><th>Opening line</th><th>State</th></tr>")
    for i, r in shown:
        n = i + 1
        tick = (f"<input type=checkbox name=rows value={n} form=bulk>" if view == "review" and not running else "")
        if status(r) in ("review", "approved") and not running:
            line = (f"<form method=post action=/line><input type=hidden name=token value={TOKEN}>"
                    f"<input type=hidden name=row value={n}><input type=text name=text value='{esc(r['line'])}'>"
                    f"<button>Save line</button></form>")
        else:
            line = esc(r["line"])
        h.append(f"<tr><td>{tick}</td><td>{n}</td><td><b>{esc(r['first'])}</b> @ {esc(r['company'])}<br>"
                 f"<small>{esc(r['email'])}<br>{esc(r['title'])}</small></td><td>{line}"
                 f"<details><summary>Email</summary><b>{esc(r['subject'])}</b><pre>{esc(r['body'])}</pre></details>"
                 f"{('<a href=' + chr(34) + esc(r['wa_link']) + chr(34) + ' target=_blank>WhatsApp link</a>') if r['wa_link'] and r['sent_at'] else ''}"
                 f"</td><td>{esc(state_label(r))}</td></tr>")
    if not shown:
        h.append("<tr><td colspan=5>Nothing here.</td></tr>")
    h.append("</table></div></main>")
    return "".join(h)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ok_host(self):
        return allowed_host(self.headers.get("Host") or "", os.environ, self.server.server_address[1])

    def _send(self, code, body="", ctype="text/html; charset=utf-8", location=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, msg="", view="review"):
        self._send(303, location=f"/?view={view}&msg={quote(msg)}")

    def do_GET(self):
        if not self._ok_host():
            return self._send(403, "forbidden")
        u = urlparse(self.path)
        if u.path != "/":
            return self._send(404, "not found")
        q = parse_qs(u.query)
        view = q.get("view", ["review"])[0]
        self._send(200, page(view if view in VIEWS else "review", q.get("msg", [""])[0]))

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

    def _upload(self, upload):
        if not upload or not upload[1]:
            return self._redirect("No file received.")
        fd, path = tempfile.mkstemp(suffix=".csv")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(upload[1])
            added, skipped = ob.draft(path)
        except Exception as e:
            return self._redirect(f"Could not read that CSV: {e}")
        finally:
            os.unlink(path)  # personal data: do not keep the upload
        why = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in skipped.items() if v)
        self._redirect(f"Added {added} relevant rows to the queue" + (f" (skipped: {why})" if why else "") + ". Review and approve below.")


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
