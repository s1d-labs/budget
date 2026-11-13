"""The family's `union-wayback` task (see scrape.json): Union Budget documents from the Wayback Machine's copy of indiabudget.gov.in (G01).

www.indiabudget.gov.in refuses this scraper's User-Agent (Akamai, reportG01.md), and other public sources do not hold the documents
(report-G01.md), so G01 takes the copies archived by web.archive.org. Each queue item is one archived version of one document
(PDF, Excel, Word, ZIP): the task fetches the archived bytes as served (`id_`), checks that they are really that kind of file,
uploads them to the family's bucket as `files/<digest>.<ext>` (same content = same key), and stores one record.

The queue is fixed: `data/indiabudget-wayback.json.gz`, made by `python union_wayback.py --list` from the CDX API (one row per
distinct content of each normalised path). Run --list again to pick up new captures, then the Setup workflow.
Try one document without the hub: python union_wayback.py --local <timestamp> <original url>
The team server's IP is rate-limited by archive.org (429), so run the full queue on the pool, not on a box.
"""
import gzip
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlencode

import requests

from crawl import LocalHub, fetch
from wayback import CDX, WAYBACK, Pacer, parse_cdx

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
DEADLINE = time.time() + float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600 - 300
LISTING = Path(__file__).parent / "data" / "indiabudget-wayback.json.gz"
DOC_EXT = ("pdf", "xlsx", "xls", "csv", "zip", "docx", "doc")
MAGIC = {"pdf": (b"%PDF-",), "xlsx": (b"PK\x03\x04",), "docx": (b"PK\x03\x04",), "zip": (b"PK\x03\x04",),
         "xls": (b"\xd0\xcf\x11\xe0", b"PK\x03\x04"), "doc": (b"\xd0\xcf\x11\xe0", b"PK\x03\x04")}
CONTENT_TYPE = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "xls": "application/vnd.ms-excel", "csv": "text/csv", "zip": "application/zip", "doc": "application/msword",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
HOST = re.compile(r"^https?://(?:www\.)?indiabudget\.gov\.in(?::\d+)?/", re.I)
FOLDER_YEAR = re.compile(r"^(?:budget|ub|es)(\d{2}|\d{4})-(\d{2}|\d{4})(\(i\))?(?:$|[ _-])")   # budget2024-25(i), es1974-75 1
SPEECH_YEAR = re.compile(r"^bs((?:19|20)\d{2})\d{2}\.")                                    # bspeech/bs194748.pdf


def norm_path(original):
    """The site path of an archived URL: host dropped, %-escapes decoded, lower case (the site's server ignores case), runs of
    slashes collapsed. Pure."""
    p = unquote(HOST.sub("", original.split("?")[0].split("#")[0]))
    return re.sub(r"/{2,}", "/", p).strip("/").lower()


def ext_of(path):
    return path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""


def fy(start):
    """'2024-25' for 2024. Pure."""
    return f"{start}-{(start + 1) % 100:02d}"


def budget_year(path, timestamp):
    """(year, interim, source) for one document. Year folders (budget2024-25, budget2024-25(i), ub2010-11, es2003-04) name the year.
    Paths without one (doc/, economicsurvey/) hold the current year's documents, so the year comes from the capture date:
    the Budget is presented on 1 February (end of February before 2017), the Economic Survey a day or two before it.
    `source` says which: 'path' or 'capture_date' (an estimate; check against the document). Pure."""
    for part in path.split("/")[:-1]:
        if m := FOLDER_YEAR.match(part):
            a = int(m[1]) if len(m[1]) == 4 else (1900 + int(m[1]) if int(m[1]) > 50 else 2000 + int(m[1]))
            return fy(a), bool(m[3]), "path"
    if m := SPEECH_YEAR.match(path.rsplit("/", 1)[-1]):
        return fy(int(m[1])), False, "path"
    if path.startswith("budget_archive/"):
        return None, False, None
    t = datetime.strptime(timestamp[:8], "%Y%m%d")
    survey = path.startswith("economicsurvey/")
    cut = (1, 25) if survey else ((2, 1) if t.year >= 2017 else (2, 20))
    start = t.year if (t.month, t.day) >= cut else t.year - 1
    return (fy(start - 1) if survey else fy(start)), False, "capture_date"


def listing_rows(captures):
    """Queue rows from CDX captures: documents only, one per (normalised path, digest), earliest capture kept, ordered by path. Pure."""
    out = {}
    for c in sorted(captures, key=lambda c: c["timestamp"]):
        path = norm_path(c["original"])
        if ext_of(path) not in DOC_EXT or not c.get("digest"):
            continue
        out.setdefault((path, c["digest"]), {"path": path, "original": c["original"], "timestamp": c["timestamp"],
                                             "digest": c["digest"], "length": int(c["length"]) if str(c.get("length")).isdigit() else 0})
    return sorted(out.values(), key=lambda r: (r["path"], r["timestamp"]))


def item_key(row):
    return f"{row['path'][:150]}@{row['digest']}"


def sniff(body, ext):
    """True when the bytes are the kind of file the name says (an archived HTML error page is not). Pure."""
    if ext == "csv":
        return not body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html"))
    return body.startswith(MAGIC.get(ext, (b"",)))


def describe(row, body):
    """(record_id, data) for one archived document. Pure."""
    path, ext = row["path"], ext_of(row["path"])
    year, interim, source = budget_year(path, row["timestamp"])
    return item_key(row), {
        "path": path, "section": path.split("/")[0] if "/" in path else "", "name": path.rsplit("/", 1)[-1], "format": ext,
        "budget_year": year, "interim": interim, "year_source": source,
        "original": row["original"], "captured": row["timestamp"], "digest": row["digest"],
        "wayback_url": WAYBACK.format(ts=row["timestamp"], url=row["original"]),
        "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "valid": sniff(body, ext)}


def work(hub, session, pace, row, store):
    """Fetch and store one document. Returns (status, error, records stored)."""
    pace.wait()
    r = fetch(hub, session, WAYBACK.format(ts=row["timestamp"], url=row["original"]), timeout=(20, 600))
    if r == 404:                                                  # capture gone from the archive: nothing to retry
        hub.failure("wayback-missing", item_key(row), "404", url=row["original"])
        return "done", None, 0
    if not isinstance(r, requests.Response):
        return "failed", f"http {r}" if r else "connection error", 0
    rid, data = describe(row, r.content)
    upload = None
    if data["valid"]:
        upload = (f"files/{row['digest']}.{data['format']}", r.content, CONTENT_TYPE.get(data["format"], "application/octet-stream"))
    else:
        hub.metric("not_a_document")
        hub.failure("not-a-document", rid, f"archived bytes are not a {data['format']}", url=data["wayback_url"])
    store(rid, data, upload)
    return "done", None, 1


def list_archive(out=LISTING):
    """Write the queue listing from the CDX API (documents only, status 200, one row per distinct content)."""
    hub, session, pace, captures, resume = LocalHub(), requests.Session(), Pacer(LocalHub()), [], None
    q = {"url": "indiabudget.gov.in/", "matchType": "prefix", "output": "json", "fl": "original,timestamp,digest,length",
         "filter": ["statuscode:200", f"original:(?i).*\\.({'|'.join(DOC_EXT)})$"], "collapse": "digest", "limit": 5000,
         "showResumeKey": "true"}
    while True:
        for attempt in range(5):
            pace.wait()
            r = fetch(hub, session, f"{CDX}?{urlencode({**q, **({'resumeKey': resume} if resume else {})}, doseq=True)}", timeout=300)
            if isinstance(r, requests.Response):
                break
            time.sleep(30 * (attempt + 1))
        else:
            sys.exit(f"cdx failed: {r}")
        rows, resume = parse_cdx(r.text)
        captures += rows
        print(f"{len(captures)} captures", file=sys.stderr)
        if not resume:
            break
    rows = listing_rows(captures)
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(gzip.compress(json.dumps(rows, separators=(",", ":")).encode(), mtime=0))
    print(f"{len(rows)} documents, {sum(r['length'] for r in rows) / 1e9:.1f} GB (archive's compressed sizes) -> {out}", file=sys.stderr)


def load_listing(path=LISTING):
    return json.loads(gzip.decompress(path.read_bytes()))


def local(timestamp, original):
    hub = LocalHub()
    row = {"path": norm_path(original), "original": original, "timestamp": timestamp, "digest": "local"}

    def store(rid, data, upload):
        print(json.dumps({"record_id": rid, "data": data, "upload": upload[0] if upload else None}, ensure_ascii=False))
    status, error, n = work(hub, requests.Session(), Pacer(hub), row, store)
    print(f"{status} {error or ''} {n} records, counters {hub.counts}", file=sys.stderr)


def main():
    from scrape_hub_client import HubClient      # imported here so --local works without it
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")), total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    session, pace = requests.Session(), Pacer(hub)

    def store(rid, data, upload):
        if upload:
            data["file"] = hub.upload_many([upload])[0]["key"]
        hub.record(rid, data)
    try:
        while time.time() < DEADLINE:
            if hub.settings.get("paused"):
                time.sleep(30)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break
            item = items[0]
            status, error, n = work(hub, session, pace, item["payload"], store)
            hub.complete([item["key"]], status, error=error or "", fetched=n)
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local(*sys.argv[2:4])
    elif sys.argv[1:2] == ["--list"]:
        list_archive()
    else:
        main()
