"""Grist formula sources (Python, they run INSIDE Grist) + a local compiler used by tests / dry-runs.

Single source of truth: setup_grist.py installs these, import_csv.py uses the same code for previews.
"""
import datetime

F_EMAIL_NORM = '''e = (($Email or "").strip().lower())
if "@" not in e:
    return ""
local, dom = e.split("@", 1)
local = local.split("+")[0]
if dom in ("gmail.com", "googlemail.com"):
    local = local.replace(".", "")
    dom = "gmail.com"
return local + "@" + dom'''

F_WA_NORM = '''import re
d = re.sub(r"\\D", "", str($WhatsApp or ""))
if d.startswith("00"):
    d = d[2:]
elif d.startswith("0") and len(d) == 11:
    d = d[1:]
if len(d) == 10:
    d = "91" + d          # default country = India
return d if 11 <= len(d) <= 15 else ""'''

F_LI_NORM = '''import re
u = (($LinkedIn or "").strip().lower())
u = re.sub(r"^https?://([a-z]{2,3}\\.)?", "", u)
u = u.split("?")[0].split("#")[0].rstrip("/")
return u'''

# True only for the LATER row of a duplicate pair (lower row id wins), so the first copy stays usable.
F_IS_DUP = '''dup = False
if $Email_norm:
    dup = dup or any(r.id < $id for r in Founders.lookupRecords(Email_norm=$Email_norm))
if $WA_norm:
    dup = dup or any(r.id < $id for r in Founders.lookupRecords(WA_norm=$WA_norm))
if $LI_norm:
    dup = dup or any(r.id < $id for r in Founders.lookupRecords(LI_norm=$LI_norm))
return dup'''

F_WA_LINK = '''import urllib.parse
if not $WA_norm or not $WA_Msg:
    return ""
return "https://wa.me/" + $WA_norm + "?text=" + urllib.parse.quote($WA_Msg)'''

F_WA2_LINK = '''import urllib.parse
if not $WA_norm or not $WA2_Msg:
    return ""
return "https://wa.me/" + $WA_norm + "?text=" + urllib.parse.quote($WA2_Msg)'''

# Follow-up cadence (business days):  Email 1 (D0) -> WhatsApp 1 (+3) -> Email 2 in same thread (+5) -> WhatsApp 2 (+3 after Email 2)
F_SENT2_DUE = '''import datetime
if $Reply in ("Replied", "Bounced", "Unsubscribed") or $Status in ("Skip", "Replied", "Bounced"):
    return None
if $Status == "Sent-1" and $Sent_1:
    d = $Sent_1 + datetime.timedelta(days=5)
    while d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return d
return None'''

F_WA1_DUE = '''import datetime
if $Reply in ("Replied", "Bounced", "Unsubscribed") or $Status in ("Skip", "Replied", "Bounced"):
    return None
if $WA_Sent or not $Sent_1 or not $WA_norm:
    return None
d = $Sent_1 + datetime.timedelta(days=3)
while d.weekday() >= 5:
    d += datetime.timedelta(days=1)
return d'''

F_WA2_DUE = '''import datetime
if $Reply in ("Replied", "Bounced", "Unsubscribed") or $Status in ("Skip", "Replied", "Bounced"):
    return None
if $WA2_Sent or not $WA_Sent or not $Sent_2 or not $WA_norm:
    return None
d = $Sent_2 + datetime.timedelta(days=3)
while d.weekday() >= 5:
    d += datetime.timedelta(days=1)
return d'''

F_FUNDED_24 = '''return bool($Last_Round_Date) and (TODAY() - $Last_Round_Date).days <= 730'''
F_FOUNDERS_COUNT = '''return len(Founders.lookupRecords(Company=$id))'''
F_COMPANY_NAME = '''return $Company.Company if $Company else ""'''


def compile_formula(src, *fields, extra=None):
    """Turn a Grist formula body into a plain python callable f(**field values) for local tests.
    `$X` becomes the argument `_X`. Pass fields as the names used in the formula (without $)."""
    body = src.replace("$", "_")
    head = "def f(" + ", ".join("_" + f for f in fields) + "):\n"
    code = head + "\n".join("    " + line for line in body.split("\n"))
    ns = {"TODAY": datetime.date.today}
    if extra:
        ns.update(extra)
    exec(code, ns)
    fn = ns["f"]
    return lambda **kw: fn(**{"_" + k: v for k, v in kw.items()})
