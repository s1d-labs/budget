"""What this family scrapes: Indian budget data. Active: G02 (state budgets, Open Budgets India), G03 (PFMS public reports: scheme
outlays and the agency registry, stage 2).
G01 (Union Budget documents): the live site is parked (it refuses this scraper); the Wayback Machine's copy runs instead.

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

# G01 from the Wayback Machine (union_wayback.py): every document (PDF, Excel, Word, ZIP) archived from indiabudget.gov.in, one item
# per distinct content of each path (16,163 items, ~9 GB, 1947-48 to 2026-27). The listing is fixed in data/ (union_wayback.py --list).
# No probe and max_workers 2, as for OBI_WAYBACK: archive.org is a non-profit and rate-limits the hub's shared IP.
def _union_wayback_items():
    from union_wayback import item_key, load_listing
    return [{"key": item_key(r), "payload": {**r, "expected": 1}} for r in load_listing()]


UNION_WAYBACK = {
    "name": f"{FAMILY}-union-wayback",
    "title": "Union Budget and Economic Survey documents, from the Wayback Machine's copy of indiabudget.gov.in",
    "task": "union-wayback",
    "target": "web.archive.org",
    "env": {},
    "max_workers": 2,
    "unit_label": "document", "record_label": "document",
    "items": _union_wayback_items(),
}

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

# G03: PFMS public reports: scheme releases, implementing agencies. PFMS moved from pfms.nic.in to pfms.gov.in (the old host now
# answers every path with "This site is now available on new URL https://pfms.gov.in"). Both answer only from India, so geo="IN"
# (India pool servers only). Stage 1 explores the site from India (pfms.py) to learn which reports are public and what their forms
# take; report items follow from what it finds. No probe yet: a probe checked from outside India would hold the work for good.
PFMS_SITE = "https://pfms.gov.in"
_LIST, _GRID = f"{PFMS_SITE}/SitePages/SchemeListing.aspx", "ctl00$cphBody$grdvSearchSchemeGBE"
_AGENCY, _P = f"{PFMS_SITE}/SitePages/SearchAgency.aspx", "ctl00$cphBody$"
# round 2 (from explore/gov): the public scheme list is a paged ASP.NET grid (name [code], grant, plan outlay) per financial year;
# the agency search takes scheme/state/district/type/status. `steps` items post those forms back the way a browser does.
PFMS_ROUND2 = [
    {"key": "explore/dashboard", "payload": {"kind": "explore", "seeds": ["https://pfmsdashboard.gov.in/"], "max_pages": 120,
                                             "max_depth": 2, "expected": 120}},
    {"key": "steps/schemes-2025-26", "payload": {"kind": "steps", "url": _LIST, "expected": 4, "steps": [
        {"fields": {_P + "ddlFinancialYear": "2026"}, "button": _P + "btnSearch"},
        {"target": _GRID, "arg": "Page$2"}, {"target": _GRID, "arg": "Page$Last"}]}},
    {"key": "steps/schemes-css-2026-27", "payload": {"kind": "steps", "url": _LIST, "expected": 2, "steps": [
        {"fields": {_P + "ddlSchemeType": "1"}, "button": _P + "btnSearch"}]}},
    {"key": "steps/agencies-bihar", "payload": {"kind": "steps", "url": _AGENCY, "expected": 3, "steps": [
        {"fields": {_P + "ddlState": "5"}, "button": _P + "btnSearch"}, {"target": _P + "gvAgency", "arg": "Page$2"}]}},
    {"key": "steps/agencies-central-govt", "payload": {"kind": "steps", "url": _AGENCY, "expected": 2, "steps": [
        {"fields": {_P + "ddlType": "5/05", _P + "ddlStatus": "43"}, "button": _P + "btnSearch"}]}},
    {"key": "raw/lookups", "payload": {"kind": "raw", "expected": 5, "urls": [
        f"{PFMS_SITE}/Agency/GetAllScheme.aspx?query=PM&c=ctl00_cphBody_ucSchemes_txtBxScheme&access=yes",
        f"{PFMS_SITE}/Agency/GetAllScheme.aspx?query=9179&c=ctl00_cphBody_ucSchemes_txtBxScheme&access=yes",
        f"{PFMS_SITE}/SitePages/js/LoginAndPassword.js", f"{PFMS_SITE}/SitePages/js/CommonFunction.js",
        f"{PFMS_SITE}/SitePages/SchemeDetail.aspx?sid=C8pUwxhQN2Y=&gbid=Y2DIa99jn48="]}},
]
# round 3: whole-state / whole-type agency searches timed out after 60 s from India; try narrow ones with a long timeout.
# pfmsdashboard.gov.in did not connect from India over https at the bare host; try www. and http.
PFMS_ROUND3 = [
    {"key": "explore/dashboard-www", "payload": {"kind": "explore", "seeds": ["https://www.pfmsdashboard.gov.in/",
                                                                             "http://pfmsdashboard.gov.in/"],
                                                 "max_pages": 120, "max_depth": 2, "expected": 120}},
    {"key": "steps/agencies-bihar-local-approved", "payload": {"kind": "steps", "url": _AGENCY, "expected": 2, "steps": [
        {"fields": {_P + "ddlState": "5", _P + "ddlType": "4/04", _P + "ddlStatus": "43"}, "button": _P + "btnSearch",
         "timeout": 240}]}},
    {"key": "steps/agencies-goa-ngo", "payload": {"kind": "steps", "url": _AGENCY, "expected": 2, "steps": [
        {"fields": {_P + "ddlState": "10", _P + "ddlType": "7/07"}, "button": _P + "btnSearch", "timeout": 240}]}},
    {"key": "steps/agencies-name-university", "payload": {"kind": "steps", "url": _AGENCY, "expected": 2, "steps": [
        {"fields": {_P + "txtName": "UNIVERSITY", _P + "ddlState": "10"}, "button": _P + "btnSearch", "timeout": 240}]}},
]
PFMS = {
    "name": f"{FAMILY}-pfms",
    "title": "PFMS public reports (pfms.gov.in): scheme releases, implementing agencies",
    "task": "pfms",
    "target": "pfms.gov.in",
    "env": {"SITE": PFMS_SITE},
    "geo": "",                                     # was "IN" (India servers); since 2026-11-01 the hub's Indian proxies
    "proxy": "IN",
    "max_workers": 1,
    "unit_label": "item", "record_label": "page",
    # explore/home (done) walked the old pfms.nic.in, which only says it moved; explore/gov walks the new site
    "items": [{"key": "explore/home", "payload": {"kind": "explore", "seeds": ["https://pfms.nic.in/", "https://pfms.nic.in/NewDefaultHome.aspx"],
                                                  "max_pages": 200, "max_depth": 3, "expected": 2}},
              {"key": "explore/gov", "payload": {"kind": "explore", "seeds": [f"{PFMS_SITE}/", "https://www.pfms.gov.in/"],
                                                 "max_pages": 250, "max_depth": 3, "expected": 250}}]
             + PFMS_ROUND2 + PFMS_ROUND3,
}

# G03 stage 2: the two public lists stage 1 found, walked page by page (pfms.py `schemes`, `agencies`), from GitHub pool workers
# through the hub's Indian proxies (one worker per proxy IP; the family borrows at most 4).
SCHEME_TYPES = {"0": "All", "1": "Centrally Sponsored Scheme", "2": "Central Sector Scheme", "14": "UT", "15": "Other Central Expenditure",
                "16": "Finance Commission Transfers", "17": "Other Transfers", "18": "Establishment Expenditure"}
FY_VALUES = [str(y) for y in range(2009, 2028)]   # dropdown value = the year the FY ends: 2009 = 2008-09 ... 2027 = 2026-27
PFMS_SCHEMES = {
    "name": f"{FAMILY}-pfms-schemes",
    "title": "PFMS Scheme Listing: every Union scheme's plan outlay per year, 2008-09 to 2026-27 (pfms.gov.in)",
    "task": "pfms", "target": "pfms.gov.in", "env": {"SITE": PFMS_SITE}, "geo": "", "proxy": "IN", "max_workers": 2,
    "unit_label": "year x scheme type", "record_label": "scheme",
    # type "0" (All) is the year's whole list: older years tag few schemes with a type (2014-15: 6 CSS), and some schemes have
    # none. Newest years first; 2008-09 is nearly empty on the site.
    "items": [{"key": f"schemes/{fy}/{t}", "payload": {"kind": "schemes", "fy": fy, "scheme_type": t, "scheme_type_name": name,
                                                       "expected": 600 if t == "0" else 60}}
              for fy in reversed(FY_VALUES) for t, name in SCHEME_TYPES.items()],
}
AGENCY_STATES = ["1", "2", "3", "4", "5", "36", "6", "33", "9", "10", "11", "12", "13", "14", "34", "15", "16", "38", "19", "17", "18",
                 "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "37", "7", "30", "31", "35", "32"]   # 36 = Central Agency
AGENCY_TYPES = ["5/05", "1/01", "2/02", "9/09", "4/04", "3/03", "7/07", "8/08", "10/10", "12/12", "13/13", "14/14", "15/15", "16/16",
                "17/17", "18/18"]                  # all but 11/11 Individuals: people, personal data (DPDP Act), never queued
PFMS_AGENCIES = {
    "name": f"{FAMILY}-pfms-agencies",
    "title": "PFMS implementing agency registry by state and type, all statuses, without individuals (pfms.gov.in)",
    "task": "pfms", "target": "pfms.gov.in", "env": {"SITE": PFMS_SITE}, "geo": "", "proxy": "IN", "max_workers": 2,
    "unit_label": "state x agency type", "record_label": "agency",
    "items": [{"key": f"agencies/{st}/{t.split('/')[1]}", "payload": {"kind": "agencies", "state": st, "type": t, "expected": 200}}
              for st in AGENCY_STATES for t in AGENCY_TYPES],
}
# G03 re-run (report-G03 §4.1, §10): 27 agency items were marked done with 0 rows (the site answered "No records" under load after
# earlier attempts had stored rows), and 9 failed after 40 attempts; schemes/2015/0 failed at page 43. seed_queue keeps existing keys,
# so each one is queued again under a new key "<old key>/r2", same payload, all statuses (a split by ddlStatus misses the statuses
# the dropdown does not offer, e.g. "Pending Agency Approval"). pfms.py now re-checks a "No records" first page and fails an empty
# list on a later attempt.
AGENCY_RERUN = ["14/16", "13/04", "4/13", "34/04", "11/08", "14/04", "16/04", "26/04", "37/04", "35/04", "27/04", "15/13", "12/04",
                "24/04", "27/13", "32/04", "18/04", "31/03", "11/04", "18/13", "11/03", "31/04", "29/04", "5/03", "33/13", "9/04",
                "32/13",                                                                 # done with 0 rows
                "16/13", "12/13", "14/13", "3/04", "34/03", "5/13", "29/13", "33/03", "17/13"]   # failed
SCHEME_RERUN = ["schemes/2015/0"]


def rerun(items, keys):
    by_key = {i["key"]: i for i in items}
    return [{"key": f"{k}/r2", "payload": dict(by_key[k]["payload"])} for k in keys]


PFMS_AGENCIES["items"] += rerun(PFMS_AGENCIES["items"], [f"agencies/{k}" for k in AGENCY_RERUN])
PFMS_SCHEMES["items"] += rerun(PFMS_SCHEMES["items"], SCHEME_RERUN)

PROJECTS = [OBI_STATES, OBI_WAYBACK, PFMS, PFMS_SCHEMES, PFMS_AGENCIES, UNION_WAYBACK]

# health checks the hub runs every minute; work for a project holds while its probe fails
PROBES = [{"name": f"{FAMILY}:obi-source", "url": f"{OBI_SOURCE}/receipts_metadata.js", "expect": "receipts_metadata"}]
