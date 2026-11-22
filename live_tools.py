"""
live_tools.py - Module 8 of the blueprint: live data tools.

  MandiTool    commodity / market prices  (Agmarknet via data.gov.in when AGRI_LIVE=1 and DATA_GOV_API_KEY is set,
               otherwise a clearly-labelled DEMO FEED)
  WeatherTool  3-day forecast + rule-based agromet advisory (Open-Meteo when AGRI_LIVE=1, otherwise DEMO FEED)

Rules that follow the blueprint:
  * tools return STRUCTURED, TIME-STAMPED data; the text answer is a template that only phrases the structured result
  * live prices / weather are NEVER written into the vector index
  * every result carries `as_of` ("data as of"), source and mode (live | demo-feed), cache age and a staleness flag
  * TTL cache, token-bucket rate limiting, retries with exponential backoff, stale-cache fallback
  * in live mode a failing source is reported as unavailable - demo numbers are never passed off as live data
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import config
import glossary as G

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(IST)


def fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso[:10]).strftime("%d-%m-%Y")
    except ValueError:
        return iso


# ----------------------------------------------------------------------------------------------------------------------
# infrastructure: cache, rate limiter, retrying HTTP
# ----------------------------------------------------------------------------------------------------------------------
class TTLCache:
    def __init__(self):
        self._d: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float, allow_stale: bool = False):
        with self._lock:
            item = self._d.get(key)
        if not item:
            return None
        val, ts = item
        age = time.time() - ts
        if age <= ttl or allow_stale:
            return val, age
        return None

    def set(self, key: str, val) -> None:
        with self._lock:
            self._d[key] = (val, time.time())


class TokenBucket:
    def __init__(self, capacity: int = config.T.rate_capacity, refill_per_s: float = config.T.rate_refill_per_s):
        self.capacity, self.refill = capacity, refill_per_s
        self.tokens, self.ts = float(capacity), time.time()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.time()
            self.tokens = min(self.capacity, self.tokens + (now - self.ts) * self.refill)
            self.ts = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


def http_get_json(url: str, retries: int = config.T.live_retries, backoff: float = config.T.live_backoff_s,
                  timeout: float = 6.0, sleep: Callable[[float], None] = time.sleep):
    """GET JSON with exponential backoff on network errors / 429 / 5xx. Raises the last error."""
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "agri-assistant/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:  # noqa: BLE001
            last = e
        if attempt < retries - 1:
            sleep(backoff * (2 ** attempt))
    raise last or RuntimeError("request failed")


# ----------------------------------------------------------------------------------------------------------------------
# result type
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class ToolResult:
    ok: bool
    tool: str
    data: dict = field(default_factory=dict)
    as_of: str = ""                  # "data as of" (ISO date)
    fetched_at: str = ""
    source: str = ""
    mode: str = "demo-feed"          # live | demo-feed
    from_cache: bool = False
    cache_age_s: float = 0.0
    stale: bool = False
    error: str = ""
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["as_of_display"] = fmt_date(self.as_of) if self.as_of else ""
        return d


# ----------------------------------------------------------------------------------------------------------------------
# Mandi prices
# ----------------------------------------------------------------------------------------------------------------------
# DEMO FEED base prices (Rs / quintal: min, modal, max). Illustrative values only - NOT real market data.
_DEMO_MANDI = {
    "onion": {"Lasalgaon": (1700, 2100, 2600), "Pune": (1900, 2300, 2800), "Nashik": (1650, 2050, 2500), "Solapur": (1200, 1800, 2400)},
    "tomato": {"Pune": (1200, 1800, 2400), "Nashik": (1000, 1600, 2200), "Nagpur": (1300, 1900, 2500)},
    "soybean": {"Latur": (4300, 4600, 4800), "Akola": (4350, 4650, 4850), "Nagpur": (4400, 4700, 4900)},
    "cotton": {"Akola": (6900, 7300, 7600), "Jalgaon": (6850, 7250, 7550), "Nagpur": (6950, 7350, 7650)},
    "wheat": {"Pune": (2600, 2900, 3200), "Nagpur": (2500, 2800, 3100), "Latur": (2550, 2850, 3150)},
    "maize": {"Jalgaon": (1900, 2100, 2300), "Pune": (2000, 2200, 2400)},
    "rice": {"Nagpur": (2800, 3100, 3400), "Kolhapur": (2900, 3200, 3500)},
    "sugarcane": {},  # intentionally no mandi feed (FRP-based pricing) -> tool reports "no data"
}
_COMMODITY_API_NAME = {"onion": "Onion", "tomato": "Tomato", "soybean": "Soyabean", "cotton": "Cotton", "wheat": "Wheat",
                       "maize": "Maize", "rice": "Paddy(Dhan)(Common)"}
AGMARKNET_RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"   # data.gov.in: current daily price of commodities


def latest_market_day(today: Optional[date] = None) -> date:
    d = today or now_ist().date()
    return d - timedelta(days=1) if d.weekday() == 6 else d  # markets closed on Sunday -> Saturday's data


def _jitter(commodity: str, market: str, d: date) -> float:
    h = int(hashlib.md5(f"{commodity}|{market}|{d.isoformat()}".encode()).hexdigest()[:6], 16)
    return 1 + ((h % 601) - 300) / 10000.0  # +/- 3 %


class MandiTool:
    name = "mandi_price"

    def __init__(self, live: Optional[bool] = None, api_key: Optional[str] = None, fetch: Optional[Callable[[str], dict]] = None,
                 ttl: int = config.T.mandi_ttl_s, stale_after_days: int = 3):
        self.live = config.LIVE_MODE if live is None else live
        self.api_key = config.DATA_GOV_API_KEY if api_key is None else api_key
        self._fetch = fetch or http_get_json
        self.cache, self.bucket, self.ttl, self.stale_after_days = TTLCache(), TokenBucket(), ttl, stale_after_days

    def lookup(self, commodity: str, market: Optional[str] = None, state: str = config.DEFAULT_STATE) -> ToolResult:
        commodity = (commodity or "").lower()
        key = f"mandi|{commodity}|{market}|{state}|{'live' if self.live else 'demo'}"
        hit = self.cache.get(key, self.ttl)
        if hit:
            res, age = hit
            return ToolResult(**{**asdict(res), "from_cache": True, "cache_age_s": round(age, 1)})
        if commodity not in _DEMO_MANDI and commodity not in _COMMODITY_API_NAME:
            return ToolResult(False, self.name, error="UNKNOWN_COMMODITY", source="-", notes=[f"no price feed for '{commodity}'"])
        try:
            res = self._live(commodity, market, state) if self.live else self._demo(commodity, market, state)
        except Exception as e:  # noqa: BLE001
            stale = self.cache.get(key, self.ttl, allow_stale=True)
            if stale:
                res, age = stale[0], stale[1]
                return ToolResult(**{**asdict(res), "from_cache": True, "cache_age_s": round(age, 1), "stale": True,
                                     "notes": res.notes + ["live source failed; serving last cached data"]})
            return ToolResult(False, self.name, error="LIVE_SOURCE_UNAVAILABLE", mode="live", source="Agmarknet (data.gov.in)",
                              notes=[f"{type(e).__name__}: {e}"])
        if res.ok:
            self.cache.set(key, res)
        return res

    # ---- demo feed
    def _demo(self, commodity: str, market: Optional[str], state: str) -> ToolResult:
        table = _DEMO_MANDI.get(commodity, {})
        if not table:
            return ToolResult(False, self.name, error="NO_DATA", mode="demo-feed", source="DEMO FEED",
                              notes=[f"no market price feed for {commodity}"])
        d = latest_market_day()
        picks = [m for m in table if market and m.lower() == market.lower()] or list(table)[:3]
        rows = []
        for m in picks:
            lo, mo, hi = table[m]
            j = _jitter(commodity, m, d)
            rows.append({"market": m, "state": state, "min": round(lo * j, -1), "modal": round(mo * j, -1), "max": round(hi * j, -1),
                         "unit": "Rs/quintal", "arrival_date": d.isoformat()})
        matched = bool(market and any(r["market"].lower() == market.lower() for r in rows))
        notes = ["DEMO FEED: simulated Agmarknet-style prices for the prototype, not real market data"]
        if market and not matched:
            notes.append(f"no feed for market '{market}'; showing nearby markets")
        return ToolResult(True, self.name, {"commodity": commodity, "rows": rows, "requested_market": market, "market_matched": matched},
                          as_of=d.isoformat(), fetched_at=now_ist().isoformat(timespec="seconds"),
                          source="Demo feed (simulated Agmarknet)", mode="demo-feed", notes=notes)

    # ---- live Agmarknet
    def _live(self, commodity: str, market: Optional[str], state: str) -> ToolResult:
        if not self.api_key:
            raise RuntimeError("DATA_GOV_API_KEY not set")
        if not self.bucket.allow():
            raise RuntimeError("rate limited")
        q = urllib.parse.urlencode({"api-key": self.api_key, "format": "json", "limit": 100, "filters[state]": state,
                                    "filters[commodity]": _COMMODITY_API_NAME.get(commodity, commodity.title())})
        payload = self._fetch(f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE}?{q}")
        recs = payload.get("records", [])
        if not recs:
            return ToolResult(False, self.name, error="NO_DATA", mode="live", source="Agmarknet (data.gov.in)",
                              notes=["no records returned for today"])

        def parse_date(r):
            try:
                return datetime.strptime(r.get("arrival_date", ""), "%d/%m/%Y").date()
            except ValueError:
                return date.min
        newest = max(parse_date(r) for r in recs)
        recs = [r for r in recs if parse_date(r) == newest]
        if market:
            m = [r for r in recs if market.lower() in (r.get("market", "") + " " + r.get("district", "")).lower()]
            recs = m or recs
        rows = [{"market": r.get("market"), "state": r.get("state"), "min": float(r.get("min_price") or 0), "modal": float(r.get("modal_price") or 0),
                 "max": float(r.get("max_price") or 0), "unit": "Rs/quintal", "arrival_date": newest.isoformat()} for r in recs[:3]]
        stale = (now_ist().date() - newest).days > self.stale_after_days
        return ToolResult(True, self.name, {"commodity": commodity, "rows": rows, "requested_market": market,
                                            "market_matched": bool(market and m)},
                          as_of=newest.isoformat(), fetched_at=now_ist().isoformat(timespec="seconds"),
                          source="Agmarknet via data.gov.in", mode="live", stale=stale,
                          notes=["prices may be outdated"] if stale else [])


# ----------------------------------------------------------------------------------------------------------------------
# Weather / agromet
# ----------------------------------------------------------------------------------------------------------------------
_COORDS = {"Pune": (18.52, 73.86), "Latur": (18.40, 76.56), "Nashik": (19.99, 73.79), "Nagpur": (21.15, 79.09), "Akola": (20.71, 77.00),
           "Jalgaon": (21.00, 75.57), "Solapur": (17.66, 75.91), "Kolhapur": (16.70, 74.24), "Lasalgaon": (20.14, 74.24),
           "Amravati": (20.93, 77.75), "Delhi": (28.61, 77.21), "Aurangabad": (19.88, 75.34)}
_DEMO_CLIMATE = {"Pune": (30, 14), "Latur": (32, 16), "Nashik": (29, 12), "Nagpur": (32, 14), "Akola": (33, 15), "Jalgaon": (33, 14),
                 "Solapur": (33, 16), "Kolhapur": (30, 17), "Lasalgaon": (29, 12), "Amravati": (32, 15), "Delhi": (27, 11), "Aurangabad": (31, 14)}


def advisory_rules(days: List[dict]) -> List[dict]:
    """Rule-based agromet advisory codes from the forecast. (Illustrative - production should show the district IMD/GKMS bulletin.)"""
    out: List[dict] = []
    rain48 = sum(d["rain_mm"] for d in days[:2])
    tmax = max(d["tmax"] for d in days[:3])
    tmin = min(d["tmin"] for d in days[:3])
    if rain48 >= 10:
        out.append({"code": "HEAVY_RAIN", "rain_mm_48h": round(rain48, 1)})
    elif rain48 >= 2:
        out.append({"code": "LIGHT_RAIN", "rain_mm_48h": round(rain48, 1)})
    else:
        out.append({"code": "DRY_SPELL", "rain_mm_48h": round(rain48, 1)})
    if tmax >= 36:
        out.append({"code": "HEAT", "tmax": tmax})
    if tmin <= 9:
        out.append({"code": "COLD", "tmin": tmin})
    return out


class WeatherTool:
    name = "weather"

    def __init__(self, live: Optional[bool] = None, fetch: Optional[Callable[[str], dict]] = None, ttl: int = config.T.weather_ttl_s):
        self.live = config.LIVE_MODE if live is None else live
        self._fetch = fetch or http_get_json
        self.cache, self.bucket, self.ttl = TTLCache(), TokenBucket(), ttl

    def forecast(self, location: str, days: int = 3) -> ToolResult:
        if location not in _COORDS:
            return ToolResult(False, self.name, error="UNKNOWN_LOCATION", source="-", notes=[f"no coordinates for '{location}'"])
        key = f"wx|{location}|{days}|{'live' if self.live else 'demo'}"
        hit = self.cache.get(key, self.ttl)
        if hit:
            res, age = hit
            return ToolResult(**{**asdict(res), "from_cache": True, "cache_age_s": round(age, 1)})
        try:
            res = self._live(location, days) if self.live else self._demo(location, days)
        except Exception as e:  # noqa: BLE001
            stale = self.cache.get(key, self.ttl, allow_stale=True)
            if stale:
                return ToolResult(**{**asdict(stale[0]), "from_cache": True, "cache_age_s": round(stale[1], 1), "stale": True,
                                     "notes": stale[0].notes + ["live source failed; serving last cached forecast"]})
            return ToolResult(False, self.name, error="LIVE_SOURCE_UNAVAILABLE", mode="live", source="Open-Meteo",
                              notes=[f"{type(e).__name__}: {e}"])
        if res.ok:
            self.cache.set(key, res)
        return res

    def _demo(self, location: str, days: int) -> ToolResult:
        tmax0, tmin0 = _DEMO_CLIMATE[location]
        today = now_ist().date()
        rows = []
        for i in range(days):
            d = today + timedelta(days=i)
            h = int(hashlib.md5(f"{location}|{d.isoformat()}".encode()).hexdigest()[:6], 16)
            prob = (h % 100)
            rain = round(((h >> 4) % 220) / 10.0, 1) if prob > 55 else 0.0
            rows.append({"date": d.isoformat(), "tmax": tmax0 + (h % 5) - 2, "tmin": tmin0 + ((h >> 3) % 5) - 2,
                         "rain_mm": rain, "rain_prob": min(95, prob)})
        return ToolResult(True, self.name, {"location": location, "days": rows, "advisories": advisory_rules(rows)},
                          as_of=today.isoformat(), fetched_at=now_ist().isoformat(timespec="seconds"),
                          source="Demo feed (simulated forecast)", mode="demo-feed",
                          notes=["DEMO FEED: simulated forecast for the prototype, not real weather data"])

    def _live(self, location: str, days: int) -> ToolResult:
        if not self.bucket.allow():
            raise RuntimeError("rate limited")
        lat, lon = _COORDS[location]
        q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "timezone": "Asia/Kolkata", "forecast_days": days,
                                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max"})
        j = self._fetch("https://api.open-meteo.com/v1/forecast?" + q)["daily"]
        rows = [{"date": j["time"][i], "tmax": round(j["temperature_2m_max"][i]), "tmin": round(j["temperature_2m_min"][i]),
                 "rain_mm": float(j["precipitation_sum"][i] or 0), "rain_prob": int(j["precipitation_probability_max"][i] or 0)}
                for i in range(len(j["time"]))]
        return ToolResult(True, self.name, {"location": location, "days": rows, "advisories": advisory_rules(rows)},
                          as_of=now_ist().date().isoformat(), fetched_at=now_ist().isoformat(timespec="seconds"),
                          source="Open-Meteo forecast API", mode="live")


# ----------------------------------------------------------------------------------------------------------------------
# Answer templates: the "LLM only phrases the structured result" rule (no free generation of numbers)
# ----------------------------------------------------------------------------------------------------------------------
def _rs(x: float) -> str:
    return f"₹{int(round(x)):,}"


def render_mandi(res: ToolResult, lang: str, commodity: str) -> str:
    name = G.localized(G.CROP_NAMES, commodity, lang)
    rows = res.data["rows"]
    req = res.data.get("requested_market")
    rows = [r for r in rows if req and r["market"].lower() == req.lower()][:1] if res.data.get("market_matched") else rows[:2]
    asof = fmt_date(res.as_of)
    if lang == "en":
        parts = [f"{r['market']}: modal {_rs(r['modal'])} per quintal (min {_rs(r['min'])}, max {_rs(r['max'])})" for r in rows]
        txt = f"{name} prices - " + "; ".join(parts) + f". Data as of {asof}. Source: {res.source}."
        if res.stale:
            txt += " This data may be outdated."
        return txt
    if lang == "mr":
        parts = [f"{G.localized(G.LOCATION_NAMES, r['market'], 'mr')} मध्ये मोडल भाव {_rs(r['modal'])} प्रति क्विंटल (किमान {_rs(r['min'])}, कमाल {_rs(r['max'])})" for r in rows]
        txt = f"{name} बाजारभाव: " + "; ".join(parts) + f". आकडेवारी {asof} पर्यंतची आहे. स्रोत: {res.source}."
        if res.stale:
            txt += " ही माहिती जुनी असू शकते."
        return txt
    parts = [f"{G.localized(G.LOCATION_NAMES, r['market'], 'hi')} में मोडल भाव {_rs(r['modal'])} प्रति क्विंटल (न्यूनतम {_rs(r['min'])}, अधिकतम {_rs(r['max'])})" for r in rows]
    txt = f"{name} का मंडी भाव: " + "; ".join(parts) + f". आंकड़े {asof} तक के हैं। स्रोत: {res.source}।"
    if res.stale:
        txt += " यह जानकारी पुरानी हो सकती है।"
    return txt


_ADV = {
    "HEAVY_RAIN": {"hi": "अगले 2 दिन में लगभग {rain_mm_48h} मिमी बारिश की संभावना है। छिड़काव और खाद टालें और खेत में जल निकास ठीक रखें।",
                   "mr": "पुढील 2 दिवसांत सुमारे {rain_mm_48h} मिमी पावसाची शक्यता आहे. फवारणी आणि खते देणे टाळा आणि शेतात पाण्याचा निचरा ठेवा.",
                   "en": "About {rain_mm_48h} mm of rain is likely in the next 2 days. Postpone spraying and fertiliser and keep drainage clear."},
    "LIGHT_RAIN": {"hi": "अगले 2 दिन में हल्की बारिश (लगभग {rain_mm_48h} मिमी) हो सकती है। छिड़काव से पहले मौसम देख लें।",
                   "mr": "पुढील 2 दिवसांत हलका पाऊस (सुमारे {rain_mm_48h} मिमी) पडू शकतो. फवारणीपूर्वी हवामान पाहा.",
                   "en": "Light rain (about {rain_mm_48h} mm) is possible in the next 2 days. Check the sky before spraying."},
    "DRY_SPELL": {"hi": "अगले 2 दिन बारिश की संभावना बहुत कम है। छिड़काव और खेत के काम किए जा सकते हैं।",
                  "mr": "पुढील 2 दिवस पावसाची शक्यता खूप कमी आहे. फवारणी आणि शेतीची कामे करता येतील.",
                  "en": "Rain is unlikely in the next 2 days. Spraying and field work can go ahead."},
    "HEAT": {"hi": "तापमान {tmax} डिग्री तक जा सकता है। सुबह या शाम हल्की सिंचाई करें।",
             "mr": "तापमान {tmax} अंशांपर्यंत जाऊ शकते. सकाळी किंवा संध्याकाळी हलके पाणी द्या.",
             "en": "Temperature may reach {tmax} degrees. Irrigate lightly in the morning or evening."},
    "COLD": {"hi": "न्यूनतम तापमान {tmin} डिग्री तक गिर सकता है। रात में ठंड से फसल को बचाएं।",
             "mr": "किमान तापमान {tmin} अंशांपर्यंत घसरू शकते. रात्री थंडीपासून पिकाचे संरक्षण करा.",
             "en": "Minimum temperature may fall to {tmin} degrees. Protect crops from the cold at night."},
}


def render_weather(res: ToolResult, lang: str) -> str:
    loc, days = res.data["location"], res.data["days"]
    lname = G.localized(G.LOCATION_NAMES, loc, lang) if lang != "en" else loc
    asof = fmt_date(res.as_of)
    d0 = days[0]
    adv = " ".join(_ADV[a["code"]][lang].format(**a) for a in res.data["advisories"])
    if lang == "en":
        head = (f"{lname} forecast: today {d0['tmin']} to {d0['tmax']} °C, rain chance {d0['rain_prob']}%. {adv} "
                f"Forecast as of {asof}. Source: {res.source}.")
    elif lang == "mr":
        head = (f"{lname} हवामान अंदाज: आज {d0['tmin']} ते {d0['tmax']} °C, पावसाची शक्यता {d0['rain_prob']}%. {adv} "
                f"अंदाज {asof} पर्यंतचा आहे. स्रोत: {res.source}.")
    else:
        head = (f"{lname} का मौसम पूर्वानुमान: आज {d0['tmin']} से {d0['tmax']} °C, बारिश की संभावना {d0['rain_prob']}%। {adv} "
                f"पूर्वानुमान {asof} तक का है। स्रोत: {res.source}।")
    return head
