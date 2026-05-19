
"""
═══════════════════════════════════════════════════════════════
 LLM FILTER — AI-Powered Relevance Filtering
═══════════════════════════════════════════════════════════════
 Uses OpenAI to review search results and keep only those
 genuinely relevant to urban mobility policy.

 This filters out "noise" — e.g., a search for "Bike" might
 return results about general cycling infrastructure that
 aren't relevant to shared mobility regulation.
═══════════════════════════════════════════════════════════════
"""

import json
import logging
from openai import AsyncOpenAI

from config import OPENAI_API_KEY, LLM_MODEL

logger = logging.getLogger("council-monitor.llm")

# Only create the client if we have an API key
client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ─── The prompt that tells the AI what to keep/discard ───
SYSTEM_PROMPT = """You are an analyst monitoring German city council decisions 
related to urban mobility, micromobility, and transportation policy for a 
mobility company.

You will receive search results (title + URL) from German city council 
information systems. Determine which results are RELEVANT.

═══ RELEVANT — KEEP THESE ═══
• Micromobility regulation (e-scooters, e-bikes, shared bikes, pedelecs)
• Ride-hailing and taxi regulation, licensing, permits
• Carsharing policy and permits
• Special use permits (Sondernutzung) for shared mobility vehicles on public land
• Transportation infrastructure changes affecting mobility services
• Air quality measures specifically related to urban transport
• Multimodal transportation initiatives and integration
• Pricing regulations for mobility (minimum prices, price corridors, tariffs)
• Public tenders / procurement for mobility services
• Vehicle electrification policy for fleets
• Shared mobility station planning and placement
• Any mention of specific companies: Bolt, Lime, Tier, Voi, ShareNow, etc.

═══ IRRELEVANT — FILTER OUT ═══
• General road construction or closures
• Public transit timetable changes (unless about integration with shared mobility)
• Cycling infrastructure that doesn't mention sharing/rental/permits
• General city council administrative matters (budgets, personnel, etc.)
• Parking regulations not related to shared vehicles
• General environmental policy not specifically about transport

═══ OUTPUT FORMAT ═══
Respond with a JSON object containing a "results" array. Each item must have:
{
  "results": [
    {
      "title": "original title",
      "url": "original url",
      "relevant": true or false,
      "reason": "one sentence explaining why"
    }
  ]
}

Respond ONLY with valid JSON. No other text."""


async def filter_results(all_results: list) -> list:
    """
    Filter search results using OpenAI.
    Only processes cities that actually found results.
    Returns the same list but with irrelevant results removed.
    """
    if not client:
        logger.warning("⚠ No OpenAI API key — skipping AI filtering (keeping all results)")
        return all_results

    for city_data in all_results:
        # Skip cities with no results or errors
        if not city_data["results"] or city_data.get("error"):
            continue

        city = city_data["city"]
        count = len(city_data["results"])
        logger.info(f"  🤖 Filtering {count} results for {city}...")

        try:
            # Prepare data for the AI
            items = [{"title": r["title"], "url": r["url"]} for r in city_data["results"]]

            response = await client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
                ],
                temperature=0.1,   # Very low = consistent, deterministic answers
                max_tokens=4096,
            )

            content = response.choices[0].message.content

            # Parse the AI's response
            parsed = json.loads(content)

            # Handle possible response formats
            if isinstance(parsed, dict) and "results" in parsed:
                filtered_items = parsed["results"]
            elif isinstance(parsed, list):
                filtered_items = parsed
            else:
                logger.warning(f"  Unexpected AI response format for {city}")
                continue

            # Keep only relevant items
            relevant = [
                {
                    "title": item["title"],
                    "url": item["url"],
                    "reason": item.get("reason", ""),
                }
                for item in filtered_items
                if item.get("relevant", False)
            ]

            logger.info(f"     {city}: {count} → {len(relevant)} relevant results")
            city_data["results"] = relevant

        except json.JSONDecodeError as e:
            logger.error(f"  AI returned invalid JSON for {city}: {e}")
            # Keep all results on failure — better safe than sorry

        except Exception as e:
            logger.error(f"  AI filtering failed for {city}: {e}")
            # Keep all results on failure

    return all_results
