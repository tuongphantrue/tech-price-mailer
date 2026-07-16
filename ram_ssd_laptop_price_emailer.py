#!/usr/bin/env python3
"""
RAM / SSD / Laptop Prices (MemoryZone.vn + HACOM.vn + PhongVu.vn) -> Email
(runs on GitHub Actions, no local computer needed)

Same shape as the gold-price-emailer / house-price-emailer this is modeled
on: fetches price data, then emails an HTML digest via Gmail SMTP. Runs in
two phases so the workflow can persist dedup state *between* them (see the
accompanying GitHub Actions workflow):

    python ram_ssd_laptop_price_emailer.py generate
        -> scrapes each retailer's three category pages, writes the
           composed email (subject/html/text) under ./email/, and updates
           the "last sent price" state file

    python ram_ssd_laptop_price_emailer.py send
        -> reads ./email/* and sends it via Gmail SMTP

SOURCES & AN IMPORTANT CAVEAT
-----------------------------
Vietnamese gold prices have a clean daily aggregator (giavang.org) with one
simple table per seller. RAM/SSD/laptop prices don't have a real
equivalent: there's no single site that publishes a clean, structured,
frequently-updated "market price" table the way giavang.org does for gold.

What this script does instead is scrape the live *listing prices* off
several retailers' category pages, currently:

    - MemoryZone.vn  (memoryzone.com.vn)
    - HACOM.vn       (hacom.vn)          - Hanoi-headquartered, showrooms
                                            across Hanoi (Hai Ba Trung,
                                            Dong Da, Cau Giay, Ha Dong, ...)
    - Phong Vu       (phongvu.vn)        - nationwide chain, Hanoi showrooms

These are each store's own asking prices (often with an active discount),
NOT a market-average and NOT a single unified price-comparison - the email
just lays each retailer's listings out side by side so you can eyeball
them. Treat this as "what each store is currently charging for the items
on page 1 of each category", not as an authoritative RAM/SSD/laptop market
index.

If you want a true cross-retailer comparison, a price-comparison site
(e.g. websosanh.vn) would be a better source, but those tend to load
listings via JavaScript rather than server-rendering them, which a plain
HTTP scraper can't read. If you find a clean scrapable source for that,
parse_listing() below is where to wire it in.

Because retail listings can reshuffle/reprice frequently, consider
SEND_ONLY_ON_CHANGE=true (see below) if you'd rather only get an email
when the scraped set of prices actually changes.

SETUP
-----
1. Install dependencies:

    pip install requests beautifulsoup4 certifi playwright
    python -m playwright install --with-deps chromium

   (The playwright browser install is a one-time step - HACOM and Phong Vu
   load their product grids via client-side JS, so those two categories
   are rendered in a real headless Chromium tab rather than fetched with
   a plain HTTP GET. MemoryZone doesn't need this - its pages are
   server-rendered - but installing it doesn't hurt.)

2. Create a Gmail "App Password" (regular Gmail passwords won't work with SMTP):
    - Go to https://myaccount.google.com/apppasswords
    - You need 2-Step Verification turned on first.
    - Create an app password for "Mail" and copy the 16-character code.

3. Set these as environment variables (see README.md for GitHub Actions
   secrets instead, if running in the cloud):

    export GMAIL_ADDRESS="youraddress@gmail.com"
    export GMAIL_APP_PASSWORD="16-char-app-password"
    export TECH_RECIPIENT="where-to-send@example.com"
    export SEND_ONLY_ON_CHANGE="false"          # optional, default false
    export TIMEZONE="Asia/Ho_Chi_Minh"          # optional, for the subject line
    export ENABLED_RETAILERS="memoryzone,hacom,phongvu"   # optional, default all three
    export MAX_ITEMS_PER_CATEGORY="12"          # optional
    export STATE_FILE="state/last_price.json"   # optional, dedup state file
    export ALLOW_INSECURE_SSL_FALLBACK="false"  # optional, last-resort TLS bypass

    # Optional per-retailer category URL overrides (defaults shown):
    export MEMORYZONE_RAM_URL="https://memoryzone.com.vn/ram-laptop"
    export MEMORYZONE_SSD_URL="https://memoryzone.com.vn/ssd"
    export MEMORYZONE_LAPTOP_URL="https://memoryzone.com.vn/laptop"
    export HACOM_RAM_URL="https://hacom.vn/ram-laptop"
    export HACOM_SSD_URL="https://hacom.vn/o-cung-ssd"
    export HACOM_LAPTOP_URL="https://hacom.vn/laptop"
    export PHONGVU_RAM_URL="https://phongvu.vn/c/ram-laptop"
    export PHONGVU_SSD_URL="https://phongvu.vn/c/o-cung-ssd"
    export PHONGVU_LAPTOP_URL="https://phongvu.vn/c/laptop"

    # Backward-compatible aliases (apply to MemoryZone only, kept so
    # existing workflows that already set these don't break):
    export RAM_URL / SSD_URL / LAPTOP_URL

NOTE ON SCRAPING
-----------------
Always worth checking each site's current robots.txt / terms before
running this unattended long-term, e.g.:
    https://memoryzone.com.vn/robots.txt
    https://hacom.vn/robots.txt
    https://phongvu.vn/robots.txt

The page markup can change at any time - if `generate` reports 0 parsed
items for a category, open that category's URL and inspect the product
cards, then update parse_listing() below. It matches by *text adjacency*
(product name line immediately followed by a "X.XXX.XXX ₫" price line),
not by exact HTML structure, which should make it reasonably resilient
across different storefront templates - but no guarantees, and it can't
distinguish "in stock" from "sold out" items.

MemoryZone server-renders its product grid, so a plain HTTP GET is
enough. HACOM does not - its product grid is fetched by client-side JS
after the page loads, so this script renders that page in a real headless
Chromium tab (via Playwright) first and parses the resulting HTML.
Phong Vu is attempted the same way, but that site also runs active bot
detection that may block even a real headless browser - if so, it will
show up as a fetch/render error in the logs for that category, not a
silent 0-items result. Because this now spans three sites with two
different fetch strategies, it's worth doing a first manual `generate`
run and checking the parsed item counts for each retailer/category before
relying on the schedule.

This is a personal price-watch tool, not investment or purchase advice -
always confirm the actual price on the retailer's site before buying.
"""

import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
import unicodedata
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

import certifi
import requests
import urllib3
from bs4 import BeautifulSoup

if os.environ.get("ALLOW_INSECURE_SSL_FALLBACK", "false").lower() == "true":
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Retailers & categories
# ---------------------------------------------------------------------------
# Each retailer has the same three category keys (ram/ssd/laptop) so the
# spec extractors below can be shared across sites. URLs are individually
# overridable via env vars, e.g. HACOM_SSD_URL.

RETAILER_DEFAULTS = {
    "memoryzone": {
        "label": "MemoryZone",
        # MemoryZone server-renders its product grid, so a plain HTTP GET
        # already contains the prices - no browser needed.
        "needs_browser": False,
        "categories": {
            "ram": ("RAM Laptop", "https://memoryzone.com.vn/ram-laptop"),
            "ssd": ("SSD", "https://memoryzone.com.vn/ssd"),
            "laptop": ("Laptop", "https://memoryzone.com.vn/laptop"),
        },
    },
    "hacom": {
        "label": "HACOM (Hà Nội)",
        # HACOM runs on Next.js: the initial HTML is just page chrome
        # (filters/nav/FAQ), and the product grid itself is fetched by
        # client-side JS after the page loads. A plain requests.get() sees
        # 0 products every time - this needs a real (headless) browser to
        # execute that JS before the price data exists in the DOM.
        "needs_browser": True,
        "categories": {
            "ram": ("RAM Laptop", "https://hacom.vn/ram-laptop"),
            "ssd": ("SSD", "https://hacom.vn/o-cung-ssd"),
            "laptop": ("Laptop", "https://hacom.vn/laptop"),
        },
    },
    "phongvu": {
        "label": "Phong Vũ",
        # Phong Vu actively blocks non-browser requests (bot detection
        # fires on a plain GET). A real headless browser may or may not
        # get through depending on how strict their check is on the day -
        # this is attempted the same way as HACOM, but is more likely to
        # fail; see fetch_category()'s error handling.
        "needs_browser": True,
        "categories": {
            "ram": ("RAM Laptop", "https://phongvu.vn/c/ram-laptop"),
            "ssd": ("SSD", "https://phongvu.vn/c/o-cung-ssd"),
            "laptop": ("Laptop", "https://phongvu.vn/c/laptop"),
        },
    },
}

# Backward-compatible env var aliases - only apply to MemoryZone, since
# that's what the original single-retailer script used.
_LEGACY_URL_ENV = {
    "ram": "RAM_URL",
    "ssd": "SSD_URL",
    "laptop": "LAPTOP_URL",
}


def _retailer_url_env(site_key, cat_key):
    """Env var name for a given retailer/category URL override,
    e.g. HACOM_SSD_URL."""
    return f"{site_key.upper()}_{cat_key.upper()}_URL"


def build_categories():
    enabled = os.environ.get("ENABLED_RETAILERS", "memoryzone,hacom,phongvu")
    enabled_sites = [s.strip().lower() for s in enabled.split(",") if s.strip()]

    categories = []
    for site_key in enabled_sites:
        defaults = RETAILER_DEFAULTS.get(site_key)
        if not defaults:
            print(f" Unknown retailer '{site_key}' in ENABLED_RETAILERS - skipping.", file=sys.stderr)
            continue
        for cat_key, (cat_label, default_url) in defaults["categories"].items():
            env_name = _retailer_url_env(site_key, cat_key)
            url = os.environ.get(env_name)
            if url is None and site_key == "memoryzone":
                url = os.environ.get(_LEGACY_URL_ENV[cat_key])
            if url is None:
                url = default_url
            categories.append(
                {
                    "site": site_key,
                    "site_label": defaults["label"],
                    "key": cat_key,
                    "label": cat_label,
                    "url": url,
                    "needs_browser": defaults.get("needs_browser", False),
                }
            )
    return categories


CATEGORIES = build_categories()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

EMAIL_DIR = "email"
STATE_FILE = os.environ.get("STATE_FILE", "state/last_price.json")
SEND_ONLY_ON_CHANGE = os.environ.get("SEND_ONLY_ON_CHANGE", "false").lower() == "true"
ALLOW_INSECURE_SSL_FALLBACK = os.environ.get("ALLOW_INSECURE_SSL_FALLBACK", "false").lower() == "true"
MAX_ITEMS_PER_CATEGORY = int(os.environ.get("MAX_ITEMS_PER_CATEGORY", "12"))
# How long to let a headless-browser page (HACOM/Phong Vu) finish loading +
# running its client-side product-fetch JS before giving up on that
# category. These pages are slower than a plain HTTP GET, so this is
# generous by design.
BROWSER_TIMEOUT_MS = int(os.environ.get("BROWSER_TIMEOUT_MS", "45000"))

# Matches Vietnamese-formatted currency like "1.990.000 ₫" (dot as thousands
# separator, ₫ as the currency mark). A listing/discount line often has two
# of these back to back: "1.990.000 ₫ 2.350.000 ₫" (sale price, then the
# crossed-out original price).
PRICE_RE = re.compile(r"([\d]{1,3}(?:\.[\d]{3})+)\s*\u20ab")

# Lines that are clearly chrome/navigation/filters, not product names -
# skip these even if a price happens to follow within lookahead range.
# Collected across MemoryZone/HACOM/Phong Vu category pages.
JUNK_NAME_PREFIXES = (
    "trang chủ", "giỏ hàng", "tài khoản", "đăng nhập", "so sánh",
    "sắp xếp", "thứ tự", "lọc giá", "bỏ hết", "xem thêm", "hãng sản xuất",
    "khoảng giá", "thương hiệu", "nhu cầu", "dung lượng", "thế hệ",
    "chuẩn", "bus ram", "danh mục", "chính sách", "hướng dẫn", "tra cứu",
)

# Review/stock/discount-badge lines that sit between one product's price
# and the next product's name - must be excluded from candidacy or the
# next price gets paired with a review sentence instead of a product name.
JUNK_NAME_RE = re.compile(
    r"^(là người đánh giá đầu tiên|xem \d+ đánh giá|-?\d+\s*%|hết hàng|"
    r"chỉ bán build pc|còn hàng|hữu ích\s*\(\d+\)|tặng |quà tặng|khuyến mại)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Spec extraction: product titles pack the spec sheet into the name itself
# (e.g. "RAM Laptop Kingston DDR4 16GB 3200MHz 1.2v KVR32...",
# "Laptop Asus Vivobook X1504VA (i5-1334U/8GB/512GB SSD/15.6 FHD/Win11)").
# There's no separate structured spec field to scrape, so these pull the
# capacity/type/CPU/etc. back out of the title text with best-effort regex.
# A field that can't be found renders as "—" rather than guessing. Same
# extractors are reused across all retailers since naming conventions are
# similar enough (brand + spec string) on Vietnamese PC-parts storefronts.
# ---------------------------------------------------------------------------
CPU_RE = re.compile(
    r"(Core\s*i[3579][\w-]*|i[3579]-[\w]+|Ryzen\s*(?:AI\s*)?[3579][\w-]*|"
    r"Ultra\s*[579][\w-]*|Celeron[\w-]*|Pentium[\w-]*|M[1-4](?:\s*(?:Pro|Max|Ultra))?)",
    re.IGNORECASE,
)
DDR_RE = re.compile(r"DDR([3-5])", re.IGNORECASE)
BUS_RE = re.compile(r"(\d{3,5})\s*MHz", re.IGNORECASE)
INTERFACE_KEYWORDS = ["NVMe", "PCIe Gen 5", "PCIe Gen 4", "PCIe Gen 3", "PCIe", "M.2 SATA", "SATA", "M.2"]
# Any "<number> GB" or "<number> TB", optionally immediately followed by
# SSD/HDD - used to tell a laptop's RAM figure apart from its storage figure.
CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(GB|TB)(\s*(SSD|HDD))?", re.IGNORECASE)


def extract_ram_specs(name):
    cap = re.search(r"(\d+)\s*GB", name, re.IGNORECASE)
    ddr = DDR_RE.search(name)
    bus = BUS_RE.search(name)
    return {
        "Dung lượng": f"{cap.group(1)}GB" if cap else "—",
        "Chuẩn": f"DDR{ddr.group(1)}" if ddr else "—",
        "Bus": f"{bus.group(1)}MHz" if bus else "—",
    }


def extract_ssd_specs(name):
    cap = re.search(r"(\d+(?:\.\d+)?)\s*(TB|GB)", name, re.IGNORECASE)
    capacity = f"{cap.group(1)}{cap.group(2).upper()}" if cap else "—"
    interface = "—"
    for kw in INTERFACE_KEYWORDS:
        if kw.lower() in name.lower():
            interface = kw
            break
    return {"Dung lượng": capacity, "Chuẩn": interface}


def extract_laptop_specs(name):
    cpu_match = CPU_RE.search(name)
    cpu = cpu_match.group(1) if cpu_match else "—"
    ram, rom = None, None
    for m in CAPACITY_RE.finditer(name):
        value, unit, _, storage_kind = m.groups()
        label = f"{value}{unit.upper()}" + (f" {storage_kind.upper()}" if storage_kind else "")
        if storage_kind or unit.upper() == "TB":
            # Explicitly tagged as SSD/HDD, or a TB figure (RAM is never
            # advertised in TB on these listings) -> storage, not RAM.
            if rom is None:
                rom = label
        elif ram is None:
            ram = label
        elif rom is None:
            # Second bare "<N>GB" with no SSD/HDD tag - on these titles
            # that's almost always the storage figure written without an
            # explicit SSD/HDD suffix (e.g. "8GB/512GB").
            rom = label
    return {"CPU": cpu, "RAM": ram or "—", "ROM (Lưu trữ)": rom or "—"}


# Maps category key -> (extractor function, ordered column labels to show).
SPEC_EXTRACTORS = {
    "ram": (extract_ram_specs, ["Dung lượng", "Chuẩn", "Bus"]),
    "ssd": (extract_ssd_specs, ["Dung lượng", "Chuẩn"]),
    "laptop": (extract_laptop_specs, ["CPU", "RAM", "ROM (Lưu trữ)"]),
}


def norm(s):
    """Collapse whitespace/NBSP and normalize to NFC so diacritics compare
    equal regardless of which composed/decomposed form the page sends."""
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return unicodedata.normalize("NFC", s)


def load_last_hash(path=STATE_FILE):
    """Return the previous run's price-data hash, or None if there isn't
    one (missing/corrupt state is treated as "first run", not fatal)."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f).get("hash")
    except (json.JSONDecodeError, OSError) as e:
        print(f" could not read {path} ({e}) - starting with empty dedup state", file=sys.stderr)
        return None


def save_last_hash(price_hash, path=STATE_FILE):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump({"hash": price_hash, "updated": datetime.utcnow().isoformat() + "Z"}, f)


def hash_data(data):
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fetch_page(url):
    """GET a page, verifying TLS against certifi's CA bundle explicitly
    (see gold-price-emailer's fetch_page for why). ALLOW_INSECURE_SSL_FALLBACK
    is an explicit opt-in last resort if that still fails."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=certifi.where())
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.SSLError as e:
        print(f" TLS verification failed with certifi's CA bundle: {e}", file=sys.stderr)
        if not ALLOW_INSECURE_SSL_FALLBACK:
            print(
                " Set ALLOW_INSECURE_SSL_FALLBACK=true to retry without verification "
                "as a last resort.",
                file=sys.stderr,
            )
            raise
        print(" ALLOW_INSECURE_SSL_FALLBACK=true - retrying with TLS verification disabled.", file=sys.stderr)
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        resp.raise_for_status()
        return resp.text


def fetch_rendered_page(url, timeout_ms=BROWSER_TIMEOUT_MS):
    """
    Load `url` in a real (headless) Chromium tab via Playwright and return
    the fully-rendered HTML, for sites like HACOM whose product grid is
    fetched by client-side JS after the initial page load and therefore
    never appears in a plain requests.get() response.

    Requires `pip install playwright` + a one-time
    `python -m playwright install --with-deps chromium` (see requirements.txt
    / the GitHub Actions workflow, which already does this).
    """
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "This category needs a headless browser to render (Playwright), "
            "but the 'playwright' package isn't installed. Run: "
            "pip install playwright && python -m playwright install --with-deps chromium"
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="vi-VN",
                viewport={"width": 1366, "height": 900},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            # The shell loads fast; the product grid itself comes in via a
            # later XHR/fetch call. "networkidle" waits for that network
            # activity to quiet down before we consider the page "loaded".
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass  # some pages keep background polling forever - proceed anyway
            # Extra belt-and-suspenders wait: give the page a few more
            # seconds if the currency symbol hasn't shown up multiple times
            # yet (i.e. the product grid specifically, not just page chrome).
            try:
                page.wait_for_function(
                    "document.body.innerText.split('\u20ab').length > 5",
                    timeout=10000,
                )
            except PlaywrightTimeoutError:
                pass
            html = page.content()
        finally:
            browser.close()
    return html


def parse_listing(html, max_items=MAX_ITEMS_PER_CATEGORY):
    """
    Parse a product-listing category page into a list of
    {name, price, old_price} rows (old_price is None if not on sale).

    Works across MemoryZone.vn / HACOM.vn / PhongVu.vn (and likely other
    similarly-templated Vietnamese PC-parts storefronts), since none of
    them are cleanly separated in the DOM in a way we can rely on
    long-term (product-card class names on storefront templates change
    with theme updates). Instead of depending on exact structure, this
    walks the page's flattened text and looks for a plausible product
    name line immediately followed - within a couple of lines - by a
    "X.XXX.XXX ₫" price line. This is more resilient to markup changes
    than a strict DOM walk, at the cost of being a bit more heuristic - if
    a run parses 0 items for a given retailer/category, open that URL and
    check product cards still show the name directly above/near the
    price, then extend JUNK_NAME_PREFIXES / JUNK_NAME_RE as needed for
    that site's particular chrome text.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [norm(l) for l in text.split("\n") if norm(l)]

    items = []
    seen = set()
    i = 0
    while i < len(lines) and len(items) < max_items:
        name = lines[i]

        is_price_line = bool(PRICE_RE.search(name))
        too_short_or_long = not (10 <= len(name) <= 150)
        is_junk = name.lower().startswith(JUNK_NAME_PREFIXES) or JUNK_NAME_RE.match(name)

        if is_price_line or too_short_or_long or is_junk or name in seen:
            i += 1
            continue

        # Look ahead up to 3 lines (skipping nothing in between except more
        # text) for the first price-shaped line - that's the product's
        # price line on these storefront card layouts.
        match = None
        for j in range(i + 1, min(i + 4, len(lines))):
            m = PRICE_RE.findall(lines[j])
            if m:
                match = (j, m)
                break
            # If we hit what looks like *another* product name before
            # finding a price, this line probably wasn't a product name -
            # bail out rather than pairing it with a distant price.
            if len(lines[j]) >= 10 and not PRICE_RE.search(lines[j]):
                continue

        if not match:
            i += 1
            continue

        j, prices = match
        price = prices[0]
        old_price = prices[1] if len(prices) > 1 and prices[1] != prices[0] else None
        seen.add(name)
        items.append({"name": name, "price": price, "old_price": old_price})
        i = j + 1

    return items


def fetch_category(key, url, max_items=MAX_ITEMS_PER_CATEGORY, needs_browser=False):
    html = fetch_rendered_page(url) if needs_browser else fetch_page(url)
    items = parse_listing(html, max_items=max_items)
    extractor = SPEC_EXTRACTORS.get(key)
    if extractor:
        extract_fn, _ = extractor
        for item in items:
            item["specs"] = extract_fn(item["name"])
    return items


def _price_html(price, old_price):
    if old_price:
        try:
            cur = int(price.replace(".", ""))
            old = int(old_price.replace(".", ""))
            pct = round((1 - cur / old) * 100)
            discount = f" <span style='color:#cf222e'>-{pct}%</span>"
        except (ValueError, ZeroDivisionError):
            discount = ""
        return (
            f"{escape(price)} \u20ab "
            f"<span style='color:#999;text-decoration:line-through'>{escape(old_price)} \u20ab</span>"
            f"{discount}"
        )
    return f"{escape(price)} \u20ab"


def _price_text(price, old_price):
    if old_price:
        return f"{price} d (was {old_price} d)"
    return f"{price} d"


def build_html(categories_data, timestamp):
    # Group by retailer so each store gets its own section header, with
    # its three categories nested underneath.
    sites = []
    seen_sites = set()
    for cat in categories_data:
        if cat["site"] not in seen_sites:
            seen_sites.add(cat["site"])
            sites.append((cat["site"], cat["site_label"]))

    site_sections = []
    for site_key, site_label in sites:
        cat_sections = []
        for cat in [c for c in categories_data if c["site"] == site_key]:
            spec_cols = SPEC_EXTRACTORS.get(cat["key"], (None, []))[1]
            if not cat["items"]:
                body = (
                    f"<p>Could not parse any items this run. "
                    f"Check <a href='{escape(cat['url'])}'>{escape(cat['url'])}</a> directly.</p>"
                )
            else:
                header_cells = "".join(
                    f"<th style='padding:8px 12px;text-align:left;'>{escape(col)}</th>" for col in spec_cols
                )
                row_html = "\n".join(
                    f"<tr>"
                    f"<td style='padding:6px 12px;border-bottom:1px solid #eee'>{escape(item['name'])}</td>"
                    + "".join(
                        f"<td style='padding:6px 12px;border-bottom:1px solid #eee;white-space:nowrap'>"
                        f"{escape(item.get('specs', {}).get(col, '—'))}</td>"
                        for col in spec_cols
                    )
                    + f"<td style='padding:6px 12px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap'>"
                    f"{_price_html(item['price'], item['old_price'])}</td>"
                    f"</tr>"
                    for item in cat["items"]
                )
                body = f"""
                <table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;max-width:720px;font-family:Arial,Helvetica,sans-serif;font-size:14px;">
                  <thead>
                    <tr style="background:#f5f5f5;">
                      <th style="padding:8px 12px;text-align:left;">Sản phẩm</th>
                      {header_cells}
                      <th style="padding:8px 12px;text-align:right;">Giá</th>
                    </tr>
                  </thead>
                  <tbody>
                    {row_html}
                  </tbody>
                </table>"""
            cat_sections.append(
                f"<h3 style='color:#1a5fb4;margin-top:20px;'>{escape(cat['label'])}</h3>"
                f"<p style='color:#999;font-size:12px;margin:4px 0 8px;'>"
                f"Nguồn: <a href='{escape(cat['url'])}'>{escape(cat['url'])}</a></p>"
                f"{body}"
            )
        site_sections.append(
            f"<h2 style='color:#111;border-bottom:2px solid #1a5fb4;padding-bottom:4px;margin-top:32px;'>"
            f"{escape(site_label)}</h2>"
            f"{''.join(cat_sections)}"
        )

    return f"""\
<html>
<body style="margin:0; padding:20px; background:#f4f4f4; font-family:Arial,Helvetica,sans-serif;">
<h1 style="color:#1a5fb4;">Giá RAM / SSD / Laptop hôm nay</h1>
<p style="color:#555;">Cập nhật {escape(timestamp)}</p>
{''.join(site_sections)}
<p style="color:#999; font-size:12px; margin-top:24px;">
Đây là giá niêm yết tại các cửa hàng ở thời điểm quét, không phải giá thị
trường trung bình · Mỗi mục vẫn là giá riêng của từng cửa hàng, đặt cạnh
nhau để tiện so sánh, không phải một chỉ số thị trường thống nhất · Email
tự động, chỉ mang tính tham khảo, không phải lời khuyên mua hàng - vui
lòng kiểm tra lại giá trên website trước khi đặt hàng.
</p>
</body>
</html>"""


def build_plain_text(categories_data, timestamp):
    lines = [f"Gia RAM/SSD/Laptop - cap nhat {timestamp}", ""]
    current_site = None
    for cat in categories_data:
        if cat["site"] != current_site:
            current_site = cat["site"]
            lines.append(f"########## {cat['site_label']} ##########")
            lines.append("")
        spec_cols = SPEC_EXTRACTORS.get(cat["key"], (None, []))[1]
        lines.append(f"== {cat['label']} ({cat['url']}) ==")
        if not cat["items"]:
            lines.append(" Could not parse any items this run.")
        else:
            for item in cat["items"]:
                specs = item.get("specs", {})
                spec_str = ", ".join(f"{col}: {specs.get(col, '—')}" for col in spec_cols)
                price_str = _price_text(item["price"], item["old_price"])
                lines.append(f" - {item['name']}")
                lines.append(f"   {spec_str} | Gia: {price_str}")
        lines.append("")
    return "\n".join(lines)


def resolve_timestamp():
    timezone_name = os.environ.get("TIMEZONE", "Asia/Ho_Chi_Minh")
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(timezone_name))
    except Exception:
        now = datetime.now()
    return now, now.strftime("%H:%M %d/%m/%Y")


def cmd_generate():
    if os.path.exists(EMAIL_DIR):
        for f in os.listdir(EMAIL_DIR):
            os.remove(os.path.join(EMAIL_DIR, f))
    os.makedirs(EMAIL_DIR, exist_ok=True)

    if not CATEGORIES:
        print("No retailers/categories enabled (check ENABLED_RETAILERS). Aborting.", file=sys.stderr)
        sys.exit(1)

    categories_data = []
    total_items = 0
    had_fetch_error = False

    for cat in CATEGORIES:
        via = " (via headless browser)" if cat.get("needs_browser") else ""
        print(f"Fetching {cat['site_label']} - {cat['label']} ({cat['url']}){via} ...")
        try:
            items = fetch_category(
                cat["key"], cat["url"], needs_browser=cat.get("needs_browser", False)
            )
        except requests.RequestException as e:
            print(f"  Failed to fetch {cat['url']}: {e}", file=sys.stderr)
            had_fetch_error = True
            items = []
        except RuntimeError as e:
            # Playwright not installed, or similar setup problem.
            print(f"  {e}", file=sys.stderr)
            had_fetch_error = True
            items = []
        except Exception as e:  # noqa: BLE001 - Playwright raises its own error types
            print(
                f"  Failed to render {cat['url']} with headless browser: {e}\n"
                f"  (If this is {cat['site_label']}, the site may be blocking automated "
                f"browsers - see README.)",
                file=sys.stderr,
            )
            had_fetch_error = True
            items = []
        print(f"  Parsed {len(items)} item(s).")
        if not items:
            print(
                f"  0 items parsed for {cat['site_label']} - {cat['label']} - the page markup may "
                f"have changed. Open {cat['url']} and check parse_listing().",
                file=sys.stderr,
            )
        categories_data.append({**cat, "items": items})
        total_items += len(items)

    if total_items == 0 and had_fetch_error:
        print("All categories failed to fetch. Aborting without sending.", file=sys.stderr)
        sys.exit(1)

    price_hash = hash_data(
        [{"site": c["site"], "label": c["label"], "items": c["items"]} for c in categories_data]
    )
    last_hash = load_last_hash()

    if total_items and SEND_ONLY_ON_CHANGE and price_hash == last_hash:
        print("Prices unchanged since last run and SEND_ONLY_ON_CHANGE=true - skipping email.")
        with open(os.path.join(EMAIL_DIR, "meta.json"), "w") as f:
            json.dump({"send": False}, f)
        return

    now, timestamp = resolve_timestamp()
    subject = f"Gia RAM/SSD/Laptop - {now.strftime('%d/%m/%Y %H:%M')}"
    html_body = build_html(categories_data, timestamp)
    text_body = build_plain_text(categories_data, timestamp)

    with open(os.path.join(EMAIL_DIR, "subject.txt"), "w") as f:
        f.write(subject)
    with open(os.path.join(EMAIL_DIR, "body.html"), "w") as f:
        f.write(html_body)
    with open(os.path.join(EMAIL_DIR, "body.txt"), "w") as f:
        f.write(text_body)
    with open(os.path.join(EMAIL_DIR, "meta.json"), "w") as f:
        json.dump({"send": True, "items": total_items}, f)

    save_last_hash(price_hash)
    print(f"Generated email ({total_items} item(s) total across {len(set(c['site'] for c in categories_data))} retailer(s)). Saved to ./{EMAIL_DIR}/")


def cmd_send():
    sender = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("TECH_RECIPIENT")

    missing = [name for name, val in [
        ("GMAIL_ADDRESS", sender),
        ("GMAIL_APP_PASSWORD", app_password),
        ("TECH_RECIPIENT", recipient),
    ] if not val]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    meta_path = os.path.join(EMAIL_DIR, "meta.json")
    if not os.path.exists(meta_path):
        print("No meta.json found - run 'generate' first.", file=sys.stderr)
        sys.exit(1)
    with open(meta_path) as f:
        meta = json.load(f)

    if not meta.get("send", False):
        print("Nothing to send this run (unchanged prices, or generate found no items).")
        return

    with open(os.path.join(EMAIL_DIR, "subject.txt")) as f:
        subject = f.read()
    with open(os.path.join(EMAIL_DIR, "body.html")) as f:
        html_body = f.read()
    with open(os.path.join(EMAIL_DIR, "body.txt")) as f:
        text_body = f.read()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender, app_password)
        server.send_message(msg)

    print(f"Sent to {recipient}!")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("generate", "send"):
        print("Usage: python ram_ssd_laptop_price_emailer.py [generate|send]", file=sys.stderr)
        sys.exit(1)
    if sys.argv[1] == "generate":
        cmd_generate()
    else:
        cmd_send()


if __name__ == "__main__":
    main()
