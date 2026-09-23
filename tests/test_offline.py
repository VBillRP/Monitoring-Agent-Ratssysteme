"""
Offline tests — no network, no browser, no secrets.
They cover the logic that decides what a run REPORTS, which is exactly
the part that used to fail silently.
"""

import json

import outcomes
from outcomes import (
    OK, EMPTY, UNVERIFIED, ERROR, BLOCKED, TIMEOUT, HTTP_ERROR, FIELD_NOT_FOUND, OTHER,
    ScrapeError, UnverifiedSearch, classify, describe,
)
import report
import teams_notify
from scraper import _safe_filename


def rec(city, outcome, results=None, error=None, category=None, http=None):
    return {
        "city": city, "url": f"https://{city}.example", "type": "standard",
        "results": results or [], "outcome": outcome, "error": error,
        "error_category": category, "http_status": http, "duration_s": 1.0,
        "timestamp": "2026-09-23T09:00:00",
    }


# ─── outcomes.classify ──────────────────────────────────

def test_scrape_error_keeps_its_category():
    assert classify(ScrapeError("x", BLOCKED)) == BLOCKED
    assert classify(UnverifiedSearch("x")) == UNVERIFIED


def test_timeout_is_recognised_from_message():
    assert classify(Exception("Page.goto: Timeout 30000ms exceeded.")) == TIMEOUT


def test_http_status_drives_blocked_and_http_error():
    assert classify(Exception("boom"), http_status=403) == BLOCKED
    assert classify(Exception("boom"), http_status=503) == BLOCKED
    assert classify(Exception("boom"), http_status=404) == HTTP_ERROR
    assert classify(Exception("boom"), http_status=502) == HTTP_ERROR


def test_waf_page_text_means_blocked():
    assert classify(Exception("boom"), page_text="You are not supposed to be here") == BLOCKED


def test_unknown_is_other_and_describe_is_safe():
    assert classify(Exception("something odd")) == OTHER
    meaning, action = describe("no-such-category")
    assert meaning and action
    for cat in outcomes.CATEGORY_INFO:
        m, a = describe(cat)
        assert m and a


# ─── report ──────────────────────────────────────────────

def test_summarise_counts_every_outcome():
    results = [
        rec("A", OK, results=[{"title": "t", "url": "u"}]),
        rec("B", EMPTY),
        rec("C", UNVERIFIED, error="no change", category=UNVERIFIED),
        rec("D", ERROR, error="Timeout", category=TIMEOUT),
    ]
    c = report.summarise(results)
    assert (c[OK], c[EMPTY], c[UNVERIFIED], c[ERROR], c["links"]) == (1, 1, 1, 1, 1)


def test_known_issues_do_not_count_as_unexpected(monkeypatch):
    monkeypatch.setattr(report, "KNOWN_ISSUES", {"Ludwigshafen": "WAF"})
    monkeypatch.setattr(report, "MAX_UNEXPECTED_FAILURES", 1)
    results = [
        rec("Ludwigshafen", ERROR, error="blocked", category=BLOCKED, http=503),
        rec("Berlin", UNVERIFIED, error="no change", category=UNVERIFIED),
    ]
    assert [r["city"] for r in report.unexpected_failures(results)] == ["Berlin"]
    assert report.run_failed(results) is False          # 1 unexpected, threshold 1
    results.append(rec("Munich", UNVERIFIED, error="no change", category=UNVERIFIED))
    assert report.run_failed(results) is True           # 2 unexpected > 1


def test_total_outage_trips_the_threshold(monkeypatch):
    monkeypatch.setattr(report, "KNOWN_ISSUES", {})
    monkeypatch.setattr(report, "MAX_UNEXPECTED_FAILURES", 2)
    results = [rec(c, ERROR, error="x", category=OTHER) for c in "ABCD"]
    assert report.run_failed(results) is True


def test_only_known_issue_cities_failing_is_not_a_failed_run(monkeypatch):
    # e.g. `python main.py --city Ludwigshafen` must not exit non-zero forever
    monkeypatch.setattr(report, "KNOWN_ISSUES", {"Ludwigshafen": "WAF", "Cologne": "down"})
    monkeypatch.setattr(report, "MAX_UNEXPECTED_FAILURES", 2)
    results = [
        rec("Ludwigshafen", ERROR, error="blocked", category=BLOCKED, http=503),
        rec("Cologne", ERROR, error="Timeout", category=TIMEOUT),
    ]
    assert report.unexpected_failures(results) == []
    assert report.run_failed(results) is False


def test_markdown_table_lists_every_city_with_outcome_and_category():
    results = [
        rec("Essen", OK, results=[{"title": "t", "url": "u"}]),
        rec("Cologne", ERROR, error="Page.goto: Timeout 30000ms | exceeded", category=TIMEOUT),
        rec("Berlin", UNVERIFIED, error="page did not change", category=UNVERIFIED),
    ]
    md = report.to_markdown(results, "23.09.2026")
    for needle in ("| Essen", "| Cologne", "| Berlin", "timeout", "unverified", "What the categories mean"):
        assert needle in md
    assert "30000ms \\| exceeded" in md      # pipes are escaped so the table survives


def test_write_artifacts_writes_json_and_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "step.md"))
    results = [rec("A", EMPTY)]
    report.write_artifacts(results, "23.09.2026")
    assert json.loads((tmp_path / report.RESULTS_JSON).read_text())[0]["city"] == "A"
    assert "| A" in (tmp_path / report.SUMMARY_MD).read_text()
    assert "| A" in (tmp_path / "step.md").read_text()


# ─── teams_notify ────────────────────────────────────────

def _card_text(card):
    return json.dumps(card, ensure_ascii=False)


def test_results_card_never_shows_error_text_to_the_team():
    results = [
        rec("Essen", OK, results=[{"title": "Vorlage 1", "url": "https://e/1"}]),
        rec("Cologne", ERROR, error="Page.goto: Timeout 30000ms exceeded.", category=TIMEOUT),
        rec("Berlin", UNVERIFIED, error="page did not change after clicking Search", category=UNVERIFIED),
        rec("Mainz", EMPTY),
    ]
    text = " ".join(_card_text(c) for c in teams_notify._build_cards(results))
    assert "Vorlage 1" in text
    assert "Not checked today" in text and "Cologne" in text and "Berlin" in text
    assert "EMPTY" in text and "Mainz" in text
    for forbidden in ("Timeout", "did not change", "ERRORS", "Attention", "timeout", "unverified"):
        assert forbidden not in text, forbidden


def test_diagnostics_card_explains_category_and_next_step():
    results = [
        rec("Cologne", ERROR, error="Page.goto: Timeout 30000ms exceeded.", category=TIMEOUT, http=None),
        rec("Ludwigshafen", ERROR, error="Myra", category=BLOCKED, http=503),
        rec("Essen", OK, results=[{"title": "t", "url": "u"}]),
    ]
    text = _card_text(teams_notify._build_diagnostics_card(results))
    assert "Cologne" in text and "timeout" in text
    assert "HTTP 503" in text and "blocked" in text
    meaning, action = describe(TIMEOUT)
    assert meaning in text and action in text
    assert "Healthy" in text and "Essen" in text


# ─── scraper helpers ─────────────────────────────────────

def test_safe_filename_handles_umlauts_and_spaces():
    assert _safe_filename("Düsseldorf") == "Duesseldorf"
    assert _safe_filename("Mönchengladbach") == "Moenchengladbach"
    assert _safe_filename("Bad Homburg v.d.H.") == "Bad_Homburg_v_d_H_"
