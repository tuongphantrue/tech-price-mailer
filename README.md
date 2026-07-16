# RAM / SSD / Laptop Price Emailer (runs on GitHub Actions, no local computer needed)

Emails you a digest of current RAM, SSD, and laptop listing prices at
**MemoryZone.vn**, **HACOM.vn** (Hanoi-headquartered, showrooms across
Hanoi), and **Phong Vũ (phongvu.vn)**, automatically, via GitHub's free
scheduled-workflow runners.

Modeled on [gold-price-emailer](https://github.com/tuongphantrue/gold-price-emailer) and [house-price-emailer](https://github.com/tuongphantrue/house-price-emailer) -
same generate/send two-phase shape, same Gmail-SMTP delivery, same
dedup-via-state-branch trick.

## Important: read this before relying on it

Vietnamese gold prices have a clean daily aggregator site (giavang.org)
with one simple table per seller. **RAM/SSD/laptop prices don't have a
real equivalent.** There's no public site that publishes a clean,
structured, frequently-updated "market price" table for computer
components the way giavang.org does for gold, or even the way Mogi.vn's
blended table does for housing.

What this script does instead is scrape the *listing prices* directly off
each retailer's own category pages - RAM Laptop, SSD, and Laptop - for:

- [MemoryZone.vn](https://memoryzone.com.vn/)
- [HACOM.vn](https://hacom.vn/)
- [Phong Vũ](https://phongvu.vn/)

These are **each store's current asking prices** (often already
discounted), not a market average. The email lays all three retailers'
listings out side by side, section by section, so you can eyeball and
compare them yourself - it is **not** a single blended price index across
retailers. Treat the email as "what each store is charging right now for
the items on page 1 of each category," not as an authoritative price
index - always check the live page before buying anything.

The parser (`parse_listing()` in the script) matches by *text adjacency* -
a product-name-looking line immediately followed by a "X.XXX.XXX ₫"
price line - rather than by exact HTML structure, so it should survive
minor theme/markup changes.

**MemoryZone** server-renders its product grid, so a plain HTTP GET
already contains the data - fast, no browser needed.

**HACOM** does not: it runs on Next.js, and the product grid is fetched
by client-side JS after the page loads, so a plain GET always sees an
empty grid. This script renders HACOM's category pages in a real headless
Chromium tab (via [Playwright](https://playwright.dev/)) first, then
parses the resulting HTML the same way.

**Phong Vũ** is attempted the same headless-browser way, but that site
also runs active bot detection that can block even a real browser - if
so, you'll see a fetch/render error in the logs for that category (not a
silent "0 items parsed"). This script does not attempt to defeat that
detection (no fingerprint spoofing, proxies, CAPTCHA solving, etc.) - if
it keeps failing, the practical option is to drop Phong Vũ from
`ENABLED_RETAILERS`.

If a run reports 0 parsed items for a retailer/category, open the
category URL and check `parse_listing()`, and the `JUNK_NAME_PREFIXES` /
`JUNK_NAME_RE` filters near the top of the file (tuned by hand per site's
nav/filter text, so a redesign may need a line or two added there).

## One-time setup (~5 minutes)

1. **Create a GitHub account** if you don't have one: <https://github.com/join>

2. **Create a new repository**

   - Click "+" (top right) -> "New repository"
   - Name it anything, e.g. `ram-ssd-laptop-price-emailer`
   - Set it to **Private** (recommended, keeps your workflow config private)
   - Click "Create repository"

3. **Upload these files** to the repo (drag-and-drop works fine via the
   GitHub web UI: "Add file" -> "Upload files"), keeping the folder structure:

   - `ram_ssd_laptop_price_emailer.py`
   - `requirements.txt`
   - `.github/workflows/send-tech-price.yml`

4. **Create a Gmail "App Password"** (your normal Gmail password won't work):

   - Turn on 2-Step Verification: <https://myaccount.google.com/signinoptions/two-step-verification>
   - Then create an app password: <https://myaccount.google.com/apppasswords>
   - Choose "Mail" as the app, copy the 16-character password it gives you.

5. **Add your secrets to the repo** (this keeps your email/password out of the code):

   - In your repo: Settings -> Secrets and variables -> Actions -> "New repository secret"
   - Add three secrets:
     * `GMAIL_ADDRESS` = your Gmail address
     * `GMAIL_APP_PASSWORD` = the 16-character app password from step 4
     * `TECH_RECIPIENT` = the email address that should receive the price update

6. **Test it manually**

   - Go to the "Actions" tab in your repo
   - Click "Send RAM/SSD/Laptop Price Email" on the left
   - Click "Run workflow" -> "Run workflow" (green button)
   - Wait **1-3 minutes** (installing the Chromium browser for HACOM/Phong
     Vũ, on top of rendering 6 categories through it, takes noticeably
     longer than the original MemoryZone-only version), refresh, click
     into the run to see logs / confirm success
   - Check the logs for each retailer/category line and confirm none of
     them say "0 items parsed" or show a render error - if one does, see
     the note above about `parse_listing()` (HACOM) or dropping the
     retailer from `ENABLED_RETAILERS` (Phong Vũ, if it's the bot
     detection)
   - Check the recipient inbox for the email

That's it - from now on it runs automatically on the schedule below.

## Changing the schedule

Open `.github/workflows/send-tech-price.yml` and edit this line:

```
- cron: "*/30 * * * *"
```

Cron format is `minute hour day month weekday`, always in **UTC**.

- `*/30 * * * *` -> every 30 minutes - current setting
- `0 1 * * *` -> once a day at 1am UTC (8am Vietnam, UTC+7)
- `0 1 * * 1` -> once a week, Monday 1am UTC
- `0 */6 * * *` -> every 6 hours

Retail listing prices don't move nearly as often as gold, so daily or
weekly is probably plenty - and keeps `SEND_ONLY_ON_CHANGE` (below) doing
useful work instead of just discarding runs. It's also worth considering
now that two of the three retailers render through a headless browser:
every-30-minutes means Chromium gets installed and six category pages get
rendered every half hour, which eats into your Actions minutes faster
than the original MemoryZone-only version did.

## Only emailing on price changes

Currently `SEND_ONLY_ON_CHANGE` is `"true"` in the workflow's "Generate
email" step. With that on, `generate` hashes the freshly scraped prices
across all retailers, compares against a hash saved from the last run -
stored in `state/last_price.json` on a dedicated `tech-price-state` branch
the workflow creates/updates automatically - and skips the email if
nothing changed anywhere. Set it to `"false"` if you'd rather get an email
on every scheduled run regardless.

## Choosing which retailers get scraped

By default all three retailers run. To narrow it down, add an
`ENABLED_RETAILERS` environment variable to the "Generate email" step in
the workflow, as a comma-separated list of `memoryzone`, `hacom`,
`phongvu`. For example, to skip MemoryZone and only check the two
Hanoi-heavy retailers:

```
ENABLED_RETAILERS: "hacom,phongvu"
```

## Configuring which pages get scraped

These are optional environment variables you can add to the "Generate
email" step in the workflow if you want different category pages or a
different number of items. Each retailer has its own RAM/SSD/Laptop URL
variables:

```
# MemoryZone
MEMORYZONE_RAM_URL: "https://memoryzone.com.vn/ram-laptop"     # or e.g. .../ram-pc
MEMORYZONE_SSD_URL: "https://memoryzone.com.vn/ssd"
MEMORYZONE_LAPTOP_URL: "https://memoryzone.com.vn/laptop"

# HACOM
HACOM_RAM_URL: "https://hacom.vn/ram-laptop"
HACOM_SSD_URL: "https://hacom.vn/o-cung-ssd"
HACOM_LAPTOP_URL: "https://hacom.vn/laptop"

# Phong Vu
PHONGVU_RAM_URL: "https://phongvu.vn/c/ram-laptop"
PHONGVU_SSD_URL: "https://phongvu.vn/c/o-cung-ssd"
PHONGVU_LAPTOP_URL: "https://phongvu.vn/c/laptop"

MAX_ITEMS_PER_CATEGORY: "12"   # applies per retailer per category
```

For backward compatibility, the original `RAM_URL` / `SSD_URL` /
`LAPTOP_URL` variables still work and apply to MemoryZone specifically, so
existing workflows that already set those don't need to change anything.

## Notes

- HACOM and Phong Vũ need a real headless browser (Chromium via
  Playwright) since their product grids are client-rendered by JS rather
  than present in the initial HTML. This makes each run slower and uses
  more Actions minutes than the original MemoryZone-only version - see
  the schedule note above if you're on the free tier's 2,000 min/month.
- The workflow needs write access to push its dedup state branch. It
  requests this itself (`permissions: contents: write` at the top of
  `send-tech-price.yml`), but some accounts/orgs override that and force
  the token to read-only regardless. If the "Persist dedup state to state
  branch" step fails with `403` / `Permission ... denied` / `exit code 128`, go to **Settings -> Actions -> General -> Workflow permissions** in your repo and select **"Read and write permissions"**, then re-run
  the workflow.
- GitHub Actions free tier includes 2,000 minutes/month for private repos.
- You can also trigger it manually anytime via the "Run workflow" button.
- If the run fails, check the Actions tab -> the failed run -> logs. Common
  causes: a secret is missing/misspelled, the Gmail app password was
  revoked, or one of the three sites changed its page markup (see below).
- If a run reports "0 items parsed" for a retailer/category, that site's
  HTML structure probably changed. Open the relevant category URL, check
  that product cards still show a name directly above/near a "X.XXX.XXX ₫"
  price, and adjust `parse_listing()` (and possibly `JUNK_NAME_PREFIXES` /
  `JUNK_NAME_RE`) in `ram_ssd_laptop_price_emailer.py` to match.
- Always worth checking each site's current `robots.txt` / terms before
  running this unattended long-term:
  <https://memoryzone.com.vn/robots.txt>,
  <https://hacom.vn/robots.txt>,
  <https://phongvu.vn/robots.txt>
- This is a personal price-watch tool, not investment or purchase advice.

## Running locally instead

```
pip install -r requirements.txt
python -m playwright install --with-deps chromium   # one-time, for HACOM/Phong Vu
export GMAIL_ADDRESS="you@gmail.com"
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
export TECH_RECIPIENT="you@gmail.com"
python ram_ssd_laptop_price_emailer.py generate
python ram_ssd_laptop_price_emailer.py send
```

Schedule it yourself with cron (`crontab -e`):

```
0 1 * * * cd /path/to/ram-ssd-laptop-price-emailer && /usr/bin/python3 ram_ssd_laptop_price_emailer.py generate && /usr/bin/python3 ram_ssd_laptop_price_emailer.py send >> tech_emailer.log 2>&1
```
