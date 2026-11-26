# Live Appointment Booking: setup and testing

Bookings are real Google Calendar events on YOUR calendar. A time is "taken" exactly when your calendar says you are busy,
so meetings you add yourself on your phone also block the website automatically. Google Meet links are created automatically.

## What changed
| File | Change |
|---|---|
| `static/index.html` | "LET'S TALK" button became "Book an Appointment" (same style). Booking window added (CSS + HTML + JS, appended). Nothing else touched |
| `booking.py` | NEW. All booking logic (availability, booking, cancel, reschedule). No new pip packages |
| `main.py` | 2 lines added to register `booking.py` |
| `knowledge.json` | New `booking` block (days, slot step, notice, how far ahead) |
| `get_google_token.py` | NEW. One-time helper to connect your Google account |
| `.env.example`, `.gitignore` | New variables, `client_secret*.json` ignored |
| `tests/test_booking_offline.py` | NEW. 25 tests with a fake Google Calendar |

## One-time Google setup (about 10 minutes, free)
1. Go to https://console.cloud.google.com, create a project, then **APIs and Services, Library, Google Calendar API, Enable**.
2. **OAuth consent screen**: User type **External**, fill app name and your Gmail. Under Scopes add `.../auth/calendar.events`. Under Test users add your Gmail.
   **Then click "Publish app" (In production).** If it stays in "Testing", Google deletes the login after 7 days and booking stops.
3. **Credentials, Create credentials, OAuth client ID, Desktop app**. Download the JSON and save it in the project folder as `client_secret.json`.
4. In the terminal:
   ```
   pip install google-auth-oauthlib
   python get_google_token.py
   ```
   A browser opens. Sign in with the Gmail whose calendar you want to use. Google will say "Google hasn't verified this app": click **Advanced, Go to (your app)**, then Allow.
5. The terminal prints 3 lines. Paste them into `.env` (and into Render's Environment Variables). Delete `client_secret.json` afterwards. Never upload it.
6. Add `SITE_URL` (your live address, for example https://yourname.onrender.com). Locally use http://127.0.0.1:5000.
7. Restart `python main.py`.

## Deploy note (important)
Use `gunicorn main:app --workers 1 --threads 4` as the Render start command. One worker is what guarantees two people can never take the same slot at the same moment.

## Settings (knowledge.json)
- Meeting hours = `availability.open_hour` / `close_hour` (now 9 to 15, Bangladesh time).
- `booking.days`: 0=Monday ... 6=Sunday. Now all 7 days are open; remove numbers for days off (for example remove 4 and 5 for Friday and Saturday).
- `slot_step_minutes` (30), `min_notice_minutes` (120: nobody can book in the next 2 hours), `max_days_ahead` (60), `buffer_minutes` (0).
- Block a day yourself by adding an all-day event, or a busy event, to your Google Calendar.

## Emails
- **Client:** Google Calendar sends the client an invitation (with Meet link and time) automatically. This works without any domain.
  The free Resend sender can only email you, not clients. To also send a branded email to clients, verify a domain at resend.com and set `RESEND_FROM`.
- **You:** an email through Resend (`NOTIFY_TO`) plus the event on your calendar.

## Test real bookings
1. Run `python main.py`, open http://127.0.0.1:5000, click **Book an Appointment**.
2. Book a slot using a second email address of yours. Check: the event appears on your Google Calendar, the Meet link works, the invitation reaches the second email, you get the notification email.
3. Reopen the booking window: that time must be gone. Try the same slot from another browser or phone: it must be refused.
4. Click **Reschedule**, then **Cancel**: the calendar event moves, then disappears, and the slot opens again.
5. Open the "cancel or reschedule" link from the event description (it ends with `#manage=...`): it must show your booking.
6. Offline tests: `python -m unittest discover -s tests -v`

## Limits (honest)
- A booking shows as confirmed only after Google Calendar accepted it. If Google is unreachable the visitor sees "not confirmed" and can retry or contact you.
- The Meet link needs a Google account that supports Meet (a normal Gmail does). If Google is slow to create it, the confirmation says it will arrive in the calendar invitation.
- Someone who knows a booking's secret link can cancel it. The link is only in that client's own invitation.
