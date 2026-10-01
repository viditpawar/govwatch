"""The validation gate between the model and the human review queue.

Schema-constrained decoding guarantees the shape of the output, not that it's true. Each
check here targets a failure seen (or easy to predict) from small local models.
"""

import re
from dataclasses import dataclass

from govwatch.agent.prompt import CRS_POLICY_AREAS

MIN_CHARS = 80
MAX_CHARS = 700
MAX_SENTENCES = 4

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ENACTED_CLAIM = re.compile(
    r"\b(signed into law|became (?:public )?law|was enacted|has been enacted)\b"
)
_PASSED_CLAIM = re.compile(
    r"\b(passed (?:by |in )?(?:the )?(?:house|senate|congress)|was passed)\b"
)
_META = re.compile(
    r"\b(as an ai|language model|i cannot|i'm sorry|the provided (?:text|summary)|"
    r"based on the (?:provided|given))\b"
)
_NOT_YET_PASSED = {"introduced", "in_committee", "reported"}
# agency / program acronyms (EPA, FEMA, AT-CTI). a cheap, precise check for one common kind
# of hallucination: naming an agency the source never mentions
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,}(?:-[A-Z0-9]+)*\b")
_COMMON_ACRONYMS = {"US", "USA", "U", "S"}
# runs of capitalized words, allowing the small words agency names contain
_NAME_RUN = re.compile(r"[A-Z][\w'-]*(?:\s+(?:(?:of|for|and|the|on|in)\s+)*[A-Z][\w'-]*)*")
_NAME_FILLER = {"of", "for", "and", "the", "on", "in"}


def _name_initials(text: str) -> list[str]:
    """Initials of every capitalized name in the text, so "Centers for Disease Control and
    Prevention" yields "CDCP" and a model writing "CDC" isn't flagged as inventing it."""
    return [
        "".join(w[0] for w in run.split() if w not in _NAME_FILLER).upper()
        for run in _NAME_RUN.findall(text)
    ]


@dataclass(frozen=True)
class Issue:
    check: str
    detail: str

    def __str__(self) -> str:
        return self.detail


def validate(output: dict, source_text: str, stage: str) -> list[Issue]:
    """Return everything wrong with a model output. An empty list means it passes."""
    issues: list[Issue] = []
    summary = output.get("summary")
    area = output.get("policy_area")

    if not isinstance(summary, str) or not summary.strip():
        return [Issue("schema", "summary is missing or empty")]
    if area not in CRS_POLICY_AREAS:
        issues.append(Issue("schema", f"policy_area {area!r} is not a CRS policy area"))

    summary = summary.strip()
    if len(summary) < MIN_CHARS:
        issues.append(Issue("length", f"summary is {len(summary)} chars, minimum {MIN_CHARS}"))
    if len(summary) > MAX_CHARS:
        issues.append(Issue("length", f"summary is {len(summary)} chars, maximum {MAX_CHARS}"))
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", summary) if s]
    if len(sentences) > MAX_SENTENCES:
        issues.append(
            Issue("length", f"summary has {len(sentences)} sentences, max {MAX_SENTENCES}")
        )

    # every number the model writes has to come from the source - invented dollar
    # amounts and dates are the most damaging kind of hallucination for analysts
    source_numbers = {n.replace(",", "") for n in _NUMBER.findall(source_text)}
    invented = sorted(
        {n for n in (m.replace(",", "") for m in _NUMBER.findall(summary))} - source_numbers
    )
    if invented:
        issues.append(
            Issue("ungrounded_number", f"numbers not in the source text: {', '.join(invented)}")
        )

    acronyms = set(_ACRONYM.findall(summary)) - _COMMON_ACRONYMS
    initials = _name_initials(source_text)
    unknown = sorted(
        a
        for a in acronyms
        if a not in source_text and not any(a.replace("-", "") in i for i in initials)
    )
    if unknown:
        issues.append(
            Issue("ungrounded_acronym", f"acronyms not in the source text: {', '.join(unknown)}")
        )

    # the stage is a code-derived fact (decisions.md #38); the summary can't contradict it
    lowered = summary.lower()
    if stage != "enacted" and _ENACTED_CLAIM.search(lowered):
        issues.append(
            Issue("stage_contradiction", f"summary says the bill became law, but it is {stage}")
        )
    elif stage in _NOT_YET_PASSED and _PASSED_CLAIM.search(lowered):
        issues.append(
            Issue("stage_contradiction", f"summary says the bill passed, but it is {stage}")
        )

    if _META.search(lowered):
        issues.append(Issue("meta_text", "summary talks about itself or the model"))
    return issues
