#!/usr/bin/env python3
"""UI server + import + reverse-map tests against an in-memory fake of Grist.  python3 tests/test_ui.py"""
import calendar
import datetime
import json
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "ui"))
import formulas as F  # noqa: E402
import grist_client as G  # noqa: E402

# ---------------------------------------------------------------- in-memory Grist
DB = {"Companies": [], "Founders": [], "Templates": [], "Outreach_Log": []}
_emailn = F.compile_formula(F.F_EMAIL_NORM, "Email")
_wan = F.compile_formula(F.F_WA_NORM, "WhatsApp")
_lin = F.compile_formula(F.F_LI_NORM, "LinkedIn")


def recompute():
    rows = DB["Founders"]
    for r in rows:
        f = r["fields"]
        f["Email_norm"], f["WA_norm"], f["LI_norm"] = _emailn(Email=f.get("Email")), _wan(WhatsApp=f.get("WhatsApp")), _lin(LinkedIn=f.get("LinkedIn"))
    for r in rows:
        f = r["fields"]
        f["Is_Dup"] = any(o["id"] < r["id"] and any(f.get(k) and o["fields"].get(k) == f[k] for k in ("Email_norm", "WA_norm", "LI_norm")) for o in rows)
        c = next((c for c in DB["Companies"] if c["id"] == f.get("Company")), None)
        f["Company_Name"] = c["fields"].get("Company") if c else ""


def fetch(table, filt=None, limit=None):
    return json.loads(json.dumps(DB[table]))


def add(table, rows, chunk=200):
    bad = G.FORMULA_COLS & {k for r in rows for k in r}
    assert not bad, bad
    out = []
    for r in rows:
        rec = {"id": len(DB[table]) + 1, "fields": dict(r)}
        DB[table].append(rec)
        out.append({"id": rec["id"]})
    if table == "Founders":
        recompute()
    return out


def update(table, rows, chunk=200):
    for u in rows:
        rec = next(r for r in DB[table] if r["id"] == u["id"])
        rec["fields"].update(u["fields"])
    if table == "Founders":
        recompute()


G.fetch, G.add, G.update = fetch, add, update

import server as S  # noqa: E402
import import_csv as I  # noqa: E402
import reverse_map as R  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")


# ---------------------------------------------------------------- reverse-map pure logic
now = 1_800_000_000
check("stage none", R.derive_stage(None, False, None, None, now), "Not sent")
check("stage sent", R.derive_stage(now - 60, False, None, None, now), "Sent")
check("stage delivered", R.derive_stage(now - 7200, False, None, None, now), "Delivered")
check("stage viewed", R.derive_stage(now - 7200, False, None, now - 100, now), "Viewed")
check("stage replied beats viewed", R.derive_stage(now - 7200, False, now - 50, now - 100, now), "Replied")
check("stage bounced", R.derive_stage(now - 7200, True, None, None, now), "Bounced")
check("status sent1", R.decide_status("Ready", [1], False, False), ("Sent-1", {}))
check("status sent2", R.decide_status("Sent-1", [1, 2], False, False), ("Sent-2", {}))
check("status replied", R.decide_status("Sent-1", [1], True, False), ("Replied", {"Reply": "Replied"}))
check("status bounced", R.decide_status("Drafted-1", [1], False, True), ("Bounced", {"Reply": "Bounced"}))
check("status skip untouched", R.decide_status("Skip", [1], False, False), ("Skip", {}))
check("when iso", R.parse_when("2026-11-14T10:00:00+05:30"), int(datetime.datetime(2026, 11, 14, 4, 30, tzinfo=datetime.timezone.utc).timestamp()))
check("when unix", R.parse_when("1790000000"), 1790000000)

tmp = ROOT / "tests" / "_views_tmp.csv"
tmp.write_text("Viewer Email,Viewed At\nA.One+x@Gmail.com,2026-11-14 10:00\nb@acme.com,\nnope,2026-11-14\n")
v = R.load_views([str(tmp)], now_ts=now)
tmp.unlink()
check("views mapped by normalised email", set(v), {"aone@gmail.com", "b@acme.com"})
check("views no time -> now", v["b@acme.com"], now)

# follow-up formulas
due1 = F.compile_formula(F.F_WA1_DUE, "Reply", "Status", "WA_Sent", "Sent_1", "WA_norm")
check("wa1 due thu+3->mon", due1(Reply=None, Status="Sent-1", WA_Sent=False, Sent_1=datetime.date(2026, 11, 12), WA_norm="919876543210"), datetime.date(2026, 11, 16))
check("wa1 stops on reply", due1(Reply="Replied", Status="Sent-1", WA_Sent=False, Sent_1=datetime.date(2026, 11, 12), WA_norm="9198"), None)
check("wa1 done", due1(Reply=None, Status="Sent-1", WA_Sent=True, Sent_1=datetime.date(2026, 11, 12), WA_norm="9198"), None)
due2 = F.compile_formula(F.F_WA2_DUE, "Reply", "Status", "WA_Sent", "WA2_Sent", "Sent_2", "WA_norm")
check("wa2 due", due2(Reply=None, Status="Sent-2", WA_Sent=True, WA2_Sent=False, Sent_2=datetime.date(2026, 11, 17), WA_norm="9198"), datetime.date(2026, 11, 20))
check("wa2 needs wa1", due2(Reply=None, Status="Sent-2", WA_Sent=False, WA2_Sent=False, Sent_2=datetime.date(2026, 11, 17), WA_norm="9198"), None)
e2 = F.compile_formula(F.F_SENT2_DUE, "Reply", "Status", "Sent_1")
check("email2 due", e2(Reply=None, Status="Sent-1", Sent_1=datetime.date(2026, 11, 12)), datetime.date(2026, 11, 17))
check("email2 stops on reply", e2(Reply="Replied", Status="Sent-1", Sent_1=datetime.date(2026, 11, 12)), None)

# ---------------------------------------------------------------- HTTP server
S.H._run = staticmethod(lambda job, saved=None: {"ok": True, "output": f"fake run {job} {saved or ''}"})
srv = S.ThreadingHTTPServer(("127.0.0.1", 0), S.H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def call(path, body=None, ui=True):
    h = {"Content-Type": "application/json"}
    if ui:
        h["X-UI"] = "1"
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=h, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers["Content-Type"].startswith("application/json") else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


st, html = call("/")
check("index served", st == 200 and b"Outreach control panel" in html, True)

CSV = ("Startup Name,Founder Name,Title,Work Email,Phone,LinkedIn URL,Industry,Funding Stage,Funding Date\n"
       "Acme Travel,A One,Co-Founder & CEO,A.One+x@gmail.com,+91 98765 43210,https://www.linkedin.com/in/aone/,Travel,Seed,12 Mar 2026\n"
       "Acme Travel,B Two,COO,b@acme.com,,https://in.linkedin.com/in/btwo,Travel,Seed,12 Mar 2026\n"
       "LearnCo,C Three,Founder,aone@gmail.com,098765 43210,,Edtech,Series A,2025-09-01\n"
       "LearnCo,D Four,VP Sales,,,,Edtech,Series A,2025-09-01\n")
st, p = call("/api/csv/preview", {"text": CSV, "filename": "list.csv"})
check("preview 200", st, 200)
check("preview people", p["stats"]["people"], 4)
check("preview dup", p["stats"]["duplicates_in_file"], 1)
check("preview mapping company", p["mapping"]["Company"], "Startup Name")
st, p2 = call("/api/csv/preview", {"text": CSV, "mapping": {"Company": "Industry"}})
check("mapping override", p2["mapping"]["Company"], "Industry")

st, r = call("/api/csv/import", {"text": CSV, "filename": "list.csv"})
check("import 200", st, 200)
check("import founders", r["founders_added"], 4)
check("import is_dup", r["is_dup_total"], 1)
check("companies created", len(DB["Companies"]), 2)

st, s = call("/api/summary")
check("summary unique", s["funnel"][0][1], 3)
check("summary dups", s["duplicates"], 1)
check("summary vertical key", "Travel / Seed" in s["by_vertical"], True)

st, fl = call("/api/founders")
check("founders list", len(fl["founders"]), 4)
fid = fl["founders"][0]["id"]
st, _ = call("/api/update", {"id": fid, "fields": {"Approve": True}})
check("update ok", st, 200)
check("approve stored", DB["Founders"][0]["fields"]["Approve"], True)
st, e = call("/api/update", {"id": fid, "fields": {"Email_norm": "x"}})
check("formula col blocked", st, 400)
st, e = call("/api/update", {"id": fid, "fields": {"Approve": True}}, ui=False)
check("no X-UI blocked", st, 403)
st, _ = call("/api/bulk", {"ids": [1, 2], "fields": {"Status": "Ready"}})
check("bulk", (st, DB["Founders"][1]["fields"]["Status"]), (200, "Ready"))

# follow-ups
today = calendar.timegm(datetime.date.today().timetuple())
DB["Founders"][1]["fields"].update({"Status": "Sent-1", "Sent2_Due": today - 86400, "Stage": "Delivered"})
st, fu = call("/api/followups")
check("followup email2", [x["id"] for x in fu["email2"]], [2])
DB["Founders"][1]["fields"]["Sent2_Due"] = today + 86400 * 3
st, fu = call("/api/followups")
check("followup not yet due", fu["email2"], [])

# reverse-map update builder
rows = [r for r in fetch("Founders") if not r["fields"].get("Is_Dup")]
ginfo = {1: {"sent_ts": now - 7200, "sent_all": [now - 7200], "thread": "t1", "replied_ts": None, "bounced": False},
         2: {"sent_ts": now - 7200, "sent_all": [now - 7200, now - 3600], "thread": "t2", "replied_ts": now - 100, "bounced": False}}
ups, stats = R.build_updates(rows, ginfo, {"aone@gmail.com": now - 500}, now)
byid = {u["id"]: u["fields"] for u in ups}
check("row1 stage viewed", byid[1]["Stage"], "Viewed")
check("row1 status", byid[1]["Status"], "Sent-1")
check("row2 replied", (byid[2]["Stage"], byid[2]["Status"], byid[2]["Reply"]), ("Replied", "Replied", "Replied"))
check("row2 sent_2 set", "Sent_2" in byid[2], True)

# views upload + run endpoint (subprocess faked)
st, v = call("/api/views/upload", {"text": "email,viewed_at\nb@acme.com,2026-11-14\n", "filename": "x.csv"})
check("views upload", (st, v["ok"]), (200, True))
for f in (ROOT / "imports").glob("views_*.csv"):
    f.unlink()
st, v = call("/api/run", {"job": "rm -rf"})
check("unknown job blocked", st, 400)
st, v = call("/api/run", {"job": "gmail_dry"})
check("run job", (st, v["ok"]), (200, True))

# ---------------------------------------------------------------- email preview
import os  # noqa: E402

os.environ.pop("RESUME_URL", None)
st, v = call("/api/preview_email", {"body": "Hi **Ayush**\n\n- 40 Cr GMV\n- 7 hires\n\nCV: {{RESUME_LINK}}",
                                    "subject": "Founder's Office"})
check("preview 200", st, 200)
check("preview subject", v["subject"], "Founder's Office")
check("preview bold", "<b>Ayush</b>" in v["html"], True)
check("preview bullets", "<ul><li>" in v["html"], True)
check("preview words", v["words"], 11)
check("preview not over", v["over_120_words"], False)
check("no resume url", "(CV attached)" in v["html"], True)
check("resume flag off", v["resume_url_set"], False)

os.environ["RESUME_URL"] = "https://example.com/cv"
st, v = call("/api/preview_email", {"body": "CV: {{RESUME_LINK}}"})
check("resume link used", '<a href="https://example.com/cv">my CV</a>' in v["html"], True)
check("resume flag on", v["resume_url_set"], True)
os.environ.pop("RESUME_URL", None)

# the preview is rendered in the page, so anything typed must come back escaped
st, v = call("/api/preview_email", {"body": "<script>alert(1)</script> & <b>x"})
check("preview escapes html", "<script>" in v["html"], False)
check("preview escapes ampersand", "&amp;" in v["html"], True)

st, v = call("/api/preview_email", {"body": " ".join(["word"] * 130)})
check("preview flags long email", (v["words"], v["over_120_words"]), (130, True))

# falls back to the stored row when only an id is given
DB["Founders"][0]["fields"]["Email_Body"] = "Stored **one**"
DB["Founders"][0]["fields"]["Email2_Body"] = "Stored **two**"
DB["Founders"][0]["fields"]["Email_Subject"] = "Stored subject"
st, v = call("/api/preview_email", {"id": DB["Founders"][0]["id"]})
check("preview by id", ("<b>one</b>" in v["html"], v["subject"]), (True, "Stored subject"))
st, v = call("/api/preview_email", {"id": DB["Founders"][0]["id"], "which": "2"})
check("preview email 2 by id", "<b>two</b>" in v["html"], True)

# unsaved drawer text wins over whatever is stored
st, v = call("/api/preview_email", {"id": DB["Founders"][0]["id"], "body": "Unsaved **edit**"})
check("preview prefers posted body", ("<b>edit</b>" in v["html"], "one" in v["html"]), (True, False))

st, v = call("/api/preview_email", {"id": 99999})
check("preview unknown id", st, 400)
st, v = call("/api/preview_email", {})
check("preview needs something", st, 400)
st, v = call("/api/preview_email", {"body": "x" * 20001})
check("preview rejects huge body", st, 400)
st, v = call("/api/preview_email", {"body": 42})
check("preview rejects non-text", st, 400)

st, v = call("/api/preview_email", {"body": "hi"}, ui=False)
check("preview needs X-UI", st, 403)

srv.shutdown()
if fails:
    print("FAILED:\n" + "\n".join(fails))
    sys.exit(1)
print("ui + reverse-map tests passed")
