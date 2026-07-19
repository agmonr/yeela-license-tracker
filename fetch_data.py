import asyncio
import json
import os
import sqlite3
from datetime import date
import pandas as pd
from playwright.async_api import async_playwright

ARCHIVE_DIR = "archive"
CACHE_DIR = "cache"
CACHE_DB_PATH = os.path.join(CACHE_DIR, "yeela_license_details.db")

LICENSE_COL = "מספר רישיון"
REQUEST_REASON_COL = "סיבת בקשה"

# The portal's own JSON API behind the Excel export - discovered by
# inspecting its network traffic. Needs no real auth (confirmed the ALB
# cookie it sets on first page load is sufficient even via plain HTTP
# outside the browser), and its grid response includes fields the Excel
# export doesn't (request reason, exact block/parcel, timestamps). Only
# used here to backfill request-reason for the still-open licenses (a few
# hundred rows), not to replace the Excel export as the source of the main
# CSV - that keeps every existing downstream column/format unchanged.
GRID_API_URL = "https://yeela-trees.moag.gov.il/api/Fo/FOServiceRequest/getFOGridPublicityLicenses"
OPEN_STATUS_CODE = 3  # "מושהה ופתוח להגשת השגה", per GetMultiLookupValues table 601


def get_cache_connection():
    """A license's request reason never changes once submitted, so once
    fetched it's cached here forever (gitignored - local operational
    state, not archival like archive/*.csv) and never re-requested from
    the API for a license_id we've already seen."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS license_reasons (
            license_id INTEGER PRIMARY KEY,
            request_reason TEXT,
            fetched_date TEXT
        )
    """)
    return conn


def load_cached_reasons(conn):
    rows = conn.execute("SELECT license_id, request_reason FROM license_reasons").fetchall()
    return {license_id: reason for license_id, reason in rows}


def save_reasons_to_cache(conn, reasons):
    if not reasons:
        return
    today = date.today().isoformat()
    conn.executemany(
        """
        INSERT INTO license_reasons (license_id, request_reason, fetched_date)
        VALUES (?, ?, ?)
        ON CONFLICT(license_id) DO UPDATE SET
            request_reason = excluded.request_reason,
            fetched_date = excluded.fetched_date
        """,
        [(license_id, reason, today) for license_id, reason in reasons.items()],
    )
    conn.commit()


async def fetch_open_license_reasons(page, already_cached):
    """Maps licenseId -> סיבת בקשה for every currently-open-for-objection
    license, via the portal's grid API (server caps pageSize at 100, so
    ~10 pages instead of the ~285 the full ~28k-row archive would need).
    Which licenses are open can only change day to day is inherently live
    state, so the paginated sweep itself can't be skipped - but licenses
    already in already_cached are recognized without needing a second
    lookup elsewhere, and every result gets folded into the persistent
    cache regardless of today's status, so once a license's reason is
    known it's known permanently. Never raises - a failure here shouldn't
    block the primary Excel-based snapshot."""
    reasons = {}
    page_number = 1
    try:
        while True:
            body = {
                "orderDetails": None,
                "pageDetails": {"pageNumber": page_number, "pageSize": 100},
                "parameters": {
                    "zoneId": None,
                    "cityId": None,
                    "appealLastDate": None,
                    "licenseId": None,
                    "licenseStatusId": OPEN_STATUS_CODE,
                },
            }
            resp = await page.request.post(
                GRID_API_URL,
                data=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )
            if resp.status != 200:
                print(f"Reason enrichment: API returned {resp.status}, stopping early.")
                break
            data = await resp.json()
            for row in data.get("result", []):
                expand = row.get("expandRows") or []
                if expand:
                    reasons[row["licenseId"]] = expand[0].get("requestReason", "")
            total_pages = data.get("pagination", {}).get("totalPages", page_number)
            if page_number >= total_pages or not data.get("result"):
                break
            page_number += 1
    except Exception as e:
        print(f"Reason enrichment failed ({e}), continuing with cached data only.")
        return {}
    new_count = sum(1 for lic in reasons if lic not in already_cached)
    print(f"Fetched reasons for {len(reasons)} currently-open licenses ({new_count} new, {len(reasons) - new_count} already cached).")
    return reasons


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

            print("Fetching request reasons for open licenses...")
            cache_conn = get_cache_connection()
            cached_reasons = load_cached_reasons(cache_conn)
            fresh_reasons = await fetch_open_license_reasons(page, cached_reasons)
            save_reasons_to_cache(cache_conn, fresh_reasons)
            all_reasons = {**cached_reasons, **fresh_reasons}
            cache_conn.close()

            df[REQUEST_REASON_COL] = df[LICENSE_COL].map(
                lambda lic: all_reasons.get(int(lic)) if pd.notna(lic) else None
            )
            print(f"Enriched {df[REQUEST_REASON_COL].notna().sum()} rows with request reasons ({len(all_reasons)} total cached).")

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
