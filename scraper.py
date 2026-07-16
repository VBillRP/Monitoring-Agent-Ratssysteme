
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
from datetime import datetime
from urllib.parse import urljoin
from playwright.async_api import async_playwright, Page

from config import KEYWORDS, TODAY_DE, TODAY_ISO, YESTERDAY_DE, YESTERDAY_ISO

logger = logging.getLogger("council-monitor.scraper")

# ─── Timing settings ─────────────────────────────────────
DELAY_BETWEEN_CITIES = 3       # Seconds to wait between cities
DELAY_BETWEEN_KEYWORDS = 1.5   # Seconds between individual keyword searches
PAGE_SETTLE_MS = 2000          # Milliseconds to let a page finish loading
PAGE_TIMEOUT_MS = 30000        # Max milliseconds before giving up on a page


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
        "results": [{"title": "...", "url": "..."}, ...],
        "error": None or "error message",
        "timestamp": "2026-05-06T09:00:00"
      },
      ...
    ]
    """
    all_results = []

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
            page.set_default_timeout(PAGE_TIMEOUT_MS)

            try:
                # Pick the right scraper function for this city type
                handler = _SCRAPER_MAP[city["type"]]
                results = await handler(page, city, debug)

                all_results.append({
                    "city": city["name"],
                    "url": city["url"],
                    "results": results,
                    "error": None,
                    "timestamp": datetime.now().isoformat(),
                })

                count = len(results)
                logger.info(f"     → {count} result(s)" if count else "     → Empty")

            except Exception as e:
                logger.error(f"     ✗ ERROR: {e}")

                # Save a screenshot so you can see what went wrong
                if debug:
                    try:
                        safe_name = city["name"].replace(" ", "_").replace("ü", "ue").replace("ö", "oe").replace("ä", "ae")
                        await page.screenshot(
                            path=f"debug_{safe_name}_error.png",
                            full_page=True,
                        )
                    except:
                        pass

                all_results.append({
                    "city": city["name"],
                    "url": city["url"],
                    "results": [],
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                })

            finally:
                await page.close()
                await asyncio.sleep(DELAY_BETWEEN_CITIES)

        await browser.close()

    return all_results


# ═══════════════════════════════════════════════════════════
#                    HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

async def _dismiss_cookies(page: Page):
    """
    Many German sites show a GDPR cookie banner.
    This tries to click "Accept" so we can access the actual page.
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
        except:
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
        except:
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
        except:
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
        except:
            continue
    return False


async def _extract_results(page: Page, base_url: str, strict: bool = False) -> list:
    """
    Generic result extractor for SessionNet / AllRIS pages.

    strict=True  → for Berlin/Munich: ONLY read from real result
                   containers, never from generic page/nav links,
                   and drop known menu/navigation entries.
    """
    results = []

    # ── Check if the page says "no results found" ──
    try:
        body_text = await page.inner_text("body")
    except:
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

    link_strategies = specific_strategies
    if not strict:
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

                except:
                    continue

            if results:
                break  # Found results with this strategy, stop trying

        except:
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
#          Mainz, Mannheim, Mönchengladbach, Ludwigshafen,
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
    await page.goto(city["url"], wait_until="domcontentloaded")
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
        except:
            pass

    if not filled:
        raise Exception("Could not find the keyword search field (Suchwort)")

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
        except:
            try:
                await page.locator('label:has-text("ODER")').click()
            except:
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
        safe = city["name"].replace(" ", "_")
        await page.screenshot(path=f"debug_{safe}_pre_search.png", full_page=True)

    # ── Step 6: Click Search button ──
    clicked = await _try_click(page, [
        'input[name="go"]',                        # Current SessionNet submit
        'input[type="submit"][value*="uch" i]',    # "Suchen" or "suchen"
        'button[type="submit"]:has-text("uch")',
        'input[name*="submit" i][value*="uch" i]',
        'input[name="smcsubmitrecherche"]',
        'input[type="submit"]',
        'button[type="submit"]',
    ])

    if not clicked:
        raise Exception("Could not find the Search button")

    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)

    if debug:
        safe = city["name"].replace(" ", "_")
        await page.screenshot(path=f"debug_{safe}_results.png", full_page=True)

    # ── Step 7: Extract results ──
    return await _extract_results(page, city["url"])


# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE 2: INDIVIDUAL KEYWORDS
#  Cities: Berlin, Munich
#  Keywords cannot be searched in bulk — each keyword
#  requires a separate search.
# ═══════════════════════════════════════════════════════════

async def _scrape_individual(page: Page, city: dict, debug: bool) -> list:
    """
    Berlin / Munich: each keyword must be searched separately.
    Returns ONLY real document hits — never navigation/menu links.
    Raises an error if no search could actually be run, so the city
    is reported as an ERROR instead of silently returning junk.
    """
    all_results = []
    searches_ok = 0  # how many keyword searches actually executed

    for i, keyword in enumerate(KEYWORDS):
        logger.info(f"       Keyword {i+1}/{len(KEYWORDS)}: {keyword}")

        try:
            if city["name"] == "Munich":
                url = (
                    f"https://risi.muenchen.de/risi/suche?3"
                    f"&von={YESTERDAY_ISO}&bis={TODAY_ISO}"
                    f"&bereich=Vorgang"
                    f"&objekt=1&objekt=2&objekt=51&objekt=52&objekt=53"
                )
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(PAGE_SETTLE_MS)
                await _dismiss_cookies(page)

                filled = await _try_fill(page, [
                    'input[name*="such" i]',
                    'input[type="search"]',
                    '#searchInput',
                    'input[type="text"]',
                ], keyword)

            else:  # Berlin
                await page.goto(city["url"], wait_until="domcontentloaded")
                await page.wait_for_timeout(PAGE_SETTLE_MS)
                await _dismiss_cookies(page)

                filled = await _try_fill(page, [
                    'input[name*="such" i]',
                    'input[name*="query" i]',
                    'input[name*="volltext" i]',
                    '#searchTerm',
                    'input[type="search"]',
                    'input[type="text"]',
                ], keyword)

                # Berlin also needs the date fields
                await _try_fill(page, [
                    'input[name*="von" i]', 'input[name*="from" i]',
                    'input[name*="start" i]',
              ], YESTERDAY_DE)
                await _try_fill(page, [
                    'input[name*="bis" i]', 'input[name*="to" i]',
                    'input[name*="end" i]',
                ], TODAY_DE)

            # No search field found → NOT a valid search. Skip this
            # keyword (do NOT scrape navigation links as "results").
            if not filled:
                logger.warning(f"       No search field for '{keyword}' — skipped")
                continue

            clicked = await _try_click(page, [
                'input[name="go"]',
                'input[type="submit"][value*="uch" i]',
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Such")',
            ])
            if not clicked:
                # Fallback: submit with Enter
                try:
                    await page.keyboard.press("Enter")
                except:
                    logger.warning(f"       Could not submit search for '{keyword}'")
                    continue

            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(PAGE_SETTLE_MS)

            if debug:
                safe = city["name"].replace(" ", "_")
                await page.screenshot(path=f"debug_{safe}_{i+1}.png", full_page=True)

            # STRICT extraction: real result links only
            results = await _extract_results(page, city["url"], strict=True)
            all_results.extend(results)
            searches_ok += 1

        except Exception as e:
            logger.warning(f"       Keyword '{keyword}' failed: {e}")

        # Be polite — don't hammer the server
        await asyncio.sleep(DELAY_BETWEEN_KEYWORDS)

    # If not a single search actually ran, report an ERROR instead
    # of pretending the city was searched successfully.
    if searches_ok == 0:
        raise Exception(
            "Could not run any keyword search (search field/button not found). "
            "Check the search URL and selectors for this city."
        )

    # Remove duplicate results (same URL found by different keywords)
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
    """Click the reveal button first, then do standard search."""
    await page.goto(city["url"], wait_until="domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    # Click the button that reveals the search form
    revealed = await _try_click(page, [
        'a:has-text("Rechercheauswahl anzeigen")',
        'button:has-text("Rechercheauswahl anzeigen")',
        'a:has-text("Recherche")',
        '*:has-text("Rechercheauswahl anzeigen")',
    ])

    if not revealed:
        try:
            await page.get_by_text("Rechercheauswahl anzeigen").click()
            revealed = True
        except:
            raise Exception("Could not find 'Rechercheauswahl anzeigen' button")

    await page.wait_for_timeout(1500)

    # Now proceed with the standard search flow
    return await _do_standard_search(page, city, debug)


async def _do_standard_search(page: Page, city: dict, debug: bool) -> list:
    """
    Shared search flow used by click_first (and potentially other variants).
    Same steps as _scrape_standard but the page is already loaded.
    """
    keywords_str = " ".join(KEYWORDS)

    filled = await _try_fill(page, [
        'input[name="__swords"]',
        'textarea[name="__swords"]',
        'input[name="smcsuchwoerter"]',
        'textarea[name="smcsuchwoerter"]',
        '#smcsuchwoerter',
        'input[name*="uchwoerter" i]',
        'textarea[name*="uchwoerter" i]',
        'input[name*="volltext" i]',
    ], keywords_str)

    if not filled:
        try:
            await page.get_by_label("Suchwort", exact=False).first.fill(keywords_str)
            filled = True
        except:
            raise Exception("Could not find keyword input field")

    # Select ODER
    await _try_click(page, [
        'input[name="__sao"][value="2"]',       # Current SessionNet: 2 = ODER
        'input[type="radio"][value="ODER"]',
        'input[type="radio"][value="oder"]',
    ])
    try:
        await page.get_by_label("ODER", exact=True).check()
    except:
        pass

    # Dates
    await _try_fill_date(page, [
        'input[name="__axxdat_full"]',
        'input[name="smcfreigabevon"]', 'input[name*="von" i][size]',
    ], TODAY_DE, TODAY_ISO)
    await _try_fill_date(page, [
        'input[name="__exxdat_full"]',
        'input[name="smcfreigabebis"]', 'input[name*="bis" i][size]',
    ], YESTERDAY_DE, YESTERDAY_ISO)

    if debug:
        safe = city["name"].replace(" ", "_")
        await page.screenshot(path=f"debug_{safe}_pre_search.png", full_page=True)

    await _try_click(page, [
        'input[name="go"]',
        'input[type="submit"][value*="uch" i]',
        'button[type="submit"]',
        'input[type="submit"]',
    ])

    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    return await _extract_results(page, city["url"])


# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE 4: ESSEN
#  Keywords joined with " O " (the letter O) as OR separator
#  because the site has no OR button.
# ═══════════════════════════════════════════════════════════

async def _scrape_essen(page: Page, city: dict, debug: bool) -> list:
    """Essen: keywords use ' O ' as the OR separator."""
    await page.goto(city["url"], wait_until="domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    # Join all keywords with " O " between them
    keywords_str = " O ".join(KEYWORDS)

    filled = await _try_fill(page, [
        'input[name*="such" i]',
        'textarea[name*="such" i]',
        'input[name*="volltext" i]',
        'input[type="text"]',
        'input[type="search"]',
    ], keywords_str)

    if not filled:
        raise Exception("Could not find keyword input for Essen")

    # Set dates
    await _try_fill(page, [
        'input[name*="freigabevon" i]', 'input[name*="von" i][size]',
   ], YESTERDAY_DE)
    await _try_fill(page, [
        'input[name*="freigabebis" i]', 'input[name*="bis" i][size]',
    ], TODAY_DE)

    if debug:
        await page.screenshot(path="debug_Essen_pre_search.png", full_page=True)

    await _try_click(page, [
        'input[type="submit"][value*="uch" i]',
        'button[type="submit"]',
        'input[type="submit"]',
    ])

    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    return await _extract_results(page, city["url"])


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
        raise Exception("Could not find keyword input for Hannover")

    # Single date field only (searches from this date onward)
    await _try_fill(page, [
        'input[name*="datum" i]',
        'input[name*="von" i]',
        'input[name*="date" i]',
        'input[type="date"]',
 ], YESTERDAY_DE)

    if debug:
        await page.screenshot(path="debug_Hannover_pre_search.png", full_page=True)

    await _try_click(page, [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Such")',
        'button:has-text("Suche starten")',
    ])

    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
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
    """Stuttgart AllRIS: click tabs first, then fill fields."""
    await page.goto(city["url"], wait_until="domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    # Step 1: Click "Vorgänge suchen, die…" tab/link
    clicked = await _try_click(page, [
        'a:has-text("Vorgänge suchen")',
        'button:has-text("Vorgänge suchen")',
        'li:has-text("Vorgänge suchen") a',
        'span:has-text("Vorgänge suchen")',
    ])
    if not clicked:
        try:
            await page.get_by_text("Vorgänge suchen, die").click()
        except:
            logger.warning("  Could not click 'Vorgänge suchen' for Stuttgart")
    await page.wait_for_timeout(1000)

    # Step 2: Keywords in "eines dieser wörter enthalten"
    keywords_str = " ".join(KEYWORDS)

    filled = await _try_fill(page, [
        'input[name*="oder" i]',
        'input[name*="worte" i]',
        'textarea[name*="oder" i]',
        'input[name*="eines" i]',
    ], keywords_str)

    if not filled:
        try:
            field = page.get_by_label("eines dieser", exact=False).first
            await field.fill(keywords_str)
            filled = True
        except:
            raise Exception("Could not find keyword field for Stuttgart")

    # Step 3: Click "Zeitraum" to reveal date fields
    await _try_click(page, [
        'a:has-text("Zeitraum")',
        'button:has-text("Zeitraum")',
        'li:has-text("Zeitraum") a',
        'span:has-text("Zeitraum")',
    ])
    await page.wait_for_timeout(1000)

    # Step 4: Set dates
    await _try_fill(page, [
        'input[name*="von" i]', 'input[name*="start" i]',
    ], YESTERDAY_DE)
    await _try_fill(page, [
        'input[name*="bis" i]', 'input[name*="end" i]',
    ], TODAY_DE)

    if debug:
        await page.screenshot(path="debug_Stuttgart_pre_search.png", full_page=True)

    # Step 5: Search
    await _try_click(page, [
        'input[type="submit"]',
        'button[type="submit"]',
        'button:has-text("Such")',
        'input[value*="uch" i]',
    ])

    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    return await _extract_results(page, city["url"])


# ═══════════════════════════════════════════════════════════
#  SCRAPER TYPE 7: FRANKFURT (PARLIS system)
#  Different platform entirely — PARLIS full-text search.
# ═══════════════════════════════════════════════════════════

async def _scrape_frankfurt(page: Page, city: dict, debug: bool) -> list:
    """Frankfurt PARLIS full-text search."""
    await page.goto(city["url"], wait_until="domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await _dismiss_cookies(page)

    keywords_str = " ".join(KEYWORDS)

    filled = await _try_fill(page, [
        'input[name*="volltext" i]',
        'textarea[name*="volltext" i]',
        'input[name*="such" i]',
        'textarea[name*="such" i]',
        'input[type="text"]',
        'input[type="search"]',
    ], keywords_str)

    if not filled:
        raise Exception("Could not find search field for Frankfurt PARLIS")

    # Set dates if available
    await _try_fill(page, [
        'input[name*="von" i]', 'input[name*="datum" i]',
  ], YESTERDAY_DE)
    await _try_fill(page, [
        'input[name*="bis" i]',
    ], TODAY_DE)

    if debug:
        await page.screenshot(path="debug_Frankfurt_pre_search.png", full_page=True)

    await _try_click(page, [
        'input[type="submit"]',
        'button[type="submit"]',
        'input[value*="uch" i]',
        'button:has-text("Such")',
    ])

    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    return await _extract_results(page, city["url"])

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
            except:
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
            except:
                pass

            if debug:
                await page.screenshot(path=f"debug_Berlin_{i+1}.png", full_page=True)

            # 5) Click "Suchen"
            clicked = await _try_click(page, [
                'button:has-text("Suchen")',
                'input[type="submit"][value*="uch" i]',
                'button[type="submit"]',
            ])
            if not clicked:
                try:
                    await page.keyboard.press("Enter")
                except:
                    logger.warning(f"       Could not submit search for '{keyword}'")
                    continue

            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(PAGE_SETTLE_MS)

            if debug:
                await page.screenshot(path=f"debug_Berlin_{i+1}_results.png", full_page=True)

            # 6) Strict extraction: real result links only
            results = await _extract_results(page, city["url"], strict=True)
            all_results.extend(results)
            searches_ok += 1

        except Exception as e:
            logger.warning(f"       Keyword '{keyword}' failed: {e}")

        await asyncio.sleep(DELAY_BETWEEN_KEYWORDS)

    if searches_ok == 0:
        raise Exception(
            "Could not run any keyword search on PARDOK "
            "(search field/button not found)."
        )

    # Remove duplicates
    seen = set()
    unique = []
    for r in all_results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    return unique
# ─────────────────────────────────────────────────────────
# DISPATCH MAP — connects city types to their scrapers
# ─────────────────────────────────────────────────────────
_SCRAPER_MAP = {
    "standard": _scrape_standard,
    "individual": _scrape_individual,
    "click_first": _scrape_click_first,
    "essen": _scrape_essen,
    "hannover": _scrape_hannover,
    "stuttgart": _scrape_stuttgart,
    "frankfurt": _scrape_frankfurt,
}
