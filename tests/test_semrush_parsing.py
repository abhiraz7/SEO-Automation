"""
Semrush CSV parsing tests. The regression that motivated these: Semrush takes
short column codes (Ph, Nq, ...) in export_columns but answers with
human-readable headers ("Keyword", "Search Volume", ...). The parsers must
translate back, otherwise every successful response reads as empty ("no data")
and the router needlessly falls through to DataForSEO. No network involved.
"""
from app import semrush

PHRASE_ALL_CSV = "Keyword;Search Volume;CPC;Competition;Intent\r\ndentist near me;90500;0;0.33;3"
KDI_CSV = "Keyword;Keyword Difficulty Index\r\ndentist near me;27"
RELATED_CSV = (
    "Keyword;Search Volume;CPC;Competition;Intent\r\n"
    "seo;2240000;6.45;0.2;2\r\n"
    "seo tips;165000;3.35;0.01;1"
)
NOTHING_FOUND = "ERROR 50 :: NOTHING FOUND\n"
ORGANIC_CSV = "Domain;Url\r\npracto.com;https://www.practo.com/dentist\r\nclovedental.in;https://clovedental.in/"


def test_parse_csv_maps_human_headers_to_codes():
    row = semrush._parse_csv(PHRASE_ALL_CSV)
    assert row["Ph"] == "dentist near me"
    assert row["Nq"] == "90500"
    assert row["Cp"] == "0"
    assert row["Co"] == "0.33"
    assert row["In"] == "3"


def test_parse_csv_maps_difficulty_header():
    assert semrush._parse_csv(KDI_CSV)["Kd"] == "27"


def test_parse_csv_rows_maps_headers():
    rows = semrush._parse_csv_rows(RELATED_CSV)
    assert [r["Ph"] for r in rows] == ["seo", "seo tips"]
    assert rows[0]["Nq"] == "2240000"


def test_nothing_found_body_parses_empty():
    assert semrush._parse_csv(NOTHING_FOUND) == {}
    assert semrush._parse_csv_rows(NOTHING_FOUND) == []


def test_normalize_maps_intent_code_to_label():
    row = semrush._parse_csv(PHRASE_ALL_CSV) | semrush._parse_csv(KDI_CSV)
    normalized = semrush.normalize_keyword_row(row, "dentist near me")
    assert normalized.volume == 90500
    assert normalized.difficulty == 27
    assert normalized.intent == "transactional"
    assert normalized.source == "semrush"
    assert normalized.status == "ok"


def test_normalize_without_intent_column_is_none():
    normalized = semrush.normalize_keyword_row({"Ph": "x", "Nq": "10"}, "x")
    assert normalized.intent is None


def test_serp_rows_shape_matches_modal_contract():
    rows = semrush._parse_csv_rows(ORGANIC_CSV)
    assert rows[0] == {"Dn": "practo.com", "Ur": "https://www.practo.com/dentist"}
