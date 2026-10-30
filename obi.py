"""The family's `obi` task (see scrape.json): Open Budgets India's normalised state budget indicators (G02).

openbudgetsindia.org is gone (the domain now serves an unrelated site; see reportG02.md). The normalised state data OBI published
survives as JSON inside CBGA's own repo cbgaindia/story-generator (src/data/*.js), pinned here to one commit so runs are
reproducible. Each queue item is one dataset (expenditure or receipts): fetch the data and metadata files, keep both in the family's
bucket, and store one record per (indicator, state, estimate, year) value, e.g. per-capita expenditure on Health in Bihar, BE 2016-17.
Try it without the hub: python obi.py --local [expenditure|receipts]
"""
import json
import os
import re
import sys
import time

import requests

from crawl import LocalHub, fetch

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
SOURCE = os.environ.get("OBI_SOURCE", "https://raw.githubusercontent.com/cbgaindia/story-generator/972ac485e67afe95ec7ab0bf67222cdc27c4f9de/src/data")
DEADLINE = time.time() + float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600 - 120
ESTIMATES = {"A": "Actuals", "BE": "Budget Estimates", "RE": "Revised Estimates"}


def js_value(text):
    """The JSON literal assigned in `export let name = [...];`."""
    return json.loads(text[text.index("=") + 1:].strip().rstrip(";"))


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def parse(dataset, data_js, meta_js):
    """[(record_id, data)], one per non-empty value. Pure: test it on saved files."""
    meta = {(m.get("category_slug", ""), m["record_slug"]): m for m in js_value(meta_js)}
    data = js_value(data_js)
    if dataset == "receipts":                                     # receipts: flat list of indicators, no sector
        data = [{"category_slug": "", "sub_records": data}]
    out = {}
    for cat in data:
        for rec in cat["sub_records"]:
            m = meta.get((cat["category_slug"], rec["record_slug"]), {})
            for row in rec["record_figures"]:
                state = row["grpby_name"]
                for est, years in row["figures"].items():
                    for pair in years:
                        for year, value in pair.items():
                            try:
                                value = float(value)
                            except ValueError:                    # "", "NA", and spreadsheet errors such as "Err:512"
                                continue
                            parts = [dataset, cat["category_slug"], rec["record_slug"], slug(state), est, year]
                            rid = "/".join(p for p in parts if p)
                            out[rid] = {"dataset": dataset, "sector": m.get("category"), "sector_slug": cat["category_slug"] or None,
                                        "indicator": rec.get("record_name") or m.get("record"), "indicator_slug": rec["record_slug"],
                                        "unit": rec.get("unit") or m.get("unit"), "state": state, "estimate": est,
                                        "estimate_name": ESTIMATES.get(est, est), "year": year, "value": value,
                                        "source": m.get("source"), "notes": m.get("notes") or None}
    return list(out.items())                                      # the source repeats some (state, year) pairs: last one wins


def load(hub, session, payload):
    """(data_js, meta_js) or None."""
    texts = []
    for url in (payload["data_url"], payload["meta_url"]):
        r = fetch(hub, session, url)
        if not isinstance(r, requests.Response):
            return None
        texts.append(r.text)
    return texts


def local(dataset):
    hub = LocalHub()
    p = {"data_url": f"{SOURCE}/{dataset}_data.js", "meta_url": f"{SOURCE}/{dataset}_metadata.js"}
    texts = load(hub, requests.Session(), p)
    rows = parse(dataset, *texts) if texts else []
    for rid, data in rows:
        print(json.dumps({"record_id": rid, "data": data}, ensure_ascii=False))
    print(f"{len(rows)} records, counters {hub.counts}", file=sys.stderr)


def main():
    from scrape_hub_client import HubClient      # imported here so --local works without it
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")), total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    session = requests.Session()
    try:
        while time.time() < DEADLINE:
            if hub.settings.get("paused"):
                time.sleep(30)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break
            item, p = items[0], items[0]["payload"]
            texts = load(hub, session, p)
            if texts is None:
                hub.complete([item["key"]], "failed", error="source file did not load")
                continue
            hub.upload_many([(f"source/{p['dataset']}_{name}.js", t.encode(), "text/javascript")
                             for name, t in zip(("data", "metadata"), texts)])
            rows = parse(p["dataset"], *texts)
            for rid, data in rows:
                hub.record(rid, data)
            hub.complete([item["key"]], "done", fetched=len(rows))
            time.sleep(max(1.0, hub.settings.get("delay_ms", 0) / 1000))
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local(sys.argv[2] if len(sys.argv) > 2 else "expenditure")
    else:
        main()
