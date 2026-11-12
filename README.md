# budget

This family of the s1d-labs scrape pool collects Indian budget data. `family.py` lists the projects.

## Projects

| Project | Source | Task | Notes |
|---|---|---|---|
| `budget-obi-states` | Open Budgets India state indicators, from CBGA's GitHub repo (cbgaindia/story-generator, pinned commit) | `obi` (`obi.py`) | G02: 26 states, 2012-13 to 2017-18 |
| `budget-obi-wayback` | the Open Budgets India catalogue in the Wayback Machine (web.archive.org) | `wayback` (`wayback.py`) | G02: datasets and data files |
| `budget-pfms` | PFMS public reports (pfms.gov.in) | `pfms` (`pfms.py`) | G03: scheme releases, implementing agencies |
| `budget-pfms-schemes` | PFMS Scheme Listing | `pfms` | G03: plan outlay per Union scheme and year, 2008-09 to 2026-27 |
| `budget-pfms-agencies` | PFMS implementing agency registry | `pfms` | G03: by state and type, all statuses, no individuals |

- The PFMS projects run on GitHub runners through the hub's Indian proxies (`proxy="IN"`).
- G01 (Union Budget documents, www.indiabudget.gov.in, task `crawl`, `crawl.py`) is parked: the site answers 403 to the
  scraper. `family.py` keeps the project (`UNION_DOCS`) out of `PROJECTS`.

## Run and check

- `python -m pytest tests` runs the parsing tests on the saved and synthetic pages in `tests/pages/`. No network.
- `scrape-check --items 2` runs setup and a task against a local stand-in hub.
- Run the **setup** workflow after a change to `family.py`.

The full contract for family code is in [CLAUDE.md](CLAUDE.md).
