"""Templates, proof lines and rendering (spec section 6). Plain text only."""
import re
import zlib

PROOFS = {
    1: "I built a BD desk from zero to a ₹40 Cr GMV quarter, and I can do the same for {{companyName}}.",
    2: "Hired 7 salespeople in 30 days and took conversion from 7% to 20%, I would love to bring that to {{companyName}}.",
    3: "I turn messy pipelines into revenue systems: ₹9 Cr scaled at AlgoUniversity, ₹40 Cr GMV at Enout.",
    4: "I close deals and build the system behind them: ₹2.7 Cr closed personally at 40% conversion.",
}
DEFAULT_PROOF = {"Founder": 1, "Job poster": 3}

SUBJECTS = {
    1: "{{roleTitle}} at {{companyName}}",
    2: "Your {{roleTitle}} post",
    3: "{{companyName}} and a BD system that runs itself",
}

BODIES = {
    # (template, step) -> body
    ("A", 1): "Hi {{firstName}},\n\n{{firstLine}}\n\n{{proofLine}}\n\nI saw the {{roleTitle}} opening and applied. Open to a 15-minute call this week?\n\nAyush",
    ("C", 1): "Hi {{firstName}},\n\nI applied for {{roleTitle}} at {{companyName}} ({{jobUrl}}). {{proofLine}}\n\nIs it still open?\n\nAyush",
    ("A", 2): "Hi {{firstName}}, one more thing that may be useful for {{companyName}}: {{secondProof}}\n\nHappy to share how I did it on a call.\n\nAyush",
    ("A", 3): "Hi {{firstName}}, closing the loop. My resume is here: {{resumeUrl}}. If the role moves, I would love to be considered.\n\nAyush",
}
BODIES[("C", 2)] = BODIES[("A", 2)]
BODIES[("C", 3)] = BODIES[("A", 3)]

LINKEDIN_INVITE = "Hi {{firstName}}, applied for {{roleTitle}} at {{companyName}}. {{proofLine}} Would love to connect."
LINKEDIN_MESSAGE = "Hi {{firstName}}, sent you an email about {{roleTitle}}. {{secondProof}} Worth a quick chat?"

_VAR = re.compile(r"\{\{(\w+)\}\}")


def render(text: str, vars: dict) -> str:
    """Substitute {{vars}} (proof lines may nest {{companyName}}). Missing/empty vars raise."""
    for _ in range(2):  # second pass resolves variables inside proof lines
        missing = [k for k in _VAR.findall(text) if not str(vars.get(k, "")).strip()]
        if missing:
            raise KeyError(f"missing template variables: {sorted(set(missing))}")
        text = _VAR.sub(lambda m: str(vars[m.group(1)]), text)
    return text


def template_for(contact_type: str) -> str:
    return "C" if contact_type == "Job poster" else "A"


def subject_id(template: str, email: str) -> int:
    """Job poster -> subject 2. Founder -> A/B between subjects 1 and 3 (stable per contact)."""
    if template == "C":
        return 2
    return 1 if zlib.crc32(email.lower().encode()) % 2 == 0 else 3


def pick_proof(contact_type: str) -> int:
    return DEFAULT_PROOF.get(contact_type, 1)


def second_proof_id(used: int) -> int:
    """Proof for follow-ups: first one that was not used in step 1."""
    return next(k for k in (3, 4, 2, 1) if k != used)


def tag(template: str, subj: int, proof: int) -> str:
    """Stored in Touches.Template so we can log what was used: e.g. 'A/s1/p1'."""
    return f"{template}/s{subj}/p{proof}"


def parse_tag(tag_: str) -> tuple[str, int, int]:
    m = re.fullmatch(r"([AC])/s(\d)/p(\d)", tag_ or "")
    if not m:
        raise ValueError(f"unparseable template tag: {tag_!r}")
    return m.group(1), int(m.group(2)), int(m.group(3))
