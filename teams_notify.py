"""
═══════════════════════════════════════════════════════════════
 TEAMS NOTIFIER — Send Results to Microsoft Teams
═══════════════════════════════════════════════════════════════
 Posts an Adaptive Card to a Teams channel via webhook.
 The card shows:
   • Cities with results (with clickable links)
   • Cities that were empty
   • Cities that had errors
═══════════════════════════════════════════════════════════════
"""

import logging
from datetime import datetime
import httpx

from config import TEAMS_WEBHOOK_URL, TODAY_DE

logger = logging.getLogger("council-monitor.teams")


async def send_to_teams(all_results: list):
    """Send the monitoring results to Microsoft Teams."""
    if not TEAMS_WEBHOOK_URL:
        logger.error("⚠ No Teams webhook URL configured — skipping notification")
        _print_results_to_console(all_results)
        return

    card = _build_card(all_results)

    # Wrap the card in the required message format
    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.post(
                TEAMS_WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

        if response.status_code in (200, 202):
            logger.info("  ✅ Teams notification sent successfully!")
        else:
            logger.error(f"  ✗ Teams returned HTTP {response.status_code}: {response.text}")

    except Exception as e:
        logger.error(f"  ✗ Failed to send Teams notification: {e}")
        _print_results_to_console(all_results)


def _build_card(all_results: list) -> dict:
    """Build an Adaptive Card with the monitoring results."""

    # Categorize results
    with_results = [r for r in all_results if r["results"]]
    empty = [r for r in all_results if not r["results"] and not r.get("error")]
    failed = [r for r in all_results if r.get("error")]
    total = sum(len(r["results"]) for r in all_results)

    # ── Card body ──
    body = []

    # Header
    body.append({
        "type": "TextBlock",
        "text": f"🏛️ German Council Monitor — {TODAY_DE}",
        "weight": "Bolder",
        "size": "Large",
        "wrap": True,
    })

    # Summary statistics
    body.append({
        "type": "ColumnSet",
        "columns": [
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "text": f"📄 **{total}** results", "wrap": True}
            ]},
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "text": f"✅ **{len(with_results)}** cities", "wrap": True}
            ]},
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "text": f"⬜ **{len(empty)}** empty", "wrap": True}
            ]},
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "text": f"❌ **{len(failed)}** errors", "wrap": True,
                 "color": "Attention" if failed else "Default"}
            ]},
        ],
    })

    # Separator
    body.append({
        "type": "TextBlock",
        "text": " ",
        "spacing": "Small",
        "separator": True,
    })

    # ── Cities WITH results ──
    if with_results:
        body.append({
            "type": "TextBlock",
            "text": "✅ CITIES WITH RESULTS",
            "weight": "Bolder",
            "size": "Medium",
            "spacing": "Medium",
            "color": "Good",
        })

        for city_data in with_results:
            n = len(city_data["results"])
            body.append({
                "type": "TextBlock",
                "text": f"**{city_data['city']}** — {n} result(s)",
                "spacing": "Medium",
                "wrap": True,
            })

            # Show up to 15 results per city
            for result in city_data["results"][:15]:
                title = result["title"][:120]
                url = result["url"]
                reason = result.get("reason", "")
                reason_text = f" _({reason})_" if reason else ""

                body.append({
                    "type": "TextBlock",
                    "text": f"• [{title}]({url}){reason_text}",
                    "wrap": True,
                    "spacing": "None",
                    "size": "Small",
                })

            if n > 15:
                body.append({
                    "type": "TextBlock",
                    "text": f"_…and {n - 15} more results_",
                    "isSubtle": True,
                    "spacing": "None",
                    "size": "Small",
                })

    # ── EMPTY cities ──
    if empty:
        names = ", ".join(r["city"] for r in empty)
        body.append({
            "type": "TextBlock",
            "text": f"⬜ **EMPTY:** {names}",
            "spacing": "Medium",
            "wrap": True,
            "isSubtle": True,
        })

    # ── FAILED cities ──
    if failed:
        body.append({
            "type": "TextBlock",
            "text": "❌ ERRORS",
            "weight": "Bolder",
            "spacing": "Medium",
            "color": "Attention",
        })
        for city_data in failed:
            err = city_data["error"][:150]
            body.append({
                "type": "TextBlock",
                "text": f"• **{city_data['city']}:** {err}",
                "wrap": True,
                "spacing": "None",
                "color": "Attention",
                "size": "Small",
            })

    # ── Footer with timestamp ──
    body.append({
        "type": "TextBlock",
        "text": f"_Completed at {datetime.now().strftime('%H:%M:%S')} CET_",
        "isSubtle": True,
        "spacing": "Large",
        "size": "Small",
        "horizontalAlignment": "Right",
    })

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }


def _print_results_to_console(all_results: list):
    """Fallback: print results to the log if Teams is not configured."""
    logger.info("  📋 RESULTS (printed to console because Teams is not configured):")
    for city_data in all_results:
        city = city_data["city"]
        if city_data.get("error"):
            logger.info(f"     ❌ {city}: ERROR — {city_data['error']}")
        elif city_data["results"]:
            logger.info(f"     ✅ {city}: {len(city_data['results'])} result(s)")
            for r in city_data["results"]:
                logger.info(f"        • {r['title']}")
                logger.info(f"          {r['url']}")
        else:
            logger.info(f"     ⬜ {city}: Empty")
