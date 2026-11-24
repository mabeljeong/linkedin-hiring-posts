"""Search configuration: generic queries, per-company queries, target companies.

LinkedIn Posts search supports quotes, OR, and parentheses in keywords.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Requirement 1: generic searches (no company filter), past week, latest first.
# Hiring managers write in the first person ("my team", "I'm hiring"); searches
# without such a phrase mostly return thought pieces, agencies, and gig work.
_ROLES = '("data scientist" OR "data engineer" OR "analytics engineer" OR "machine learning engineer")'
GENERIC_QUERIES: tuple[str, ...] = (
    'hiring ("data engineer" OR "data scientist") "my team"',
    f'"I\'m hiring" {_ROLES}',
    f'"join my team" {_ROLES}',
    f'"my team is hiring" {_ROLES}',
    'hiring ("machine learning engineer" OR "applied scientist" OR "AI engineer") "my team"',
    'hiring ("evals" OR "LLM evaluation" OR "AI quality") "my team"',
)

# Requirement 2: searches over batches of target companies. `{company}` expands
# to ("Company1" OR "Company2" OR ...) using each company's first alias.
#
# LinkedIn Posts search silently returns nothing once a query has more than
# MAX_QUERY_TERMS OR'd quoted terms (tested: 7 works, 8 and 9 return 0), so
# batch size + role terms must stay within it. validate_queries() enforces this.
#
# The "data" and "ai" searches alternate between runs (see main.py --part):
# each run is 13 company searches + 6 generic instead of 32, and since every
# search covers the past week, running each type once a day misses nothing.
MAX_QUERY_TERMS = 7
COMPANY_BATCH_SIZE = 4
COMPANY_QUERY_TEMPLATES: dict[str, str] = {
    "data": '{company} hiring ("data engineer" OR "data scientist" OR "analytics engineer")',
    "ai": '{company} hiring ("machine learning" OR "evals" OR "AI quality")',
}

# Target companies: display name -> aliases. The first alias is used in the
# search; a post is assigned to a company if any alias appears in the author's
# headline or the post talks about hiring for it.
#
# Your real list lives in companies.json (git-ignored), e.g.
#   {"PayPal": ["PayPal"], "Bristol Myers Squibb": ["Bristol Myers Squibb", "BMS"]}
# Without it, this example list is used.
EXAMPLE_COMPANIES: dict[str, tuple[str, ...]] = {
    "Stripe": ("Stripe",),
    "Airbnb": ("Airbnb",),
    "Netflix": ("Netflix",),
    "Spotify": ("Spotify",),
    "Databricks": ("Databricks",),
    "Snowflake": ("Snowflake",),
    "Duolingo": ("Duolingo",),
    "Pinterest": ("Pinterest",),
}
COMPANIES_PATH = Path(__file__).parent / "companies.json"


def load_companies() -> dict[str, tuple[str, ...]]:
    if not COMPANIES_PATH.exists():
        return EXAMPLE_COMPANIES
    raw = json.loads(COMPANIES_PATH.read_text(encoding="utf-8"))
    return {name: tuple(aliases) for name, aliases in raw.items()}


COMPANIES = load_companies()


def company_batches() -> list[dict[str, tuple[str, ...]]]:
    items = list(COMPANIES.items())
    return [dict(items[i : i + COMPANY_BATCH_SIZE]) for i in range(0, len(items), COMPANY_BATCH_SIZE)]


def company_keyword(batch: dict[str, tuple[str, ...]]) -> str:
    """Search-term form of a batch: ("A" OR "B" OR ...)."""
    return "(" + " OR ".join(f'"{aliases[0]}"' for aliases in batch.values()) + ")"


def query_terms(query: str) -> int:
    return len(re.findall(r'"[^"]+"', query))


def validate_queries() -> None:
    """Fail fast if any query would exceed LinkedIn's term limit."""
    queries = list(GENERIC_QUERIES) + [
        template.format(company=company_keyword(batch))
        for batch in company_batches()
        for template in COMPANY_QUERY_TEMPLATES.values()
    ]
    too_long = [q for q in queries if query_terms(q) > MAX_QUERY_TERMS]
    if too_long:
        raise ValueError(
            f"{len(too_long)} queries exceed {MAX_QUERY_TERMS} quoted terms, which makes "
            f"LinkedIn return no results. Lower COMPANY_BATCH_SIZE or role terms. "
            f"First: {too_long[0]}"
        )
