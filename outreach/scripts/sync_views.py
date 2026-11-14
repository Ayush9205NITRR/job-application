#!/usr/bin/env python3
"""Pull open/click events from the tracking Worker and write counts into Grist.

Env (.env): TRACK_BASE=https://track.yourdomain.com   TRACK_TOKEN=<same secret as the Worker>
  python3 scripts/sync_views.py            # updates Views_Count, Last_Open, Resume_Clicks, Last_Click

Opens are DIRECTIONAL only (Gmail image proxy and Apple Mail pre-fetch inflate them). Resume clicks are the real signal.
Events from the first 120 seconds after Sent are not filtered here; treat a single open as noise.
"""
import json
import os
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grist_client as G  # noqa: E402


def events():
    base = os.environ["TRACK_BASE"].rstrip("/")
    req = urllib.request.Request(base + "/api/events?since=0",
                                 headers={"Authorization": "Bearer " + os.environ["TRACK_TOKEN"]})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["events"]


def main():
    ev = events()
    agg = {}
    for e in ev:
        a = agg.setdefault(e["fid"], {"open": 0, "click": 0, "last_open": 0, "last_click": 0})
        if e["type"] == "open":
            a["open"] += 1
            a["last_open"] = max(a["last_open"], e["ts"])
        elif e["type"] == "click":
            a["click"] += 1
            a["last_click"] = max(a["last_click"], e["ts"])
    rows = G.fetch("Founders")
    ups = []
    for r in rows:
        tid = r["fields"].get("Track_ID")
        if tid and tid in agg:
            a = agg[tid]
            f = {"Views_Count": a["open"], "Resume_Clicks": a["click"]}
            if a["last_open"]:
                f["Last_Open"] = a["last_open"]
            if a["last_click"]:
                f["Last_Click"] = a["last_click"]
            ups.append({"id": r["id"], "fields": f})
    if ups:
        G.update("Founders", ups)
    print(f"events: {len(ev)}  founders updated: {len(ups)}  unmatched track ids: {len(set(agg) - {r['fields'].get('Track_ID') for r in rows})}")


if __name__ == "__main__":
    main()
