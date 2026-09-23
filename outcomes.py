"""
═══════════════════════════════════════════════════════════════
 OUTCOMES — What happened to each city, in words a human can act on
═══════════════════════════════════════════════════════════════
 Every city ends a run in exactly one OUTCOME:

   ok          results were found
   empty       the search ran, we confirmed it ran, and it found nothing
   unverified  the search may not have run at all (form did not submit,
               page did not change) — "empty" would be a lie here
   error       something broke (timeout, blocked, missing field, ...)

 Errors additionally get a CATEGORY, and every category maps to a
 plain-English meaning and a next step (CATEGORY_INFO). This is what
 the dev channel and the run summary show, so nobody has to decode a
 Playwright stack trace to know whether anyone needs to act.
═══════════════════════════════════════════════════════════════
"""

# ─── Outcomes ─────────────────────────────────────────────
OK = "ok"
EMPTY = "empty"
UNVERIFIED = "unverified"
ERROR = "error"

# ─── Error categories ─────────────────────────────────────
BLOCKED = "blocked"
TIMEOUT = "timeout"
HTTP_ERROR = "http_error"
FIELD_NOT_FOUND = "field_not_found"
LAYOUT_CHANGED = "layout_changed"
OTHER = "other"

# category → (what it means, what to do about it)
CATEGORY_INFO = {
    BLOCKED: (
        "The site blocks automated access (WAF / bot protection).",
        "Selector changes will not fix this. First check from a home IP: if it "
        "works there it is an IP block (self-hosted runner / residential proxy); "
        "if it is blocked there too the WAF detects the headless browser itself "
        "(real browser on our own machine, or ask the city for a data feed).",
    ),
    TIMEOUT: (
        "The site did not respond in time.",
        "Ignore a one-off. If it persists 3+ days, check the site by hand; "
        "if it also hangs in a normal browser the server is down, not us.",
    ),
    HTTP_ERROR: (
        "The server answered with an error status (404 / 5xx).",
        "404 → the URL in config.py has moved. 5xx → their server; wait.",
    ),
    FIELD_NOT_FOUND: (
        "The search form no longer has the field or button we expect.",
        "The city changed its page. Open debug/<city>.html, find the new "
        "field names, update the selectors in scraper.py (see README).",
    ),
    LAYOUT_CHANGED: (
        "The page loaded but its structure is not what the handler expects.",
        "Open debug/<city>.html and compare with the handler's assumptions.",
    ),
    UNVERIFIED: (
        "We filled the form and clicked Search, but the page did not change — "
        "the search probably never ran.",
        "Treat as a failure, not as 'no news'. Check debug/<city>.png: was "
        "the form filled? Did the click land on the right button?",
    ),
    OTHER: (
        "An unexpected error.",
        "Read the error message and the debug/<city>.html snapshot.",
    ),
}


class ScrapeError(Exception):
    """An error with a known category. Handlers raise this instead of a
    bare Exception so the outcome can be classified without guessing."""

    def __init__(self, message: str, category: str = OTHER):
        super().__init__(message)
        self.category = category


class UnverifiedSearch(ScrapeError):
    """The handler could not confirm that the search actually executed."""

    def __init__(self, message: str):
        super().__init__(message, UNVERIFIED)


def classify(exc: Exception, http_status=None, page_text: str = "") -> str:
    """Best-effort category for an exception, using the HTTP status of the
    last navigation and the page text when the exception itself is vague."""
    if isinstance(exc, ScrapeError):
        return exc.category

    msg = str(exc).lower()
    text = (page_text or "").lower()

    if http_status in (403, 429, 503) or "not supposed to be here" in text or "waf" in msg:
        return BLOCKED
    if http_status is not None and (http_status == 404 or http_status >= 500):
        return HTTP_ERROR
    if "timeout" in msg or "timed out" in msg or "net::err_" in msg:
        return TIMEOUT
    if "not found" in msg or "nicht gefunden" in msg:
        return FIELD_NOT_FOUND
    return OTHER


def describe(category: str):
    """(meaning, action) for a category; safe for unknown values."""
    return CATEGORY_INFO.get(category, CATEGORY_INFO[OTHER])
