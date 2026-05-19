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
   python main.py --debug           Save screenshots for debugging
   python main.py --city Cologne    Run for one city only
   python main.py --no-llm          Skip AI filtering
   python main.py --no-teams        Skip Teams (print to console)
═══════════════════════════════════════════════════════════════
"""

import asyncio
import argparse
import logging
import sys

# Load secrets from .env file (for local development only)
from dotenv import load_dotenv
load_dotenv()

from config import CITIES, KEYWORDS, TODAY_DE
from scraper import run_all_scrapers
from llm_filter import filter_results
from teams_notify import send_to_teams

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
    # ═══════════════════════════════════════════════════════
    has_any_results = any(r["results"] for r in results)

    if not args.no_llm and has_any_results:
        logger.info("")
        logger.info("━" * 55)
        logger.info("STEP 2 │ Filtering with AI...")
        logger.info("━" * 55)
        results = await filter_results(results)
    else:
        if args.no_llm:
            logger.info("\n⏭️  Skipping AI filter (--no-llm flag)")
        else:
            logger.info("\n⏭️  Skipping AI filter (no results to filter)")

    # ═══════════════════════════════════════════════════════
    # STEP 3: SEND to Microsoft Teams
    # ═══════════════════════════════════════════════════════
    if not args.no_teams:
        logger.info("")
        logger.info("━" * 55)
        logger.info("STEP 3 │ Sending to Microsoft Teams...")
        logger.info("━" * 55)
        await send_to_teams(results)
    else:
        logger.info("\n⏭️  Skipping Teams (--no-teams flag)")
        # Still print to console
        from teams_notify import _print_results_to_console
        _print_results_to_console(results)

    # ═══════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════
    logger.info("")
    logger.info("═" * 55)
    total_results = sum(len(r["results"]) for r in results)
    total_errors = sum(1 for r in results if r.get("error"))
    total_empty = sum(1 for r in results if not r["results"] and not r.get("error"))
    logger.info(f"✅ DONE │ {total_results} results │ {total_empty} empty │ {total_errors} errors")
    logger.info("═" * 55)

    # Return non-zero exit code if all cities failed
    if total_errors == len(cities):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
