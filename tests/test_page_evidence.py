"""
Page evidence: the SSRF guard, the HTTP fetch, extraction, and the fetch strategy
(HTTP first, browser only for JS shells, never to get around access controls).
No network and no real browser: DNS, HTTP and the browser subprocess are mocked.
"""
import socket
import subprocess
from unittest.mock import patch

import httpx
import pytest

from app.services import page_evidence as pe


def _addrinfo(*ips):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


# ── SSRF guard ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.10",
    "169.254.169.254",   # cloud metadata endpoint: the classic SSRF target
    "0.0.0.0", "100.64.0.1", "224.0.0.1", "::1", "fe80::1", "fc00::1",
])
def test_non_public_addresses_are_refused(ip):
    with patch.object(pe.socket, "getaddrinfo", return_value=_addrinfo(ip)):
        assert pe.check_public_url("https://example.com/page") is not None


def test_a_public_address_is_allowed():
    with patch.object(pe.socket, "getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert pe.check_public_url("https://example.com/page") is None


def test_one_private_address_among_public_ones_refuses_the_host():
    """DNS answers with several addresses: any non-public one is enough to refuse."""
    with patch.object(pe.socket, "getaddrinfo", return_value=_addrinfo("93.184.216.34", "10.0.0.5")):
        assert "private or reserved" in pe.check_public_url("https://example.com/")


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/x", "gopher://example.com", "javascript:alert(1)",
    "https://user:pass@example.com/", "https://example.com:22/", "https://example.com:3306/", "https:///nohost", "", None,
])
def test_bad_schemes_credentials_ports_and_hosts_are_refused(url):
    with patch.object(pe.socket, "getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert pe.check_public_url(url) is not None


def test_unresolvable_host_is_refused():
    with patch.object(pe.socket, "getaddrinfo", side_effect=socket.gaierror("nope")):
        assert "could not be resolved" in pe.check_public_url("https://nope.invalid/")


@pytest.mark.parametrize("url", ["https://example.com:8443/x", "http://example.com:8080/x", "http://example.com/x"])
def test_normal_web_ports_are_allowed(url):
    with patch.object(pe.socket, "getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert pe.check_public_url(url) is None


# ── HTTP fetch ────────────────────────────────────────────────────────────

ARTICLE_WORDS = " ".join(["word"] * 400)
GOOD_HTML = f"<html><head><title>T</title></head><body><main><h1>Main</h1><h2>One</h2><p>{ARTICLE_WORDS}</p><h2>Two</h2></main></body></html>"


def _client_factory(handler):
    real = httpx.Client

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)
    return factory


def _public_unless_internal(url):
    return "resolves to a private or reserved address" if "internal" in url else None


def _get(handler, url="https://example.com/a"):
    with patch.object(pe.httpx, "Client", _client_factory(handler)), \
         patch.object(pe, "check_public_url", side_effect=_public_unless_internal):
        return pe._http_get(url)


def test_http_get_returns_final_url_and_html():
    final, html = _get(lambda req: httpx.Response(200, html=GOOD_HTML))
    assert final == "https://example.com/a" and "<h1>Main</h1>" in html


def test_redirects_are_followed_by_hand_and_the_final_url_is_returned():
    def handler(req):
        if req.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        return httpx.Response(200, html=GOOD_HTML)
    final, _ = _get(handler)
    assert final == "https://example.com/b"


def test_a_redirect_to_an_internal_host_is_refused_not_followed():
    """The SSRF-by-redirect case: a public page bounces the server to an internal address."""
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(302, headers={"location": "http://internal.local/admin"})
    with pytest.raises(pe.FetchError) as e:
        _get(handler)
    assert "refused" in str(e.value) and e.value.kind == "error"
    assert calls == ["https://example.com/a"]  # the internal URL was never requested


def test_too_many_redirects():
    with pytest.raises(pe.FetchError) as e:
        _get(lambda req: httpx.Response(302, headers={"location": "/loop"}))
    assert "redirects" in str(e.value)


def test_redirect_without_location():
    with pytest.raises(pe.FetchError):
        _get(lambda req: httpx.Response(302))


@pytest.mark.parametrize("status,needle", [(401, "blocked"), (402, "blocked"), (403, "blocked"), (429, "rate limited"), (404, "HTTP 404"), (500, "HTTP 500")])
def test_error_statuses_are_errors(status, needle):
    with pytest.raises(pe.FetchError) as e:
        _get(lambda req: httpx.Response(status))
    assert needle in str(e.value) and e.value.kind == "error"


def test_non_html_is_no_data_not_error():
    with pytest.raises(pe.FetchError) as e:
        _get(lambda req: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"))
    assert e.value.kind == "no_data" and "pdf" in str(e.value)


def test_oversized_page_is_refused():
    big = b"a" * (pe.MAX_BYTES + 10)
    with pytest.raises(pe.FetchError) as e:
        _get(lambda req: httpx.Response(200, headers={"content-type": "text/html"}, content=big))
    assert "2 MB" in str(e.value)


def test_timeout_is_an_error():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)
    with pytest.raises(pe.FetchError) as e:
        _get(handler)
    assert "timed out" in str(e.value)


def test_connection_failure_is_an_error():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)
    with pytest.raises(pe.FetchError) as e:
        _get(handler)
    assert "request failed" in str(e.value)


# ── extraction ────────────────────────────────────────────────────────────

def test_extract_article_structure():
    html = f"""<html><head><title> My  Guide </title></head><body>
      <nav><h2>Menu</h2><a>Home</a></nav>
      <main><h1>B.Ed Eligibility</h1>
        <h2>Who can apply?</h2><p>{ARTICLE_WORDS}</p>
        <h3>Age limit</h3><p>text</p><h2>Documents</h2><p>More text here?</p>
      </main>
      <footer><h2>Footer heading</h2></footer><script>var x=1;</script></body></html>"""
    e = pe.extract_page(html)
    assert e["title"] == "My Guide" and e["h1"] == "B.Ed Eligibility"
    assert [h["text"] for h in e["headings"]] == ["Who can apply?", "Age limit", "Documents"]  # nav/footer headings excluded
    assert "var x" not in e["text"] and "Menu" not in e["text"]
    assert e["word_count"] > 400
    assert "Who can apply?" in e["questions"]
    assert e["extraction_confidence"] == "high" and e["js_shell"] is False


def test_text_is_truncated_but_word_count_is_the_real_count():
    html = "<html><body><main><h2>a</h2><h2>b</h2><p>" + ("word " * 20000) + "</p></main></body></html>"
    e = pe.extract_page(html)
    assert len(e["text"]) <= pe.MAX_TEXT_CHARS and e["word_count"] >= 20000


def test_question_sentences_in_body_text_are_found_and_capped():
    body = " ".join(f"What is the rule number {i} for eligibility?" for i in range(40))
    e = pe.extract_page(f"<html><body><main><p>{body}</p></main></body></html>")
    assert 1 <= len(e["questions"]) <= 15


def test_confidence_tiers():
    medium = pe.extract_page(f"<html><body><h2>x</h2><p>{' '.join(['w'] * 200)}</p></body></html>")
    low = pe.extract_page("<html><body><p>tiny</p></body></html>")
    assert medium["extraction_confidence"] == "medium" and low["extraction_confidence"] == "low"


@pytest.mark.parametrize("html", [
    '<html><body><div id="root"></div></body></html>',
    '<html><body><div id="__next"></div><noscript>Please enable JavaScript to view</noscript></body></html>',
    '<html><body><noscript>You need to enable JavaScript to run this app.</noscript></body></html>',
])
def test_js_shells_are_detected(html):
    assert pe.extract_page(html)["js_shell"] is True


def test_a_real_page_with_a_root_div_is_not_a_shell():
    assert pe.extract_page(f'<html><body><div id="root"><h2>x</h2><p>{ARTICLE_WORDS}</p></div></body></html>')["js_shell"] is False


def test_garbage_input_does_not_raise():
    for junk in ("", "<<<>>>", "\x00\x01", None):
        assert pe.extract_page(junk)["word_count"] == 0


# ── fetch strategy ────────────────────────────────────────────────────────

def _http_ok(html=GOOD_HTML, final="https://example.com/a"):
    return patch.object(pe, "_http_get", return_value=(final, html))


def test_a_normal_page_is_fetched_by_http_only():
    with _http_ok(), patch.object(pe, "_browser_fetch") as browser:
        out = pe.fetch_page_evidence("https://example.com/a")
    browser.assert_not_called()
    assert out["status"] == "ok" and out["fetch_method"] == "http" and out["h1"] == "Main" and out["word_count"] > 400
    assert out["error"] is None


def test_a_js_shell_falls_back_to_the_browser():
    rendered = {"title": "Rendered", "h1": ["Big"], "heading_structure": [{"tag": "h2", "text": "One"}, {"tag": "h2", "text": "Two?"}],
                "custom_content": ARTICLE_WORDS}
    with _http_ok('<html><body><div id="root"></div></body></html>'), patch.object(pe, "_browser_fetch", return_value=rendered) as browser:
        out = pe.fetch_page_evidence("https://example.com/a")
    browser.assert_called_once_with("https://example.com/a")
    assert out["status"] == "ok" and out["fetch_method"] == "browser" and out["h1"] == "Big"
    assert [h["text"] for h in out["headings"]] == ["One", "Two?"] and "Two?" in out["questions"]


def test_the_browser_gets_the_final_redirected_url_not_the_original():
    with _http_ok('<html><body><div id="root"></div></body></html>', final="https://example.com/final"), \
         patch.object(pe, "_browser_fetch", return_value={"error": "x"}) as browser:
        pe.fetch_page_evidence("https://example.com/start")
    browser.assert_called_once_with("https://example.com/final")


@pytest.mark.parametrize("status_error", ["blocked (HTTP 403: login, paywall or access control)", "HTTP 404", "timed out"])
def test_a_blocked_or_failed_fetch_is_an_error_and_never_retried_in_a_browser(status_error):
    with patch.object(pe, "_http_get", side_effect=pe.FetchError(status_error)), patch.object(pe, "_browser_fetch") as browser:
        out = pe.fetch_page_evidence("https://example.com/a")
    browser.assert_not_called()  # no getting around access controls
    assert out["status"] == "error" and out["error"] == status_error and out["text"] == "" and out["word_count"] == 0


def test_a_non_html_page_is_no_data():
    with patch.object(pe, "_http_get", side_effect=pe.FetchError("not an HTML page (application/pdf)", kind="no_data")):
        out = pe.fetch_page_evidence("https://example.com/a.pdf")
    assert out["status"] == "no_data"


def test_js_shell_that_cannot_be_rendered_is_no_data_never_ok():
    with _http_ok('<html><body><div id="root"></div></body></html>'), patch.object(pe, "_browser_fetch", return_value={"error": "timed out"}):
        out = pe.fetch_page_evidence("https://example.com/a")
    assert out["status"] == "no_data" and out["error"] and out["word_count"] == 0


def test_with_the_fallback_off_a_shell_is_no_data():
    with _http_ok('<html><body><div id="root"></div></body></html>'), patch.object(pe, "_browser_fetch") as browser:
        out = pe.fetch_page_evidence("https://example.com/a", allow_browser_fallback=False)
    browser.assert_not_called()
    assert out["status"] == "no_data" and out["fetch_method"] == "http"


def test_usable_http_text_is_kept_at_low_confidence_if_the_browser_fails_on_a_js_page():
    html = f'<html><body><div id="root"></div><p>{" ".join(["word"] * 60)}</p></body></html>'
    with _http_ok(html), patch.object(pe, "_browser_fetch", return_value={"error": "boom"}):
        out = pe.fetch_page_evidence("https://example.com/a")
    assert out["status"] == "ok" and out["fetch_method"] == "http" and out["extraction_confidence"] == "low"


def test_an_unexpected_exception_becomes_an_error_result():
    with patch.object(pe, "_http_get", side_effect=RuntimeError("weird")):
        out = pe.fetch_page_evidence("https://example.com/a")
    assert out["status"] == "error" and "weird" in out["error"]


def test_a_parser_crash_is_reported_not_raised():
    with _http_ok(), patch.object(pe, "extract_page", side_effect=ValueError("bad html")), patch.object(pe, "_browser_fetch", return_value={"error": "x"}):
        out = pe.fetch_page_evidence("https://example.com/a")
    assert out["status"] == "no_data" and "could not parse" in out["error"]


# ── browser subprocess wrapper ────────────────────────────────────────────

def test_browser_fetch_parses_the_crawlers_json():
    done = subprocess.CompletedProcess([], 0, stdout='noise\n@@JSON@@{"title": "T", "custom_content": "hello"}\n', stderr="")
    with patch.object(pe.subprocess, "run", return_value=done):
        assert pe._browser_fetch("https://example.com/") == {"title": "T", "custom_content": "hello"}


def test_browser_fetch_timeout_is_an_error_dict():
    with patch.object(pe.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 75)):
        assert "timed out" in pe._browser_fetch("https://example.com/")["error"]


def test_browser_fetch_with_no_json_is_an_error_dict():
    done = subprocess.CompletedProcess([], 1, stdout="", stderr="Traceback...\nplaywright missing")
    with patch.object(pe.subprocess, "run", return_value=done):
        assert "playwright missing" in pe._browser_fetch("https://example.com/")["error"]


def test_browser_fetch_is_run_from_the_repo_root_with_a_hard_timeout():
    done = subprocess.CompletedProcess([], 0, stdout='@@JSON@@{"error": "x"}', stderr="")
    with patch.object(pe.subprocess, "run", return_value=done) as run:
        pe._browser_fetch("https://example.com/")
    kwargs = run.call_args.kwargs
    assert kwargs["timeout"] == pe.BROWSER_TIMEOUT_SECONDS and (kwargs["cwd"] / "app" / "crawler.py").exists()


# ── batches and the browser budget ────────────────────────────────────────

def test_batch_returns_an_entry_for_every_url():
    def fake(url, allow=True, budget=None):
        return {"status": "ok", "url_seen": url}
    with patch.object(pe, "fetch_page_evidence", side_effect=fake):
        out = pe.fetch_pages_evidence(["https://a.com/1", "https://b.com/2", "https://c.com/3"])
    assert set(out) == {"https://a.com/1", "https://b.com/2", "https://c.com/3"}


def test_empty_batch():
    assert pe.fetch_pages_evidence([]) == {}


def test_only_two_pages_per_analysis_may_use_the_browser():
    shell = '<html><body><div id="root"></div></body></html>'
    rendered = {"title": "R", "custom_content": ARTICLE_WORDS}
    with patch.object(pe, "_http_get", return_value=("https://x.com/p", shell)), patch.object(pe, "_browser_fetch", return_value=rendered) as browser:
        out = pe.fetch_pages_evidence([f"https://x{i}.com/p" for i in range(5)], max_workers=1)
    assert browser.call_count == pe.MAX_BROWSER_FALLBACKS_PER_ANALYSIS
    statuses = sorted(r["fetch_method"] + ":" + r["status"] for r in out.values())
    assert statuses.count("browser:ok") == 2 and statuses.count("http:no_data") == 3


def test_budget_is_thread_safe_and_exact():
    b = pe.BrowserBudget(3)
    assert [b.take() for _ in range(5)] == [True, True, True, False, False]
