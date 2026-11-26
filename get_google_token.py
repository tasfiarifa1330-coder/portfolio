"""ONE-TIME helper: signs in to YOUR Google account and prints the 3 lines to paste into .env
so the website can read and write YOUR calendar.

Before running:  pip install google-auth-oauthlib
and put the downloaded OAuth file next to this script as  client_secret.json  (see README_BOOKING.md).
"""
import json, glob
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
files = glob.glob("client_secret*.json")
if not files:
    raise SystemExit("client_secret.json not found in this folder. See README_BOOKING.md, step 3.")
flow = InstalledAppFlow.from_client_secrets_file(files[0], SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
if not creds.refresh_token:
    raise SystemExit("Google did not return a refresh token. Remove this app at myaccount.google.com/permissions and run again.")
info = json.load(open(files[0]))
info = info.get("installed") or info.get("web")
print("\nPaste these 3 lines into your .env (and into Render's Environment Variables). Keep them secret:\n")
print("GOOGLE_CLIENT_ID=" + info["client_id"])
print("GOOGLE_CLIENT_SECRET=" + info["client_secret"])
print("GOOGLE_REFRESH_TOKEN=" + creds.refresh_token)
