"""What this family scrapes: Indian budget data. Active: G02 (state budgets, Open Budgets India). Parked: G01 (Union Budget documents).

FAMILY must equal the repo name (s1d-labs/<FAMILY>) and the family the hub owner added on scrape.s1d.me/runners.
Each project is one queue of work on one site; `target` is the host it hits (the hub caps workers per target).

G01 URL layout (from search-engine results; the site itself refuses self-identified crawlers, see reportG01.md):
  current year      https://www.indiabudget.gov.in/doc/<file>
  earlier years     https://www.indiabudget.gov.in/budget2025-26/doc/<file>   (seen back to budget2021-22)
  per-demand notes  doc/eb/sbe<N>.pdf  (Notes on Demands for Grants, one per demand, ~1-102)
  older archive     https://www.indiabudget.gov.in/budget_archive/ub2005-06/...  (HTML, layout varies by year)
"""
import json
from pathlib import Path

FAMILY = json.loads((Path(__file__).parent / "scrape.json").read_text())["family"]
SITE = "https://www.indiabudget.gov.in"            # the bare indiabudget.gov.in host does not answer; use www.
CURRENT = "2026-27"                                # served from /doc/ rather than /budget<year>/doc/
YEARS = ["2026-27", "2025-26", "2024-25", "2023-24", "2022-23", "2021-22"]
DEMANDS = range(1, 106)                            # sbe<N>.pdf; numbers that do not exist answer 404 and are skipped


def doc_base(year):
    return f"{SITE}/doc/" if year == CURRENT else f"{SITE}/budget{year}/doc/"


def year_docs(year):
    """Queue items for one budget year: the compiled volumes, the outcome budget, and one item per demand."""
    y1, y2 = year.split("-")
    outcome = f"OutcomeBudgetE{y1}_{y1[:2]}{y2}.pdf"
    docs = [("budget_speech", "budget_speech.pdf"), ("expenditure_budget", "eb/allsbe.pdf"),
            ("demands_for_grants", "eb/alldg.pdf"), ("outcome_budget", outcome)]
    docs += [(f"sbe{n}", f"eb/sbe{n}.pdf") for n in DEMANDS]
    return [{"key": f"{year}/{name}", "payload": {"kind": "pdf", "year": year, "doc": name, "url": doc_base(year) + path,
                                                  "expected": 1}} for name, path in docs]


INDEX_PAGES = [("home", f"{SITE}/"), ("budget_archive", f"{SITE}/budget_archive/"),
               ("economic_surveys", f"{SITE}/economicsurvey/allpes.php")]

# G01 is parked: www.indiabudget.gov.in (Akamai) answers 403 to this scraper (reportG01.md), so it is kept out of PROJECTS and
# PROBES (not registered by setup, not tested by check). To bring it back: PROJECTS.insert(0, UNION_DOCS) and add UNION_DOCS_PROBE.
UNION_DOCS = {
    "name": f"{FAMILY}-union-docs",
    "title": "Union Budget documents (indiabudget.gov.in)",
    "task": "crawl",
    "target": "www.indiabudget.gov.in",
    "env": {"SITE": SITE},
    "probe": f"{FAMILY}:indiabudget.gov.in",
    "unit_label": "document", "record_label": "document",
    # index pages list the documents linked from them (records only); pdf items download one document each
    "items": [{"key": f"index/{name}", "payload": {"kind": "index", "url": url, "expected": 50}} for name, url in INDEX_PAGES]
             + [item for year in YEARS for item in year_docs(year)],
}
UNION_DOCS_PROBE = {"name": f"{FAMILY}:indiabudget.gov.in", "url": f"{SITE}/", "expect": "Union Budget"}

# G02: Open Budgets India's normalised state indicators, from the copy in CBGA's GitHub repo (openbudgetsindia.org is gone)
OBI_SOURCE = "https://raw.githubusercontent.com/cbgaindia/story-generator/972ac485e67afe95ec7ab0bf67222cdc27c4f9de/src/data"
OBI_STATES = {
    "name": f"{FAMILY}-obi-states",
    "title": "State budget indicators (Open Budgets India, 26 states, 2012-13 to 2017-18)",
    "task": "obi",
    "target": "raw.githubusercontent.com",
    "env": {"OBI_SOURCE": OBI_SOURCE},
    "probe": f"{FAMILY}:obi-source",
    "unit_label": "dataset", "record_label": "value",
    "items": [{"key": d, "payload": {"dataset": d, "data_url": f"{OBI_SOURCE}/{d}_data.js", "meta_url": f"{OBI_SOURCE}/{d}_metadata.js",
                                     "expected": n}} for d, n in (("expenditure", 14239), ("receipts", 2736))],
}

# G02, later years: OBI's CKAN catalogue as archived by the Wayback Machine. One item per 2-character prefix of the path under
# openbudgetsindia.org/dataset/ (CKAN names and resource ids are [0-9a-z-_]), so each item is one small CDX listing plus its files.
# No probe: the hub shares the team server's IP, which web.archive.org rate-limits (429), so a probe would hold the work for good.
# max_workers 2: archive.org is a non-profit; be gentle.
ALNUM = "0123456789abcdefghijklmnopqrstuvwxyz"
OBI_WAYBACK = {
    "name": f"{FAMILY}-obi-wayback",
    "title": "Open Budgets India catalogue, from the Wayback Machine (datasets and data files)",
    "task": "wayback",
    "target": "web.archive.org",
    "env": {},
    "max_workers": 2,
    "unit_label": "prefix", "record_label": "dataset or file",
    "items": [{"key": a + b, "payload": {"prefix": f"openbudgetsindia.org/dataset/{a}{b}"}} for a in ALNUM for b in ALNUM],
}

PROJECTS = [OBI_STATES, OBI_WAYBACK]

# health checks the hub runs every minute; work for a project holds while its probe fails
PROBES = [{"name": f"{FAMILY}:obi-source", "url": f"{OBI_SOURCE}/receipts_metadata.js", "expect": "receipts_metadata"}]
