"""
═══════════════════════════════════════════════════════════════
 TEAMS NOTIFIER — Send Results to Microsoft Teams
═══════════════════════════════════════════════════════════════
 Two audiences, two cards:

   RESULTS card  → TEAMS_WEBHOOK_URL, the channel the whole team reads.
                   Results only. Cities that could not be checked are
                   listed neutrally ("Not checked today: …") — no error
                   text, no stack traces, nothing that looks broken.

   DIAGNOSTICS   → TEAMS_DEV_WEBHOOK_URL, a private dev channel.
                   Per-city outcome, HTTP status, error category, what
                   it means and what to do. Falls back to the run log
                   when the dev webhook is not configured.

 If there are too many results for a single card, the message is
 automatically split into several cards ("Part 1 of 2", ...).
═══════════════════════════════════════════════════════════════
"""

import asyncio
import logging
from datetime import datetime
import httpx

from config import TEAMS_WEBHOOK_URL, TEAMS_DEV_WEBHOOK_URL, TODAY_DE, KNOWN_ISSUES
from outcomes import OK, EMPTY, UNVERIFIED, ERROR, describe

logger = logging.getLogger("council-monitor.teams")

# Keep each Adaptive Card comfortably under Teams' size limit.
MAX_BLOCKS_PER_CARD = 90


async def send_to_teams(all_results: list):
    """Send the RESULTS card(s) to the team channel."""
    if not TEAMS_WEBHOOK_URL:
        logger.error("⚠ No Teams webhook URL configured — skipping notification")
        _print_results_to_console(all_results)
        return

    ok = await _post_cards(TEAMS_WEBHOOK_URL, _build_cards(all_results), "results")
    if not ok:
        _print_results_to_console(all_results)


async def send_diagnostics(all_results: list, force_console: bool = False):
    """Send the DIAGNOSTICS card to the private dev channel, or log it."""
    if force_console or not TEAMS_DEV_WEBHOOK_URL:
        if not TEAMS_DEV_WEBHOOK_URL and not force_console:
            logger.info("  ℹ TEAMS_DEV_WEBHOOK_URL not set — diagnostics go to this log only")
        _print_diagnostics_to_console(all_results)
        return

    ok = await _post_cards(TEAMS_DEV_WEBHOOK_URL, [_build_diagnostics_card(all_results)], "diagnostics")
    if not ok:
        _print_diagnostics_to_console(all_results)


async def _post_cards(webhook_url: str, cards: list, label: str) -> bool:
    """POST each Adaptive Card to a webhook. Returns True if all were accepted."""
    all_ok = True
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
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                if response.status_code in (200, 202):
                    logger.info(f"  ✅ Teams {label} message {idx}/{len(cards)} sent!")
                else:
                    all_ok = False
                    logger.error(
                        f"  ✗ Teams ({label}) returned HTTP {response.status_code}: {response.text}"
                    )
                await asyncio.sleep(1)  # small pause to avoid throttling
    except Exception as e:
        all_ok = False
        logger.error(f"  ✗ Failed to send Teams {label} notification: {e}")
    return all_ok


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
    empty = [r for r in all_results if r.get("outcome") == EMPTY
             or (not r["results"] and not r.get("error") and not r.get("outcome"))]
    # Everything that did not produce a trustworthy answer. Shown to the
    # team as "not checked" — the reasons live in the diagnostics card.
    not_checked = [r for r in all_results
                   if r.get("outcome") in (UNVERIFIED, ERROR) or r.get("error")]
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
                {"type": "TextBlock", "text": f"🔕 **{len(not_checked)}** not checked", "wrap": True,
                 "isSubtle": True}]},
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
    if not_checked:
        # Neutral wording on purpose: a city we could not check is not
        # "no news from that city", and it is not something the wider
        # team needs to act on. Details go to the dev channel.
        names = ", ".join(r["city"] for r in not_checked)
        tail.append({
            "type": "TextBlock",
            "text": f"🔕 **Not checked today:** {names}",
            "spacing": "Medium",
            "wrap": True,
            "isSubtle": True,
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


def _build_diagnostics_card(all_results: list) -> dict:
    """The DEV card: one line per healthy city, a block per problem city
    with category, HTTP status, meaning and next step."""
    problems = [r for r in all_results if r.get("outcome") in (UNVERIFIED, ERROR)]
    healthy = [r for r in all_results if r not in problems]
    icon = {OK: "✅", EMPTY: "⬜", UNVERIFIED: "❓", ERROR: "❌"}

    body = [{
        "type": "TextBlock",
        "text": f"🛠️ Council Monitor diagnostics — {TODAY_DE}",
        "weight": "Bolder",
        "size": "Large",
        "wrap": True,
    }, {
        "type": "TextBlock",
        "text": (f"{len(healthy)} healthy · {len(problems)} problem(s) · "
                 f"{sum(1 for r in problems if r['city'] in KNOWN_ISSUES)} of them known issues"),
        "isSubtle": True,
        "wrap": True,
    }]

    for r in problems:
        cat = r.get("error_category") or ERROR
        meaning, action = describe(cat)
        known = " · known issue" if r["city"] in KNOWN_ISSUES else ""
        http = f" · HTTP {r['http_status']}" if r.get("http_status") else ""
        body.append({
            "type": "TextBlock",
            "text": f"{icon[r['outcome']]} **{r['city']}** — {r['outcome']} · {cat}{http} · {r.get('duration_s', '')}s{known}",
            "wrap": True,
            "spacing": "Medium",
            "color": "Default" if r["city"] in KNOWN_ISSUES else "Attention",
        })
        body.append({
            "type": "TextBlock",
            "text": f"_{meaning}_ **Next:** {action}",
            "wrap": True,
            "spacing": "None",
            "size": "Small",
        })
        body.append({
            "type": "TextBlock",
            "text": (r.get("error") or "")[:300],
            "wrap": True,
            "spacing": "None",
            "size": "Small",
            "isSubtle": True,
            "fontType": "Monospace",
        })

    if healthy:
        line = " · ".join(f"{icon[r['outcome']]} {r['city']} ({len(r['results'])})" for r in healthy)
        body.append({
            "type": "TextBlock",
            "text": f"**Healthy:** {line}",
            "wrap": True,
            "spacing": "Medium",
            "size": "Small",
        })

    body.append({
        "type": "TextBlock",
        "text": "_Full table and screenshots: the GitHub Actions run page → Summary + artifacts._",
        "isSubtle": True,
        "size": "Small",
        "spacing": "Medium",
        "wrap": True,
    })
    return _wrap_card(body, 1, 1)


def _print_diagnostics_to_console(all_results: list):
    """Diagnostics fallback: the same information, in the run log."""
    problems = [r for r in all_results if r.get("outcome") in (UNVERIFIED, ERROR)]
    logger.info("  🛠️ DIAGNOSTICS:")
    if not problems:
        logger.info("     all cities healthy")
    for r in problems:
        cat = r.get("error_category") or ERROR
        meaning, action = describe(cat)
        known = " (known issue)" if r["city"] in KNOWN_ISSUES else ""
        http = f", HTTP {r['http_status']}" if r.get("http_status") else ""
        logger.info(f"     {r['outcome'].upper()} {r['city']}{known}: {cat}{http}, {r.get('duration_s', '')}s")
        logger.info(f"        {meaning} Next: {action}")


def _print_results_to_console(all_results: list):
    """Fallback: print results to the log if Teams is not configured."""
    logger.info("  📋 RESULTS (printed to console because Teams is not configured):")
    for city_data in all_results:
        city = city_data["city"]
        outcome = city_data.get("outcome")
        if outcome in (UNVERIFIED, ERROR) or city_data.get("error"):
            logger.info(f"     {'❓' if outcome == UNVERIFIED else '❌'} {city}: {outcome or 'error'} — {city_data.get('error')}")
        elif city_data["results"]:
            logger.info(f"     ✅ {city}: {len(city_data['results'])} result(s)")
            for r in city_data["results"]:
                logger.info(f"        • {r['title']}")
                logger.info(f"          {r['url']}")
        else:
            logger.info(f"     ⬜ {city}: Empty (verified)")
