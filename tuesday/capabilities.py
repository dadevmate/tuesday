"""Everyday assistant abilities that work without the cloud: web search and reading, weather, time,
a calculator, reminders and local vision. All of it runs on hardware the owner controls."""
import ast
import base64
import html
import json
import math
import operator
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"}
TZ = ZoneInfo(config.TZ_NAME)


def _get(url: str, data: bytes | None = None, timeout: float = 20) -> str:
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(2_000_000)
        charset = r.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


# ---------------- web ----------------
SEARXNG = config.env("TUESDAY_SEARXNG_URL", "http://127.0.0.1:8888")


def search_web(query: str, max_results: int = 6) -> list[dict]:
    """The owner's own SearXNG on this Mac (./run.sh --install-search): one query spread over several engines, with no
    account, cookies or tracking. Falls back to DuckDuckGo's HTML results if SearXNG isn't running."""
    try:
        data = json.loads(_get(f"{SEARXNG}/search?" + urllib.parse.urlencode({"q": query, "format": "json"}), timeout=12))
        hits = [{"title": r.get("title", ""), "url": r["url"], "snippet": (r.get("content") or "")[:300]} for r in data.get("results", []) if r.get("url")]
        if hits:
            return hits[:max_results]
    except Exception:
        pass
    return _search_ddg(query, max_results)


def _search_ddg(query: str, max_results: int = 6) -> list[dict]:
    """DuckDuckGo HTML results (no API key)."""
    page = _get("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode({"q": query}).encode())
    results = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?(?:class="result__snippet"[^>]*>(.*?)</a>)?', page, re.S):
        href, title, snippet = m.group(1), m.group(2), m.group(3) or ""
        if "uddg=" in href:
            href = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0])
        clean = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
        if href.startswith("http") and "duckduckgo.com/y.js" not in href:
            results.append({"title": clean(title), "url": href, "snippet": clean(snippet)})
        if len(results) >= max_results:
            break
    return results


def fetch_page(url: str, max_chars: int = 6000) -> str:
    if not url.startswith(("http://", "https://")):
        return "Only http(s) URLs."
    page = _get(url)
    page = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", " ", page)
    title = re.search(r"(?is)<title>(.*?)</title>", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    text = re.sub(r"\s+", " ", text).strip()
    return (f"{html.unescape(title.group(1).strip())}\n\n" if title else "") + text[:max_chars]


# ---------------- weather & time ----------------
WMO = {0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 80: "rain showers", 81: "heavy showers", 82: "violent showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


def weather(place: str = "") -> dict:
    place = place or config.CITY
    geo = json.loads(_get(f"https://geocoding-api.open-meteo.com/v1/search?count=1&name={urllib.parse.quote(place)}"))
    if not geo.get("results"):
        return {"error": f"unknown place {place}"}
    g = geo["results"][0]
    q = (f"https://api.open-meteo.com/v1/forecast?latitude={g['latitude']}&longitude={g['longitude']}&timezone=auto"
         "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
         "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code&forecast_days=3")
    w = json.loads(_get(q))
    c, d = w["current"], w["daily"]
    return {
        "place": f"{g['name']}, {g.get('country', '')}",
        "now": f"{c['temperature_2m']}°C, {WMO.get(c['weather_code'], 'code ' + str(c['weather_code']))}, humidity {c['relative_humidity_2m']}%, wind {c['wind_speed_10m']} km/h",
        "next_days": [f"{d['time'][i]}: {d['temperature_2m_min'][i]}–{d['temperature_2m_max'][i]}°C, {WMO.get(d['weather_code'][i], '')}, rain {d['precipitation_probability_max'][i]}%" for i in range(len(d["time"]))],
    }


def now() -> str:
    t = datetime.now(TZ)
    return f"{t.strftime('%A %d %B %Y')}, {t.strftime('%I:%M %p').lstrip('0')} ({t.strftime('%H:%M')}), timezone {TZ.key}"


# ---------------- calculator ----------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow,
        ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {k: getattr(math, k) for k in ("sqrt", "log", "log10", "log2", "exp", "sin", "cos", "tan", "asin", "acos", "atan", "floor", "ceil", "fabs", "factorial", "radians", "degrees")}
_FUNCS.update(abs=abs, round=round, min=min, max=max)
_CONST = {"pi": math.pi, "e": math.e}


def calculate(expression: str):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            if isinstance(n.op, ast.Pow) and abs(ev(n.right)) > 1000:
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _CONST:
            return _CONST[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError("unsupported expression")
    return ev(ast.parse(expression.replace("^", "**"), mode="eval"))


# ---------------- reminders ----------------
REMINDERS = config.DATA / "reminders.json"
_rlock = threading.Lock()


def _load_r() -> list[dict]:
    try:
        return json.loads(REMINDERS.read_text())
    except Exception:
        return []


def _save_r(rs):
    REMINDERS.write_text(json.dumps(rs, indent=1))


def set_reminder(text: str, in_minutes: float | None = None, at: str | None = None) -> dict:
    """at: 'HH:MM' (today, or tomorrow if passed) or ISO datetime; or in_minutes from now."""
    t = datetime.now(TZ)
    if in_minutes is not None:
        due = t + timedelta(minutes=float(in_minutes))
    elif at:
        if re.fullmatch(r"\d{1,2}:\d{2}", at.strip()):
            h, m = map(int, at.split(":"))
            due = t.replace(hour=h, minute=m, second=0, microsecond=0)
            if due <= t:
                due += timedelta(days=1)
        else:
            due = datetime.fromisoformat(at)
            due = due if due.tzinfo else due.replace(tzinfo=TZ)
    else:
        return {"error": "give in_minutes or at"}
    with _rlock:
        rs = _load_r()
        r = {"id": int(time.time() * 1000) % 10**9, "text": text, "due": due.isoformat(), "done": False, "created": t.isoformat()}
        rs.append(r)
        _save_r(rs)
    return {"set": r["id"], "due": due.strftime("%a %d %b %H:%M"), "text": text}


def list_reminders() -> list[dict]:
    return [r for r in _load_r() if not r["done"]]


def cancel_reminder(reminder_id: int) -> dict:
    with _rlock:
        rs = [r for r in _load_r() if r["id"] != int(reminder_id)]
        _save_r(rs)
    return {"cancelled": reminder_id}


def start_reminder_loop(announce):
    """announce(text) is called when a reminder is due (speaks through the robot and logs it).
    During quiet hours due reminders wait, and are said together when quiet hours end, except reminders he set during
    the quiet hours themselves (he's clearly awake: "remind me in 10 minutes" at 23:00 should go off)."""
    from . import routines  # late import: routines imports this module

    def loop():
        while True:
            try:
                due = []
                quiet = routines.quiet_now()
                with _rlock:
                    rs = _load_r()
                    n = datetime.now(TZ)
                    for r in rs:
                        set_while_quiet = bool(r.get("created")) and routines.quiet_now(datetime.fromisoformat(r["created"])) \
                            and n - datetime.fromisoformat(r["created"]) < timedelta(hours=6)
                        if quiet and not set_while_quiet:
                            continue
                        if not r["done"] and datetime.fromisoformat(r["due"]) <= n:
                            r["done"] = True
                            due.append(r)
                    if due:
                        _save_r(rs)
                held = [r for r in due if datetime.fromisoformat(r["due"]) < datetime.now(TZ) - timedelta(minutes=5)]
                if len(held) > 1:
                    announce("Held overnight: " + "; ".join(r["text"] for r in held) + ".")
                    due = [r for r in due if r not in held]
                for r in due:
                    announce(f"Reminder: {r['text']}")
            except Exception as e:
                print("[reminders]", e, flush=True)
            time.sleep(15)
    threading.Thread(target=loop, daemon=True, name="reminders").start()


# ---------------- local vision ----------------
def describe_image(jpeg: bytes | None, question: str = "Describe what you see, briefly and concretely.") -> str:
    if not jpeg:
        return "No camera image available (robot not connected)."
    # the instruct variant answers directly (the thinking one spent ~400 hidden tokens, 15 s, per look); kept loaded only
    # 2 min: next to Tuesday Core and another mind it would overflow the GPU's memory (the core then crashes)
    body = {"model": config.VISION_MODEL, "stream": False, "keep_alive": "30s", "options": {"num_predict": 160, "temperature": 0.1},
            "messages": [{"role": "user", "content": question + " The image is from a small robot's camera at desk height; "
                          "don't mention the robot's own lift or arms in the foreground.", "images": [base64.b64encode(jpeg).decode()]}]}
    req = urllib.request.Request(config.OLLAMA_URL + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["message"]["content"].strip()
