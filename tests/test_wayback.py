"""Parsing tests for the `wayback` task. The CDX page and dataset page are synthetic (see the fixtures' notes)."""
from pathlib import Path

import family
import wayback

PAGES = Path(__file__).parent / "pages" / "wayback"


def test_cdx_page_and_resume_key():
    rows, resume = wayback.parse_cdx((PAGES / "cdx-page.json").read_text())
    assert len(rows) == 4 and resume == "openbudgetsindia.org/dataset/st+20200115083555"
    assert rows[0] == {"original": "https://openbudgetsindia.org/dataset/state-budget-bihar-2019-20", "timestamp": "20200115083012",
                       "mimetype": "text/html", "length": "9120"}
    assert wayback.parse_cdx("") == ([], None) and wayback.parse_cdx("[]") == ([], None)


def test_classify():
    rows, _ = wayback.parse_cdx((PAGES / "cdx-page.json").read_text())
    kinds = [wayback.classify(r["original"]) for r in rows]
    assert kinds[0] == ("dataset", "state-budget-bihar-2019-20")
    assert kinds[1] is None                                             # resource page: skipped
    assert kinds[2] == ("file", "2b9a7c3e-1d2f-4e5a-9b8c-7d6e5f4a3b2c", "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0", "bihar budget 2019-20.xlsx")
    assert kinds[3] is None                                             # query strings: activity streams etc.


def test_dataset_page():
    d = wayback.parse_dataset((PAGES / "dataset-page.html").read_text())
    assert d["title"] == "Bihar Budget 2019-20"
    assert d["description"] == "Department-wise budget estimates of Bihar & its sectors, 2019-20."   # not the site-wide blurb
    assert d["organization"] == "bihar" and d["sectors"] == ["education", "health"]
    assert d["tags"] == ["State Budget", "bihar"]
    assert d["resource_ids"] == ["0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0", "9a8b7c6d-5e4f-4321-8765-0fedcba98765"]


def test_queue_covers_all_prefixes():
    items = family.OBI_WAYBACK["items"]
    assert len(items) == 36 * 36 == len({i["key"] for i in items})
    assert items[0]["payload"]["prefix"] == "openbudgetsindia.org/dataset/00"
    assert "budget-union-docs" not in [p["name"] for p in family.PROJECTS]          # G01 parked
    assert "probe" not in family.OBI_WAYBACK                                         # hub IP is rate-limited by archive.org
