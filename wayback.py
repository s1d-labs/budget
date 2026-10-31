"""The family's `wayback` task (see scrape.json): Open Budgets India's CKAN catalogue as archived by the Wayback Machine (G02).

openbudgetsindia.org is gone (reportG02.md), but web.archive.org kept many of its pages and files. Each queue item is one prefix of
the path under openbudgetsindia.org/dataset/. For it the task:
1. lists the archived URLs under the prefix (CDX API, one capture per URL, paged with a resume key);
2. for each dataset page (/dataset/<name>), fetches the archived HTML and stores a `dataset/<name>` record: title, description,
   organization, sectors (CKAN groups), tags, resource ids (these say which datasets are state budgets);
3. for each resource download (/dataset/<id>/resource/<rid>/download/<file>), downloads data files (csv, xls, ...) into the
   family's bucket and stores a `file/<rid>/<file>` record; other formats (pdf, ...) are recorded but not downloaded.
Try it without the hub: python wayback.py --local <prefix>   (e.g. openbudgetsindia.org/dataset/st)
The team server's IP is rate-limited by archive.org (429), so run this on the pool, not on a box.
"""
import hashlib
import html
import json
import os
import re
import sys
import time
from urllib.parse import quote, unquote, urlsplit

import requests

from crawl import LocalHub, fetch

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
DEADLINE = time.time() + float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600 - 300
CDX = "https://web.archive.org/cdx/search/cdx"
WAYBACK = "https://web.archive.org/web/{ts}id_/{url}"         # id_: the archived bytes as served, links not rewritten
DATA_EXT = {"csv", "xls", "xlsx", "ods", "json", "zip"}
MAX_BYTES = 300 * 1024 * 1024
GAP_S = 3.0                                                     # at least this long between requests to archive.org
DATASET_PAGE = re.compile(r"^/dataset/([A-Za-z0-9_-]+)/?$")
DOWNLOAD = re.compile(r"^/dataset/([A-Za-z0-9_-]+)/resource/([0-9a-f-]{36})/download/([^/]+)$")


def cdx_url(prefix, resume=None):
    q = (f"url={quote(prefix, safe='/:.')}&matchType=prefix&output=json&fl=original,timestamp,mimetype,length"
         f"&filter=statuscode:200&collapse=urlkey&limit=2000&showResumeKey=true")
    return f"{CDX}?{q}" + (f"&resumeKey={quote(resume)}" if resume else "")


def parse_cdx(text):
    """([{original, timestamp, mimetype, length}], resume_key or None) from one CDX JSON page. Pure."""
    rows = json.loads(text) if text.strip() else []
    if not rows:
        return [], None
    head, body, resume = rows[0], rows[1:], None
    if len(body) >= 2 and body[-2] == [] and len(body[-1]) == 1:  # showResumeKey: [..., [], [key]]
        resume, body = body[-1][0], body[:-2]
    return [dict(zip(head, r)) for r in body if len(r) == len(head)], resume


def classify(original):
    """('dataset', name) | ('file', dataset, resource_id, filename) | None for one archived URL. Pure."""
    parts = urlsplit(original)
    if parts.query:
        return None
    if m := DATASET_PAGE.match(parts.path):
        return "dataset", m[1]
    if m := DOWNLOAD.match(parts.path):
        return "file", m[1], m[2], unquote(m[3])
    return None


def text_of(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_dataset(page):
    """Metadata of one archived CKAN dataset page (OBI theme: cbgaindia/ckanext-openbudgetsin_theme, package/read_base.html). Pure.
    og:title / og:description are the dataset's own; <meta name="description"> is the site-wide blurb, so it is not used."""
    title = (re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', page, re.I)
             or re.search(r"<title>(.*?)</title>", page, re.S | re.I))
    title = re.split(r"\s+[-|]\s+(?:Datasets?\s+[-|]\s+)?Open\s*Budgets", text_of(title[1]) if title else "", flags=re.I)[0]
    desc = re.search(r'<meta\s+property="og:description"\s+content="([^"]*)"', page, re.I)

    def links(pattern):
        return sorted({unquote(html.unescape(x)).replace("+", " ") for x in re.findall(pattern, page)})
    orgs = links(r'href="[^"]*/organization/([A-Za-z0-9_-]+)"')
    return {"title": title or None, "description": html.unescape(desc[1]).strip() or None if desc else None,
            "organization": orgs[0] if orgs else None, "sectors": links(r'href="[^"]*/group/([A-Za-z0-9_-]+)"'),
            "tags": links(r'href="[^"]*/dataset/?\?tags=([^"&]+)"'),
            "resource_ids": links(r'/dataset/[A-Za-z0-9_-]+/resource/([0-9a-f-]{36})')}


class Pacer:
    def __init__(self, hub):
        self.hub, self.last = hub, 0.0

    def wait(self):
        gap = max(GAP_S, self.hub.settings.get("delay_ms", 0) / 1000)
        time.sleep(max(0.0, self.last + gap - time.time()))
        self.last = time.time()


def work(hub, session, prefix, store):
    """List and fetch one prefix. Returns (status, error, records stored). `store(kind, rid, data, upload)` saves a record."""
    pace, captures, resume = Pacer(hub), [], None
    while True:
        pace.wait()
        r = fetch(hub, session, cdx_url(prefix, resume), timeout=120)
        if not isinstance(r, requests.Response):
            return "failed", f"cdx: http {r}" if r else "cdx: connection error", 0
        rows, resume = parse_cdx(r.text)
        captures += rows
        if not resume:
            break
    hub.metric("cdx_urls", len(captures))
    n = 0
    for c in captures:
        if time.time() > DEADLINE:
            return "failed", "out of time (will be retried)", n
        kind = classify(c["original"])
        if kind is None:
            hub.metric("skipped_urls")
            continue
        wb = WAYBACK.format(ts=c["timestamp"], url=c["original"])
        meta = {"original": c["original"], "captured": c["timestamp"], "wayback_url": wb, "mimetype": c["mimetype"]}
        if kind[0] == "dataset":
            pace.wait()
            page = fetch(hub, session, wb, timeout=60)
            if not isinstance(page, requests.Response):
                hub.failure("wayback-page", f"dataset/{kind[1]}", f"http {page}", url=wb)
                continue
            store(f"dataset/{kind[1]}", {"name": kind[1], **parse_dataset(page.text), **meta}, None)
            n += 1
            continue
        _, dataset, rid, filename = kind
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        rec = {"dataset": dataset, "resource_id": rid, "filename": filename, "format": ext, **meta, "downloaded": False}
        size = int(c["length"]) if str(c.get("length", "")).isdigit() else 0
        if ext in DATA_EXT and size <= MAX_BYTES:
            pace.wait()
            f = fetch(hub, session, wb, timeout=(20, 600))
            if isinstance(f, requests.Response):
                body = f.content
                path = f"files/{rid}/{re.sub(r'[^A-Za-z0-9._-]', '_', filename)[:120] or 'file'}"
                rec.update(downloaded=True, bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
                store(f"file/{rid}/{filename}", rec, (path, body, f.headers.get("content-type", "application/octet-stream")))
                n += 1
                continue
            hub.failure("wayback-file", f"file/{rid}/{filename}", f"http {f}", url=wb)
        store(f"file/{rid}/{filename}", rec, None)
        n += 1
    return "done", None, n


def local(prefix):
    hub = LocalHub()

    def store(rid, data, upload):
        print(json.dumps({"record_id": rid, "data": data, "upload": upload[0] if upload else None}, ensure_ascii=False))
    status, error, n = work(hub, requests.Session(), prefix, store)
    print(f"{status} {error or ''} {n} records, counters {hub.counts}", file=sys.stderr)


def main():
    from scrape_hub_client import HubClient      # imported here so --local works without it
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")), total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    session = requests.Session()

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
            status, error, n = work(hub, session, item["payload"]["prefix"], store)
            hub.complete([item["key"]], status, error=error or "", fetched=n)
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local(sys.argv[2] if len(sys.argv) > 2 else "openbudgetsindia.org/dataset/st")
    else:
        main()
