"""
Migrate business_profiles from flat scalar columns to entity lists, without
dropping any existing data.

Old shape (10 flat Text columns):
    business_name, business_description, industry, products_services,
    target_audience, city, state_region, country, primary_market, brand_tone

New shape:
    brand, industry, services[], locations[], audiences[], tone, usp

Field mapping (documented here since some of it is a judgment call, not a
1:1 rename):
    business_name          -> brand
    industry                -> industry                (unchanged)
    products_services       -> services  = [value] if set else []
    target_audience          -> audiences = [value] if set else []
    brand_tone               -> tone
    city, state_region,
    country                  -> locations[0] = "City, State, Country"
                                (joined, skipping empty parts)
    primary_market           -> locations[1] if set (doesn't fit any of the
                                7 target fields; appended rather than dropped
                                so no data is lost)
    business_description     -> usp
                                (no dedicated "description" field in the new
                                shape; usp is the closest semantic home for
                                free-form descriptive text)

Run with:
    python migrations/002_business_profile_entities.py [path/to/seo_automation.db]

Safe to re-run: if the new columns already exist, it skips straight to a no-op
(it will not re-append primary_market/duplicate data on a second run because it
also drops the old columns at the end of a successful migration).
"""
import json
import sqlite3
import sys

DEFAULT_DB_PATH = "seo_automation.db"

OLD_COLUMNS = [
    "business_name", "business_description", "industry", "products_services",
    "target_audience", "city", "state_region", "country", "primary_market", "brand_tone",
]
NEW_COLUMNS = ["brand", "industry", "services", "locations", "audiences", "tone", "usp"]


def _existing_columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _build_locations(city, state_region, country, primary_market):
    parts = [p for p in (city, state_region, country) if p]
    locations = [", ".join(parts)] if parts else []
    if primary_market:
        locations.append(primary_market)
    return locations


def migrate(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    try:
        cols = _existing_columns(con, "business_profiles")
        if "business_name" not in cols:
            print("Nothing to migrate: business_profiles already has the new shape (or no old columns present).")
            return

        # 1. Add the new columns alongside the old ones.
        for col in ("brand", "services", "locations", "audiences", "tone", "usp"):
            if col not in cols:
                con.execute(f"ALTER TABLE business_profiles ADD COLUMN {col} TEXT")
        # "industry" already exists under the same name in the old schema — no ALTER needed.

        # 2. Transform each existing row's flat values into the new entity shape.
        rows = con.execute(
            "SELECT id, business_name, business_description, industry, products_services, "
            "target_audience, city, state_region, country, primary_market, brand_tone "
            "FROM business_profiles"
        ).fetchall()

        for (row_id, business_name, business_description, industry, products_services,
             target_audience, city, state_region, country, primary_market, brand_tone) in rows:
            services = [products_services] if products_services else []
            audiences = [target_audience] if target_audience else []
            locations = _build_locations(city, state_region, country, primary_market)

            con.execute(
                "UPDATE business_profiles SET brand = ?, services = ?, locations = ?, "
                "audiences = ?, tone = ?, usp = ? WHERE id = ?",
                (
                    business_name,
                    json.dumps(services),
                    json.dumps(locations),
                    json.dumps(audiences),
                    brand_tone,
                    business_description,
                    row_id,
                ),
            )

        # 3. Drop the old flat columns now that their data lives in the new ones.
        #    Requires SQLite 3.35+ (DROP COLUMN support).
        for col in OLD_COLUMNS:
            if col == "industry":
                continue  # kept as-is, not dropped
            con.execute(f"ALTER TABLE business_profiles DROP COLUMN {col}")

        con.commit()
        print(f"Migrated {len(rows)} business_profiles row(s) to the entity schema.")
    finally:
        con.close()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    migrate(db_path)
