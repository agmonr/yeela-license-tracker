import os
import pandas as pd
from datetime import datetime
import time
from sheet_subscribers import get_subscribers
from mailer import send_html_mail

def get_diff():
    """
    Compares v1 and v2 and returns a DataFrame of both added and removed rows.
    """
    v1_path = "archive/full_licenses_v1.csv"
    v2_path = "archive/full_licenses_v2.csv"
    
    if not os.path.exists(v1_path) or not os.path.exists(v2_path):
        print("Need both v1 and v2 files to compare.")
        return None

    print("Detecting all changes (additions and deletions)...")
    # Load and normalize
    df_new = pd.read_csv(v1_path, dtype=str).fillna('').map(lambda x: str(x).strip())
    df_old = pd.read_csv(v2_path, dtype=str).fillna('').map(lambda x: str(x).strip())
    
    def normalize_nums(val):
        if val.endswith('.0'): return val[:-2]
        return val
    
    df_new = df_new.map(normalize_nums)
    df_old = df_old.map(normalize_nums)

    # Use merge to find differences
    merged = df_new.merge(df_old, how='outer', indicator=True)
    
    # Rows in v1 but not v2
    added = merged[merged['_merge'] == 'left_only'].copy()
    added['סוג שינוי'] = 'חדש/עודכן'
    
    # Rows in v2 but not v1
    removed = merged[merged['_merge'] == 'right_only'].copy()
    removed['סוג שינוי'] = 'הוסר מהמערכת'

    all_changes = pd.concat([added, removed], ignore_index=True).drop('_merge', axis=1)
    
    return all_changes

def send_notifications(diff_df):
    if diff_df is None or diff_df.empty:
        print("No changes detected.")
        return

    subscribers = get_subscribers()
    if not subscribers:
        print("Error: could not load subscribers from Google Sheet.")
        return

    print(f"Found {len(diff_df)} total row changes. Checking against {len(subscribers)} subscriber(s)...")

    for email, city_raw in subscribers:
        city_key = city_raw.replace("'", "").replace('"', "")

        city_diff = diff_df[diff_df['ישוב'].str.contains(city_key, na=False)]

        if not city_diff.empty:
            print(f"-> Sending {len(city_diff)} changes to {email} for {city_key}")

            # Create HTML table with CSS for styling
            html_table = city_diff.to_html(index=False, classes='diff-table', border=0)

            # Full HTML structure with RTL support for Hebrew
            html_body = f"""
<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<style>
    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; direction: rtl; text-align: right; background-color: #f4f7f6; padding: 20px; }}
    .container {{ background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
    h2 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
    .diff-table {{ border-collapse: collapse; width: 100%; margin-top: 20px; font-size: 13px; }}
    .diff-table th, .diff-table td {{ border: 1px solid #e0e0e0; padding: 12px 8px; text-align: right; }}
    .diff-table th {{ background-color: #3498db; color: white; white-space: nowrap; }}
    .diff-table tr:nth-child(even) {{ background-color: #f2f2f2; }}
    .diff-table tr:hover {{ background-color: #e1f5fe; }}
    .status-new {{ color: #27ae60; font-weight: bold; }}
    .status-removed {{ color: #c0392b; font-weight: bold; }}
    .footer {{ margin-top: 30px; font-size: 12px; color: #7f8c8d; border-top: 1px solid #eee; padding-top: 10px; }}
</style>
</head>
<body>
    <div class="container">
        <h2>עדכון רישיונות כריתה - {city_key}</h2>
        <p>שלום,</p>
        <p>נמצאו <strong>{len(city_diff)}</strong> שינויים ברשימת הרישיונות עבור היישוב <strong>{city_key}</strong>.</p>
        
        {html_table}
        
        <div class="footer">
            הודעה זו נשלחה באופן אוטומטי על ידי בוט מעקב רישיונות כריתה.<br>
            תאריך הפקה: {datetime.now().strftime('%d/%m/%Y %H:%M')}
        </div>
    </div>
</body>
</html>
"""
            # Post-process the table to add coloring classes if needed
            html_body = html_body.replace('חדש/עודכן', '<span class="status-new">חדש/עודכן</span>')
            html_body = html_body.replace('הוסר מהמערכת', '<span class="status-removed">הוסר מהמערכת</span>')

            # Save local copy for debugging
            os.makedirs("tmp", exist_ok=True)
            debug_filename = f"tmp/last_mail_{city_key.replace(' ', '_')}.html"
            with open(debug_filename, "w", encoding="utf-8") as df:
                df.write(html_body)
            print(f"   Debug HTML saved to: {debug_filename}")

            # Send mail with HTML content
            subject = f"שינויים ברישיונות כריתה - {city_key}"
            send_html_mail([email], subject, html_body)
            time.sleep(1)
        else:
            print(f"-> No relevant changes for {city_key} ({email}).")

if __name__ == "__main__":
    diff = get_diff()
    send_notifications(diff)
