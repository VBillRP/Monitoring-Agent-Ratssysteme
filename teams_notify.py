"""
═══════════════════════════════════════════════════════════════
 TEAMS NOTIFIER — Send Results to Microsoft Teams
═══════════════════════════════════════════════════════════════
 Posts one or more Adaptive Cards to a Teams channel via webhook.
 If there are too many results for a single card, the message is
 automatically split into several cards ("Part 1 of 2", ...).
═══════════════════════════════════════════════════════════════
"""

import asyncio
import logging
from datetime import datetime
import httpx

from config import TEAMS_WEBHOOK_URL, TODAY_DE

logger = logging.getLogger("council-monitor.teams")

# Keep each Adaptive Card comfortably under Teams' size limit.
MAX_BLOCKS_PER_CARD = 90


async def send_to_teams(all_results: list):
    """Send the monitoring results to Microsoft Teams (splits into
    several messages if there are too many results for one card)."""
    if not TEAMS_WEBHOOK_URL:
        logger.error("⚠ No Teams webhook URL configured — skipping notification")
        _print_results_to_console(all_results)
        return

    cards = _build_cards(all_results)

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            for idx, card in enumerate(cards, 1):
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
                response = await http.post(
                    TEAMS_WEBHOOK_URL,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                if response.status_code in (200, 202):
                    logger.info(f"  ✅ Teams message {idx}/{len(cards)} sent!")
                else:
                    logger.error(
                        f"  ✗ Teams returned HTTP {response.status_code}: {response.text}"
                    )
                await asyncio.sleep(1)  # small pause to avoid throttling

    except Exception as e:
        logger.error(f"  ✗ Failed to send Teams notification: {e}")
        _print_results_to_console(all_results)


def _city_block(city_data: dict) -> list:
    """All TextBlocks for one city — EVERY result, no 15-item cap."""
    n = len(city_data["results"])
    blocks = [{
        "type": "TextBlock",
        "text": f"**{city_data['city']}** — {n} result(s)",
        "spacing": "Medium",
        "wrap": True,
    }]
    for result in city_data["results"]:
        title = result["title"][:120]
        url = result["url"]
        reason = result.get("reason", "")
        reason_text = f" _({reason})_" if reason else ""
        blocks.append({
            "type": "TextBlock",
            "text": f"• [{title}]({url}){reason_text}",
            "wrap": True,
            "spacing": "None",
            "size": "Small",
        })
    return blocks


def _wrap_card(body: list, part: int, total: int) -> dict:
    if total > 1:
        body = [{
            "type": "TextBlock",
            "text": f"_(Part {part} of {total})_",
            "isSubtle": True,
            "size": "Small",
        }] + body
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }


def _build_cards(all_results: list) -> list:
    """Build one or more Adaptive Cards. Cities with results are split
    across several cards if they don't fit into one."""
    with_results = [r for r in all_results if r["results"]]
    empty = [r for r in all_results if not r["results"] and not r.get("error")]
    failed = [r for r in all_results if r.get("error")]
    total = sum(len(r["results"]) for r in all_results)

    # ── Header + summary ──
    head = [{
        "type": "TextBlock",
        "text": f"🏛️ German Council Monitor — {TODAY_DE}",
        "weight": "Bolder",
        "size": "Large",
        "wrap": True,
    }, {
        "type": "ColumnSet",
        "columns": [
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "text": f"📄 **{total}** results", "wrap": True}]},
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "text": f"✅ **{len(with_results)}** cities", "wrap": True}]},
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "text": f"⬜ **{len(empty)}** empty", "wrap": True}]},
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "text": f"❌ **{len(failed)}** errors", "wrap": True,
                 "color": "Attention" if failed else "Default"}]},
        ],
    }]
    if with_results:
        head.append({
            "type": "TextBlock",
            "text": "✅ CITIES WITH RESULTS",
            "weight": "Bolder",
            "size": "Medium",
            "spacing": "Medium",
            "color": "Good",
        })

    header_len = len(head)

    # ── Fill cards, splitting when a card gets too big ──
    pages = []
    current = list(head)
    for city_data in with_results:
        block = _city_block(city_data)
        if len(current) + len(block) > MAX_BLOCKS_PER_CARD and len(current) > header_len:
            pages.append(current)
            current = []
        current.extend(block)

    # ── Tail: empty cities, errors, footer (on the last card) ──
    tail = []
    if empty:
        names = ", ".join(r["city"] for r in empty)
        tail.append({
            "type": "TextBlock",
            "text": f"⬜ **EMPTY:** {names}",
            "spacing": "Medium",
            "wrap": True,
            "isSubtle": True,
        })
    if failed:
        tail.append({
            "type": "TextBlock",
            "text": "❌ ERRORS",
            "weight": "Bolder",
            "spacing": "Medium",
            "color": "Attention",
        })
        for city_data in failed:
            err = city_data["error"][:150]
            tail.append({
                "type": "TextBlock",
                "text": f"• **{city_data['city']}:** {err}",
                "wrap": True,
                "spacing": "None",
                "color": "Attention",
                "size": "Small",
            })
    tail.append({
        "type": "TextBlock",
        "text": f"_Completed at {datetime.now().strftime('%H:%M:%S')} CET_",
        "isSubtle": True,
        "spacing": "Large",
        "size": "Small",
        "horizontalAlignment": "Right",
    })

    if current and len(current) + len(tail) > MAX_BLOCKS_PER_CARD and len(current) > header_len:
        pages.append(current)
        current = []
    current.extend(tail)
    pages.append(current)

    total_parts = len(pages)
    return [_wrap_card(body, i + 1, total_parts) for i, body in enumerate(pages)]


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
