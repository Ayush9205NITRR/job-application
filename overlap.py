"""Overlap between a company's customers (read from its public website) and Ayush's own client list.

No AI key needed: it looks for YOUR client names and industries in THEIR customers / case-studies / about text.
  proof.csv columns: client, industry, persona, deal, name_usable
  `deal` is written to continue the sentence "I ...", e.g. "ran their outbound pipeline and closed Rs 2.7 Cr".
  name_usable = y/n. When n, the client name is never put in copy, only the industry.
Respects robots.txt, reads at most 4 pages, caches each site for 90 days, never fetches private addresses.
"""
import csv
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

UA = "Mozilla/5.0 (compatible; OutboundResearch/1.0)"
PAGES = ["/", "/customers", "/case-studies", "/about"]
CACHE_DAYS = 90
MAX_BYTES = 300_000
MAX_WORDS = 30


def _nh(h):
    return re.sub(r"[^a-z0-9]", "", h.lower())


def load_proof(path):
    path = Path(path)
    if not path.is_file():
        return []
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            r = {_nh(k): (v or "").strip() for k, v in raw.items() if k}
            if r.get("client") or r.get("industry"):
                out.append({"client": r.get("client", ""), "industry": r.get("industry", ""), "persona": r.get("persona", ""),
                            "deal": r.get("deal", ""), "name_usable": r.get("nameusable", "").lower() in ("y", "yes", "true", "1")})
    return out


# ---- safe fetching ---------------------------------------------------------
def public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for i in infos:
        ip = ipaddress.ip_address(i[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return bool(infos)


def safe_url(url):
    u = urlparse(url)
    return u.scheme in ("http", "https") and bool(u.hostname) and public_host(u.hostname)


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_url(newurl):
            raise urllib.error.URLError("redirect to a non-public address blocked")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_fetch(url):
    """-> (status, text). status 0 = network problem or blocked address."""
    if not safe_url(url):
        return 0, ""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.build_opener(_Redirect).open(req, timeout=8) as r:
            return r.status, r.read(MAX_BYTES).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError, ValueError):
        return 0, ""


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        for k, v in attrs:  # logos on customer pages usually only have their name in alt / title
            if k in ("alt", "title") and v and not self.skip:
                self.parts.append(v)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(html):
    p = _Text()
    try:
        p.feed(html)
    except Exception:
        pass
    return re.sub(r"\s+", " ", " ".join(p.parts)).strip()


def domain(website):
    d = re.sub(r"^https?://", "", (website or "").strip().lower()).split("/")[0]
    return re.sub(r"^www\.", "", d)


def site_text(website, cache_dir, fetch=default_fetch, now=None):
    now = now or datetime.now(timezone.utc)
    d = domain(website)
    if not d:
        return ""
    cache_dir = Path(cache_dir)
    cache = cache_dir / (re.sub(r"[^a-z0-9.-]", "_", d) + ".json")
    if cache.is_file():
        try:
            c = json.loads(cache.read_text())
            if now - datetime.fromisoformat(c["at"]) < timedelta(days=CACHE_DAYS):
                return c["text"]
        except (ValueError, KeyError):
            pass
    base = "https://" + d
    status, robots = fetch(base + "/robots.txt")
    rp = robotparser.RobotFileParser()
    if status == 200:
        rp.parse(robots.splitlines())
    elif status in (404, 410):
        rp.parse([])
    else:
        return ""  # cannot confirm what is allowed, so read nothing (and do not cache the failure)
    texts = []
    for path in PAGES:
        if len(texts) >= 4:
            break
        if rp.can_fetch(UA, base + path):
            st, body = fetch(base + path)
            if st == 200 and body:
                texts.append(html_text(body))
    text = " ".join(texts)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"at": now.isoformat(), "text": text}))
    return text


# ---- matching ----------------------------------------------------------------
def _has(low, term):
    return bool(term) and re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", low) is not None


def valid_line(s):
    return bool(s) and len(s.split()) <= MAX_WORDS and "—" not in s and "–" not in s and "\n" not in s


def find_overlap(company, company_industry, text, proof):
    """-> {level: High|Medium|'', note, line}. High = one of YOUR clients is on THEIR site. Medium = same industry."""
    if not proof:
        return {"level": "", "note": "no client list uploaded", "line": ""}
    low = (text or "").lower()
    for p in proof:
        if _has(low, p["client"]):
            if p["name_usable"]:
                line = f"I saw {company} works with {p['client']}, where I {p['deal']}."
            else:
                ind = p["industry"] or "a similar"
                line = f"I saw {company} serves {ind} teams, and I {p['deal']} for one of them."
            return _result("High", "same client: " + (p["client"] if p["name_usable"] else f"{p['industry'] or 'private'} client"), line)
    for p in proof:
        if p["industry"] and (_has(low, p["industry"]) or _has((company_industry or "").lower(), p["industry"])):
            return _result("Medium", f"same industry: {p['industry']}", f"I saw {company} sells into {p['industry']}, a space where I {p['deal']}.")
    return {"level": "", "note": "no overlap found" if text else "site text not available", "line": ""}


def _result(level, note, line):
    if not valid_line(line):
        return {"level": level, "note": note + " (suggested line too long, write it by hand)", "line": ""}
    return {"level": level, "note": note, "line": line}
