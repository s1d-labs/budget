"""The family's `pfms` task (see scrape.json): PFMS public reports (G03): scheme releases, implementing agencies.

PFMS moved from pfms.nic.in to pfms.gov.in (as of 2026-10-31 the old host only says so). Both answer only from India (they
time out from the team server and from GitHub runners without a proxy), so the projects run through the hub's Indian
proxies (proxy="IN"; until 2026-11-01 on the India pool servers, geo="IN"). The site's report pages are ASP.NET forms, and their layout could not be read beforehand (the Wayback
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

Stage 2 walks the two public lists that stage 1 found, page by page (see "stage 2" below):
- `schemes` item: one financial year of the Scheme Listing -> `scheme/<fy>/<grant>/<code>` records (outlay, grant); items per
  (year, scheme type) -> `scheme-type/...` records that tag schemes with their type.
- `agencies` item: one (state, agency type) of the agency registry, all statuses -> `agency/<hash>` records. Type Individuals is
  never queued (personal data).
The project runs on GitHub pool workers through the hub's Indian proxies (proxy="IN"); requests picks up HTTPS_PROXY by itself.

Try it without the hub (from an Indian IP only): myproxy run python pfms.py --local [url]
or one queue item: myproxy run python pfms.py --item '{"key": "k", "payload": {"kind": "schemes", "fy": "2026", "scheme_type": "1"}}'
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
            r = post(hub, session, target, step_data(state, step), referer=r.url, timeout=step.get("timeout", 60))
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


# ---- stage 2: the two public lists, walked page by page ----------------------------------------------------------------------
# Both are ASP.NET GridViews, 10 rows a page, with a pager row (1 2 ... 10 ... >) as the grid's last row. The grid accepts only the
# Page$N arguments its pager shows (anything else gives the site's error page), so a list is walked one page at a time, each posted
# from the page before; the agency grid also accepts Page$Last, which gives its size up front.
LIST_URL, LIST_GRID = f"{SITE}/SitePages/SchemeListing.aspx", "ctl00$cphBody$grdvSearchSchemeGBE"
AGENCY_URL, AGENCY_GRID = f"{SITE}/SitePages/SearchAgency.aspx", "ctl00$cphBody$grdvAgencyDetails"
PAGE_GAP_S = 1.0                                   # between grid pages (the site answers a page in < 1 s)
PAGE_TRIES = 4                                     # posts of one page before the item fails
NO_RECORDS_WAIT_S = 30.0                           # wait before a "No records" first page is posted again
WALK_RESTARTS = 5                                  # new searches (jumping back to the failed page) before an item fails
RESTART_WAIT_S = 60.0                              # wait before such a new search
PAGE_EST_S = 3.0                                   # time one grid page takes in the pool (2.7 s measured, Nov 2026)
PAGER_CELL = re.compile(r"^(?:\d+|\.\.\.|<|>|<<|>>|First|Last|)$")
SCHEME = re.compile(r"^(.*?)\s*\[([A-Za-z0-9]+)\]\s*$")       # codes are digits, UT schemes letters too ([DN34])
GRANT = re.compile(r"^(\d+)\s*-\s*(.*)$")


NO_RECORDS = re.compile(r"No records? (?:match|found)", re.I)


class ItemFailed(Exception):
    pass


class NotEnoughTime(ItemFailed):
    """The list is too long for the time this process has left: the item fails at once, and the process takes no more items."""


def grid_rows(page, grid):
    """Data rows of one GridView (`grid` is its postback name, ctl00$...): header and pager rows left out. [] when the page has no
    such grid (no results). Pure."""
    tid = grid.replace("$", "_")
    t = next((t for t in tables(SCRIPT.sub(" ", page), max_rows=1000) if t["id"] == tid), None)
    if t is None:
        return []
    return [r for r in t["rows"] if not all(PAGER_CELL.match(c) for c in r)]


def page_problem(page, grid, n):
    """Why a page that answered 200 is not page n of the result grid, or None when it is. A grid that is missing, empty without
    the site's "No records" message, or whose pager does not show n as the current page (the site sometimes answers a postback with
    another page, which would otherwise end the walk early and quietly) is a problem. n=None: any page with rows. Pure."""
    tid = grid.replace("$", "_")
    t = next((t for t in tables(SCRIPT.sub(" ", page), max_rows=1000) if t["id"] == tid), None)
    if t is None:
        return "no result grid"
    if not grid_rows(page, grid):
        return None if n == 1 and NO_RECORDS.search(text_of(page)) else "empty grid without the no-records message"
    if n is not None and n > 1:
        pager = next((r for r in reversed(t["rows"]) if r and all(PAGER_CELL.match(c) for c in r)), [])
        if str(n) not in pager or has_page(page, grid, n):
            return f"the pager does not show page {n} as current"
    return None


def has_page(page, grid, n):
    """True when the page's pager links to page n of `grid`. Pure."""
    return (grid, f"Page${n}") in set(POSTBACK.findall(html.unescape(page)))


def linked_pages(page, grid):
    """Page numbers the pager of `grid` links to, sorted. The pager shows 10 numbers; its "..." links jump to the page before or
    after them (page 1 links to 2-11, page 21 to 20 and 22-31). Pure."""
    return sorted({int(a[5:]) for t, a in POSTBACK.findall(html.unescape(page)) if t == grid and re.fullmatch(r"Page\$\d+", a)})


def current_page(page, grid):
    """The page number the pager shows as current (the number without a link), or None when there is no pager. Pure."""
    tid = grid.replace("$", "_")
    t = next((t for t in tables(SCRIPT.sub(" ", page), max_rows=1000) if t["id"] == tid), None)
    pager = next((r for r in reversed(t["rows"]) if r and all(PAGER_CELL.match(c) for c in r)), []) if t else []
    links = set(linked_pages(page, grid))
    return next((int(c) for c in pager if c.isdigit() and int(c) not in links), None)


def fy_label(value):
    """The financial year dropdown's value is the year the FY ends: '2026' -> '2025-26'. Pure."""
    y = int(value)
    return f"{y - 1}-{str(y)[2:]}"


def money(text):
    try:
        return float(text.replace(",", "").strip())
    except ValueError:
        return None


def scheme_records(rows, fy, stype, stype_name):
    """[(record_id, data)] from Scheme Listing rows: name [code] | grant (number - ministry) | plan outlay (₹ crore). Pure.
    The year's whole list (scheme type "0", All) gives `scheme/<fy>/<grant>/<code>`, one per scheme and year; the lists per scheme
    type give `scheme-type/<fy>/<type>/<grant>/<code>`, which only say which type a scheme has (many schemes have none)."""
    out = []
    for row in rows:
        if len(row) < 3:
            continue
        m, g = SCHEME.match(row[0]), GRANT.match(row[1])
        code = m[2] if m else hashlib.sha1(row[0].encode()).hexdigest()[:10]
        grant = g[1] if g else "x"
        rid = f"scheme/{fy_label(fy)}/{grant}/{code}" if stype == "0" else f"scheme-type/{fy_label(fy)}/{stype}/{grant}/{code}"
        out.append((rid, {
            "fy": fy_label(fy), "scheme_code": m[2] if m else None, "scheme": (m[1] if m else row[0]).strip(),
            **({} if stype == "0" else {"scheme_type": stype, "scheme_type_name": stype_name}),
            "grant_no": g[1] if g else None, "ministry": (g[2] if g else row[1]).strip(),
            "outlay_cr": money(row[2]), "outlay_text": row[2]}))
    return out


def agency_records(rows, state_id, type_id):
    """[(record_id, data)] from Search Agency rows. The site has no public agency id (the unique code is masked, and sometimes shows
    the end of an e-mail address), so the id is a hash of the whole row; only the code's last digits are kept, when they are
    digits. Pure."""
    out = []
    for row in rows:
        if len(row) < 9:
            continue
        name, atype, code, city, state, status, remark, created_by, date = row[:9]
        tail = code[-4:] if code[-4:].isdigit() else None
        rid = "agency/" + hashlib.sha1("|".join([state_id, type_id, *row[:9]]).encode()).hexdigest()[:20]
        out.append((rid, {"name": name.strip('"').strip(), "agency_type": atype, "code_last4": tail, "city": city, "state": state,
                          "status": status, "remark": remark, "created_by": created_by, "date": date,
                          "state_id": state_id, "type_id": type_id}))
    return out


def grid_page(hub, session, prev, step, grid, n, tries=PAGE_TRIES):
    """Post `step` from the page `prev` until the answer is page n of the result grid (n=None: any page with rows), at most
    `tries` posts. Returns the response; raises ItemFailed when no post gives page n."""
    for attempt in range(tries):                   # the site now and then answers a page with an empty grid: post it again
        if attempt:
            hub.metric("page_retry")
        time.sleep(max(PAGE_GAP_S * (1 + 4 * attempt), hub.settings.get("delay_ms", 0) / 1000))
        action, state = form_state(prev.text)
        r = post(hub, session, urljoin(prev.url, action or prev.url), step_data(state, step), referer=prev.url,
                 timeout=step["timeout"])
        if not isinstance(r, requests.Response):
            problem = f"http {r}" if r else "connection error or timeout"
        elif "<title>Error" in r.text[:3000] or "<title>\r\n\tError" in r.text[:3000]:
            problem = "the site's error page"
        else:
            problem = page_problem(r.text, grid, n)
        if not problem:
            return r
    raise ItemFailed(f"page {n}: {problem} ({tries} tries)")


def search(hub, session, url, fields, grid, timeout):
    """GET the form page and search with `fields`: page 1 of the result grid. A "No records" answer is checked once more after
    NO_RECORDS_WAIT_S: under load the site sometimes answers "No records" for a search that has thousands of rows."""
    form = fetch(hub, session, url, timeout=60)
    if not isinstance(form, requests.Response):
        raise ItemFailed(f"form page: http {form}" if form else "form page: connection error")
    step = {"fields": fields, "button": "ctl00$cphBody$btnSearch", "timeout": timeout}
    r = grid_page(hub, session, form, step, grid, 1)
    if not grid_rows(r.text, grid):
        hub.metric("no_records_recheck")
        time.sleep(NO_RECORDS_WAIT_S)
        r = grid_page(hub, session, form, step, grid, 1)
    return r


def seek(hub, session, r, grid, n):
    """From result page `r`, follow the pager to page n: each time the farthest page it links to that is not past n (1 -> 11 ->
    21 -> ... -> n, about n/10 posts). Returns page n; raises ItemFailed when the pager does not lead there."""
    at = current_page(r.text, grid) or 1
    while at != n:
        nxt = max((k for k in linked_pages(r.text, grid) if at < k <= n), default=None)
        if nxt is None:
            raise ItemFailed(f"page {n}: the pager of page {at} does not lead there")
        hub.metric("page_seek")
        r = grid_page(hub, session, r, {"target": grid, "arg": f"Page${nxt}", "timeout": 120}, grid, nxt)
        at = nxt
    return r


def check_time(hub, session, first, grid):
    """Post Page$Last from page 1 to learn the number of pages; raise NotEnoughTime when the walk cannot end before DEADLINE. A
    grid that refuses Page$Last is walked without the check."""
    try:
        last = grid_page(hub, session, first, {"target": grid, "arg": "Page$Last", "timeout": 120}, grid, None, tries=2)
    except ItemFailed:
        hub.metric("page_last_failed")
        return None
    pages = current_page(last.text, grid)
    if pages is None:
        hub.metric("page_last_failed")
        return None
    need, left = (pages - 1) * PAGE_EST_S, DEADLINE - time.time()
    if need > left:
        raise NotEnoughTime(f"not enough time: {pages} pages need ~{need / 60:.0f} min, {left / 60:.0f} min left")
    return pages


def grid_walk(hub, session, url, fields, grid, first_timeout=240):
    """Search with `fields`, then yield (page number, page text) for every page of the result grid, each page posted from the one
    before. When a page does not load (PAGE_TRIES posts), the walk does not start again from page 1: it makes a new search in a new
    session and follows the pager back to that page (seek), at most WALK_RESTARTS times. Raises ItemFailed when a page does not load
    after that, and NotEnoughTime before a walk that cannot end in the time left."""
    r = search(hub, session, url, fields, grid, first_timeout)
    yield 1, r.text
    if not has_page(r.text, grid, 2):
        return
    if grid == AGENCY_GRID:                                  # the Scheme Listing answers Page$Last with its error page
        check_time(hub, session, r, grid)
    n, restarts = 2, 0
    while True:
        try:
            r = grid_page(hub, session, r, {"target": grid, "arg": f"Page${n}", "timeout": 120}, grid, n)
        except ItemFailed as e:
            err, r = e, None
            while r is None:
                if restarts >= WALK_RESTARTS:
                    raise ItemFailed(f"{err}; {restarts} restarts")
                restarts += 1
                hub.metric("walk_restart")
                time.sleep(RESTART_WAIT_S)
                session = requests.Session()                 # new cookies: the old ASP.NET session may be what went wrong
                try:
                    r = seek(hub, session, search(hub, session, url, fields, grid, first_timeout), grid, n)
                except ItemFailed as e2:
                    err = e2
        yield n, r.text
        if not has_page(r.text, grid, n + 1):
            return
        if time.time() > DEADLINE:
            raise ItemFailed(f"out of time after page {n}")
        n += 1


def schemes(hub, session, item, store):
    """One (financial year, scheme type) of the Scheme Listing: every scheme with its grant and plan outlay."""
    p = item["payload"]
    fields = {"ctl00$cphBody$ddlFinancialYear": p["fy"], "ctl00$cphBody$ddlSchemeType": p["scheme_type"]}
    n = 0
    for page_no, page in grid_walk(hub, session, LIST_URL, fields, LIST_GRID):
        for rid, data in scheme_records(grid_rows(page, LIST_GRID), p["fy"], p["scheme_type"], p.get("scheme_type_name")):
            hub.record(rid, {**data, "page": page_no})
            n += 1
    return n, None


def agencies(hub, session, item, store):
    """One (state, agency type) of the agency registry: all statuses, or one when the payload has `status` (the ddlStatus value;
    shorter walks for the big lists). Type Individuals is never queued (personal data). Record ids do not depend on the status
    filter, so a re-run split by status replaces the rows of an earlier run over all statuses."""
    p = item["payload"]
    fields = {"ctl00$cphBody$ddlState": p["state"], "ctl00$cphBody$ddlType": p["type"]}
    if p.get("status"):
        fields["ctl00$cphBody$ddlStatus"] = p["status"]
    n = 0
    for page_no, page in grid_walk(hub, session, AGENCY_URL, fields, AGENCY_GRID):
        for rid, data in agency_records(grid_rows(page, AGENCY_GRID), p["state"], p["type"]):
            hub.record(rid, {**data, "page": page_no})
            n += 1
    return n, None


KINDS = {"explore": explore, "steps": steps, "raw": raw, "schemes": schemes, "agencies": agencies}
LISTS = {"schemes", "agencies"}                    # an empty result is a valid answer for these


def local(url):
    hub = LocalHub()

    def show(rid, r, info):
        print(json.dumps({"record_id": rid, "data": info}, ensure_ascii=False)[:4000])

    n, bad = explore(hub, requests.Session(), {"payload": {"seeds": [url], "max_pages": 1, "max_depth": 0}}, show)
    print(f"{n} pages, {bad} failed, counters {hub.counts}", file=sys.stderr)


def local_item(item):
    """Run one queue item without the hub (from an Indian IP: `myproxy run python pfms.py --item '<json>'`); records to stdout."""
    class Hub(LocalHub):
        def record(self, rid, data):
            print(json.dumps({"record_id": rid, "data": data}, ensure_ascii=False))

    hub = Hub()
    try:
        n, bad = KINDS[item["payload"]["kind"]](hub, requests.Session(), item, lambda rid, r, info: hub.record(rid, info))
        print(f"{n} records, counters {hub.counts}", file=sys.stderr)
    except ItemFailed as e:
        print(f"failed: {e}, counters {hub.counts}", file=sys.stderr)


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
            try:
                n, bad = kind(hub, session, item, store)
            except NotEnoughTime as e:
                # fail it now so that a worker with a full time budget takes it; stop, so that this process does not claim it
                # again (failed items come back after 3 minutes) and use up its attempts
                hub.complete([item["key"]], "failed", error=str(e)[:200])
                break
            except ItemFailed as e:
                hub.complete([item["key"]], "failed", error=str(e)[:200])
                continue
            if n == 0 and item["payload"]["kind"] in LISTS and item.get("attempts", 1) > 1:
                # an earlier attempt of this item got further (or failed), so an empty list now is most likely the site's
                # "No records" under load: fail, so that the hub retries, instead of marking the item done (report-G03 §4.1)
                hub.complete([item["key"]], "failed", error=f"empty list on attempt {item['attempts']}")
            elif n or item["payload"]["kind"] in LISTS:
                hub.complete([item["key"]], "done", fetched=n)
            else:
                hub.complete([item["key"]], "failed", error=f"no page loaded ({bad} failed)")
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if sys.argv[1:2] == ["--item"]:
        local_item(json.loads(sys.argv[2]))
    elif sys.argv[1:2] == ["--local"]:
        local(sys.argv[2] if len(sys.argv) > 2 else SITE + "/")
    else:
        main()
