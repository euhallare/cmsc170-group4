#!/usr/bin/env python3
"""
scrape.py - READ-ONLY collector of UPLB AMIS class data for the CMSC 170 project.

What it does
------------
1. Gets a Bearer token with auth.py (your existing Playwright login helper).
2. Calls GET /api/students/classes for each subject, page by page.
3. REMOVES instructor names / user ids before anything is written to disk
   (only the numeric faculty_id is kept - the project guidelines forbid PII).
4. Saves one JSON file per page under data/raw/term_<ID>/<SUBJECT>/page_NNNN.json.
   Pages that already exist are skipped, so you can stop and resume safely.

What it does NOT do
-------------------
It never bookmarks, enlists, or sends anything to AMIS. GET requests only.

Usage (run from the folder that also contains auth.py)
------------------------------------------------------
  python scrape.py --probe                 # 1st run: ~15 requests to check how the API behaves
  python scrape.py                         # CMSC only (small set)
  python scrape.py --set curriculum        # CMSC, MATH, STAT, HK, NSTP, ... (BS CS flowchart subjects)
  python scrape.py --subjects CMSC STAT    # any subjects you like
  python scrape.py --set all               # every class in the term (big!)
"""
import argparse
import datetime
import json
import os
import random
import re
import sys
import time

import requests

BASE_URL = "https://api-amis.uplb.edu.ph/api/students/classes"
DEFAULT_TERM_ID = 1261          # current term; change with --term
DELAY = (1.0, 2.0)              # random pause (seconds) between requests - be gentle

SUBJECT_SETS = {
    "small": ["CMSC"],
    # every subject prefix that appears in the BS CS (2018) flowchart
    "curriculum": ["CMSC", "MATH", "STAT", "HK", "NSTP", "ETHICS", "STS",
                   "ARTS", "KAS", "PI", "COMM", "ENG"],
}

# Keys removed everywhere in the JSON before saving (instructor identity).
STRIP_KEYS = {"first_name", "middle_name", "last_name", "formatted_name",
              "user_id", "user"}


def scrub(obj):
    """Recursively drop personal-identity keys. faculty_id (a number) stays."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k not in STRIP_KEYS}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj


class AmisSession:
    """requests session that knows how to refresh its Bearer token."""

    def __init__(self):
        self.http = requests.Session()
        self.http.headers.update({
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        self.refresh_token()

    def refresh_token(self):
        # imported here so scrub()/build tools can be tested without Playwright
        from auth import get_fresh_bearer_token
        token = get_fresh_bearer_token()
        if not token:
            sys.exit("Could not get a Bearer token. Log in to AMIS in the browser "
                     "window that opens, then run the script again.")
        self.http.headers["Authorization"] = f"Bearer {token}"
        print("  token OK")

    def get_json(self, params):
        """GET one page. Retries on network errors / 5xx / 429; refreshes on 401."""
        for attempt in range(5):
            try:
                resp = self.http.get(BASE_URL, params=params, timeout=30)
            except requests.RequestException as exc:
                wait = 3 * 2 ** attempt
                print(f"  network error ({exc}); retrying in {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code == 401:
                print("  401 - token expired, refreshing ...")
                self.refresh_token()
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 5 * 2 ** attempt
                print(f"  server said {resp.status_code}; waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()      # other 4xx: a real error, show it
            return resp.json()
        raise RuntimeError("gave up after repeated failures")


def build_params(term_id, subject, page, items, section_like, class_status):
    params = {
        "page": str(page),
        "items": str(items),
        "status": "Active",
        "by_term_type": "true",
        "term_id": str(term_id),
    }
    if subject:
        params["course_code_like"] = subject
    if section_like:
        params["section_like"] = section_like
    if class_status:
        params["class_status"] = class_status
    return params


def tag_for(subject):
    return re.sub(r"[^A-Za-z0-9]+", "_", subject or "").strip("_") or "ALL"


def scrape_subject(sess, subject, term_id, out_root, items, section_like,
                   class_status, assume_yes):
    folder = os.path.join(out_root, f"term_{term_id}", tag_for(subject))
    os.makedirs(folder, exist_ok=True)
    label = subject or "ALL"
    page, last_page, rows_seen = 1, 1, 0
    while page <= last_page:
        path = os.path.join(folder, f"page_{page:04d}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            src = "cached"
        else:
            raw = sess.get_json(build_params(term_id, subject, page, items,
                                             section_like, class_status))
            data = scrub(raw)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            src = "fetched"
            time.sleep(random.uniform(*DELAY))
        block = data.get("classes") or {}
        rows = block.get("data") or []
        last_page = int(block.get("last_page") or 1)
        rows_seen += len(rows)
        print(f"[{label}] page {page}/{last_page}  rows={len(rows)}  ({src})")
        if page == 1 and last_page > 150 and not assume_yes:
            ans = input(f"  {label} has {last_page} pages (~{last_page * 1.5 / 60:.0f}+ min). "
                        "Continue? [y/N] ").strip().lower()
            if ans != "y":
                print("  skipped.")
                break
        if not rows:
            break
        page += 1
    return rows_seen


def probe(sess, term_id, subject):
    """A few cheap requests that answer the open questions about the API."""
    def show(label, params):
        try:
            block = (sess.get_json(params) or {}).get("classes") or {}
            print(f"{label:<44} total={block.get('total')}  "
                  f"per_page={block.get('per_page')}  last_page={block.get('last_page')}")
            return block
        except Exception as exc:                      # keep probing on errors
            print(f"{label:<44} ERROR: {exc}")
            return {}
        finally:
            time.sleep(random.uniform(*DELAY))

    print(f"\n--- PROBE: subject={subject}, term={term_id} ---")
    base = build_params(term_id, subject, 1, 20, None, None)
    block = show("baseline (no section_like, no class_status)", base)
    codes = sorted({r.get("course_code") for r in (block.get("data") or [])})
    print("   course codes on page 1:", codes)
    show('with section_like="--"  (sniper value)',
         build_params(term_id, subject, 1, 20, "--", None))
    show("with class_status=Open",
         build_params(term_id, subject, 1, 20, None, "Open"))
    for name in ("items", "per_page", "limit", "size"):
        p = dict(base)
        p.pop("items", None)
        p[name] = "50"
        show(f"page-size param '{name}'=50", p)
    print("\nEarlier terms (does AMIS still return them?):")
    for t in range(term_id - 8, term_id):
        show(f"  term_id={t}", build_params(t, subject, 1, 20, None, None))
    print("\nPaste this output back to Claude if anything looks odd.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--term", type=int, default=DEFAULT_TERM_ID)
    ap.add_argument("--set", choices=list(SUBJECT_SETS) + ["all"], default="small")
    ap.add_argument("--subjects", nargs="+", help="explicit subject prefixes, e.g. CMSC MATH")
    ap.add_argument("--items", type=int, default=20, help="page-size request (server may ignore)")
    ap.add_argument("--section-like", default=None,
                    help='optional section_like filter (the sniper used "--")')
    ap.add_argument("--class-status", default=None,
                    help="optional class_status filter, e.g. Open (default: none = all sections)")
    ap.add_argument("--out", default=os.path.join("data", "raw"))
    ap.add_argument("--probe", action="store_true", help="test API behaviour and exit")
    ap.add_argument("--yes", action="store_true", help="don't ask before very long scrapes")
    args = ap.parse_args()

    if args.subjects:
        subjects = args.subjects
    elif args.set == "all":
        subjects = [None]
    else:
        subjects = SUBJECT_SETS[args.set]

    print("Starting. A browser window will open for the AMIS token ...")
    sess = AmisSession()

    if args.probe:
        probe(sess, args.term, subjects[0] or "CMSC")
        return

    counts = {}
    for subj in subjects:
        counts[subj or "ALL"] = scrape_subject(
            sess, subj, args.term, args.out, args.items,
            args.section_like, args.class_status, args.yes)

    meta_path = os.path.join(args.out, f"term_{args.term}", "meta.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    meta.update({
        "term_id": args.term,
        "last_scrape_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": BASE_URL,
        "filters": {"items": args.items, "section_like": args.section_like,
                    "class_status": args.class_status, "status": "Active"},
        "note": "Instructor names removed at scrape time; only faculty_id kept.",
    })
    meta.setdefault("rows_per_subject", {}).update(counts)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print("\nDone.", counts)
    print(f"Raw files: {os.path.join(args.out, f'term_{args.term}')}")
    print("Next: python build_datasets.py")


if __name__ == "__main__":
    main()