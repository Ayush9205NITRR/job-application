#!/usr/bin/env python3
"""Build the HTML for one email (draft body) from a Founders row.

  python3 scripts/build_email_html.py <founder_row_id> [--which 1|2] > out/draft.html
  python3 scripts/build_email_html.py --text "Hi **Ayush**\n- a\n- b"          # for testing

Email_Body conventions (what /run-b writes):
  **bold** -> <b>; lines starting with "- " -> bullet list; blank line -> paragraph;
  {{RESUME_LINK}} -> link to RESUME_URL from .env (or "(CV attached)" if unset).
"""
import argparse
import html
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grist_client as G  # noqa: E402  (also loads .env)


def inline(s, resume_link):
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = s.replace("{{RESUME_LINK}}", f'<a href="{resume_link}">my CV</a>' if resume_link else "(CV attached)")
    return s


def md_to_html(text, resume_link=""):
    text = (text or "").replace("\r\n", "\n")
    out = []
    for b in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in b.split("\n") if l.strip()]
        if lines and all(l.strip().startswith("- ") for l in lines):
            items = "".join(f"<li>{inline(l.strip()[2:], resume_link)}</li>" for l in lines)
            out.append(f"<ul>{items}</ul>")
        else:
            out.append("<p>" + "<br>".join(inline(l, resume_link) for l in lines) + "</p>")
    return '<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5">' + "\n".join(out) + "</div>"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("row", nargs="?")
    ap.add_argument("--which", default="1")
    ap.add_argument("--text")
    a = ap.parse_args()
    resume = os.environ.get("RESUME_URL", "")
    if a.text is not None:
        print(md_to_html(a.text.replace("\\n", "\n"), resume))
        return
    rows = [r for r in G.fetch("Founders") if str(r["id"]) == str(a.row)]
    if not rows:
        sys.exit("row not found")
    f = rows[0]["fields"]
    print(md_to_html(f.get("Email_Body") if a.which == "1" else f.get("Email2_Body"), resume))


if __name__ == "__main__":
    main()
