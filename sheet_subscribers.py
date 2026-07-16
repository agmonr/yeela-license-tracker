import pandas as pd
from config import SHEET_ID, OWNER_SUBSCRIPTIONS

SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv"

def get_subscribers():
    """
    Fetches (email, city) pairs from the subscriber Google Sheet.
    Returns a list of (email, city) tuples.
    """
    try:
        df = pd.read_csv(SHEET_CSV_URL, dtype=str).fillna('')
    except Exception as e:
        print(f"Error fetching subscriber sheet: {e}")
        return []

    email_col, city_col = df.columns[1], df.columns[2]

    subscribers = list(OWNER_SUBSCRIPTIONS)
    for _, row in df.iterrows():
        email = row[email_col].strip()
        city = row[city_col].strip()
        if email and city:
            subscribers.append((email, city))
    return subscribers
