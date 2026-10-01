"""Things the agent should never ask a model for, because code gets them right every time.

See decisions.md #38: on real bills, small models got the legislative stage right 1-3
times out of 6 even with the action text in the prompt.
"""

import html
import re

STAGES = (
    "introduced",
    "in_committee",
    "reported",  # out of committee / on a calendar, waiting for a floor vote
    "passed_one_chamber",
    "passed_both",
    "enacted",
    "adopted",  # simple resolutions (hres/sres) only need their own chamber
)

SIMPLE_RESOLUTIONS = {"hres", "sres"}

_PASSED = re.compile(
    r"passed (?:the )?(senate|house)"
    r"|passed/agreed to in (senate|house)"
    r"|agreed to in (?:the )?(senate|house)"
    r"|(?:considered,? and )?agreed to\b"
    r"|\band passed\b"
)


def derive_stage(latest_action: str | None, bill_type: str) -> str:
    """Map a bill's latest action text to a coarse legislative stage.

    Chamber-aware: when the latest action happens in the other chamber, the bill has
    already passed its own, even if the text never says "passed" (e.g. a House bill
    "Received in the Senate").
    """
    t = (latest_action or "").lower()
    if "became public law" in t or "signed by president" in t:
        return "enacted"
    if "presented to president" in t or "cleared for white house" in t:
        return "passed_both"

    simple = bill_type in SIMPLE_RESOLUTIONS
    origin = "senate" if bill_type.startswith("s") else "house"
    other = "house" if origin == "senate" else "senate"

    m = _PASSED.search(t)
    if m:
        if simple:
            return "adopted"
        chamber = next((g for g in m.groups() if g), None)
        return "passed_both" if chamber == other else "passed_one_chamber"

    if not simple and (other in t or "held at the desk" in t):
        return "passed_one_chamber"
    if (
        ("placed on" in t and "calendar" in t)
        or t.startswith("reported by")
        or "ordered to be reported" in t
    ):
        return "reported"
    if "referred to" in t or "committee" in t:
        return "in_committee"
    return "introduced"


def html_to_text(raw: str) -> str:
    """CRS summaries come as HTML fragments; flatten to plain text."""
    text = re.sub(r"<(br|/p|/li|/h\d)\s*/?>", "\n", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()
