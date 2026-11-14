#!/usr/bin/env python3
"""Tests for the tracker import:  python3 tests/test_tracker.py

Needs the workbook at imports/Email_Outreach_Tracker.xlsx; without it the file-backed checks are
skipped and only the pure-logic ones run. Nothing here touches Grist.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import grist_client as G  # noqa: E402
import import_tracker as IT  # noqa: E402
import tracker_clean as TC  # noqa: E402

fails = []
WORKBOOK = ROOT / "imports" / "Email_Outreach_Tracker.xlsx"


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")


# ---------------------------------------------------------------- pure helpers
for raw, want in {
    "2026-10-28 15:50 IST": "2026-10-28 15:50",
    "2026-10-29 00:00:00": "2026-10-29 00:00",
    "Thu, 29 Jan 2026 13:00:00 +0530": "2026-01-29 13:00",
    46324: "2026-10-29 00:00",          # a raw Excel serial, as two Master cells hold
    "": None, None: None, "not a date": None,
}.items():
    check(f"parse_dt {raw!r}", TC.parse_dt(raw)[0], want)

check("excel serial is flagged", TC.parse_dt(46324)[1], "excel_serial")
check("junk date is flagged", TC.parse_dt("not a date")[1], "unparsed_date")

for raw, want in {"  Yash@Potpie.AI ": "yash@potpie.ai", "no email": "no email", "": ""}.items():
    check(f"norm_email {raw!r}", TC.norm_email(raw)[0], want)
check("bad address is flagged", TC.norm_email("no email")[1], "invalid_email")
check("blank address is not flagged", TC.norm_email("")[1], None)

check("clean_str collapses space", TC.clean_str("  a\n  b  "), "a b")
check("clean_str drops placeholders", TC.clean_str("N/A"), "")

# the three lead layouts
normal = {"recipient_email": "a@b.com", "recipient_name": "Ann", "company": "Acme", "role": "Ops"}
check("layout normal", TC._relayout_lead(dict(normal)), "normal")

no_mail = dict(normal, recipient_email="No email")
rec = dict(no_mail)
check("layout no_email", TC._relayout_lead(rec), "no_email")
check("no_email keeps the person", (rec["recipient_name"], rec["company"]), ("Ann", "Acme"))
check("no_email blanks the address", rec["recipient_email"], "")

# B: email=company, name=role, company=person, role=designation
rec = {"recipient_email": "creso technologies", "recipient_name": "Founders' Office - Growth",
       "company": "Pranay Agarwal", "role": "Co-Founder", "priority": "High"}
check("layout shifted_b", TC._relayout_lead(rec), "shifted_b")
check("shifted_b person", rec["recipient_name"], "Pranay Agarwal")
check("shifted_b company", rec["company"], "creso technologies")
check("shifted_b role", rec["role"], "Founders' Office - Growth")
check("shifted_b designation", rec["designation"], "Co-Founder")

# A: email=company, name=sector, company=role, role=person, campaign=designation, priority=linkedin
rec = {"recipient_email": "chai nagri", "recipient_name": "F&B / Retail Startup",
       "company": "Founder's Office Executive", "role": "Divam Wadhwa",
       "campaign": "Founder", "priority": "https://www.linkedin.com/in/divam"}
check("layout shifted_a", TC._relayout_lead(rec), "shifted_a")
check("shifted_a person", rec["recipient_name"], "Divam Wadhwa")
check("shifted_a company", rec["company"], "chai nagri")
check("shifted_a role", rec["role"], "Founder's Office Executive")
check("shifted_a designation", rec["designation"], "Founder")
check("shifted_a linkedin", rec["linkedin"], "https://www.linkedin.com/in/divam")
check("shifted_a sector", rec["sector"], "F&B / Retail Startup")

check("designation from bucket", IT.designation_of("Founding_Member"), "Founding Member")
check("designation from title", IT.designation_of("", "Head of Talent Acquisition"), "Talent Acquisition")
check("designation falls back", IT.designation_of("", ""), "Other")

# a value the user owns is never overwritten once it holds something
check("user field is protected",
      IT._changes({"Status": "Skip"}, {"Status": "Sent-1"}), {})
check("user field fills when empty",
      IT._changes({"Status": ""}, {"Status": "Sent-1"}), {"Status": "Sent-1"})
check("plain field is overwritten",
      IT._changes({"Email_Subject": "old"}, {"Email_Subject": "new"}), {"Email_Subject": "new"})
check("blank incoming is ignored",
      IT._changes({"Email_Subject": "old"}, {"Email_Subject": ""}), {})

# the formula guard: these are computed inside Grist and must never be written
for colname in ("Email_norm", "Is_Dup", "WA_Link", "WA1_Due", "WA2_Due"):
    check(f"{colname} is guarded", colname in G.FORMULA_COLS, True)

# ---------------------------------------------------------------- the real workbook
if WORKBOOK.exists():
    cleaned = TC.clean_workbook(WORKBOOK)
    s = TC.summarise(cleaned)

    check("row counts", s["rows"], {"leads": 88, "email_log": 139, "followup_drafts": 99,
                                    "companies": 78, "decision_makers": 69, "deleted": 43})
    check("attempted", s["attempted"], 99)
    check("delivered", s["delivered"], 92)
    check("replied", s["replied"], 6)
    check("bounced", s["bounced"], 7)
    check("queued", s["queued"], 40)
    check("attempted adds up", s["delivered"] + s["bounced"], s["attempted"])
    check("leads recovered from the crushed cell", s["recovered_leads"], 19)
    check("shifted leads", s["shifted_leads"], 13)
    check("leads with no address", s["leads_without_email"], 14)

    # every lead ends up with one of the three layouts, so none is silently dropped
    layouts = {r["_layout"] for r in cleaned["tables"]["leads"]}
    check("layouts seen", layouts, {"normal", "no_email", "shifted_a", "shifted_b"})

    # One log row is a real Gmail draft (it has a thread id) whose recipient, name and company cells
    # are all a stray backslash. There is no way to tell who it was for, so it is reported as an
    # invalid address and no person is created — which is why the people below are one short of the
    # 40 queued messages.
    nameless = [r for r in cleaned["tables"]["email_log"] if not IT.usable_email(r.get("recipient_email"))]
    check("log rows with no usable address", len(nameless), 1)
    check("the nameless row is a draft", nameless[0]["stage"], "DRAFT")
    check("and it is reported", any(i["kind"] == "invalid_email" for i in cleaned["issues"]), True)

    people = IT.build_people(cleaned)
    companies = IT.build_companies(cleaned)
    check("people merged", len(people), 333)
    check("companies incl. ones only named elsewhere", len(companies), 194)

    with_mail = [p for p in people.values() if p["fields"].get("Email")]
    check("unique addresses", len(with_mail), 249)
    addresses = [p["fields"]["Email"] for p in with_mail]
    check("no address appears twice", len(addresses), len(set(addresses)))

    # the 46 people written down in more than one sheet became ONE record each
    multi = [p for p in people.values() if len(p["sources"]) > 1]
    check("people found in more than one sheet", len(multi), 57)

    status = {}
    for p in people.values():
        status[p["fields"].get("Status")] = status.get(p["fields"].get("Status"), 0) + 1
    check("sent-1 count", status.get("Sent-1"), 86)
    check("replied count", status.get("Replied"), 6)
    check("bounced count", status.get("Bounced"), 7)
    check("drafted count", status.get("Drafted-1"), 39)   # 40 queued, minus the nameless draft above
    check("everyone has a status", sum(status.values()), len(people))

    for p in people.values():
        if G.FORMULA_COLS & set(p["fields"]):
            fails.append(f"import would write a formula column: {G.FORMULA_COLS & set(p['fields'])}")
            break

    # ---------------------------------------------------------- the tidy export round trip
    # A workbook we exported has to import exactly like the original, or dropping it into imports/
    # would quietly produce different numbers.
    import tempfile

    import export_clean as EC  # noqa: E402

    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "clean.xlsx"
        EC.build(cleaned, out, "test")
        again = TC.clean_workbook(out)

        check("round trip: row counts", TC.summarise(again)["rows"], s["rows"])
        for field in ("attempted", "delivered", "replied", "bounced", "queued",
                      "recovered_leads", "shifted_leads", "leads_without_email"):
            check(f"round trip: {field}", TC.summarise(again)[field], s[field])

        people2 = IT.build_people(again)
        check("round trip: people", len(people2), len(people))
        check("round trip: companies", len(IT.build_companies(again)), len(companies))
        addresses2 = sorted(p["fields"]["Email"] for p in people2.values() if p["fields"].get("Email"))
        check("round trip: same addresses", addresses2, sorted(addresses))
        status2 = {}
        for p in people2.values():
            status2[p["fields"].get("Status")] = status2.get(p["fields"].get("Status"), 0) + 1
        check("round trip: same statuses", status2, status)

        # a workbook that is neither layout explains itself instead of raising KeyError
        import openpyxl

        junk = pathlib.Path(tmp) / "junk.xlsx"
        wbj = openpyxl.Workbook()
        wbj.active.title = "Something else"
        wbj.save(junk)
        try:
            TC.clean_workbook(junk)
            fails.append("an unrecognised workbook should have been refused")
        except SystemExit as e:
            check("unknown workbook names its sheets", "Something else" in str(e), True)
            check("unknown workbook says what it wants", "Master" in str(e), True)
else:
    print(f"(skipped workbook checks: {WORKBOOK} not present)")

if fails:
    print("FAILED:\n" + "\n".join(fails))
    sys.exit(1)
print("tracker tests passed")
