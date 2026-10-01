import json
from pathlib import Path

import pytest

from govwatch.agent.facts import STAGES, derive_stage, html_to_text

FIXTURES = Path(__file__).parent / "fixtures"


# real latest-action texts from congress.gov, with the stage a person would assign
@pytest.mark.parametrize(
    "bill_type, action, expected",
    [
        ("hr", "Became Public Law No: 119-113.", "enacted"),
        ("s", "Presented to President.", "passed_both"),
        (
            "hr",
            "Passed Senate without amendment by Voice Vote. (consideration: CR S5120)",
            "passed_both",
        ),
        ("s", "Passed Senate with an amendment by Yea-Nay Vote. 77 - 22.", "passed_one_chamber"),
        (
            "s",
            "Introduced in the Senate, read twice, considered, read the third time, and passed "
            "without amendment by Voice Vote.",
            "passed_one_chamber",
        ),
        # the latest action happens in the other chamber, so it already passed its own
        (
            "hr",
            "Received in the Senate and Read twice and referred to the Committee on Homeland "
            "Security and Governmental Affairs.",
            "passed_one_chamber",
        ),
        (
            "hr",
            "Received in the Senate. Read twice. Placed on Senate Legislative Calendar under "
            "General Orders.",
            "passed_one_chamber",
        ),
        ("s", "Message on Senate action sent to the House.", "passed_one_chamber"),
        (
            "hr",
            "Motion to proceed to consideration of measure withdrawn in Senate.",
            "passed_one_chamber",
        ),
        ("s", "Held at the desk.", "passed_one_chamber"),
        # simple resolutions only need their own chamber
        (
            "sres",
            "Resolution agreed to in Senate without amendment and with a preamble by Unanimous "
            "Consent.",
            "adopted",
        ),
        (
            "sres",
            "Submitted in the Senate, considered, and agreed to without amendment and with a "
            "preamble by Unanimous Consent.",
            "adopted",
        ),
        (
            "sres",
            "Motion to discharge Senate Committee on Foreign Relations rejected.",
            "in_committee",
        ),
        ("hr", "Placed on the Union Calendar, Calendar No. 575.", "reported"),
        ("hr", "Ordered to be Reported (Amended) by the Yeas and Nays: 62 - 2.", "reported"),
        (
            "s",
            "Introduced in the Senate. Read the first time. Placed on Senate Legislative Calendar "
            "under Read the First Time.",
            "reported",
        ),
        ("s", "Read twice and referred to the Committee on Finance.", "in_committee"),
        ("hres", "Referred to the House Committee on Natural Resources.", "in_committee"),
        ("hr", "Referred to the Subcommittee on Social Security.", "in_committee"),
        ("hr", "Introduced in House", "introduced"),
        ("hr", None, "introduced"),
    ],
)
def test_derive_stage(bill_type, action, expected):
    assert expected in STAGES
    assert derive_stage(action, bill_type) == expected


def test_html_to_text_flattens_a_real_crs_summary():
    summaries = json.loads((FIXTURES / "congress_bill_with_summary_summaries.json").read_text())
    raw = summaries["summaries"][0]["text"]
    text = html_to_text(raw)

    assert "<" not in text and "&nbsp;" not in text
    assert "This bill" in text
    # headings become their own line instead of running into the next sentence
    assert "Act of 2025This bill" not in text


def test_html_to_text_unescapes_entities():
    assert html_to_text("<p>Fish &amp; Wildlife&nbsp;Service</p>") == "Fish & Wildlife Service"
