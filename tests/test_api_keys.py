"""The key panel shows status, never values: the Settings page has no login."""
from app import api_keys

SECRET = "sk-ant-api03-SUPERSECRETVALUE-1234567890abcdef"


def test_unset_and_blank_values_are_reported_missing():
    rows = {r["name"]: r for r in api_keys.key_status({"ANTHROPIC_API_KEY": "   "})}
    assert rows["ANTHROPIC_API_KEY"]["is_set"] is False and rows["ANTHROPIC_API_KEY"]["masked"] == ""
    assert rows["SEMRUSH_API_KEY"]["is_set"] is False


def test_long_values_show_only_length_and_last_four():
    assert api_keys.mask(SECRET) == f"set, ends …cdef ({len(SECRET)} characters)"


def test_short_values_show_no_characters_at_all():
    assert api_keys.mask("abc12345") == "set (8 characters)"


def test_no_raw_value_or_long_fragment_ever_appears_in_the_status():
    env = {k: SECRET for k, _ in api_keys.KEYS}
    rendered = repr(api_keys.key_status(env))
    assert SECRET not in rendered
    assert "SUPERSECRET" not in rendered and "sk-ant" not in rendered


def test_every_documented_key_has_a_purpose_and_a_unique_name():
    names = [k for k, _ in api_keys.KEYS]
    assert len(names) == len(set(names)) and all(p for _, p in api_keys.KEYS)
    assert {"ANTHROPIC_API_KEY", "DATAFORSEO_PASSWORD", "SEMRUSH_API_KEY", "GSC_TOKEN_KEY"} <= set(names)


def test_reads_the_real_environment_by_default(monkeypatch):
    monkeypatch.setenv("SEMRUSH_API_KEY", "a1b2c3d4e5f60718293a4b5c6d7e8f90")
    row = next(r for r in api_keys.key_status() if r["name"] == "SEMRUSH_API_KEY")
    assert row["is_set"] and "a1b2c3d4" not in row["masked"] and row["masked"].endswith("(32 characters)")
