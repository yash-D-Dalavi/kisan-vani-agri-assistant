import unittest
from datetime import date

import helpers  # noqa: F401
import live_tools as LT


class FlakyFetch:
    def __init__(self, fail_times, payload):
        self.fail_times, self.payload, self.calls = fail_times, payload, 0

    def __call__(self, url):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("boom")
        return self.payload


AGMARK = {"records": [
    {"state": "Maharashtra", "district": "Nashik", "market": "Lasalgaon", "commodity": "Onion", "arrival_date": "19/11/2026",
     "min_price": "1700", "max_price": "2600", "modal_price": "2100"},
    {"state": "Maharashtra", "district": "Pune", "market": "Pune", "commodity": "Onion", "arrival_date": "18/11/2026",
     "min_price": "1800", "max_price": "2700", "modal_price": "2200"}]}


class MandiTests(unittest.TestCase):
    def test_demo_feed_has_as_of_and_is_labelled(self):
        r = LT.MandiTool(live=False).lookup("onion", "Lasalgaon")
        self.assertTrue(r.ok)
        self.assertEqual(r.mode, "demo-feed")
        self.assertTrue(r.as_of)
        self.assertTrue(any("DEMO FEED" in n for n in r.notes))
        self.assertEqual(r.data["rows"][0]["market"], "Lasalgaon")

    def test_cache_hit(self):
        t = LT.MandiTool(live=False)
        t.lookup("onion", "Pune")
        again = t.lookup("onion", "Pune")
        self.assertTrue(again.from_cache)

    def test_unknown_and_no_feed_commodities(self):
        t = LT.MandiTool(live=False)
        self.assertEqual(t.lookup("dragonfruit").error, "UNKNOWN_COMMODITY")
        self.assertEqual(t.lookup("sugarcane").error, "NO_DATA")

    def test_live_failure_is_reported_never_faked(self):
        f = FlakyFetch(1, AGMARK)
        t = LT.MandiTool(live=True, api_key="k", fetch=f)
        r = t.lookup("onion", "Lasalgaon")        # first call: the source raises; the tool must not crash
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "LIVE_SOURCE_UNAVAILABLE")      # no silent fallback to demo numbers in live mode
        r2 = t.lookup("onion", "Lasalgaon")
        self.assertTrue(r2.ok)
        self.assertEqual(r2.mode, "live")
        self.assertEqual(r2.as_of, "2026-11-19")                  # newest arrival_date wins
        self.assertEqual(r2.data["rows"][0]["modal"], 2100.0)

    def test_http_get_json_retries_with_backoff(self):
        sleeps = []
        calls = {"n": 0}
        import urllib.request as ur
        real = ur.urlopen

        class R:
            def read(self):
                return b'{"ok": 1}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=0):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("down")
            return R()
        ur.urlopen = fake
        try:
            out = LT.http_get_json("http://x", retries=3, backoff=0.5, sleep=sleeps.append)
        finally:
            ur.urlopen = real
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(sleeps, [0.5, 1.0])                      # exponential backoff

    def test_stale_cache_served_when_live_fails(self):
        f = FlakyFetch(0, AGMARK)
        t = LT.MandiTool(live=True, api_key="k", fetch=f, ttl=0)    # ttl=0 -> always expired, but kept for stale fallback
        self.assertTrue(t.lookup("onion", "Lasalgaon").ok)
        f.calls, f.fail_times = 0, 99
        r = t.lookup("onion", "Lasalgaon")
        self.assertTrue(r.ok and r.stale and r.from_cache)

    def test_rate_limiter(self):
        b = LT.TokenBucket(capacity=2, refill_per_s=0.0001)
        self.assertEqual([b.allow() for _ in range(3)], [True, True, False])

    def test_sunday_uses_saturday(self):
        self.assertEqual(LT.latest_market_day(date(2026, 11, 22)), date(2026, 11, 21))   # 22 Nov 2026 is a Sunday


class WeatherTests(unittest.TestCase):
    def test_demo_forecast_and_advisory(self):
        r = LT.WeatherTool(live=False).forecast("Pune")
        self.assertTrue(r.ok)
        self.assertEqual(len(r.data["days"]), 3)
        self.assertTrue(r.data["advisories"])
        self.assertEqual(r.as_of, LT.now_ist().date().isoformat())

    def test_advisory_rules(self):
        wet = [{"tmax": 28, "tmin": 20, "rain_mm": 8, "rain_prob": 90}, {"tmax": 28, "tmin": 20, "rain_mm": 9, "rain_prob": 90}, {"tmax": 28, "tmin": 20, "rain_mm": 0, "rain_prob": 10}]
        self.assertEqual(LT.advisory_rules(wet)[0]["code"], "HEAVY_RAIN")
        dry_hot = [{"tmax": 39, "tmin": 24, "rain_mm": 0, "rain_prob": 0}] * 3
        self.assertEqual([a["code"] for a in LT.advisory_rules(dry_hot)], ["DRY_SPELL", "HEAT"])

    def test_live_open_meteo_parsing(self):
        payload = {"daily": {"time": ["2026-11-21", "2026-11-22", "2026-11-23"], "temperature_2m_max": [31.2, 30.1, 29.9],
                             "temperature_2m_min": [15.0, 14.2, 13.8], "precipitation_sum": [0.0, 12.5, 3.0],
                             "precipitation_probability_max": [5, 80, 40]}}
        r = LT.WeatherTool(live=True, fetch=lambda url: payload).forecast("Pune")
        self.assertEqual((r.mode, r.data["days"][1]["rain_mm"]), ("live", 12.5))
        self.assertEqual(r.data["advisories"][0]["code"], "HEAVY_RAIN")

    def test_unknown_location(self):
        self.assertEqual(LT.WeatherTool(live=False).forecast("Atlantis").error, "UNKNOWN_LOCATION")


class RenderTests(unittest.TestCase):
    def test_answers_contain_data_as_of_in_each_language(self):
        res = LT.MandiTool(live=False).lookup("onion", "Nashik")
        d = LT.fmt_date(res.as_of)
        for lang in ("hi", "mr", "en"):
            self.assertIn(d, LT.render_mandi(res, lang, "onion"))
        w = LT.WeatherTool(live=False).forecast("Latur")
        for lang in ("hi", "mr", "en"):
            self.assertIn(LT.fmt_date(w.as_of), LT.render_weather(w, lang))


if __name__ == "__main__":
    unittest.main()
