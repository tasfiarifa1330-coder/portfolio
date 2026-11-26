"""Offline booking tests: a fake in-memory Google Calendar replaces the real one (no internet, no keys).
Run from the project folder:   python -m unittest discover -s tests -v
"""
import os, sys, threading, unittest
from datetime import datetime, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-real-123456")
import types as T
from types import SimpleNamespace as N
try:
    import google.genai  # noqa
except ImportError:
    g, ga, ty = T.ModuleType("google"), T.ModuleType("google.genai"), T.ModuleType("google.genai.types")
    ty.__getattr__ = lambda n: (lambda *a, **k: N(**k))
    ty.Part = type("Part", (), {"__new__": lambda c, **k: N(**k), "from_function_response": staticmethod(lambda **k: N(**k))})
    ga.Client = lambda **k: None; ga.types = ty; g.genai = ga
    sys.modules.update({"google": g, "google.genai": ga, "google.genai.types": ty})
import main, booking  # noqa: E402


class FakeCal:
    def __init__(self):
        self.events, self.n, self.fail = {}, 0, False

    def api(self, method, path, params=None, body=None):
        params = params or {}
        if self.fail and method in ("POST", "PATCH", "DELETE"):
            raise booking.GoogleError(500, "boom")
        if method == "GET" and path.endswith("/events"):
            items = list(self.events.values())
            if params.get("privateExtendedProperty"):
                k, v = params["privateExtendedProperty"].split("=", 1)
                items = [e for e in items if e.get("extendedProperties", {}).get("private", {}).get(k) == v]
            return {"items": items}
        if method == "GET":
            return self.events[path.rsplit("/", 1)[1]]
        if method == "POST":
            self.n += 1
            ev = dict(body, id=f"ev{self.n}", status="confirmed")
            if "conferenceData" in body:
                ev["hangoutLink"] = "https://meet.google.com/abc-defg-hij"
            self.events[ev["id"]] = ev
            return ev
        eid = path.rsplit("/", 1)[1]
        if method == "PATCH":
            self.events[eid].update(body); return self.events[eid]
        if method == "DELETE":
            del self.events[eid]; return {}


def future_day(n=3):
    return (datetime.now(booking.BD) + timedelta(days=n)).date()


class Base(unittest.TestCase):
    def setUp(self):
        self.cal = FakeCal()
        booking.g_api, booking.g_token = self.cal.api, lambda: "tok"
        booking.configured = lambda: True
        booking._busy.update(t=0, data=None); booking._hits.clear()
        self.mail = []
        booking.resend = lambda to, subj, text, sender="x": (self.mail.append((to, subj)) or True)
        os.environ["NOTIFY_TO"] = "rifa@example.com"; os.environ.pop("RESEND_FROM", None)
        self.c = main.app.test_client()
        self.day = future_day().isoformat()

    def book(self, time="10:00", dur=30, ip="1.1.1.1", **over):
        d = dict(name="Sam Client", email="sam@example.com", company="Acme", details="Need a chatbot for my shop", purpose="Chatbot Development",
                 meeting_type="meet", date=self.day, time=time, duration=dur, website="")
        d.update(over)
        return self.c.post("/api/booking", json=d, headers={"X-Forwarded-For": ip})

    def slots(self, dur=30, day=None):
        return self.c.get(f"/api/booking/slots?date={day or self.day}&duration={dur}").json["slots"]


class AvailabilityTests(Base):
    def test_slots_follow_working_hours_9_to_3(self):
        s = self.slots(30)
        self.assertEqual((s[0], s[-1], len(s)), ("09:00", "14:30", 12))
        self.assertEqual(self.slots(60)[-1], "14:00")

    def test_days_exclude_past_and_include_future(self):
        m = self.day[:7]
        j = self.c.get(f"/api/booking/days?month={m}&duration=30").json
        self.assertIn(self.day, j["days"]); self.assertTrue(all(d >= j["today"] for d in j["days"]))
        self.assertEqual(j["timezone"], "Asia/Dhaka")

    def test_minimum_notice_blocks_slots_too_soon(self):
        now = datetime.now(booking.BD)
        s = booking.valid_slots(now.date(), 30, [], now=now.replace(hour=10, minute=0))
        self.assertTrue(all(t >= now.replace(hour=12, minute=0, second=0, microsecond=0) for t in s))

    def test_past_and_far_future_have_no_slots(self):
        self.assertEqual(self.slots(30, (future_day(-2)).isoformat()), [])
        self.assertEqual(self.slots(30, future_day(200).isoformat()), [])

    def test_all_day_event_blocks_day_but_transparent_does_not(self):
        d2 = future_day(4)
        self.cal.events["a"] = {"id": "a", "start": {"date": d2.isoformat()}, "end": {"date": (d2 + timedelta(days=1)).isoformat()}}
        self.assertEqual(self.slots(30, d2.isoformat()), [])
        self.cal.events["a"]["transparency"] = "transparent"
        booking._busy.update(t=0, data=None)
        self.assertEqual(len(self.slots(30, d2.isoformat())), 12)

    def test_closed_weekdays_setting(self):
        orig = booking.settings
        booking.settings = lambda: dict(orig(), days={(future_day().weekday() + 1) % 7})
        try:
            self.assertEqual(self.slots(30), [])
        finally:
            booking.settings = orig

    def test_not_configured(self):
        booking.configured = lambda: False
        self.assertEqual(self.c.get(f"/api/booking/slots?date={self.day}&duration=30").status_code, 503)
        self.assertEqual(self.book().status_code, 503)

    def test_google_down_gives_error_not_empty_calendar(self):
        def boom(*a, **k): raise booking.GoogleError(500, "x")
        booking.g_api = boom
        self.assertEqual(self.c.get(f"/api/booking/slots?date={self.day}&duration=30").status_code, 502)


class BookingTests(Base):
    def test_successful_booking_is_saved_and_blocks_slot(self):
        r = self.book(); self.assertEqual(r.status_code, 201)
        j = r.json; ev = list(self.cal.events.values())[0]
        self.assertTrue(j["id"].startswith("RT-")); self.assertTrue(j["token"]); self.assertEqual(j["duration"], 30)
        self.assertEqual(j["meet_link"], "https://meet.google.com/abc-defg-hij")
        self.assertEqual(ev["attendees"][0]["email"], "sam@example.com")
        self.assertIn("conferenceData", ev)
        self.assertEqual(ev["start"]["timeZone"], "Asia/Dhaka"); self.assertTrue(ev["start"]["dateTime"].endswith("+06:00"))
        self.assertNotIn("10:00", self.slots(30))
        self.assertEqual(self.mail[0][0], "rifa@example.com")

    def test_token_is_stored_only_as_hash(self):
        j = self.book().json
        p = list(self.cal.events.values())[0]["extendedProperties"]["private"]
        self.assertNotIn(j["token"], str(p)); self.assertEqual(p["tokenHash"], booking.hash_token(j["token"]))

    def test_same_slot_twice_is_rejected(self):
        self.assertEqual(self.book().status_code, 201)
        self.assertEqual(self.book(ip="2.2.2.2", email="b@example.com").status_code, 409)
        self.assertEqual(len(self.cal.events), 1)

    def test_overlapping_longer_meeting_is_rejected(self):
        self.book("10:00", 30)
        self.assertEqual(self.book("09:30", 60, ip="3.3.3.3").status_code, 409)
        self.assertEqual(self.book("10:30", 30, ip="4.4.4.4").status_code, 201)     # right after is fine

    def test_simultaneous_attempts_only_one_wins(self):
        codes = []
        def go(i): codes.append(self.book("11:00", 30, ip=f"9.9.9.{i}", email=f"u{i}@example.com").status_code)
        ts = [threading.Thread(target=go, args=(i,)) for i in range(10)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sorted(codes), [201] + [409] * 9); self.assertEqual(len(self.cal.events), 1)

    def test_outside_hours_and_past_are_rejected(self):
        self.assertEqual(self.book("20:00").status_code, 409)
        self.assertEqual(self.book("10:00", date=future_day(-1).isoformat(), ip="5.5.5.5").status_code, 409)
        self.assertEqual(self.cal.events, {})

    def test_validation_errors_and_honeypot(self):
        r = self.book(email="nope", name="S", details="", purpose="Hacking", meeting_type="x", ip="6.6.6.6")
        self.assertEqual(r.status_code, 400); self.assertEqual(set(r.json["fields"]), {"email", "name", "details", "purpose", "meeting_type"})
        self.assertEqual(self.book(website="http://spam", ip="6.6.6.7").status_code, 400)
        self.assertEqual(self.book(duration=45, ip="6.6.6.8").json["fields"].keys(), {"slot"})
        self.assertEqual(self.cal.events, {})

    def test_save_failure_is_never_confirmed(self):
        self.cal.fail = True
        r = self.book(); self.assertEqual(r.status_code, 502); self.assertEqual(r.json["error"], "not_saved")
        self.assertEqual(self.cal.events, {}); self.assertEqual(self.mail, [])

    def test_online_meeting_has_no_meet_link(self):
        j = self.book(meeting_type="online").json
        ev = list(self.cal.events.values())[0]
        self.assertNotIn("conferenceData", ev); self.assertIsNone(j["meet_link"]); self.assertEqual(j["meeting_type_label"], "Online Meeting")

    def test_booking_rate_limit(self):
        codes = [self.book(f"{9 + i // 2:02d}:{'00' if i % 2 == 0 else '30'}", ip="7.7.7.7").status_code for i in range(8)]
        self.assertEqual(codes[:6], [201] * 6); self.assertEqual(codes[6], 429)

    def test_client_email_only_when_verified_sender_configured(self):
        self.book(); self.assertEqual(len(self.mail), 1)
        os.environ["RESEND_FROM"] = "Rifa <hello@verified.example>"
        self.book("12:00", ip="8.8.8.8", email="c@example.com"); self.assertIn("c@example.com", [m[0] for m in self.mail])


class ManageTests(Base):
    def setUp(self):
        super().setUp()
        self.b = self.book("10:00", 60).json
        self.url = f"/api/booking/{self.b['id']}"

    def test_view_needs_correct_token(self):
        self.assertEqual(self.c.get(self.url + "?token=" + self.b["token"]).status_code, 200)
        self.assertEqual(self.c.get(self.url + "?token=wrong").status_code, 404)
        self.assertEqual(self.c.get("/api/booking/RT-AAAAAAAA?token=x").status_code, 404)
        self.assertEqual(self.c.get("/api/booking/bad id?token=x").status_code, 404)

    def test_cancel_frees_the_slot(self):
        self.assertEqual(self.c.post(self.url + "/cancel", json={"token": "wrong"}).status_code, 404)
        self.assertEqual(self.cal.events and len(self.cal.events), 1)
        self.assertEqual(self.c.post(self.url + "/cancel", json={"token": self.b["token"]}).status_code, 200)
        self.assertEqual(self.cal.events, {}); self.assertIn("10:00", self.slots(30))

    def test_reschedule_to_free_slot_and_overlapping_own_time(self):
        r = self.c.post(self.url + "/reschedule", json={"token": self.b["token"], "date": self.day, "time": "10:30"})
        self.assertEqual(r.status_code, 200)       # overlaps its own old time: allowed
        ev = list(self.cal.events.values())[0]; self.assertIn("T10:30:00", ev["start"]["dateTime"]); self.assertIn("T11:30:00", ev["end"]["dateTime"])

    def test_reschedule_to_taken_slot_rejected(self):
        self.book("13:00", 30, ip="2.2.2.2")
        r = self.c.post(self.url + "/reschedule", json={"token": self.b["token"], "date": self.day, "time": "13:00"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("T10:00:00", [e for e in self.cal.events.values() if e["id"] == "ev1"][0]["start"]["dateTime"])

    def test_reschedule_wrong_token(self):
        self.assertEqual(self.c.post(self.url + "/reschedule", json={"token": "x", "date": self.day, "time": "12:00"}).status_code, 404)

    def test_availability_for_reschedule_ignores_own_event(self):
        own = self.c.get(f"/api/booking/slots?date={self.day}&duration=60&manage={self.b['id']}.{self.b['token']}").json["slots"]
        self.assertIn("10:00", own); self.assertNotIn("10:00", self.slots(60))


if __name__ == "__main__":
    unittest.main()
