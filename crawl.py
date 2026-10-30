"""The family's `crawl` task (see scrape.json): Union Budget documents from www.indiabudget.gov.in.
Try it without the hub: python crawl.py --local <url>. The pool worker runs it with the assignment's env and:
SCRAPE_POOL_PROJECT (which project), SCRAPE_JOB_INDEX / SCRAPE_TOTAL_JOBS (this worker's slice), SCRAPE_MAX_HOURS (time budget).

Two kinds of queue item (family.py):
- `pdf`: download one budget document, keep it in the family's bucket (signed by the hub), store one record describing it;
- `index`: fetch an HTML page and store one record per document (PDF/XLS) linked from it, so the full archive can be mapped.

The site sits behind Akamai, which answers 403 to this self-identified client (reportG01.md). Those answers are counted as
blocks (http_403, hub.block()), so the hub backs off, and the items fail and are retried later.
"""
import gzip
import hashlib
import html
import json
import os
import re
import sys
import time
from urllib.parse import urljoin, urlsplit

import requests

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
SITE = os.environ.get("SITE", "https://www.indiabudget.gov.in")
DEADLINE = time.time() + float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600 - 120
UA = "s1d-labs scraper (https://github.com/s1d-labs)"
LINK = re.compile(r'<a\b[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)
DOC_EXT = (".pdf", ".xls", ".xlsx", ".csv", ".zip")
YEAR = re.compile(r"(?:budget|ub|es)(\d{4})-(\d{2,4})")


def parse(page, base_url):
    """[(record_id, data)] for the documents linked from one HTML page. Pure: test it on saved pages."""
    out, seen = [], set()
    for href, text in LINK.findall(page):
        url = urljoin(base_url, html.unescape(href.strip()))
        parts = urlsplit(url)
        if not parts.netloc.endswith("indiabudget.gov.in") or not parts.path.lower().endswith(DOC_EXT):
            continue
        rid = parts.path.lstrip("/")                  # the site's own path: stable across runs and years
        if rid in seen:
            continue
        seen.add(rid)
        title = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
        m = YEAR.search(parts.path)
        out.append((rid, {"url": url, "title": title, "year": f"{m[1]}-{m[2][-2:]}" if m else None,
                          "format": parts.path.rsplit(".", 1)[-1].lower(), "listed_on": base_url}))
    return out


def describe_pdf(body, payload):
    """(record_id, data) for one downloaded document. Pure."""
    rid = urlsplit(payload["url"]).path.lstrip("/")
    return rid, {"url": payload["url"], "year": payload.get("year"), "doc": payload.get("doc"), "bytes": len(body),
                 "sha256": hashlib.sha256(body).hexdigest(), "pdf": body[:5] == b"%PDF-"}


class LocalHub:
    """Stand-in for HubClient so `--local` runs without the hub: counters go to stderr, records to stdout."""
    settings = {"threads": 1, "delay_ms": 0, "paused": False}

    def __init__(self):
        self.counts = {}

    def metric(self, name, value=1):
        self.counts[name] = self.counts.get(name, 0) + value

    def error(self, n=1):
        self.metric("errors", n)

    def block(self, n=1):
        self.metric("blocked", n)


def fetch(hub, session, url, timeout=30):
    """The response on 200, the status code (int) otherwise, None on a connection error."""
    t = time.time()
    try:
        r = session.get(url, timeout=timeout, headers={"User-Agent": UA})
    except requests.RequestException:
        hub.metric("http_error")
        hub.error()
        return None
    hub.metric("n_http")
    hub.metric("t_http_s", time.time() - t)
    if r.status_code != 200:
        hub.metric(f"http_{r.status_code}")
        if r.status_code in (403, 429, 503):
            hub.block()
        return r.status_code
    if not r.encoding or r.encoding.lower() == "iso-8859-1":     # no charset declared: requests guesses Latin-1; trust the content
        r.encoding = r.apparent_encoding or "utf-8"
    return r


def local(url):
    hub = LocalHub()
    r = fetch(hub, requests.Session(), url, timeout=180)
    rows = []
    if isinstance(r, requests.Response):
        rows = [describe_pdf(r.content, {"url": url})] if r.content[:5] == b"%PDF-" else parse(r.text, url)
    else:
        print(f"no page: {r}", file=sys.stderr)
    for rid, data in rows:
        print(json.dumps({"record_id": rid, "data": data}, ensure_ascii=False))
    print(f"{len(rows)} records, counters {hub.counts}", file=sys.stderr)


def work(hub, session, item):
    """Fetch one queue item and store what it gives. Returns (status, error, records)."""
    p = item["payload"]
    r = fetch(hub, session, p["url"], timeout=(15, 300) if p["kind"] == "pdf" else 30)
    if r == 404:
        hub.failure("not_found", item["key"], "404", url=p["url"])     # e.g. a demand number that year does not have
        return "done", None, 0
    if not isinstance(r, requests.Response):
        return "failed", f"http {r}" if r else "connection error", 0
    if p["kind"] == "index":
        hub.upload_many([(f"pages/{item['key']}.html.gz", gzip.compress(r.content), "application/gzip")])
        rows = parse(r.text, p["url"])
    else:
        if r.content[:5] != b"%PDF-":                                  # an HTML error page served with 200
            hub.metric("not_pdf")
            return "failed", "not a PDF", 0
        stored = hub.upload_many([(f"docs/{item['key']}.pdf", r.content, "application/pdf")])
        rid, data = describe_pdf(r.content, p)
        rows = [(rid, {**data, "file": stored[0]["key"]})]
    for rid, data in rows:
        hub.record(rid, data)
    return "done", None, len(rows)


def main():
    from scrape_hub_client import HubClient      # imported here so --local works without it
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")), total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    session = requests.Session()
    try:
        while time.time() < DEADLINE:
            if hub.settings.get("paused"):
                time.sleep(30)                          # the hub holds us (site down, or the family owner paused it)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break                                   # nothing left for this project: exit, the worker asks the hub for more
            item = items[0]
            status, error, n = work(hub, session, item)
            hub.complete([item["key"]], status, error=error, fetched=n)
            time.sleep(max(1.0, hub.settings.get("delay_ms", 0) / 1000))
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local(sys.argv[2] if len(sys.argv) > 2 else SITE + "/")
    else:
        main()
