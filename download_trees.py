import asyncio
import os
import pandas as pd
from playwright.async_api import async_playwright
from datetime import datetime
import subprocess
from sheet_subscribers import get_subscribers

def rotate_and_get_diff():
    """
    Rotates the full list versions and returns a DataFrame of truly new or modified rows.
    """
    base_name = "archive/full_licenses"
    v1_path = f"{base_name}_v1.csv"
    v2_path = f"{base_name}_v2.csv"

    # 1. Rotate existing files
    print("Rotating historical files...")
    os.makedirs("archive", exist_ok=True)
    for i in range(9, 0, -1):
        old_file = f"{base_name}_v{i}.csv"
        new_file = f"{base_name}_v{i+1}.csv"
        if os.path.exists(old_file):
            if i == 9 and os.path.exists(f"{base_name}_v10.csv"):
                os.remove(f"{base_name}_v10.csv")
            os.rename(old_file, new_file)

    # 2. Move new download to v1
    if os.path.exists("temp_full.csv"):
        if os.path.exists(v1_path): os.remove(v1_path)
        os.rename("temp_full.csv", v1_path)
        print(f"New data saved as {v1_path}")
    else:
        return None

    # 3. Compare v1 and v2
    if os.path.exists(v2_path):
        print("Detecting all new or modified entries...")
        # Load and normalize all data as strings
        df1 = pd.read_csv(v1_path, dtype=str).fillna('').map(lambda x: str(x).strip())
        df2 = pd.read_csv(v2_path, dtype=str).fillna('').map(lambda x: str(x).strip())
        
        # CRITICAL FIX: To handle Excel float issues, normalize numbers ending in .0
        def normalize_nums(val):
            if val.endswith('.0'): return val[:-2]
            return val
        
        df1 = df1.map(normalize_nums)
        df2 = df2.map(normalize_nums)

        # Merge with indicator to find rows in NEW file that are NOT in the OLD file
        # This catches both completely new licenses and existing ones that have any data change
        merged = df1.merge(df2, how='outer', indicator=True)
        diff_df = merged[merged['_merge'] == 'left_only'].drop('_merge', axis=1)
        
        return diff_df
    
    return None

async def download_full_list():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        page.set_default_timeout(120000)
        
        print("Connecting to portal...")
        try:
            await page.goto("https://yeela-trees.moag.gov.il/FoPublic/FoLicence", wait_until="domcontentloaded")
            
            # Expand search panel
            expand_btn = page.locator(".form-expand").first
            await expand_btn.wait_for(state="visible", timeout=60000)
            await expand_btn.click()
            await asyncio.sleep(3)
            
            print("Triggering Excel export...")
            async with page.expect_download(timeout=120000) as download_info:
                await page.get_by_text("יצוא תוצאות לאקסל").click()
            
            download = await download_info.value
            temp_xls = "temp_full.xlsx"
            await download.save_as(temp_xls)
            
            print("Converting to CSV...")
            df = pd.read_excel(temp_xls)
            df.to_csv("temp_full.csv", index=False, encoding='utf-8-sig')
            os.remove(temp_xls)
            print("Download successful.")
            
        except Exception as e:
            print(f"Error during download: {e}")
        finally:
            await browser.close()

def send_notifications(global_diff):
    if global_diff is None:
        print("Initial run: Data saved. No comparison possible yet.")
        return
        
    if global_diff.empty:
        print("No new or modified entries detected.")
        return

    print(f"Found {len(global_diff)} new or modified row(s).")

    subscribers = get_subscribers()
    if not subscribers:
        print("Error: could not load subscribers from Google Sheet.")
        return

    for email, city_raw in subscribers:
        city_key = city_raw.replace("'", "").replace('"', "")

        # Filter the diff for the city
        city_diff = global_diff[global_diff['ישוב'].str.contains(city_key, na=False)]

        if not city_diff.empty:
            print(f"-> Sending {len(city_diff)} updates to {email} ({city_key})")

            safe_city = "".join(x for x in city_key if x.isalnum())
            diff_filename = f"diff_{datetime.now().strftime('%Y%m%d_%H%M')}_{safe_city}.csv"
            city_diff.to_csv(diff_filename, index=False, encoding='utf-8-sig')

            subject = f"עדכון רישיונות כריתה - {city_key}"
            body = f"שלום,\n\nנמצאו {len(city_diff)} עדכונים או רישיונות חדשים ביישוב {city_key}.\n\nפרטים מצורפים בקובץ.\n\nבברכה."

            mail_cmd = f'echo "{body}" | mail -s "{subject}" -A "{diff_filename}" "{email}"'
            subprocess.run(mail_cmd, shell=True)

async def run():
    await download_full_list()
    diff = rotate_and_get_diff()
    send_notifications(diff)

if __name__ == "__main__":
    asyncio.run(run())
