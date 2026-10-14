"""Gmail via API. Creates DRAFTS and reads threads. This module never calls send."""
import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from .config import ROOT, env

# compose: create drafts. readonly: read threads for sent/reply detection.
SCOPES = ["https://www.googleapis.com/auth/gmail.compose", "https://www.googleapis.com/auth/gmail.readonly"]
_svc = None


def service():
    global _svc
    if _svc:
        return _svc
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token = ROOT / env("GMAIL_TOKEN", "token.json", required=False)
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            cred_file = ROOT / env("GMAIL_CREDENTIALS", "credentials.json", required=False)
            creds = InstalledAppFlow.from_client_secrets_file(str(cred_file), SCOPES).run_local_server(port=0)
        Path(token).write_text(creds.to_json())
    _svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return _svc


def my_address() -> str:
    return service().users().getProfile(userId="me").execute()["emailAddress"].lower()


def create_draft(to: str, subject: str, body: str, thread_id: str | None = None,
                 in_reply_to: str | None = None) -> tuple[str, str]:
    """Plain-text draft. Returns (draft_id, thread_id)."""
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    payload = {"message": {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}}
    if thread_id:
        payload["message"]["threadId"] = thread_id
    d = service().users().drafts().create(userId="me", body=payload).execute()
    return d["id"], d["message"]["threadId"]


def _header(msg: dict, name: str) -> str:
    for h in msg["payload"].get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _text(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        t = _text(part)
        if t:
            return t
    return ""


def thread_messages(thread_id: str) -> list[dict] | None:
    """Messages oldest first, drafts excluded. None if the thread no longer exists."""
    from googleapiclient.errors import HttpError

    try:
        t = service().users().threads().get(userId="me", id=thread_id, format="full").execute()
    except HttpError as e:
        if e.resp.status == 404:
            return None
        raise
    out = []
    for m in t.get("messages", []):
        labels = set(m.get("labelIds", []))
        if "DRAFT" in labels:
            continue
        out.append({
            "id": m["id"],
            "from": _header(m, "From").lower(),
            "message_id": _header(m, "Message-ID") or _header(m, "Message-Id"),
            "sent": "SENT" in labels,
            "at": datetime.fromtimestamp(int(m["internalDate"]) / 1000, tz=timezone.utc),
            "body": _text(m["payload"]),
        })
    return sorted(out, key=lambda m: m["at"])


def is_bounce(msg: dict) -> bool:
    return "mailer-daemon" in msg["from"] or "postmaster" in msg["from"]
