#!/usr/bin/env python3
"""Tiny Grist REST helper. Stdlib only. Reads GRIST_URL, GRIST_API_KEY, GRIST_DOC_ID from env (.env is loaded
automatically if python-dotenv-style KEY=VALUE lines exist in the repo root).

CLI:
  python3 scripts/grist_client.py fetch  Founders '{"Status":["Ready"],"Is_Dup":[false]}' [limit]
  python3 scripts/grist_client.py add    Founders '[{"Name":"A"}]'
  python3 scripts/grist_client.py update Founders '[{"id":12,"fields":{"Status":"Skip"}}]'

Rules:
  - fetch returns [{"id":..,"fields":{..}}], including formula columns (Email_norm, Is_Dup, ...).
  - NEVER write formula columns: Email_norm, WA_norm, LI_norm, Is_Dup, WA_Link, WA2_Link, Sent2_Due,
    Funded_24mo, Founders_Count, Company_Name. Grist rejects it.
  - Date / DateTime columns are unix seconds over the API. Helpers: ts_date("2026-11-20"), ts_now().
"""
import calendar
import datetime
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request


def load_env():
    p = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
URL = os.environ.get("GRIST_URL", "http://localhost:8484").rstrip("/")
KEY = os.environ.get("GRIST_API_KEY", "")
DOC = os.environ.get("GRIST_DOC_ID", "")

FORMULA_COLS = {"Email_norm", "WA_norm", "LI_norm", "Is_Dup", "WA_Link", "WA2_Link",
                "Sent2_Due", "Funded_24mo", "Founders_Count", "Company_Name",
                "WA1_Due", "WA2_Due"}

# Grist's free cloud plan allows 3000 API calls a month across the whole site, so every call counts.
# refresh.py records this in Refresh_Runs.Api_Calls and the tests assert a refresh stays small.
_CALLS = 0


def calls():
    """How many HTTP calls this process has made to Grist."""
    return _CALLS


def reset_calls():
    global _CALLS
    _CALLS = 0


def _call(method, path, body=None):
    global _CALLS
    _CALLS += 1
    req = urllib.request.Request(
        URL + "/api" + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} {method} {path}: {e.read().decode()}")


def fetch(table, filt=None, limit=None):
    q = []
    if filt:
        q.append("filter=" + urllib.parse.quote(json.dumps(filt)))
    if limit:
        q.append(f"limit={int(limit)}")
    path = f"/docs/{DOC}/tables/{table}/records" + ("?" + "&".join(q) if q else "")
    return _call("GET", path)["records"]


def add(table, rows, chunk=200):
    out = []
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        bad = FORMULA_COLS & {k for r in part for k in r}
        if bad:
            raise SystemExit(f"refusing to write formula columns: {sorted(bad)}")
        body = {"records": [{"fields": r} for r in part]}
        out += _call("POST", f"/docs/{DOC}/tables/{table}/records", body)["records"]
    return out


def update(table, rows, chunk=200):
    """PATCH rows, each {"id": .., "fields": {..}}.

    Grist rejects a PATCH whose records do not all carry the same field names, so rows are grouped by
    their set of fields and each group is sent on its own.
    """
    bad = FORMULA_COLS & {k for r in rows for k in r.get("fields", {})}
    if bad:
        raise SystemExit(f"refusing to write formula columns: {sorted(bad)}")
    groups = {}
    for r in rows:
        groups.setdefault(tuple(sorted(r.get("fields", {}))), []).append(r)
    for part in groups.values():
        for i in range(0, len(part), chunk):
            _call("PATCH", f"/docs/{DOC}/tables/{table}/records", {"records": part[i:i + chunk]})


def ts_date(s):
    return calendar.timegm(datetime.date.fromisoformat(s).timetuple())


def ts_now():
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp())


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, table = sys.argv[1], sys.argv[2]
    arg = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
    if cmd == "fetch":
        lim = sys.argv[4] if len(sys.argv) > 4 else None
        print(json.dumps(fetch(table, arg, lim), ensure_ascii=False, indent=1))
    elif cmd == "add":
        print(json.dumps(add(table, arg)))
    elif cmd == "update":
        update(table, arg)
        print("ok")
    else:
        sys.exit(__doc__)
