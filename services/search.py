import re
import asyncio
import base64
import httpx
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Tag
import copy as _copy

# Global semaphore — caps total simultaneous HTTP requests across all scrapes
_HTTP_SEM = asyncio.Semaphore(25)

LEGAL_SUFFIXES = r"\b(inc|llc|ltd|corp|co|gmbh|s\.a\.|plc|ag|bv|nv|oy|ab|as|a/s|pty|pvt)\b\.?"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

MAX_CANDIDATES = 8


ALT_TLDS = (".com", ".tv", ".io", ".ai", ".co", ".org", ".net", ".tech", ".app")

PARKED_INDICATORS = (
    "hugedomains", "godaddy", "sedo.com", "parkingcrew", "bodis.com",
    "afternic", "dan.com", "undeveloped", "domainmarket", "buy this domain",
    "domain is for sale", "this domain", "domain name for sale",
    "parked free", "registrar-servers",
)


def _clean_brand(company: str) -> str:
    """Return normalised brand slug: 'SambaTV Inc.' → 'sambatv'."""
    name = company.lower().strip()
    name = re.sub(LEGAL_SUFFIXES, "", name, flags=re.IGNORECASE)
    name = name.replace("&", "").replace("'", "").replace(".", "")
    name = re.sub(r"\s+", "", name.strip())
    return re.sub(r"[^a-z0-9\-]", "", name)


def _guess_domain(company: str) -> str:
    brand = _clean_brand(company)
    # If company name ends with "tv", try .tv first (SambaTV → samba.tv)
    if brand.endswith("tv") and len(brand) > 2:
        return f"{brand[:-2]}.tv"
    return f"{brand}.com"


def _to_slug(company: str) -> str:
    s = re.sub(r"\s+", "_", company.lower().strip())
    return re.sub(r"[^a-z0-9_]", "", s) or "company"


def _normalize(url: str | None, base: str) -> str | None:
    if not url:
        return None
    url = url.strip()
    if url.startswith("data:"):
        return url
    if url.startswith("//"):
        url = "https:" + url
    try:
        return urljoin(base, url)
    except Exception:
        return None


def _has_logo_word(text: str) -> bool:
    """True if 'logo' or 'brand' appears as a distinct word (not inside 'catalog' etc)."""
    return bool(re.search(r'\b(logo|brand|logotype|wordmark)\b', (text or "").lower()))


def _get_img_src(img: Tag) -> str | None:
    return img.get("src") or img.get("data-src") or img.get("data-lazy-src")


def _svg_has_drawing(svg_tag: Tag) -> bool:
    drawing = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line", "text", "image"}
    for child in svg_tag.descendants:
        if isinstance(child, Tag) and child.name in drawing:
            return True
    return False


def _svg_use_href(svg_tag: Tag) -> str | None:
    use = svg_tag.find("use")
    if not use:
        return None
    return use.get("href") or use.get("xlink:href") or None


def _inline_svg_to_data_url(svg_tag: Tag, soup: BeautifulSoup) -> str | None:
    try:
        href = _svg_use_href(svg_tag)

        if href and not _svg_has_drawing(svg_tag):
            if href.startswith("#"):
                symbol = soup.find(id=href[1:])
                if not symbol:
                    return None
                new_svg = _copy.copy(svg_tag)
                new_svg.clear()
                for attr in ("viewBox", "width", "height"):
                    val = symbol.get(attr)
                    if val:
                        new_svg[attr] = val
                for child in symbol.children:
                    new_svg.append(_copy.copy(child))
                svg_str = str(new_svg)
            else:
                return None
        else:
            svg_str = str(svg_tag)

        if 'xmlns=' not in svg_str:
            svg_str = svg_str.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)

        if len(svg_str) > 150_000 or len(svg_str) < 20:
            return None

        b64 = base64.b64encode(svg_str.encode()).decode()
        return f"data:image/svg+xml;base64,{b64}"
    except Exception:
        return None


def _extract_image_from_tag(tag: Tag, soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    """Extract (url, label) pairs from a tag that might contain an img or inline SVG."""
    results = []

    # Direct <img>
    if tag.name == "img":
        src = _get_img_src(tag)
        if src:
            label = tag.get("alt") or tag.get("title") or "Logo"
            results.append((_normalize(src, base_url), label))
        return results

    # Direct <svg>
    if tag.name == "svg":
        data_url = _inline_svg_to_data_url(tag, soup)
        if data_url:
            label = tag.get("aria-label") or tag.get("title") or "SVG logo"
            results.append((data_url, str(label)))
        else:
            # Maybe external sprite
            href = _svg_use_href(tag)
            if href and not href.startswith("#"):
                sprite = href.split("#")[0]
                url = _normalize(sprite, base_url)
                if url:
                    results.append((url, "SVG sprite"))
        return results

    # Children: find <img> and <svg> inside
    for img in tag.find_all("img", limit=3):
        src = _get_img_src(img)
        if src:
            label = img.get("alt") or img.get("title") or "Logo"
            results.append((_normalize(src, base_url), label))

    for svg in tag.find_all("svg", limit=2):
        data_url = _inline_svg_to_data_url(svg, soup)
        if data_url:
            label = svg.get("aria-label") or svg.get("title") or "SVG logo"
            results.append((data_url, str(label)))
        else:
            href = _svg_use_href(svg)
            if href and not href.startswith("#"):
                sprite = href.split("#")[0]
                url = _normalize(sprite, base_url)
                if url:
                    results.append((url, "SVG sprite"))

    return results


def _is_parked(html: str) -> bool:
    """Detect domain-parking / 'for sale' pages."""
    snippet = html[:5000].lower()
    return any(ind in snippet for ind in PARKED_INDICATORS)


def _redirect_ok(original_url: str, final_url: str, brand: str) -> bool:
    """Return False if the redirect landed on a completely unrelated domain."""
    orig_host = urlparse(original_url).hostname or ""
    final_host = urlparse(final_url).hostname or ""
    # Same domain (with/without www) is fine
    if orig_host.removeprefix("www.") == final_host.removeprefix("www."):
        return True
    # Final host still contains the brand name → probably fine (e.g. mntn → mntn.com)
    if brand and brand in final_host.replace(".", "").replace("-", ""):
        return True
    return False


async def _try_fetch(url: str, client: httpx.AsyncClient,
                     brand: str = "") -> tuple[str | None, str | None]:
    async with _HTTP_SEM:
        try:
            r = await client.get(url, timeout=6.0, follow_redirects=True, headers=HEADERS)
            ct = r.headers.get("content-type", "")
            if "text/html" not in ct:
                return None, None
            if r.status_code not in (200, 403):
                return None, None
            if r.status_code == 403 and len(r.text) < 2000:
                return None, None
            # Reject if redirected to unrelated domain
            if not _redirect_ok(url, str(r.url), brand):
                return None, None
            # Reject parked pages
            if _is_parked(r.text):
                return None, None
            # Reject near-empty pages (bot-detection shells, placeholders)
            if len(r.text) < 500:
                return None, None
            return r.text, str(r.url)
        except Exception:
            pass
    return None, None


async def _fetch_html(domain: str, client: httpx.AsyncClient,
                      brand: str = "") -> tuple[str | None, str | None]:
    base = domain.removeprefix("www.")
    brand = brand or (base.split(".")[0] if "." in base else base)
    name_no_tld = base.split(".")[0] if "." in base else base

    # ── Stage 1: try primary .com (fast path — works for ~80% of companies) ──
    primary = [f"https://www.{base}", f"https://{base}"]
    tasks = [_try_fetch(u, client, brand) for u in primary]
    results = await asyncio.gather(*tasks)
    for html, final_url in results:
        if html:
            return html, final_url

    # ── Stage 2: try all alternative domains in parallel ─────────────────────
    seen: set[str] = {urlparse(u).hostname or u for u in primary}
    alt_urls: list[str] = []

    def _add(u: str) -> None:
        key = urlparse(u).hostname or u
        if key not in seen:
            seen.add(key)
            alt_urls.append(u)

    # Name variants (strip tld-like suffix: "sambatv" → "samba")
    name_variants = [name_no_tld]
    for suffix in ("tv", "io", "ai", "app", "tech", "hq", "dev", "co"):
        if name_no_tld.endswith(suffix) and len(name_no_tld) > len(suffix) + 1:
            name_variants.append(name_no_tld[:-len(suffix)])

    for name_var in name_variants:
        for tld in ALT_TLDS:
            _add(f"https://{name_var}{tld}")

    _add(f"https://global.{name_no_tld}")
    if name_no_tld != base:
        _add(f"https://global.{base}")

    if alt_urls:
        tasks = [_try_fetch(u, client, brand) for u in alt_urls]
        results = await asyncio.gather(*tasks)
        for html, final_url in results:
            if html:
                return html, final_url

    return None, None


def _is_domain(text: str) -> bool:
    """Return True if text looks like a ready-made domain (e.g. 'tcl.com', 'www.apple.com')."""
    t = text.lower().strip().removeprefix("https://").removeprefix("http://").split("/")[0]
    return bool(re.match(r'^[\w\-]+(\.[\w\-]+)+$', t))


async def scrape_logos(company: str, client: httpx.AsyncClient,
                       domain: str | None = None) -> tuple[str, list[dict]]:
    """Scrape logos for a company. `domain` is pre-resolved (e.g. by Claude)."""
    slug = _to_slug(company)

    # Resolve domain
    if domain:
        # Already resolved (e.g. by Claude)
        domain = (domain.lower()
                  .removeprefix("https://").removeprefix("http://")
                  .removeprefix("www.").rstrip("/").split("/")[0])
        brand = domain.split(".")[0]
    elif _is_domain(company):
        raw = company.lower().strip().removeprefix("https://").removeprefix("http://").split("/")[0]
        domain = raw.removeprefix("www.")
        brand = domain.split(".")[0]
    else:
        domain = _guess_domain(company)
        brand = _clean_brand(company)

    seen: set[str] = set()
    candidates: list[dict] = []

    def add(url: str | None, label: str, priority: int) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        candidates.append({"url": url, "label": label[:60], "priority": priority})

    # ── Always-available fallbacks (work even if site is unreachable) ────
    add(f"https://www.google.com/s2/favicons?domain={domain}&sz=128",
        "Google Favicon (128px)", 10)
    add(f"https://icons.duckduckgo.com/ip3/{domain}.ico",
        "DuckDuckGo Icon", 5)

    html, base_url = await _fetch_html(domain, client, brand)
    if not html:
        # Sort and return just the fallbacks
        candidates.sort(key=lambda c: c["priority"], reverse=True)
        result = [
            {"id": f"{slug}_{i}", "url": c["url"], "source": "fallback", "label": c["label"]}
            for i, c in enumerate(candidates)
        ]
        return domain, result

    soup = BeautifulSoup(html, "html.parser")

    # ── Strategy 1: First <a> in <header> or <nav> (highest priority) ───
    for container_tag in ("header", "nav"):
        container = soup.find(container_tag)
        if not container:
            continue
        first_link = container.find("a")
        if first_link:
            for url, label in _extract_image_from_tag(first_link, soup, base_url):
                add(url, label, 110)
        # First direct <img> or <svg> child
        for child in container.children:
            if isinstance(child, Tag) and child.name in ("img", "svg"):
                for url, label in _extract_image_from_tag(child, soup, base_url):
                    add(url, label, 105)
                break

    # ── Strategy 2: Elements with "logo" or "brand" in class/id ────────
    SKIP_WORDS = {"partner", "client", "customer", "sponsor", "trust",
                  "testimonial", "marquee", "ticker", "scrolling", "carousel",
                  "slider", "logo-section", "logos-section", "logo-wall",
                  "logo-grid", "logo-bar", "logo-strip", "logo-row"}

    logo_els = soup.select(
        '[class*="logo"], [class*="brand"], '
        '[id*="logo"], [id*="brand"]'
    )
    for el in logo_els:
        cls = " ".join(el.get("class", [])).lower()
        iid = (el.get("id") or "").lower()
        tag_text = cls + " " + iid
        if any(skip in tag_text for skip in SKIP_WORDS):
            continue
        # Also check ancestors (up to 4 levels) for client-logo sections
        skip_ancestor = False
        parent = el.parent
        for _ in range(10):
            if parent is None or not isinstance(parent, Tag):
                break
            p_cls = " ".join(parent.get("class", [])).lower()
            p_id = (parent.get("id") or "").lower()
            ancestor_text = p_cls + " " + p_id
            if any(skip in ancestor_text for skip in SKIP_WORDS):
                skip_ancestor = True
                break
            parent = parent.parent
        if skip_ancestor:
            continue
        for url, label in _extract_image_from_tag(el, soup, base_url):
            add(url, label, 100)

    # ── Strategy 3: og:image / twitter:image (high priority — always relevant) ─
    for prop in ["og:image", "og:logo", "twitter:image"]:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            url = _normalize(tag["content"], base_url)
            add(url, f"Meta ({prop})", 95)

    # ── Strategy 4: Favicons (reliable fallback) ─────────────────────────
    for link_tag in soup.find_all("link", rel=True):
        rels = link_tag.get("rel", [])
        if isinstance(rels, str):
            rels = [rels]
        rels_str = " ".join(rels).lower()
        if "apple-touch-icon" in rels_str:
            url = _normalize(link_tag.get("href"), base_url)
            add(url, "Apple Touch Icon", 85)
        elif "icon" in rels_str:
            href = link_tag.get("href", "")
            # Prefer SVG/PNG favicons, deprioritize .ico
            prio = 80 if (".svg" in href.lower() or ".png" in href.lower()) else 50
            url = _normalize(href, base_url)
            add(url, "Favicon", prio)

    # ── Strategy 5: Any <img> with "logo" in src/alt/class/id ───────────
    for img in soup.find_all("img", limit=200):
        src = _get_img_src(img) or ""
        alt = img.get("alt") or ""
        cls = " ".join(img.get("class", []))
        iid = img.get("id") or ""
        combined = f"{src} {alt} {cls} {iid}"
        if _has_logo_word(combined) and src:
            url = _normalize(src, base_url)
            add(url, alt or iid or "Logo", 70)

    # ── Strategy 6: Any URL (href/src) with "logo" in path ──────────────
    for tag in soup.find_all(True, limit=500):
        for attr in ("href", "src", "data-src", "data", "content"):
            val = tag.get(attr) or ""
            if _has_logo_word(val) and re.search(r'\.(svg|png|jpg|jpeg|webp)(\?|$)', val, re.I):
                url = _normalize(val, base_url)
                add(url, "Logo file", 65)

    # Sort by priority desc, keep top N
    candidates.sort(key=lambda c: c["priority"], reverse=True)
    candidates = candidates[:MAX_CANDIDATES]

    result = [
        {"id": f"{slug}_{i}", "url": c["url"], "source": "website", "label": c["label"]}
        for i, c in enumerate(candidates)
    ]
    return domain, result


async def generate_all(companies: list[str], context: str = "") -> list[dict]:
    from services.domain_resolver import resolve_domains

    valid = [c.strip() for c in companies if c.strip()]

    # Step 1: resolve all domains via Claude (one batch call)
    domain_map = await resolve_domains(valid, context=context)

    # Step 2: scrape all sites (max 8 concurrent, shared HTTP semaphore caps total requests)
    scrape_sem = asyncio.Semaphore(8)
    limits = httpx.Limits(max_connections=60, max_keepalive_connections=15)

    async def _scrape(company: str, client: httpx.AsyncClient):
        async with scrape_sem:
            return await scrape_logos(company, client, domain=domain_map.get(company))

    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [_scrape(c, client) for c in valid]
        results = await asyncio.gather(*tasks)

    return [
        {"company": c, "domain_guess": domain, "candidates": candidates}
        for c, (domain, candidates) in zip(valid, results)
    ]
