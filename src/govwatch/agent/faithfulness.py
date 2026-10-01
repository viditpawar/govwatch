"""How much of a summary is actually supported by its source text.

A crude lexical measure: the share of a summary's content words (ignoring the bill's own
title) that appear in the CRS text, compared on a 6-letter prefix so "authorizes" and
"authorization" match. It can't tell a paraphrase from an invention - "allocates funds"
for "provides amounts" scores low - so it's too noisy to reject single summaries with.

Over a batch it separates cleanly, which is what it's used for (decisions.md #55). On the
golden set the median was 0.77 for normal summaries and 0.20 when the model only saw the
title, which is the regression that the validation gate and policy-area agreement both
missed entirely (one title-only summary turned a SNAP bill into an oil spill bill).
"""

import re

_WORD = re.compile(r"[a-z][a-z'-]+")
# a word list reads better as text than as an 80-line list literal
_STOP = frozenset(
    """
    a an the and or of for to in on at by with from as is are was were be been this that
    these those it its their they them which who whom whose will would shall may can could
    should must also such other than into over under about after before between during
    within without through bill bills resolution act acts law laws federal provides provide
    requires require certain including include includes allows allow affects affect
    individuals aims new
    """.split()  # noqa: SIM905
)
PREFIX = 6


def _content_words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) >= 4 and w not in _STOP]


def source_support(summary: str, source: str, title: str = "") -> float:
    """0..1: share of the summary's content words found in the source. Title words are
    left out - repeating the title proves nothing about having read the bill."""
    title_stems = {w[:PREFIX] for w in _content_words(title)}
    source_stems = {w[:PREFIX] for w in _content_words(source)}
    words = [w for w in _content_words(summary) if w[:PREFIX] not in title_stems]
    if not words:
        return 1.0
    return sum(w[:PREFIX] in source_stems for w in words) / len(words)
