"""Tuesday's proactive side: morning briefings, an evening check-in and nudges, spoken through the robot.
Times live in data/routines.json (created with defaults on first run, editable by hand or through Tuesday's tools).
Nothing proactive is said during quiet hours (default 22:00-06:00)."""
import json
import random
import re
import threading
import time
from datetime import datetime, timedelta

from . import capabilities as cap, config, memory

FILE = config.DATA / "routines.json"
STATE = config.DATA / "routines-state.json"
GRACE_MIN = 45  # a routine missed (Mac asleep, Tuesday restarting) still runs if this late, then is skipped for the day

DEFAULTS = {
    "quiet": ["22:00", "06:00"],
    "routines": [
        {"id": "briefing", "days": "mon-fri", "at": "06:05", "kind": "briefing", "enabled": True},
        {"id": "exercise", "days": "mon-fri", "at": "06:20", "kind": "nudge", "topic": "exercise", "enabled": True},
        {"id": "weekend-briefing", "days": "sat,sun", "at": "09:00", "kind": "briefing", "enabled": True},
        {"id": "checkin", "days": "daily", "at": "20:30", "kind": "checkin", "enabled": True},
        {"id": "study", "days": "daily", "at": "21:00", "kind": "nudge", "topic": "study", "enabled": True},
        {"id": "sleep", "days": "daily", "at": "21:45", "kind": "nudge", "topic": "sleep", "enabled": True},
        {"id": "journal", "days": "daily", "at": "23:30", "kind": "journal", "silent": True, "enabled": True},
    ],
}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

NUDGES = {
    "exercise": ("Nudge him to exercise this morning: a run, or the gym later.",
                 ["Run time, Sir. The legs file complaints, the future signs off on them.",
                  "Morning run time, Sir. I'd join you, but my wheels are 4 cm tall."]),
    "study": ("Nudge him to spend a focused half hour learning AI engineering tonight. One goal at a time.",
              ["Thirty minutes of AI engineering tonight, Sir? One topic, done properly.",
               "Study block: half an hour on AI engineering, then you're free. Deal?"]),
    "sleep": ("Remind him to wind down and sleep soon; he has an early start.",
              ["Wind-down time, Sir. The alarm arrives whether we like it or not.",
               "Bed soon, Sir. Even Friday powers down. Briefly."]),
}


# ---------------- config ----------------
def load() -> dict:
    if not FILE.exists():
        FILE.write_text(json.dumps(DEFAULTS, indent=1))
    try:
        cfg = json.loads(FILE.read_text())
    except Exception:
        return DEFAULTS
    have = {r["id"] for r in cfg.get("routines", [])}
    added = [r for r in DEFAULTS["routines"] if r["id"] not in have]  # routines added in newer versions
    if added:
        cfg["routines"] = cfg.get("routines", []) + added
        _save(cfg)
    return cfg


def _save(cfg: dict):
    FILE.write_text(json.dumps(cfg, indent=1))


def list_routines() -> dict:
    return load()


def set_routine(routine_id: str, at: str | None = None, days: str | None = None, enabled: bool | None = None) -> dict:
    """Change a routine's time ('HH:MM'), days ('daily', 'mon-fri', 'sat,sun', ...) or switch it on/off.
    routine_id 'quiet' with at='HH:MM-HH:MM' changes quiet hours."""
    cfg = load()
    if routine_id == "quiet" and at and "-" in at:
        cfg["quiet"] = [x.strip() for x in at.split("-", 1)]
        _save(cfg)
        return {"quiet": cfg["quiet"]}
    for r in cfg["routines"]:
        if r["id"] == routine_id:
            if at:
                datetime.strptime(at, "%H:%M")
                r["at"] = at
            if days:
                _day_set(days)
                r["days"] = days
            if enabled is not None:
                r["enabled"] = bool(enabled)
            _save(cfg)
            return r
    return {"error": f"no routine {routine_id}; have {[r['id'] for r in cfg['routines']]}"}


def _day_set(spec: str) -> set[int]:
    spec = spec.lower().replace(" ", "")
    if spec in ("daily", "everyday", "all"):
        return set(range(7))
    out = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = (DAYS.index(x[:3]) for x in part.split("-"))
            out |= set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
        else:
            out.add(DAYS.index(part[:3]))
    return out


def _minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def quiet_now(now: datetime | None = None) -> bool:
    now = now or datetime.now(cap.TZ)
    start, end = (_minutes(x) for x in load().get("quiet", DEFAULTS["quiet"]))
    t = now.hour * 60 + now.minute
    return start <= t < end if start < end else t >= start or t < end


def due(now: datetime, state: dict) -> list[dict]:
    """Routines whose time has come today, not yet run today, within the grace window, outside quiet hours."""
    out = []
    today = now.strftime("%Y-%m-%d")
    for r in load().get("routines", []):
        if not r.get("enabled", True) or state.get(r["id"]) == today or now.weekday() not in _day_set(r.get("days", "daily")):
            continue
        at = now.replace(hour=_minutes(r["at"]) // 60, minute=_minutes(r["at"]) % 60, second=0, microsecond=0)
        silent = r.get("silent")  # silent routines (the journal) say nothing, so quiet hours don't apply
        if at <= now <= at + timedelta(minutes=GRACE_MIN) and (silent or (not quiet_now(at) and not quiet_now(now))):
            out.append(r)
    return out


# ---------------- what Tuesday says ----------------
def _spoken(instruction: str, material: str = "", fallback: str = "") -> str:
    """Write a spoken line in Tuesday's voice with the local model (free, private). Falls back to a fixed line."""
    from .agent import PERSONA
    from .minds import local  # imported late: the minds import tools, which import this module
    if not local.available():
        return fallback
    prompt = f"{instruction}\nPlain spoken English, no markdown, no lists, no emojis. Address him as Sir.\n\n{material}".strip()
    try:
        out = local.quick(prompt, system=PERSONA).strip().strip('"')
        return out or fallback
    except Exception:
        return fallback


def _headlines() -> str:
    """Top space / AI / robotics headlines from a web search (DuckDuckGo, no account, nothing personal sent)."""
    lines = []
    for topic in ("space exploration news today", "AI news today", "robotics news today", "stock market today"):
        try:
            r = cap.search_web(topic, 1)
            if r:
                lines.append(r[0]["title"])
        except Exception:
            pass
    return "\n".join(lines)


def _mentioned_today(now: datetime) -> list[str]:
    """Things the owner mentioned that fall on today (memories store absolute dates). Private ones are never spoken."""
    keys = {now.strftime("%Y-%m-%d"), f"{now.day} {now.strftime('%B')}", f"{now.strftime('%B')} {now.day}"}
    out = []
    for m in memory.all_memories():
        if memory.is_private(m) or m.get("ts", "")[:10] >= now.strftime("%Y-%m-%d"):
            continue  # mentioned today means he already knows; a date in an older memory is a plan for today
        text = re.sub(r"\s*\(noted \d{4}-\d{2}-\d{2}\)", "", m["text"])  # the date it was learned isn't the date it's about
        low = text.lower()
        if any(k.lower() in low and not re.search(rf"(as of|confirmed|since)\s+{re.escape(k.lower())}", low) for k in keys):
            out.append(text)
    return out


def briefing(now: datetime | None = None) -> str:
    now = now or datetime.now(cap.TZ)
    parts = [f"Date: {now.strftime('%A %d %B')}"]
    try:
        w = cap.weather(config.CITY)
        parts.append(f"Weather: {w['now']}. Today: {w['next_days'][0]}")
    except Exception:
        pass
    today = now.strftime("%Y-%m-%d")
    rems = [r["text"] + " at " + r["due"][11:16] for r in cap.list_reminders() if r["due"].startswith(today)]
    if rems:
        parts.append("Reminders today: " + "; ".join(rems))
    mentioned = _mentioned_today(now)
    if mentioned:
        parts.append("He mentioned for today: " + "; ".join(mentioned[:3]))
    news = _headlines()
    if news:
        parts.append("News and markets:\n" + news)
    material = "\n".join(parts)
    spoken = _spoken("Give Sir his morning briefing in three or four short spoken sentences: greeting, weather, anything on today, "
                     "then the best one or two headlines. Witty but brisk.", material, fallback=material.replace("\n", ". "))
    return spoken


def checkin() -> str:
    return _spoken("Open the evening check-in: in one or two short spoken sentences, ask Sir how he's feeling and how his day went. "
                   "Warm, a little playful, not syrupy.",
                   fallback=random.choice(["Evening, Sir. How are you feeling, and how did today treat you?",
                                           "Check-in time, Sir. How was the day, honestly?"]))


def nudge(topic: str) -> str:
    instruction, lines = NUDGES.get(topic, (f"Nudge him about {topic}.", [f"Quick nudge, Sir: {topic}."]))
    return _spoken(instruction + " One short spoken sentence.", fallback=random.choice(lines))


def next_up(now: datetime) -> str | None:
    """The next spoken routine today, e.g. 'evening check-in at 20:30'."""
    names = {"briefing": "morning briefing", "checkin": "evening check-in", "nudge": "nudge"}
    t = now.hour * 60 + now.minute
    todays = [r for r in load().get("routines", []) if r.get("enabled", True) and not r.get("silent")
              and now.weekday() in _day_set(r.get("days", "daily")) and _minutes(r["at"]) > t]
    if not todays:
        return None
    r = min(todays, key=lambda r: _minutes(r["at"]))
    label = f"{r.get('topic')} nudge" if r.get("kind") == "nudge" else names.get(r.get("kind"), r["id"])
    return f"{label} at {r['at']}"


def journal(now: datetime | None = None) -> str:
    """Nightly, silent: turn today's conversations with the owner into one dated journal memory, so
    'what did we talk about yesterday?' works. Anything emotional or secret keeps the journal private."""
    now = now or datetime.now(cap.TZ)
    today = now.strftime("%Y-%m-%d")
    turns = []
    for f in (config.DATA / "conversations").glob("*.jsonl"):
        if f.stem == "announcements" or f.stem.endswith("-guest"):
            continue
        for line in f.read_text().splitlines():
            try:
                t = json.loads(line)
            except Exception:
                continue
            if t.get("ts", "").startswith(today) and t.get("user"):
                turns.append(t)
    if not turns:
        return ""
    turns.sort(key=lambda t: t["ts"])
    convo = "\n".join(f"[{t['ts'][11:16]}] {config.OWNER_NAME.upper()}: {t['user'][:300]}\nTUESDAY: {t['tuesday'][:300]}" for t in turns[-60:])
    from .minds import local
    if not local.available():
        return ""
    entry = local.quick(f"Today's conversations between {config.OWNER_NAME} and Tuesday:\n{convo}\n\nWrite a journal entry: 3 to 6 short bullets of what was "
                        f"discussed, decided, planned or left open. Facts only, refer to him as {config.OWNER_NAME}.", system="You write concise factual journals.").strip()
    if entry:
        private = any(memory.PRIVATE_HINTS.search(t["user"]) for t in turns)
        memory.remember(f"Journal {today} ({now.strftime('%A')}): " + " ".join(l.strip("-• ").strip() for l in entry.splitlines() if l.strip()),
                        ["journal"] + (["private"] if private else []), source="nightly journal", dedupe=False)
    return ""  # nothing to say out loud


def run(r: dict) -> str:
    kind = r.get("kind")
    if kind == "journal":
        return journal()
    if kind == "briefing":
        return briefing()
    if kind == "checkin":
        return checkin()
    return nudge(r.get("topic", r["id"]))


# ---------------- loop ----------------
def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def start_loop(tuesday):
    """Check every 30 s; speak due routines through tuesday.announce. A check-in also opens the voice conversation,
    so the owner's spoken answer is understood as a reply to it."""
    def loop():
        while True:
            try:
                now = datetime.now(cap.TZ)
                state = _state()
                for r in due(now, state):
                    state[r["id"]] = now.strftime("%Y-%m-%d")
                    STATE.write_text(json.dumps(state))  # mark first: a failing routine must not repeat every 30 s
                    text = run(r)
                    if text:
                        tuesday.announce(text)
                        if r.get("kind") == "checkin":
                            for pool in ("owner", "spoken"):  # he may answer by voice or on the chat page
                                tuesday.histories.setdefault(pool, tuesday._load_history(pool)).append({"role": "assistant", "content": text})
            except Exception as e:
                print("[routines]", e, flush=True)
            time.sleep(30)
    threading.Thread(target=loop, daemon=True, name="routines").start()
