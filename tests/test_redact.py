"""The log viewer shows raw logs to anyone who can open Settings, so redaction
must catch credentials however they get into a message."""
from app.redact import redact

ENV = {
    "ANTHROPIC_API_KEY": "sk-ant-api03-REALSECRETVALUE1234567890",
    "SEMRUSH_API_KEY": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "DATAFORSEO_PASSWORD": "hunter2-hunter2",
    "DATAFORSEO_LOGIN": "someone@example.com",   # not secret-named: left alone
    "TINY_KEY": "abc",                            # too short to replace safely
    "APP_COMMIT": "12f9be47ab9d62d28b3104a257838c75b6774487",
}


def test_live_env_values_are_replaced_with_the_variable_name():
    out = redact("401 from api: invalid key sk-ant-api03-REALSECRETVALUE1234567890 for user", ENV)
    assert "REALSECRET" not in out and "[redacted:ANTHROPIC_API_KEY]" in out


def test_semrush_key_in_a_url_is_redacted_even_when_not_in_the_environment():
    url = "HTTPError 403 for https://api.semrush.com/?type=domain_ranks&key=ffffffffffffffffffffffffffffffff&domain=x.com"
    out = redact(url, {})
    assert "ffffffff" not in out and "key=[redacted]" in out and "domain=x.com" in out


def test_known_key_shapes_bearer_and_basic():
    assert "AbC" not in redact("Authorization: Bearer AbCdEfGhIjKlMnOp", {})
    assert "dXNlcjpwYXNz" not in redact("Authorization: Basic dXNlcjpwYXNzd29yZA==", {})
    assert "AIzaSy" not in redact("bad key AIzaSyA1234567890abcdefghijklmnop", {})
    assert "sk-proj" not in redact("sk-proj-abcdefghijklmnopqrstuvwxyz0123", {})


def test_password_and_token_pairs():
    out = redact("connect failed password=swordfish&token=abc123xyz&user=bob", {})
    assert "swordfish" not in out and "abc123xyz" not in out and "user=bob" in out


def test_ordinary_text_and_short_or_non_secret_values_are_untouched():
    text = "DataForSEO returned 40501 for someone@example.com on https://example.com/notes/hindi (हिंदी) commit 12f9be4"
    assert redact(text, ENV) == text
    assert redact("the tiny value abc is fine", ENV) == "the tiny value abc is fine"


def test_longest_value_wins_when_one_secret_contains_another():
    env = {"A_TOKEN": "supersecret", "B_TOKEN": "supersecret-and-more"}
    assert redact("x supersecret-and-more y", env) == "x [redacted:B_TOKEN] y"


def test_none_and_non_strings_do_not_raise():
    assert redact(None, ENV) == "" and redact(12345, ENV) == "12345"


def test_real_process_environment_is_used_by_default(monkeypatch):
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "tok_live_0123456789abcdef")
    assert "0123456789abcdef" not in redact("failed with tok_live_0123456789abcdef")
