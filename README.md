# Monitoring-Agent-Ratssysteme

Ein Monitoring Agent, der Ratssystemseiten durchsucht und Ergebnisse weiterleitet.

Every weekday at 09:00 Berlin time, a GitHub Actions workflow searches the
council information systems of 18 German cities for mobility-related
documents (keywords in `config.py`) and posts what it finds to a Teams
channel. This README is the handover guide: how it works, how to run it,
how to read a run, and how to fix a city when its website changes.

---

## How a run works

```
main.py
  ├─ scraper.py      one handler per website type; fills the search form,
  │                  submits it, PROVES it ran, extracts the result links
  ├─ llm_filter.py   optional AI relevance filter (off by default)
  ├─ report.py       run-summary.md + run-results.json + Actions summary
  └─ teams_notify.py results card → team channel · diagnostics → dev channel
```

Every city ends the run in exactly one **outcome** (`outcomes.py`):

| Outcome | Meaning |
|---|---|
| `ok` | results were found |
| `empty` | the search ran, we confirmed it ran, and it found nothing |
| `unverified` | we filled the form and clicked Search but the page did not change — the search probably never ran. **Treat as a failure, not as "no news".** |
| `error` | something broke; it gets a category: `timeout`, `blocked`, `http_error`, `field_not_found`, `layout_changed`, `other` |

Each category has a plain-English meaning and a next step in `outcomes.py`.
They appear in the diagnostics card and on the Actions run page.

## Where to look when something is wrong

1. **GitHub → Actions → the run → Summary.** A table per city: outcome,
   HTTP status, time, category, error. Red run = more than
   `MAX_UNEXPECTED_FAILURES` cities failed that are not in `KNOWN_ISSUES`.
2. **The run's artifact** (`run-<n>`): `run-summary.md`, `run-results.json`
   and `debug/<City>.png` + `debug/<City>.html` for every failed city.
   The HTML is the important one — it shows the real field names when a
   city has changed its search form.
3. **The private dev Teams channel** (if `TEAMS_DEV_WEBHOOK_URL` is set):
   the same diagnostics, delivered every morning.

The team channel only ever sees results and a neutral "Not checked today:
…" line. No error text goes there.

## Running it locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
playwright install chromium
cp .env.example .env            # fill in what you need; nothing is required for a dry run

python main.py --city Essen --no-teams          # one city, print to console
python main.py --city Essen --no-teams --debug  # + screenshots of every step
python main.py --no-teams                       # all cities
python -m pytest -q                             # offline tests (no network)
```

`--no-teams` is the safe way to test: nothing is posted anywhere.

## Testing a change without spamming the team

Open a branch and a pull request — CI (`.github/workflows/ci.yml`) runs the
offline tests on every push. To exercise the real websites from GitHub,
run the *German Council Monitor* workflow manually and **untick
"send_teams"**: the run page still shows the full summary and artifacts,
but nothing is posted to the team channel.

## Fixing a city whose website changed

Symptom: `field_not_found`, `layout_changed`, or a city stuck on `unverified`.

1. Download the run artifact and open `debug/<City>.html` in a browser
   (or a text editor). Find the search form: the keyword box, the date
   fields, the submit button. Note their `name=` / `id=` attributes.
2. Compare with the selectors in that city's handler in `scraper.py`
   (`_scrape_standard` for SessionNet cities; the city name otherwise).
   Add the new selector **at the top of the list** — keep the old ones.
3. `python main.py --city <City> --no-teams --debug` until the outcome is
   `ok` or `empty` (verified), not `unverified`.
4. Open a PR. CI must be green. Merge — the next 09:00 run picks it up.

If a city moved to a different URL, update it in `config.py`.

## Cities that fail for reasons outside the code

Listed in `KNOWN_ISSUES` in `config.py` with the reason. They are still
attempted every run (so we notice when they recover) but do not turn the
run red. Currently Ludwigshafen (Myra WAF blocks GitHub's IPs) and
Cologne (server does not accept connections at all — checked from a home
IP on 23 September 2026 as well). Leipzig is disabled in `CITIES`; its
handler exists.

## Configuration

| Variable | Where | Purpose |
|---|---|---|
| `TEAMS_WEBHOOK_URL` | secret | results card → team channel |
| `TEAMS_DEV_WEBHOOK_URL` | secret, optional | diagnostics card → private dev channel |
| `ENABLE_AI_FILTER` | repository **variable**, default `false` | switch the AI relevance filter on |
| `AZURE_OPENAI_*` | secrets | only needed when the AI filter is on |

`.env.example` lists the same variables for local runs.

## Conventions

- Work on a branch; open a PR; let CI run. Do not edit `main` directly —
  the daily run uses whatever is on `main` at 09:00.
- Keep the old selectors when adding new ones; websites change back.
- When a handler raises, raise `ScrapeError(message, category)` so the
  outcome is classified without guessing.
