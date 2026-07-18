"""
Standalone SEMrush API smoke test.

Not part of the app -- run directly to sanity-check API key + report access:

    python scripts/test_semrush_api.py [domain] [keyword]

Reads SEMRUSH_API_KEY from the environment (or .env in the repo root) and
hits a handful of SEMrush reports, printing raw CSV + parsed values for each
so you can see exactly what a given API key/plan is entitled to.
"""
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://api.semrush.com"
ANALYTICS = "https://api.semrush.com/analytics/v1/"


def load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.read().decode("utf-8")


def parse_csv_rows(text: str) -> list[dict]:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return []
    headers = lines[0].split(";")
    return [dict(zip(headers, line.split(";"))) for line in lines[1:]]


def run_report(title: str, url: str) -> None:
    print(f"\n--- {title} ---")
    print(f"GET {url}")
    try:
        raw = get(url)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body.strip()}")
        return
    except Exception as e:
        print(f"ERROR: {e}")
        return

    print(f"RAW: {raw.strip()}")
    rows = parse_csv_rows(raw)
    if not rows:
        print("PARSED: (no rows)")
        return
    for i, row in enumerate(rows):
        print(f"PARSED[{i}]: {row}")


def main() -> None:
    load_dotenv(os.path.join(REPO_ROOT, ".env"))

    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    if not api_key:
        print("SEMRUSH_API_KEY not set (checked environment and .env). Aborting.")
        sys.exit(1)

    domain = sys.argv[1] if len(sys.argv) > 1 else "example.com"
    keyword = sys.argv[2] if len(sys.argv) > 2 else "seo tools"
    database = "us"

    print(f"Using domain={domain!r} keyword={keyword!r} database={database!r}")
    print(f"API key loaded: {api_key[:4]}...{api_key[-4:]} (len={len(api_key)})")

    # 1. Account/API units left -- cheap way to confirm the key itself is valid.
    run_report(
        "API units remaining",
        f"https://www.semrush.com/users/countapiunits.html?key={api_key}",
    )

    # 2. Domain ranks -- organic traffic + keyword count for a domain.
    run_report(
        "Domain ranks (organic traffic/keywords)",
        f"{BASE}/?type=domain_ranks&key={api_key}"
        f"&export_columns=Dn,Rk,Or,Ot,Oc,Ad,At,Ac&domain={domain}&database={database}",
    )

    # 3. Backlinks overview -- authority score + referring domains.
    run_report(
        "Backlinks overview (authority score, referring domains)",
        f"{ANALYTICS}?key={api_key}&type=backlinks_overview"
        f"&target={domain}&target_type=root_domain"
        f"&export_columns=ascore,total,domains_num,urls_num,ips_num,follows_num,nofollows_num",
    )

    # 4. Keyword overview -- volume, CPC, competition.
    run_report(
        "Keyword overview (phrase_all)",
        f"{BASE}/?type=phrase_all&key={api_key}"
        f"&export_columns=Ph,Nq,Cp,Co,Nr,Td&phrase={urllib.parse.quote(keyword)}&database={database}",
    )

    # 5. Keyword difficulty.
    run_report(
        "Keyword difficulty (phrase_kdi)",
        f"{BASE}/?type=phrase_kdi&key={api_key}"
        f"&export_columns=Ph,Kd&phrase={urllib.parse.quote(keyword)}&database={database}",
    )

    # 6. Related keywords.
    run_report(
        "Related keywords (phrase_related)",
        f"{BASE}/?type=phrase_related&key={api_key}"
        f"&export_columns=Ph,Nq,Cp,Co,Nr&phrase={urllib.parse.quote(keyword)}"
        f"&database={database}&display_limit=10",
    )

    # 7. Keyword questions.
    run_report(
        "Keyword questions (phrase_questions)",
        f"{BASE}/?type=phrase_questions&key={api_key}"
        f"&export_columns=Ph,Nq&phrase={urllib.parse.quote(keyword)}"
        f"&database={database}&display_limit=10",
    )

    # 8. Domain organic keywords list.
    run_report(
        "Domain organic keywords (domain_organic)",
        f"{BASE}/?type=domain_organic&key={api_key}"
        f"&export_columns=Ph,Po,Nq,Cp,Co,Tr,Tc,Ur&domain={domain}"
        f"&database={database}&display_limit=10",
    )

    print("\nDone.")


if __name__ == "__main__":
    main()
