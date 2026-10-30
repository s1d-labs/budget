"""Parsing tests for the G02 task on files from cbgaindia/story-generator@972ac48 (expenditure cut to two sectors)."""
from pathlib import Path

import obi

OBI = Path(__file__).parent / "pages" / "obi"


def read(name):
    return (OBI / name).read_text(encoding="utf-8")


def test_per_capita_health_expenditure():
    rows = dict(obi.parse("expenditure", read("expenditure_data_health_agri.js"), read("expenditure_metadata.js")))
    r = rows["expenditure/health/per_capita_expenditure_on_sector/bihar/A/2015-16"]
    assert r["value"] == 448.0 and r["unit"] == "INR Rs." and r["state"] == "Bihar" and r["sector"] == "Health"
    assert r["estimate_name"] == "Actuals" and r["year"] == "2015-16"
    assert rows["expenditure/health/per_capita_expenditure_on_sector/kerala/A/2015-16"]["value"] == 1346.8
    assert {r["state"] for r in rows.values()} >= {"Andhra Pradesh", "Jammu and Kashmir", "West Bengal"}
    assert all(isinstance(r["value"], float) for r in rows.values())


def test_blanks_and_spreadsheet_errors_are_skipped():
    data = ('export let x = [{"category_slug": "health", "sub_records": [{"record_slug": "s", "record_name": "S", "unit": "u", '
            '"record_figures": [{"grpby_name": "Goa", "figures": {"BE": [{"2015-16": ""}, {"2016-17": "Err:512"}, {"2017-18": "7.5"}]}}]}]}];')
    rows = obi.parse("expenditure", data, "export let m = [];")
    assert rows == [("expenditure/health/s/goa/BE/2017-18", rows[0][1])] and rows[0][1]["value"] == 7.5


def test_receipts_have_no_sector_and_unique_ids():
    rows = obi.parse("receipts", read("receipts_data.js"), read("receipts_metadata.js"))
    assert len(rows) == 2736 and len({rid for rid, _ in rows}) == len(rows)
    rid, r = next((rid, r) for rid, r in rows if r["indicator_slug"] == "total_receipts" and r["state"] == "Andhra Pradesh"
                  and r["estimate"] == "A" and r["year"] == "2015-16")
    assert rid == "receipts/total_receipts/andhra-pradesh/A/2015-16" and r["value"] == 149601.0 and r["sector"] is None
