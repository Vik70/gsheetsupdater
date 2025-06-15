from gsheets import fetch_keepa_data_batch, process_offers

def debug_asin(asin):
    # Fetch data for the ASIN
    product_data = fetch_keepa_data_batch([asin]).get(asin)
    if not product_data:
        print(f"No data found for ASIN {asin}")
        return

    # Process the offers
    sell_price, sellers = process_offers(product_data)
    print(f"\nFinal Results:")
    print(f"Sell Price: £{sell_price}")
    print(f"Number of Sellers: {sellers}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python debug_asin.py <ASIN>")
        sys.exit(1)
    debug_asin(sys.argv[1]) 