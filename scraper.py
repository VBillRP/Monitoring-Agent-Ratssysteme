
"""
═══════════════════════════════════════════════════════════════
 SCRAPER — Browser Automation for City Council Websites
═══════════════════════════════════════════════════════════════
 Uses Playwright (a headless browser) to:
   1. Visit each city's search page
   2. Fill in keywords and dates
   3. Click search
   4. Collect the results

 Each city TYPE has its own handler function because the
 websites have different layouts and interaction flows.
═══════════════════════════════════════════════════════════════
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin
from playwright.async_api import async_playwright, Page

from config import KEYWORDS, TODAY_DE, TODAY_ISO, YESTERDAY_DE, YESTERDAY_ISO
from outcomes import (
    OK, EMPTY, UNVERIFIED, ERROR,
    BLOCKED, FIELD_NOT_FOUND, LAYOUT_CHANGED,
    ScrapeError, UnverifiedSearch, classify,
)

logger = logging.getLogger("council-monitor.scraper")

# ─── Timing settings ─────────────────────────────────────
DELAY_BETWEEN_CITIES = 3       # Seconds to wait between cities
DELAY_BETWEEN_KEYWORDS = 1.5   # Seconds between individual keyword searches
PAGE_SETTLE_MS = 2000          # Milliseconds to let a page finish loading
PAGE_TIMEOUT_MS = 30000        # Max milliseconds before giving up on a page
SUBMIT_VERIFY_S = 10           # Seconds to wait for the page to react to Search

# Where screenshots + page snapshots of failed cities are written.
# The workflow uploads this folder as an artifact after every run.
DEBUG_DIR = "debug"

# Submit-button selectors shared by every SessionNet-style handler.
SESSIONNET_SUBMIT = [
    'input[name="go"]',                        # Current SessionNet submit
    'input[type="submit"][value*="uch" i]',    # "Suchen" or "suchen"
    'button[type="submit"]:has-text("uch")',
    'input[name*="submit" i][value*="uch" i]',
    'input[name="smcsubmitrecherche"]',
    'input[type="submit"]',
    'button[type="submit"]',
]

# Stealth-Skript: versteckt die verraeterischsten Automatisierungs-Signale.
# Wird VOR dem Laden jeder Seite ausgefuehrt, wenn ein Handler es anfordert.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['de-DE', 'de', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = { runtime: {} };
"""

# ═══════════════════════════════════════════════════════════
#                    MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════

async def run_all_scrapers(cities: list, debug: bool = False) -> list:
    """
    Scrape all configured cities. Returns a list of dicts:
    [
      {
        "city": "Bielefeld",
        "url": "https://...",
        "type": "standard",
        "results": [{"title": "...", "url": "..."}, ...],
        "outcome": "ok" | "empty" | "unverified" | "error",
        "error": None or "error message",
        "error_category": None or "timeout" | "blocked" | ... (see outcomes.py),
        "http_status": None or the status of the last page navigation,
        "duration_s": 12.3,
        "timestamp": "2026-05-06T09:00:00"
      },
      ...
    ]
    """
    all_results = []
    os.makedirs(DEBUG_DIR, exist_ok=True)

    async with async_playwright() as pw:
        # Launch a headless (invisible) Chrome browser
        browser = await pw.chromium.launch(headless=True)

        # Create a browser context that looks like a normal German user
        context = await browser.new_context(
            locale="de-DE",
            timezone_id="Europe/Berlin",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )

        for city in cities:
            logger.info(f"  🔍 {city['name']}...")
            page = await context.new_page()
            page.set_default_timeout(city.get("timeout_ms", PAGE_TIMEOUT_MS))

            # Remember the HTTP status of the last top-level navigation so an
            # error can be classified (403/503 = blocked, 404 = moved, ...).
            nav_status = {"code": None}

            def _on_response(resp, _status=nav_status, _page=page):
                try:
                    if resp.request.is_navigation_request() and resp.frame == _page.main_frame:
                        _status["code"] = resp.status
                except Exception:
                    pass

            page.on("response", _on_response)

            record = {
                "city": city["name"],
                "url": city["url"],
                "type": city["type"],
                "results": [],
                "outcome": ERROR,
                "error": None,
                "error_category": None,
                "http_status": None,
                "duration_s": 0.0,
                "timestamp": datetime.now().isoformat(),
            }
            started = time.monotonic()

            try:
                # Pick the right scraper function for this city type
                handler = _SCRAPER_MAP[city["type"]]
                results = await handler(page, city, debug)

                record["results"] = results
                record["outcome"] = OK if results else EMPTY
                count = len(results)
                logger.info(f"     → {count} result(s)" if count else "     → Empty (verified)")

            except Exception as e:
                page_text = await _safe_body_text(page)
                category = classify(e, nav_status["code"], page_text)
                record["outcome"] = UNVERIFIED if category == UNVERIFIED else ERROR
                record["error"] = str(e)
                record["error_category"] = category
                label = "UNVERIFIED" if record["outcome"] == UNVERIFIED else "ERROR"
                logger.error(f"     ✗ {label} [{category}]: {e}")

                # Always keep evidence of a failure — it is what you need to fix it.
                await _save_evidence(page, city["name"])

            finally:
                record["http_status"] = nav_status["code"]
                record["duration_s"] = round(time.monotonic() - started, 1)
                all_results.append(record)
                await page.close()
                await asyncio.sleep(DELAY_BETWEEN_CITIES)

        await browser.close()

    return all_results


def _safe_filename(name: str) -> str:
    """'Düsseldorf' -> 'Duesseldorf' so files are safe on every OS."""
    name = (name.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
                .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue").replace("ß", "ss"))
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name)


async def _save_evidence(page: Page, city_name: str):
    """Screenshot + HTML snapshot of the page as it was when the city failed.
    The HTML is the more useful of the two: it shows the real field names
    when a city has changed its search form."""
    base = os.path.join(DEBUG_DIR, _safe_filename(city_name))
    try:
        await page.screenshot(path=f"{base}.png", full_page=True)
    except Exception as e:
        logger.debug(f"     (no screenshot for {city_name}: {e})")
    try:
        html = await _safe_content(page)
        if html:
            with open(f"{base}.html", "w", encoding="utf-8") as f:
                f.write(html)
    except Exception as e:
        logger.debug(f"     (no HTML snapshot for {city_name}: {e})")


# ═══════════════════════════════════════════════════════════
#                    HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

async def _dismiss_cookies(page: Page):
    """
    Many German sites show a GDPR cookie banner.
    This tries to  "Accept" so we can access the actual page.
    """
    accept_texts = [
        "Alle akzeptieren", "Akzeptieren", "Zustimmen",
        "Alle annehmen", "OK", "Verstanden", "Accept",
    ]
    for text in accept_texts:
        try:
            btn = page.get_by_role("button", name=text)
            if await btn.is_visible(timeout=1500):
                await btn.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def _try_fill(page: Page, selectors: list, value: str) -> bool:
    """
    Try a list of CSS selectors one by one until one works,
    then fill it with the given value.
    Returns True if successful, False if none worked.
    """
    for sel in selectors:
        try:
            elem = page.locator(sel).first
            if await elem.is_visible(timeout=1500):
                await elem.clear()
                await elem.fill(value)
                return True
        except Exception:
            continue
    return False


async def _try_fill_date(page: Page, selectors: list, de_value: str, iso_value: str) -> bool:
    """
    Fill a date field, formatting the value for the input type.

    Current SessionNet uses HTML5 <input type="date">, which only accepts
    ISO format (YYYY-MM-DD); filling German DD.MM.YYYY raises "Malformed
    value". Legacy text inputs still expect the German format.

    After filling a native date input we press Escape to dismiss the
    picker — otherwise it stays open and intercepts the click on the
    Search button, which manifests as "Could not find the Search button".
    """
    for sel in selectors:
        try:
            elem = page.locator(sel).first
            if await elem.is_visible(timeout=1500):
                input_type = (await elem.get_attribute("type") or "").lower()
                if input_type == "date":
                    await elem.fill(iso_value)
                    await elem.press("Escape")
                else:
                    await elem.clear()
                    await elem.fill(de_value)
                return True
        except Exception:
            continue
    return False


async def _try_click(page: Page, selectors: list) -> bool:
    """
    Try a list of CSS selectors one by one until one works,
    then click it. Returns True if successful.
    """
    for sel in selectors:
        try:
            elem = page.locator(sel).first
            if await elem.is_visible(timeout=1500):
                await elem.click()
                return True
        except Exception:
            continue
    return False


async def _safe_body_text(page: Page) -> str:
    """The page's visible text, or '' if the page is mid-navigation/closed."""
    for _ in range(3):
        try:
            return await page.inner_text("body")
        except Exception:
            await page.wait_for_timeout(500)
    return ""


async def _safe_content(page: Page) -> str:
    """page.content() that survives 'page is navigating' — the Frankfurt race.
    Waits for the network to go quiet first, then retries briefly."""
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    for _ in range(3):
        try:
            return await page.content()
        except Exception:
            await page.wait_for_timeout(700)
    return ""


async def _submit_search(page: Page, city_name: str, selectors: list,
                         enter_fallback: bool = False, enter_target: str = None):
    """
    Click the search button and PROVE the search ran.

    The old code clicked and hoped. If the click did nothing (wrong button,
    date-picker in the way, form validation), the unchanged form page went
    through the extractor, found no result links, and was reported as
    "Empty" — indistinguishable from "no news today". This helper compares
    the page's text before and after submitting and raises UnverifiedSearch
    when nothing changed, so that case is reported honestly.
    """
    before = await _safe_body_text(page)

    clicked = await _try_click(page, selectors)
    if not clicked:
        if enter_fallback:
            # Press Enter INSIDE the keyword field when we know it: Enter on
            # whatever happens to have focus (often a date field) may do nothing.
            if enter_target:
                await page.locator(enter_target).first.press("Enter")
            else:
                await page.keyboard.press("Enter")
        else:
            raise ScrapeError(f"{city_name}: search button not found", FIELD_NOT_FOUND)

    try:
        await page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    await page.wait_for_timeout(PAGE_SETTLE_MS)

    # Some sites (Stuttgart's AllRIS, Wicket) render the result list a few
    # seconds after the click. Poll for the change instead of judging after
    # a single fixed pause — fast sites are detected on the first check.
    deadline = time.monotonic() + SUBMIT_VERIFY_S
    while True:
        after = await _safe_body_text(page)
        if after.strip() != before.strip():
            return
        if time.monotonic() >= deadline:
            break
        await page.wait_for_timeout(1000)

    raise UnverifiedSearch(
        f"{city_name}: page did not change within {SUBMIT_VERIFY_S}s of clicking "
        "Search — the search probably never ran"
    )


async def _extract_results(page: Page, base_url: str, strict: bool = False,
                           strategies: list = None) -> list:
    """
    Generic result extractor for SessionNet / AllRIS pages.

    strict=True  → for Berlin: ONLY read from real result containers,
                   never from generic page/nav links, and drop known
                   menu/navigation entries.
    strategies   → an explicit list of CSS selectors for THIS site's result
                   links (from the city's "result_selectors" in config.py).
                   Use it when the generic selectors below find nothing on a
                   page that clearly has results — e.g. Munich's Wicket UI.
    """
    results = []

    # ── Check if the page says "no results found" ──
    try:
        body_text = await page.inner_text("body")
    except Exception:
        return []

    no_result_phrases = [
        "keine ergebnisse", "keine treffer", "es wurden keine",
        "0 ergebnisse", "0 treffer", "nichts gefunden",
        "kein ergebnis", "no results",
    ]
    body_lower = body_text.lower()
    for phrase in no_result_phrases:
        if phrase in body_lower:
            return []

    # ── Result-container selectors (specific → generic) ──
    specific_strategies = [
        'table.smccontenttable a[href]',
        'table.smclisttable a[href]',
        '.smclistbody a[href]',
        '#smcresult a[href]',
        'table.tl1 a[href]',
        'table.tk1 a[href]',
        '#applicationcontent a[href]',
        'table a[href*="vo020"]',
        'table a[href*="to020"]',
        'table a[href*="si010"]',
        'table a[href*="vo0050"]',
        '.resultlist a[href]',
        '.search-results a[href]',
        'ul.results a[href]',
    ]
    # These grab EVERYTHING on the page (incl. nav) — only used
    # for the normal cities, never in strict mode.
    last_resort_strategies = [
        '#main a[href]',
        '#content a[href]',
        'table a[href]',
    ]

    if strategies:
        link_strategies = list(strategies)
    elif strict:
        link_strategies = specific_strategies
    else:
        link_strategies = specific_strategies + last_resort_strategies

    # Words that identify menu / navigation links (never real hits)
    nav_noise = [
        "impressum", "datenschutz", "kontakt", "sitemap", "login",
        "anmelden", "startseite", "home", "hilfe", "barrierefrei",
        "zur navigation", "zum inhalt", "erweiterte suche", "neue suche",
        "lobbyregister", "plenarsitzung", "wahlperiode",
        "schriftliche anfragen", "recherche ab", "dokumentenabruf",
        "dokumentation@", "@parlament", "drucksachennummern",
    ]

    for strategy in link_strategies:
        try:
            links = await page.locator(strategy).all()
            if not links:
                continue

            for link in links:
                try:
                    text = (await link.inner_text()).strip()
                    href = await link.get_attribute("href")

                    # Skip empty, very short, or non-links
                    if not text or not href or len(text) < 5:
                        continue
                    if href == "#" or href.startswith("javascript:") or href.startswith("mailto:"):
                        continue

                    # Skip navigation / menu entries
                    if any(w in text.lower() for w in nav_noise):
                        continue

                    full_url = href if href.startswith("http") else urljoin(base_url, href)

                    # In strict mode also drop links back to the search/browse portal
                    if strict and ("browse.tt.html" in full_url or full_url.rstrip("/") == base_url.rstrip("/")):
                        continue

                    results.append({"title": text, "url": full_url})

                except Exception:
                    continue

            if results:
                break  # Found results with this strategy, stop trying

        except Exception:
            continue

    # ── Remove duplicates (same URL) ──
    seen_urls = set()
    unique = []
    for r in results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            unique.append(r)

    return unique


# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE 1: STANDARD
#  Cities: Bielefeld, Dortmund, Münster, Nuremberg, Leipzig,
#          Mainz, Mannheim, Mönchengladbach, 
#          Heidelberg, Cologne
# ═══════════════════════════════════════════════════════════

async def _scrape_standard(page: Page, city: dict, debug: bool) -> list:
    """
    Standard process (majority of cities):
    1. Go to search page
    2. Enter all keywords (space-separated) into "Suchwort" field
    3. Select "ODER" (OR) radio button
    4. Set "Freigabe von" = today
    5. Set "bis" = today
    6. Click Search
    7. Collect results
    """
    await page.goto(
        city["url"],
        wait_until="domcontentloaded",              # wartet nicht auf alle Ressourcen
        timeout=city.get("timeout_ms", PAGE_TIMEOUT_MS),  # Leipzig: 60 s, sonst 30 s
    )
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    # All keywords as a single space-separated string
    keywords_str = " ".join(KEYWORDS)

    # ── Step 2: Enter keywords ──
    filled = await _try_fill(page, [
        'input[name="__swords"]',               # Current SessionNet field name
        'textarea[name="__swords"]',
        'input[name="smcsuchwoerter"]',         # Older SessionNet
        'textarea[name="smcsuchwoerter"]',
        '#smcsuchwoerter',
        'input[name*="uchwoerter" i]',          # Partial match
        'textarea[name*="uchwoerter" i]',
        'input[name*="volltext" i]',            # AllRIS variant
        'textarea[name*="volltext" i]',
        'input[name*="suchbegriff" i]',         # Another variant
    ], keywords_str)

    if not filled:
        # Fallback: find input by its visible label text
        try:
            field = page.get_by_label("Suchwort", exact=False).first
            await field.fill(keywords_str)
            filled = True
        except Exception:
            pass

    if not filled:
        raise ScrapeError("Could not find the keyword search field (Suchwort)", FIELD_NOT_FOUND)

    # ── Step 3: Select "ODER" (OR search) ──
    # Current SessionNet uses radio __sao with value="1" (UND) / "2" (ODER).
    oder_clicked = await _try_click(page, [
        'input[name="__sao"][value="2"]',       # Current SessionNet: 2 = ODER
        'input[type="radio"][value="ODER"]',
        'input[type="radio"][value="oder"]',
        'input[type="radio"][value="or"]',
        '#smcverknuepfungoder',
        'input[name="smcverknuepfung"][value="oder" i]',
    ])
    if not oder_clicked:
        # Try using the label
        try:
            await page.get_by_label("ODER", exact=True).check()
        except Exception:
            try:
                await page.locator('label:has-text("ODER")').click()
            except Exception:
                logger.warning(f"  Could not select ODER for {city['name']} — proceeding anyway")

    # ── Steps 4 & 5: Set date fields ──
    await _try_fill_date(page, [
        'input[name="__axxdat_full"]',          # Current SessionNet: date from
        'input[name="smcfreigabevon"]',
        'input[name*="freigabevon" i]',
        '#smcfreigabevon',
        'input[name*="datumvon" i]',
        'input[name*="von" i][size]',
   ], YESTERDAY_DE, YESTERDAY_ISO)

    await _try_fill_date(page, [
        'input[name="__exxdat_full"]',          # Current SessionNet: date to
        'input[name="smcfreigabebis"]',
        'input[name*="freigabebis" i]',
        '#smcfreigabebis',
        'input[name*="datumbis" i]',
        'input[name*="bis" i][size]',
    ], TODAY_DE, TODAY_ISO)

    if debug:
        await page.screenshot(path=f"{DEBUG_DIR}/{_safe_filename(city['name'])}_pre_search.png", full_page=True)

    # ── Step 6: Click Search — and confirm the page actually responded ──
    await _submit_search(page, city["name"], SESSIONNET_SUBMIT)

    if debug:
        await page.screenshot(path=f"{DEBUG_DIR}/{_safe_filename(city['name'])}_results.png", full_page=True)

    # ── Step 7: Extract results ──
    return await _extract_results(page, city["url"])


# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE 2: INDIVIDUAL (Munich)
#  Keywords are searched ONE AT A TIME — one search per word.
#  (Berlin used to share this handler; it now has its own, "berlin".)
# ═══════════════════════════════════════════════════════════

async def _scrape_individual(page: Page, city: dict, debug: bool) -> list:
    """Munich: search each keyword separately and merge the results."""
    all_results = []
    verified_searches = 0

    for i, keyword in enumerate(KEYWORDS):
        logger.info(f"       Keyword {i+1}/{len(KEYWORDS)}: {keyword}")

        try:
            await page.goto(city["url"], wait_until="domcontentloaded")
            await page.wait_for_timeout(PAGE_SETTLE_MS)
            await _dismiss_cookies(page)

            # The RiSI page (Wicket) has TWO search forms: a hidden navbar
            # quick-search (name="text") and the real one (name="suchtext").
            filled = await _try_fill(page, [
                'input[name="suchtext"]',
                'input[name*="such" i]',
                'input[name*="query" i]',
                'input[type="search"]',
                'input[type="text"]',
            ], keyword)
            if not filled:
                logger.warning(f"       No search field for '{keyword}' — skipped")
                continue

            # Native <input type="date"> needs ISO YYYY-MM-DD
            await _try_fill_date(page, [
                'input[name="von"]',
                'input[name*="von" i]',
                'input[name*="from" i]',
            ], YESTERDAY_DE, YESTERDAY_ISO)

            await _try_fill_date(page, [
                'input[name="bis"]',
                'input[name*="bis" i]',
                'input[name*="to" i]',
            ], TODAY_DE, TODAY_ISO)

            if debug:
                await page.screenshot(path=f"{DEBUG_DIR}/Munich_{i}_pre_search.png", full_page=True)

            # Submit the form that holds the keyword field — NOT the first
            # submit button on the page, which belongs to the hidden navbar
            # search and silently did nothing (that is why Munich was
            # "Empty" every day). Enter in the keyword field also works.
            await _submit_search(page, city["name"], [
                'form:has(input[name="suchtext"]) button[type="submit"]',
                'form:has(input[name*="such" i]) button[type="submit"]',
                'form:has(input[name*="such" i]) input[type="submit"]',
            ], enter_fallback=True, enter_target='input[name="suchtext"]')
            verified_searches += 1

            all_results.extend(await _extract_results(
                page, city["url"], strategies=city.get("result_selectors"),
            ))

        except Exception as e:
            logger.warning(f"       Keyword '{keyword}' failed: {e}")

        await asyncio.sleep(DELAY_BETWEEN_KEYWORDS)

    if verified_searches == 0:
        raise UnverifiedSearch(
            f"{city['name']}: none of the {len(KEYWORDS)} keyword searches "
            "could be confirmed to have run"
        )

    # Remove duplicates (same URL found by different keywords)
    seen = set()
    unique = []
    for r in all_results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    return unique


# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE 3: CLICK FIRST
#  City: Düsseldorf
#  Must click "Rechercheauswahl anzeigen" to reveal the
#  search form, then proceed as standard.
# ═══════════════════════════════════════════════════════════

async def _scrape_click_first(page: Page, city: dict, debug: bool) -> list:
    """
    Düsseldorf (SessionNet 'Sitzungsdienst Session').
    Das Bürgerinfo liegt in einem <iframe>; wir springen direkt auf die
    darin verlinkte Recherche-Seite (suchen01.asp) und suchen dort.
    DATUM: Der Standard-Ablauf hat Düsseldorfs Datumsfelder NICHT gesetzt
    (Screenshot: von=21.07., bis=20.07. – Zukunft + verdreht => 0 Treffer).
    Deshalb setzen wir von/bis hier explizit (ISO, native date inputs) und
    LESEN die Werte zurueck (KONTROLLE), damit 'Empty' nur bei nachweislich
    korrektem Zeitraum (von <= heute) gilt.
    von = gestern (Mo: Freitag), bis = heute.
    """
    von_iso = YESTERDAY_ISO
    bis_iso = TODAY_ISO

    # ── Step 1: Landeseite -> iframe -> echte Recherche-Adresse ──
    await page.goto(city["url"], wait_until="domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    reche_target = ""
    for f in page.frames:
        try:
            hrefs = await f.evaluate("""
            () => {
              const out = [];
              for (const el of document.querySelectorAll('a')) {
                const txt = (el.textContent || '').trim().toLowerCase();
                if (txt === 'recherche' && el.href) out.push(el.href);
              }
              return out;
            }
            """)
        except Exception:
            hrefs = []
        for h in hrefs:
            if not reche_target:
                reche_target = h

    if not reche_target:
        frame_srcs = [f.url for f in page.frames if f.url and f.url != page.url]
        if not frame_srcs:
            raise ScrapeError("Düsseldorf: kein iframe/Recherche-Link gefunden", LAYOUT_CHANGED)
        reche_target = frame_srcs[0].replace("info.asp", "suchen01.asp")

    logger.info(f"  Düsseldorf: Recherche-Seite -> {reche_target}")
    await page.goto(reche_target, wait_until="domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    # ── Step 2: Keywords ──
    keywords_str = " ".join(KEYWORDS)
    filled = await _try_fill(page, [
        'input[name="__swords"]',
        'textarea[name="__swords"]',
        'input[name*="uchwoerter" i]',
        'textarea[name*="uchwoerter" i]',
    ], keywords_str)
    if not filled:
        try:
            await page.get_by_label("Suchwort", exact=False).first.fill(keywords_str)
            filled = True
        except Exception:
            raise ScrapeError("Düsseldorf: Suchwort-Feld nicht gefunden", FIELD_NOT_FOUND)

    # ── Step 3: ODER ──
    await _try_click(page, [
        'input[name="__sao"][value="2"]',
        'input[type="radio"][value="2"]',
    ])
    try:
        await page.get_by_label("ODER", exact=True).check()
    except Exception:
        pass

    # ── Step 4: Datum robust setzen (Name zuerst, sonst positionsbasiert) ──
    async def _set_date(named_selectors, iso, date_index):
        for sel in named_selectors:
            try:
                elem = page.locator(sel).first
                if await elem.is_visible(timeout=1000):
                    await elem.fill(iso)
                    await elem.press("Escape")
                    val = await elem.input_value()
                    if val:
                        return val
            except Exception:
                continue
        try:
            elem = page.locator('input[type="date"]').nth(date_index)
            if await elem.is_visible(timeout=1000):
                await elem.fill(iso)
                await elem.press("Escape")
                return await elem.input_value()
        except Exception:
            pass
        return "(nicht gesetzt)"

    von_val = await _set_date(
        ['input[name="__axxdat_full"]', 'input[name="smcfreigabevon"]'],
        von_iso, 0)
    bis_val = await _set_date(
        ['input[name="__exxdat_full"]', 'input[name="smcfreigabebis"]'],
        bis_iso, 1)

    logger.info(f"  >> Düsseldorf KONTROLLE: von='{von_val}' (soll {von_iso}), "
                f"bis='{bis_val}' (soll {bis_iso})")

    if debug:
        await page.screenshot(path=f"{DEBUG_DIR}/Duesseldorf_pre_search.png", full_page=True)

    # ── Step 5: Suchen — und pruefen, dass die Seite reagiert hat ──
    await _submit_search(page, "Düsseldorf", SESSIONNET_SUBMIT)

    if debug:
        await page.screenshot(path=f"{DEBUG_DIR}/Duesseldorf_results.png", full_page=True)

    return await _extract_results(page, reche_target)


# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE 4: ESSEN
#  Keywords joined with " O " (the letter O) as OR separator
#  because the site has no OR button.
# ═══════════════════════════════════════════════════════════

async def _scrape_essen(page: Page, city: dict, debug: bool) -> list:
    """Essen (Sternberg SD.NET RIM): 'Recherche' form. Keyword box
    'Suchbegriffe', two native date inputs, button 'Anzeigen'. ' O ' = OR.
    Documents live under '/vorgang/' and '/tops/'. The clickable link is
    often an ICON with no text, so the TITLE comes from the table ROW."""
    await page.goto(city["url"], wait_until="domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    # ── Date window: yesterday .. today (Monday reaches back to Friday) ──
    von_iso = YESTERDAY_ISO
    bis_iso = TODAY_ISO
    logger.info(f"  Essen: date window {von_iso} .. {bis_iso}")

    keywords_str = " O ".join(KEYWORDS)

    # ── Keyword box: the FORM field ('Suchbegriffe'), not the sidebar box ──
    filled = False
    for sel in [
        'input[placeholder="Suchbegriffe"]',
        'input[name="suchbegriffe"]',
        '#suchbegriffe',
        'input[name*="begriff" i]',
    ]:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.fill(keywords_str)
                filled = True
                logger.info(f"  Essen: keyword field matched -> {sel}")
                break
        except Exception:
            continue
    if not filled:
        try:
            await page.get_by_label("Suchbegriffe", exact=False).first.fill(keywords_str)
            filled = True
        except Exception:
            pass
    if not filled:
        raise ScrapeError("Could not find keyword input for Essen", FIELD_NOT_FOUND)

    # ── Native date fields: first = von, second = bis (ISO) ──
    date_inputs = await page.locator('input[type="date"]').all()
    if len(date_inputs) >= 1:
        try:
            await date_inputs[0].fill(von_iso)
        except Exception:
            pass
    if len(date_inputs) >= 2:
        try:
            await date_inputs[1].fill(bis_iso)
        except Exception:
            pass

    if debug:
        await page.screenshot(path=f"{DEBUG_DIR}/Essen_pre_search.png", full_page=True)

    # ── Click 'Anzeigen' (NOT the sidebar 'Anmelden' login button) ──
    await _submit_search(page, "Essen", [
        'input[type="submit"][value="Anzeigen"]',
        'input[type="submit"][value*="Anzeigen" i]',
        'button:has-text("Anzeigen")',
        'input[value*="Anzeigen" i]',
    ])

    if debug:
        await page.screenshot(path=f"{DEBUG_DIR}/Essen_results.png", full_page=True)

    # ── Extraction: iterate ROWS, keep those with a '/vorgang/' or '/tops/'
    #    link. Title = cleaned ROW text (the link itself is often an icon). ──
    DOC_PATHS = ("/vorgang/", "/tops/")

    def clean(txt: str) -> str:
        t = re.sub(r"(?:Mo|Di|Mi|Do|Fr|Sa|So),\s*\d{2}\.\d{2}\.\d{4}", "", txt or "")
        t = re.sub(r"\d{2}:\d{2}\s*Uhr", "", t)
        t = re.sub(r"\s{2,}", " ", t).strip(" -–|,")
        return t

    results, seen = [], set()

    for row in await page.locator("tr").all():
        try:
            row_txt = " ".join((await row.inner_text()).split())
        except Exception:
            row_txt = ""
        # find the document link inside this row
        doc_url = None
        for a in await row.locator("a[href]").all():
            try:
                h = await a.get_attribute("href")
            except Exception:
                continue
            if not h:
                continue
            full = urljoin(city["url"], h)
            if any(p in full for p in DOC_PATHS):
                doc_url = full
                break
        if not doc_url or doc_url in seen:
            continue
        seen.add(doc_url)
        title = clean(row_txt) or "(ohne Titel)"
        results.append({"title": title[:200], "url": doc_url})

    # catch any doc links that were NOT inside a <tr> (use their own text)
    for a in await page.locator('a[href*="/vorgang/"], a[href*="/tops/"]').all():
        try:
            raw = await a.inner_text()
            t = " ".join(raw.split()) if raw else ""
            h = await a.get_attribute("href")
        except Exception:
            continue
        if not h:
            continue
        full = urljoin(city["url"], h)
        if full in seen:
            continue
        seen.add(full)
        results.append({"title": (clean(t) or "(ohne Titel)")[:200], "url": full})

    logger.info(f"  Essen: extracted {len(results)} document(s)")
    for r in results:
        logger.info(f"  Essen: kept -> {r['title'][:70]}")

    if not results:
        logger.info("  Essen: no document links — falling back to generic extractor")
        results = await _extract_results(page, city["url"])

    return results


# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE 5: HANNOVER
#  Keywords joined with " ODER " as the OR separator.
#  Only ONE date field (searches from that date onward).
# ═══════════════════════════════════════════════════════════

async def _scrape_hannover(page: Page, city: dict, debug: bool) -> list:
    """Hannover: ' ODER ' separator + single date field."""
    await page.goto(city["url"], wait_until="domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    keywords_str = " ODER ".join(KEYWORDS)

    filled = await _try_fill(page, [
        'input[name*="such" i]',
        'textarea[name*="such" i]',
        'input[type="text"]',
        'input[type="search"]',
    ], keywords_str)

    if not filled:
        raise ScrapeError("Could not find keyword input for Hannover", FIELD_NOT_FOUND)

    # Single date field only (searches from this date onward)
    await _try_fill_date(page, [
        'input[name*="datum" i]',
        'input[name*="von" i]',
        'input[name*="date" i]',
        'input[type="date"]',
    ], YESTERDAY_DE, YESTERDAY_ISO)

    if debug:
        await page.screenshot(path=f"{DEBUG_DIR}/Hannover_pre_search.png", full_page=True)

    await _submit_search(page, "Hannover", [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Such")',
        'button:has-text("Suche starten")',
    ], enter_fallback=True)

    return await _extract_results(page, city["url"])


# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE 6: STUTTGART
#  Different UI: must click tabs to reveal fields.
#  1. Click "Vorgänge suchen, die…"
#  2. Fill "eines dieser wörter enthalten" field
#  3. Click "Zeitraum" tab
#  4. Set dates
#  5. Search
# ═══════════════════════════════════════════════════════════

async def _scrape_stuttgart(page: Page, city: dict, debug: bool) -> list:
    """
    Stuttgart AllRIS (tr010, a Wicket app). What the page really looks like
    (from the saved evidence, 23 September 2026):

      • a HEADER quick-search with its own "Suchen" button — the first
        button[type=submit] on the page. The old handler clicked THAT, so
        it submitted an empty header search and reported "Empty" every day.
      • the real criteria form: keyword box #trsimple ("eines dieser Wörter
        enthalten"), a collapsed "Aktueller Zeitraum" panel whose "+" toggle
        (a.js-simple-tooltip) reveals #beginDateField / "Bis:" date inputs,
        and the "Anzeigen" button #searchButton.
      • the date panel re-renders via AJAX, so: open it, set the dates,
        THEN type the keywords, then click Anzeigen. Results take a few
        seconds to render (handled by _submit_search's polling).
    """
    await page.goto(city["url"], wait_until="domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    # Step 1: open the "Aktueller Zeitraum" panel if its date inputs are hidden
    date_von = ['#beginDateField', 'input[placeholder="Von:"]', 'input[aria-label*="Beginn" i]']
    date_bis = ['input[placeholder="Bis:"]', 'input[aria-label*="Ende" i]']
    try:
        dates_visible = await page.locator(date_von[0]).first.is_visible(timeout=1000)
    except Exception:
        dates_visible = False
    if not dates_visible:
        opened = await _try_click(page, [
            'div:has(> div > h2:has-text("Aktueller Zeitraum")) a.js-simple-tooltip',
            'div:has(> div > h2:has-text("Zeitraum")) a',
            'a:has-text("Zeitraum")',
        ])
        if opened:
            try:
                await page.wait_for_selector('input[type="date"]', state="visible", timeout=8000)
            except Exception:
                logger.warning("  Stuttgart: Zeitraum panel did not open — searching without a date window")
        else:
            logger.warning("  Stuttgart: Zeitraum toggle not found — searching without a date window")

    # Step 2: dates (native <input type=date> → ISO), each followed by a
    # short pause for the AJAX re-render
    await _try_fill_date(page, date_von, YESTERDAY_DE, YESTERDAY_ISO)
    await page.wait_for_timeout(600)
    await _try_fill_date(page, date_bis, TODAY_DE, TODAY_ISO)
    await page.wait_for_timeout(600)

    # Step 3: keywords LAST (an earlier fill is wiped by the panel re-render)
    keywords_str = " ".join(KEYWORDS)
    filled = await _try_fill(page, [
        '#trsimple',
        'input[name$="trsimple"]',
        'input[name*="oder" i]',
        'input[name*="worte" i]',
    ], keywords_str)
    if not filled:
        try:
            await page.get_by_label("eines dieser", exact=False).first.fill(keywords_str)
            filled = True
        except Exception:
            raise ScrapeError("Could not find keyword field for Stuttgart", FIELD_NOT_FOUND)

    if debug:
        await page.screenshot(path=f"{DEBUG_DIR}/Stuttgart_pre_search.png", full_page=True)

    # Step 4: "Anzeigen" — the criteria form's button, never the header "Suchen"
    await _submit_search(page, "Stuttgart", [
        '#searchButton',
        'button[name="searchPanel:search"]',
        'button:has-text("Anzeigen")',
        'form:has(#trsimple) button[type="submit"]',
    ], enter_fallback=True, enter_target='#trsimple')

    return await _extract_results(page, city["url"])


# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE 7: FRANKFURT (PARLIS system)
#  Different platform entirely — PARLIS full-text search.
# ═══════════════════════════════════════════════════════════

async def _scrape_frankfurt(page: Page, city: dict, debug: bool) -> list:
    """
    Frankfurt PARLIS (volltext.html).
    Suche ist beweisbar identisch zur Handarbeit:
      TEXT (Keywords, Leerzeichen-getrennt), TEXT_O = 'beinhaltet (oder)',
      DATUM (von) + DATUM_2 (bis).
    Auslesen:
      - 'kein Treffer' => ehrlich EMPTY,
      - es zaehlen NUR echte Dokumentlinks '/PARLISLINK/DDW?'.
        Alles andere (Menue .html/.htm/.php, SDF-Sprungmarken,
        EDW-Ansicht, PARLIS2S-Login, externe Links) wird verworfen.
    """
    von_de = YESTERDAY_DE
    bis_de = TODAY_DE

    keywords_str = " ".join(KEYWORDS)

    await page.goto(city["url"], wait_until="domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    # ── 1) Keyword-Feld TEXT ──
    filled = await _try_fill(page, [
        'input[name="TEXT"]',
        'textarea[name="TEXT"]',
    ], keywords_str)
    if not filled:
        raise ScrapeError("Frankfurt: Keyword-Feld TEXT nicht gefunden", FIELD_NOT_FOUND)

    # ── 2) Operator TEXT_O EXAKT auf 'beinhaltet (oder)' ──
    try:
        await page.select_option('select[name="TEXT_O"]', label="beinhaltet (oder)")
    except Exception:
        try:
            await page.select_option('select[name="TEXT_O"]', value="beinhaltet (oder)")
        except Exception:
            logger.warning("  Frankfurt: TEXT_O konnte nicht auf 'oder' gesetzt werden")

    # ── 3) Datumsfelder ──
    await _try_fill(page, ['input[name="DATUM"]'], von_de)
    await _try_fill(page, ['input[name="DATUM_2"]'], bis_de)

    # ── KONTROLLE vor dem Absenden ──
    try:
        to_val = await page.input_value('select[name="TEXT_O"]')
        d1 = await page.input_value('input[name="DATUM"]')
        d2 = await page.input_value('input[name="DATUM_2"]')
        logger.info(f"  >> Frankfurt Kontrolle: TEXT_O='{to_val}', DATUM(von)='{d1}', DATUM_2(bis)='{d2}'")
    except Exception:
        pass

    if debug:
        await page.screenshot(path=f"{DEBUG_DIR}/Frankfurt_pre_search.png", full_page=True)

    # ── Absenden — und pruefen, dass die Seite reagiert hat ──
    await _submit_search(page, "Frankfurt", [
        'input[type="submit"]',
        'button[type="submit"]',
        'input[value*="Such" i]',
        'button:has-text("Such")',
    ])

    if debug:
        await page.screenshot(path=f"{DEBUG_DIR}/Frankfurt_results.png", full_page=True)

    # ── 4) 'kein Treffer' => ehrlich EMPTY ──
    # _safe_content waits for the network to settle first: reading the page
    # while PARLIS is still navigating raised "Unable to retrieve content".
    page_text = (await _safe_content(page)).lower()
    empty_markers = [
        "kein treffer", "keine treffer",
        "wurde kein treffer erzielt", "keine dokumente",
    ]
    if any(m in page_text for m in empty_markers):
        logger.info("  Frankfurt: PARLIS meldet 'kein Treffer' => Empty (korrekt)")
        return []

    # ── 5) NUR echte Dokumentlinks '/PARLISLINK/DDW?' ──
    results = []
    seen = set()
    links = await page.locator("a").all()
    for link in links:
        try:
            href = await link.get_attribute("href")
            title = (await link.inner_text()).strip()
        except Exception:
            continue
        if not href or not title:
            continue

        # Der einzige, bewiesene Treffer-Marker:
        if "/parlislink/ddw?" not in href.lower():
            continue

        full = urljoin(city["url"], href)
        if full in seen:
            continue
        seen.add(full)
        results.append({"title": title[:200], "url": full})

    logger.info(f"  Frankfurt: {len(results)} echte Dokumente extrahiert")
    return results
 

# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE 8: BERLIN (PARDOK "portala")
#  Own search page. Steps per keyword:
#   1. Type keyword into the main search box
#   2. Turn on "Volltextsuche"
#   3. Expand "Weitere Suchoptionen" to reveal the date fields
#   4. Fill "Von" (yesterday) and "Bis" (today)
#   5. Click "Suchen"
#   6. Read ONLY real result links (strict)
# ═══════════════════════════════════════════════════════════

async def _scrape_berlin(page: Page, city: dict, debug: bool) -> list:
    """Berlin PARDOK: dedicated handler, one keyword at a time."""
    all_results = []
    searches_ok = 0

    for i, keyword in enumerate(KEYWORDS):
        logger.info(f"       Keyword {i+1}/{len(KEYWORDS)}: {keyword}")
        try:
            await page.goto(city["url"], wait_until="domcontentloaded")
            await page.wait_for_timeout(PAGE_SETTLE_MS)
            await _dismiss_cookies(page)

            # 1) Keyword into the main search box
            filled = await _try_fill(page, [
                'input[placeholder*="Suchbegriff" i]',
                'input[placeholder*="Drucksachennummer" i]',
                'input[type="search"]',
                'input[type="text"]',
            ], keyword)
            if not filled:
                logger.warning(f"       No search field for '{keyword}' — skipped")
                continue

            # 2) Switch on "Volltextsuche" (search in document text)
            try:
                await page.get_by_text("Volltextsuche", exact=False).first.click()
                await page.wait_for_timeout(300)
            except Exception:
                pass

            # 3) Expand "Weitere Suchoptionen" to reveal date fields
            await _try_click(page, [
                'button:has-text("Weitere Suchoptionen")',
                'a:has-text("Weitere Suchoptionen")',
                '*:has-text("Weitere Suchoptionen")',
            ])
            await page.wait_for_timeout(800)

            # 4) Date range: Von = yesterday, Bis = today (DD.MM.YYYY)
            von_ok = await _try_fill(page, [
                'input[placeholder="Von"]',
                'input[placeholder*="Von" i]',
            ], YESTERDAY_DE)
            await _try_fill(page, [
                'input[placeholder="Bis"]',
                'input[placeholder*="Bis" i]',
            ], TODAY_DE)
            # Close any date-picker popup that might block the button
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

            if debug:
                await page.screenshot(path=f"{DEBUG_DIR}/Berlin_{i+1}.png", full_page=True)

            # 5) Click "Suchen" (Enter as fallback) — and confirm the page responded
            await _submit_search(page, city["name"], [
                'button:has-text("Suchen")',
                'input[type="submit"][value*="uch" i]',
                'button[type="submit"]',
            ], enter_fallback=True)

            if debug:
                await page.screenshot(path=f"{DEBUG_DIR}/Berlin_{i+1}_results.png", full_page=True)

            # 6) Strict extraction: real result links only
            results = await _extract_results(page, city["url"], strict=True)
            all_results.extend(results)
            searches_ok += 1

        except Exception as e:
            logger.warning(f"       Keyword '{keyword}' failed: {e}")

        await asyncio.sleep(DELAY_BETWEEN_KEYWORDS)

    if searches_ok == 0:
        raise UnverifiedSearch(
            "Berlin: none of the keyword searches on PARDOK could be "
            "confirmed to have run (search field/button not found, or the "
            "page never changed after submitting)."
        )

    # Remove duplicates
    seen = set()
    unique = []
    for r in all_results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    return unique

# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE: LEIPZIG (AllRIS vo040)
#  The default list is already sorted newest-first. We do NOT
#  touch the search form. We read each table row, take the date
#  from the row's date cell, keep rows within our window, and
#  stop as soon as we reach an older row. Self-contained: it
#  computes its own date window and logs what it sees.
# ═══════════════════════════════════════════════════════════
async def _scrape_leipzig(page: Page, city: dict, debug: bool) -> list:
    """
    Leipzig (AllRIS vo040) — form-free.
    Table is pre-sorted by 'Vorlage freigegeben' DESC (newest first).
    Read rows, keep those inside our date window, stop at the first older row.
    If a full page (25 rows) is all in-window, page forward.
    """
    # ── Date window (Monday reaches back to Friday — see config.py) ──
    window_start = YESTERDAY_ISO
    window_end = TODAY_ISO
    logger.info(f"  Leipzig: date window {window_start} .. {window_end}")

    date_re = re.compile(r"^\s*(\d{2})\.(\d{2})\.(\d{4})\s*$")

    results = []
    scanned = 0
    stop = False
    page_num = 1
    MAX_PAGES = 20

    await page.goto(city["url"], wait_until="domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    while page_num <= MAX_PAGES and not stop:
        rows = await page.locator("tr").all()
        for row in rows:
            cells = await row.locator("td").all()
            row_date = None
            for c in cells:
                try:
                    txt = (await c.inner_text()).strip()
                except Exception:
                    continue
                m = date_re.match(txt)
                if m:
                    row_date = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
                    break
            if row_date is None:
                continue
            scanned += 1
            if row_date > window_end:
                continue                      # future-dated, skip but keep scanning
            if row_date < window_start:
                stop = True                   # older than window -> everything below is older
                break
            # in window — pick the link with the most text (the Betreff)
            links = await row.locator("a").all()
            best, best_len = None, -1
            for a in links:
                try:
                    t = (await a.inner_text()).strip()
                    h = await a.get_attribute("href")
                except Exception:
                    continue
                if h and len(t) > best_len:
                    best, best_len = (t, h), len(t)
            if best:
                results.append({"title": best[0], "url": urljoin(city["url"], best[1])})

        logger.info(
            f"  Leipzig: scanned {scanned} dated row(s), "
            f"kept {len(results)} in window (page {page_num})"
        )

        if stop:
            break

        # ── PAGINATION ──
        next_page = str(page_num + 1)

        # DIAGNOSTIC: log the real pagination links so we never have to guess again
        candidates = []
        for a in await page.locator("a").all():
            try:
                t = (await a.inner_text()).strip()
                h = await a.get_attribute("href")
            except Exception:
                continue
            if t in (next_page, "»", "›", ">", "weiter", "nächste", "Weiter") \
               or (t.isdigit() and h and "vo040" in h):
                candidates.append((t, (h or "")[:80]))
        if candidates:
            logger.info(f"  Leipzig: pagination candidates -> {candidates[:8]}")
        else:
            logger.info("  Leipzig: no pagination links found — stopping")
            break

        # Robust attempt: click the page NUMBER (e.g. '2'), not the arrow
        clicked = False
        try:
            await page.get_by_role("link", name=next_page, exact=True).first.click()
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(PAGE_SETTLE_MS)
            clicked = True
        except Exception as e:
            logger.info(f"  Leipzig: could not click page {next_page}: {str(e)[:120]}")

        if not clicked:
            break
        page_num += 1

    if scanned == 0:
        # A list with no dated rows at all is not "empty" — the page layout
        # is not what this handler expects (or the list did not load).
        raise ScrapeError(
            "Leipzig: no dated table rows found — the list did not load or "
            "its layout changed", LAYOUT_CHANGED,
        )

    logger.info(f"  Leipzig: {len(results)} result(s) total")
    return results
 
# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE: LUDWIGSHAFEN
#  Sitzt hinter einer Myra-WAF. Stufe 1: Browser als echten
#  Nutzer tarnen (Stealth + Header + JS-Challenge-Zeit).
#  Danach EHRLICH pruefen: durchgekommen oder immer noch Sperre?
# ═══════════════════════════════════════════════════════════

async def _scrape_ludwigshafen(page: Page, city: dict, debug: bool) -> list:
    """Ludwigshafen: Myra-WAF. Erst tarnen, dann pruefen, dann Standard-Suche."""
    # 1) Automatisierungs-Signale verstecken (vor dem Laden)
    await page.add_init_script(_STEALTH_JS)

    # 2) Echte Browser-Header setzen
    await page.set_extra_http_headers({
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        "Referer": "https://www.google.com/",
        "Upgrade-Insecure-Requests": "1",
    })

    # 3) Seite laden und der WAF Zeit fuer eine JS-Pruefung geben
    await page.goto(city["url"], wait_until="networkidle")
    await page.wait_for_timeout(6000)

    # 4) EHRLICHE Pruefung: sind wir durch oder gesperrt?
    content = (await _safe_content(page)).lower()
    if "you are not supposed to be here" in content:
        # Checked 23 September 2026: blocked from GitHub's US runners AND from
        # a UK home connection — in every browser mode, even a real headed
        # Chrome — but the site loads normally from the Berlin office. So the
        # WAF filters by IP/region, not by browser. Only the runner's location fixes it.
        raise ScrapeError(
            "Myra-WAF blockiert diese IP/Region (503) — GitHub-Runner (USA) und "
            "UK-Anschluss werden abgewiesen, aus dem Berliner Buero laedt die Seite "
            "normal (geprueft 23.09.2026). Kein Browser-Problem: Loesung ist der "
            "Self-Hosted-Runner im Buero (README: 'Running from the office').",
            BLOCKED,
        )

    logger.info("  Ludwigshafen: WAF passiert ✓ → Standard-Suche")
    await _dismiss_cookies(page)
    return await _scrape_standard(page, city, debug)


# ─────────────────────────────────────────────────────────
# DISPATCH MAP — connects city types to their scrapers
# ─────────────────────────────────────────────────────────
_SCRAPER_MAP = {
    "standard": _scrape_standard,
    "individual": _scrape_individual,
    "berlin": _scrape_berlin,
    "click_first": _scrape_click_first,
    "essen": _scrape_essen,
    "hannover": _scrape_hannover,
    "stuttgart": _scrape_stuttgart,
    "frankfurt": _scrape_frankfurt,
    "ludwigshafen": _scrape_ludwigshafen,
    "leipzig": _scrape_leipzig,
}
