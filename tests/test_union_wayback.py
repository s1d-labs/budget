"""Tests for the `union-wayback` task (G01 from the Wayback Machine). The CDX rows are copied from the real listing
(data/indiabudget-wayback.json.gz); the document bodies are minimal stand-ins (magic bytes only)."""
import family
import union_wayback as u


def test_norm_path():
    assert u.norm_path("https://www.indiabudget.gov.in/budget2019-20/economicsurvey///doc/echapter_vol2.pdf") == \
        "budget2019-20/economicsurvey/doc/echapter_vol2.pdf"
    assert u.norm_path("http://indiabudget.gov.in:80/doc/eb/SBE1.pdf?x=1") == "doc/eb/sbe1.pdf"
    assert u.norm_path("https://www.indiabudget.gov.in/budget_archive/es1974-75%201/ch_1.pdf") == "budget_archive/es1974-75 1/ch_1.pdf"


def test_budget_year():
    assert u.budget_year("budget2024-25/doc/eb/sbe1.pdf", "20240801000000") == ("2024-25", False, "path")
    assert u.budget_year("budget2024-25(i)/doc/budget_speech.pdf", "20240202000000") == ("2024-25", True, "path")
    assert u.budget_year("budget2018-2019/doc/eb/allsbe.pdf", "20190101000000") == ("2018-19", False, "path")
    # Economic Survey: the Budget's year (survey 1998-99 goes with Budget 1999-00), the same as under budget<year>/economicsurvey/
    assert u.budget_year("es98-99/chapt99/chap11.pdf", "20050101000000") == ("1999-00", False, "path")
    assert u.budget_year("budget_archive/es1974-75 1/ch_1.pdf", "20200101000000") == ("1975-76", False, "path")
    assert u.budget_year("budget_archive/bspeech/bs199192.pdf", "20200101000000") == ("1991-92", False, "path")
    assert u.budget_year("doc/bspeech/bs199192(i).pdf", "20200101000000") == ("1991-92", True, "path")
    assert u.budget_year("doc/bspeech/bs197475(july).pdf", "20200101000000") == ("1974-75", False, "path")
    assert u.budget_year("doc/bspeech/bs2024_25.pdf", "20250301000000") == ("2024-25", False, "path")
    # current-year paths: the year comes from the capture date (Budget on 1 February; Survey a few days before)
    assert u.budget_year("doc/eb/sbe1.pdf", "20260201120000") == ("2026-27", False, "capture_date")
    assert u.budget_year("doc/eb/sbe1.pdf", "20260131120000") == ("2025-26", False, "capture_date")
    assert u.budget_year("doc/eb/sbe1.pdf", "20160225000000") == ("2016-17", False, "capture_date")   # end of Feb before 2017
    assert u.budget_year("economicsurvey/doc/echapter.pdf", "20260130000000") == ("2026-27", False, "capture_date")
    lab = u.labels({"path": "budget2025-26/economicsurvey/doc/stat/tab8.8.xlsx", "original": "x", "timestamp": "20250216000000", "digest": "D"})
    assert (lab["budget_year"], lab["survey_year"]) == ("2025-26", "2024-25")
    assert u.labels({"path": "doc/eb/sbe1.pdf", "original": "x", "timestamp": "20260202000000", "digest": "D"})["survey_year"] is None


def test_listing_rows_dedupes_and_keeps_documents():
    caps = [{"original": "https://www.indiabudget.gov.in/doc/budget_speech.pdf", "timestamp": "20250301000000", "digest": "B", "length": "9"},
            {"original": "http://indiabudget.gov.in/doc/Budget_Speech.pdf", "timestamp": "20250202000000", "digest": "B", "length": "9"},
            {"original": "https://www.indiabudget.gov.in/doc/budget_speech.pdf", "timestamp": "20260202000000", "digest": "C", "length": "-"},
            {"original": "https://www.indiabudget.gov.in/index.php", "timestamp": "20260202000000", "digest": "D", "length": "1"}]
    rows = u.listing_rows(caps)
    assert [(r["path"], r["digest"], r["timestamp"], r["length"]) for r in rows] == [
        ("doc/budget_speech.pdf", "B", "20250202000000", 9), ("doc/budget_speech.pdf", "C", "20260202000000", 0)]


def test_sniff_and_describe():
    assert u.sniff(b"%PDF-1.7 ...", "pdf") and not u.sniff(b"<!DOCTYPE html>", "pdf")
    assert u.sniff(b"PK\x03\x04...", "xlsx") and u.sniff(b"\xd0\xcf\x11\xe0...", "xls")
    assert u.sniff(b"a,b\n1,2", "csv") and not u.sniff(b"  <html>", "csv")
    row = {"path": "budget2025-26/doc/eb/sbe49.pdf", "original": "https://www.indiabudget.gov.in/budget2025-26/doc/eb/sbe49.pdf",
           "timestamp": "20250701000000", "digest": "ABC234", "length": 100}
    rid, d = u.describe(row, b"%PDF-1.4 x")
    assert rid == "budget2025-26/doc/eb/sbe49.pdf@ABC234"
    assert d["section"] == "budget2025-26" and d["name"] == "sbe49.pdf" and d["format"] == "pdf" and d["valid"]
    assert d["budget_year"] == "2025-26" and d["year_source"] == "path" and d["bytes"] == 10
    assert d["wayback_url"] == "https://web.archive.org/web/20250701000000id_/https://www.indiabudget.gov.in/budget2025-26/doc/eb/sbe49.pdf"


def test_listing_and_queue():
    rows = u.load_listing()
    assert len(rows) > 16000 and all(u.ext_of(r["path"]) in u.DOC_EXT for r in rows)
    items = family.UNION_WAYBACK["items"]
    keys = [i["key"] for i in items]
    fixes = u.load_fixes()
    assert len(keys) == len(set(keys)) == len(rows) + len(fixes) and max(map(len, keys)) <= 200
    assert all(u.budget_year(r["path"], r["timestamp"])[0] for r in rows)           # every document gets a year
    assert "budget2024-25/doc/eb/sbe1.pdf" in {r["path"] for r in rows}
    p = next(p for p in family.PROJECTS if p["name"] == "budget-union-wayback")
    assert p["task"] == "union-wayback" and "probe" not in p and p["max_workers"] == 2


def test_relabel_and_alternates_without_hub():
    """A relabel item stores the new labels with the known download fields and makes no request; fix keys are new keys."""
    from crawl import LocalHub
    fixes = u.load_fixes()
    rel = next(f for f in fixes if "known" in f["payload"])
    stored = []
    status, err, n = u.work(LocalHub(), None, None, rel["payload"], lambda rid, d, up: stored.append((rid, d, up)))
    assert (status, n) == ("done", 1) and stored[0][2] is None
    rid, d = stored[0][:2]
    assert rid == u.item_key(rel["payload"]) and d["sha256"] == rel["payload"]["known"]["sha256"]
    listing_keys = {u.item_key(r) for r in u.load_listing()}
    assert not listing_keys & {f["key"] for f in fixes}
    assert sum("alternates" in f["payload"] for f in fixes) == 2
