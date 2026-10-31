"""Parsing tests for the G03 explorer (pfms.py). The page is synthetic, shaped like an ASP.NET PFMS report page, until a real one is
saved from an Indian IP."""
from pathlib import Path

import family
import pfms

PAGE = (Path(__file__).parent / "pages" / "pfms" / "synthetic-report.html").read_text(encoding="utf-8")
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
    form = pfms.parse_page(PAGE, URL)["forms"][0]
    assert form["method"] == "post" and form["action"] == "https://pfms.nic.in/Static/SchemeRelease.aspx?rpt=1"
    names = {f["name"]: f for f in form["fields"]}
    assert names["__VIEWSTATE"]["bytes"] == 24 and "value" not in names["__VIEWSTATE"]
    assert names["ctl00$btnSubmit"]["value"] == "Submit"
    assert form["selects"]["ctl00$ddlFinYear"]["autopostback"]
    assert form["selects"]["ctl00$ddlScheme"]["options"] == [["-1", "--Select--"], ["9179", "PM-KISAN & Allied"]]


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
    assert p["geo"] == "IN" and p["task"] == "pfms" and "probe" not in p
