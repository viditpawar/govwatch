"""Prompt and output schema. Bump PROMPT_VERSION on any change to either - every bill
gets re-summarized under the new version, and summaries record which one produced them."""

from dataclasses import dataclass

PROMPT_VERSION = "v1"

# congress.gov / CRS policy areas. the official one for a bill is a fact we store as-is;
# the model picks from the same list only as a shadow check (decisions.md #41)
CRS_POLICY_AREAS = (
    "Agriculture and Food",
    "Animals",
    "Armed Forces and National Security",
    "Arts, Culture, Religion",
    "Civil Rights and Liberties, Minority Issues",
    "Commerce",
    "Congress",
    "Crime and Law Enforcement",
    "Economics and Public Finance",
    "Education",
    "Emergency Management",
    "Energy",
    "Environmental Protection",
    "Families",
    "Finance and Financial Sector",
    "Foreign Trade and International Finance",
    "Geographic Areas, Entities, and Committees",
    "Government Operations and Politics",
    "Health",
    "Housing and Community Development",
    "Immigration",
    "International Affairs",
    "Labor and Employment",
    "Law",
    "Native Americans",
    "Private Legislation",
    "Public Lands and Natural Resources",
    "Science, Technology, Communications",
    "Social Sciences and History",
    "Social Welfare",
    "Sports and Recreation",
    "Taxation",
    "Transportation and Public Works",
    "Water Resources Development",
)

# enforced by ollama during decoding (decisions.md #39). content rules that a schema
# can't express live in validate.py
OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["summary", "policy_area"],
    "properties": {
        "summary": {"type": "string"},
        "policy_area": {"type": "string", "enum": list(CRS_POLICY_AREAS)},
    },
}

SYSTEM = (
    "You write short, neutral briefs on US legislation for government affairs analysts. "
    "Use only the information provided. Do not add facts, numbers, agencies, dates or "
    "effects that the source text does not state, and do not speculate about outcomes."
)

# CRS summaries can run to thousands of words; keep the prompt inside a small context window
MAX_SOURCE_CHARS = 6000


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    truncated: bool


def build_prompt(title: str, stage: str, crs_summary: str) -> Prompt:
    source = crs_summary
    truncated = len(source) > MAX_SOURCE_CHARS
    if truncated:
        source = source[:MAX_SOURCE_CHARS].rsplit(" ", 1)[0] + " [...]"
    user = (
        f"Title: {title}\n"
        f"Current stage: {stage.replace('_', ' ')}\n\n"
        f"Official CRS summary:\n{source}\n\n"
        "Write `summary`: 2-3 plain-English sentences on what the bill does and who it "
        "affects. Then pick the single best `policy_area`."
    )
    return Prompt(system=SYSTEM, user=user, truncated=truncated)


def with_feedback(prompt: Prompt, problems: list[str]) -> Prompt:
    """Second attempt: same prompt plus what the validation gate rejected."""
    issues = "\n".join(f"- {p}" for p in problems)
    return Prompt(
        system=prompt.system,
        user=f"{prompt.user}\n\nYour previous answer was rejected:\n{issues}\nFix these.",
        truncated=prompt.truncated,
    )
