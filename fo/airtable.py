"""Thin Airtable REST client. Records come back flat: {"_id": "rec...", **fields}."""
import time
from urllib.parse import quote

from .config import env
from .http import request

DRY_RUN = False
_API = "https://api.airtable.com/v0"


def _headers():
    return {"Authorization": f"Bearer {env('AIRTABLE_TOKEN')}", "Content-Type": "application/json"}


def _chunks(seq, n=10):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


class Table:
    def __init__(self, name: str):
        self.name = name

    @property
    def url(self):
        return f"{_API}/{env('AIRTABLE_BASE_ID')}/{quote(self.name)}"

    def all(self, formula: str | None = None) -> list[dict]:
        out, offset = [], None
        while True:
            params = {"pageSize": 100}
            if formula:
                params["filterByFormula"] = formula
            if offset:
                params["offset"] = offset
            data = request("GET", self.url, headers=_headers(), params=params).json()
            out += [{"_id": r["id"], **r["fields"]} for r in data["records"]]
            offset = data.get("offset")
            if not offset:
                return out
            time.sleep(0.22)  # 5 req/s limit

    def create(self, rows: list[dict]) -> list[dict]:
        if DRY_RUN:
            print(f"[dry-run] {self.name}: create {len(rows)}")
            return [{"_id": f"dry{i}", **r} for i, r in enumerate(rows)]
        out = []
        for chunk in _chunks(rows):
            body = {"records": [{"fields": r} for r in chunk], "typecast": True}
            data = request("POST", self.url, headers=_headers(), json=body).json()
            out += [{"_id": r["id"], **r["fields"]} for r in data["records"]]
            time.sleep(0.22)
        return out

    def update(self, updates: list[tuple[str, dict]]) -> None:
        if DRY_RUN:
            print(f"[dry-run] {self.name}: update {len(updates)}")
            return
        for chunk in _chunks(updates):
            body = {"records": [{"id": i, "fields": f} for i, f in chunk], "typecast": True}
            request("PATCH", self.url, headers=_headers(), json=body)
            time.sleep(0.22)

    def upsert(self, rows: list[dict], merge_on: list[str]) -> list[dict]:
        if DRY_RUN:
            print(f"[dry-run] {self.name}: upsert {len(rows)} on {merge_on}")
            return [{"_id": f"dry{i}", **r} for i, r in enumerate(rows)]
        out = []
        for chunk in _chunks(rows):
            body = {
                "performUpsert": {"fieldsToMergeOn": merge_on},
                "records": [{"fields": r} for r in chunk],
                "typecast": True,
            }
            data = request("PATCH", self.url, headers=_headers(), json=body).json()
            out += [{"_id": r["id"], **r["fields"]} for r in data["records"]]
            time.sleep(0.22)
        return out


Companies = Table("Companies")
Jobs = Table("Jobs")
Contacts = Table("Contacts")
Touches = Table("Touches")
Replies = Table("Replies")


def first_link(row: dict, field: str) -> str | None:
    v = row.get(field)
    return v[0] if v else None
