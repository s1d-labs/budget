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
    assert len(sch["items"]) == 19 * 8 + 1 and sch["items"][0]["key"] == "schemes/2027/0"
    assert all(i["payload"]["type"] != "11/11" for i in ag["items"])        # Individuals: personal data, never queued
    assert len(ag["items"]) == 37 * 16 + 36 and len({i["key"] for i in ag["items"]}) == len(ag["items"])
    assert len({i["key"] for i in sch["items"]}) == len(sch["items"])


def test_rerun_items_copy_the_old_payloads():
    p = {p["name"]: p for p in family.PROJECTS}
    ag = {i["key"]: i["payload"] for i in p["budget-pfms-agencies"]["items"]}
    sch = {i["key"]: i["payload"] for i in p["budget-pfms-schemes"]["items"]}
    reruns = [k for k in ag if k.endswith("/r2")]
    assert len(reruns) == 36 and all(ag[k] == ag[k[:-3]] and "status" not in ag[k] for k in reruns)
    assert ag["agencies/31/04/r2"] == {"kind": "agencies", "state": "31", "type": "4/04", "expected": 200}
    assert sch["schemes/2015/0/r2"] == sch["schemes/2015/0"]


# ---- false "No records" (report-G03 §4.1): 27 agency items were marked done with 0 rows after earlier attempts stored thousands ----

def _walk_first_page(monkeypatch, answers):
    """Run grid_walk() on the agency form with the site answering the posts with `answers` in turn; return (first page yielded,
    posts made, hub counters)."""
    class Resp:
        def __init__(self, text, url):
            self.text, self.url, self.status_code, self.content = text, url, 200, text.encode()
    posts = []
    answers = list(answers)
    monkeypatch.setattr(pfms.requests, "Response", Resp)
    monkeypatch.setattr(pfms.time, "sleep", lambda s: None)
    monkeypatch.setattr(pfms, "fetch", lambda hub, s, url, timeout=0: Resp(AGENCY, url))
    monkeypatch.setattr(pfms, "post", lambda hub, s, url, data, referer, timeout=60: posts.append(data) or Resp(answers.pop(0), url))
    hub = pfms.LocalHub()
    walk = pfms.grid_walk(hub, None, pfms.AGENCY_URL, {"ctl00$cphBody$ddlState": "10"}, pfms.AGENCY_GRID)
    n, page = next(walk)
    assert n == 1
    return page, posts, hub.counts


def test_no_records_first_page_is_posted_again(monkeypatch):
    page, posts, counts = _walk_first_page(monkeypatch, [EMPTY_AG, AG1])      # "No records" under load, then the real page 1
    assert page == AG1 and len(posts) == 2 and counts.get("no_records_recheck") == 1
    assert posts[0] == posts[1] and posts[1]["ctl00$cphBody$btnSearch"] == "Search"   # the same search, from the form page


def test_no_records_twice_is_accepted(monkeypatch):
    page, posts, counts = _walk_first_page(monkeypatch, [EMPTY_AG, EMPTY_AG])
    assert page == EMPTY_AG and len(posts) == 2 and counts.get("no_records_recheck") == 1


def test_normal_first_page_is_not_posted_again(monkeypatch):
    page, posts, counts = _walk_first_page(monkeypatch, [AG1])
    assert page == AG1 and len(posts) == 1 and "no_records_recheck" not in counts


def test_agency_status_filter_goes_into_the_search(monkeypatch):
    seen = []
    monkeypatch.setattr(pfms, "grid_walk", lambda hub, s, url, fields, grid: seen.append(fields) or iter([(1, AG1)]))
    hub = pfms.LocalHub()
    hub.record = lambda rid, data: None
    pfms.agencies(hub, None, {"key": "k", "payload": {"kind": "agencies", "state": "10", "type": "7/07", "status": "43"}}, None)
    pfms.agencies(hub, None, {"key": "k", "payload": {"kind": "agencies", "state": "10", "type": "7/07"}}, None)
    assert seen[0]["ctl00$cphBody$ddlStatus"] == "43" and "ctl00$cphBody$ddlStatus" not in seen[1]


def _run_main(monkeypatch, attempts, n):
    """Run main() on one agency item whose walk gives n rows; return the hub's complete() calls."""
    import sys
    import types
    done = []

    class FakeHub:
        settings = {}

        def __init__(self, *a, **k):
            self.items = [{"key": "agencies/14/16", "payload": {"kind": "agencies", "state": "14", "type": "16/16"},
                           "attempts": attempts}]

        def claim(self, *a, **k):
            return [self.items.pop()] if self.items else []

        def complete(self, keys, status, error=None, fetched=None):
            done.append((keys[0], status, fetched, error))

        def close(self, *a):
            pass
    monkeypatch.setitem(sys.modules, "scrape_hub_client", types.SimpleNamespace(HubClient=FakeHub))
    monkeypatch.setitem(pfms.KINDS, "agencies", lambda hub, s, item, store: (n, None))
    pfms.main()
    return done


def test_empty_list_on_a_later_attempt_fails(monkeypatch):
    assert _run_main(monkeypatch, attempts=1, n=0) == [("agencies/14/16", "done", 0, None)]       # first try: a real empty list
    key, status, _, error = _run_main(monkeypatch, attempts=3, n=0)[0]
    assert status == "failed" and "attempt 3" in error                                               # not done with 0 rows
    assert _run_main(monkeypatch, attempts=3, n=120) == [("agencies/14/16", "done", 120, None)]


# ---- long walks: a failed page is reached again through the pager, not by a new walk from page 1; a walk that cannot end in the
# time left is not started ----

def test_pager_numbers_on_saved_pages():
    assert pfms.linked_pages(SCH1, pfms.LIST_GRID) == list(range(2, 12))           # 2-10, and "..." -> 11
    assert pfms.current_page(SCH1, pfms.LIST_GRID) == 1
    assert pfms.current_page(SCH10, pfms.LIST_GRID) == 10 and 11 in pfms.linked_pages(SCH10, pfms.LIST_GRID)
    assert pfms.current_page(AG5, pfms.AGENCY_GRID) == 5 and pfms.linked_pages(AG5, pfms.AGENCY_GRID) == [1, 2, 3, 4]
    assert pfms.current_page(EMPTY_AG, pfms.AGENCY_GRID) is None


def fake_grid(n, total, grid=pfms.AGENCY_GRID):
    """A result page like the site's: page n of `total`, a pager of 10 numbers with "..." links before and after them."""
    s = (n - 1) // 10 * 10 + 1
    link = lambda k, label: f"<td><a href=\"javascript:__doPostBack('{grid}','Page${k}')\">{label}</a></td>"
    cells = ([link(s - 1, "...")] if s > 1 else []) + [f"<td><span>{k}</span></td>" if k == n else link(k, k)
                                                       for k in range(s, min(s + 9, total) + 1)]
    cells += [link(s + 10, "...")] if s + 10 <= total else []
    rows = "".join(f"<tr><td>AGENCY {n}-{i}</td>" + "<td>x</td>" * 8 + "</tr>" for i in range(10))
    return (f'<html><title>Search Agency</title><form method="post" action="SearchAgency.aspx" id="aspnetForm">'
            f'<input type="hidden" name="__VIEWSTATE" value="p{n}">'
            f'<table id="{grid.replace("$", "_")}"><tr><th>Name</th>' + "<th>h</th>" * 8 + f"</tr>{rows}"
            f'<tr><td><table><tr>{"".join(cells)}</tr></table></td></tr></table></form></html>')


class FakeGridSite:
    """Answers the agency form like pfms.gov.in: a search gives page 1; Page$k gives page k only when the page it was posted from
    links to it (the real site answers anything else with its error page); Page$Last gives the last page. `fail` = {page: number of
    posts of that page that get an empty grid}."""

    def __init__(self, monkeypatch, total, fail=None, fail_searches=()):
        self.total, self.fail, self.posts, self.sessions = total, dict(fail or {}), [], set()
        self.fail_searches, self.n_search = set(fail_searches), 0          # search posts (1 = the first) that get an empty grid

        class Resp:
            def __init__(self, text, url):
                self.text, self.url, self.status_code, self.content = text, url, 200, text.encode()
        self.Resp = Resp
        monkeypatch.setattr(pfms.requests, "Response", Resp)
        monkeypatch.setattr(pfms.requests, "Session", object)
        monkeypatch.setattr(pfms.time, "sleep", lambda s: None)
        monkeypatch.setattr(pfms, "fetch", lambda hub, s, url, timeout=0: Resp(AGENCY, url))
        monkeypatch.setattr(pfms, "post", self.post)

    def post(self, hub, session, url, data, referer, timeout=60):
        self.sessions.add(id(session))
        arg, frm = data["__EVENTARGUMENT"], data["__VIEWSTATE"]
        if not arg:
            k = 1
        elif arg == "Page$Last":
            k = self.total
        else:
            k = int(arg[5:])
            src = int(frm[1:]) if frm.startswith("p") else 0
            assert k in pfms.linked_pages(fake_grid(src, self.total), pfms.AGENCY_GRID), f"page {k} posted from page {src}"
        self.posts.append(arg or "search")
        self.n_search += not arg
        if not arg and self.n_search in self.fail_searches:
            return self.Resp(EMPTY_AG.replace("No Records Found", ""), url)
        if self.fail.get(k):
            self.fail[k] -= 1
            return self.Resp(EMPTY_AG.replace("No Records Found", ""), url)
        return self.Resp(fake_grid(k, self.total), url)


def walk(total, monkeypatch, fail=None, deadline=None, fail_searches=()):
    site = FakeGridSite(monkeypatch, total, fail, fail_searches)
    monkeypatch.setattr(pfms, "DEADLINE", deadline or pfms.time.time() + 3600)
    hub = pfms.LocalHub()
    got = []
    try:
        for n, page in pfms.grid_walk(hub, "first session", pfms.AGENCY_URL, {"ctl00$cphBody$ddlState": "1"}, pfms.AGENCY_GRID):
            assert pfms.current_page(page, pfms.AGENCY_GRID) == n
            got.append(n)
        return got, None, site, hub.counts
    except pfms.ItemFailed as e:
        return got, e, site, hub.counts


def test_walk_goes_through_every_page(monkeypatch):
    got, err, site, counts = walk(35, monkeypatch)
    assert err is None and got == list(range(1, 36))
    assert site.posts[:2] == ["search", "Page$Last"] and "walk_restart" not in counts


def test_failed_page_is_reached_again_by_pager_jumps(monkeypatch):
    got, err, site, counts = walk(35, monkeypatch, fail={27: pfms.PAGE_TRIES})
    assert err is None and got == list(range(1, 36))                       # every page once, none lost or repeated
    assert counts["walk_restart"] == 1 and len(site.sessions) == 2           # a new search in a new session ...
    i = len(site.posts) - 1 - site.posts[::-1].index("search")
    assert site.posts[i:i + 4] == ["search", "Page$11", "Page$21", "Page$27"]   # ... then 3 jumps, not 26 pages


def test_page_that_never_loads_fails_after_the_restarts(monkeypatch):
    got, err, site, counts = walk(35, monkeypatch, fail={27: 1000})
    assert got == list(range(1, 27)) and counts["walk_restart"] == pfms.WALK_RESTARTS
    assert "page 27" in str(err) and f"{pfms.WALK_RESTARTS} restarts" in str(err)


def test_restart_survives_a_failed_search(monkeypatch):
    # page 27 fails; the first new search fails too (search posts 2-5, PAGE_TRIES each); the second new search gets there
    got, err, site, counts = walk(35, monkeypatch, fail={27: pfms.PAGE_TRIES}, fail_searches=range(2, 2 + pfms.PAGE_TRIES))
    assert err is None and got == list(range(1, 36)) and counts["walk_restart"] == 2


def test_walk_too_long_for_the_time_left_is_not_started(monkeypatch):
    got, err, site, counts = walk(1500, monkeypatch, deadline=pfms.time.time() + 600)      # ~75 min of pages, 10 min left
    assert isinstance(err, pfms.NotEnoughTime) and "1500 pages" in str(err) and got == [1]
    assert site.posts == ["search", "Page$Last"]
    got, err, site, counts = walk(150, monkeypatch, deadline=pfms.time.time() + 3600)
    assert err is None and len(got) == 150


def test_not_enough_time_stops_the_process(monkeypatch):
    import sys
    import types
    done, claims = [], []

    class FakeHub:
        settings = {}

        def __init__(self, *a, **k):
            pass

        def claim(self, *a, **k):
            claims.append(1)
            return [{"key": f"agencies/1/{len(claims)}", "payload": {"kind": "agencies"}, "attempts": 1}]

        def complete(self, keys, status, error=None, fetched=None):
            done.append((keys[0], status, error))

        def close(self, *a):
            pass

    def too_long(hub, s, item, store):
        raise pfms.NotEnoughTime("not enough time: 1500 pages")
    monkeypatch.setitem(sys.modules, "scrape_hub_client", types.SimpleNamespace(HubClient=FakeHub))
    monkeypatch.setitem(pfms.KINDS, "agencies", too_long)
    pfms.main()
    assert done == [("agencies/1/1", "failed", "not enough time: 1500 pages")] and len(claims) == 1
