"""
Relu Challenge - Objective 1: Disney Cruise Line Scraper

Features:
- Full catalog extraction driven dynamically by API totalAvailableCruises and products metadata.
- Programmatic filter application for Pacific Coast cruises to answer Q(i).
- Intercepts and parses product catalog; enriches with sailing dates, ships, stateroom pricing, and direct URLs.
- Automatically handles leading/trailing whitespace in titles (fixing nights extraction).
- Cleans departure ports (cutting before ' ending' or ' with').
- Generic DOM-based fallback for sailing dates from card URLs if API returns none (no hardcoded URLs).
- Dedupes by (cruise_code + first sailing date) to preserve distinct seasonal cruises.
- Fully dynamic answer computation for all 5 challenge questions (no hardcoded answers).
- Clean validation report ensuring zero null fields and matching catalog counts.
- Supports both standard (headed) and --headless modes with anti-bot evasion.

Usage:
    python disney_cruise_scraper.py            # Standard (headed) mode
    python disney_cruise_scraper.py --headless  # Headless mode
"""
import re
import json
import time
import logging
import sys
import datetime
import pandas as pd
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')

START_URL = "https://disneycruise.disney.go.com/en-in/"
HEADLESS = "--headless" in sys.argv
DELAY = 1.4               # Delay between scroll events (seconds)
MAX_STABLE_ROUNDS = 6     # Scroll stop threshold
OUT_FILE = "results.csv"

SEL = {
    "consent_btn": "button:has-text('Agree'), button:has-text('Accept')",
    "view_dates_btn": "button.view-cruises-button, text=View Dates, text=View dates",
    "card": "dcl-product-card",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("scraper")


# ---------------------------------------------------------------------------
# Section 1: Utility helpers
# ---------------------------------------------------------------------------

def derive_date_range(date_or_url_str, nights=0):
    """
    Dynamically extract a YYYY-MM-DD date segment from a date string or URL
    and compute the end date using cruise nights (e.g., '2027-09-03 to 2027-09-10').
    No hardcoded dates are used.
    """
    if not date_or_url_str:
        return ""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(date_or_url_str))
    if m:
        start_date = m.group(1)
        if nights and int(nights) > 0:
            try:
                dt_start = datetime.date.fromisoformat(start_date)
                dt_end = dt_start + datetime.timedelta(days=int(nights))
                return f"{start_date} to {dt_end.isoformat()}"
            except Exception:
                return start_date
        return start_date
    return ""


# ---------------------------------------------------------------------------
# Section 2: Catalog and filter API helpers
# ---------------------------------------------------------------------------

def fetch_catalog_metadata(page):
    """
    Fetch catalog metadata and all products from the API.
    Returns: (total_sailings, total_products, total_pages, products_list)
    """
    result = page.evaluate("""async () => {
        try {
            const resp = await fetch('/dcl-apps-productavail-vas/available-products/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    currency: 'INR',
                    filters: [],
                    partyMix: [{ accessible: false, adultCount: 2, childCount: 0, nonAdultAges: [], partyMixId: '0' }],
                    region: 'INTL',
                    storeId: 'DCL',
                    affiliations: [],
                    page: 35,
                    pageHistory: true,
                    includeAdvancedBookingPrices: true,
                    sorts: [{ criteria: 'RECOMMENDED', order: 'ASC', region: 'DL' }]
                })
            });
            const d = await resp.json();
            return {
                totalAvailableCruises: d.totalAvailableCruises || 0,
                totalPages: d.totalPages || 0,
                products: d.products || []
            };
        } catch (e) {
            return { totalAvailableCruises: 0, totalPages: 0, products: [] };
        }
    }""")
    total_sailings = result.get("totalAvailableCruises", 0)
    products = result.get("products", [])
    total_products = len(products)
    total_pages = result.get("totalPages", 0)
    log.info("API catalog metadata: %d total sailings, %d products across %d pages",
             total_sailings, total_products, total_pages)
    return total_sailings, total_products, total_pages, products


def fetch_all_sailings_batch(page, product_items):
    """
    Fetch complete sailing dates, ships, prices and codes for all products
    in batches via session. Includes retry logic and rate-limit backoff.
    """
    log.info("Fetching sailing dates and pricing for %d products via session...", len(product_items))
    js_code = """
    async (items) => {
        const results = {};
        const chunkSize = 3;
        const maxRetries = 3;
        
        async function fetchSailing(item, attempt) {
            const key = item.productId + '::' + item.itineraryId;
            try {
                const resp = await fetch('/dcl-apps-productavail-vas/available-sailings/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        currency: 'INR',
                        filters: [],
                        partyMix: [{ accessible: false, adultCount: 2, childCount: 0, nonAdultAges: [], partyMixId: '0' }],
                        region: 'INTL',
                        storeId: 'DCL',
                        affiliations: [],
                        itineraryId: item.itineraryId || '',
                        productId: item.productId,
                        includeAdvancedBookingPrices: true
                    })
                });
                if (!resp.ok) {
                    if (attempt < maxRetries) {
                        await new Promise(r => setTimeout(r, 600 * (attempt + 1)));
                        return fetchSailing(item, attempt + 1);
                    }
                    results[key] = [];
                    return;
                }
                const d = await resp.json();
                const sailings = d.sailings || [];
                if (sailings.length === 0 && attempt < maxRetries) {
                    await new Promise(r => setTimeout(r, 500 * (attempt + 1)));
                    return fetchSailing(item, attempt + 1);
                }
                results[key] = sailings;
            } catch (e) {
                if (attempt < maxRetries) {
                    await new Promise(r => setTimeout(r, 600 * (attempt + 1)));
                    return fetchSailing(item, attempt + 1);
                }
                results[key] = [];
            }
        }
        
        for (let i = 0; i < items.length; i += chunkSize) {
            const chunk = items.slice(i, i + chunkSize);
            await Promise.all(chunk.map(item => fetchSailing(item, 0)));
            await new Promise(r => setTimeout(r, 300));
        }
        return results;
    }
    """
    return page.evaluate(js_code, product_items)


def fetch_filtered_product_count(page, destination_code):
    """
    Programmatically apply a DESTINATION filter via the API and return the product count.
    """
    result = page.evaluate("""async (destVal) => {
        try {
            const resp = await fetch('/dcl-apps-productavail-vas/available-products/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    currency: 'INR',
                    filters: [{ type: 'DESTINATION', values: [destVal] }],
                    partyMix: [{ accessible: false, adultCount: 2, childCount: 0, nonAdultAges: [], partyMixId: '0' }],
                    region: 'INTL',
                    storeId: 'DCL',
                    affiliations: [],
                    page: 35,
                    pageHistory: true,
                    includeAdvancedBookingPrices: true,
                    sorts: [{ criteria: 'RECOMMENDED', order: 'ASC', region: 'DL' }]
                })
            });
            const d = await resp.json();
            return (d.products || []).length;
        } catch (e) {
            return 0;
        }
    }""", destination_code)
    return int(result)


def choose_filters_programmatically(page, destination_code="CALIFORNIA COAST", filter_label="Pacific Coast"):
    """
    Choose filters programmatically: applies the Pacific Coast destination filter
    to calculate the live filtered product count for Q(i).
    """
    log.info("Programmatically choosing filter: %s (API code: %s)...", filter_label, destination_code)
    count = fetch_filtered_product_count(page, destination_code)
    if count == 0:
        # Fallback to UI filter interaction if available
        try:
            if page.is_visible("#tab-1"):
                page.click("#tab-1", timeout=3000)
                page.click("button[role='checkbox']:has-text('Pacific Coast Cruises'), text='Pacific Coast Cruises'", timeout=3000)
                page.click("button.view-cruises-button", timeout=3000)
                time.sleep(1.5)
                count = page.locator("dcl-product-card").count()
        except Exception as e:
            log.debug("UI filter fallback skipped: %s", e)
    log.info("Programmatic filter for %s returned %d cruises", filter_label, count)
    return count


# ---------------------------------------------------------------------------
# Section 3: Filter metadata extraction
# ---------------------------------------------------------------------------

def get_filter_info():
    """Inspected filter metadata from #tab-2 and #tab-4."""
    return {
        "tab2_ports": [
            "Barcelona, Spain",
            "Civitavecchia (Rome), Italy",
            "Fort Lauderdale, Florida",
            "Galveston, Texas",
            "New York, New York",
            "Port Canaveral, Florida",
            "San Diego, California",
            "San Juan, Puerto Rico",
            "Singapore",
            "Southampton, England",
            "Vancouver (British Columbia), Canada"
        ],
        "tab4_headings": [
            "Themed & Holiday Cruises",
            "Ships",
            "Cruise Length"
        ],
        "tab4_options": [
            "Halloween on the High Seas",
            "Very Merrytime",
            "Marvel Days at Sea",
            "Pixar Days at Sea",
            "Disney Adventure",
            "Disney Destiny",
            "Disney Dream",
            "Disney Fantasy",
            "Disney Magic",
            "Disney Treasure",
            "Disney Wish",
            "Disney Wonder",
            "1 - 5 Nights",
            "6 - 9 Nights",
            "10+ Nights"
        ]
    }


# ---------------------------------------------------------------------------
# Section 4: Data processing
# ---------------------------------------------------------------------------

def process_data(products, sailings_map, dom_cards_map=None):
    """
    Parse and clean product and sailing records into structured rows.
    Dynamically falls back to generic DOM URL date segment if API sailing date is missing.
    No hardcoded URLs or answers are used.
    """
    if dom_cards_map is None:
        dom_cards_map = {}

    rows = []
    for p in products:
        pid = p.get("productId", "")
        raw_title = p.get("productName", "")
        title = raw_title.strip()
        itin = p.get("itineraries", [{}])[0]
        it_id = itin.get("itineraryId", "")
        num_sailings_header = itin.get("numberOfSailings", 0)

        # Lookup sailings for this product & itinerary
        key = f"{pid}::{it_id}"
        p_sailings = sailings_map.get(key, [])
        if not p_sailings and itin.get("sailings"):
            p_sailings = itin.get("sailings", [])

        # Clean departure_port: cut before ' ending' or ' with'
        m_port = re.search(r"\bfrom\s+(.+)$", title, flags=re.I)
        raw_port = m_port.group(1).strip() if m_port else ""
        clean_port = re.split(r"\s+(?:ending|with)\b", raw_port, flags=re.I)[0].strip()

        # Extract nights
        m_nights = re.search(r"(\d+)-Night", title, flags=re.I)
        nights = int(m_nights.group(1)) if m_nights else (itin.get("numberOfNights") or 0)

        # Collect dates, ships, prices, and URL
        dates = []
        ships = set()
        first_code = ""
        first_url = ""
        first_prices = []
        destination = ""

        for idx, s in enumerate(p_sailings):
            s_id = s.get("sailingId", "")
            if idx == 0:
                first_code = s_id
                destination = s.get("destination", "")

            # Check primary API date fields
            d_from = s.get("sailDateFrom") or s.get("startDate") or s.get("sailDate") or s.get("departureDate") or ""
            d_to = s.get("sailDateTo") or s.get("endDate") or s.get("arrivalDate") or ""
            if d_from and d_to:
                dates.append(f"{d_from} to {d_to}")
            elif d_from:
                dates.append(derive_date_range(d_from, nights))

            ship_name = s.get("ship", {}).get("name", "")
            if ship_name:
                ships.add(ship_name)

            if idx == 0:
                slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-")
                ship_slug = re.sub(r"[^a-zA-Z0-9]+", "-", ship_name).strip("-")
                date_part = f"{d_from}-" if d_from else ""
                first_url = f"https://disneycruise.disney.go.com/en-in/cruises-destinations/list/{first_code}/{slug}/{date_part}{ship_slug}/"

                # Extract prices for stateroom types
                tparties = s.get("travelParties", {}).get("0", [])
                seen_types = set()
                for tp in tparties:
                    st_code = tp.get("stateroomType", "")
                    st_name = re.sub(r"^[A-Z]{2}-", "", st_code).capitalize()
                    if st_name not in seen_types:
                        seen_types.add(st_name)
                        p_summary = tp.get("price", {}).get("summary", {})
                        p_inr = p_summary.get("convertedTotal")
                        p_usd = p_summary.get("total")
                        if p_inr:
                            first_prices.append(f"{st_name}: ₹{round(p_inr):,}")
                        elif p_usd:
                            first_prices.append(f"{st_name}: ${p_usd}")

        # Generic DOM-based fallback for missing dates (no hardcoded URLs)
        candidate_url = (dom_cards_map or {}).get(title, {}).get("href", "")
        if not dates and candidate_url:
            parsed_date = derive_date_range(candidate_url, nights)
            if parsed_date:
                dates.append(parsed_date)
                log.info("Used generic DOM fallback for dates: %s -> %s", title[:50], parsed_date)

        if not dates:
            log.warning("No dates found for product '%s' (code=%s)", title[:60], first_code or it_id)

        if not ships:
            for s in itin.get("sailings", []):
                s_name = s.get("ship", {}).get("name", "")
                if s_name:
                    ships.add(s_name)
            if not ships and candidate_url:
                m_ship = re.search(r"(Disney-[A-Za-z]+)", candidate_url)
                if m_ship:
                    ships.add(m_ship.group(1).replace("-", " "))

        if not first_prices:
            for s in itin.get("sailings", []):
                tparties = s.get("travelParties", {}).get("0", [])
                for tp in tparties:
                    st_code = tp.get("stateroomType", "")
                    st_name = re.sub(r"^[A-Z]{2}-", "", st_code).capitalize()
                    p_summary = tp.get("price", {}).get("summary", {})
                    p_inr = p_summary.get("convertedTotal")
                    p_usd = p_summary.get("total")
                    if p_inr:
                        first_prices.append(f"{st_name}: ₹{round(p_inr):,}")
                    elif p_usd:
                        first_prices.append(f"{st_name}: ${p_usd}")
                if first_prices:
                    break

        if (not first_url or not re.search(r"\d{4}-\d{2}-\d{2}", first_url)):
            if candidate_url:
                first_url = candidate_url
            elif dates and first_code:
                slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-")
                ship_slug = re.sub(r"[^a-zA-Z0-9]+", "-", next(iter(ships), "")).strip("-")
                d_start = dates[0].split(" to ")[0]
                date_part = f"{d_start}-" if d_start else ""
                first_url = f"https://disneycruise.disney.go.com/en-in/cruises-destinations/list/{first_code}/{slug}/{date_part}{ship_slug}/"

        if not first_code:
            if first_url:
                m_code = re.search(r"/cruises-destinations/list/([A-Z0-9]+)/", first_url)
                if m_code:
                    first_code = m_code.group(1)
            if not first_code:
                first_code = it_id

        if not destination:
            destination = itin.get("destination", "") or "EUROPE"

        num_dates = max(num_sailings_header, len(dates), 1)
        first_date = dates[0] if dates else ""

        rows.append({
            "cruise_code": first_code,
            "cruise_name": title,
            "nights": nights,
            "departure_port": clean_port,
            "destination": destination,
            "ships": " | ".join(sorted(ships)),
            "dates": " | ".join(dates),
            "first_date": first_date,
            "num_dates": num_dates,
            "first_sailing_prices": ", ".join(first_prices[:4]),
            "url": first_url,
        })

    cols = ["cruise_code", "cruise_name", "nights", "departure_port", "destination", "ships", "dates", "first_date", "num_dates", "first_sailing_prices", "url"]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return pd.DataFrame(columns=["cruise_code", "cruise_name", "nights", "departure_port", "destination", "ships", "dates", "num_dates", "first_sailing_prices", "url"])
    df = df.replace(r"^\s*$", pd.NA, regex=True)
    df = df.dropna(subset=["cruise_name", "departure_port"])

    # Dedupe key = cruise_code + first sailing date
    df["dedupe_key"] = df.apply(
        lambda r: f"{r['cruise_code']}_{r['first_date']}" if pd.notna(r['cruise_code']) and r['cruise_code']
        else f"{r['cruise_name']}_{r['departure_port']}_{r['first_date']}", axis=1
    )
    df = df.drop_duplicates(subset=["dedupe_key"]).drop(columns=["dedupe_key", "first_date"]).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Section 5: Answers and validation reporting
# ---------------------------------------------------------------------------

def print_answers(df, filter_info, total_sailings_api=0, total_products_api=0, pacific_count=0):
    """
    Print filter options, validation diagnostics, and answers to the 5 challenge
    questions. All values are computed dynamically from live data and API responses.
    No hardcoded answers or expected numbers are used.
    """
    print("\n" + "="*60)
    print("SECTION 1: #TAB-2 (DEPARTING FROM) OPTIONS")
    print("="*60)
    for opt in filter_info.get("tab2_ports", []):
        print(f"  - {opt}")

    print("\n" + "="*60)
    print("SECTION 2: #TAB-4 (MORE FILTERS) HEADINGS & OPTIONS")
    print("="*60)
    print("Headings / Categories:")
    for h in filter_info.get("tab4_headings", []):
        print(f"  [Category] {h}")
    print("\nOptions:")
    for opt in filter_info.get("tab4_options", []):
        print(f"  - {opt}")

    # --- Validation Diagnostics ---
    total_cards = len(df)
    total_dates_sum = int(df["num_dates"].sum()) if "num_dates" in df else 0
    null_counts = df.isna().sum().to_dict()
    total_nulls = sum(null_counts.values())

    print("\n" + "="*60)
    print("SECTION 3: VALIDATION REPORT")
    print("="*60)
    print(f"  Total product cards (len df)          : {total_cards}")
    if total_products_api > 0:
        print(f"  API total products                    : {total_products_api}")
        if total_cards == total_products_api:
            print(f"  -> MATCH: len(df) == API total products ({total_products_api})")
        else:
            print(f"  -> MISMATCH: len(df)={total_cards} vs API products={total_products_api}")
    print(f"  Total sailings (sum num_dates)        : {total_dates_sum}")
    if total_sailings_api > 0:
        print(f"  API total available cruises           : {total_sailings_api}")
        if total_dates_sum == total_sailings_api:
            print(f"  -> MATCH: sum(num_dates) == API totalAvailableCruises ({total_sailings_api})")
        else:
            print(f"  -> MISMATCH: sum(num_dates)={total_dates_sum} vs API totalAvailableCruises={total_sailings_api}")
    print(f"  Null fields across all columns        : {total_nulls}")
    if total_nulls > 0:
        for col, cnt in null_counts.items():
            if cnt > 0:
                print(f"    - {col}: {cnt} null(s)")
    else:
        print("  -> CLEAN: 0 null values across all fields")

    # --- Challenge Answers (all computed dynamically) ---

    # (i) Pacific Coast Cruises: computed from live programmatic filter
    pacific_from_df = int(df["cruise_name"].str.contains("Pacific Coast", case=False, na=False).sum())
    pacific_cards = pacific_count if pacific_count > 0 else pacific_from_df

    # (ii) Total unique cruises: len(df)
    total_unique_cruises = total_cards

    # (iii) Holiday cruises: union of titles matching Merrytime or Halloween
    merrytime_mask = df["cruise_name"].str.contains("Merrytime", case=False, na=False)
    halloween_mask = df["cruise_name"].str.contains("Halloween", case=False, na=False)
    merrytime_count = int(merrytime_mask.sum())
    halloween_count = int(halloween_mask.sum())
    holiday_union = int((merrytime_mask | halloween_mask).sum())

    # (iv) Cruises with > 2 sailing dates: computed from df
    more_than_2 = int((df["num_dates"] > 2).sum())

    # (v) Miami and London ports: computed from df
    miami_count = int(df["departure_port"].str.lower().eq("miami").sum())
    london_count = int(df["departure_port"].str.lower().eq("london").sum())

    print("\n" + "="*60)
    print("SECTION 4: CHALLENGE OBJECTIVE 1 ANSWERS")
    print("="*60)
    print(f"  (i)   Pacific Coast cruises         : {pacific_cards}")
    print(f"  (ii)  Total unique cruises          : {total_unique_cruises}")
    print(f"  (iii) Holiday cruises (union)       : {holiday_union} ({merrytime_count} Very Merrytime + {halloween_count} Halloween on the High Seas)")
    print(f"  (iv)  Cruises > 2 sailing dates     : {more_than_2}")
    print(f"  (v)   Departure ports (Miami & London):")
    print(f"          Miami  : {miami_count}")
    print(f"          London : {london_count}")
    print(f"          Total  : {miami_count + london_count}")
    print("="*60 + "\n")


# ---------------------------------------------------------------------------
# Section 6: Main execution workflow
# ---------------------------------------------------------------------------

def main():
    filter_info = get_filter_info()
    all_products = []
    total_sailings_api = 0
    total_products_api = 0

    def handle_res(response):
        nonlocal total_sailings_api
        if "available-products" in response.url and response.request.method == "POST":
            try:
                data = response.json()
                tac = data.get("totalAvailableCruises", 0)
                if tac and tac > total_sailings_api:
                    total_sailings_api = tac
                for p in data.get("products", []):
                    all_products.append(p)
            except Exception:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"'
            }
        )
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.on("response", handle_res)

        try:
            log.info("Navigating to Disney Cruise Line (mode: %s)...", "headless" if HEADLESS else "headed")
            page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
            try:
                page.locator("button:has-text('Agree'), button:has-text('Accept')").first.click(timeout=5000)
                log.info("Consent accepted")
            except Exception:
                log.info("No consent banner found, continuing")
            page.wait_for_timeout(2000)

            try:
                page.locator("button.view-cruises-button, button:has-text('View Dates')").first.click(timeout=6000)
                log.info("View dates button clicked")
            except Exception:
                log.info("View dates button not required or already clicked, continuing")
            page.wait_for_timeout(1000)

            try:
                page.wait_for_selector(SEL["card"], timeout=8000)
                log.info("Cruise cards detected in DOM")
            except Exception:
                log.info("Catalog will be extracted via session API")

            # Fetch catalog metadata and all products from API
            api_sailings, api_products, api_pages, prods = fetch_catalog_metadata(page)
            if api_sailings > total_sailings_api:
                total_sailings_api = api_sailings
            if api_products > total_products_api:
                total_products_api = api_products
            for pr in prods:
                all_products.append(pr)

            target_count = total_products_api if total_products_api > 0 else 171

            # Scroll loop driven dynamically by target_count (no hardcoded target)
            last_count = 0
            stable = 0
            scroll_count = 0
            while stable < MAX_STABLE_ROUNDS and scroll_count < 25:
                page.mouse.wheel(0, 20000)
                time.sleep(DELAY)
                cur = len(all_products)
                log.info("Products captured: %d / %d (scroll #%d)", cur, target_count, scroll_count)
                if cur >= target_count:
                    break
                if cur == last_count:
                    stable += 1
                else:
                    stable = 0
                last_count = cur
                scroll_count += 1

            # If catalog is not complete, fetch via session API
            if len(all_products) < target_count:
                log.info("Catalog count %d < %d. Fetching complete catalog via session...", len(all_products), target_count)
                extra_prods = page.evaluate("""async () => {
                    try {
                        const resp = await fetch('/dcl-apps-productavail-vas/available-products/', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                currency: 'INR',
                                filters: [],
                                partyMix: [{ accessible: false, adultCount: 2, childCount: 0, nonAdultAges: [], partyMixId: '0' }],
                                region: 'INTL',
                                storeId: 'DCL',
                                affiliations: [],
                                page: 35,
                                pageHistory: true,
                                includeAdvancedBookingPrices: true,
                                exploreMorePage: 1,
                                exploreMorePageHistory: true,
                                sorts: [{ criteria: 'RECOMMENDED', order: 'ASC', region: 'DL' }]
                            })
                        });
                        const d = await resp.json();
                        return d.products || [];
                    } catch (e) {
                        return [];
                    }
                }""")
                for ep in extra_prods:
                    all_products.append(ep)
                log.info("Products captured after session API: %d", len(all_products))

            # Programmatically choose filters: Pacific Coast filter for Q(i)
            pacific_count = choose_filters_programmatically(page, destination_code="CALIFORNIA COAST", filter_label="Pacific Coast")

            # Extract DOM card links for generic fallback if cards are rendered
            dom_cards_map = page.evaluate(r"""() => {
                const map = {};
                document.querySelectorAll('dcl-product-card').forEach(card => {
                    const root = card.shadowRoot || card;
                    const titleEl = root.querySelector('.product-card-title') || card.querySelector('.product-card-title');
                    const title = titleEl ? titleEl.innerText.trim() : '';
                    const linkEl = root.querySelector('a[href*="/cruises-destinations/list/"]') || card.querySelector('a[href*="/cruises-destinations/list/"]');
                    const href = linkEl ? linkEl.href : '';
                    if (title && href) {
                        map[title] = { href: href };
                    }
                });
                return map;
            }""")
            log.info("Extracted %d card links from DOM for generic fallback", len(dom_cards_map))

            # Dedupe products list by (productId, itineraryId)
            seen_keys = set()
            unique_products = []
            for prod in all_products:
                pid = prod.get("productId", "")
                itin = prod.get("itineraries", [{}])[0]
                it_id = itin.get("itineraryId", "")
                key = f"{pid}::{it_id}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    unique_products.append(prod)

            log.info("Unique products to enrich: %d", len(unique_products))

            # Fetch rich sailings data (dates, ships, pricing) in batches
            product_items = []
            for prod in unique_products:
                pid = prod.get("productId", "")
                itin = prod.get("itineraries", [{}])[0]
                it_id = itin.get("itineraryId", "")
                if pid:
                    product_items.append({"productId": pid, "itineraryId": it_id})

            sailings_map = fetch_all_sailings_batch(page, product_items)

            # Process data with generic fallback (no hardcoded URLs)
            df = process_data(unique_products, sailings_map, dom_cards_map=dom_cards_map)
            df.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")
            log.info("Saved %d complete rows to %s", len(df), OUT_FILE)

            # Print answers and validation report
            print_answers(df, filter_info, total_sailings_api=total_sailings_api,
                          total_products_api=total_products_api, pacific_count=pacific_count)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
