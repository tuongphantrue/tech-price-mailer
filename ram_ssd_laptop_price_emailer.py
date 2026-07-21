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

    - MemoryZone.vn       (memoryzone.com.vn)
    - HACOM.vn             (hacom.vn)          - Hanoi-headquartered, showrooms
                                                  across Hanoi (Hai Ba Trung,
                                                  Dong Da, Cau Giay, Ha Dong, ...)
    - Phong Vu             (phongvu.vn)        - nationwide chain, Hanoi showrooms.
                                                  Disabled by default - sits behind a
                                                  Cloudflare challenge that hasn't
                                                  cleared even for a real headless
                                                  browser in testing; see the note
                                                  further down and in README.md.
    - GEARVN               (gearvn.com)        - nationwide chain, Hanoi showrooms
    - An Phat Computer      (anphatpc.com.vn)   - Hanoi-headquartered
    - Phuc Anh              (phucanh.vn)        - nationwide chain, Hanoi showrooms
    - ThinkPro              (thinkpro.vn)       - nationwide chain, Hanoi showrooms
    - Hoang Ha PC           (hoanghapc.vn)      - Hanoi-headquartered (Cau Giay,
                                                  Dong Da showrooms)

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
    export ENABLED_RETAILERS="memoryzone,hacom,gearvn,anphat,thinkpro,hoangha"
                                                 # optional, default is all EXCEPT phongvu and phucanh (see notes below)
    export MAX_ITEMS_PER_CATEGORY="24"          # optional
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
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from urllib.parse import urljoin

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
            "hdd": ("HDD", "https://memoryzone.com.vn/hdd"),
            "vga": ("VGA - Card màn hình", "https://memoryzone.com.vn/vga"),
            "mainboard": ("Mainboard", "https://memoryzone.com.vn/mainboard-pc"),
            "psu": ("PSU - Nguồn máy tính", "https://memoryzone.com.vn/psu-nguon-may-tinh"),
            "monitor": ("Màn hình", "https://memoryzone.com.vn/man-hinh"),
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
            "hdd": ("HDD", "https://hacom.vn/o-cung-hdd-desktop"),
            "vga": ("VGA - Card màn hình", "https://hacom.vn/vga-card-man-hinh"),
            "mainboard": ("Mainboard", "https://hacom.vn/mainboard-bo-mach-chu"),
            "psu": ("PSU - Nguồn máy tính", "https://hacom.vn/nguon-may-tinh"),
            "monitor": ("Màn hình", "https://hacom.vn/man-hinh-may-tinh"),
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
    "gearvn": {
        "label": "GEARVN",
        # NOT Shopify despite the /collections/ URLs - actually runs on
        # Haravan (a Vietnamese e-commerce platform). Confirmed by fetching
        # the raw page directly: the initial HTML has page chrome (nav,
        # filters, footer) but the product grid itself is completely empty -
        # it is populated by client-side JS after load, same situation as
        # HACOM. Confirmed needs a browser.
        "needs_browser": True,
        "categories": {
            "ram": ("RAM Laptop", "https://gearvn.com/collections/ram-laptop"),
            "ssd": ("SSD", "https://gearvn.com/collections/ssd-o-cung-the-ran"),
            "laptop": ("Laptop", "https://gearvn.com/collections/laptop"),
            "hdd": ("HDD", "https://gearvn.com/collections/hdd-o-cung-pc"),
            "vga": ("VGA - Card màn hình", "https://gearvn.com/collections/vga-card-man-hinh"),
            "mainboard": ("Mainboard", "https://gearvn.com/collections/mainboard-bo-mach-chu"),
            "psu": ("PSU - Nguồn máy tính", "https://gearvn.com/collections/psu-nguon-may-tinh"),
            "monitor": ("Màn hình", "https://gearvn.com/collections/man-hinh"),
        },
    },
    "anphat": {
        "label": "An Phát Computer (Hà Nội)",
        # Despite the classic .html URL style, this site turned out to have
        # an enormous, deeply-nested mega-menu on every page (thousands of
        # category links) that could not be confirmed to give way to a
        # server-rendered product grid from this environment. Given GEARVN
        # already turned out to need a browser despite looking similarly
        # "classic" at a glance, this defaults to the safer assumption too.
        "needs_browser": True,
        "categories": {
            "ram": ("RAM Laptop", "https://www.anphatpc.com.vn/ram-laptop.html"),
            "ssd": ("SSD", "https://www.anphatpc.com.vn/o-cung-hdd-ssd_dm1314.html"),
            "laptop": ("Laptop", "https://www.anphatpc.com.vn/may-tinh-xach-tay-laptop.html"),
            "hdd": ("HDD", "https://www.anphatpc.com.vn/o-cung-desktop_dm1047.html"),
            "vga": ("VGA - Card màn hình", "https://www.anphatpc.com.vn/vga-card-man-hinh.html"),
            "mainboard": ("Mainboard", "https://www.anphatpc.com.vn/mainboard-theo-hang.html"),
            "psu": ("PSU - Nguồn máy tính", "https://www.anphatpc.com.vn/nguon-dien-may-tinh-psu_dm1051.html"),
            "monitor": ("Màn hình", "https://www.anphatpc.com.vn/man-hinh-may-tinh.html-1"),
        },
    },
    "phucanh": {
        "label": "Phúc Anh",
        # Confirmed to sit behind a Cloudflare challenge that doesn't
        # clear even for a real headless browser (same "verification
        # successful, still waiting" stuck state as Phong Vu) - excluded
        # from the default ENABLED_RETAILERS list. Left defined here in
        # case someone wants to retry it from a different network.
        "needs_browser": True,
        "categories": {
            "ram": ("RAM Laptop", "https://www.phucanh.vn/bo-nho-trong-linh-kien-pc.html"),
            "ssd": ("SSD", "https://www.phucanh.vn/o-cung-ssd.html"),
            "laptop": ("Laptop", "https://www.phucanh.vn/may-tinh-xach-tay-laptop.html"),
        },
    },
    "thinkpro": {
        "label": "ThinkPro",
        # Clean-path URLs (no .html, e.g. /o-cung) suggest a modern JS
        # framework similar to HACOM's. Not individually confirmed from
        # this environment, but consistent with every other site checked
        # so far needing a browser.
        "needs_browser": True,
        "categories": {
            "ram": ("RAM", "https://thinkpro.vn/ram"),
            "ssd": ("SSD / Ổ cứng", "https://thinkpro.vn/o-cung"),
            "laptop": ("Laptop", "https://thinkpro.vn/laptop"),
            # ThinkPro doesn't sell VGA/mainboard/PSU/HDD as separate
            # PC-building components (their own nav only lists Laptop,
            # RAM/Ổ cứng, Màn hình, and accessories) - only Monitor added.
            "monitor": ("Màn hình", "https://thinkpro.vn/man-hinh"),
        },
    },
    "hoangha": {
        "label": "Hoàng Hà PC (Hà Nội)",
        # Same reasoning as ThinkPro - clean-path URLs suggest a modern JS
        # framework, so this defaults to needing a browser.
        "needs_browser": True,
        "categories": {
            "ram": ("RAM", "https://hoanghapc.vn/ram-bo-nho-trong"),
            "ssd": ("SSD", "https://hoanghapc.vn/o-cung-the-ran-ssd"),
            "laptop": ("Laptop", "https://hoanghapc.vn/laptop"),
            "hdd": ("HDD", "https://hoanghapc.vn/o-cung-hdd"),
            "vga": ("VGA - Card màn hình", "https://hoanghapc.vn/vga-card-man-hinh"),
            "mainboard": ("Mainboard", "https://hoanghapc.vn/main-bo-mach-chu"),
            "psu": ("PSU - Nguồn máy tính", "https://hoanghapc.vn/psu-nguon-may-tinh"),
            "monitor": ("Màn hình", "https://hoanghapc.vn/man-hinh-may-tinh"),
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
    # phongvu is deliberately excluded from the default list: confirmed
    # (see README) to sit behind a Cloudflare challenge that doesn't clear
    # even for a real headless browser, at least from the environments
    # this was tested from. It's still a valid value here if you want to
    # try it anyway - e.g. from a residential home connection.
    enabled = os.environ.get(
        "ENABLED_RETAILERS", "memoryzone,hacom,gearvn,anphat,thinkpro,hoangha"
    )
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
                legacy_env_name = _LEGACY_URL_ENV.get(cat_key)
                url = os.environ.get(legacy_env_name) if legacy_env_name else None
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
PRICE_HISTORY_FILE = os.environ.get("PRICE_HISTORY_FILE", "state/price_history.json")
# Trend windows shown in the email, as (label, days-ago).
TREND_WINDOWS = [("7 ngày", 7), ("1 tháng", 30), ("6 tháng", 180), ("1 năm", 365)]
# Prune history points older than this so the state file doesn't grow
# forever - a bit past the longest trend window (1 year) is plenty.
HISTORY_MAX_AGE_DAYS = 400
SEND_ONLY_ON_CHANGE = os.environ.get("SEND_ONLY_ON_CHANGE", "false").lower() == "true"
ALLOW_INSECURE_SSL_FALLBACK = os.environ.get("ALLOW_INSECURE_SSL_FALLBACK", "false").lower() == "true"
MAX_ITEMS_PER_CATEGORY = int(os.environ.get("MAX_ITEMS_PER_CATEGORY", "24"))
# How long to let a headless-browser page (HACOM/Phong Vu) finish loading +
# running its client-side product-fetch JS before giving up on that
# category. These pages are slower than a plain HTTP GET, so this is
# generous by design.
BROWSER_TIMEOUT_MS = int(os.environ.get("BROWSER_TIMEOUT_MS", "45000"))

# Matches Vietnamese-formatted currency like "1.990.000 ₫" (dot as thousands
# separator). The currency mark varies by site: MemoryZone/HACOM use the
# actual currency symbol "₫" (U+20AB DONG SIGN), but Phong Vu renders it as
# the plain Vietnamese letter "đ"/"Đ" (U+0111/U+0110) instead - visually
# near-identical but a different character, so both are matched here or
# Phong Vu prices are invisible to this regex entirely. A listing/discount
# line often has two of these back to back: "1.990.000 ₫ 2.350.000 ₫"
# (sale price, then the crossed-out original price).
PRICE_RE = re.compile(
    r"([\d]{1,3}(?:\.[\d]{3})+)\s*(?:\u20ab|[đĐ](?![A-Za-zÀ-ỹ0-9_]))"
)

# A price-formatted number with NO currency mark at all - e.g. a
# crossed-out original price rendered as a bare "26.999.000" text node on
# its own line, separate from the marked-up sale price. This is matched
# against a *whole stripped line* (not searched within a larger line like
# PRICE_RE), specifically so these lines get excluded from product-name
# candidacy - otherwise a bare old-price line gets mistaken for the next
# product's name, paired with that next product's real price.
BARE_NUMBER_RE = re.compile(r"^\d{1,3}(?:\.\d{3}){1,3}$")

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
    r"chỉ bán build pc|còn hàng|hữu ích\s*\(\d+\)|tặng |quà tặng|khuyến mại|"
    r"mã\s*(sp|sản phẩm)?\s*:\s*\S+|\(tiết kiệm|tiết kiệm\s*\d+\s*%|"
    r"giá (tăng|giảm) dần|\(\d+\s*(đánh giá|sản phẩm)\)|giá (khuyến mãi|niêm yết):?$)",
    re.IGNORECASE,
)

# Catches stray JS/script syntax that shouldn't reach here (script/style
# tags are stripped before parsing) but is cheap to guard against anyway -
# e.g. Next.js RSC streaming payloads like self.__next_f.push([1,"153:...]).
JS_SYNTAX_RE = re.compile(r"__next_f|\.push\(\[|function\s*\(|=>\s*\{|<script|</script")

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


HDD_SIZE_RE = re.compile(r"(2\.5|3\.5)\s*(?:inch|\")", re.IGNORECASE)
HDD_RPM_RE = re.compile(r"(\d{4,5})\s*RPM", re.IGNORECASE)


def extract_hdd_specs(name):
    cap = re.search(r"(\d+(?:\.\d+)?)\s*(TB|GB)", name, re.IGNORECASE)
    capacity = f"{cap.group(1)}{cap.group(2).upper()}" if cap else "—"
    size = HDD_SIZE_RE.search(name)
    rpm = HDD_RPM_RE.search(name)
    return {
        "Dung lượng": capacity,
        "Kích thước": f'{size.group(1)}"' if size else "—",
        "Tốc độ": f"{rpm.group(1)} RPM" if rpm else "—",
    }


VGA_CHIP_RE = re.compile(
    r"(RTX\s?\d{3,4}(?:\s?Ti)?(?:\s?Super)?|GTX\s?\d{3,4}(?:\s?Ti)?(?:\s?Super)?|"
    r"RX\s?\d{3,4}(?:\s?XT)?|Radeon\s?(?:RX\s?)?\d{3,4}(?:\s?XT)?|Quadro[\w\s]*|Arc\s?[AB]\d{3})",
    re.IGNORECASE,
)
VGA_VRAM_RE = re.compile(r"(\d{1,2})\s*GB", re.IGNORECASE)


def extract_vga_specs(name):
    chip = VGA_CHIP_RE.search(name)
    vram = VGA_VRAM_RE.search(name)
    return {
        "Chip": chip.group(1) if chip else "—",
        "VRAM": f"{vram.group(1)}GB" if vram else "—",
    }


MAINBOARD_SOCKET_RE = re.compile(r"(LGA\s?\d{3,4}|AM[45]\+?|sTRX\d+|TRX\d+)", re.IGNORECASE)
MAINBOARD_CHIPSET_RE = re.compile(r"\b([BHXZ]\d{3}[A-Z]?)\b")
MAINBOARD_FORM_RE = re.compile(r"(E-?ATX|Micro-?ATX|mATX|Mini-?ITX|ITX|ATX)", re.IGNORECASE)


def extract_mainboard_specs(name):
    socket = MAINBOARD_SOCKET_RE.search(name)
    chipset = MAINBOARD_CHIPSET_RE.search(name)
    form = MAINBOARD_FORM_RE.search(name)
    return {
        "Socket": socket.group(1) if socket else "—",
        "Chipset": chipset.group(1) if chipset else "—",
        "Kích thước": form.group(1) if form else "—",
    }


PSU_WATT_RE = re.compile(r"(\d{3,4})\s?W\b", re.IGNORECASE)
PSU_RATING_RE = re.compile(
    r"80\s?Plus\s?(Titanium|Platinum|Gold|Silver|Bronze|White)?", re.IGNORECASE
)


def extract_psu_specs(name):
    watt = PSU_WATT_RE.search(name)
    rating = PSU_RATING_RE.search(name)
    rating_label = "—"
    if rating:
        tier = rating.group(1)
        rating_label = f"80 Plus {tier}" if tier else "80 Plus"
    return {
        "Công suất": f"{watt.group(1)}W" if watt else "—",
        "Chuẩn": rating_label,
    }


MONITOR_SIZE_RE = re.compile(r'(\d{2}(?:\.\d)?)\s*(?:inch|")', re.IGNORECASE)
MONITOR_RES_RE = re.compile(r"(FHD|QHD|UHD|4K|2K|8K|1920\s?x\s?1080|2560\s?x\s?1440|3840\s?x\s?2160)", re.IGNORECASE)
MONITOR_HZ_RE = re.compile(r"(\d{2,3})\s?Hz", re.IGNORECASE)


def extract_monitor_specs(name):
    size = MONITOR_SIZE_RE.search(name)
    res = MONITOR_RES_RE.search(name)
    hz = MONITOR_HZ_RE.search(name)
    return {
        "Kích thước": f'{size.group(1)}"' if size else "—",
        "Độ phân giải": res.group(1).upper() if res else "—",
        "Tần số quét": f"{hz.group(1)}Hz" if hz else "—",
    }


# Maps category key -> (extractor function, ordered column labels to show).
SPEC_EXTRACTORS = {
    "ram": (extract_ram_specs, ["Dung lượng", "Chuẩn", "Bus"]),
    "ssd": (extract_ssd_specs, ["Dung lượng", "Chuẩn"]),
    "laptop": (extract_laptop_specs, ["CPU", "RAM", "ROM (Lưu trữ)"]),
    "hdd": (extract_hdd_specs, ["Dung lượng", "Kích thước", "Tốc độ"]),
    "vga": (extract_vga_specs, ["Chip", "VRAM"]),
    "mainboard": (extract_mainboard_specs, ["Socket", "Chipset", "Kích thước"]),
    "psu": (extract_psu_specs, ["Công suất", "Chuẩn"]),
    "monitor": (extract_monitor_specs, ["Kích thước", "Độ phân giải", "Tần số quét"]),
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


def load_price_history(path=PRICE_HISTORY_FILE):
    """
    {item_key: [{"date": "YYYY-MM-DD", "price": int}, ...]}, sorted
    ascending by date per item. Missing/corrupt state is treated as "no
    history yet", not fatal - trends just won't be available until enough
    runs have accumulated data.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f" could not read {path} ({e}) - starting with empty price history", file=sys.stderr)
        return {}


def save_price_history(history, path=PRICE_HISTORY_FILE):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(history, f, ensure_ascii=False)


def item_history_key(site, cat_key, item):
    """
    A stable identifier for tracking one item's price across runs. The
    product page URL is the most reliable choice (survives re-orderings,
    price-driven title changes, etc.) - falls back to
    site+category+current-name for items no product link was found for,
    which is less stable (a name tweak on the retailer's side breaks
    continuity) but still workable.
    """
    if item.get("product_url"):
        return item["product_url"]
    return f"{site}|{cat_key}|{item['name']}"


def _price_to_int(price_str):
    try:
        return int(price_str.replace(".", ""))
    except (ValueError, AttributeError):
        return None


def update_history_and_get_trends(history, site, cat_key, items, today):
    """
    For each item: look up trend info (% change vs the closest available
    price point at or before each of the TREND_WINDOWS) using the
    *existing* history (i.e. not counting today's own price), attach it
    as item["trend"], then record today's price into history for future
    runs. Mutates `history` in place; returns nothing.
    """
    today_str = today.isoformat()
    cutoff_str = (today - timedelta(days=HISTORY_MAX_AGE_DAYS)).isoformat()

    for item in items:
        current = _price_to_int(item["price"])
        key = item_history_key(site, cat_key, item)
        entries = history.get(key, [])

        trends = {}
        if current is not None:
            for label, days in TREND_WINDOWS:
                target_str = (today - timedelta(days=days)).isoformat()
                # Most recent entry at or before the target date - the
                # closest available reference point for "N days ago".
                candidates = [e for e in entries if e["date"] <= target_str]
                if candidates:
                    ref = max(candidates, key=lambda e: e["date"])
                    if ref["price"]:
                        pct = round((current - ref["price"]) / ref["price"] * 100, 1)
                        trends[label] = pct
        item["trend"] = trends

        if current is not None:
            entries = [e for e in entries if e["date"] != today_str]
            entries.append({"date": today_str, "price": current})
            entries = [e for e in entries if e["date"] >= cutoff_str]
            entries.sort(key=lambda e: e["date"])
            history[key] = entries


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

            # Some sites (Phong Vu) sit behind a Cloudflare JS challenge:
            # an interstitial "verifying you're not a bot" page that
            # auto-resolves after a few seconds and then redirects to the
            # real page. A real headless browser generally passes this
            # automatic check fine - the previous "networkidle" wait above
            # just often lands *during* that interstitial, before its
            # redirect has fired. Detect that case and wait specifically
            # for it to clear, rather than reading the interstitial itself.
            try:
                is_still_challenge = page.evaluate(
                    "() => /xác minh bảo mật|checking your browser|just a moment|ray id/i"
                    ".test(document.body.innerText)"
                )
            except Exception:
                is_still_challenge = False
            if is_still_challenge:
                for _ in range(3):
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except PlaywrightTimeoutError:
                        pass
                    try:
                        still_there = page.evaluate(
                            "() => /xác minh bảo mật|checking your browser|just a moment|ray id/i"
                            ".test(document.body.innerText)"
                        )
                    except Exception:
                        still_there = False
                    if not still_there:
                        break

            # Extra belt-and-suspenders wait: give the page a few more
            # seconds if a currency marker hasn't shown up multiple times
            # yet (i.e. the product grid specifically, not just page
            # chrome). Checks both currency marks used across these sites -
            # the "₫" symbol (MemoryZone/HACOM) and the plain "đ" letter
            # (Phong Vu) - missing either one here was a real past bug.
            try:
                page.wait_for_function(
                    "(document.body.innerText.match(/[\u20abđĐ]/g) || []).length > 5",
                    timeout=10000,
                )
            except PlaywrightTimeoutError:
                pass
            # Some grids are populated by scroll-triggered lazy loading
            # (IntersectionObserver) rather than loading everything
            # up-front - nudge that along before reading the final HTML.
            try:
                for _ in range(3):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(800)
                page.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass  # non-critical - proceed with whatever loaded
            html = page.content()
        finally:
            browser.close()
    return html


def _best_img_src(img_tag, base_url):
    """
    Pick the most plausible real image URL off an <img> tag. Many
    storefronts lazy-load images: the `src` attribute holds a tiny
    placeholder (a blank/base64 pixel) until JS swaps in the real URL from
    a `data-src`/`data-lazy-src`/`data-original`/`srcset` attribute once
    the browser paints it. Playwright already waited for the page to
    settle before this runs, but the placeholder attribute can still be
    left behind in the DOM alongside the now-populated one, so this
    checks the lazy-load attributes first and falls back to `src` last.
    Relative and protocol-relative URLs are resolved against the category
    page's URL so they work standalone in an email client.
    """
    for attr in ("data-src", "data-lazy-src", "data-original", "srcset", "data-srcset", "src"):
        val = img_tag.get(attr)
        if not val:
            continue
        val = val.strip()
        if not val or val.startswith("data:"):
            continue  # inline base64 placeholder - not a usable standalone URL
        if "," in val and " " in val:
            # srcset format: "url1 1x, url2 2x" - take the first candidate
            val = val.split(",")[0].strip().split(" ")[0]
        if val:
            return urljoin(base_url, val)
    return None


def _nearest_link(node, base_url):
    """The href of the nearest ancestor <a> tag wrapping this node, or
    None. Storefront product cards are almost always wrapped in a single
    <a href="/product-slug"> that contains the thumbnail, name, and price
    together, so walking up to the enclosing anchor (rather than tracking
    "last <a> seen" the way images are tracked) gets the right link even
    when a page has other unrelated links between product cards."""
    a = node.find_parent("a")
    if not a:
        return None
    href = (a.get("href") or "").strip()
    if not href or href.startswith("javascript:") or href == "#":
        return None
    return urljoin(base_url, href)


def _extract_ordered_lines(soup, base_url=""):
    """
    Like soup.get_text("\\n").split("\\n"), but also emits each <img>'s alt
    text (if any) at its position in document order, and returns two
    parallel lists alongside the text lines:

    - image_urls: "the most recently seen product image URL" for each
      line, so a name/price pair can carry a thumbnail along with it. A
      line inherits the nearest *preceding* image in the DOM, which lines
      up with how these storefront cards are laid out (thumbnail, then
      title/SKU, then price) whether the name itself came from an
      <img alt> (HACOM-style) or a plain text node (MemoryZone-style).
    - link_urls: the href of the nearest *enclosing* <a> tag for each
      line (see _nearest_link) - i.e. the product page link, since these
      cards are normally one big <a> wrapping the thumbnail/name/price.

    Many storefronts - HACOM included - render the product title only as
    an <img alt="..."> for SEO/lazy-loading reasons, with no matching
    plain-text node anywhere on the card (the visible on-page text next to
    the price ends up being just the SKU code, e.g. "Mã: RAKT0273").
    soup.get_text() only sees text nodes, so it silently skips the real
    title and this script would otherwise "successfully" pair the price
    with the SKU line instead - wrong, but not a 0-items failure, so easy
    to miss. Interleaving alt text back into the sequence (in the same
    position it occupies in the DOM) lets the name-immediately-before-price
    heuristic in parse_listing() find the real title again.

    <script>/<style>/<noscript>/<template> contents are stripped first -
    BeautifulSoup's text walk otherwise includes raw <script> text too
    (a well-known gotcha), and on a Next.js site like HACOM that text
    includes RSC streaming payloads such as
    `self.__next_f.push([1,"153:T622,"])` for every chunk of the page -
    which are just plausible-length/shape enough to occasionally get
    mistaken for a product name sitting near a real price.
    """
    from bs4 import NavigableString

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    lines = []
    image_urls = []
    link_urls = []
    last_img_src = None
    for node in soup.descendants:
        # Exact-type check (not isinstance): NavigableString subclasses
        # like Comment, CData, Declaration also satisfy isinstance() but
        # aren't real page text - an HTML comment sitting near a price
        # would otherwise occasionally get mistaken for a product name,
        # the same class of bug as the earlier <script>-content one.
        if type(node) is NavigableString:
            t = norm(str(node))
            if t:
                lines.append(t)
                image_urls.append(last_img_src)
                link_urls.append(_nearest_link(node, base_url))
        elif getattr(node, "name", None) == "img":
            src = _best_img_src(node, base_url)
            if src:
                last_img_src = src
            alt = norm(node.get("alt", "") or "")
            if alt:
                lines.append(alt)
                image_urls.append(src or last_img_src)
                link_urls.append(_nearest_link(node, base_url))
    return lines, image_urls, link_urls


def parse_listing(html, max_items=MAX_ITEMS_PER_CATEGORY, base_url=""):
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
    lines, image_urls, link_urls = _extract_ordered_lines(soup, base_url=base_url)

    items = []
    seen = set()
    i = 0
    while i < len(lines) and len(items) < max_items:
        name = lines[i]

        is_price_line = bool(PRICE_RE.search(name)) or bool(BARE_NUMBER_RE.match(name))
        too_short_or_long = not (10 <= len(name) <= 150)
        is_junk = name.lower().startswith(JUNK_NAME_PREFIXES) or JUNK_NAME_RE.match(name) or JS_SYNTAX_RE.search(name)

        if is_price_line or too_short_or_long or is_junk or name in seen:
            i += 1
            continue

        # Look ahead a handful of lines (skipping over SKU-code/badge lines,
        # which are filtered out as junk above but can still sit between a
        # product's name and its price) for the first price-shaped line -
        # that's the product's price line on these storefront card layouts.
        match = None
        for j in range(i + 1, min(i + 7, len(lines))):
            line_j = lines[j]
            m = PRICE_RE.findall(line_j)
            if m:
                match = (j, m)
                break
            if BARE_NUMBER_RE.match(line_j):
                # No currency mark glued to this number in the same DOM
                # text node (e.g. ThinkPro renders the "đ" as a separate
                # sibling element from the digits) - a dot-grouped 6+
                # digit number sitting this close to a product name is
                # still, in context, almost certainly its price.
                match = (j, [line_j])
                break
            # If we hit a substantial line that isn't recognized junk
            # before finding a price, this candidate probably wasn't a
            # real product name after all (or it's the *next* product's
            # name) - stop searching rather than reaching past it to pair
            # with some later, unrelated price. Recognized junk (SKU
            # codes, discount badges, spec-filter chips like "Bus:
            # 3200MHz" sitting between one product's price and the next
            # product's name) is still skipped over, same as before.
            is_junk_j = (
                line_j.lower().startswith(JUNK_NAME_PREFIXES)
                or JUNK_NAME_RE.match(line_j)
                or JS_SYNTAX_RE.search(line_j)
            )
            if len(line_j) >= 10 and not is_junk_j:
                break

        if not match:
            i += 1
            continue

        j, prices = match
        price = prices[0]
        old_price = prices[1] if len(prices) > 1 and prices[1] != prices[0] else None
        if old_price is None and j + 1 < len(lines):
            # The crossed-out original price is sometimes its own text node
            # with no currency mark at all (e.g. "26.999.000" on its own
            # line right after "29.999.000 ₫") rather than appearing
            # alongside the sale price on the same line.
            bare = BARE_NUMBER_RE.match(lines[j + 1])
            if bare and lines[j + 1] != price:
                old_price = lines[j + 1]
        seen.add(name)
        items.append(
            {
                "name": name,
                "price": price,
                "old_price": old_price,
                "image_url": image_urls[i],
                "product_url": link_urls[i],
            }
        )
        i = j + 1

    return items


def diagnose_empty_render(html):
    """
    When a browser-rendered category parses to 0 items, this can't
    distinguish "page structure changed" from "we got a bot-check/
    interstitial page instead of the real one" from log output alone -
    and this script's sandboxed dev environment has no way to load these
    sites directly to check. This builds a short diagnostic summary from
    the rendered HTML so the next run's logs carry enough information to
    tell the difference without needing to reproduce the failure by hand.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    body_text = norm(soup.get_text(" "))[:400]

    dong_count = len(re.findall(r"[\u20abđĐ]", body_text))
    suspect_terms = [
        term
        for term in (
            "captcha", "cloudflare", "just a moment", "checking your browser",
            "are you human", "access denied", "403 forbidden", "verify you are",
            "bạn không phải", "xác minh", "vui lòng chờ",
        )
        if term in body_text.lower()
    ]

    return (
        f"    html length: {len(html)} chars | body text sample (first 400 chars): "
        f"{body_text!r}\n"
        f"    currency-mark occurrences (₫/đ) in that sample: {dong_count}\n"
        f"    possible bot-check/interstitial keywords found: {suspect_terms or 'none'}"
    )


def fetch_category(key, url, max_items=MAX_ITEMS_PER_CATEGORY, needs_browser=False, return_html=False):
    html = fetch_rendered_page(url) if needs_browser else fetch_page(url)
    extractor = SPEC_EXTRACTORS.get(key)
    # Collect a larger batch of raw candidates than we actually want to
    # show: if the page has a run of off-topic junk (a cross-department
    # "hot deals" sidebar, category nav links, etc.) mixed in before or
    # between real products, stopping collection right at max_items would
    # often mean stopping on mostly junk, before reaching enough real
    # products further down the page. Over-collect, spec-filter, then
    # truncate to what was actually asked for.
    raw_cap = max_items * 4 if extractor else max_items
    items = parse_listing(html, max_items=raw_cap, base_url=url)
    if extractor:
        extract_fn, _ = extractor
        for item in items:
            item["specs"] = extract_fn(item["name"])
        with_specs = [it for it in items if any(v != "—" for v in it["specs"].values())]
        # A genuine RAM/SSD/Laptop listing title reliably contains
        # extractable spec info (capacity, DDR generation, CPU model...)
        # on every site checked so far - an item with *zero* matched
        # fields is almost always off-topic content that leaked in from
        # an unrelated part of the page rather than a real product this
        # extractor just doesn't understand. Keep only those, unless
        # literally none matched at all - in that edge case, showing the
        # unfiltered raw batch is better than showing nothing.
        items = with_specs if with_specs else items
        items = items[:max_items]
    if return_html:
        return items, html
    return items


SITE_COLORS = [
    "#4F46E5",  # indigo
    "#0EA5E9",  # sky
    "#059669",  # emerald
    "#DC2626",  # red
    "#D97706",  # amber
    "#7C3AED",  # violet
    "#DB2777",  # pink
    "#0891B2",  # cyan
]


def _thumb_html(item, size=52):
    """
    A small rounded product-thumbnail <img>, or a blank placeholder box if
    no image URL was found for this item. Email clients commonly block
    remote images until the person clicks "display images" - that's
    normal, expected behavior for any email with images, not something
    this script can or should try to bypass.
    """
    url = item.get("image_url")
    if not url:
        return (
            f"<div style='width:{size}px;height:{size}px;background:#f1f3f5;"
            f"border-radius:10px;'></div>"
        )
    return (
        f"<img src='{escape(url)}' alt='' width='{size}' height='{size}' "
        f"style='width:{size}px;height:{size}px;object-fit:contain;"
        f"border-radius:10px;border:1px solid #eef0f3;background:#ffffff;display:block;' />"
    )


def _trend_html(item):
    """
    Compact, colored price-trend line: % change vs the closest available
    price point for each of TREND_WINDOWS. Green/down = price dropped
    since then (good for a buyer), red/up = price rose. A window shows
    "—" instead of a percentage when there isn't yet a price point old
    enough to compare against (e.g. the workflow hasn't been running for
    a full year yet) - this is normal in the early days of running this
    script and fills in on its own as history accumulates, not an error.
    """
    trend = item.get("trend") or {}
    parts = []
    for label, _days in TREND_WINDOWS:
        pct = trend.get(label)
        if pct is None:
            parts.append(
                f"<span style='color:#c0c5cc;'>{escape(label)} —</span>"
            )
        elif pct > 0:
            parts.append(
                f"<span style='color:#dc2626;'>{escape(label)} ▲{pct:g}%</span>"
            )
        elif pct < 0:
            parts.append(
                f"<span style='color:#16a34a;'>{escape(label)} ▼{abs(pct):g}%</span>"
            )
        else:
            parts.append(
                f"<span style='color:#9ca3af;'>{escape(label)} •0%</span>"
            )
    return (
        "<div style='font-size:10px;margin-top:5px;line-height:1.6;'>"
        + "&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts)
        + "</div>"
    )


def _spec_tags_html(item, spec_cols):
    """Small pill-style tags for whatever specs were extracted, skipping
    any that came back as '—' (not found) rather than showing a row of
    placeholder dashes."""
    specs = item.get("specs", {})
    tags = [
        f"<span style='display:inline-block;background:#f1f5f9;color:#475569;"
        f"font-size:10.5px;line-height:1.4;padding:2px 8px;border-radius:10px;"
        f"margin:3px 4px 0 0;white-space:nowrap;'>{escape(specs[col])}</span>"
        for col in spec_cols
        if specs.get(col, "—") != "—"
    ]
    return "".join(tags)


def _price_block_html(price, old_price):
    """Right-aligned price stack: discount badge (if on sale) above the
    current price, crossed-out original price below it."""
    badge = ""
    old_line = ""
    if old_price:
        try:
            cur = int(price.replace(".", ""))
            old = int(old_price.replace(".", ""))
            pct = round((1 - cur / old) * 100)
            if pct > 0:
                badge = (
                    f"<span style='display:inline-block;background:#fee2e2;color:#dc2626;"
                    f"font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:6px;"
                    f"margin-bottom:3px;'>-{pct}%</span><br/>"
                )
        except (ValueError, ZeroDivisionError):
            pass
        old_line = (
            f"<div style='font-size:11px;color:#9ca3af;text-decoration:line-through;"
            f"margin-top:2px;'>{escape(old_price)} \u20ab</div>"
        )
    return (
        f"<div style='text-align:right;'>"
        f"{badge}"
        f"<div style='font-size:14.5px;font-weight:700;color:#111827;white-space:nowrap;'>"
        f"{escape(price)} \u20ab</div>"
        f"{old_line}"
        f"</div>"
    )


def _price_text(price, old_price):
    if old_price:
        return f"{price} d (was {old_price} d)"
    return f"{price} d"


TYPE_META = {
    "laptop": ("Laptop", "💻"),
    "ram": ("RAM Laptop", "🧠"),
    "ssd": ("SSD", "💾"),
    "hdd": ("HDD", "🗄️"),
    "vga": ("VGA - Card màn hình", "🎮"),
    "mainboard": ("Mainboard", "🔌"),
    "psu": ("PSU - Nguồn máy tính", "🔋"),
    "monitor": ("Màn hình", "🖥️"),
}
TYPE_ORDER = ["laptop", "ram", "ssd", "hdd", "vga", "mainboard", "psu", "monitor"]


def _render_offer_rows(cat, color):
    """The source line + product-rows table for one (retailer, category)
    pairing - i.e. one retailer's offers within a type section."""
    spec_cols = SPEC_EXTRACTORS.get(cat["key"], (None, []))[1]
    if not cat["items"]:
        body = (
            f"<div style='font-size:13px;color:#6b7280;padding:8px 0 4px;'>"
            f"Không lấy được sản phẩm nào trong lần chạy này. Xem trực tiếp tại "
            f"<a href='{escape(cat['url'])}' style='color:{color};'>{escape(cat['url'])}</a>.</div>"
        )
    else:
        rows = []
        for item in cat["items"]:
            tags_html = _spec_tags_html(item, spec_cols)
            tags_block = f"<div>{tags_html}</div>" if tags_html else ""
            trend_block = _trend_html(item)
            # Fall back to the category page itself if no product-specific
            # link was found for this item, so the row is still clickable
            # rather than dead.
            link = item.get("product_url") or cat["url"]
            link_open = f"<a href='{escape(link)}' style='text-decoration:none;color:inherit;' target='_blank'>"
            link_close = "</a>"
            rows.append(
                f"<tr>"
                f"<td style='padding:10px 6px;border-bottom:1px solid #f1f3f5;width:60px;vertical-align:top;'>"
                f"{link_open}{_thumb_html(item)}{link_close}</td>"
                f"<td style='padding:10px 6px;border-bottom:1px solid #f1f3f5;vertical-align:top;'>"
                f"{link_open}<div style='font-size:13.5px;font-weight:600;color:#1f2937;line-height:1.35;'>"
                f"{escape(item['name'])}</div>{link_close}{tags_block}{trend_block}</td>"
                f"<td style='padding:10px 6px;border-bottom:1px solid #f1f3f5;vertical-align:top;text-align:right;'>"
                f"{link_open}{_price_block_html(item['price'], item['old_price'])}{link_close}</td>"
                f"</tr>"
            )
        body = (
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            "style='border-collapse:collapse;'>" + "".join(rows) + "</table>"
        )
    return (
        f"<div style='margin:16px 0 4px;padding:12px 14px 4px;background:#fafbfc;"
        f"border:1px solid #f0f1f3;border-radius:10px;'>"
        f"<div style='display:block;'>"
        f"<span style='font-size:13px;font-weight:700;color:{color};'>{escape(cat['site_label'])}</span>"
        f"<span style='font-size:11px;color:#9ca3af;margin-left:8px;'>"
        f"<a href='{escape(cat['url'])}' style='color:#9ca3af;'>{escape(cat['url'])}</a></span>"
        f"</div>"
        f"{body}"
        f"</div>"
    )


def build_html(categories_data, timestamp):
    # Assign each retailer a stable color, in the order it first appears,
    # regardless of how sections get grouped below.
    site_colors = {}
    for cat in categories_data:
        if cat["site"] not in site_colors:
            site_colors[cat["site"]] = SITE_COLORS[len(site_colors) % len(SITE_COLORS)]

    # Group by item TYPE first (ram/ssd/laptop), not by retailer, so
    # offers for the same kind of item from every retailer sit together -
    # much easier to eyeball-compare than scrolling through each
    # retailer's full catalog separately. Within a type, retailers appear
    # in the order they were fetched.
    types_present = []
    by_type = {}
    for cat in categories_data:
        key = cat["key"]
        if key not in by_type:
            by_type[key] = []
            types_present.append(key)
        by_type[key].append(cat)
    ordered_types = [t for t in TYPE_ORDER if t in by_type] + [
        t for t in types_present if t not in TYPE_ORDER
    ]

    type_blocks = []
    for type_key in ordered_types:
        label, icon = TYPE_META.get(type_key, (type_key.title(), "🛒"))
        offer_blocks = "".join(
            _render_offer_rows(cat, site_colors[cat["site"]]) for cat in by_type[type_key]
        )
        type_blocks.append(
            f"<tr><td style='padding:26px 28px 0;'>"
            f"<div style='font-size:18px;font-weight:800;color:#111827;'>{icon} {escape(label)}</div>"
            f"<div style='height:2px;background:#eef0f3;margin-top:10px;'></div>"
            f"{offer_blocks}"
            f"</td></tr>"
        )

    return f"""\
<html>
<body style="margin:0;padding:0;background:#eef1f5;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef1f5;padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;background:#ffffff;border-radius:16px;overflow:hidden;">
<tr><td style="background-color:#4338CA;background-image:linear-gradient(135deg,#4338CA,#6366F1);padding:30px 28px;">
<div style="font-size:21px;font-weight:700;color:#ffffff;">💻 Bảng giá RAM · SSD · Laptop</div>
<div style="font-size:13px;color:#E0E7FF;margin-top:6px;">Cập nhật {escape(timestamp)}</div>
</td></tr>
{''.join(type_blocks)}
<tr><td style="padding:22px 28px;background:#f8fafc;border-top:1px solid #eef0f3;margin-top:20px;">
<div style="font-size:11px;color:#94a3b8;line-height:1.6;">
Đây là giá niêm yết tại các cửa hàng ở thời điểm quét, không phải giá thị
trường trung bình. Mỗi mục vẫn là giá riêng của từng cửa hàng, đặt cạnh
nhau để tiện so sánh, không phải một chỉ số thị trường thống nhất. Email
tự động, chỉ mang tính tham khảo, không phải lời khuyên mua hàng - vui
lòng kiểm tra lại giá trên website trước khi đặt hàng.
</div>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def build_plain_text(categories_data, timestamp):
    lines = [f"Gia RAM/SSD/Laptop - cap nhat {timestamp}", ""]

    types_present = []
    by_type = {}
    for cat in categories_data:
        key = cat["key"]
        if key not in by_type:
            by_type[key] = []
            types_present.append(key)
        by_type[key].append(cat)
    ordered_types = [t for t in TYPE_ORDER if t in by_type] + [
        t for t in types_present if t not in TYPE_ORDER
    ]

    for type_key in ordered_types:
        label, _icon = TYPE_META.get(type_key, (type_key.title(), ""))
        lines.append(f"########## {label} ##########")
        lines.append("")
        for cat in by_type[type_key]:
            spec_cols = SPEC_EXTRACTORS.get(cat["key"], (None, []))[1]
            lines.append(f"== {cat['site_label']} ({cat['url']}) ==")
            if not cat["items"]:
                lines.append(" Could not parse any items this run.")
            else:
                for item in cat["items"]:
                    specs = item.get("specs", {})
                    spec_str = ", ".join(f"{col}: {specs.get(col, '—')}" for col in spec_cols)
                    price_str = _price_text(item["price"], item["old_price"])
                    lines.append(f" - {item['name']}")
                    lines.append(f"   {spec_str} | Gia: {price_str}")
                    trend = item.get("trend") or {}
                    if trend:
                        trend_str = ", ".join(
                            f"{label}: {'+' if trend[label] > 0 else ''}{trend[label]:g}%"
                            for label, _days in TREND_WINDOWS
                            if trend.get(label) is not None
                        )
                        if trend_str:
                            lines.append(f"   Bien dong gia: {trend_str}")
                    if item.get("product_url"):
                        lines.append(f"   Link: {item['product_url']}")
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
        rendered_html = None
        try:
            items, rendered_html = fetch_category(
                cat["key"], cat["url"], needs_browser=cat.get("needs_browser", False), return_html=True
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
            if cat.get("needs_browser") and rendered_html:
                print(diagnose_empty_render(rendered_html), file=sys.stderr)
        categories_data.append({**cat, "items": items})
        total_items += len(items)

    if total_items == 0 and had_fetch_error:
        print("All categories failed to fetch. Aborting without sending.", file=sys.stderr)
        sys.exit(1)

    if total_items:
        price_history = load_price_history()
        today = resolve_timestamp()[0].date()
        for cat in categories_data:
            update_history_and_get_trends(price_history, cat["site"], cat["key"], cat["items"], today)
        save_price_history(price_history)

    price_hash = hash_data(
        [
            {
                "site": c["site"],
                "label": c["label"],
                "items": [
                    {"name": it["name"], "price": it["price"], "old_price": it["old_price"]}
                    for it in c["items"]
                ],
            }
            for c in categories_data
        ]
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
