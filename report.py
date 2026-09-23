"""
═══════════════════════════════════════════════════════════════
 REPORT — Run summary for humans and for the workflow
═══════════════════════════════════════════════════════════════
 Turns the list of city records from the scraper into:
   • counts per outcome (ok / empty / unverified / error)
   • the list of UNEXPECTED failures (errors that are not in KNOWN_ISSUES)
   • a Markdown table — shown on the GitHub Actions run page and saved
     as run-summary.md, next to run-results.json with the full data
 No network, no browser: everything here is testable offline.
═══════════════════════════════════════════════════════════════
"""

import json
import os

from config import KNOWN_ISSUES, MAX_UNEXPECTED_FAILURES
from outcomes import OK, EMPTY, UNVERIFIED, ERROR, describe

ICON = {OK: "✅", EMPTY: "⬜", UNVERIFIED: "❓", ERROR: "❌"}

SUMMARY_MD = "run-summary.md"
RESULTS_JSON = "run-results.json"


def summarise(results: list) -> dict:
    """Counts per outcome plus the total number of result links."""
    counts = {OK: 0, EMPTY: 0, UNVERIFIED: 0, ERROR: 0}
    for r in results:
        counts[r.get("outcome", ERROR)] = counts.get(r.get("outcome", ERROR), 0) + 1
    counts["links"] = sum(len(r.get("results", [])) for r in results)
    return counts


def problem_cities(results: list) -> list:
    """Every city that did not produce a trustworthy answer."""
    return [r for r in results if r.get("outcome") in (UNVERIFIED, ERROR)]


def unexpected_failures(results: list) -> list:
    """Problem cities that are NOT already on the known-issues list.
    These are the ones that should make the workflow go red."""
    return [r for r in problem_cities(results) if r["city"] not in KNOWN_ISSUES]


def run_failed(results: list) -> bool:
    """True when the run should be marked failed: more UNEXPECTED failures
    than MAX_UNEXPECTED_FAILURES. Known issues never count, so a run over
    only Ludwigshafen and Cologne is not "failed"; a total outage of the
    whole list trips the threshold on its own."""
    return len(unexpected_failures(results)) > MAX_UNEXPECTED_FAILURES


def to_markdown(results: list, date_label: str = "") -> str:
    """The per-city table shown on the Actions run page."""
    c = summarise(results)
    lines = [
        f"## 🏛️ Council Monitor — {date_label}".rstrip(" —"),
        "",
        f"**{c['links']}** result links · {ICON[OK]} {c[OK]} with results · "
        f"{ICON[EMPTY]} {c[EMPTY]} empty (verified) · "
        f"{ICON[UNVERIFIED]} {c[UNVERIFIED]} unverified · {ICON[ERROR]} {c[ERROR]} errors",
        "",
        "| City | Outcome | Results | HTTP | Time | Category | Detail |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for r in results:
        outcome = r.get("outcome", ERROR)
        known = " _(known issue)_" if r["city"] in KNOWN_ISSUES and outcome in (ERROR, UNVERIFIED) else ""
        detail = (r.get("error") or "").replace("|", "\\|").replace("\n", " ")
        if len(detail) > 160:
            detail = detail[:157] + "..."
        lines.append(
            f"| {r['city']}{known} | {ICON[outcome]} {outcome} | {len(r.get('results', []))} "
            f"| {r.get('http_status') or ''} | {r.get('duration_s', '')}s "
            f"| {r.get('error_category') or ''} | {detail} |"
        )

    problems = problem_cities(results)
    if problems:
        lines += ["", "### What the categories mean", ""]
        seen = set()
        for r in problems:
            cat = r.get("error_category") or ERROR
            if cat in seen:
                continue
            seen.add(cat)
            meaning, action = describe(cat)
            lines.append(f"- **{cat}** — {meaning} _Next step:_ {action}")

    unexpected = unexpected_failures(results)
    lines += [
        "",
        f"Unexpected failures: **{len(unexpected)}** "
        f"(threshold {MAX_UNEXPECTED_FAILURES}; known issues excluded: "
        f"{', '.join(KNOWN_ISSUES) or 'none'}).",
    ]
    return "\n".join(lines) + "\n"


def write_artifacts(results: list, date_label: str = "") -> str:
    """Save run-summary.md and run-results.json in the working directory and,
    when running inside GitHub Actions, append the summary to the run page."""
    md = to_markdown(results, date_label)
    with open(SUMMARY_MD, "w", encoding="utf-8") as f:
        f.write(md)
    with open(RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as f:
                f.write(md)
        except OSError:
            pass
    return md
