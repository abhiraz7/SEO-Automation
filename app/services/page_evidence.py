"""
One-off page evidence for the AI Competitor Gap / Content Optimizer features:
fetch a page ONCE, extract what analysis needs (title, H1, H2/H3, main text, word
count, questions) and say how much to trust the extraction.

NOT a crawler: no queue, no persistence of its own, no link following. It reads
the handful of pages a single analysis needs and forgets the connection.

Fetch strategy (the spec's order):
  1. plain HTTP first;
  2. a real browser (Crawl4AI/Playwright, via the existing crawler) ONLY when the
     HTTP answer is a JavaScript shell / has no usable text. A page that answered
     "403", "404", a paywall or a timeout is reported as an error, NOT retried in a
     browser: this module does not try to get around access controls.

Every result carries an explicit status -- "ok", "no_data" (fetched fine, nothing
extractable) or "error" (could not fetch) -- so a failed fetch can never look like
a successful empty page.

SECURITY: the URLs come from a search engine's results, i.e. third-party input,
and this server fetches them. So every hop (including each redirect) must resolve
ONLY to public addresses; redirects are followed by hand so each one is re-checked,
the body is size-capped, and non-HTML answers are refused.
"""
import ipaddress
import json
import re
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "VTechSEO-PageAnalysis/1.0 (single-page analysis; honest identification, no evasion)"
REQUEST_TIMEOUT = 15
MAX_BYTES = 2_000_000          # a page bigger than this is not an article we should be parsing
MAX_REDIRECTS = 3
ALLOWED_PORTS = (80, 443, 8080, 8443)
MAX_TEXT_CHARS = 30_000        # stored evidence, never copied into generated content
MIN_USABLE_WORDS = 30          # below this there is nothing to analyse
JS_SHELL_WORDS = 80            # below this a JS mount point suggests an un-rendered app shell
BROWSER_TIMEOUT_SECONDS = 75
MAX_BROWSER_FALLBACKS_PER_ANALYSIS = 2   # a browser is heavy: cap how many pages of one analysis may use it
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BROWSER_LOCK = threading.Lock()         # one browser at a time (this server has ~1 GB of RAM)

_NOISE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "form", "nav", "footer", "aside")
_JS_MOUNT_ID = re.compile(r"^(root|app|__next|__nuxt|___gatsby)$", re.I)
_QUESTION_SENTENCE = re.compile(r"[^.?!\n]{12,160}\?")


# ── SSRF guard ────────────────────────────────────────────────────────────

def check_public_url(url: str) -> str | None:
    """None if `url` is safe to fetch, else a short human reason. Safe means:
    http(s), no embedded credentials, a normal web port, and a host that resolves
    ONLY to public (globally routable) addresses. Loopback, private, link-local
    (cloud metadata!), multicast and reserved ranges are all refused."""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return "not a valid URL"
    if parsed.scheme not in ("http", "https"):
        return "only http and https URLs can be fetched"
    if parsed.username or parsed.password:
        return "URLs with embedded credentials are not fetched"
    host = parsed.hostname
    if not host:
        return "URL has no host"
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return "URL has an invalid port"
    if port not in ALLOWED_PORTS:
        return f"port {port} is not fetched"
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return "host name could not be resolved"
    if not infos:
        return "host name could not be resolved"
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            return "host resolved to an unreadable address"
        # is_global alone is NOT enough: Python reports some multicast ranges
        # (e.g. 224.0.0.1) as global, so the special ranges are refused explicitly.
        if (not addr.is_global or addr.is_multicast or addr.is_reserved or addr.is_loopback
                or addr.is_link_local or addr.is_unspecified or addr.is_private):
            return "host resolves to a private or reserved address"
    return None


# ── HTTP fetch ────────────────────────────────────────────────────────────

class FetchError(Exception):
    """A fetch that did not produce an HTML body. `kind` is 'error' (could not
    fetch: blocked, missing, timeout, refused by the guard) or 'no_data'
    (fetched fine but it is not an HTML page)."""

    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def _http_get(url: str) -> tuple[str, str]:
    """(final_url, html). Raises FetchError. Redirects are followed by hand so
    every hop is re-checked against the SSRF guard."""
    current = url
    with httpx.Client(timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}, follow_redirects=False) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            reason = check_public_url(current)
            if reason:
                raise FetchError(f"refused: {reason}")
            try:
                with client.stream("GET", current) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise FetchError(f"HTTP {resp.status_code} redirect without a Location")
                        current = urljoin(current, location)
                        continue
                    if resp.status_code in (401, 402, 403, 407):
                        raise FetchError(f"blocked (HTTP {resp.status_code}: login, paywall or access control)")
                    if resp.status_code == 429:
                        raise FetchError("rate limited by the site (HTTP 429)")
                    if resp.status_code >= 400:
                        raise FetchError(f"HTTP {resp.status_code}")
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if ctype and "html" not in ctype:
                        raise FetchError(f"not an HTML page ({ctype.split(';')[0].strip()})", kind="no_data")
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise FetchError("page is larger than the 2 MB analysis limit")
                    encoding = resp.encoding or "utf-8"
                    return current, bytes(body).decode(encoding, errors="replace")
            except httpx.TimeoutException:
                raise FetchError("timed out") from None
            except httpx.HTTPError as exc:
                raise FetchError(f"request failed: {exc}") from None
    raise FetchError("too many redirects")


# ── extraction ────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return " ".join((text or "").split())


def extract_page(html: str) -> dict:
    """HTML -> {title, h1, headings, text, word_count, questions, js_shell,
    extraction_confidence}. Pure and side-effect free."""
    soup = BeautifulSoup(html or "", "lxml")
    js_mount = bool(soup.find(id=_JS_MOUNT_ID))
    noscript_says_js = any("javascript" in (n.get_text() or "").lower() for n in soup.find_all("noscript"))
    title = _clean(soup.title.get_text()) if soup.title else None

    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()

    root = soup.find("main") or soup.find("article") or soup.body or soup
    had_main = bool(soup.find("main") or soup.find("article"))

    h1s = [_clean(h.get_text(" ")) for h in root.find_all("h1") if _clean(h.get_text(" "))]
    headings = [
        {"tag": h.name, "text": _clean(h.get_text(" "))}
        for h in root.find_all(["h2", "h3"]) if _clean(h.get_text(" "))
    ]
    text = _clean(root.get_text(" "))
    words = len(text.split())

    questions = [h["text"] for h in headings if h["text"].endswith("?")]
    for m in _QUESTION_SENTENCE.finditer(text):
        q = _clean(m.group(0))
        if q not in questions:
            questions.append(q)
        if len(questions) >= 15:
            break

    js_shell = words < JS_SHELL_WORDS and (js_mount or noscript_says_js)
    if words >= 300 and len(headings) >= 2 and had_main:
        confidence = "high"
    elif words >= 150 and headings:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "title": title,
        "h1": h1s[0] if h1s else None,
        "headings": headings,
        "text": text[:MAX_TEXT_CHARS],
        "word_count": words,
        "questions": questions[:15],
        "js_shell": js_shell,
        "extraction_confidence": confidence,
    }


def _needs_browser(extracted: dict) -> bool:
    """A JS shell, or so little text that an un-rendered page is the likely reason."""
    return extracted["js_shell"] or extracted["word_count"] < MIN_USABLE_WORDS


# ── browser fallback (killable) ───────────────────────────────────────────

_BROWSER_SNIPPET = (
    "import json, sys\n"
    "from app.crawler import crawl_single_page\n"
    "print('@@JSON@@' + json.dumps(crawl_single_page(sys.argv[1]), default=str))\n"
)


def _browser_fetch(url: str) -> dict:
    """The existing one-off browser crawl, run in a SUBPROCESS with a hard timeout:
    this codebase already learned that a browser driven from a worker thread can
    hang forever, and a hung thread cannot be killed but a process can. Returns the
    crawler's own dict, or {"error": ...}. Never raises."""
    try:
        with _BROWSER_LOCK:
            proc = subprocess.run(
                [sys.executable, "-c", _BROWSER_SNIPPET, url],
                capture_output=True, text=True, timeout=BROWSER_TIMEOUT_SECONDS, cwd=_REPO_ROOT,
            )
    except subprocess.TimeoutExpired:
        return {"error": f"browser fetch timed out after {BROWSER_TIMEOUT_SECONDS}s"}
    except OSError as exc:
        return {"error": f"browser fetch could not start: {exc}"}
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("@@JSON@@"):
            try:
                return json.loads(line[len("@@JSON@@"):])
            except ValueError:
                break
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no output"]
    return {"error": f"browser fetch failed: {tail[0][:200]}"}


def _from_browser_result(result: dict) -> dict | None:
    """The crawler's dict -> the extract_page() shape, or None if it has no text."""
    if not isinstance(result, dict) or result.get("error"):
        return None
    text = _clean(result.get("fit_markdown") or result.get("custom_content") or result.get("markdown") or "")
    if not text:
        return None
    headings = [
        {"tag": h.get("tag"), "text": _clean(h.get("text"))}
        for h in (result.get("heading_structure") or [])
        if isinstance(h, dict) and h.get("tag") in ("h2", "h3") and _clean(h.get("text"))
    ]
    h1s = [h for h in (result.get("h1") or []) if h]
    words = len(text.split())
    return {
        "title": _clean(result.get("title")) or None,
        "h1": _clean(h1s[0]) if h1s else None,
        "headings": headings,
        "text": text[:MAX_TEXT_CHARS],
        "word_count": words,
        "questions": [h["text"] for h in headings if h["text"].endswith("?")][:15],
        "js_shell": False,
        "extraction_confidence": "medium" if words >= 150 and headings else "low",
    }


# ── public API ────────────────────────────────────────────────────────────

class BrowserBudget:
    """How many pages of ONE analysis may still be rendered in a browser."""

    def __init__(self, n: int = MAX_BROWSER_FALLBACKS_PER_ANALYSIS):
        self._left = n
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self._left <= 0:
                return False
            self._left -= 1
            return True


def _result(status: str, method: str, extracted: dict | None = None, error: str | None = None) -> dict:
    e = extracted or {}
    return {
        "status": status,
        "fetch_method": method,
        "title": e.get("title"),
        "h1": e.get("h1"),
        "headings": e.get("headings") or [],
        "text": e.get("text") or "",
        "word_count": e.get("word_count") or 0,
        "questions": e.get("questions") or [],
        "extraction_confidence": e.get("extraction_confidence") or "low",
        "error": error,
    }


def fetch_page_evidence(url: str, allow_browser_fallback: bool = True, budget: "BrowserBudget | None" = None) -> dict:
    """Fetch + extract ONE page. Never raises. See the module docstring for the
    status contract and the fetch order. `budget` (shared across one analysis's
    pages) caps how many pages may fall back to the browser."""
    try:
        final_url, html = _http_get(url)
    except FetchError as exc:
        return _result(exc.kind, "http", error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- a parsing/library bug must not take the analysis down
        return _result("error", "http", error=f"fetch failed: {exc}")

    try:
        extracted = extract_page(html)
    except Exception as exc:  # noqa: BLE001
        extracted = None
        extraction_error = f"could not parse the page: {exc}"
    else:
        extraction_error = None

    if extracted is not None and not _needs_browser(extracted):
        return _result("ok", "http", extracted)

    # A JS shell / an empty extraction is the ONLY reason to open a browser, and
    # only on the final, guard-checked URL.
    tried_browser = False
    if allow_browser_fallback and (budget is None or budget.take()):
        tried_browser = True
        rendered = _from_browser_result(_browser_fetch(final_url))
        if rendered and rendered["word_count"] >= MIN_USABLE_WORDS:
            return _result("ok", "browser", rendered)

    if extracted is not None and extracted["word_count"] >= MIN_USABLE_WORDS:
        # HTTP had usable text after all (fallback was off, or failed): keep it, low trust.
        return _result("ok", "http", {**extracted, "extraction_confidence": "low"})
    reason = extraction_error or "the page had no extractable text (empty, or a JavaScript-only page that could not be rendered)"
    return _result("no_data", "browser" if tried_browser else "http", extracted, error=reason)


def fetch_pages_evidence(urls: list[str], max_workers: int = 5, allow_browser_fallback: bool = True) -> dict[str, dict]:
    """{url: result} for several pages, fetched concurrently (HTTP is I/O bound).
    Order-independent; every url gets an entry."""
    if not urls:
        return {}
    budget = BrowserBudget()
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(urls)))) as pool:
        results = list(pool.map(lambda u: fetch_page_evidence(u, allow_browser_fallback, budget), urls))
    return dict(zip(urls, results))
