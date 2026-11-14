#!/usr/bin/env python3
"""Build the dashboard pages inside Grist itself: Review, Pipeline, Duplicates, Funnel, Data health.

  python3 scripts/make_pages.py            # create (or rebuild) the pages
  python3 scripts/make_pages.py --list     # just show what pages exist now

Grist already is the UI, so there is no separate web app: these are ordinary Grist pages, and you can
rearrange, filter or add to them by clicking once they exist. Re-running removes only the pages this
script made (by name) and builds them again, so your own pages are left alone.

Built with the document's user-action API:
  CreateViewSection(tableRef, viewRef, sectionType, groupbyColRefs, title)
    viewRef 0 starts a new page; groupbyColRefs None is a plain list, a list of column refs makes it a
    summary (and sectionType "chart" draws it).
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grist_client as G  # noqa: E402

PAGES = ["Review", "Pipeline", "Duplicates", "Funnel", "Data health"]

# Columns worth seeing on the Review page, in this order. Everything else is hidden.
REVIEW_FIELDS = ["Company_Name", "Name", "Designation", "Applied_Role", "Research_Hook",
                 "Email_Subject", "Email_Body", "WA_Link", "LI_Note", "Check_Before_Sending", "Approve"]
PIPELINE_FIELDS = ["Company_Name", "Name", "Designation", "Email", "Status", "Stage",
                   "Sent_1", "Reply", "Applied_Role", "Source_System"]


def meta(table):
    return G._call("GET", f"/docs/{G.DOC}/tables/{table}/records")["records"]


class Doc:
    """The bits of document metadata the page builder needs, read once."""

    def __init__(self):
        self.tables = {r["fields"]["tableId"]: r["id"] for r in meta("_grist_Tables")}
        self.cols = {}
        for r in meta("_grist_Tables_column"):
            self.cols[(r["fields"]["parentId"], r["fields"]["colId"])] = r["id"]
        self.views = {r["id"]: r["fields"].get("name") for r in meta("_grist_Views")}

    def col(self, table, colid):
        ref = self.cols.get((self.tables[table], colid))
        if ref is None:
            sys.exit(f"{table}.{colid} does not exist — run scripts/setup_grist.py first")
        return ref

    def apply(self, actions):
        return G._call("POST", f"/docs/{G.DOC}/apply", actions)["retValues"]


def section(doc, table, title, view=0, kind="record", groupby=None):
    """-> (viewRef, sectionRef). view=0 starts a new page."""
    ret = doc.apply([["CreateViewSection", doc.tables[table], view, kind, groupby, title]])[0]
    return ret["viewRef"], ret["sectionRef"]


def name_page(doc, view, name):
    doc.apply([["UpdateRecord", "_grist_Views", view, {"name": name}]])


def set_chart(doc, sec, chart_type="bar"):
    """Draw the section as a bar chart of one series.

    A summary table also carries a sum of every numeric column in the source (Mails_Sent,
    Founders_Count), and the chart plots each of them as its own series. Keep the group-by column and
    count, drop the rest.
    """
    doc.apply([["UpdateRecord", "_grist_Views_section", sec, {"chartType": chart_type}]])
    section_table = {r["id"]: r["fields"]["tableRef"] for r in meta("_grist_Views_section")}[sec]
    colid = {r["id"]: r["fields"]["colId"] for r in meta("_grist_Tables_column")
             if r["fields"]["parentId"] == section_table}
    drop = [["RemoveRecord", "_grist_Views_section_field", f["id"]]
            for f in meta("_grist_Views_section_field")
            if f["fields"].get("parentId") == sec
            and colid.get(f["fields"].get("colRef"), "") not in ("count",)
            and colid.get(f["fields"].get("colRef"), "").endswith(("_Sent", "_Count", "Mails_Sent"))]
    if drop:
        doc.apply(drop)


def set_filter(doc, sec, table, colid, included=None, excluded=None):
    """Show only some values of one column, the way the filter button does."""
    spec = {"included": included} if included is not None else {"excluded": excluded}
    doc.apply([["AddRecord", "_grist_Filters", None,
                {"viewSectionRef": sec, "colRef": doc.col(table, colid), "filter": json.dumps(spec)}]])


def show_only(doc, sec, table, colids):
    """Keep these columns visible on a section, in this order, and hide the rest."""
    want = {doc.col(table, c): i for i, c in enumerate(colids)}
    actions = []
    for f in meta("_grist_Views_section_field"):
        if f["fields"].get("parentId") != sec:
            continue
        ref = f["fields"].get("colRef")
        if ref in want:
            actions.append(["UpdateRecord", "_grist_Views_section_field", f["id"],
                            {"parentPos": float(want[ref] + 1)}])
        else:
            actions.append(["RemoveRecord", "_grist_Views_section_field", f["id"]])
    if actions:
        doc.apply(actions)


def drop_pages(doc, names):
    gone = [v for v, n in doc.views.items() if n in names]
    if gone:
        doc.apply([["RemoveView", v] for v in gone])
        print(f"  removed {len(gone)} existing page(s): {', '.join(sorted(names & set(doc.views.values())))}")
    return gone


def build(doc):
    # --- Review: the people waiting for a yes/no, one card each
    view, sec = section(doc, "Founders", "Ready to review", kind="detail")
    name_page(doc, view, "Review")
    set_filter(doc, sec, "Founders", "Status", included=["Ready", "Contact-ready"])
    set_filter(doc, sec, "Founders", "Is_Dup", included=[False])
    show_only(doc, sec, "Founders", REVIEW_FIELDS)
    print("  Review")

    # --- Pipeline: where everyone stands, with the counts above the list
    view, sec = section(doc, "Founders", "How many at each status",
                        kind="record", groupby=[doc.col("Founders", "Status")])
    name_page(doc, view, "Pipeline")
    _, listing = section(doc, "Founders", "Everyone", view=view, kind="record")
    show_only(doc, listing, "Founders", PIPELINE_FIELDS)
    print("  Pipeline")

    # --- Duplicates: what the Is_Dup formula caught
    view, sec = section(doc, "Founders", "Flagged as duplicates", kind="record")
    name_page(doc, view, "Duplicates")
    set_filter(doc, sec, "Founders", "Is_Dup", included=[True])
    print("  Duplicates")

    # --- Funnel: the charts
    view, sec = section(doc, "Founders", "By stage", kind="chart",
                        groupby=[doc.col("Founders", "Stage")])
    name_page(doc, view, "Funnel")
    set_chart(doc, sec)
    for title, table, colid in (("By status", "Founders", "Status"),
                                ("By designation", "Founders", "Designation"),
                                ("Companies by funding stage", "Companies", "Stage"),
                                ("Companies by source", "Companies", "Source_System")):
        _, s = section(doc, table, title, view=view, kind="chart", groupby=[doc.col(table, colid)])
        set_chart(doc, s)
    print("  Funnel")

    # --- Data health: what the import found, and whether the refresh is still running
    view, sec = section(doc, "Data_Issues", "Problems by sheet", kind="chart",
                        groupby=[doc.col("Data_Issues", "Sheet")])
    name_page(doc, view, "Data health")
    set_chart(doc, sec)
    section(doc, "Data_Issues", "Every problem", view=view, kind="record")
    section(doc, "Refresh_Runs", "Refresh runs", view=view, kind="record")
    print("  Data health")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="show the document's pages and exit")
    a = ap.parse_args()
    if not (G.KEY and G.DOC):
        sys.exit("Set GRIST_API_KEY and GRIST_DOC_ID in .env")

    doc = Doc()
    if a.list:
        for v, n in sorted(doc.views.items()):
            print(f"  {v:>3}  {n}")
        return
    for needed in ("Founders", "Companies", "Data_Issues", "Refresh_Runs"):
        if needed not in doc.tables:
            sys.exit(f"table {needed} is missing — run scripts/setup_grist.py first")

    drop_pages(doc, set(PAGES))
    build(doc)
    print(f"\nDone. {len(PAGES)} pages built. Grist API calls: {G.calls()}")


if __name__ == "__main__":
    main()
