import json
from pathlib import Path

from govwatch.agent.faithfulness import source_support

GOLDEN = {
    json.loads(line)["bill_id"]: json.loads(line)
    for line in (Path(__file__).parent / "eval" / "golden_bills.jsonl").open(encoding="utf-8")
}


def test_real_grounded_vs_title_only_summary():
    """119-hr-5223, from the eval where the model was shown only titles: it confused the
    2025 RESTORE Act (SNAP eligibility) with the 2012 one (oil spill restoration)."""
    bill = GOLDEN["119-hr-5223"]
    grounded = (
        "The RESTORE Act of 2025 removes a ban on SNAP participation for individuals with "
        "certain felony drug convictions and allows incarcerated individuals to apply for "
        "benefits up to 30 days before their release."
    )
    title_only = (
        "The RESTORE Act of 2025 focuses on allocating funds from oil spill cleanup efforts "
        "to coastal restoration and maintenance projects."
    )
    good = source_support(grounded, bill["crs_summary"], bill["title"])
    bad = source_support(title_only, bill["crs_summary"], bill["title"])
    assert good > 0.7
    assert bad < 0.2


def test_repeating_the_title_earns_nothing():
    title = "Rural Hospital Sustainability Act"
    assert source_support("The Rural Hospital Sustainability Act.", "Unrelated text.", title) == 1.0
    # "1.0" means nothing to judge, not "well supported": a summary that is only the title
    # is caught by the gate's length check instead


def test_word_forms_match_on_a_shared_prefix():
    source = "The bill authorizes grants to states."
    assert source_support("It authorized grants to the states", source) == 1.0
