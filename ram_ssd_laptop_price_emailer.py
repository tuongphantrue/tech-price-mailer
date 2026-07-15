#!/usr/bin/env python3
"""
RAM / SSD / Laptop Prices (MemoryZone.vn) -> Email
(runs on GitHub Actions, no local computer needed)

Same shape as the gold-price-emailer / house-price-emailer this is modeled
on: fetches price data, then emails an HTML digest via Gmail SMTP. Runs in
two phases so the workflow can persist dedup state *between* them (see the
accompanying GitHub Actions workflow):

    python ram_ssd_laptop_price_emailer.py generate
        -> scrapes the three category pages, writes the composed email
           (subject/html/text) under ./email/, and updates the
           "last sent price" state file

    python ram_ssd_laptop_price_emailer.py send
        -> reads ./email/* and sends it via Gmail SMTP

SOURCE & AN IMPORTANT CAVEAT
-----------------------------
Vietnamese gold prices have a clean daily aggregator (giavang.org) with one
simple table per seller. RAM/SSD/laptop prices don't have a real
equivalent: there's no single site that publishes a clean, structured,
frequently-updated "market price" table the way giavang.org does for gold.

What this script does instead is scrape the live *listing prices* off one
retailer's category pages: MemoryZone.vn's "RAM Laptop", "SSD", and
"Laptop" pages. These are one store's asking prices (often with an active
discount), NOT a market-average and NOT a price-comparison across
retailers. Treat this as "what MemoryZone is currently charging for the
items on page 1 of each category", not as an authoritative RAM/SSD/laptop
market index.

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
   pip install requests beautifulsoup4 certifi

2. Create a Gmail "App Password" (regular Gmail passwords won't work with SMTP):
   - Go to https://myaccount.google.com/apppasswords
   - You need 2-Step Verification turned on first.
   - Create an app password for "Mail" and copy the 16-character code.

3. Set these as environment variables (see README.md for GitHub Actions
   secrets instead, if running in the cloud):

   export GMAIL_ADDRESS="youraddress@gmail.com"
   export GMAIL_APP_PASSWORD="16-char-app-password"
   export TECH_RECIPIENT="where-to-send@example.com"
   export SEND_ONLY_ON_CHANGE="false"                     # optional, default false
   export TIMEZONE="Asia/Ho_Chi_Minh"                      # optional, for the subject line
   export RAM_URL="https://memoryzone.com.vn/ram-laptop"   # optional
   export SSD_URL="https://memoryzone.com.vn/ssd"          # optional
   export LAPTOP_URL="https://memoryzone.com.vn/laptop"    # optional
   export MAX_ITEMS_PER_CATEGORY="12"                      # optional
   export STATE_FILE="state/last_price.json"               # optional, dedup state file
   export ALLOW_INSECURE_SSL_FALLBACK="false"               # optional, last-resort TLS bypass

NOTE ON SCRAPING
-----------------
Always worth checking the current robots.txt / terms of whatever site this
is pointed at before running it unattended long-term, e.g.:
https://memoryzone.com.vn/robots.txt

The page markup can change at any time - if `generate` reports 0 parsed
items for a category, open that category's URL and inspect the product
cards, then update parse_listing() below. It matches by *text adjacency*
(product name line immediately followed by a "X.XXX.XXX ₫" price line),
not by exact HTML structure, which should make it reasonably resilient -
but no guarantees, and it can't distinguish "in stock" from "sold out"
items or catch prices rendered only after JavaScript runs.

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

CATEGORIES = [
    {"key": "ram", "label": "RAM Laptop", "url": os.environ.get("RAM_URL", "https://memoryzone.com.vn/ram-laptop")},
    {"key": "ssd", "label": "SSD", "url": os.environ.get("SSD_URL", "https://memoryzone.com.vn/ssd")},
    {"key": "laptop", "label": "Laptop", "url": os.environ.get("LAPTOP_URL", "https://memoryzone.com.vn/laptop")},
]

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

# Matches Vietnamese-formatted currency like "1.990.000 ₫" (dot as thousands
# separator, ₫ as the currency mark). A listing/discount line often has two
# of these back to back: "1.990.000 ₫ 2.350.000 ₫" (sale price, then the
# crossed-out original price).
PRICE_RE = re.compile(r"([\d]{1,3}(?:\.[\d]{3})+)\s*\u20ab")

# Lines that are clearly chrome/navigation/filters, not product names -
# skip these even if a price happens to follow within lookahead range.
JUNK_NAME_PREFIXES = (
    "trang chủ", "giỏ hàng", "tài khoản", "đăng nhập", "so sánh",
    "sắp xếp", "thứ tự", "lọc giá", "bỏ hết", "xem thêm", "hãng sản xuất",
)

# Review/stock/discount-badge lines that sit between one product's price
# and the next product's name - must be excluded from candidacy or the
# next price gets paired with a review sentence instead of a product name.
JUNK_NAME_RE = re.compile(
    r"^(là người đánh giá đầu tiên|xem \d+ đánh giá|-?\d+\s*%|hết hàng|"
    r"chỉ bán build pc|còn hàng)",
    re.IGNORECASE,
)


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
        print(f"  could not read {path} ({e}) - starting with empty dedup state", file=sys.stderr)
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
        print(f"  TLS verification failed with certifi's CA bundle: {e}", file=sys.stderr)
        if not ALLOW_INSECURE_SSL_FALLBACK:
            print(
                "  Set ALLOW_INSECURE_SSL_FALLBACK=true to retry without verification "
                "as a last resort.",
                file=sys.stderr,
            )
            raise
        print("  ALLOW_INSECURE_SSL_FALLBACK=true - retrying with TLS verification disabled.", file=sys.stderr)
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        resp.raise_for_status()
        return resp.text


def parse_listing(html, max_items=MAX_ITEMS_PER_CATEGORY):
    """
    Parse a MemoryZone.vn category page into a list of
    {name, price, old_price} rows (old_price is None if not on sale).

    The page isn't cleanly separated in the DOM in an obvious way we can
    rely on long-term (product-card class names on storefront templates
    change with theme updates), so rather than depend on exact structure,
    this walks the page's flattened text and looks for a plausible product
    name line immediately followed - within a couple of lines - by a
    "X.XXX.XXX ₫" price line. This is more resilient to markup changes
    than a strict DOM walk, at the cost of being a bit more heuristic - if
    a run parses 0 items, open the category URL and check product cards
    still show the name directly above the price.
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


def fetch_category(url, max_items=MAX_ITEMS_PER_CATEGORY):
    html = fetch_page(url)
    return parse_listing(html, max_items=max_items)


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
    sections = []
    for cat in categories_data:
        if not cat["items"]:
            body = (
                f"<p>Could not parse any items this run. "
                f"Check <a href='{escape(cat['url'])}'>{escape(cat['url'])}</a> directly.</p>"
            )
        else:
            row_html = "\n".join(
                f"<tr>"
                f"<td style='padding:6px 12px;border-bottom:1px solid #eee'>{escape(item['name'])}</td>"
                f"<td style='padding:6px 12px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap'>"
                f"{_price_html(item['price'], item['old_price'])}</td>"
                f"</tr>"
                for item in cat["items"]
            )
            body = f"""
<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;max-width:640px;font-family:Arial,Helvetica,sans-serif;font-size:14px;">
<thead>
<tr style="background:#f5f5f5;">
<th style="padding:8px 12px;text-align:left;">Sản phẩm</th>
<th style="padding:8px 12px;text-align:right;">Giá</th>
</tr>
</thead>
<tbody>
{row_html}
</tbody>
</table>"""
        sections.append(
            f"<h2 style='color:#1a5fb4;margin-top:28px;'>{escape(cat['label'])}</h2>"
            f"<p style='color:#999;font-size:12px;margin:4px 0 8px;'>"
            f"Nguồn: <a href='{escape(cat['url'])}'>{escape(cat['url'])}</a></p>"
            f"{body}"
        )

    return f"""\
<html>
<body style="margin:0; padding:20px; background:#f4f4f4; font-family:Arial,Helvetica,sans-serif;">
<h1 style="color:#1a5fb4;">Giá RAM / SSD / Laptop hôm nay</h1>
<p style="color:#555;">Cập nhật {escape(timestamp)}</p>
{''.join(sections)}
<p style="color:#999; font-size:12px; margin-top:24px;">
Đây là giá niêm yết tại một cửa hàng (MemoryZone.vn) tại thời điểm quét,
không phải giá thị trường trung bình hay so sánh nhiều nhà bán · Email tự
động, chỉ mang tính tham khảo, không phải lời khuyên mua hàng - vui lòng
kiểm tra lại giá trên website trước khi đặt hàng.
</p>
</body>
</html>"""


def build_plain_text(categories_data, timestamp):
    lines = [f"Gia RAM/SSD/Laptop - cap nhat {timestamp}", ""]
    for cat in categories_data:
        lines.append(f"== {cat['label']} ({cat['url']}) ==")
        if not cat["items"]:
            lines.append("  Could not parse any items this run.")
        else:
            for item in cat["items"]:
                lines.append(f"  - {item['name']}: {_price_text(item['price'], item['old_price'])}")
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

    categories_data = []
    total_items = 0
    had_fetch_error = False
    for cat in CATEGORIES:
        print(f"Fetching {cat['label']} ({cat['url']}) ...")
        try:
            items = fetch_category(cat["url"])
        except requests.RequestException as e:
            print(f"  Failed to fetch {cat['url']}: {e}", file=sys.stderr)
            had_fetch_error = True
            items = []
        print(f"  Parsed {len(items)} item(s).")
        if not items:
            print(
                f"  0 items parsed for {cat['label']} - the page markup may have changed. "
                f"Open {cat['url']} and check parse_listing().",
                file=sys.stderr,
            )
        categories_data.append({**cat, "items": items})
        total_items += len(items)

    if total_items == 0 and had_fetch_error:
        print("All categories failed to fetch. Aborting without sending.", file=sys.stderr)
        sys.exit(1)

    price_hash = hash_data([{"label": c["label"], "items": c["items"]} for c in categories_data])
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
    print(f"Generated email ({total_items} item(s) total). Saved to ./{EMAIL_DIR}/")


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
