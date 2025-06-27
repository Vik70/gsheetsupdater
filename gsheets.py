import gspread
import requests
import json
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import time as time_module

# Load environment variables
load_dotenv()

# --- CONFIG ---
SHEET_NAME = "VIK SHEET"
TAB_NAME = "Rapesco £2500"
CREDS_PATH = "/etc/secrets/google-sheets-key.json"
KEEPA_API_KEY = os.getenv('KEEPA_API_KEY')
PROGRESS_FILE = "progress.json"
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')  # Add this to your .env file

# API rate limiting
KEEPA_REQUESTS_PER_MINUTE = 20  # Keepa's actual limit
REQUEST_INTERVAL = 60 / KEEPA_REQUESTS_PER_MINUTE  # Time between requests in seconds
last_request_time = 0
BATCH_SIZE = 10  # Number of ASINs to process in one batch
MAX_ROWS_PER_RUN = 50  # Maximum number of rows to process in one run

# Google Sheets rate limiting
SHEETS_REQUESTS_PER_MINUTE = 60  # Google Sheets limit
SHEETS_REQUEST_INTERVAL = 60 / SHEETS_REQUESTS_PER_MINUTE
last_sheets_request_time = 0

# Token management
class TokenManager:
    def __init__(self):
        self.tokens_left = 1200  # Start with max tokens
        self.refill_time = 0
        self.refill_rate = 20  # Tokens per minute
        self.last_update = time_module.time()
    
    def update_from_response(self, response):
        self.tokens_left = response.get('tokensLeft', self.tokens_left)
        self.refill_time = response.get('refillIn', 0)
        self.refill_rate = response.get('refillRate', self.refill_rate)
        self.last_update = time_module.time()
    
    def has_tokens(self):
        # Update tokens based on time passed
        current_time = time_module.time()
        time_passed = current_time - self.last_update
        if time_passed > 0:
            tokens_refilled = int(time_passed * (self.refill_rate / 60))
            self.tokens_left = min(1200, self.tokens_left + tokens_refilled)
            self.last_update = current_time
        
        return self.tokens_left > 0
    
    def wait_for_tokens(self):
        if self.tokens_left <= 0:
            wait_time = self.refill_time if self.refill_time > 0 else 60
            print(f"⏳ Waiting {wait_time} seconds for token refill...")
            time_module.sleep(wait_time)
            return True
        return False

# Initialize token manager
token_manager = TokenManager()

# Simple Discord webhook sender using requests

def send_discord_message(message, is_error=False):
    if DISCORD_WEBHOOK_URL:
        try:
            # Add emoji based on message type
            if is_error:
                message = f"❌ {message}"
            elif "completed" in message.lower():
                message = f"✅ {message}"
            elif "paused" in message.lower():
                message = f"⏸️ {message}"
            elif "resuming" in message.lower():
                message = f"🔄 {message}"
            elif "waiting" in message.lower():
                message = f"⏳ {message}"
            data = {"content": message}
            requests.post(DISCORD_WEBHOOK_URL, json=data)
        except Exception as e:
            print(f"Failed to send Discord message: {str(e)}")

def rate_limit():
    global last_request_time
    current_time = time_module.time()
    time_since_last_request = current_time - last_request_time
    
    if time_since_last_request < REQUEST_INTERVAL:
        sleep_time = REQUEST_INTERVAL - time_since_last_request
        time_module.sleep(sleep_time)
    
    last_request_time = time_module.time()

def sheets_rate_limit():
    global last_sheets_request_time
    current_time = time_module.time()
    time_since_last_request = current_time - last_sheets_request_time
    
    if time_since_last_request < SHEETS_REQUEST_INTERVAL:
        sleep_time = SHEETS_REQUEST_INTERVAL - time_since_last_request
        time_module.sleep(sleep_time)
    
    last_sheets_request_time = time_module.time()

def fetch_keepa_data_batch(asins):
    rate_limit()  # Apply rate limiting
    
    # Check if we have tokens before making request
    if not token_manager.has_tokens():
        if token_manager.wait_for_tokens():
            return fetch_keepa_data_batch(asins)  # Retry after waiting
    
    # Join ASINs with commas for the batch request
    asin_string = ",".join(asins)
    url = f"https://api.keepa.com/product?key={KEEPA_API_KEY}&domain=2&asin={asin_string}&buybox=1&offers=40"
    
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            r = requests.get(url)
            data = r.json()
            
            # Update token manager with response data
            token_manager.update_from_response(data)
            
            # Check for API errors
            if "error" in data:
                error_msg = data.get("error", {}).get("message", "Unknown error")
                if "tokens" in error_msg.lower():
                    print(f"⚠️ Keepa API token limit reached. Tokens left: {token_manager.tokens_left}, Refill in: {token_manager.refill_time} seconds")
                    if token_manager.wait_for_tokens():
                        retry_count += 1
                        continue
                else:
                    print(f"⚠️ Keepa API error: {error_msg}")
                    print(f"Request URL: {url}")
                    print(f"Response: {data}")
                    return {}
                    
            if "products" not in data:
                print(f"⚠️ No product data found for batch")
                print(f"Request URL: {url}")
                print(f"Response: {data}")
                return {}
                
            # Create a dictionary mapping ASINs to their product data
            products = {product.get("asin"): product for product in data["products"]}
            
            # Log which ASINs were found and which were missing
            found_asins = set(products.keys())
            missing_asins = set(asins) - found_asins
            if missing_asins:
                print(f"⚠️ Missing data for ASINs: {', '.join(missing_asins)}")
            
            return products
            
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Network error while fetching batch: {str(e)}")
            retry_count += 1
            time_module.sleep(5)  # Wait 5 seconds before retrying
            continue
        except json.JSONDecodeError as e:
            print(f"⚠️ Invalid JSON response for batch: {str(e)}")
            print(f"Response text: {r.text}")
            retry_count += 1
            time_module.sleep(5)
            continue
        except Exception as e:
            print(f"⚠️ Unexpected error while fetching batch: {str(e)}")
            retry_count += 1
            time_module.sleep(5)
            continue
    
    print(f"❌ Failed to fetch data after {max_retries} retries")
    return {}

def extract_current_price_from_csv(product):
    csv_arrays = product.get("csv", [])
    if not csv_arrays or not isinstance(csv_arrays, list) or len(csv_arrays) == 0:
        return 0.0
    flat_array = csv_arrays[0]
    if not isinstance(flat_array, list) or len(flat_array) < 2:
        return 0.0
    for i in range(len(flat_array) - 1, 0, -2):
        price = flat_array[i]
        if price is not None and price > 0:
            return round(price / 100, 2)
    return 0.0

def process_offers(product_data):
    if not product_data:
        return 0.0, 0
        
    offers = product_data.get("offers") or []
    fba_prime_prices = []
    amazon_prices = []
    last_update = product_data.get("lastUpdate", 0)
    
    # Seller tracking sets
    fba_sellers = set()
    amazon_sellers = set()
    other_sellers = set()
    
    # Consider offers within 1 hour (3600 seconds) as live
    LIVE_WINDOW = 3600

    print(f"\n--- DEBUGGING ASIN ---")
    print(f"Last update timestamp: {last_update}")
    print(f"Total offers found: {len(offers)}")

    if not offers:
        print("No offers found in the data")
        return 0.0, 0

    print("\n=== DETAILED SELLER INFORMATION ===")
    for i, offer in enumerate(offers):
        seen = offer.get("lastSeen", 0)
        is_fba = offer.get("isFBA") is True
        is_prime = offer.get("isPrime") is True
        is_amazon = offer.get("isAmazon") is True
        is_shippable = offer.get("isShippable") is True
        is_new = offer.get("condition") == 1
        is_live = abs(seen - last_update) <= LIVE_WINDOW
        is_warehouse = offer.get("isWarehouseDeal") is True
        is_scam = offer.get("isScam") is True
        is_preorder = offer.get("isPreorder") is True
        is_map = offer.get("isMAP") is True
        seller_id = offer.get("sellerId", "Unknown")
        condition = offer.get("condition", 0)
        condition_comment = offer.get("conditionComment", "")
        offer_csv = offer.get("offerCSV", [])
        price_cents = offer_csv[-2] if len(offer_csv) >= 2 else None

        # Fallback if needed
        if price_cents is None or not isinstance(price_cents, int):
            price_cents = offer.get("price")

        print(
            f"\nOffer #{i+1} (Seller: {seller_id}):"
            f"\n  - Last Seen: {seen}"
            f"\n  - Time Diff: {abs(seen - last_update)} seconds"
            f"\n  - Is Live: {is_live}"
            f"\n  - Is FBA: {is_fba}"
            f"\n  - Is Prime: {is_prime}"
            f"\n  - Is Amazon: {is_amazon}"
            f"\n  - Is Shippable: {is_shippable}"
            f"\n  - Is New: {is_new}"
            f"\n  - Price (cents): {price_cents}"
            f"\n  - Price (£): {price_cents/100 if isinstance(price_cents, int) else 'N/A'}"
        )

        if is_live and is_new and is_shippable and not is_scam and not is_warehouse:
            print(f"\nSeller ID: {seller_id}")
            print(f"  - Is Live: {is_live}")
            print(f"  - Is New: {is_new}")
            print(f"  - Is Shippable: {is_shippable}")
            print(f"  - Is FBA: {is_fba}")
            print(f"  - Is Prime: {is_prime}")
            print(f"  - Is Amazon: {is_amazon}")
            print(f"  - Is Warehouse Deal: {is_warehouse}")
            print(f"  - Is Scam: {is_scam}")
            print(f"  - Is Preorder: {is_preorder}")
            print(f"  - Is MAP: {is_map}")
            print(f"  - Condition: {condition}")
            print(f"  - Condition Comment: {condition_comment}")
            
            if is_amazon:
                amazon_sellers.add(seller_id)
                if isinstance(price_cents, int) and price_cents > 0:
                    price = price_cents / 100
                    amazon_prices.append(price)
                    print(f"✅ Accepted Amazon price: £{price}")
            elif is_fba and is_prime:
                fba_sellers.add(seller_id)
                if isinstance(price_cents, int) and price_cents > 0:
                    price = price_cents / 100
                    fba_prime_prices.append(price)
                    print(f"✅ Accepted FBA Prime price: £{price}")
            elif seller_id == "A30DC7701CXIBH":  # Special case for Amazon EU seller
                other_sellers.add(seller_id)
            else:
                print(f"❌ Skipped - Reason: ", end="")
                if not is_fba: print("Not FBA")
                elif not is_prime: print("Not Prime")
                elif is_amazon: print("Is Amazon")
                else: print("Unknown")
        else:
            print(f"❌ Skipped - Reason: ", end="")
            if not is_live: print(f"Not live (time diff: {abs(seen - last_update)} seconds)")
            elif not is_new: print("Not new")
            elif not is_shippable: print("Not shippable")
            elif not isinstance(price_cents, int): print("Invalid price format")
            elif price_cents <= 0: print("Price <= 0")
            else: print("Unknown")

    print(f"\n✅ Final Amazon Prices: {amazon_prices}")
    print(f"✅ Final FBA Prime Prices: {fba_prime_prices}")
    
    # Get lowest prices from each category
    lowest_amazon = min(amazon_prices) if amazon_prices else float('inf')
    lowest_fba = min(fba_prime_prices) if fba_prime_prices else float('inf')
    
    # Use the lower of the two prices
    final_price = min(lowest_amazon, lowest_fba)
    print(f"✅ Final price used: £{final_price} (Amazon: £{lowest_amazon}, FBA Prime: £{lowest_fba})")

    print("\n=== SELLER COUNTS ===")
    print(f"Amazon Sellers: {amazon_sellers}")
    print(f"FBA Prime Sellers: {fba_sellers}")
    print(f"Other Sellers: {other_sellers}")
    
    return round(final_price, 2) if final_price != float('inf') else 0.0, len(fba_sellers) + len(amazon_sellers) + len(other_sellers)

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
    amazon_sellers = set()
    other_sellers = set()
    last_update = product_data.get("lastUpdate")
    
    # Use same time window as extract_sell_price_from_offers
    LIVE_WINDOW = 3600

    print("\n=== DETAILED SELLER INFORMATION ===")
    for offer in offers:
        seen = offer.get("lastSeen", 0)
        is_fba = offer.get("isFBA") is True
        is_prime = offer.get("isPrime") is True
        is_amazon = offer.get("isAmazon") is True
        is_shippable = offer.get("isShippable") is True
        is_new = offer.get("condition") == 1
        is_live = abs(seen - last_update) <= LIVE_WINDOW
        is_warehouse = offer.get("isWarehouseDeal") is True
        is_scam = offer.get("isScam") is True
        is_preorder = offer.get("isPreorder") is True
        is_map = offer.get("isMAP") is True
        seller_id = offer.get("sellerId", "Unknown")
        condition = offer.get("condition", 0)
        condition_comment = offer.get("conditionComment", "")
        
        # Include all valid sellers that are live, new, shippable and not scams
        if is_live and is_new and is_shippable and not is_scam and not is_warehouse:
            print(f"\nSeller ID: {seller_id}")
            print(f"  - Is Live: {is_live}")
            print(f"  - Is New: {is_new}")
            print(f"  - Is Shippable: {is_shippable}")
            print(f"  - Is FBA: {is_fba}")
            print(f"  - Is Prime: {is_prime}")
            print(f"  - Is Amazon: {is_amazon}")
            print(f"  - Is Warehouse Deal: {is_warehouse}")
            print(f"  - Is Scam: {is_scam}")
            print(f"  - Is Preorder: {is_preorder}")
            print(f"  - Is MAP: {is_map}")
            print(f"  - Condition: {condition}")
            print(f"  - Condition Comment: {condition_comment}")
            
            if is_amazon:
                amazon_sellers.add(seller_id)
            elif is_fba and is_prime:
                fba_sellers.add(seller_id)
            elif seller_id == "A30DC7701CXIBH":  # Special case for Amazon EU seller
                other_sellers.add(seller_id)

    print("\n=== SELLER COUNTS ===")
    print(f"Amazon Sellers: {amazon_sellers}")
    print(f"FBA Prime Sellers: {fba_sellers}")
    print(f"Other Sellers: {other_sellers}")
    
    # Return total count of all valid sellers
    return len(fba_sellers) + len(amazon_sellers) + len(other_sellers)





# --- CALCULATE PROFIT ---
def calculate_profits(buy_price, sell_price, fba_fees):
    referral_fee = round(sell_price * 0.15, 2)
    fba_fee = round(fba_fees.get("pickAndPackFee", 0) / 100, 2) if fba_fees else 0.0
    VAT_fee = round(sell_price * 0.16, 2)
    profit_per_unit = round(sell_price - referral_fee - fba_fee - buy_price - VAT_fee, 2)
    roi = round((profit_per_unit / buy_price) * 100, 2) if buy_price > 0 else 0
    return profit_per_unit, roi


# --- MAIN PROCESS ---
def get_all_worksheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_PATH, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open(SHEET_NAME)
    return spreadsheet.worksheets()

def save_progress(sheet_title, last_processed_row):
    progress = {
        'sheet_title': sheet_title,
        'last_processed_row': last_processed_row,
        'timestamp': datetime.now().isoformat()
    }
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f)

def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return None
    try:
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    except:
        return None

def update_sheet(ws):
    progress = load_progress()
    start_row = 0
    
    # If we have progress and it's for this sheet, resume from last position
    if progress and progress['sheet_title'] == ws.title:
        start_row = progress['last_processed_row']
        message = f"Resuming from row {start_row} in sheet {ws.title}"
        print(message)
        send_discord_message(message)
    
    rows = ws.get_all_values()[1:]  # Skip header
    total_rows = len(rows)
    processed_rows = 0
    profit_items = {
        'high_profit': [],    # >100% margin
        'medium_profit': [],  # >50% margin
        'low_profit': []      # >30% margin
    }

    # Get the spreadsheet object and sheet ID for conditional formatting
    spreadsheet = ws.spreadsheet
    sheet_id = ws.id
    
    # Define the conditional formatting rules
    rules = [
        {
            'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': len(rows)+1, 'startColumnIndex': 8, 'endColumnIndex': 9}],  # Column I
            'booleanRule': {
                'condition': {'type': 'NUMBER_GREATER', 'values': [{'userEnteredValue': '30'}]},
                'format': {'backgroundColor': {'red': 1.0, 'green': 1.0, 'blue': 0.0}}  # Yellow
            }
        },
        {
            'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': len(rows)+1, 'startColumnIndex': 8, 'endColumnIndex': 9}],  # Column I
            'booleanRule': {
                'condition': {'type': 'NUMBER_GREATER', 'values': [{'userEnteredValue': '50'}]},
                'format': {'backgroundColor': {'red': 0.7, 'green': 0.9, 'blue': 1.0}}  # Light Blue
            }
        },
        {
            'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': len(rows)+1, 'startColumnIndex': 8, 'endColumnIndex': 9}],  # Column I
            'booleanRule': {
                'condition': {'type': 'NUMBER_GREATER', 'values': [{'userEnteredValue': '100'}]},
                'format': {'backgroundColor': {'red': 0.7, 'green': 1.0, 'blue': 0.7}}  # Green
            }
        }
    ]
    
    # Clear existing rules and add new ones
    requests = [
        {
            'deleteConditionalFormatRule': {
                'sheetId': sheet_id,
                'index': 0
            }
        }
    ]
    
    # Add new rules
    for rule in rules:
        requests.append({
            'addConditionalFormatRule': {
                'rule': rule
            }
        })
    
    try:
        sheets_rate_limit()  # Apply rate limiting before Google Sheets API call
        spreadsheet.batch_update({
            'requests': requests
        })
    except Exception as e:
        error_msg = f"Error updating conditional formatting: {str(e)}"
        print(f"⚠️ {error_msg}")
        send_discord_message(error_msg, is_error=True)
        # Continue without conditional formatting if it fails

    # Process rows in batches
    i = start_row
    while i < len(rows):
        # Check if we've hit the max rows per run
        if i >= start_row + MAX_ROWS_PER_RUN:
            message = f"Paused after processing {MAX_ROWS_PER_RUN} rows. Waiting for token refill..."
            print(f"\n⏸️ {message}")
            send_discord_message(message)
            
            # Wait for token refill
            if token_manager.wait_for_tokens():
                # Update start position and continue
                start_row = i
                save_progress(ws.title, start_row)
                message = f"Resuming from row {start_row}"
                print(message)
                send_discord_message(message)
                continue
            else:
                message = "No tokens available. Please run the script again later."
                print(message)
                send_discord_message(message, is_error=True)
                return profit_items
        
        batch_rows = rows[i:i + BATCH_SIZE]
        batch_asins = []
        batch_indices = []
        batch_buy_prices = []
        
        # Collect ASINs and buy prices for the batch
        for idx, row in enumerate(batch_rows, start=i+2):
            asin_link = row[0]
            if not asin_link.strip():
                continue
                
            asin = asin_link.split("/dp/")[-1].split("/")[0]
            try:
                buy_price = float(row[2].replace("£", ""))
                batch_asins.append(asin)
                batch_indices.append(idx)
                batch_buy_prices.append(buy_price)
            except:
                print(f"Invalid buy price in row {idx}. Skipping.")
                continue
        
        if not batch_asins:
            i += BATCH_SIZE
            continue
            
        processed_rows += len(batch_asins)
        progress_message = f"Progress: {processed_rows}/{total_rows} ASINs processed in {ws.title}"
        print(f"\n{progress_message}")
        send_discord_message(progress_message)
        
        print(f"Processing batch of {len(batch_asins)} ASINs: {', '.join(batch_asins)}")
        
        # Save progress after each batch
        save_progress(ws.title, i + len(batch_rows))
        
        # Fetch data for the batch
        batch_data = fetch_keepa_data_batch(batch_asins)
        
        # Process each ASIN in the batch
        for asin, idx, buy_price in zip(batch_asins, batch_indices, batch_buy_prices):
            product_data = batch_data.get(asin)
            if not product_data:
                print(f"No data for ASIN {asin}")
                continue

            stats = product_data.get("stats") or {}

            # Get both sell price and seller count in one pass
            sell_price, sellers = process_offers(product_data)
            if sell_price == 0.0:
                sell_price = round(stats.get("buyBoxPrice", 0) / 100, 2)  # Then try buyBoxPrice
            if sell_price == 0.0:
                sell_price = extract_current_price_from_csv(product_data)
            if sell_price == 0.0:
                sell_price = extract_latest_price(product_data.get("buyBoxPriceHistory", []))

            spm = product_data.get("monthlySold") or 0

            fba_fees = product_data.get("fbaFees") or {}
            profit, roi = calculate_profits(buy_price, sell_price, fba_fees)
            # Calculate profit margin as (profit / sell_price) * 100
            profit_margin = round((profit / sell_price) * 100, 2) if sell_price > 0 else 0.0

            # Log summary
            print(f"ASIN: {asin}")
            print(f"Buy: £{buy_price} | Sell: £{sell_price} | SPM: {spm} | Sellers: {sellers}")
            print(f"Profit/unit: £{profit} | Profit Margin: {profit_margin}% | ROI: {roi}%")

            # Add to profit categories and send notifications based on margin
            item_info = f"ASIN: {asin} | Profit Margin: {profit_margin}% | ROI: {roi}%"
            if profit_margin > 15:
                profit_items['high_profit'].append(item_info)
                profit_message = (
                    f"@everyone :rotating_light: :red_circle: **BIG PROFIT MARGIN ALERT!** :red_circle: :rotating_light:\n"
                    f"ASIN: {asin}\n"
                    f"Profit Margin: **{profit_margin}%**\n"
                    f"ROI: {roi}%\n"
                    f"Buy Price: £{buy_price}\n"
                    f"Sell Price: £{sell_price}\n"
                    f"SPM: {spm}"
                )
                send_discord_message(profit_message)
            elif profit_margin > 10:
                profit_items['medium_profit'].append(item_info)
                profit_message = (
                    f"🟢 PROFIT MARGIN ALERT!\n"
                    f"ASIN: {asin}\n"
                    f"Profit Margin: {profit_margin}%\n"
                    f"ROI: {roi}%\n"
                    f"Buy Price: £{buy_price}\n"
                    f"Sell Price: £{sell_price}\n"
                    f"SPM: {spm}"
                )
                send_discord_message(profit_message)

            try:
                # Batch update A, E, F, G, H, I (preserving B-D)
                sheets_rate_limit()  # Apply rate limiting before Google Sheets API call
                values = [[
                    f"£{sell_price}",      # D
                    f"{roi:.2f}%",         # E
                    f"£{profit}",          # F
                    spm,                   # G
                    sellers,               # H
                    f"{profit_margin}%",   # I
                ]]
                ws.update(f"D{idx}:I{idx}", values)
            except Exception as e:
                error_msg = f"Error updating row {idx}: {str(e)}"
                print(f"⚠️ {error_msg}")
                send_discord_message(error_msg, is_error=True)
                continue
        
        i += BATCH_SIZE

    # If we've processed all rows, clear the progress file
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
    completion_message = f"Completed processing sheet {ws.title}"
    print(f"✅ {completion_message}")
    send_discord_message(completion_message)

    return profit_items

def update_all_sheets():
    worksheets = get_all_worksheets()
    all_profit_items = {
        'high_profit': [],
        'medium_profit': [],
        'low_profit': []
    }
    
    for ws in worksheets:
        print(f"\nProcessing sheet: {ws.title}")
        profit_items = update_sheet(ws)
        
        # Merge results
        for category in all_profit_items:
            all_profit_items[category].extend(profit_items[category])
    
    return all_profit_items

if __name__ == "__main__":
    update_all_sheets()
