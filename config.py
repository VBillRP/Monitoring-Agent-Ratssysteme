
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
_days_back = 90   # ← 90-TAGE-TRIAGE, nach dem Test diese EINE Zeile wieder ENTFERNEN
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

TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")

# ─────────────────────────────────────────────────────────
# AI MODEL for filtering results
# "gpt-4o-mini" is fast & cheap (~$0.15 per 1M input tokens)
# Change to "gpt-4o" for better accuracy (10x more expensive)
# ─────────────────────────────────────────────────────────


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
    # NOTE: Leipzig runs AllRIS (not SessionNet) and uses a different form
    # layout — the "standard" handler does not fit it. It also blocks
    # datacenter/CI IPs. Needs a dedicated AllRIS handler; left as-is for now.
  {
    "name": "Leipzig",
    "url": "https://ratsinformation.leipzig.de/allris_leipzig_public/vo040",
    "type": "leipzig",
},
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
    # NOTE: Ludwigshafen sits behind a Myra WAF that returns a 503
    # "blocked" page to datacenter/CI traffic. May work from a residential
    # IP; expect it to fail in GitHub Actions regardless of selectors.
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
    {
        "name": "Cologne",
        "url": "https://ratsinformation.stadt-koeln.de/suchen01.asp",
        "type": "standard",
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
    {
        "name": "Munich",
        "url": "https://risi.muenchen.de/risi/suche",
        "type": "individual",
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
