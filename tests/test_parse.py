"""Parsing tests on saved pages: fast, no network. The index page is synthetic until the site lets us save a real one."""
from pathlib import Path

import crawl
import family

PAGES = Path(__file__).parent / "pages"
BASE = "https://www.indiabudget.gov.in/"


def test_index_page_lists_documents():
    rows = dict(crawl.parse((PAGES / "synthetic-index.html").read_text(encoding="utf-8"), BASE))
    assert list(rows) == ["doc/budget_speech.pdf", "doc/eb/allsbe.pdf", "doc/eb/sbe35.pdf",
                          "budget2025-26/doc/OutcomeBudgetE2025_2026.pdf", "budget_archive/ub2005-06/eb/stat1.xls"]
    assert rows["doc/eb/allsbe.pdf"]["title"] == "Expenditure Budget (Notes on Demands)"
    assert rows["doc/eb/sbe35.pdf"]["title"] == "No. 35 & Department of Revenue"     # first link wins, entities decoded
    assert rows["doc/eb/sbe35.pdf"]["url"] == BASE + "doc/eb/sbe35.pdf"
    assert rows["budget2025-26/doc/OutcomeBudgetE2025_2026.pdf"]["year"] == "2025-26"
    assert rows["budget_archive/ub2005-06/eb/stat1.xls"]["format"] == "xls"


def test_an_empty_page_gives_nothing():
    assert crawl.parse("<html><body>Access Denied</body></html>", BASE) == []


def test_describe_pdf():
    rid, data = crawl.describe_pdf(b"%PDF-1.7 x", {"url": BASE + "budget2024-25/doc/eb/sbe1.pdf", "year": "2024-25", "doc": "sbe1"})
    assert rid == "budget2024-25/doc/eb/sbe1.pdf" and data["pdf"] and data["bytes"] == 10


def test_queue_urls_follow_the_site_layout():
    items = {i["key"]: i["payload"]["url"] for i in family.UNION_DOCS["items"]}
    assert items["2026-27/outcome_budget"] == "https://www.indiabudget.gov.in/doc/OutcomeBudgetE2026_2027.pdf"
    assert items["2024-25/outcome_budget"] == "https://www.indiabudget.gov.in/budget2024-25/doc/OutcomeBudgetE2024_2025.pdf"
    assert items["2025-26/expenditure_budget"] == "https://www.indiabudget.gov.in/budget2025-26/doc/eb/allsbe.pdf"
    assert len(items) == len(family.UNION_DOCS["items"])             # keys unique
    assert all(len(k) <= 200 for k in items)
