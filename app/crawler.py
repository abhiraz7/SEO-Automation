import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "VTechSEO-Crawler/1.0"
HEADERS = {"User-Agent": USER_AGENT}
REQUEST_TIMEOUT = 15
SITEMAP_TIMEOUT = 15
MAX_SITEMAP_DEPTH = 4
FETCH_WORKERS = 8

DOMAIN_SCHEMA_TYPES = {"organization", "website", "localbusiness"}

ASSET_EXTENSION_RE = re.compile(
    r"\.(jpg|jpeg|png|gif|svg|css|js|pdf|zip|ico|mp4|webp|woff|woff2|ttf)$", re.I
)
SKIP_HREF_PREFIXES = ("#", "mailto:", "tel:", "javascript:")
COMMON_SITEMAP_PATHS = [
    "sitemap.xml",
    "sitemap_index.xml",
    "sitemap-index.xml",
    "wp-sitemap.xml",
    "sitemap1.xml",
    "post-sitemap.xml",
    "page-sitemap.xml",
]


class CrawlError(Exception):
    pass


def _fetch(url, timeout=REQUEST_TIMEOUT):
    try:
        return httpx.get(url, timeout=timeout, follow_redirects=True, headers=HEADERS)
    except httpx.RequestError as exc:
        raise CrawlError(str(exc)) from exc


def _fetch_many(urls, timeout=REQUEST_TIMEOUT, max_workers=FETCH_WORKERS):
    """Fetch URLs concurrently. Returns {url: (response_or_None, error_or_None)}."""
    results = {}
    if not urls:
        return results
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch, url, timeout): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                results[url] = (future.result(), None)
            except CrawlError as exc:
                results[url] = (None, str(exc))
    return results


def _meta_content(soup, **attrs):
    tag = soup.find("meta", attrs=attrs)
    return tag.get("content", "").strip() or None if tag else None


def _extract_jsonld(soup):
    domain_schema = []
    page_schemas = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            schema_type = str(item.get("@type", "")).lower()
            (domain_schema if schema_type in DOMAIN_SCHEMA_TYPES else page_schemas).append(item)
    return domain_schema, page_schemas


def extract_page_data(url, html, status_code):
    soup = BeautifulSoup(html, "lxml")

    title = soup.title.get_text(strip=True) if soup.title else None

    canonical_tag = soup.find("link", rel="canonical")
    html_tag = soup.find("html")
    main_content = soup.find("main") or soup.find("article") or soup.body

    domain_schema, page_schemas = _extract_jsonld(soup)

    return {
        "url": url,
        "status_code": status_code,
        "error": None,
        "title": title,
        "meta_description": _meta_content(soup, name="description"),
        "meta_keywords": _meta_content(soup, name="keywords"),
        "h1": [h.get_text(strip=True) for h in soup.find_all("h1")],
        "h2": [h.get_text(strip=True) for h in soup.find_all("h2")],
        "heading_structure": [
            {"tag": h.name, "text": h.get_text(strip=True)}
            for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
        ],
        "image_alts": [
            {"src": img.get("src"), "alt": img.get("alt")} for img in soup.find_all("img")
        ],
        "domain_schema": domain_schema,
        "page_schemas": page_schemas,
        "canonical": canonical_tag.get("href") if canonical_tag else None,
        "og_title": _meta_content(soup, property="og:title"),
        "og_description": _meta_content(soup, property="og:description"),
        "og_url": _meta_content(soup, property="og:url"),
        "twitter_title": _meta_content(soup, name="twitter:title"),
        "twitter_description": _meta_content(soup, name="twitter:description"),
        "twitter_site": _meta_content(soup, name="twitter:site"),
        "twitter_card": _meta_content(soup, name="twitter:card"),
        "lang": html_tag.get("lang") if html_tag else None,
        "custom_content": main_content.get_text(separator=" ", strip=True) if main_content else None,
    }


def _error_result(url, message, status_code=None):
    return {"url": url, "error": message, "status_code": status_code}


def _result_for(url, resp, error):
    if error:
        return _error_result(url, error)
    if resp.status_code >= 400:
        return _error_result(str(resp.url), f"HTTP {resp.status_code}", resp.status_code)
    return extract_page_data(str(resp.url), resp.text, resp.status_code)


def crawl_single_page(url):
    try:
        resp = _fetch(url)
    except CrawlError as exc:
        return _error_result(url, str(exc))

    if resp.status_code >= 400:
        return _error_result(str(resp.url), f"HTTP {resp.status_code}", resp.status_code)

    return extract_page_data(str(resp.url), resp.text, resp.status_code)


def _clean_link(base_url, href, domain):
    abs_url = urljoin(base_url, href)
    clean_url, _ = urldefrag(abs_url)
    clean_url = clean_url.split("?", 1)[0].rstrip("/")

    parsed = urlparse(clean_url)
    if parsed.scheme not in ("http", "https") or parsed.netloc != domain:
        return None
    if ASSET_EXTENSION_RE.search(parsed.path):
        return None
    return clean_url


def discover_links(base_url, html):
    soup = BeautifulSoup(html, "lxml")
    domain = urlparse(base_url).netloc
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(SKIP_HREF_PREFIXES):
            continue
        link = _clean_link(base_url, href, domain)
        if link:
            links.add(link)
    return links


# --- Sitemap discovery -------------------------------------------------


def _extract_sitemap_locs(xml_text):
    try:
        soup = BeautifulSoup(xml_text, "xml")
    except Exception:
        return []
    return [loc.get_text(strip=True) for loc in soup.find_all("loc")]


def _fetch_sitemap_urls(sitemap_url, visited=None, depth=0):
    if visited is None:
        visited = set()
    if depth > MAX_SITEMAP_DEPTH or sitemap_url in visited:
        return []
    visited.add(sitemap_url)

    try:
        resp = _fetch(sitemap_url, timeout=SITEMAP_TIMEOUT)
    except CrawlError:
        return []
    if resp.status_code != 200 or not resp.text:
        return []

    xml_text = resp.text
    locs = _extract_sitemap_locs(xml_text)
    if "<sitemapindex" in xml_text:
        urls = []
        for child in locs:
            urls.extend(_fetch_sitemap_urls(child, visited, depth + 1))
        return urls
    if "<urlset" in xml_text or "<url>" in xml_text:
        return locs
    return []


def _find_sitemap_candidates(base_origin):
    candidates = []
    try:
        resp = _fetch(f"{base_origin}/robots.txt", timeout=SITEMAP_TIMEOUT)
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                match = re.match(r"^Sitemap:\s*(.+)$", line, flags=re.I)
                if match:
                    candidates.append(match.group(1).strip())
    except CrawlError:
        pass

    candidates += [f"{base_origin}/{path}" for path in COMMON_SITEMAP_PATHS]
    return list(dict.fromkeys(candidates))


def discover_via_sitemap(base_origin, max_pages):
    """Return up to max_pages same-domain URLs from the first sitemap that yields any."""
    for sitemap_url in _find_sitemap_candidates(base_origin):
        urls = []
        seen = set()
        for url in _fetch_sitemap_urls(sitemap_url):
            clean = url.split("?", 1)[0].rstrip("/")
            parsed = urlparse(clean)
            if f"{parsed.scheme}://{parsed.netloc}" != base_origin or clean in seen:
                continue
            seen.add(clean)
            urls.append(clean)
            if len(urls) >= max_pages:
                break
        if urls:
            return urls
    return []


# --- Site crawl ----------------------------------------------------------


def _crawl_known_urls(urls):
    fetched = _fetch_many(urls)
    return [_result_for(url, resp, error) for url, (resp, error) in fetched.items()]


def _crawl_via_links(start_url, max_pages, batch_size=FETCH_WORKERS):
    visited = set()
    queue = [start_url]
    results = []

    while queue and len(visited) < max_pages:
        batch = []
        while queue and len(batch) < batch_size and len(visited) < max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            batch.append(url)

        fetched = _fetch_many(batch)
        for url, (resp, error) in fetched.items():
            results.append(_result_for(url, resp, error))
            if resp is not None and resp.status_code < 400:
                for link in discover_links(url, resp.text):
                    if link not in visited and link not in queue and len(visited) + len(queue) < max_pages:
                        queue.append(link)

    return results


def crawl_site(base_url, max_pages=25):
    parsed = urlparse(base_url)
    base_origin = f"{parsed.scheme}://{parsed.netloc}"
    base_url_clean = base_url.rstrip("/")

    sitemap_urls = discover_via_sitemap(base_origin, max_pages)
    if sitemap_urls:
        urls = [base_url_clean] + [u for u in sitemap_urls if u != base_url_clean]
        return _crawl_known_urls(urls[:max_pages])

    return _crawl_via_links(base_url_clean, max_pages)
