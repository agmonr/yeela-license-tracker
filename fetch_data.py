import asyncio
import os
import pandas as pd
from playwright.async_api import async_playwright

ARCHIVE_DIR = "archive"

def rotate_files():
    """
    Shifts existing files: v9 -> v10, ..., v1 -> v2.
    """
    base_name = os.path.join(ARCHIVE_DIR, "full_licenses")
    print("Rotating historical files...")
    for i in range(365, 0, -1):
        old_file = f"{base_name}_v{i}.csv"
        new_file = f"{base_name}_v{i+1}.csv"
        if os.path.exists(old_file):
            if i == 9 and os.path.exists(f"{base_name}_v10.csv"):
                os.remove(f"{base_name}_v10.csv")
            os.rename(old_file, new_file)

    temp_csv = os.path.join(ARCHIVE_DIR, "temp_full.csv")
    if os.path.exists(temp_csv):
        v1_path = f"{base_name}_v1.csv"
        if os.path.exists(v1_path): os.remove(v1_path)
        os.rename(temp_csv, v1_path)
        print(f"New data saved as {v1_path}")

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
            
            # Expand search panel to ensure export button is ready
            expand_btn = page.locator(".form-expand").first
            await expand_btn.wait_for(state="visible", timeout=60000)
            await expand_btn.click()
            await asyncio.sleep(3)
            
            print("Triggering Excel export...")
            async with page.expect_download(timeout=120000) as download_info:
                await page.get_by_text("יצוא תוצאות לאקסל").click()
            
            os.makedirs(ARCHIVE_DIR, exist_ok=True)
            download = await download_info.value
            temp_xls = os.path.join(ARCHIVE_DIR, "temp_full.xlsx")
            await download.save_as(temp_xls)

            print("Converting to CSV...")
            df = pd.read_excel(temp_xls)
            df.to_csv(os.path.join(ARCHIVE_DIR, "temp_full.csv"), index=False, encoding='utf-8-sig')
            os.remove(temp_xls)
            print("Download and conversion successful.")
            
        except Exception as e:
            print(f"Error during download: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(download_full_list())
    rotate_files()
