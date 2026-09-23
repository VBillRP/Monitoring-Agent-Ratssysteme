
"""
═══════════════════════════════════════════════════════════════
 CONFIG — German Council Information System Monitor
═══════════════════════════════════════════════════════════════
 All settings are defined here. Edit this file to:
   • Add or remove cities
   • Change keywords
   • Adjust the AI model
═══════════════════════════════════════════════════════════════
"""

import os
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────
# SEARCH KEYWORDS
# These cover micromobility, ride-hailing, carsharing,
# taxi, and related urban transportation policy topics.
# Words ending with * are wildcard searches.
# ─────────────────────────────────────────────────────────
KEYWORDS = [
    "Bolt",
    "Mikromobilität",
    "Scooter",
    "Ridehailing",
    "Fahrtenvermittlung",
    "Roller",
    "Mietwagen",
    "Carsharing",
    "Sondernutzung*",
    "Gemeingebrauch",
    "Elektrokleinstfahrzeug*",
    "Fachkundenachweis",
    "Taxi",
    "Bike",
    "Pedelec",
    "Fahrrad",
    "Mindestpreis*",
    "Ausschreibung*",
    "Luftqualität",
    "multimodal*",
    "Shared",
    "Verkehrswende",
    "Meile",
    "Sharing-Stationen",
    "Elektrifizierung",
    "Mindestentgelt",
    "Preiskorridor",
]

# ─────────────────────────────────────────────────────────
# DATE FORMATS
# German government sites use DD.MM.YYYY format.
# Some (like Munich) use ISO YYYY-MM-DD in URLs.
# ─────────────────────────────────────────────────────────
TODAY_DE = datetime.now().strftime("%d.%m.%Y")    # e.g. "06.05.2026"
TODAY_ISO = datetime.now().strftime("%Y-%m-%d")    # e.g. "2026-05-06"

# Startdatum der Suche.
# Das Tool läuft nur Mo–Fr. Am Montag müssen wir daher bis Freitag
# zurückgehen, um Freitagnachmittag + das ganze Wochenende abzudecken.
# An allen anderen Tagen reicht "gestern".
#   weekday(): Montag = 0, Dienstag = 1, ..., Sonntag = 6
_days_back = 3 if datetime.now().weekday() == 0 else 1
_start_date = datetime.now() - timedelta(days=_days_back)

YESTERDAY_DE = _start_date.strftime("%d.%m.%Y")
YESTERDAY_ISO = _start_date.strftime("%Y-%m-%d")

# ─────────────────────────────────────────────────────────
# API KEYS — loaded from environment variables (never hard-code!)
# ─────────────────────────────────────────────────────────
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

# Results card — the channel the whole team reads. Never receives diagnostics.
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")

# Diagnostics card — a private dev channel. Optional: when unset, the
# diagnostics are written to the run log instead.
TEAMS_DEV_WEBHOOK_URL = os.environ.get("TEAMS_DEV_WEBHOOK_URL", "")

# ─────────────────────────────────────────────────────────
# AI FILTER
# Off by default: the team chose to review unfiltered results until
# the filter has been validated side by side. Set ENABLE_AI_FILTER=true
# (and the AZURE_OPENAI_* variables) to switch it on.
# ─────────────────────────────────────────────────────────
ENABLE_AI_FILTER = os.environ.get("ENABLE_AI_FILTER", "false").strip().lower() in ("1", "true", "yes")

# ─────────────────────────────────────────────────────────
# RUN HEALTH
# The workflow is marked failed when more than this many cities fail
# or cannot be verified, NOT counting the cities listed in KNOWN_ISSUES.
# ─────────────────────────────────────────────────────────
MAX_UNEXPECTED_FAILURES = 2

# Cities that are expected to fail for reasons outside the code.
# They are still attempted every run (so we notice when they recover),
# but they do not count towards the failure threshold above.
KNOWN_ISSUES = {
    "Ludwigshafen": "Myra WAF blocks our headless browser — from GitHub AND from a "
                    "home IP (checked 23 September 2026), so it is not an IP block. "
                    "Options: ask the city for a feed, or run a real (non-headless) "
                    "browser on a machine of our own.",
    "Cologne": "TCP connection times out from GitHub AND from a home IP "
               "(checked 23 September 2026) — server unreachable, not an IP block. "
               "Re-check periodically.",
}


# ─────────────────────────────────────────────────────────
# CITY CONFIGURATIONS
#
# Each city has:
#   name — Display name
#   url  — The search page URL
#   type — Which scraping method to use:
#
#   "standard"    → Enter keywords in Suchwort, click ODER
#                   radio button, set two date fields, search.
#                   (Most cities use this.)
#
#   "individual"  → Same keywords but searched ONE AT A TIME
#                   because the site can't handle bulk search.
#                   (Berlin, Munich)
#
#   "click_first" → Must click a button to reveal the search
#                   form, then proceed as standard.
#                   (Düsseldorf)
#
#   "essen"       → Keywords typed with " O " between them
#                   as the OR separator in the text box.
#                   (Essen)
#
#   "hannover"    → Keywords typed with " ODER " between them;
#                   only one date field instead of two.
#                   (Hannover)
#
#   "stuttgart"   → Must click "Vorgänge suchen, die…" tab first,
#                   keyword field is called differently,
#                   must click "Zeitraum" to show date fields.
#                   (Stuttgart)
#
#   "frankfurt"   → Uses the PARLIS system (different platform).
#                   (Frankfurt)
#
#   "berlin"      → PARDOK portal, one keyword at a time with
#                   full-text search and a date range. (Berlin)
#
#   "leipzig"     → AllRIS list that is already sorted newest-first;
#                   read rows inside the date window, no form. (Leipzig)
#
#   "ludwigshafen"→ Standard SessionNet behind a Myra WAF: disguise the
#                   browser, check we got through, then run "standard".
#
# Optional per-city keys:
#   timeout_ms        — page-load timeout for slow sites (default 30 000)
#   result_selectors  — CSS selectors for THIS site's result links, when
#                       the generic extractor finds nothing (see Munich)
# ─────────────────────────────────────────────────────────

CITIES = [
    # ═══════════════════════════════════════════════
    # STANDARD CITIES (majority case)
    # ═══════════════════════════════════════════════
    {
        "name": "Bielefeld",
        "url": "https://anwendungen.bielefeld.de/bi/suchen01.asp?smcrecherche=7020",
        "type": "standard",
    },
    {
        "name": "Dortmund",
        "url": "https://sessionnet.owl-it.de/dortmund/bi/suchen01.asp?smcrecherche=7020",
        "type": "standard",
    },
    {
        "name": "Münster",
        "url": "https://www.stadt-muenster.de/sessionnet/sessionnetbi/suchen01.php?smcrecherche=7020",
        "type": "standard",
    },
    {
        "name": "Nuremberg",
        "url": "https://online-service2.nuernberg.de/buergerinfo/suchen01.asp?smcrecherche=7020",
        "type": "standard",
    },
    # Leipzig is DISABLED: the site does not answer connections from
    # GitHub Actions (connection timeout). A dedicated handler exists
    # (scraper.py: _scrape_leipzig) — uncomment this entry to try it again.
    # {
    #     "name": "Leipzig",
    #     "url": "https://ratsinformation.leipzig.de/allris_leipzig_public/vo040",
    #     "type": "leipzig",
    # },
    {
        "name": "Mainz",
        "url": "https://bi.mainz.de/suchen01.php?smcrecherche=7020",
        "type": "standard",
    },
    {
        "name": "Mannheim",
        "url": "https://buergerinfo.mannheim.de/buergerinfo/suchen01.asp?smcrecherche=7020",
        "type": "standard",
    },
    {
        "name": "Mönchengladbach",
        "url": "https://ris-moenchengladbach.itk-rheinland.de/sessionnetmglbi/suchen01.asp",
        "type": "standard",
    },
    # Ludwigshafen sits behind a Myra WAF — see KNOWN_ISSUES above.
    {
        "name": "Ludwigshafen",
        "url": "https://www.ludwigshafen.de/ratsinformationssystem/bi/suchen01.php?smcrecherche=7020",
        "type": "ludwigshafen",
    },
    {
        "name": "Heidelberg",
        "url": "https://gemeinderat.heidelberg.de/suchen01.asp?smcrecherche=7020",
        "type": "standard",
    },
    # Cologne currently does not accept connections at all — see KNOWN_ISSUES.
    # The longer timeout is for the days it is merely slow.
    {
        "name": "Cologne",
        "url": "https://ratsinformation.stadt-koeln.de/suchen01.asp",
        "type": "standard",
        "timeout_ms": 60000,
    },

    # ═══════════════════════════════════════════════
    # SPECIAL CITIES
    # ═══════════════════════════════════════════════
    {
        "name": "Frankfurt",
        "url": "https://www.stvv.frankfurt.de/parlis2/volltext.html",
        "type": "frankfurt",
    },
    {
        "name": "Berlin",
        "url": "https://pardok.parlament-berlin.de/portala/browse.tt.html",
      "type": "berlin",
    },
    # Munich (RiSI, a Wicket app): the generic extractor finds nothing on
    # its results page, so the real hits are named explicitly. Document
    # previews (?dokument=) and in-page anchors (#…) are excluded.
    {
        "name": "Munich",
        "url": "https://risi.muenchen.de/risi/suche",
        "type": "individual",
        "result_selectors": [
            'a[href*="/sitzungsvorlage/detail/"]:not([href*="?dokument="]):not([href*="#"])',
            'a[href*="/vorgang/detail/"]:not([href*="?dokument="]):not([href*="#"])',
            'a[href*="/antrag/detail/"]:not([href*="?dokument="]):not([href*="#"])',
        ],
    },
    {
        "name": "Düsseldorf",
        "url": "https://www.duesseldorf.de/rat/buergerinfo",
        "type": "click_first",
    },
    {
        "name": "Essen",
        "url": "https://ris.essen.de/recherche",
        "type": "essen",
    },
    {
        "name": "Hannover",
        "url": "https://e-government.hannover-stadt.de/lhhsimwebre.nsf/Suche.xsp",
        "type": "hannover",
    },
    {
        "name": "Stuttgart",
        "url": "https://allris.stuttgart.de/tr010",
        "type": "stuttgart",
    },
]
