"""What this family scrapes: Union Budget documents from www.indiabudget.gov.in (G01).

FAMILY must equal the repo name (s1d-labs/<FAMILY>) and the family the hub owner added on scrape.s1d.me/runners.
Each project is one queue of work on one site; `target` is the host it hits (the hub caps workers per target).

URL layout (from search-engine results; the site itself refuses self-identified crawlers, see reportG01.md):
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

PROJECTS = [
    {
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
    },
]

# health checks the hub runs every minute; work for a project holds while its probe fails (it fails while Akamai refuses us)
PROBES = [{"name": f"{FAMILY}:indiabudget.gov.in", "url": f"{SITE}/", "expect": "Union Budget"}]
