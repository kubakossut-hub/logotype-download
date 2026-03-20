"""
Uses Claude claude-3-5-haiku to resolve the correct website domain for each company.
One batched API call for all companies — fast and cheap.
Falls back to heuristic guessing if the API is unavailable.
"""
import json
import os
import re
import logging

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a company domain lookup tool with broad knowledge of businesses worldwide. "
    "Given a list of company names and optional industry context, return their official website domains. "
    "Reply ONLY with a JSON object mapping each company name to its domain. "
    "Domain format: lowercase, no https://, no www., no trailing slash.\n"
    "Process each company in two steps:\n"
    "1. IDENTIFY: Use your knowledge (plus any provided industry context) to identify the specific "
    "company and recall its actual official domain. Industry context helps disambiguate — e.g. 'MNTN' "
    "in AdTech is mntn.com (not a mountain gear brand). Trust your training knowledge over spelling.\n"
    "2. FALLBACK (only if truly unknown): derive from the company name — strip legal suffixes "
    "(Inc/LLC/Ltd/Corp/GmbH), remove spaces and special chars, lowercase, append .com "
    "(or .tv/.io/.ai if strongly implied). Preserve brand spelling exactly.\n"
    "Example: {\"Apple\": \"apple.com\", \"ShowHeroes\": \"showheroes.com\", \"SambaTV\": \"samba.tv\"}"
)


def _heuristic_domain(company: str) -> str:
    """Simple fallback when Claude is unavailable."""
    LEGAL = r"\b(inc|llc|ltd|corp|co|gmbh|s\.a\.|plc|ag|bv|nv|oy|ab|as|a/s|pty|pvt)\b\.?"
    name = company.lower().strip()
    name = re.sub(LEGAL, "", name, flags=re.IGNORECASE)
    name = name.replace("&", "").replace("'", "").replace(".", "")
    name = re.sub(r"\s+", "", name.strip())
    name = re.sub(r"[^a-z0-9\-]", "", name)
    # .tv suffix for names ending in "tv"
    if name.endswith("tv") and len(name) > 2:
        return f"{name[:-2]}.tv"
    return f"{name}.com"


async def resolve_domains(companies: list[str], context: str = "") -> dict[str, str]:
    """
    Returns {company_name: domain} for each company.
    Uses claude-3-5-haiku-20241022 in a single batched call.
    Falls back to heuristics if ANTHROPIC_API_KEY is missing or call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — using heuristic domain guessing")
        return {c: _heuristic_domain(c) for c in companies}

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)

        context_note = f"\nContext about these companies: {context}" if context.strip() else ""
        user_msg = f"Resolve domains for these companies:{context_note}\n" + json.dumps(companies)

        message = await client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = message.content[0].text.strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        mapping: dict = json.loads(raw)

        # Normalise values: strip https://, www., trailing slashes
        result: dict[str, str] = {}
        for company in companies:
            domain = mapping.get(company, "")
            if domain:
                domain = (domain
                          .lower()
                          .removeprefix("https://")
                          .removeprefix("http://")
                          .removeprefix("www.")
                          .rstrip("/")
                          .split("/")[0])
            result[company] = domain or _heuristic_domain(company)

        logger.info("Claude resolved %d domains", len(result))
        return result

    except Exception as e:
        logger.warning("Domain resolution via Claude failed (%s) — falling back to heuristics", e)
        return {c: _heuristic_domain(c) for c in companies}
