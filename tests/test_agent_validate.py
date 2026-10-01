import pytest

from govwatch.agent.validate import validate

SOURCE = (
    "Air Traffic Control Workforce Development Act of 2025\n"
    "Referred to the Subcommittee on Aviation.\n"
    "This bill expands Air Traffic Control (ATC) workforce training. It provides statutory "
    "authority for the Enhanced Air Traffic-Collegiate Training Initiative (AT-CTI) program "
    "and authorizes $5,000,000 for each of FY2026 through FY2030 for the FAA."
)
GOOD = {
    "summary": (
        "The bill expands air traffic controller training and gives the AT-CTI program a "
        "statutory basis. It authorizes $5,000,000 a year for the FAA to support it."
    ),
    "policy_area": "Transportation and Public Works",
}


def checks(output, stage="in_committee", source=SOURCE):
    return [i.check for i in validate(output, source, stage)]


def test_a_grounded_summary_passes():
    assert checks(GOOD) == []


def test_missing_summary():
    assert checks({"policy_area": "Health"}) == ["schema"]


def test_policy_area_outside_crs_list():
    assert "schema" in checks({**GOOD, "policy_area": "Aviation"})


@pytest.mark.parametrize(
    "summary",
    ["Expands ATC training.", "This bill does many things for training. " * 20],
)
def test_length_limits(summary):
    assert "length" in checks({**GOOD, "summary": summary})


def test_invented_dollar_amount_is_caught():
    out = {**GOOD, "summary": GOOD["summary"].replace("$5,000,000", "$50 million")}
    issues = validate(out, SOURCE, "in_committee")
    assert [i.check for i in issues] == ["ungrounded_number"]
    assert "50" in issues[0].detail


def test_numbers_with_different_comma_formatting_still_match():
    out = {**GOOD, "summary": GOOD["summary"].replace("$5,000,000", "$5000000")}
    assert checks(out) == []


def test_invented_agency_acronym_is_caught():
    out = {**GOOD, "summary": GOOD["summary"] + " The NTSB will oversee the program."}
    assert checks(out) == ["ungrounded_acronym"]


def test_acronym_of_a_name_spelled_out_in_the_source_is_grounded():
    """Real false positive from the first live batch (119-hres-835): the CRS text spelled
    out the agency and the model abbreviated it."""
    source = (
        "This resolution declares gun violence to be a public health crisis. It also urges "
        "the Centers for Disease Control and Prevention to expand research and data "
        "collection on preventing gun violence."
    )
    summary = (
        "The resolution declares gun violence a public health crisis and urges expanded CDC "
        "research on preventing it."
    )
    assert checks({**GOOD, "summary": summary}, source=source) == []
    # second real false positive, found in the review UI (118-hr-4033): "USDA" for a source
    # that only says "Department of Agriculture"
    usda_source = (
        "This bill pays costs incurred by states for certain Department of Agriculture programs."
    )
    usda = (
        "The bill funds state agencies that run certain USDA food assistance programs nationwide."
    )
    assert checks({**GOOD, "summary": usda}, source=usda_source) == []
    assert checks({**GOOD, "summary": usda.replace("USDA", "USPS")}, source=usda_source) == [
        "ungrounded_acronym"
    ]
    # but an agency that isn't there at all is still caught
    assert checks({**GOOD, "summary": summary.replace("CDC", "NIH")}, source=source) == [
        "ungrounded_acronym"
    ]


@pytest.mark.parametrize(
    "claim, stage",
    [
        ("The bill was signed into law to expand controller training at the FAA.", "in_committee"),
        ("The bill became law and expands controller training at the FAA.", "passed_both"),
        ("The bill passed the Senate and expands controller training at the FAA.", "introduced"),
    ],
)
def test_summary_cannot_contradict_the_code_derived_stage(claim, stage):
    out = {**GOOD, "summary": claim + " It supports the AT-CTI program."}
    assert "stage_contradiction" in checks(out, stage=stage)


def test_saying_it_passed_is_fine_once_it_has():
    out = {
        **GOOD,
        "summary": "The bill passed the Senate. " + GOOD["summary"],
    }
    assert checks(out, stage="passed_one_chamber") == []


def test_meta_text_is_caught():
    out = {**GOOD, "summary": "Based on the provided text, " + GOOD["summary"]}
    assert "meta_text" in checks(out)


def test_every_check_the_gate_can_report_is_registered():
    """A check missing from CHECKS wouldn't get its metric series pre-created, so it would
    be invisible on the dashboard until the first time it fired."""
    import inspect
    import re

    from govwatch.agent import validate as v

    emitted = set(re.findall(r'Issue\(\s*"([a-z_]+)"', inspect.getsource(v)))
    assert emitted == set(v.CHECKS)
