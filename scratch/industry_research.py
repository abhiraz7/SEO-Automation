#!/usr/bin/env python3
"""Standalone DataForSEO research runner for technology opportunities in Indian traditional industries.

Credentials are read only from DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD.
Example:
  export DATAFORSEO_LOGIN="..."
  export DATAFORSEO_PASSWORD="..."
  python scratch/industry_research.py

The output is saved under scratch/results/. Search volume and ad CPC are demand
proxies only; they do not prove operational pain or willingness to pay.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://api.dataforseo.com/v3/keywords_data/google_ads/keywords_for_keywords/live"
INDIA_LOCATION_CODE = 2356
LANGUAGE_CODE = "en"

# Each sector is a separate DataForSEO task so results remain attributable.
SECTORS = {
    "food_processing": [
        "food processing plant automation",
        "food processing machinery",
        "food quality testing equipment",
        "food waste management",
    ],
    "manufacturing": [
        "industrial machine monitoring",
        "predictive maintenance software",
        "factory automation system",
        "machine condition monitoring",
    ],
    "wholesale_distribution": [
        "wholesale inventory management software",
        "distributor management software",
        "B2B order management software",
        "procurement management software",
    ],
    "construction_materials": [
        "construction material management software",
        "building material inventory software",
        "construction equipment tracking",
        "concrete batching plant automation",
    ],
    "water_infrastructure": [
        "industrial water quality monitoring",
        "water tank level monitoring system",
        "water leakage detection system",
        "IoT water monitoring system",
    ],
}


def call_api(tasks: list[dict], timeout: int) -> dict:
    login = os.environ.get("DATAFORSEO_LOGIN", "").strip()
    password = os.environ.get("DATAFORSEO_PASSWORD", "").strip()
    if not login or not password:
        raise RuntimeError("Missing DATAFORSEO_LOGIN or DATAFORSEO_PASSWORD.")

    encoded = base64.b64encode(f"{login}:{password}".encode()).decode("ascii")
    request = Request(
        API_URL,
        data=json.dumps(tasks).encode("utf-8"),
        headers={
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"DataForSEO HTTP {exc.code}: {detail}") from None
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from None

    if payload.get("status_code") != 20000:
        raise RuntimeError(
            f"DataForSEO error {payload.get('status_code')}: "
            f"{payload.get('status_message', 'unknown error')}"
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover search-demand signals for traditional-industry technology problems."
    )
    parser.add_argument("--sectors", default=",".join(SECTORS),
                        help=f"Comma-separated keys: {', '.join(SECTORS)}")
    parser.add_argument("--location-code", type=int, default=INDIA_LOCATION_CODE)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--output-dir", default="scratch/results")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    selected = [part.strip() for part in args.sectors.split(",") if part.strip()]
    unknown = sorted(set(selected) - set(SECTORS))
    if unknown:
        parser.error(f"Unknown sector(s): {', '.join(unknown)}")
    if not selected:
        parser.error("Choose at least one sector.")

    tasks = [
        {
            "location_code": args.location_code,
            "language_code": LANGUAGE_CODE,
            "keywords": SECTORS[sector],
            "closely_variants": True,
            "sort_by": "search_volume",
            "tag": f"traditional-industry-{sector}",
        }
        for sector in selected
    ]

    print(f"Prepared {len(tasks)} task(s), India location code={args.location_code}.")
    for sector in selected:
        print(f"  {sector}: {len(SECTORS[sector])} seed terms")
    if args.dry_run:
        print("Dry run: no API request sent.")
        return 0

    payload = call_api(tasks, args.timeout)
    output_rows = []
    response_tasks = payload.get("tasks") or []
    for index, task in enumerate(response_tasks):
        sector = selected[index] if index < len(selected) else "unknown"
        if task.get("status_code") != 20000:
            output_rows.append({
                "sector": sector,
                "keyword": "",
                "search_volume": "",
                "cpc_usd": "",
                "competition": "",
                "competition_index": "",
                "low_top_of_page_bid_usd": "",
                "high_top_of_page_bid_usd": "",
                "task_error": task.get("status_message", "Task failed"),
            })
            continue
        for item in task.get("result") or []:
            output_rows.append({
                "sector": sector,
                "keyword": item.get("keyword", ""),
                "search_volume": item.get("search_volume"),
                "cpc_usd": item.get("cpc"),
                "competition": item.get("competition"),
                "competition_index": item.get("competition_index"),
                "low_top_of_page_bid_usd": item.get("low_top_of_page_bid"),
                "high_top_of_page_bid_usd": item.get("high_top_of_page_bid"),
                "task_error": "",
            })

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out / f"industry_research_{stamp}.json"
    csv_path = out / f"industry_research_{stamp}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    columns = [
        "sector", "keyword", "search_volume", "cpc_usd", "competition",
        "competition_index", "low_top_of_page_bid_usd",
        "high_top_of_page_bid_usd", "task_error",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Rows returned: {len(output_rows)}")
    print(f"API-reported cost: {payload.get('cost', 'not supplied')} USD")
    print(f"JSON output: {json_path}")
    print(f"CSV output:  {csv_path}")
    print("Interpret search volume/CPC as demand clues, not buyer validation.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
