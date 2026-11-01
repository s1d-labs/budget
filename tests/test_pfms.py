"""Parsing tests for the G03 explorer (pfms.py): a synthetic ASP.NET page, and two real pfms.gov.in pages saved by the pool's first
exploration from India (2026-10-31): the scheme listing (2026-27, page 1) and the agency search form."""
from pathlib import Path

import family
import pfms

PAGES = Path(__file__).parent / "pages" / "pfms"
PAGE = (PAGES / "synthetic-report.html").read_text(encoding="utf-8")
LISTING = (PAGES / "scheme-listing.html").read_text(encoding="utf-8")
AGENCY = (PAGES / "search-agency.html").read_text(encoding="utf-8")
URL = "https://pfms.nic.in/Static/SchemeRelease.aspx?rpt=1"


def test_page_links_docs_and_postbacks():
    info = pfms.parse_page(PAGE, URL)
    assert info["title"] == "PFMS : Scheme Wise Release"
    urls = [l["url"] for l in info["links"]]
    assert urls == ["https://pfms.nic.in/NewDefaultHome.aspx", "https://pfms.nic.in/static/Reports.aspx",
                    "https://pfms.nic.in/Users/LogOut.aspx", "https://cga.gov.in/", "https://pfms.nic.in/Static/Embedded.aspx"]
    assert info["links"][1]["text"] == "Reports & Dashboards"
    assert info["docs"] == [{"url": "https://pfms.nic.in/writereaddata/circular.pdf", "text": "Circular"}]
    assert {"target": "ctl00$lnkExport", "arg": ""} in info["postbacks"]
    assert info["aspnet"] and info["captcha"]


def test_forms_keep_field_names_and_dropdown_options():
    info = pfms.parse_page(PAGE, URL)
    form = info["forms"][0]
    assert form["method"] == "post" and form["action"] == "https://pfms.nic.in/Static/SchemeRelease.aspx?rpt=1"
    names = {f["name"]: f for f in info["fields"]}
    assert names["__VIEWSTATE"]["bytes"] == 24 and "value" not in names["__VIEWSTATE"]
    assert names["ctl00$btnSubmit"]["value"] == "Submit"
    assert info["selects"]["ctl00$ddlFinYear"]["autopostback"]
    assert info["selects"]["ctl00$ddlScheme"]["options"] == [["-1", "--Select--"], ["9179", "PM-KISAN & Allied"]]


def test_real_listing_fields_inside_nested_forms():
    """pfms.gov.in nests a small search <form> inside the page-wide aspnetForm; the report's own fields must still be found."""
    info = pfms.parse_page(LISTING, "https://pfms.gov.in/SitePages/SchemeListing.aspx")
    years = info["selects"]["ctl00$cphBody$ddlFinancialYear"]["options"]
    assert years[0] == ["2009", "2008-2009"] and years[-1] == ["2027", "2026-2027"] and len(years) == 19
    assert ["1", "Centrally Sponsored Scheme"] in info["selects"]["ctl00$cphBody$ddlSchemeType"]["options"]
    assert {"target": "ctl00$cphBody$grdvSearchSchemeGBE", "arg": "Page$2"} in info["postbacks"]
    agency = pfms.parse_page(AGENCY, "https://pfms.gov.in/SitePages/SearchAgency.aspx")
    assert len(agency["selects"]["ctl00$cphBody$ddlState"]["options"]) == 38
    assert "ctl00$cphBody$ucSchemes$hdnSchemeId" in [f["name"] for f in agency["fields"]]


def test_real_listing_grid():
    grid = next(t for t in pfms.tables(LISTING) if t["id"] == "ctl00_cphBody_grdvSearchSchemeGBE")
    assert grid["header"] == ["Scheme Name", "Grant(Department/Ministry)", "Plan Outlay (In Cr.)"]
    assert grid["rows"][0] == ["ACADEMIC BANK OF CREDITS ABC [3957]", "026 - Department of Higher Education", "10.00"]
    assert ["ADDITIONAL CENTRAL ASSISTANCE FOR EXTERNALLY AIDED PROJECTS (BLOCK LOANS) [4154]", "042 - Transfers to States",
            "47,753.00"] in grid["rows"]


def test_postback_state_and_step():
    action, state = pfms.form_state(LISTING)
    assert action == "SchemeListing.aspx"
    assert state["ctl00$cphBody$ddlFinancialYear"] == "2027" and state["ctl00$cphBody$ddlSchemeType"] == "0"
    assert len(state["__VIEWSTATE"]) > 1000 and "ctl00$cphBody$btnSearch" not in state
    data = pfms.step_data(state, {"fields": {"ctl00$cphBody$ddlFinancialYear": "2026"}, "button": "ctl00$cphBody$btnSearch"})
    assert data["ctl00$cphBody$ddlFinancialYear"] == "2026" and data["ctl00$cphBody$btnSearch"] == "Search"
    assert data["__EVENTTARGET"] == "" and data["__VIEWSTATE"] == state["__VIEWSTATE"]
    page2 = pfms.step_data(state, {"target": "ctl00$cphBody$grdvSearchSchemeGBE", "arg": "Page$2"})
    assert page2["__EVENTARGUMENT"] == "Page$2" and "ctl00$cphBody$btnSearch" not in page2


def test_walk_stays_on_site_and_skips_logout():
    nxt = pfms.next_urls(pfms.parse_page(PAGE, URL))
    assert nxt == ["https://pfms.nic.in/NewDefaultHome.aspx", "https://pfms.nic.in/static/Reports.aspx",
                   "https://pfms.nic.in/Static/Embedded.aspx"]


def test_page_ids():
    assert pfms.page_id("https://pfms.nic.in/Static/A.aspx?x=1") == "page/static/a.aspx?x=1"
    assert pfms.page_id("https://pfms.nic.in/") == "page/"
    long = pfms.page_id("https://pfms.nic.in/a.aspx?" + "q" * 400)
    assert len(long) <= 180


def test_project_runs_in_india():
    p = next(p for p in family.PROJECTS if p["name"] == "budget-pfms")
    assert p["proxy"] == "IN" and p["geo"] == "" and p["task"] == "pfms" and "probe" not in p


def test_steps_flow_posts_back_from_each_page(monkeypatch):
    class Resp:
        def __init__(self, text, url):
            self.text, self.url, self.status_code, self.content = text, url, 200, text.encode()
    posted = []
    monkeypatch.setattr(pfms.requests, "Response", Resp)
    monkeypatch.setattr(pfms, "fetch", lambda hub, s, url, timeout=0: Resp(LISTING, url))
    monkeypatch.setattr(pfms, "post", lambda hub, s, url, data, referer, timeout=60: posted.append((url, data)) or Resp(LISTING, url))
    monkeypatch.setattr(pfms, "GAP_S", 0)
    stored = []
    hub = pfms.LocalHub()
    item = {"key": "steps/x", "payload": {"kind": "steps", "url": "https://pfms.gov.in/SitePages/SchemeListing.aspx", "steps": [
        {"fields": {"ctl00$cphBody$ddlFinancialYear": "2026"}, "button": "ctl00$cphBody$btnSearch"},
        {"target": "ctl00$cphBody$grdvSearchSchemeGBE", "arg": "Page$2"}]}}
    assert pfms.steps(hub, None, item, lambda rid, r, info: stored.append((rid, info))) == (3, 0)
    assert [rid for rid, _ in stored] == ["step/steps/x/0", "step/steps/x/1", "step/steps/x/2"]
    assert posted[0][0] == "https://pfms.gov.in/SitePages/SchemeListing.aspx"
    assert posted[1][1]["__EVENTARGUMENT"] == "Page$2"
    assert stored[1][1]["tables"][0]["n_rows"] == 11


# ---- stage 2: real result pages saved through an Indian proxy on 2026-11-01 ----
# Scheme Listing, 2025-26, Central Sector (type 2): page 1 and page 10 (whose pager runs on to 11 through "...");
# Search Agency, Goa, Registered Societies (NGOs): page 1 of 5 and page 5.
SCH1 = (PAGES / "schemes-cs-2025-26-p1.html").read_text(encoding="utf-8")
SCH10 = (PAGES / "schemes-cs-2025-26-p10.html").read_text(encoding="utf-8")
AG1 = (PAGES / "agencies-goa-ngo-p1.html").read_text(encoding="utf-8")
AG5 = (PAGES / "agencies-goa-ngo-p5.html").read_text(encoding="utf-8")
EMPTY_SCH = (PAGES / "schemes-ut-2014-15-empty.html").read_text(encoding="utf-8")         # 2014-15, type UT: no schemes
EMPTY_AG = (PAGES / "agencies-lakshadweep-private-academic-empty.html").read_text(encoding="utf-8")


def test_grid_rows_drop_header_and_pager():
    rows = pfms.grid_rows(SCH1, pfms.LIST_GRID)
    assert len(rows) == 10 and all(len(r) == 3 for r in rows)
    assert pfms.grid_rows(SCH1, pfms.AGENCY_GRID) == []                     # no such grid on the page: no results
    assert len(pfms.grid_rows(AG1, pfms.AGENCY_GRID)) == 10
    assert 1 <= len(pfms.grid_rows(AG5, pfms.AGENCY_GRID)) <= 10


def test_pager_goes_on_through_the_ellipsis_and_stops_at_the_end():
    assert pfms.has_page(SCH1, pfms.LIST_GRID, 2) and not pfms.has_page(SCH1, pfms.LIST_GRID, 12)
    assert pfms.has_page(SCH10, pfms.LIST_GRID, 11)                         # "..." after 10 posts Page$11
    assert pfms.has_page(AG1, pfms.AGENCY_GRID, 2)
    assert not pfms.has_page(AG5, pfms.AGENCY_GRID, 6)                      # last page: the walk ends


def test_page_checks_catch_a_wrong_or_empty_page():
    assert pfms.page_problem(SCH1, pfms.LIST_GRID, 1) is None
    assert pfms.page_problem(SCH10, pfms.LIST_GRID, 10) is None
    assert pfms.page_problem(SCH1, pfms.LIST_GRID, 4) == "the pager does not show page 4 as current"   # got page 1 back
    assert pfms.page_problem(AG5, pfms.AGENCY_GRID, 5) is None
    assert pfms.page_problem(SCH1, pfms.AGENCY_GRID, 1) == "no result grid"
    assert pfms.page_problem(EMPTY_SCH, pfms.LIST_GRID, 1) is None and pfms.grid_rows(EMPTY_SCH, pfms.LIST_GRID) == []
    assert pfms.page_problem(EMPTY_AG, pfms.AGENCY_GRID, 1) is None
    no_message = EMPTY_AG.replace("No Records Found", "")
    assert pfms.page_problem(no_message, pfms.AGENCY_GRID, 1) is not None


def test_scheme_records():
    recs = pfms.scheme_records(pfms.grid_rows(SCH1, pfms.LIST_GRID), "2026", "2", "Central Sector Scheme")
    assert len(recs) == 10 and len({rid for rid, _ in recs}) == 10
    rid, d = recs[0]
    assert rid == f"scheme-type/2025-26/2/{d['grant_no']}/{d['scheme_code']}" and d["fy"] == "2025-26" and d["scheme_code"].isdigit()
    assert d["scheme_type_name"] == "Central Sector Scheme"
    assert d["grant_no"].isdigit() and d["ministry"] and isinstance(d["outlay_cr"], float)
    assert pfms.scheme_records([["X SCHEME [12]", "042 - Transfers to States", "47,753.00"]], "2027", "0", "All")[0] == (
        "scheme/2026-27/042/12", {"fy": "2026-27", "scheme_code": "12", "scheme": "X SCHEME", "grant_no": "042",
                                  "ministry": "Transfers to States", "outlay_cr": 47753.0, "outlay_text": "47,753.00"})


def test_ut_scheme_codes_have_letters():
    rid, d = pfms.scheme_records([["UT DNH INVESTMENT PROMOTION SCHEME [DN34]", "054 - Dadra and Nagar Haveli", "50.00"]], "2027", "0", "All")[0]
    assert rid == "scheme/2026-27/054/DN34" and d["scheme"] == "UT DNH INVESTMENT PROMOTION SCHEME"


def test_agency_records_keep_no_masked_email():
    recs = pfms.agency_records(pfms.grid_rows(AG1, pfms.AGENCY_GRID), "10", "7/07")
    assert len(recs) == 10 and len({rid for rid, _ in recs}) == 10
    d = recs[0][1]
    assert d["state"] == "GOA" and d["agency_type"] == "Registered Societies (NGOs)" and d["status"]
    assert all(r[1]["code_last4"] is None or r[1]["code_last4"].isdigit() for r in recs)
    assert "@" not in str(recs)
    assert not d["name"].startswith('"')


def test_stage2_queues():
    p = {p["name"]: p for p in family.PROJECTS}
    sch, ag = p["budget-pfms-schemes"], p["budget-pfms-agencies"]
    assert sch["proxy"] == ag["proxy"] == "IN" and sch["geo"] == ag["geo"] == ""
    assert len(sch["items"]) == 19 * 8 and sch["items"][0]["key"] == "schemes/2027/0"
    assert all(i["payload"]["type"] != "11/11" for i in ag["items"])        # Individuals: personal data, never queued
    assert len(ag["items"]) == 37 * 16 and len({i["key"] for i in ag["items"]}) == len(ag["items"])
