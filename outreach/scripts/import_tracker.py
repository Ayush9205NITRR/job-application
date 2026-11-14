#!/usr/bin/env python3
"""Load a cleaned Email_Outreach_Tracker workbook into the Companies / Founders / Outreach_Log tables.

  python3 scripts/import_tracker.py --file imports/Email_Outreach_Tracker.xlsx --dry-run
  python3 scripts/import_tracker.py --file imports/Email_Outreach_Tracker.xlsx

Safe to re-run: everything is matched on a natural key and updated in place, so a second run adds
nothing. Your own edits in Grist survive — a column already holding a value is only overwritten when
the workbook has something newer to say, and Approve / Status / Notes are never clobbered once set.

Three of the workbook's tables describe the SAME people (leads, the email log, the follow-up drafts;
46 addresses appear in more than one). They are merged on the email address BEFORE anything is
written, so one person becomes one Founders row. Is_Dup is the safety net, not the plan.

Companies named anywhere but missing from the Companies sheet are created as bare rows, so that every
Founders row can still point at a company.
"""
import argparse
import pathlib
import re
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grist_client as G  # noqa: E402
import tracker_clean as TC  # noqa: E402

# tracker stage -> (Founders.Status, Founders.Stage)
STATUS_BY_STAGE = {
    "SENT_NO_REPLY": ("Sent-1", "Sent"),
    "SENT_REPLIED": ("Replied", "Replied"),
    "BOUNCED": ("Bounced", "Bounced"),
    "DRAFT": ("Drafted-1", "Not sent"),
    "SCHEDULED": ("Drafted-1", "Not sent"),
}
REPLY_BY_STAGE = {"SENT_REPLIED": "Replied", "BOUNCED": "Bounced"}

# Decision_Makers.bucket -> Founders.Designation (a free-text bucket onto a fixed choice list)
DESIGNATION = {
    "founder": "Founder", "co-founder": "Co-Founder", "cofounder": "Co-Founder",
    "founder / ceo": "Founder", "ceo": "CEO", "coo": "COO", "founder / coo": "COO",
    "coo / leadership": "COO", "founder / md": "Founder", "cmd / ceo": "CEO",
    "founding_member": "Founding Member", "founding member": "Founding Member",
    "leadership": "Leadership", "hr": "Talent Acquisition", "hiring": "Recruiter",
    "hiring / operations": "Recruiter", "job poster / director": "Recruiter",
    "founder's office": "Other",
}

# The workbook writes funding stage 18 different ways ("Seed / Bootstrapped", "Series A / YC W21",
# "Growth Stage"), which makes any chart of it unreadable. Tidy it to one value for charting and keep
# the original in Stage_Raw. The dropped half is already recorded: "YC W21" is in Investors, and
# "Venture Backed" / "Bootstrapped" say the same thing as the amount beside them.
STAGE_RULES = [
    ("pre-series a", "Pre-Series A"), ("pre-seed", "Pre-Seed"),
    ("seed", "Seed"), ("series a", "Series A"), ("series b", "Series B"),
    ("growth", "Growth"), ("early", "Early Stage"), ("bootstrapped", "Bootstrapped"),
    ("public", "Public"), ("funded", "Funded"), ("jv", "Funded"),
]


def normalise_stage(raw):
    """-> a value from the Companies.Stage choice list. Unrecognised text becomes Unknown."""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    head = s.split("/")[0].strip()
    for needle, out in STAGE_RULES:
        if head.startswith(needle):
            return out
    for needle, out in STAGE_RULES:
        if needle in s:
            return out
    return "Unknown"


# Fields the user owns. Once they hold a value in Grist, an import leaves them alone.
USER_OWNED = {"Approve", "Status", "Notes", "Check_Before_Sending", "Research_Hook",
              "WA_Sent", "WA2_Sent", "LI_Sent"}


def norm_company(s):
    return (s or "").strip().lower()


def designation_of(bucket, title=""):
    b = (bucket or "").strip().lower()
    if b in DESIGNATION:
        return DESIGNATION[b]
    t = (title or "").lower()
    for needle, out in (("co-found", "Co-Founder"), ("founder", "Founder"), ("ceo", "CEO"),
                        ("coo", "COO"), ("talent", "Talent Acquisition"), ("recruit", "Recruiter"),
                        ("hiring", "Hiring Manager")):
        if needle in t:
            return out
    return "Other"


# --------------------------------------------------------------------------- build rows to write
def build_companies(cleaned):
    """-> {normalised name: fields}. The Companies sheet first, then bare rows for names it misses."""
    out = {}
    for c in cleaned["tables"]["companies"]:
        name = (c.get("company_name") or "").strip()
        if not name:
            continue
        key = norm_company(name)
        fields = {"Company": name, "Source_System": "tracker"}
        for src, dest in (("website", "Website"), ("city", "City"), ("sector", "Sector"),
                          ("amount", "Round_Amount"),
                          ("investors", "Investors"), ("status", "Status")):
            if c.get(src):
                fields[dest] = c[src]
        if c.get("stage"):
            fields["Stage_Raw"] = c["stage"]
            fields["Stage"] = normalise_stage(c["stage"])
        if c.get("picked_at"):
            fields["Picked_At"] = G.ts_date(c["picked_at"][:10])
        out.setdefault(key, fields).update(fields)

    for table, field in (("leads", "company"), ("email_log", "company"), ("decision_makers", "company")):
        for row in cleaned["tables"][table]:
            name = (row.get(field) or "").strip()
            if name and norm_company(name) not in out:
                out[norm_company(name)] = {"Company": name, "Source_System": "tracker"}
    return out


def usable_email(v):
    """An address we are willing to key on and write. The log holds one row of pure punctuation."""
    s = (v or "").strip().lower()
    return s if TC.EMAIL_RE.match(s) else ""


def _person(people, email, name, company):
    """Find or start the one record for this person. Email first; otherwise name + company.

    A row with no address and no real name is not a person — the email log has one row whose every
    cell is a stray backslash. tracker_clean already reports it; here it is simply not imported.
    """
    email = usable_email(email)
    if email:
        key = email
    else:
        if len(re.sub(r"[^0-9a-z]", "", (name or "").lower())) < 2:
            return None
        key = f"~{name.strip().lower()}|{norm_company(company)}"
    rec = people.setdefault(key, {"_key": key, "fields": {}, "company": "", "sources": set()})
    if company and not rec["company"]:
        rec["company"] = company
    return rec


def _set(rec, field, value):
    """First non-empty value wins, so a later source cannot blank out an earlier one."""
    if value not in (None, "") and not rec["fields"].get(field):
        rec["fields"][field] = value


def build_people(cleaned):
    """Merge the three person-shaped tables plus decision makers into one record per person."""
    people = {}
    t = cleaned["tables"]

    for r in t["leads"]:
        rec = _person(people, r.get("recipient_email"), r.get("recipient_name"), r.get("company"))
        if rec is None:
            continue
        rec["sources"].add("lead")
        _set(rec, "Name", r.get("recipient_name"))
        _set(rec, "Email", usable_email(r.get("recipient_email")))
        _set(rec, "Applied_Role", r.get("role"))
        _set(rec, "LinkedIn", r.get("linkedin"))
        if r.get("designation"):
            _set(rec, "Designation", designation_of("", r["designation"]))
        if r.get("_layout") == "normal":
            _set(rec, "Email_Subject", r.get("original_subject"))
        flags = []
        if r.get("_recovered"):
            flags.append("recovered from the crushed cell in Master row 72")
        if str(r.get("_layout", "")).startswith("shifted"):
            flags.append(f"columns were in a different order ({r['_layout']}), re-read by layout")
        elif r.get("_layout") == "no_email":
            flags.append("Master had 'No email' instead of an address")
        if flags:
            _set(rec, "Parse_Flag", "; ".join(flags))

    for r in t["email_log"]:
        rec = _person(people, r.get("recipient_email"), r.get("recipient_name"), r.get("company"))
        if rec is None:
            continue
        rec["sources"].add("log")
        _set(rec, "Name", r.get("recipient_name"))
        _set(rec, "Email", usable_email(r.get("recipient_email")))
        _set(rec, "Email_Subject", r.get("subject"))
        _set(rec, "Thread_ID", r.get("thread_id"))
        stage = r.get("stage") or ""
        status, lifecycle = STATUS_BY_STAGE.get(stage, (None, None))
        if status:
            rec["fields"]["Status"] = status
            rec["fields"]["Stage"] = lifecycle
        if stage in REPLY_BY_STAGE:
            rec["fields"]["Reply"] = REPLY_BY_STAGE[stage]
        if r.get("sent_date") and stage in TC.ATTEMPTED_STAGES:
            ts = G.ts_date(r["sent_date"][:10])
            rec["fields"]["Sent_1"] = ts
            rec["fields"]["Last_Sent"] = ts
            rec["fields"]["Mails_Sent"] = 1
        if stage == "SCHEDULED" and r.get("scheduled_datetime"):
            _set(rec, "Planned_Send", G.ts_date(r["scheduled_datetime"][:10]))

    for r in t["followup_drafts"]:
        rec = _person(people, r.get("recipient_email"), r.get("recipient_name"), r.get("company"))
        if rec is None:
            continue
        rec["sources"].add("followup")
        _set(rec, "Name", r.get("recipient_name"))
        _set(rec, "Email", usable_email(r.get("recipient_email")))
        _set(rec, "Email_Subject", r.get("original_subject"))
        _set(rec, "Email2_Body", r.get("followup_2_body") or r.get("followup_1_body"))

    for r in t["decision_makers"]:
        rec = _person(people, "", r.get("person_name"), r.get("company"))
        if rec is None:
            continue
        rec["sources"].add("decision_maker")
        _set(rec, "Name", r.get("person_name"))
        _set(rec, "LinkedIn", r.get("person_linkedin_url"))
        _set(rec, "Designation", designation_of(r.get("bucket"), r.get("title")))
        _set(rec, "Source", r.get("source"))

    for rec in people.values():
        rec["fields"].setdefault("Source_System", "tracker")
        if not rec["fields"].get("Status"):
            rec["fields"]["Status"] = "Contact-ready" if rec["fields"].get("Email") else "New"
        # say "Not sent" rather than leaving it blank, so the funnel chart has no nameless bar
        rec["fields"].setdefault("Stage", "Not sent")
    return people


# --------------------------------------------------------------------------- write
def _changes(existing_fields, wanted, protect_user_fields=True):
    """Only the fields that would actually change, leaving the user's own edits alone."""
    out = {}
    for k, v in wanted.items():
        if v in (None, ""):
            continue
        current = existing_fields.get(k)
        if protect_user_fields and k in USER_OWNED and current not in (None, "", 0, False):
            continue
        if current != v:
            out[k] = v
    return out


def sync_companies(wanted, dry):
    rows = G.fetch("Companies")
    by_name = {norm_company(r["fields"].get("Company")): r for r in rows}
    add, update = [], []
    for key, fields in wanted.items():
        hit = by_name.get(key)
        if hit is None:
            add.append(fields)
        else:
            diff = _changes(hit["fields"], fields)
            if diff:
                update.append({"id": hit["id"], "fields": diff})
    if not dry:
        if add:
            G.add("Companies", add)
        if update:
            G.update("Companies", update)
        rows = G.fetch("Companies")
    ids = {norm_company(r["fields"].get("Company")): r["id"] for r in rows}
    return ids, len(add), len(update)


def sync_people(people, company_ids, dry):
    rows = G.fetch("Founders")
    by_email, by_name = {}, {}
    for r in rows:
        f = r["fields"]
        if f.get("Email_norm"):
            by_email[f["Email_norm"]] = r
        by_name[f"~{(f.get('Name') or '').lower()}|{norm_company(f.get('Company_Name'))}"] = r

    add, update = [], []
    for rec in people.values():
        fields = dict(rec["fields"])
        cid = company_ids.get(norm_company(rec["company"]))
        if cid:
            fields["Company"] = cid
        hit = by_email.get(rec["_key"]) or by_name.get(rec["_key"])
        if hit is None:
            add.append(fields)
        else:
            diff = _changes(hit["fields"], fields)
            if diff:
                update.append({"id": hit["id"], "fields": diff})
    if not dry:
        if add:
            G.add("Founders", add)
        if update:
            G.update("Founders", update)
    return len(add), len(update)


def sync_log(cleaned, dry):
    """One Outreach_Log row per message that actually went out, plus the deleted drafts."""
    existing = {(r["fields"].get("Gmail_Msg_Id"), r["fields"].get("Action")) for r in G.fetch("Outreach_Log")}
    founders = {r["fields"].get("Email_norm"): r["id"] for r in G.fetch("Founders") if r["fields"].get("Email_norm")}
    add = []
    for r in cleaned["tables"]["email_log"]:
        if r.get("stage") not in TC.ATTEMPTED_STAGES:
            continue
        mid = r.get("message_id") or f"{r.get('recipient_email')}|{r.get('sent_date')}"
        if (mid, "Sent") in existing:
            continue
        row = {"Action": "Sent", "Channel": "Email", "Gmail_Msg_Id": mid,
               "Note": (r.get("subject") or "")[:200]}
        if r.get("sent_date"):
            row["At"] = G.ts_date(r["sent_date"][:10])
        fid = founders.get(r.get("recipient_email"))
        if fid:
            row["Founder"] = fid
        add.append(row)
    for r in cleaned["tables"]["deleted"]:
        mid = f"deleted|{r.get('recipient')}|{r.get('subject', '')[:60]}"
        if (mid, "Skipped") in existing:
            continue
        row = {"Action": "Skipped", "Channel": "Email", "Gmail_Msg_Id": mid,
               "Note": (r.get("reason") or "")[:200]}
        if r.get("timestamp"):
            row["At"] = G.ts_date(r["timestamp"][:10])
        add.append(row)
    if add and not dry:
        G.add("Outreach_Log", add)
    return len(add)


def sync_issues(cleaned, dry):
    existing = {(r["fields"].get("Sheet"), r["fields"].get("Kind"), r["fields"].get("Detail"))
                for r in G.fetch("Data_Issues")}
    now = G.ts_now()
    add = [{"Sheet": i["sheet"], "Kind": i["kind"], "Detail": i["detail"][:300],
            "Severity": i["sev"], "Found_At": now}
           for i in cleaned["issues"]
           if (i["sheet"], i["kind"], i["detail"][:300]) not in existing]
    if add and not dry:
        G.add("Data_Issues", add)
    return len(add)


def run(path, dry=False, quiet=False):
    cleaned = TC.clean_workbook(path)
    summary = TC.summarise(cleaned)
    companies = build_companies(cleaned)
    people = build_people(cleaned)

    company_ids, c_add, c_upd = sync_companies(companies, dry)
    p_add, p_upd = sync_people(people, company_ids, dry)
    logged = sync_log(cleaned, dry)
    issues = sync_issues(cleaned, dry)

    result = {
        "companies_add": c_add, "companies_update": c_upd,
        "people_add": p_add, "people_update": p_upd,
        "log_add": logged, "issues_add": issues,
        "people_total": len(people), "companies_total": len(companies),
        "api_calls": G.calls(), **summary,
    }
    if not quiet:
        head = "would write" if dry else "wrote"
        print(f"  cleaned: " + ", ".join(f"{k} {v}" for k, v in summary["rows"].items()))
        print(f"  {summary['attempted']} emails attempted: {summary['delivered']} delivered "
              f"({summary['replied']} replied), {summary['bounced']} bounced, {summary['queued']} queued")
        print(f"  merged into {len(people)} people and {len(companies)} companies")
        print(f"  {head}: companies +{c_add} ~{c_upd} | people +{p_add} ~{p_upd} | "
              f"log +{logged} | issues +{issues}")
        print(f"  Grist API calls: {G.calls()}")
        srcs = Counter(tuple(sorted(r["sources"])) for r in people.values())
        for combo, n in srcs.most_common():
            print(f"    {n:>4} from {' + '.join(combo)}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    path = pathlib.Path(a.file)
    if not path.exists():
        sys.exit(f"no such file: {path}")
    if not (G.KEY and G.DOC):
        sys.exit("Set GRIST_API_KEY and GRIST_DOC_ID in .env")
    run(path, dry=a.dry_run)


if __name__ == "__main__":
    main()
