"""
Example configuration for the yeela tree-license bot.

Copy this file to config.py and fill in real values:
    cp config.example.py config.py

config.py holds private data (a Sheet ID and personal email addresses)
and is gitignored — it must never be committed.
"""

# Google Sheet ID of the subscriber signup form responses.
# Found in the sheet's URL: https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit
SHEET_ID = "your-google-sheet-id-here"

# The sheet owner's own subscription is typed directly into the header
# cell text of columns B/C instead of as a normal response row, so it
# can't be read from the sheet and must be listed here explicitly as
# (email, city) pairs. Leave empty if this doesn't apply to your sheet.
OWNER_SUBSCRIPTIONS = [
    ("owner@example.com", "עיר לדוגמה'"),
]

# Recipients for the weekly summary report (covers all cities, unlike
# the daily per-subscriber emails which are filtered by city).
ADMIN_EMAILS = [
    "admin1@example.com",
    "admin2@example.com",
]
