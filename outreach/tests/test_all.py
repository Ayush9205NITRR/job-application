#!/usr/bin/env python3
"""Plain-python tests (no pytest needed):  python3 tests/test_all.py"""
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import formulas as F  # noqa: E402
import import_csv as I  # noqa: E402
import build_email_html as B  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")


email = F.compile_formula(F.F_EMAIL_NORM, "Email")
for raw, want in {"  Ayush.T+jobs@Gmail.com ": "ayusht@gmail.com", "Foo@Startup.IO": "foo@startup.io",
                  "bad": "", None: "", "a.b@company.com": "a.b@company.com"}.items():
    check(f"email {raw!r}", email(Email=raw), want)

wa = F.compile_formula(F.F_WA_NORM, "WhatsApp")
for raw, want in {"+91 98765 43210": "919876543210", "098765 43210": "919876543210", "9876543210": "919876543210",
                  "0091-98765-43210": "919876543210", "12345": "", None: "", "+1 (415) 555-0100": "14155550100"}.items():
    check(f"wa {raw!r}", wa(WhatsApp=raw), want)

li = F.compile_formula(F.F_LI_NORM, "LinkedIn")
for raw, want in {"https://www.linkedin.com/in/foo-bar/?utm=1": "linkedin.com/in/foo-bar",
                  "https://in.linkedin.com/in/Foo-Bar": "linkedin.com/in/foo-bar",
                  "http://linkedin.com/in/foo-bar/": "linkedin.com/in/foo-bar"}.items():
    check(f"li {raw!r}", li(LinkedIn=raw), want)

due = F.compile_formula(F.F_SENT2_DUE, "Reply", "Status", "Sent_1")
check("due thu", due(Reply=None, Status="Sent-1", Sent_1=datetime.date(2026, 11, 12)), datetime.date(2026, 11, 17))
check("due fri", due(Reply=None, Status="Sent-1", Sent_1=datetime.date(2026, 11, 13)), datetime.date(2026, 11, 18))
check("due none", due(Reply=None, Status="Ready", Sent_1=None), None)

f24 = F.compile_formula(F.F_FUNDED_24, "Last_Round_Date")
check("funded recent", f24(Last_Round_Date=datetime.date.today() - datetime.timedelta(days=100)), True)
check("funded old", f24(Last_Round_Date=datetime.date(2020, 1, 1)), False)
check("funded none", f24(Last_Round_Date=None), False)

# Is_Dup logic via a fake Founders.lookupRecords
class Rec:
    def __init__(self, id, **kw):
        self.id = id
        self.__dict__.update(kw)

DB = [Rec(1, Email_norm="a@x.com", WA_norm="", LI_norm=""), Rec(2, Email_norm="a@x.com", WA_norm="", LI_norm=""),
      Rec(3, Email_norm="", WA_norm="919876543210", LI_norm="linkedin.com/in/z"),
      Rec(4, Email_norm="", WA_norm="919876543210", LI_norm="")]


class Founders:
    @staticmethod
    def lookupRecords(**kw):
        k, v = next(iter(kw.items()))
        return [r for r in DB if getattr(r, k) == v]


isdup = F.compile_formula(F.F_IS_DUP, "id", "Email_norm", "WA_norm", "LI_norm", extra={"Founders": Founders})
check("dup first", isdup(id=1, Email_norm="a@x.com", WA_norm="", LI_norm=""), False)
check("dup second", isdup(id=2, Email_norm="a@x.com", WA_norm="", LI_norm=""), True)
check("dup wa later", isdup(id=4, Email_norm="", WA_norm="919876543210", LI_norm=""), True)
check("dup wa first", isdup(id=3, Email_norm="", WA_norm="919876543210", LI_norm="linkedin.com/in/z"), False)

# CSV mapping
hdr = ["Startup Name", "Founder Name", "Title", "Work Email", "Phone", "LinkedIn URL", "Industry", "Funding Stage", "Funding Date", "Extra"]
m = I.detect_mapping(hdr)
check("map company", m.get("Company"), "Startup Name")
check("map name", m.get("Name"), "Founder Name")
check("map email", m.get("Email"), "Work Email")
check("map wa", m.get("WhatsApp"), "Phone")
check("map stage", m.get("Stage"), "Funding Stage")
m2 = I.detect_mapping(["Name", "Email"])  # lone "Name" is treated as company
check("lone name -> company", m2.get("Company"), "Name")
check("desig cofounder", I.norm_designation("Co-Founder & CEO"), "Co-Founder")
check("desig founder", I.norm_designation("Founder and CEO"), "Founder")
check("desig coo", I.norm_designation("Chief Operating Officer"), "COO")
check("desig other", I.norm_designation("VP Sales"), "")
check("vertical", I.norm_vertical("Ed-Tech / K12"), "Edtech")
check("vertical travel", I.norm_vertical("Travel & Hospitality"), "Travel")
check("stage", I.norm_stage("Series A (Led by X)"), "Series A")
check("stage seed", I.norm_stage("Pre-Seed"), "Seed")
check("date", I.parse_date("12 Mar 2026"), datetime.date(2026, 3, 12))
check("date2", I.parse_date("2026-03-12"), datetime.date(2026, 3, 12))

rows = [
    {"Startup Name": "Acme Travel", "Founder Name": "A One", "Title": "Co-Founder", "Work Email": "a@acme.com", "Phone": "98765 43210", "LinkedIn URL": "", "Industry": "Travel", "Funding Stage": "Seed", "Funding Date": "2026-01-05", "Extra": ""},
    {"Startup Name": "Acme Travel", "Founder Name": "B Two", "Title": "CEO", "Work Email": "", "Phone": "", "LinkedIn URL": "https://www.linkedin.com/in/btwo", "Industry": "Travel", "Funding Stage": "Seed", "Funding Date": "2026-01-05", "Extra": ""},
    {"Startup Name": "", "Founder Name": "", "Title": "", "Work Email": "", "Phone": "", "LinkedIn URL": "", "Industry": "", "Funding Stage": "", "Funding Date": "", "Extra": ""},
]
comps, people, skipped = I.build(rows, m)
check("companies", len(comps), 1)
check("people", len(people), 2)
check("skipped", len(skipped), 1)
check("status p1", people[0]["Status"], "Contact-ready")
check("status p2", people[1]["Status"], "New")

html = B.md_to_html("Hi **Ayush**\n\n- ₹40 Cr **GMV**\n- 7 hires\n\nCV: {{RESUME_LINK}}", "https://t.x/cv")
check("html bold", "<b>Ayush</b>" in html, True)
check("html ul", "<ul><li>" in html, True)
check("html link", 'href="https://t.x/cv"' in html, True)
check("html no pixel", "<img" in html, False)

if fails:
    print("FAILED:\n" + "\n".join(fails))
    sys.exit(1)
print("all tests passed")
