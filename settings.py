"""
Edit this file to set up the centre's branding and email.
Restart the app (Ctrl+C, then run uvicorn again) after changing it.
"""

# ---------- Branding (shown on the report and email) ----------
CENTRE_NAME = "Your Centre"
CENTRE_TAGLINE = "Selective Entry Coaching"
# Path to a logo image (PNG or JPG), e.g. "logo.png" in this folder.
# Leave as None to use the placeholder logo (the centre's initials).
LOGO_FILE = None
BRAND_COLOUR = "#4338CA"   # main colour (indigo). Any hex colour works.

# ---------- Email (Gmail) ----------
# 1. Turn on 2-Step Verification for this Google account.
# 2. Create an App Password: https://myaccount.google.com/apppasswords
# 3. Paste the 16-letter password below (spaces are fine). Never share this file.
GMAIL_ADDRESS = "your.address@gmail.com"
GMAIL_APP_PASSWORD = ""

# While testing, every report goes to this address instead of the parent's.
# Set to None when you are ready to email real parents (uses students.csv).
SEND_ALL_TO = "your.test.inbox@example.com"
