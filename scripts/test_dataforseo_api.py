"""
Isolation test for DataForSEO API — mirrors scripts/test_semrush_api.py so
results are directly comparable. Tests auth + the exact three report types
app/dataforseo.py depends on: keyword_overview, related_keywords, and a
question-style filter over related_keywords.

Usage:
    python scripts/test_dataforseo_api.py "digital marketing"

Requires DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD in .env (or exported in
the shell). Location defaults to India (2356) since that's VTechys's primary
market — pass --location-code to override (e.g. 2840 for US).
"""
import argparse
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.dataforseo.com/v3"
LOCATION_INDIA = 2356
LOCATION_US = 2840
LANGUAGE_EN = "en"


def _auth() -> tuple[str, str] | None:
    login = os.environ.get("DATAFORSEO_LOGIN", "").strip()
    password = os.environ.get("DATAFORSEO_PASSWORD", "").strip()
    if not login or not password:
        return None
    return (login, password)


def _post(path: str, payload: list[dict]) -> httpx.Response:
    auth = _auth()
    if not auth:
        print("ERROR: DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set in environment.")
        sys.exit(1)
    url = f"{BASE}{path}"
    print(f"POST {url}")
    print(f"PAYLOAD: {json.dumps(payload)}")
    return httpx.post(url, json=payload, auth=auth, timeout=20)


def _report(label: str, resp: httpx.Response) -> None:
    print(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        print("RAW (not JSON):", resp.text[:500])
        return

    status_code = data.get("status_code")
    status_message = data.get("status_message")
    print(f"TOP-LEVEL status_code={status_code} status_message={status_message}")

    tasks = data.get("tasks") or []
    if not tasks:
        print("NO TASKS RETURNED")
        return

    for task in tasks:
        t_status = task.get("status_code")
        t_message = task.get("status_message")
        print(f"  task status_code={t_status} status_message={t_message}")
        result = task.get("result") or []
        if not result:
            print("  NO RESULT ITEMS")
            continue
        items = result[0].get("items") if isinstance(result[0], dict) else None
        if items is None:
            print("  RESULT (no 'items' key):", json.dumps(result[0])[:400])
            continue
        print(f"  {len(items)} item(s) returned")
        for item in items[:3]:
            kw = item.get("keyword")
            info = item.get("keyword_info") or {}
            props = item.get("keyword_properties") or {}
            intent = item.get("search_intent_info") or {}
            print(
                f"    - keyword={kw!r} volume={info.get('search_volume')} "
                f"cpc={info.get('cpc')} difficulty={props.get('keyword_difficulty')} "
                f"intent={intent.get('main_intent')}"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("keyword", help="seed keyword / phrase to test, e.g. 'digital marketing'")
    parser.add_argument("--location-code", type=int, default=LOCATION_INDIA,
                         help=f"DataForSEO location_code (default {LOCATION_INDIA} = India; "
                              f"{LOCATION_US} = US)")
    args = parser.parse_args()

    auth = _auth()
    if not auth:
        print("ERROR: DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set. Check your .env.")
        sys.exit(1)
    login, _ = auth
    print(f"Using login={login} keyword={args.keyword!r} location_code={args.location_code}")
    print()

    # --- 1. Keyword overview (the exact call app/dataforseo.py::fetch_keyword_overview makes) ---
    print("--- Keyword overview (keyword_overview/live) ---")
    resp = _post(
        "/dataforseo_labs/google/keyword_overview/live",
        [{"keywords": [args.keyword], "location_code": args.location_code, "language_code": LANGUAGE_EN}],
    )
    _report("keyword_overview", resp)
    print()

    # --- 2. Related keywords (Suggestions tab primary source) ---
    print("--- Related keywords (related_keywords/live) ---")
    resp = _post(
        "/dataforseo_labs/google/related_keywords/live",
        [{"keyword": args.keyword, "location_code": args.location_code, "language_code": LANGUAGE_EN, "limit": 10}],
    )
    _report("related_keywords", resp)
    print()

    # --- 3. Account status sanity check: also try a known-cheap SERP call ---
    print("--- SERP organic (serp/google/organic/live/advanced) — used by 'View SERP' ---")
    resp = _post(
        "/serp/google/organic/live/advanced",
        [{"keyword": args.keyword, "location_code": args.location_code, "language_code": LANGUAGE_EN, "device": "desktop"}],
    )
    print(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
        print(f"status_code={data.get('status_code')} status_message={data.get('status_message')}")
        tasks = data.get("tasks") or []
        if tasks:
            print(f"  task status_code={tasks[0].get('status_code')} status_message={tasks[0].get('status_message')}")
    except Exception:
        print("RAW (not JSON):", resp.text[:300])

    print()
    print("=" * 60)
    print("READ THE RESULTS:")
    print("  - HTTP 401                         -> bad login/password")
    print("  - status_code 40xxx w/ 'not enough" )
    print("    funds' or similar                -> trial credit exhausted / plan issue")
    print("  - status_code 20000 + 0 items       -> auth+entitlement OK, genuinely no data")
    print("    for this keyword/location combo   (try a more common keyword to confirm)")
    print("  - status_code 20000 + items with     -> fully working end to end")
    print("    real volume/difficulty/intent")
    print("=" * 60)


if __name__ == "__main__":
    main()