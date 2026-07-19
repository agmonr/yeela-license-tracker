import asyncio
import os
from datetime import date
import pandas as pd
from playwright.async_api import async_playwright

ARCHIVE_DIR = "archive"

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
            dest = os.path.join(ARCHIVE_DIR, f"full_licenses_{date.today().isoformat()}.csv")
            df.to_csv(dest, index=False, encoding='utf-8-sig')
            os.remove(temp_xls)
            print(f"Saved as {dest}")

        except Exception as e:
            print(f"Error during download: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(download_full_list())
