"""The family's `pfms` task (see scrape.json): PFMS public reports (G03): scheme releases, implementing agencies.

PFMS moved from pfms.nic.in to pfms.gov.in (as of 2026-10-31 the old host only says so). Both answer only from India (they
time out from the team server and from GitHub runners), so the project runs with geo="IN" on
the hub's India pool servers. The site's report pages are ASP.NET forms, and their layout could not be read beforehand (the Wayback
Machine rate-limits the team IP), so the first stage is **exploration**:

- `explore` item: a breadth-first walk of the PFMS site from the item's seed pages (same site only, bounded by depth and page count,
  >= 2 s between requests). For every page it keeps the HTML (gzipped) in the family's bucket and stores a `page/<path>` record:
  title, visible text (start), links, ASP.NET postback targets, and each form's fields with the options of every dropdown (scheme,
  ministry, state, financial year, ...). Those records say which reports are public and what their forms ask for, so the report
  items of the next stage can be written from them.
- `steps` item: GET one page, then post its form back once per step (a dropdown value and the button pressed, or a grid's
  Page$N), each from the page the last step returned, as a browser does; every page is stored as `step/<key>/<n>`, with the
  page's tables (ASP.NET grids) as rows.
- `raw` item: fetch URLs as they are (scripts, lookup endpoints) and store `raw/<path>` with the start of the text.

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

from crawl import UA, LocalHub, fetch

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
SITE = os.environ.get("SITE", "https://pfms.gov.in")
DEADLINE = time.time() + float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600 - 300
GAP_S = 2.0
HOSTS = ("pfms.nic.in", "pfms.gov.in", "pfmsdashboard.gov.in")
SKIP = re.compile(r"logout|signout|\.(?:pdf|xlsx?|csv|zip|docx?|pptx?|jpe?g|png|gif|svg|css|js|ico|mp4)(?:$|\?)", re.I)
DOC = re.compile(r"\.(?:pdf|xlsx?|csv|zip|docx?)(?:$|\?)", re.I)
TAG = re.compile(r"<[^>]+>")
ATTR = re.compile(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))')
LINK = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
FORM_OPEN = re.compile(r"<form\b([^>]*)>", re.I)
TABLE = re.compile(r"<table\b([^>]*)>(.*?)</table>", re.S | re.I)
ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
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
    # ASP.NET pages nest small <form>s inside the page-wide one, so fields and dropdowns are read from the whole page
    forms = [{"action": urljoin(url, at.get("action", "")), "method": at.get("method", "get").lower(), "id": at.get("id")}
             for at in map(attrs, FORM_OPEN.findall(markup))]
    fields = []
    for inp in INPUT.findall(markup):
        at = attrs(inp)
        name = at.get("name")
        if not name:
            continue
        typ = at.get("type", "text").lower()
        value = at.get("value", "")
        fields.append({"name": name, "type": typ, **({"value": value[:200]} if typ in ("submit", "button", "radio", "checkbox") else {}),
                       **({"bytes": len(value)} if name.startswith("__") else {})})
    selects = {}
    for sa, sbody in SELECT.findall(markup):
        sat = attrs(sa)
        opts = [[attrs(oa).get("value", text_of(ot)), text_of(ot)] for oa, ot in OPTION.findall(sbody)]
        selects[sat.get("name") or sat.get("id") or "?"] = {"n": len(opts), "options": opts[:400],
                                                           "autopostback": "__doPostBack" in sa or "onchange" in sat}
    m = TITLE.search(page)
    body = text_of(page)
    return {"url": url, "title": text_of(m[1]) if m else "", "text": body[:3000], "text_chars": len(body),
            "links": links[:500], "n_links": len(links), "docs": docs[:300], "n_docs": len(docs),
            "postbacks": [{"target": t, "arg": a} for t, a in postbacks][:300], "forms": forms, "fields": fields,
            "selects": selects, "tables": tables(markup), "captcha": bool(CAPTCHA.search(page)), "aspnet": "__VIEWSTATE" in page}


def tables(markup, max_rows=300):
    """[{id, header, rows, n_rows}] for every <table> with an id (ASP.NET grids) or with >= 2 rows. Cells as text. Pure."""
    out = []
    for ta, body in TABLE.findall(markup):
        rows = [[text_of(c) for c in CELL.findall(tr)] for tr in ROW.findall(body)]
        rows = [r for r in rows if any(r)]
        tid = attrs(ta).get("id")
        if not rows or (not tid and len(rows) < 2):
            continue
        out.append({"id": tid, "header": rows[0], "rows": rows[1:max_rows + 1], "n_rows": len(rows) - 1})
    return out


def form_state(page):
    """(action, {name: value}) that the page's main form would post as it stands: hidden and text inputs, checked boxes, the
    selected option of each dropdown; buttons left out (a step names the one it presses). Pure."""
    markup = SCRIPT.sub(" ", page)
    opens = [attrs(f) for f in FORM_OPEN.findall(markup)]
    main = next((f for f in opens if f.get("id") == "aspnetForm"), None) or next((f for f in opens if f.get("method", "").lower() == "post"), {})
    data = {}
    for inp in INPUT.findall(markup):
        at = attrs(inp)
        name, typ = at.get("name"), at.get("type", "text").lower()
        if not name or typ in ("submit", "button", "image", "reset", "file"):
            continue
        if typ in ("checkbox", "radio") and "checked" not in at:
            continue
        data[name] = at.get("value", "on" if typ in ("checkbox", "radio") else "")
    for sa, sbody in SELECT.findall(markup):
        name = attrs(sa).get("name")
        if not name:
            continue
        opts = [attrs(oa) | {"_text": text_of(ot)} for oa, ot in OPTION.findall(sbody)]
        chosen = next((o for o in opts if "selected" in o), opts[0] if opts else None)
        if chosen is not None:
            data[name] = chosen.get("value", chosen["_text"])
    return main.get("action", ""), data


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
        store(page_id(url), r, info)
        stored += 1
        if depth < max_depth:
            for nxt in next_urls(info):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, depth + 1))
    return stored, failed


def post(hub, session, url, data, referer, timeout=60):
    """POST a form like fetch() GETs a page: the response on 200, the status code otherwise, None on a connection error."""
    t = time.time()
    try:
        r = session.post(url, data=data, timeout=timeout, headers={"User-Agent": UA, "Referer": referer})
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
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r


def step_data(state, step):
    """The form data for one postback step: the page's own state, the step's field values on top, and the event that the
    step fires (a grid's Page$N, a dropdown change) or the button it presses. Pure."""
    data = dict(state)
    data.update(step.get("fields", {}))
    data["__EVENTTARGET"] = step.get("target", "")
    data["__EVENTARGUMENT"] = step.get("arg", "")
    if step.get("button"):
        data[step["button"]] = step.get("button_value", "Search")
    return data


def steps(hub, session, item, store):
    """GET the item's page, then post it back once per step, each from the page the previous one returned. Every page is stored
    (`step/<key>/<n>`). Returns (pages stored, pages failed)."""
    p, key = item["payload"], item["key"]
    url, stored = p["url"], 0
    r = fetch(hub, session, url, timeout=45)
    for n, step in enumerate([None] + p.get("steps", [])):
        if n:
            time.sleep(max(GAP_S, hub.settings.get("delay_ms", 0) / 1000))
            action, state = form_state(r.text)
            target = urljoin(r.url, action or r.url)
            r = post(hub, session, target, step_data(state, step), referer=r.url)
        if not isinstance(r, requests.Response):
            hub.failure("step", f"{key}/{n}", f"http {r}" if r else "connection error", url=url, step=step)
            return stored, 1
        info = parse_page(r.text, r.url)
        info.update({"requested": url, "step": n, "did": step, "status": r.status_code})
        store(f"step/{key}/{n}", r, info)
        stored += 1
    return stored, 0


def raw(hub, session, item, store):
    """Fetch each URL as it is (scripts, lookups) and store its start as text. Returns (stored, failed)."""
    stored = failed = 0
    for url in item["payload"]["urls"]:
        r = fetch(hub, session, url, timeout=45)
        time.sleep(max(GAP_S, hub.settings.get("delay_ms", 0) / 1000))
        if not isinstance(r, requests.Response):
            failed += 1
            hub.failure("raw", url[:200], f"http {r}" if r else "connection error")
            continue
        info = {"url": r.url, "requested": url, "status": r.status_code, "content_type": r.headers.get("content-type"),
                "bytes": len(r.content), "text": r.text[:20000]}
        if "html" in (r.headers.get("content-type") or ""):
            info["page"] = parse_page(r.text, r.url)
        store("raw/" + page_id(url)[5:], r, info)
        stored += 1
    return stored, failed


KINDS = {"explore": explore, "steps": steps, "raw": raw}


def local(url):
    hub = LocalHub()

    def show(rid, r, info):
        print(json.dumps({"record_id": rid, "data": info}, ensure_ascii=False)[:4000])

    n, bad = explore(hub, requests.Session(), {"payload": {"seeds": [url], "max_pages": 1, "max_depth": 0}}, show)
    print(f"{n} pages, {bad} failed, counters {hub.counts}", file=sys.stderr)


def main():
    from scrape_hub_client import HubClient
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")), total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    session = requests.Session()

    def store(rid, r, info):
        name = hashlib.sha1(rid.encode()).hexdigest()[:20]
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
            kind = KINDS.get(item["payload"].get("kind"))
            if kind is None:
                hub.complete([item["key"]], "failed", error=f"unknown kind {item['payload'].get('kind')}")
                continue
            n, bad = kind(hub, session, item, store)
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
