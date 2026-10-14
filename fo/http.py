import time

import requests


def request(method: str, url: str, *, retries: int = 4, **kw) -> requests.Response:
    """requests with exponential backoff on 429/5xx. Raises with the response body on 4xx."""
    kw.setdefault("timeout", 120)
    delay = 2
    for attempt in range(retries + 1):
        r = requests.request(method, url, **kw)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {url.split('?')[0]} -> {r.status_code}: {r.text[:500]}")
        return r
    raise RuntimeError("unreachable")
