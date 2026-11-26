"""Live appointment booking (Google Calendar). Registered in main.py as a Flask blueprint.

Google Calendar is the database: every booking is a real calendar event on YOUR calendar, so a slot
is "taken" exactly when your calendar says you are busy. No extra database and no new pip packages.
Secrets come from .env only. See README_BOOKING.md for the one-time Google setup.
"""
import os, re, json, time, hmac, hashlib, secrets, threading, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta, timezone, date as Date, time as Time
from flask import Blueprint, request, jsonify

bp = Blueprint("booking", __name__)
BD = timezone(timedelta(hours=6))          # Asia/Dhaka, UTC+6 (no daylight saving)
BASE = os.path.dirname(os.path.abspath(__file__))
PURPOSES = ["AI Development", "Chatbot Development", "Graphic Design", "Video Editing",
            "Presentation Design", "General Discussion"]
TYPES = {"meet": "Google Meet", "online": "Online Meeting"}
DURATIONS = (15, 30, 60)
LOCK = threading.Lock()                    # one booking at a time: prevents double booking
_busy = {"t": 0.0, "data": None}
_tok = {"v": None, "exp": 0.0}
_hits = {}


class GoogleError(Exception):
    def __init__(self, status, body=""):
        super().__init__(f"Google API {status}: {body}")
        self.status = status


# ------------------------------------------------------------------ settings
def settings():
    kb = {}
    try:
        with open(os.path.join(BASE, "knowledge.json"), encoding="utf-8") as f:
            kb = json.load(f)
    except Exception as e:
        print("booking: knowledge.json problem:", e)
    a, b = kb.get("availability", {}), kb.get("booking", {})
    return {"open": a.get("open_hour", 9), "close": a.get("close_hour", 15),
            "days": set(b.get("days", [0, 1, 2, 3, 4, 5, 6])),        # Monday=0 ... Sunday=6
            "step": b.get("slot_step_minutes", 30), "notice": b.get("min_notice_minutes", 120),
            "ahead": b.get("max_days_ahead", 60), "buffer": b.get("buffer_minutes", 0),
            "email": kb.get("contact", {}).get("email", ""), "whatsapp": kb.get("contact", {}).get("whatsapp", "")}


def configured():
    return all(os.getenv(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"))


def cal_id():
    return urllib.parse.quote(os.getenv("GOOGLE_CALENDAR_ID", "primary"), safe="")


# ------------------------------------------------------------------ Google Calendar REST
def g_token():
    if _tok["v"] and time.time() < _tok["exp"] - 60:
        return _tok["v"]
    data = urllib.parse.urlencode({"client_id": os.getenv("GOOGLE_CLIENT_ID"), "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                                   "refresh_token": os.getenv("GOOGLE_REFRESH_TOKEN"), "grant_type": "refresh_token"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data), timeout=15) as r:
            j = json.load(r)
    except urllib.error.HTTPError as e:
        raise GoogleError(e.code, e.read()[:200].decode("utf-8", "ignore"))
    _tok.update(v=j["access_token"], exp=time.time() + j.get("expires_in", 3000))
    return _tok["v"]


def g_api(method, path, params=None, body=None):
    url = "https://www.googleapis.com/calendar/v3" + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + g_token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise GoogleError(e.code, e.read()[:300].decode("utf-8", "ignore"))
    except urllib.error.URLError as e:
        raise GoogleError(0, str(e))


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fetch_busy(t0, t1, ignore_id=None):
    """All busy periods on the calendar between t0 and t1, as (start, end) pairs."""
    busy, page = [], None
    while True:
        p = {"timeMin": t0.isoformat(), "timeMax": t1.isoformat(), "singleEvents": "true", "maxResults": 250,
             "fields": "nextPageToken,items(id,status,transparency,start,end,attendees(self,responseStatus))"}
        if page:
            p["pageToken"] = page
        r = g_api("GET", f"/calendars/{cal_id()}/events", p)
        for ev in r.get("items", []):
            if ev.get("id") == ignore_id or ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
                continue
            if any(a.get("self") and a.get("responseStatus") == "declined" for a in ev.get("attendees", [])):
                continue
            s, e = ev["start"], ev["end"]
            if "dateTime" in s:
                busy.append((parse_dt(s["dateTime"]), parse_dt(e["dateTime"])))
            else:   # all-day event
                busy.append((datetime.fromisoformat(s["date"]).replace(tzinfo=BD), datetime.fromisoformat(e["date"]).replace(tzinfo=BD)))
        page = r.get("nextPageToken")
        if not page:
            return busy


def get_busy(ignore_id=None, fresh=False):
    cfg = settings()
    now = datetime.now(BD)
    if not fresh and ignore_id is None and _busy["data"] is not None and time.time() - _busy["t"] < 15:
        return _busy["data"]
    data = fetch_busy(now - timedelta(days=1), now + timedelta(days=cfg["ahead"] + 2), ignore_id)
    if ignore_id is None:
        _busy.update(t=time.time(), data=data)
    return data


# ------------------------------------------------------------------ availability maths
def valid_slots(day, duration, busy, cfg=None, now=None):
    """Start times (aware datetimes, BD) on `day` that fit working hours, notice rules and are free."""
    cfg, now = cfg or settings(), now or datetime.now(BD)
    if day.weekday() not in cfg["days"] or day > now.date() + timedelta(days=cfg["ahead"]):
        return []
    earliest = now + timedelta(minutes=cfg["notice"])
    t = datetime.combine(day, Time(cfg["open"]), BD)
    end_limit = datetime.combine(day, Time(cfg["close"]), BD)
    out, dur, buf = [], timedelta(minutes=duration), timedelta(minutes=cfg["buffer"])
    while t + dur <= end_limit:
        if t >= earliest and not any(b0 < t + dur + buf and b1 > t - buf for b0, b1 in busy):
            out.append(t)
        t += timedelta(minutes=cfg["step"])
    return out


# ------------------------------------------------------------------ helpers
def clean(s, n):
    return re.sub(r"[\x00-\x08\x0b-\x1f]", "", str(s or "")).strip()[:n]


def limited(kind, per_hour):
    ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr
    now = time.time()
    k = (kind, ip)
    _hits[k] = [t for t in _hits.get(k, []) if now - t < 3600]
    if len(_hits[k]) >= per_hour:
        return True
    _hits[k].append(now)
    return False


def fail(code, error, status, **extra):
    return jsonify(error=error, **extra), status


def need_google():
    return None if configured() else fail(503, "not_configured", 503)


def parse_duration(v):
    try:
        d = int(v)
    except (TypeError, ValueError):
        return None
    return d if d in DURATIONS else None


def parse_date(v):
    try:
        return datetime.strptime(str(v), "%Y-%m-%d").date()
    except ValueError:
        return None


def hash_token(t):
    return hashlib.sha256(t.encode()).hexdigest()


def find_event(booking_id):
    r = g_api("GET", f"/calendars/{cal_id()}/events", {"privateExtendedProperty": f"bookingId={booking_id}",
                                                       "singleEvents": "true", "maxResults": 1})
    items = [e for e in r.get("items", []) if e.get("status") != "cancelled"]
    return items[0] if items else None


def authorised(booking_id, token):
    """Returns the event if booking_id + token match, else None."""
    if not re.fullmatch(r"RT-[A-Z0-9]{8}", booking_id or "") or not token:
        return None
    ev = find_event(booking_id)
    if not ev:
        return None
    stored = ev.get("extendedProperties", {}).get("private", {}).get("tokenHash", "")
    return ev if hmac.compare_digest(stored, hash_token(token)) else None


def view(ev, booking_id=None, token=None):
    p = ev.get("extendedProperties", {}).get("private", {})
    s, e = parse_dt(ev["start"]["dateTime"]), parse_dt(ev["end"]["dateTime"])
    out = {"id": p.get("bookingId", booking_id), "name": p.get("clientName", ""), "start": s.astimezone(BD).isoformat(),
           "end": e.astimezone(BD).isoformat(), "duration": int((e - s).total_seconds() // 60),
           "meeting_type": p.get("meetingType", "meet"), "meeting_type_label": TYPES.get(p.get("meetingType", "meet")),
           "purpose": p.get("purpose", ""), "meet_link": ev.get("hangoutLink")}
    if token:
        out["token"] = token
    return out


def resend(to, subject, text, sender="Portfolio Booking <onboarding@resend.dev>"):
    key = os.getenv("RESEND_API_KEY")
    if not (key and to):
        return False
    req = urllib.request.Request("https://api.resend.com/emails", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                 data=json.dumps({"from": sender, "to": [to], "subject": subject, "text": text}).encode())
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print("Booking email error:", e)
        return False


def when(v):
    s = datetime.fromisoformat(v["start"])
    return f"{s.strftime('%A, %d %B %Y, %I:%M %p')} Bangladesh time (UTC+6), {v['duration']} minutes"


# ------------------------------------------------------------------ routes
@bp.get("/api/booking/days")
def days():
    bad = need_google()
    if bad:
        return bad
    cfg, now = settings(), datetime.now(BD)
    dur = parse_duration(request.args.get("duration"))
    m = re.fullmatch(r"(\d{4})-(\d{2})", request.args.get("month", ""))
    if not dur or not m:
        return fail(400, "bad_request", 400)
    y, mo = int(m.group(1)), int(m.group(2))
    if not 1 <= mo <= 12:
        return fail(400, "bad_request", 400)
    try:
        ignore = None
        if request.args.get("manage"):
            bid, _, tok = request.args["manage"].partition(".")
            ev = authorised(bid, tok)
            ignore = ev["id"] if ev else None
        busy = get_busy(ignore_id=ignore)
    except GoogleError as e:
        print("Availability error:", e)
        return fail(502, "calendar_unavailable", 502)
    out, d = [], Date(y, mo, 1)
    while d.month == mo:
        if valid_slots(d, dur, busy, cfg, now):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return jsonify(days=out, today=now.date().isoformat(), max_date=(now.date() + timedelta(days=cfg["ahead"])).isoformat(),
                   timezone="Asia/Dhaka")


@bp.get("/api/booking/slots")
def slots():
    bad = need_google()
    if bad:
        return bad
    day, dur = parse_date(request.args.get("date")), parse_duration(request.args.get("duration"))
    if not day or not dur:
        return fail(400, "bad_request", 400)
    try:
        ignore = None
        if request.args.get("manage"):
            bid, _, tok = request.args["manage"].partition(".")
            ev = authorised(bid, tok)
            ignore = ev["id"] if ev else None
        busy = get_busy(ignore_id=ignore, fresh=True)
    except GoogleError as e:
        print("Availability error:", e)
        return fail(502, "calendar_unavailable", 502)
    return jsonify(slots=[t.strftime("%H:%M") for t in valid_slots(day, dur, busy)], timezone="Asia/Dhaka")


@bp.post("/api/booking")
def create():
    bad = need_google()
    if bad:
        return bad
    d = request.get_json(silent=True) or {}
    if d.get("website"):                                   # honeypot: real visitors never fill this
        return fail(400, "bad_request", 400)
    if limited("book", 6):
        return fail(429, "too_many", 429)
    f, errors = {k: clean(d.get(k), n) for k, n in (("name", 80), ("email", 120), ("company", 100), ("details", 1000))}, {}
    if len(f["name"]) < 2:
        errors["name"] = "Please enter your full name."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}", f["email"]):
        errors["email"] = "Please enter a valid email address."
    if len(f["details"]) < 5:
        errors["details"] = "Please tell Rifa briefly what the meeting is about."
    purpose, mtype = clean(d.get("purpose"), 40), clean(d.get("meeting_type"), 10)
    if purpose not in PURPOSES:
        errors["purpose"] = "Please choose a meeting purpose."
    if mtype not in TYPES:
        errors["meeting_type"] = "Please choose a meeting type."
    day, dur, hm = parse_date(d.get("date")), parse_duration(d.get("duration")), re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(d.get("time", "")))
    if not day or not dur or not hm:
        errors["slot"] = "Please choose a date, time and duration."
    if errors:
        return fail(400, "validation", 400, fields=errors)

    start = datetime.combine(day, Time(int(hm.group(1)), int(hm.group(2))), BD)
    bid, token = "RT-" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8)), secrets.token_urlsafe(18)
    site = (os.getenv("SITE_URL") or request.host_url).rstrip("/")
    manage = f"{site}/#manage={bid}.{token}"
    cfg = settings()
    body = {"summary": f"Appointment: {f['name']} ({purpose})",
            "description": (f"Booked through the portfolio website.\n\nClient: {f['name']} <{f['email']}>\nCompany: {f['company'] or '-'}\n"
                            f"Purpose: {purpose}\nMeeting type: {TYPES[mtype]}\nDuration: {dur} minutes\nDetails: {f['details']}\n\n"
                            f"Booking ID: {bid}\nNeed to cancel or reschedule? {manage}"),
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Dhaka"},
            "end": {"dateTime": (start + timedelta(minutes=dur)).isoformat(), "timeZone": "Asia/Dhaka"},
            "attendees": [{"email": f["email"], "displayName": f["name"]}], "guestsCanModify": False,
            "reminders": {"useDefault": False, "overrides": [{"method": "email", "minutes": 60}, {"method": "popup", "minutes": 15}]},
            "extendedProperties": {"private": {"bookingId": bid, "tokenHash": hash_token(token), "clientName": f["name"],
                                               "meetingType": mtype, "purpose": purpose, "source": "portfolio"}}}
    if mtype == "meet":
        body["conferenceData"] = {"createRequest": {"requestId": bid, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
    else:
        body["location"] = "Online meeting (Rifa will share the meeting details)"

    try:
        with LOCK:                                          # check + save as one step
            busy = get_busy(fresh=True)
            if start not in valid_slots(day, dur, busy, cfg):
                return fail(409, "slot_taken", 409)
            ev = g_api("POST", f"/calendars/{cal_id()}/events", {"conferenceDataVersion": 1, "sendUpdates": "all"}, body)
            _busy["data"] = None
    except GoogleError as e:
        print("Booking save error:", e)
        return fail(502, "not_saved", 502)                  # NOT confirmed: nothing was saved

    if mtype == "meet" and not ev.get("hangoutLink"):       # Meet link is sometimes ready a moment later
        time.sleep(1.5)
        try:
            ev = g_api("GET", f"/calendars/{cal_id()}/events/{ev['id']}")
        except GoogleError:
            pass
    v = view(ev, bid, token)
    v.update(manage_url=manage, client_invite_sent=True)
    # Notification to Rifa (never affects the saved booking)
    link = v["meet_link"] or ("(not generated)" if mtype == "meet" else "Online meeting, share details with the client")
    note = (f"New appointment booked.\n\nWhen: {when(v)}\nName: {f['name']}\nEmail: {f['email']}\nCompany: {f['company'] or '-'}\n"
            f"Purpose: {purpose}\nType: {TYPES[mtype]}\nLink: {link}\nDetails: {f['details']}\nBooking ID: {bid}")
    v["notified_owner"] = resend(os.getenv("NOTIFY_TO"), f"New appointment: {f['name']} on {start.strftime('%d %b, %I:%M %p')}", note)
    sender = os.getenv("RESEND_FROM")                       # only works with a verified Resend domain
    if sender:
        v["client_email_sent"] = resend(f["email"], "Your appointment with Rifa Tasfia is confirmed",
                                        f"Hi {f['name']},\n\nYour appointment is confirmed.\n\nWhen: {when(v)}\nType: {TYPES[mtype]}\nLink: {link}\n\n"
                                        f"Booking ID: {bid}\nCancel or reschedule: {manage}\n\nRifa Tasfia", sender)
    return jsonify(v), 201


@bp.get("/api/booking/<bid>")
def show(bid):
    bad = need_google()
    if bad:
        return bad
    try:
        ev = authorised(bid, request.args.get("token", ""))
    except GoogleError:
        return fail(502, "calendar_unavailable", 502)
    return jsonify(view(ev, bid)) if ev else fail(404, "not_found", 404)


@bp.post("/api/booking/<bid>/cancel")
def cancel(bid):
    bad = need_google()
    if bad:
        return bad
    if limited("manage", 20):
        return fail(429, "too_many", 429)
    try:
        with LOCK:
            ev = authorised(bid, (request.get_json(silent=True) or {}).get("token", ""))
            if not ev:
                return fail(404, "not_found", 404)
            v = view(ev, bid)
            if parse_dt(ev["start"]["dateTime"]) < datetime.now(BD):
                return fail(400, "already_past", 400)
            g_api("DELETE", f"/calendars/{cal_id()}/events/{ev['id']}", {"sendUpdates": "all"})
            _busy["data"] = None
    except GoogleError as e:
        print("Cancel error:", e)
        return fail(502, "not_saved", 502)
    resend(os.getenv("NOTIFY_TO"), f"Appointment cancelled: {v['name']}", f"A client cancelled.\n\nWas: {when(v)}\nName: {v['name']}\nBooking ID: {bid}")
    return jsonify(cancelled=True, id=bid)


@bp.post("/api/booking/<bid>/reschedule")
def reschedule(bid):
    bad = need_google()
    if bad:
        return bad
    if limited("manage", 20):
        return fail(429, "too_many", 429)
    d = request.get_json(silent=True) or {}
    day, hm = parse_date(d.get("date")), re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(d.get("time", "")))
    if not day or not hm:
        return fail(400, "bad_request", 400)
    start = datetime.combine(day, Time(int(hm.group(1)), int(hm.group(2))), BD)
    try:
        with LOCK:
            ev = authorised(bid, d.get("token", ""))
            if not ev:
                return fail(404, "not_found", 404)
            old = view(ev, bid)
            if parse_dt(ev["start"]["dateTime"]) < datetime.now(BD):
                return fail(400, "already_past", 400)
            busy = get_busy(ignore_id=ev["id"], fresh=True)         # ignore this booking's own time
            if start not in valid_slots(day, old["duration"], busy):
                return fail(409, "slot_taken", 409)
            ev = g_api("PATCH", f"/calendars/{cal_id()}/events/{ev['id']}", {"sendUpdates": "all"},
                       {"start": {"dateTime": start.isoformat(), "timeZone": "Asia/Dhaka"},
                        "end": {"dateTime": (start + timedelta(minutes=old["duration"])).isoformat(), "timeZone": "Asia/Dhaka"}})
            _busy["data"] = None
    except GoogleError as e:
        print("Reschedule error:", e)
        return fail(502, "not_saved", 502)
    v = view(ev, bid, d.get("token"))
    resend(os.getenv("NOTIFY_TO"), f"Appointment rescheduled: {v['name']}", f"A client rescheduled.\n\nWas: {when(old)}\nNow: {when(v)}\nName: {v['name']}\nBooking ID: {bid}")
    return jsonify(v)
