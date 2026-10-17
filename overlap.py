"""Which of MY clients does a company ALSO work with? Read from the company's public website.

No AI key: it looks for my client names (and aliases) in their home / customers / case-studies / about pages.
Respects robots.txt, reads at most 4 pages per site, caches each site for 90 days, never fetches private addresses.
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


def parse_clients(text):
    """One client per line: `Name | alias | alias`. Lines starting with # are ignored. -> [{name, aliases}]"""
    out = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if parts and not parts[0].startswith("#"):
            out.append({"name": parts[0], "aliases": parts[1:]})
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


def _compact(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def match_clients(text, clients):
    """Names of MY clients found in `text`. A name matches as a whole word; names with a space also match
    without it ("Mongo DB" finds "MongoDB"). Short names (under 5 letters) only match as whole words."""
    low, flat, hits = (text or "").lower(), _compact(text or ""), []
    for c in clients:
        for term in [c["name"], *c.get("aliases", [])]:
            if _has(low, term) or (" " in term.strip() and len(_compact(term)) >= 5 and _compact(term) in flat):
                hits.append(c["name"])
                break
    return hits


def join_names(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def pitch_line(company, names):
    """Opening line when overlap exists. Claims only what Ayush supplied: that he has worked with these clients."""
    if not names:
        return ""
    them = names[0] if len(names) == 1 else "them"
    return f"I saw {company} works with {join_names(names)}. I have worked with {them} too."
