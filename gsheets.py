import gspread
import requests
import json
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta

# --- CONFIG ---
SHEET_NAME = "VIK SHEET"
TAB_NAME = "Gloveman £2000"
CREDS_PATH = "creds/google-sheets-key.json"
KEEPA_API_KEY = "1nhn816kg8045h0tp3669h6fp6n3f7s0fs97ojl9mkdvvff1a87hdclusd63vep2"

# --- GOOGLE AUTH ---
def get_worksheet(sheet_name, tab_name):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_PATH, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).worksheet(tab_name)

# --- KEEPA FETCH ---
def fetch_keepa_data(asin):
    url = f"https://api.keepa.com/product?key={KEEPA_API_KEY}&domain=2&asin={asin}&buybox=1&offers=40"
    r = requests.get(url)
    data = r.json()
    if "products" not in data:
        return None
    return data["products"][0]

def extract_current_price_from_csv(product):
    csv_arrays = product.get("csv", [])
    if not csv_arrays or len(csv_arrays[0]) < 2:
        return 0.0
    flat_array = csv_arrays[0]
    for i in range(len(flat_array) - 1, 0, -2):
        price = flat_array[i]
        if price is not None and price > 0:
            return round(price / 100, 2)
    return 0.0


def extract_sell_price_from_offers(product_data):
    offers = product_data.get("offers") or []
    fba_prime_prices = []
    last_update = product_data.get("lastUpdate")

    print(f"\n--- DEBUGGING ASIN ---")
    print(f"Last update timestamp: {last_update}")
    print(f"Total offers found: {len(offers)}")

    for i, offer in enumerate(offers):
        seen = offer.get("lastSeen", 0)
        is_fba = offer.get("isFBA") is True
        is_prime = offer.get("isPrime") is True
        is_amazon = offer.get("isAmazon") is True
        is_shippable = offer.get("isShippable") is True
        is_new = offer.get("condition") == 1
        is_live = seen == last_update
        offer_csv = offer.get("offerCSV", [])

        price_cents = offer_csv[-2] if len(offer_csv) >= 2 else None

        # Fallback if needed
        if price_cents is None or not isinstance(price_cents, int):
            price_cents = offer.get("price")

        print(
            f"Offer #{i+1}: seen={seen}, live={is_live}, fba={is_fba}, prime={is_prime}, "
            f"amazon={is_amazon}, shippable={is_shippable}, new={is_new}, "
            f"price_cents={price_cents}"
        )

        if all([is_live, is_fba, is_prime, not is_amazon, is_shippable, is_new]) and isinstance(price_cents, int) and price_cents > 0:
            price = price_cents / 100
            fba_prime_prices.append(price)
            print(f"✅ Accepted: £{price}")
        else:
            print(f"❌ Skipped")

    print(f"\n✅ Final FBA Prime Prices Used: {fba_prime_prices}")
    return round(min(fba_prime_prices), 2) if fba_prime_prices else 0.0




def extract_latest_price(price_history):
    try:
        price = next(p for p in reversed(price_history) if p is not None and p > 0)
        return round(price / 100, 2)
    except:
        return 0.0

def extract_buybox_seller_count(product_data):
    counts = product_data.get("buyBoxEligibleOfferCounts") or []
    # print(product_data.get("offers"))
    print(product_data.get("fbaFees"))
    if len(counts) < 2:
        return 1
    for i in range(len(counts) - 1, 0, -2):
        count = counts[i]
        if count is not None and count > 0:
            return count
    return 1

from time import time

def count_fba_sellers(product_data):
    offers = product_data.get("offers") or []
    fba_sellers = set()
    last_update = product_data.get("lastUpdate")

    for offer in offers:
        if (
            offer.get("isFBA") is True and
            offer.get("condition") == 1 and
            offer.get("isShippable") is True and
            offer.get("lastSeen") == last_update
        ):
            seller_id = offer.get("sellerId")
            if seller_id:
                fba_sellers.add(seller_id)

    print(f"Live FBA Sellers: {fba_sellers}")
    return len(fba_sellers)





# --- CALCULATE PROFIT ---
def calculate_profits(buy_price, sell_price, fba_fees):
    referral_fee = round(sell_price * 0.15, 2)
    fba_fee = round(fba_fees.get("pickAndPackFee", 0) / 100, 2) if fba_fees else 0.0
    VAT_fee = round(sell_price * 0.16, 2)
    profit_per_unit = round(sell_price - referral_fee - fba_fee - buy_price - VAT_fee, 2)
    roi = round((profit_per_unit / buy_price) * 100, 2) if buy_price > 0 else 0
    return profit_per_unit, roi


# --- MAIN PROCESS ---
def update_sheet():
    ws = get_worksheet(SHEET_NAME, TAB_NAME)
    rows = ws.get_all_values()[1:]  # Skip header

    for idx, row in enumerate(rows, start=2):
        asin_link = row[0]
        if not asin_link.strip():
            continue
        asin = asin_link.split("/dp/")[-1].split("/")[0]

        try:
            buy_price = float(row[2].replace("£", ""))
        except:
            print(f"Invalid buy price in row {idx}. Skipping.")
            continue

        product_data = fetch_keepa_data(asin)
        if not product_data:
            print(f"No data for ASIN {asin}")
            continue

        stats = product_data.get("stats") or {}

        # Price fallback order
        sell_price = round(stats.get("buyBoxPrice", 0) / 100, 2)
        if sell_price == 0.0:
            sell_price = extract_current_price_from_csv(product_data)
        if sell_price == 0.0:
            sell_price = extract_latest_price(product_data.get("buyBoxPriceHistory", []))
        if sell_price == 0.0:
            sell_price = extract_sell_price_from_offers(product_data)

        spm = product_data.get("monthlySold") or 0
        sellers = count_fba_sellers(product_data)

        fba_fees = product_data.get("fbaFees") or {}
        profit, roi = calculate_profits(buy_price, sell_price, fba_fees)
        monthly_profit = round(profit * spm / (sellers + 1), 2)

        # Log summary
        print(f"ASIN: {asin}")
        print(f"Buy: £{buy_price} | Sell: £{sell_price} | SPM: {spm} | Sellers: {sellers}")
        print(f"Profit/unit: £{profit} | Monthly Profit: £{monthly_profit} | ROI: {roi}%")

        # Batch update A, E, F, G, H, I (preserving B-D)
        values = [[
            f"£{sell_price}",      # D
            f"{roi:.2f}%",         # E
            f"£{profit}",          # F
            spm,                   # G
            sellers,                #H
            f"£{monthly_profit}", # I
        ]]
        ws.update(f"D{idx}:I{idx}", values)

if __name__ == "__main__":
    update_sheet()
