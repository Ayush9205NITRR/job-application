#!/usr/bin/env python3
"""Local control panel for the outreach system. Stdlib only. Talks to Grist through scripts/grist_client.py.

  python3 ui/server.py            # then open http://localhost:8585
  PORT=9000 python3 ui/server.py

Binds to 127.0.0.1 only. Every POST needs the header X-UI: 1 (sent by the page itself).
The UI never sends email. Drafts are created by /run-c in Claude Code; sending is you, in Gmail.
"""
import calendar
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_email_html as B  # noqa: E402
import grist_client as G  # noqa: E402
import import_csv as I  # noqa: E402

PORT = int(os.environ.get("PORT", "8585"))
INDEX = (ROOT / "ui" / "index.html")

EDITABLE = {
    "Approve", "Status", "Reply", "Notes", "Name", "Email", "WhatsApp", "LinkedIn", "Designation",
    "Email_Subject", "Email_Body", "Email2_Body", "WA_Msg", "WA2_Msg", "LI_Note",
    "WA_Sent", "WA2_Sent", "LI_Sent",
}
LIST_FIELDS = [
    "Name", "Company_Name", "Designation", "Email", "WhatsApp", "LinkedIn", "Status", "Stage", "Approve", "Is_Dup",
    "Mails_Sent", "Sent_At", "Viewed_At", "Replied_At", "Reply", "Email_Subject", "Email_Body", "Email2_Body",
    "WA_Msg", "WA_Link", "WA2_Msg", "WA2_Link", "LI_Note", "Check_Before_Sending", "Research_Hook", "Notes",
    "Sent2_Due", "WA1_Due", "WA2_Due", "WA_Sent", "WA2_Sent", "LI_Sent", "Sent_1", "Sent_2", "Planned_Send",
]
JOBS = {
    "gmail_dry": ["python3", "scripts/reverse_map.py", "--dry-run"],
    "gmail_apply": ["python3", "scripts/reverse_map.py", "--apply"],
    "recompute": ["python3", "scripts/reverse_map.py", "--apply", "--skip-gmail"],
}
SENT_STAGES = {"Sent", "Delivered", "Viewed", "Replied", "Bounced"}


def today_ts():
    return calendar.timegm(datetime.date.today().timetuple())


# ---------------------------------------------------------------- pure helpers (tested)
def summarize(founders, companies, today=None):
    today = today if today is not None else today_ts()
    comp = {c["id"]: c["fields"] for c in companies}
    uniq = [r["fields"] | {"id": r["id"]} for r in founders if not r["fields"].get("Is_Dup")]
    stage_of = lambda f: f.get("Stage") or "Not sent"  # noqa: E731
    stages = Counter(stage_of(f) for f in uniq)
    statuses = Counter((f.get("Status") or "New") for f in uniq)
    funnel = [
        ("Unique people", len(uniq)),
        ("Contactable (email or WhatsApp)", sum(1 for f in uniq if f.get("Email_norm") or f.get("WA_norm"))),
        ("Ready for review", statuses.get("Ready", 0)),
        ("Approved", sum(1 for f in uniq if f.get("Approve"))),
        ("Drafted", statuses.get("Drafted-1", 0) + statuses.get("Drafted-2", 0)),
        ("Sent", sum(1 for f in uniq if stage_of(f) in SENT_STAGES)),
        ("Delivered (inferred)", sum(1 for f in uniq if stage_of(f) in {"Delivered", "Viewed", "Replied"})),
        ("Viewed (needs views CSV)", sum(1 for f in uniq if f.get("Viewed_At"))),
        ("Replied", stages.get("Replied", 0)),
        ("Bounced", stages.get("Bounced", 0)),
    ]
    by_vertical = {}
    for f in uniq:
        c = comp.get(f.get("Company")) or {}
        key = f"{c.get('Vertical') or '?'} / {c.get('Stage') or '?'}"
        d = by_vertical.setdefault(key, {"people": 0, "sent": 0, "replied": 0})
        d["people"] += 1
        d["sent"] += stage_of(f) in SENT_STAGES
        d["replied"] += stage_of(f) == "Replied"
    due = lambda k: sum(1 for f in uniq if f.get(k) and f[k] <= today)  # noqa: E731
    return {
        "total_rows": len(founders), "duplicates": len(founders) - len(uniq),
        "funnel": funnel, "by_status": dict(statuses), "by_stage": dict(stages), "by_vertical": by_vertical,
        "due": {"email2": due("Sent2_Due"), "wa1": due("WA1_Due"), "wa2": due("WA2_Due")},
    }


def followups(founders, today=None):
    today = today if today is not None else today_ts()
    out = {"email2": [], "wa1": [], "wa2": []}
    for r in founders:
        f = r["fields"]
        if f.get("Is_Dup"):
            continue
        row = {"id": r["id"], **{k: f.get(k) for k in LIST_FIELDS}}
        for key, fld in (("email2", "Sent2_Due"), ("wa1", "WA1_Due"), ("wa2", "WA2_Due")):
            if f.get(fld) and f[fld] <= today:
                out[key].append(row)
    for k in out:
        out[k].sort(key=lambda x: x.get({"email2": "Sent2_Due", "wa1": "WA1_Due", "wa2": "WA2_Due"}[k]) or 0)
    return out


def clean_update(fields):
    bad = set(fields) - EDITABLE
    if bad:
        raise ValueError(f"not editable: {sorted(bad)}")
    return fields


MAX_BODY = 20000


def preview_email(b):
    """Render an email body the way the Gmail draft will look.

    Takes the text as it stands in the drawer, unsaved, so formatting can be checked before
    approving; falls back to the stored row when only an id is given. Nothing is written, and no
    mail is touched — this only runs the same markdown-to-HTML step build_email_html.py uses.
    """
    which = "2" if str(b.get("which") or "1") == "2" else "1"
    body, subject = b.get("body"), b.get("subject") or ""
    if body is None:
        if b.get("id") is None:
            raise ValueError("give either body text or an id")
        rows = [r for r in G.fetch("Founders") if r["id"] == int(b["id"])]
        if not rows:
            raise ValueError(f"no person with id {b['id']}")
        f = rows[0]["fields"]
        body = f.get("Email2_Body") if which == "2" else f.get("Email_Body")
        subject = subject or f.get("Email_Subject") or ""
    body = body or ""
    if not isinstance(body, str) or not isinstance(subject, str):
        raise ValueError("subject and body must be text")
    if len(body) > MAX_BODY:
        raise ValueError(f"body is {len(body)} characters; the limit is {MAX_BODY}")
    resume = os.environ.get("RESUME_URL", "")
    words = len([w for w in body.split() if w.strip()])
    return {"which": which, "subject": subject, "html": B.md_to_html(body, resume),
            "words": words, "over_120_words": words > 120, "resume_url_set": bool(resume)}


# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        try:
            path = self.path.split("?")[0]
            if path == "/":
                return self._send(200, INDEX.read_bytes(), "text/html")
            if path == "/api/summary":
                return self._send(200, summarize(G.fetch("Founders"), G.fetch("Companies")))
            if path == "/api/founders":
                rows = G.fetch("Founders")
                return self._send(200, {"founders": [{"id": r["id"], **{k: r["fields"].get(k) for k in LIST_FIELDS}} for r in rows]})
            if path == "/api/followups":
                return self._send(200, followups(G.fetch("Founders")))
            if path == "/api/health":
                return self._send(200, {"ok": True, "grist": G.URL, "doc": bool(G.DOC)})
            return self._send(404, {"error": "not found"})
        except SystemExit as e:  # grist_client raises SystemExit on HTTP errors
            return self._send(502, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": repr(e)})

    def do_POST(self):
        if self.headers.get("X-UI") != "1":
            return self._send(403, {"error": "missing X-UI header"})
        try:
            path, b = self.path, self._body()
            if path == "/api/update":
                G.update("Founders", [{"id": int(b["id"]), "fields": clean_update(b["fields"])}])
                return self._send(200, {"ok": True})
            if path == "/api/bulk":
                f = clean_update(b["fields"])
                G.update("Founders", [{"id": int(i), "fields": f} for i in b["ids"]])
                return self._send(200, {"ok": True, "updated": len(b["ids"])})
            if path in ("/api/csv/preview", "/api/csv/import"):
                rows = I.read_rows_text(b["text"])
                if not rows:
                    return self._send(400, {"error": "empty CSV"})
                headers = list(rows[0].keys())
                mapping = I.detect_mapping(headers, b.get("mapping") or None)
                if path.endswith("preview"):
                    return self._send(200, {"headers": headers, "mapping": mapping, "fields": list(I.ALIASES),
                                            "stats": I.analyse(rows, mapping)})
                res = I.write_import(rows, mapping, "csv:" + re.sub(r"[^\w.\-]", "_", b.get("filename") or "upload.csv"))
                return self._send(200, res)
            if path == "/api/views/upload":
                name = "views_" + time.strftime("%Y%m%d_%H%M%S") + ".csv"
                (ROOT / "imports").mkdir(exist_ok=True)
                (ROOT / "imports" / name).write_text(b["text"], encoding="utf-8")
                return self._send(200, self._run("recompute", name))
            if path == "/api/preview_email":
                return self._send(200, preview_email(b))
            if path == "/api/run":
                if b.get("job") not in JOBS:
                    return self._send(400, {"error": "unknown job"})
                return self._send(200, self._run(b["job"]))
            return self._send(404, {"error": "not found"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except SystemExit as e:
            return self._send(502, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": repr(e)})

    @staticmethod
    def _run(job, saved=None):
        p = subprocess.run(JOBS[job], cwd=ROOT, capture_output=True, text=True, timeout=900)
        out = (p.stdout + ("\n" + p.stderr if p.stderr else "")).strip()
        if saved:
            out = f"saved imports/{saved}\n" + out
        return {"ok": p.returncode == 0, "output": out[-6000:]}


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Control panel: http://localhost:{PORT}   (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
