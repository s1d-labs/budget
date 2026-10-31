"""The family's `pfms` task (see scrape.json): PFMS public reports, pfms.nic.in (G03): scheme releases, implementing agencies.

pfms.nic.in answers only from India (it times out from the team server and from GitHub runners), so its project runs with geo="IN" on
the hub's India pool servers. The site's report pages are ASP.NET forms, and their layout could not be read beforehand (the Wayback
Machine rate-limits the team IP), so the first stage is **exploration**:

- `explore` item: a breadth-first walk of pfms.nic.in from the item's seed pages (same site only, bounded by depth and page count,
  >= 2 s between requests). For every page it keeps the HTML (gzipped) in the family's bucket and stores a `page/<path>` record:
  title, visible text (start), links, ASP.NET postback targets, and each form's fields with the options of every dropdown (scheme,
  ministry, state, financial year, ...). Those records say which reports are public and what their forms ask for, so the report
  items of the next stage can be written from them.

Try it without the hub (from an Indian IP only): python pfms.py --local [url]
"""
import gzip
import hashlib
import html
import json
import os
import re
import sys
import time
from collections import deque
from urllib.parse import urldefrag, urljoin, urlsplit

import requests

from crawl import LocalHub, fetch

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
SITE = os.environ.get("SITE", "https://pfms.nic.in")
DEADLINE = time.time() + float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600 - 300
GAP_S = 2.0
HOSTS = ("pfms.nic.in", "pfms.gov.in")
SKIP = re.compile(r"logout|signout|\.(?:pdf|xlsx?|csv|zip|docx?|pptx?|jpe?g|png|gif|svg|css|js|ico|mp4)(?:$|\?)", re.I)
DOC = re.compile(r"\.(?:pdf|xlsx?|csv|zip|docx?)(?:$|\?)", re.I)
TAG = re.compile(r"<[^>]+>")
ATTR = re.compile(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))')
LINK = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S | re.I)
SELECT = re.compile(r"<select\b([^>]*)>(.*?)</select>", re.S | re.I)
OPTION = re.compile(r"<option\b([^>]*)>(.*?)</option>", re.S | re.I)
INPUT = re.compile(r"<(?:input|textarea)\b([^>]*)>", re.I)
IFRAME = re.compile(r"<i?frame\b([^>]*)>", re.I)
POSTBACK = re.compile(r"__doPostBack\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)")
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
SCRIPT = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
CAPTCHA = re.compile(r"captcha", re.I)


def attrs(tag_body):
    return {m[1].lower(): html.unescape(m[2] if m[2] is not None else m[3] if m[3] is not None else m[4]) for m in ATTR.finditer(tag_body)}


def text_of(fragment):
    return " ".join(html.unescape(TAG.sub(" ", SCRIPT.sub(" ", fragment))).split())


def on_site(url):
    host = urlsplit(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in HOSTS)


def page_id(url):
    """Record id for one page: path + query, lowercased path (IIS paths are case-insensitive); hashed when too long. Pure."""
    p = urlsplit(url)
    rid = "page/" + p.path.lower().lstrip("/") + (f"?{p.query}" if p.query else "")
    return rid if len(rid) <= 180 else rid[:150] + "#" + hashlib.sha1(rid.encode()).hexdigest()[:16]


def parse_page(page, url):
    """Everything an explorer needs from one HTML page. Pure: test it on saved pages."""
    links, docs, seen = [], [], set()
    markup = SCRIPT.sub(" ", page)                               # links written inside scripts are not links
    for a, inner in LINK.findall(markup):
        at = attrs(a)
        href, label = at.get("href", "").strip(), text_of(inner) or at.get("title", "")
        if not href or href.startswith(("mailto:", "tel:", "#")):
            continue
        if href.lower().startswith("javascript:"):
            continue                                              # postbacks are collected below
        target = urldefrag(urljoin(url, href))[0]
        if target in seen:
            continue
        seen.add(target)
        (docs if DOC.search(target) else links).append({"url": target, "text": label[:200]})
    for fr in IFRAME.findall(markup):
        src = attrs(fr).get("src")
        if src and not src.lower().startswith(("javascript:", "about:")):
            target = urldefrag(urljoin(url, src))[0]
            if target not in seen:
                seen.add(target)
                links.append({"url": target, "text": "(frame)"})
    postbacks = sorted({(t, a) for t, a in POSTBACK.findall(html.unescape(page))})
    forms = []
    for fa, body in FORM.findall(markup):
        fat = attrs(fa)
        fields, selects = [], {}
        for inp in INPUT.findall(body):
            at = attrs(inp)
            name = at.get("name")
            if not name:
                continue
            typ = at.get("type", "text").lower()
            value = at.get("value", "")
            fields.append({"name": name, "type": typ, **({"value": value[:200]} if typ in ("submit", "button", "radio", "checkbox") else {}),
                           **({"bytes": len(value)} if name.startswith("__") else {})})
        for sa, sbody in SELECT.findall(body):
            sat = attrs(sa)
            name = sat.get("name") or sat.get("id") or "?"
            opts = [[attrs(oa).get("value", text_of(ot)), text_of(ot)] for oa, ot in OPTION.findall(sbody)]
            selects[name] = {"n": len(opts), "options": opts[:400], "autopostback": "__doPostBack" in sa}
        forms.append({"action": urljoin(url, fat.get("action", "")), "method": fat.get("method", "get").lower(),
                      "fields": fields, "selects": selects})
    m = TITLE.search(page)
    body = text_of(page)
    return {"url": url, "title": text_of(m[1]) if m else "", "text": body[:3000], "text_chars": len(body),
            "links": links[:500], "n_links": len(links), "docs": docs[:300], "n_docs": len(docs),
            "postbacks": [{"target": t, "arg": a} for t, a in postbacks][:300], "forms": forms,
            "captcha": bool(CAPTCHA.search(page)), "aspnet": "__VIEWSTATE" in page}


def next_urls(info):
    """Same-site page links worth visiting from one parsed page. Pure."""
    return [l["url"] for l in info["links"] if on_site(l["url"]) and not SKIP.search(l["url"]) and urlsplit(l["url"]).scheme in ("http", "https")]


def explore(hub, session, item, store):
    """Walk the site from the item's seeds. `store(url, response, info)` keeps one page. Returns (pages stored, pages failed)."""
    p = item["payload"]
    max_pages, max_depth = p.get("max_pages", 150), p.get("max_depth", 3)
    queue, seen = deque((u, 0) for u in p["seeds"]), set(p["seeds"])
    stored = failed = 0
    while queue and stored < max_pages and time.time() < DEADLINE:
        url, depth = queue.popleft()
        r = fetch(hub, session, url, timeout=45)
        time.sleep(max(GAP_S, hub.settings.get("delay_ms", 0) / 1000))
        if not isinstance(r, requests.Response):
            failed += 1
            hub.failure("page", page_id(url), f"http {r}" if r else "connection error", url=url, depth=depth)
            continue
        if "html" not in r.headers.get("content-type", "html").lower():
            continue
        info = parse_page(r.text, r.url)
        info.update({"requested": url, "depth": depth, "status": r.status_code})
        store(url, r, info)
        stored += 1
        if depth < max_depth:
            for nxt in next_urls(info):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, depth + 1))
    return stored, failed


def local(url):
    hub = LocalHub()

    def show(u, r, info):
        print(json.dumps({"record_id": page_id(u), "data": info}, ensure_ascii=False)[:4000])

    n, bad = explore(hub, requests.Session(), {"payload": {"seeds": [url], "max_pages": 1, "max_depth": 0}}, show)
    print(f"{n} pages, {bad} failed, counters {hub.counts}", file=sys.stderr)


def main():
    from scrape_hub_client import HubClient
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")), total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    session = requests.Session()

    def store(url, r, info):
        rid = page_id(url)
        name = hashlib.sha1(url.encode()).hexdigest()[:20]
        up = hub.upload_many([(f"pages/{name}.html.gz", gzip.compress(r.content), "application/gzip")])
        hub.record(rid, {**info, "file": up[0]["key"]})

    try:
        while time.time() < DEADLINE:
            if hub.settings.get("paused"):
                time.sleep(30)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break
            item = items[0]
            if item["payload"].get("kind") != "explore":
                hub.complete([item["key"]], "failed", error=f"unknown kind {item['payload'].get('kind')}")
                continue
            n, bad = explore(hub, session, item, store)
            if n:
                hub.complete([item["key"]], "done", fetched=n)
            else:
                hub.complete([item["key"]], "failed", error=f"no page loaded ({bad} failed)")
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local(sys.argv[2] if len(sys.argv) > 2 else SITE + "/")
    else:
        main()
