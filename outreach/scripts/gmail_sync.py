#!/usr/bin/env python3
"""Read-only Gmail sync into Grist: who has already been emailed, who replied, who bounced.

Uses the Gmail API with the READ-ONLY scope, so this script can never send or modify mail.

Setup (one time, free):
  pip install -r requirements.txt
  Google Cloud Console > new project > enable Gmail API > OAuth consent (External, add yourself as test user)
  > Credentials > OAuth client ID > Desktop app > download as credentials.json into the repo root.
  First run opens a browser to authorise; token.json is saved locally (gitignored).

Usage:
  python3 scripts/gmail_sync.py --dry-run               # report only, no writes to Grist
  python3 scripts/gmail_sync.py --apply                  # write Mails_Sent / Last_Sent / Status / Reply
  python3 scripts/gmail_sync.py --gate someone@x.com     # dedupe gate for one address (sent + drafts)

Status rules (only rows with Is_Dup = false):
  reply found            -> Status Replied, Reply Replied
  bounce found           -> Status Bounced, Reply Bounced
  sent >= 1 and Status in New/Contact-ready/Ready/Drafted-1  -> Sent-1 (Sent_1 = first sent date)
  sent >= 2 and Status in New/Contact-ready/Ready/Drafted-1/Sent-1/Drafted-2 -> Sent-2 (Sent_2 = 2nd sent date)
"""
import argparse
import datetime
import json
import os
import pathlib
import sys
import time
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import grist_client as G  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
PRE_SENT = {"", "New", "Contact-ready", "Ready", "Drafted-1"}
PRE_SENT2 = PRE_SENT | {"Sent-1", "Drafted-2"}


def gmail_service():
    """Read-only Gmail.

    On a laptop this opens a browser once and remembers the result in token.json.
    On a server there is no browser, so GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN
    are used directly when all three are set. See the README for getting a refresh token.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    cid = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    refresh = os.environ.get("GOOGLE_REFRESH_TOKEN", "").strip()
    if cid and secret and refresh:
        creds = Credentials(None, refresh_token=refresh, client_id=cid, client_secret=secret,
                            token_uri="https://oauth2.googleapis.com/token", scopes=SCOPES)
        creds.refresh(Request())
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    from google_auth_oauthlib.flow import InstalledAppFlow

    tok = ROOT / "token.json"
    creds = Credentials.from_authorized_user_file(str(tok), SCOPES) if tok.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            cred_file = ROOT / "credentials.json"
            if not cred_file.exists():
                sys.exit("No Gmail credentials. Either put credentials.json in the repo root for the "
                         "browser sign-in, or set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / "
                         "GOOGLE_REFRESH_TOKEN (see the README).")
            flow = InstalledAppFlow.from_client_secrets_file(str(cred_file), SCOPES)
            creds = flow.run_local_server(port=0)
        tok.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def search(svc, q, cap=100):
    ids, token = [], None
    while True:
        resp = svc.users().messages().list(userId="me", q=q, maxResults=min(cap, 100), pageToken=token).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token or len(ids) >= cap:
            return ids[:cap]


def sent_dates(svc, ids):
    out = []
    for i in ids:
        m = svc.users().messages().get(userId="me", id=i, format="minimal").execute()
        out.append(datetime.datetime.fromtimestamp(int(m["internalDate"]) / 1000, datetime.timezone.utc).date())
    return sorted(out)


def addrs_for(fields):
    a = {(fields.get("Email") or "").strip().lower(), (fields.get("Email_norm") or "").strip().lower()}
    return sorted(x for x in a if "@" in x)


def q_any(prefix, addrs, extra=""):
    return f"{extra} (" + " OR ".join(f"{prefix}:{a}" for a in addrs) + ")"


def gate(svc, email):
    a = [email.lower()]
    return {
        "sent": len(search(svc, q_any("to", a, "in:sent"))),
        "drafts": len(search(svc, q_any("to", a, "in:drafts"))),
        "anywhere_from_me": len(search(svc, q_any("to", a, "in:anywhere from:me"))),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--gate")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    svc = gmail_service()

    if args.gate:
        print(json.dumps(gate(svc, args.gate)))
        return
    if not (args.apply or args.dry_run):
        sys.exit("choose --dry-run or --apply")

    rows = [r for r in G.fetch("Founders") if not r["fields"].get("Is_Dup") and addrs_for(r["fields"])]
    if args.limit:
        rows = rows[: args.limit]
    stats, updates = Counter(), []
    for n, r in enumerate(rows, 1):
        f, a = r["fields"], addrs_for(r["fields"])
        status = f.get("Status") or ""
        sent = sent_dates(svc, search(svc, q_any("to", a, "in:sent")))
        replied = bool(search(svc, q_any("from", a, "-in:sent"), cap=1))
        bounced = bool(search(svc, 'from:(mailer-daemon OR postmaster) (' + " OR ".join(f'"{x}"' for x in a) + ")", cap=1))
        upd = {"Mails_Sent": len(sent)}
        if sent:
            upd["Last_Sent"] = G.ts_date(sent[-1].isoformat())
        new_status = status
        if replied:
            new_status, upd["Reply"] = "Replied", "Replied"
        elif bounced:
            new_status, upd["Reply"] = "Bounced", "Bounced"
        elif len(sent) >= 2 and status in PRE_SENT2:
            new_status = "Sent-2"
            upd["Sent_1"], upd["Sent_2"] = G.ts_date(sent[0].isoformat()), G.ts_date(sent[1].isoformat())
        elif len(sent) >= 1 and status in PRE_SENT:
            new_status = "Sent-1"
            upd["Sent_1"] = G.ts_date(sent[0].isoformat())
        if new_status != status:
            upd["Status"] = new_status
        stats["checked"] += 1
        stats["already_emailed"] += bool(sent)
        stats["replied"] += replied
        stats["bounced"] += bounced
        updates.append({"id": r["id"], "fields": upd})
        if n % 25 == 0:
            print(f"  ...{n}/{len(rows)}", file=sys.stderr)
        time.sleep(0.05)

    print(json.dumps(dict(stats), indent=1))
    if args.apply:
        G.update("Founders", updates)
        print(f"wrote {len(updates)} rows to Grist")
    else:
        print("(dry run, nothing written)")


if __name__ == "__main__":
    main()
