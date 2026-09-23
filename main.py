#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
 GERMAN COUNCIL INFORMATION SYSTEM MONITOR
═══════════════════════════════════════════════════════════════
 Automatically searches 19 German city council websites for
 mobility-related policy documents and posts results to
 Microsoft Teams.

 Usage:
   python main.py                   Run normally (all cities)
   python main.py --debug           Also save screenshots of every step
   python main.py --city Cologne    Run for one city only
   python main.py --no-llm          Skip AI filtering (also off unless ENABLE_AI_FILTER=true)
   python main.py --no-teams        Skip Teams (print to console) — use this when testing

 Every run writes run-summary.md and run-results.json, and saves a
 screenshot + HTML snapshot of each failed city under debug/.
═══════════════════════════════════════════════════════════════
"""

import asyncio
import argparse
import logging
import sys

# Load secrets from .env file (for local development only)
from dotenv import load_dotenv
load_dotenv()

from config import CITIES, KEYWORDS, TODAY_DE, ENABLE_AI_FILTER
from scraper import run_all_scrapers
from llm_filter import filter_results
from teams_notify import send_to_teams, send_diagnostics
from report import summarise, unexpected_failures, run_failed, write_artifacts
from outcomes import OK, EMPTY, UNVERIFIED, ERROR

# ─── Set up logging ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("council-monitor")


async def main():
    # ── Parse command-line arguments ──
    parser = argparse.ArgumentParser(
        description="German Council Information System Monitor"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Save browser screenshots for debugging selectors"
    )
    parser.add_argument(
        "--city", type=str,
        help="Run for a single city only (e.g., --city Cologne)"
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip AI filtering (keep all raw results)"
    )
    parser.add_argument(
        "--no-teams", action="store_true",
        help="Skip Teams notification (print to console instead)"
    )
    args = parser.parse_args()

    # ── Startup banner ──
    logger.info("═" * 55)
    logger.info("🏛️  GERMAN COUNCIL MONITOR")
    logger.info(f"   Date: {TODAY_DE}")
    logger.info(f"   Cities: {len(CITIES)} configured")
    logger.info(f"   Keywords: {len(KEYWORDS)}")
    logger.info("═" * 55)

    # ── Select which cities to search ──
    cities = CITIES
    if args.city:
        cities = [
            c for c in CITIES
            if args.city.lower() in c["name"].lower()
        ]
        if not cities:
            available = ", ".join(c["name"] for c in CITIES)
            logger.error(f"City '{args.city}' not found.\nAvailable: {available}")
            sys.exit(1)
        logger.info(f"   Filtered to: {[c['name'] for c in cities]}")

    # ═══════════════════════════════════════════════════════
    # STEP 1: SCRAPE all city council websites
    # ═══════════════════════════════════════════════════════
    logger.info("")
    logger.info("━" * 55)
    logger.info("STEP 1 │ Scraping city council websites...")
    logger.info("━" * 55)
    results = await run_all_scrapers(cities, debug=args.debug)

    # ═══════════════════════════════════════════════════════
    # STEP 2: FILTER results with AI
    # Off unless ENABLE_AI_FILTER=true (config.py) — the team is
    # reviewing unfiltered results until the filter is validated.
    # ═══════════════════════════════════════════════════════
    has_any_results = any(r["results"] for r in results)

    if ENABLE_AI_FILTER and not args.no_llm and has_any_results:
        logger.info("")
        logger.info("━" * 55)
        logger.info("STEP 2 │ Filtering with AI...")
        logger.info("━" * 55)
        results = await filter_results(results)
    elif args.no_llm:
        logger.info("\n⏭️  Skipping AI filter (--no-llm flag)")
    elif not ENABLE_AI_FILTER:
        logger.info("\n⏭️  Skipping AI filter (ENABLE_AI_FILTER is not set)")
    else:
        logger.info("\n⏭️  Skipping AI filter (no results to filter)")

    # ═══════════════════════════════════════════════════════
    # STEP 3: REPORT — summary for the run page + saved artifacts
    # ═══════════════════════════════════════════════════════
    write_artifacts(results, TODAY_DE)

    # ═══════════════════════════════════════════════════════
    # STEP 4: SEND to Microsoft Teams
    #   • results card   → the channel the whole team reads
    #   • diagnostics    → the private dev channel (or the log)
    # ═══════════════════════════════════════════════════════
    if not args.no_teams:
        logger.info("")
        logger.info("━" * 55)
        logger.info("STEP 4 │ Sending to Microsoft Teams...")
        logger.info("━" * 55)
        await send_to_teams(results)
        await send_diagnostics(results)
    else:
        logger.info("\n⏭️  Skipping Teams (--no-teams flag)")
        # Still print to console
        from teams_notify import _print_results_to_console
        _print_results_to_console(results)
        await send_diagnostics(results, force_console=True)

    # ═══════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════
    c = summarise(results)
    unexpected = unexpected_failures(results)
    logger.info("")
    logger.info("═" * 55)
    logger.info(
        f"✅ DONE │ {c['links']} results │ {c[OK]} ok │ {c[EMPTY]} empty │ "
        f"{c[UNVERIFIED]} unverified │ {c[ERROR]} errors"
    )
    if unexpected:
        logger.info(f"   Unexpected failures: {', '.join(r['city'] for r in unexpected)}")
    logger.info("═" * 55)

    # Mark the run failed when too many cities failed for reasons we did
    # not already know about (see MAX_UNEXPECTED_FAILURES / KNOWN_ISSUES).
    if run_failed(results):
        logger.error("Run marked FAILED: too many unexpected failures — see run-summary.md")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
