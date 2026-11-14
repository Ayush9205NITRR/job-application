#!/usr/bin/env python3
"""Build the HTML for one email (draft body) from a Founders row, adding the tracked resume link / optional pixel.

  python3 scripts/build_email_html.py <founder_row_id> [--which 1|2] > out/draft.html
  python3 scripts/build_email_html.py --text "Hi **Ayush**\n- a\n- b" --track-id abc123     # for testing

Email_Body conventions (what Run B writes):
  **bold** -> <b>; lines starting with "- " -> bullet list; blank line -> paragraph; {{RESUME_LINK}} -> tracked link.
Env (.env): TRACK_BASE, RESUME_URL (fallback when TRACK_BASE unset), PIXEL_ENABLED=true|false (default false).
"""
import argparse
import html
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grist_client as G  # noqa: E402,F401  (loads .env)


def md_to_html(text, resume_link, pixel):
    text = (text or "").replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    out = []
    for b in blocks:
        lines = b.split("\n")
        if all(l.strip().startswith("- ") for l in lines if l.strip()):
            items = "".join(f"<li>{inline(l.strip()[2:], resume_link)}</li>" for l in lines if l.strip())
            out.append(f"<ul>{items}</ul>")
        else:
            out.append("<p>" + "<br>".join(inline(l, resume_link) for l in lines) + "</p>")
    body = "\n".join(out)
    if pixel:
        body += f'\n<img src="{pixel}" width="1" height="1" alt="" style="display:none">'
    return f'<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5">{body}</div>'


def inline(s, resume_link):
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = s.replace("{{RESUME_LINK}}", f'<a href="{resume_link}">my CV</a>' if resume_link else "(CV attached)")
    return s


def links(track_id):
    base = os.environ.get("TRACK_BASE", "").rstrip("/")
    resume = (f"{base}/r/{track_id}" if base and track_id else os.environ.get("RESUME_URL", ""))
    pixel = (f"{base}/p/{track_id}.gif" if base and track_id and os.environ.get("PIXEL_ENABLED", "false").lower() == "true" else "")
    return resume, pixel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("row", nargs="?")
    ap.add_argument("--which", default="1")
    ap.add_argument("--text")
    ap.add_argument("--track-id")
    a = ap.parse_args()
    if a.text is not None:
        resume, pixel = links(a.track_id)
        print(md_to_html(a.text.replace("\\n", "\n"), resume, pixel))
        return
    rows = [r for r in G.fetch("Founders") if str(r["id"]) == str(a.row)]
    if not rows:
        sys.exit("row not found")
    f = rows[0]["fields"]
    if not f.get("Track_ID"):
        sys.exit("Track_ID missing: set it first (grist_client.new_track_id())")
    resume, pixel = links(f["Track_ID"])
    body = f.get("Email_Body") if a.which == "1" else f.get("Email2_Body")
    print(md_to_html(body, resume, pixel))


if __name__ == "__main__":
    main()
