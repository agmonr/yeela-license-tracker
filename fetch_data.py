import asyncio
import json
import math
import os
import sqlite3
import sys
from datetime import date
from urllib.parse import quote_plus
import pandas as pd
from playwright.async_api import async_playwright

# Stdout is fully block-buffered (not line-buffered) when it's not a TTY -
# e.g. redirected to a log file by a background runner - so without this,
# progress prints below only become visible after the buffer fills or the
# process exits, making a long-running scrape look hung when it isn't.
sys.stdout.reconfigure(line_buffering=True)

ARCHIVE_DIR = "archive"
CACHE_DIR = "cache"
CACHE_DB_PATH = os.path.join(CACHE_DIR, "yeela_license_details.db")

LICENSE_COL = "מספר רישיון"
REQUEST_REASON_COL = "סיבת בקשה"
CITY_COL = "ישוב"
STREET_COL = "רחוב ומספר בית"
GUSH_COL = "גוש"
HELKA_COL = "חלקה"

# Only ~83% of exported rows carry גוש/חלקה from the portal itself - the
# rest are backfilled below via address geocoding, scoped to currently-open
# licenses only (the actionable set an objector needs the parcel for).
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "yeela-license-tracker (github.com/agmonr/yeela-license-tracker)"
GOVMAP_WFS_URL = "https://open.govmap.gov.il/geoserver/opendata/wfs"
EARTH_RADIUS_M = 6378137.0  # for lon/lat -> EPSG:3857, which PARCEL_ALL's the_geom is natively stored in
# Playwright's page-level default (120s) is sized for the portal itself;
# left on these enrichment calls, one unresponsive government endpoint
# among hundreds of sequential lookups could stall the whole run for
# minutes. GovMap's WFS in particular has been observed taking 10-20s on
# an otherwise-healthy request (verified live: 14.3s and 9.7s round trips
# for the same parcel back to back), so this needs real headroom above
# that rather than assuming sub-second responses.
ENRICHMENT_TIMEOUT_MS = 45000

# מנהל התכנון's own planning-plan layer - separate from GovMap. A parcel
# is usually covered by several plans (a small local one plus large
# national/metro ones that also happen to intersect it); pl_area_dunam is
# what makes the small, specific plan findable instead of buried under a
# 100,000+ dunam תמ"א.
XPLAN_URL = "https://ags.iplan.gov.il/arcgisiplan/rest/services/PlanningPublic/Xplan/MapServer/1/query"
PLAN_NUMBER_COL = "מספר תכנית"
PLAN_URL_COL = "קישור לתכנית"

# GovMap's own public viewer - c=<x,y> (EPSG:2039, Israel's ITM grid) and
# z=12 with b=1 (aerial photo background) together land tightly zoomed on
# a single parcel/building, not just the neighborhood (verified live: z=7
# gives ~1:35,000, z=12 gives ~1:1,000). Reuses the same WFS geometry
# fetch already made for the plan lookup below rather than a second call.
GOVMAP_VIEW_URL = "https://www.govmap.gov.il/"
GOVMAP_URL_COL = "קישור ל-GovMap"

# The portal's own JSON API behind its UI - discovered by inspecting its
# network traffic. Needs no real auth (confirmed the ALB cookie it sets on
# first page load is sufficient even via plain HTTP outside the browser).
GRID_API_URL = "https://yeela-trees.moag.gov.il/api/Fo/FOServiceRequest/getFOGridPublicityLicenses"
OPEN_STATUS_CODE = 3  # "מושהה ופתוח להגשת השגה", per GetMultiLookupValues table 601

# Same endpoint the "יצוא תוצאות לאקסל" button calls - verified
# byte-for-byte identical to the UI-triggered download (same 28,454 rows,
# same 17 columns), including the exact pageDetails/parameters body a real
# UI click sends (pageSize is ignored server-side; the export always
# returns the full filtered result set). Calling it directly skips the
# UI's expand-panel-then-click-button flow entirely, so there's no
# .form-expand/text-locator to break if the portal's frontend changes.
EXPORT_API_URL = "https://yeela-trees.moag.gov.il/api/Fo/FOServiceRequest/exportRecordsToExcel"
EXPORT_BODY = {"orderDetails": None, "pageDetails": {"pageNumber": 1, "pageSize": 20}, "parameters": {"appealLastDate": None}}


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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parcel_lookups (
            address_key TEXT PRIMARY KEY,
            gush TEXT,
            helka TEXT,
            fetched_date TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plan_lookups (
            parcel_key TEXT PRIMARY KEY,
            plan_number TEXT,
            plan_url TEXT,
            fetched_date TEXT
        )
    """)
    try:
        conn.execute("ALTER TABLE plan_lookups ADD COLUMN govmap_url TEXT")
    except sqlite3.OperationalError:
        pass  # already migrated on a previous run
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


def address_key(city, street):
    city = str(city).strip() if pd.notna(city) else ""
    street = str(street).strip() if pd.notna(street) else ""
    return f"{city}|{street}"


def load_cached_parcels(conn):
    rows = conn.execute("SELECT address_key, gush, helka FROM parcel_lookups").fetchall()
    return {key: (gush, helka) for key, gush, helka in rows}


def save_parcels_to_cache(conn, parcels):
    if not parcels:
        return
    today = date.today().isoformat()
    conn.executemany(
        """
        INSERT INTO parcel_lookups (address_key, gush, helka, fetched_date)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(address_key) DO UPDATE SET
            gush = excluded.gush,
            helka = excluded.helka,
            fetched_date = excluded.fetched_date
        """,
        [(key, gush, helka, today) for key, (gush, helka) in parcels.items()],
    )
    conn.commit()


def lonlat_to_web_mercator(lon, lat):
    x = lon * math.pi / 180 * EARTH_RADIUS_M
    y = math.log(math.tan(math.pi / 4 + lat * math.pi / 360)) * EARTH_RADIUS_M
    return x, y


def parcel_key(gush, helka):
    return f"{gush}|{helka}"


def load_cached_plans(conn):
    rows = conn.execute("SELECT parcel_key, plan_number, plan_url, govmap_url FROM plan_lookups").fetchall()
    return {key: (number, url, govmap_url) for key, number, url, govmap_url in rows}


def save_plans_to_cache(conn, plans):
    if not plans:
        return
    today = date.today().isoformat()
    conn.executemany(
        """
        INSERT INTO plan_lookups (parcel_key, plan_number, plan_url, govmap_url, fetched_date)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(parcel_key) DO UPDATE SET
            plan_number = excluded.plan_number,
            plan_url = excluded.plan_url,
            govmap_url = excluded.govmap_url,
            fetched_date = excluded.fetched_date
        """,
        [(key, number, url, govmap_url, today) for key, (number, url, govmap_url) in plans.items()],
    )
    conn.commit()


def first_point_from_esri_rings(geometry):
    """Esri/GeoJSON polygon and multipolygon coordinates are arbitrarily
    nested rings of [x, y] pairs - drill down to the first actual point,
    which is enough to intersect-query Xplan (any point inside the
    parcel works; we don't need the full shape)."""
    coords = geometry["coordinates"]
    while isinstance(coords[0], list):
        coords = coords[0]
    return coords[0], coords[1]


async def geocode_parcel(page, city, street):
    """address -> (gush, helka) via OSM Nominatim geocoding, then a GovMap
    WFS point-intersection query against the PARCEL_ALL cadastre layer
    (native CRS EPSG:3857 - lon/lat degrees silently return zero features,
    hence the reprojection). Returns (None, None) on any failure (no
    geocode match, either API erroring, unexpected shape) rather than
    raising - this enrichment is best-effort and must never block the
    primary snapshot."""
    address = f"{street}, {city}, ישראל" if street else f"{city}, ישראל"
    try:
        geo_resp = await page.request.get(
            NOMINATIM_URL,
            params={"format": "json", "q": address, "countrycodes": "il", "limit": "1"},
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=ENRICHMENT_TIMEOUT_MS,
        )
        if geo_resp.status != 200:
            return None, None
        geo_results = await geo_resp.json()
        if not geo_results:
            return None, None
        lat, lon = float(geo_results[0]["lat"]), float(geo_results[0]["lon"])

        x, y = lonlat_to_web_mercator(lon, lat)
        wfs_resp = await page.request.get(
            GOVMAP_WFS_URL,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": "opendata:PARCEL_ALL",
                "outputFormat": "application/json",
                "propertyName": "GUSH_NUM,PARCEL",
                "CQL_FILTER": f"INTERSECTS(the_geom,POINT({x} {y}))",
            },
            timeout=ENRICHMENT_TIMEOUT_MS,
        )
        if wfs_resp.status != 200:
            return None, None
        wfs_data = await wfs_resp.json()
        features = wfs_data.get("features") or []
        if not features:
            return None, None
        props = features[0]["properties"]
        gush, helka = props.get("GUSH_NUM"), props.get("PARCEL")
        if gush is None or helka is None:
            return None, None
        return str(gush), str(helka)
    except Exception as e:
        print(f"Parcel lookup failed for '{address}' ({e}), skipping.")
        return None, None


async def fill_missing_parcels(page, conn, df, open_license_ids, already_cached):
    """Backfills GUSH_COL/HELKA_COL for currently-open licenses whose
    portal export left them blank, one geocode+WFS lookup per distinct
    (city, street) address rather than per row - many licenses share a
    site. Nominatim's usage policy caps at 1 req/sec, so this is
    deliberately sequential with a sleep between calls; scoping to only
    open licenses (tens of addresses/day, not the ~1,000 missing across
    the full archive) keeps that bound reasonable. Each successful
    lookup is cached immediately (not batched until the end) so an
    interrupted run keeps whatever progress it already made instead of
    losing it all; a transient failure just gets retried on a later run
    instead of being permanently blank."""
    missing = df[
        df[LICENSE_COL].isin(open_license_ids)
        & df[GUSH_COL].isna()
    ]
    if missing.empty:
        return {}

    addresses = missing[[CITY_COL, STREET_COL]].drop_duplicates()
    to_fetch = [
        (city, street)
        for city, street in addresses.itertuples(index=False)
        if address_key(city, street) not in already_cached
    ]
    if not to_fetch:
        return {}

    print(f"Looking up גוש/חלקה for {len(to_fetch)} addresses missing it among open licenses...")
    resolved = {}
    for i, (city, street) in enumerate(to_fetch):
        gush, helka = await geocode_parcel(page, city, street)
        status = f"{gush}/{helka}" if gush is not None else "not found"
        print(f"  [{i + 1}/{len(to_fetch)}] {street}, {city} -> {status}")
        if gush is not None:
            key = address_key(city, street)
            resolved[key] = (gush, helka)
            save_parcels_to_cache(conn, {key: (gush, helka)})
        if i < len(to_fetch) - 1:
            await asyncio.sleep(1)  # Nominatim usage policy: max 1 req/sec
    print(f"Resolved {len(resolved)}/{len(to_fetch)} addresses.")
    return resolved


async def lookup_parcel_geometry(page, gush, helka):
    """GUSH_NUM/PARCEL are an exact attribute match against GovMap's
    cadastre - no geocoding needed here, unlike geocode_parcel above.
    Compound חלקה values (e.g. "123-16,214", representing several source
    parcels merged into one license row) don't match any single WFS
    feature and simply return None - that's an honest "can't resolve
    this one" rather than a wrong guess.

    Requested in EPSG:2039 (Israel's ITM grid), not the layer's native
    EPSG:3857 - verified live that Xplan's inSR also accepts 2039
    directly (same results as 3857), and 2039 is what GovMap's own
    public viewer URL (?c=x,y&z=..&b=1) expects, so one fetch serves
    both the plan lookup and the GovMap deep link below."""
    try:
        resp = await page.request.get(
            GOVMAP_WFS_URL,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": "opendata:PARCEL_ALL",
                "outputFormat": "application/json",
                "srsName": "EPSG:2039",
                "CQL_FILTER": f"GUSH_NUM={gush} AND PARCEL={helka}",
            },
            timeout=ENRICHMENT_TIMEOUT_MS,
        )
        if resp.status != 200:
            return None
        data = await resp.json()
        features = data.get("features") or []
        if not features:
            return None
        return features[0]["geometry"]
    except Exception as e:
        print(f"Parcel geometry lookup failed for גוש {gush} חלקה {helka} ({e}), skipping.")
        return None


async def lookup_plan(page, gush, helka, city, street):
    """(gush, helka) -> (plan_number, plan_url, govmap_url).

    plan_number/plan_url are for the smallest (most specific) מנהל
    התכנון plan covering that parcel - ranked by pl_area_dunam
    ascending, since a parcel is typically covered by both a small
    local plan and one or more sprawling national/metro plans (e.g. a
    תמ"א spanning 100,000+ dunam) that are technically correct but
    useless as an answer.

    govmap_url is set as soon as the parcel's geometry resolves,
    independently of whether a plan is found - a link straight to the
    parcel's aerial photo on GovMap is useful on its own, so a
    "no plan found" parcel still gets a map link rather than nothing.

    Returns (None, None, None) only if the geometry itself can't be
    resolved; otherwise govmap_url is populated even when the plan
    lookup fails or finds nothing."""
    geometry = await lookup_parcel_geometry(page, gush, helka)
    if geometry is None:
        return None, None, None
    x, y = first_point_from_esri_rings(geometry)
    street = str(street).strip() if pd.notna(street) else ""
    city = str(city).strip() if pd.notna(city) else ""
    address = f"{street} {city}".strip()
    govmap_url = f"{GOVMAP_VIEW_URL}?c={x},{y}&z=12&b=1&q={quote_plus(address)}"
    try:
        resp = await page.request.get(
            XPLAN_URL,
            params={
                "geometry": f"{x},{y}",
                "geometryType": "esriGeometryPoint",
                "inSR": "2039",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "pl_number,pl_area_dunam,pl_url",
                "f": "json",
            },
            timeout=ENRICHMENT_TIMEOUT_MS,
        )
        if resp.status != 200:
            return None, None, govmap_url
        data = await resp.json()
        features = data.get("features") or []
        if not features:
            return None, None, govmap_url
        plans = sorted(
            (f["attributes"] for f in features),
            key=lambda a: a.get("pl_area_dunam") if a.get("pl_area_dunam") is not None else float("inf"),
        )
        best = plans[0]
        plan_number, plan_url = best.get("pl_number"), best.get("pl_url")
        if not plan_number or not plan_url:
            return None, None, govmap_url
        return str(plan_number), str(plan_url), govmap_url
    except Exception as e:
        print(f"Plan lookup failed for גוש {gush} חלקה {helka} ({e}), skipping.")
        return None, None, govmap_url


async def fill_plan_links(page, conn, df, open_license_ids, already_cached):
    """Looks up the building-plan link for every distinct (גוש, חלקה)
    among currently-open licenses that has both fields known (whether
    from the portal export directly or from fill_missing_parcels above)
    and isn't already cached. Scoped to open licenses for the same
    reason as fill_missing_parcels - this is the actionable set, and
    parcel-plan mappings rarely change so the cache carries most of the
    cost after the first run. Each result is cached immediately (see
    fill_missing_parcels' docstring for why) rather than batched until
    this whole (potentially long, sequential) loop finishes.

    Deliberately sequential, not concurrent: GovMap's WFS has been
    observed taking 10-20s per request even one at a time (see
    ENRICHMENT_TIMEOUT_MS), suggesting the server itself is the
    bottleneck right now - adding concurrent load on top of that is
    more likely to cause more timeouts than to finish faster."""
    open_rows = df[df[LICENSE_COL].isin(open_license_ids) & df[GUSH_COL].notna() & df[HELKA_COL].notna()]
    if open_rows.empty:
        return {}

    # One representative (city, street) per parcel, for the GovMap link's
    # &q= address text - a parcel is one lookup regardless of how many
    # license rows share it, so this just takes whichever address that
    # parcel's first matching row happens to have.
    parcels = open_rows[[GUSH_COL, HELKA_COL, CITY_COL, STREET_COL]].drop_duplicates(subset=[GUSH_COL, HELKA_COL])
    to_fetch = [
        (gush, helka, city, street)
        for gush, helka, city, street in parcels.itertuples(index=False)
        # (None, None, None) covers both "never cached" and rows cached by
        # a version of this code before govmap_url existed (added via an
        # ALTER TABLE migration, so those rows have it NULL) - both need
        # a real fetch, not just a lookup by key presence.
        if already_cached.get(parcel_key(gush, helka), (None, None, None))[2] is None
    ]
    if not to_fetch:
        return {}

    print(f"Looking up building plans and map links for {len(to_fetch)} parcels among open licenses...")
    resolved = {}
    n_with_plan = 0
    for i, (gush, helka, city, street) in enumerate(to_fetch):
        plan_number, plan_url, govmap_url = await lookup_plan(page, gush, helka, city, street)
        if plan_number is not None:
            status = plan_number
            n_with_plan += 1
        else:
            status = "no plan found" if govmap_url is not None else "unresolved"
        print(f"  [{i + 1}/{len(to_fetch)}] גוש {gush} חלקה {helka} -> {status}")
        if govmap_url is not None:
            key = parcel_key(gush, helka)
            resolved[key] = (plan_number, plan_url, govmap_url)
            save_plans_to_cache(conn, {key: (plan_number, plan_url, govmap_url)})
    print(f"Resolved {len(resolved)}/{len(to_fetch)} parcels to a map link ({n_with_plan} with a matching plan).")
    return resolved


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
            await page.goto("https://yeela-trees.moag.gov.il/FoPublic/FoLicence", wait_until="networkidle")

            print("Fetching Excel export directly via API...")
            resp = await page.request.post(
                EXPORT_API_URL,
                data=json.dumps(EXPORT_BODY),
                headers={"Content-Type": "application/json", "Accept": "application/json", "userorgroleid": ""},
            )
            if resp.status != 200:
                raise RuntimeError(f"Export API returned HTTP {resp.status}")
            content = await resp.body()

            os.makedirs(ARCHIVE_DIR, exist_ok=True)
            temp_xls = os.path.join(ARCHIVE_DIR, "temp_full.xlsx")
            with open(temp_xls, "wb") as f:
                f.write(content)

            print("Converting to CSV...")
            df = pd.read_excel(temp_xls)

            print("Fetching request reasons for open licenses...")
            cache_conn = get_cache_connection()
            cached_reasons = load_cached_reasons(cache_conn)
            fresh_reasons = await fetch_open_license_reasons(page, cached_reasons)
            save_reasons_to_cache(cache_conn, fresh_reasons)
            all_reasons = {**cached_reasons, **fresh_reasons}

            df[REQUEST_REASON_COL] = df[LICENSE_COL].map(
                lambda lic: all_reasons.get(int(lic)) if pd.notna(lic) else None
            )
            print(f"Enriched {df[REQUEST_REASON_COL].notna().sum()} rows with request reasons ({len(all_reasons)} total cached).")

            print("Backfilling missing גוש/חלקה for open licenses...")
            open_license_ids = set(fresh_reasons.keys())
            cached_parcels = load_cached_parcels(cache_conn)
            fresh_parcels = await fill_missing_parcels(page, cache_conn, df, open_license_ids, cached_parcels)
            all_parcels = {**cached_parcels, **fresh_parcels}

            missing_mask = df[GUSH_COL].isna()
            keys = df.loc[missing_mask].apply(lambda r: address_key(r[CITY_COL], r[STREET_COL]), axis=1)
            filled = keys.map(lambda k: all_parcels.get(k, (None, None)))
            df.loc[missing_mask, GUSH_COL] = filled.map(lambda t: t[0])
            df.loc[missing_mask, HELKA_COL] = filled.map(lambda t: t[1])
            print(f"Backfilled {filled.map(lambda t: t[0] is not None).sum()} rows with geocoded גוש/חלקה.")

            print("Looking up building plans (מנהל התכנון) and GovMap links for open licenses...")
            cached_plans = load_cached_plans(cache_conn)
            fresh_plans = await fill_plan_links(page, cache_conn, df, open_license_ids, cached_plans)
            all_plans = {**cached_plans, **fresh_plans}
            cache_conn.close()

            has_parcel_mask = df[GUSH_COL].notna() & df[HELKA_COL].notna()
            parcel_keys = df.loc[has_parcel_mask].apply(lambda r: parcel_key(r[GUSH_COL], r[HELKA_COL]), axis=1)
            plan_values = parcel_keys.map(lambda k: all_plans.get(k, (None, None, None)))
            df.loc[has_parcel_mask, PLAN_NUMBER_COL] = plan_values.map(lambda t: t[0])
            df.loc[has_parcel_mask, PLAN_URL_COL] = plan_values.map(lambda t: t[1])
            df.loc[has_parcel_mask, GOVMAP_URL_COL] = plan_values.map(lambda t: t[2])
            print(f"Linked {plan_values.map(lambda t: t[0] is not None).sum()} rows to a building plan, "
                  f"{plan_values.map(lambda t: t[2] is not None).sum()} rows to a GovMap link.")

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
