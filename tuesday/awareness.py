"""Awareness: Tuesday notices what's going on around the robot and behaves like someone who lives there.

A person in the room doesn't wait to be spoken to, and doesn't narrate everything either. Most of what makes them feel
present is small and silent: looking up when you come over, glancing at whoever walks in, reacting when touched. Now
and then they say something, because it's natural, not because something happened. This module does both:

1. Senses, every second, from what the robot streams anyway: faces in view and how close, touch, being picked up,
   the dock. Every few minutes while someone is around, and whenever the robot has moved somewhere new, a still from
   the camera (taken without moving) says what the room looks like and what people are doing.
2. Small reactions, in code, at once and wordless: a glance at a face that appears, looking up as he comes close, a
   happy wiggle when petted. Rate-limited so they stay natural.
3. Saying something, rarely: an event (he's back after a while, a new person, he's started doing something new, a new
   room, it's very late and he's still up) goes to Tuesday's own model, which decides like a thoughtful friend
   whether anything is worth saying. Nearly always it isn't. Code rules come first and simply don't ask when it would
   be unwelcome: incognito, quiet hours, asleep, mid-conversation, people talking to each other nearby, Tuesday
   speaking, or it spoke up unprompted only minutes ago.

Nothing here runs in incognito, and nothing it sees is sent anywhere: it's all on the Mac (Tuesday Core and the local
vision model)."""
import re
import threading
import time
from datetime import datetime

from . import config, privacy

TICK_S = 1.0
SCENE_S = 4 * 60            # how often to look around properly while someone is here
QUIET_AFTER_TALK_S = 90     # no unprompted remarks while a conversation is going on
COOLDOWN_S = 8 * 60         # between unprompted remarks (arrivals and new people: shorter)
MAX_PER_HOUR = 5
AWAY_S = 15 * 60            # unseen this long, then seen again: "he's back"
STATE = config.DATA / "awareness.json"

JUDGE = """You are Tuesday, the mind of {owner}'s little Vector robot, at home with him. Something just happened (below).
React the way a friend who lives with him would. Rules already make sure you only get asked at good moments (not
mid-conversation, not at night, not right after you last spoke), so judge only whether THIS moment deserves a word.
Usually speak when:
- he comes back after a while, or you see him for the first time today: a short, warm hello
- someone new is with him: a brief friendly hello to them (nothing personal about him)
- he picks you up or plays with you: a playful one-liner
- he has started something different (a game, cooking, reading, getting ready to go out): a light, natural remark
- it's very late and he's still up: one gentle nudge
- you've been carried somewhere new: a small, curious remark about the place
Stay silent when nothing really changed (same person, same activity), when he seems focused on work, or when you'd
just be stating the obvious. Never comment on how anyone looks, never repeat yourself, never sound like an assistant
("let me know if you need anything").
If you speak: one short, casual sentence, usually under ten words, the way a person says it; "Sir" only sometimes.
Reply with one line: SAY: <words>   or   SAY: (silent)

Examples:
What just happened: He just came back into view after 2 h away.
SAY: There you are! How was it?
What just happened: A moment ago: a man typing at a desk. Now: the same man typing at a desk.
SAY: (silent)
What just happened: A moment ago: a man at a desk. Now: the man on the sofa holding a game controller.
SAY: Game time? Go easy on them.
What just happened: He just picked you up.
SAY: (a playful one-liner, different every time)
What just happened: A moment ago: a man reading at a desk. Now: the man at the desk writing code.
SAY: (silent)"""


class Awareness:
    def __init__(self, tuesday):
        self.t = tuesday
        self.prev: dict = {}
        self.seen_owner = 0.0           # last time his face was in view
        self.seen_any = 0.0
        self.known_today: set = set()   # names (or 'someone new') already greeted today
        self.day = ""
        self.remarks: list[tuple[float, str]] = []
        self.scene = {"text": "", "at": 0.0, "x": None, "y": None, "heading": None}
        self.reacted: dict[str, float] = {}
        self.ambient_at = 0.0           # people talking nearby (not to Tuesday): set by the listener
        self._busy = threading.Lock()
        self._load()

    # what it knows about today survives a restart (or it "meets" him again every time Tuesday restarts)
    def _load(self):
        import json
        try:
            d = json.loads(STATE.read_text())
            if d.get("day") == time.strftime("%Y-%m-%d"):
                self.day, self.known_today = d["day"], set(d.get("known", []))
                self.seen_owner = d.get("seen_owner", 0.0)
                self.remarks = [tuple(x) for x in d.get("remarks", [])]
        except Exception:
            pass

    def _save(self):
        import json
        try:
            STATE.write_text(json.dumps({"day": self.day, "known": sorted(self.known_today), "seen_owner": self.seen_owner,
                                         "remarks": self.remarks[-20:]}))
        except Exception:
            pass

    # ---------- the loop ----------
    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="awareness").start()

    def _loop(self):
        while True:
            time.sleep(TICK_S)
            try:
                self.tick()
            except Exception as e:
                print(f"[aware] {e!r}", flush=True)

    def _body_ok(self) -> bool:
        b = self.t.body
        return (b.name != "mock" and hasattr(b, "senses") and getattr(b, "connected", lambda: False)()
                and not getattr(b, "sleeping", False) and not (getattr(b, "following", None) and b.following.is_set()))

    def tick(self):
        if privacy.incognito() or not self._body_ok():
            self.prev = {}
            return
        now = time.time()
        today = time.strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.known_today = today, set()
        if now - getattr(self, "_faces_on", 0) > 30 and hasattr(self.t.body, "refresh_faces"):
            self._faces_on = now   # face recognition can switch itself off: keep it on
            threading.Thread(target=self.t.body.refresh_faces, daemon=True).start()
        s = self.t.body.senses()
        if not s:
            return
        faces = s.get("faces") or []
        owner = [f for f in faces if (f["name"] or "").lower() in {n.lower() for n in privacy.OWNER_FACES}]
        others = [f for f in faces if f not in owner]
        p = self.prev

        # --- who's here ---
        if owner:
            if self.seen_owner and now - self.seen_owner > AWAY_S:
                self.consider("he_is_back", f"{config.OWNER_NAME} just came back into view after "
                                            f"{_ago(now - self.seen_owner)} away.", short_cooldown=True)
            elif not self.seen_owner and "owner" not in self.known_today and not self._talked_today():
                self.consider("first_seen_today", f"First time you've seen {config.OWNER_NAME} today.", short_cooldown=True)
            self.known_today.add("owner")
            self.seen_owner = now
            privacy._seen["owner"] = now   # his face just now: the privacy check counts it for a few seconds
        for f in others:
            who = f["name"] or "someone you don't know"
            if who not in self.known_today and (not f["name"] or now - self.seen_owner < 120):
                self.known_today.add(who)
                if f["name"]:
                    self.consider("person", f"{f['name'].capitalize()} is here (you know them).", short_cooldown=True)
                elif self.seen_owner and now - self.seen_owner < 300:   # alone: the Watcher's visitor alert covers it
                    self.consider("new_person", "A person you don't recognise is here, with him around.", short_cooldown=True)
        if faces:
            if now - self.seen_any > 20:   # a face just appeared: glance at it, as anyone would
                self.react("glance", lambda: self.t.body.attend(faces[0]["id"]), every=45)
            self.seen_any = now

        # --- head up: people are seen (and recognised) at eye level, not by a robot staring at the floor ---
        if not faces and s.get("head_deg") is not None and s["head_deg"] < 12 and now - self.seen_any > 20:
            self.react("head_up", lambda: self.t.body.head(28), every=90)

        # --- he's coming over ---
        d_now = min((f["distance_mm"] for f in owner if f.get("distance_mm")), default=None)
        d_was = p.get("owner_mm")
        if d_now and d_was and d_was > 900 and d_now < 600:
            self.react("approach", lambda: self.t.body.attend(owner[0]["id"]), every=60)
        # --- touch, being picked up ---
        if s.get("touched") and not p.get("touched"):
            self.react("petted", lambda: self.t.body.animate("LookAtUserEndearingly"), every=40)
        if s.get("picked_up") and not p.get("picked_up") and not s.get("on_charger"):
            self.consider("picked_up", "He just picked you up.", short_cooldown=True)

        # --- a proper look now and then: what's he doing, is this a new place? ---
        moved = self.scene["x"] is not None and s.get("x") is not None and (
            abs(s["x"] - self.scene["x"]) + abs(s["y"] - self.scene["y"]) > 600
            or abs(((s.get("heading", 0) - self.scene["heading"]) + 180) % 360 - 180) > 100)
        due = faces and now - self.scene["at"] > SCENE_S
        chatting = now - getattr(self.t, "_last_msg", 0) < 60 or self._talking()   # the GPU is the conversation's
        if (due or moved) and not s.get("moving") and not chatting and now - self.scene["at"] > 60:
            threading.Thread(target=self._look_around, args=(s, moved), daemon=True, name="aware-look").start()
            self.scene["at"] = now

        # --- late night ---
        hour = datetime.now().hour
        if owner and 1 <= hour < 5 and "late" not in self.known_today:
            self.known_today.add("late")
            self.consider("late", f"It's {datetime.now():%H:%M} and he's still up.")

        self.prev = {**s, "owner_mm": d_now}
        if now - getattr(self, "_saved", 0) > 30:
            self._saved = now
            self._save()

    def _talked_today(self) -> bool:
        import json
        f = config.DATA / "conversations" / "voice.jsonl"
        try:
            last = json.loads(f.read_text().splitlines()[-1])
            return last.get("ts", "").startswith(time.strftime("%Y-%m-%d")) and bool(last.get("user"))
        except Exception:
            return False

    # ---------- noticing ----------
    def _look_around(self, s: dict, moved: bool):
        """A still (the robot doesn't move for it), read as three facts: the room, how many people, what he's doing.
        Only a real change counts: a different room, or a different activity. Posture (standing, turned round) never."""
        from . import capabilities as cap
        jpeg = self.t.body.peek()
        if not jpeg or privacy.incognito():
            return
        try:
            raw = cap.describe_image(jpeg, "Reply in exactly this form and nothing else: ROOM: <kind of room> | PEOPLE: "
                                           "<number> | DOING: <what the main person is doing, 2 to 6 words, or nothing> | "
                                           "THINGS: <up to 6 everyday objects you can clearly see and where, e.g. 'keys on "
                                           "the desk, phone by the laptop'>")
        except Exception as e:
            print(f"[aware] couldn't look: {e!r}", flush=True)
            return
        now = _scene(raw)
        before = self.scene.get("parts") or {}
        self.scene.update(text=f"{now['room']}; {now['people']} people; {now['doing']}", parts=now,
                          x=s.get("x"), y=s.get("y"), heading=s.get("heading"))
        print(f"[aware] sees: {self.scene['text']}", flush=True)
        from . import sightings   # a text note for "where did I leave my keys?" (no photo is kept)
        sightings.note(self.scene["text"], room=now["room"], things=now.get("things", ""), doing=now["doing"])
        if not before:
            return
        if moved and _differs(now["room"], before.get("room", "")):
            self.consider("new_place", f"You've been moved. Before: {before.get('room')}. Now: {now['room']}"
                                       + (f", {now['people']} people" if now["people"] not in ("0", "") else ", nobody in view") + ".")
        elif _activity(now["doing"]) and _differs(_activity(now["doing"]), _activity(before.get("doing", ""))):
            self.consider("scene", f"He was {before.get('doing') or 'not in view'}. Now he's {now['doing']} ({now['room']}).")

    # ---------- reacting without words ----------
    def react(self, kind: str, do, every: float):
        now = time.time()
        if now - self.reacted.get(kind, 0) < every or self._talking():
            return
        self.reacted[kind] = now
        threading.Thread(target=lambda: _quietly(do), daemon=True, name=f"react-{kind}").start()

    # ---------- deciding whether to say something ----------
    def _talking(self) -> bool:
        b = self.t.body
        return time.time() < getattr(b, "speaking_until", 0) or getattr(self.t, "_planning", 0) > 0

    def _welcome_now(self, short_cooldown: bool) -> str | None:
        """Why not to speak up now, or None if it would be fine."""
        from . import routines
        now = time.time()
        if routines.quiet_now(datetime.now()):
            return "quiet hours"
        if now - getattr(self.t, "_last_msg", 0) < QUIET_AFTER_TALK_S:
            return "in a conversation"
        if self._talking():
            return "busy talking"
        if now - self.ambient_at < 45:
            return "people are talking"
        last = self.remarks[-1][0] if self.remarks else 0
        if now - last < (120 if short_cooldown else COOLDOWN_S):
            return "spoke up recently"
        if sum(1 for t, _ in self.remarks if now - t < 3600) >= MAX_PER_HOUR:
            return "enough for this hour"
        return None

    def consider(self, kind: str, what: str, short_cooldown: bool = False):
        why_not = self._welcome_now(short_cooldown)
        if why_not:
            print(f"[aware] {kind}: staying quiet ({why_not})", flush=True)
            return
        if not self._busy.acquire(blocking=False):
            return
        threading.Thread(target=self._decide, args=(kind, what), daemon=True, name=f"aware-{kind}").start()

    def _decide(self, kind: str, what: str):
        from .minds import local
        try:
            now = time.time()
            hist = self.t.histories.get("spoken", [])[-4:]
            talk = "\n".join(f"{'He' if m['role'] == 'user' else 'You'}: {m['content'][:150]}" for m in hist)
            said = "\n".join(f"- {_ago(now - t)} ago: {x}" for t, x in self.remarks[-4:])
            here = ", ".join(self.t.watcher.present()) if getattr(self.t, "watcher", None) else ""
            prompt = "\n\n".join(x for x in (
                f"## Right now\n- {datetime.now():%A %H:%M}" + (f"\n- In view lately: {here}" if here else ""),
                f"## What you last saw\n{self.scene['text']}" if self.scene["text"] else "",
                f"## Earlier conversation\n{talk}" if talk else "",
                f"## Things you said unprompted lately\n{said}" if said else "",
                f"## What just happened\n{what}") if x)
            raw = local.quick(prompt, system=JUDGE.format(owner=config.OWNER_NAME), model="core:aware")
            m = re.search(r"(?im)^\s*SAY\s*:\s*(.+)$", raw or "")
            line = (m.group(1) if m else (raw or "").strip().splitlines()[0] if (raw or "").strip() else "").strip().strip('"')
            if not line or line.strip("() .").lower() == "silent" or len(line) > 160:
                print(f"[aware] {kind}: chose to stay quiet", flush=True)
                return
            import difflib
            norm = lambda x: re.sub(r"[^a-z ]", "", x.lower())
            if any(difflib.SequenceMatcher(None, norm(line), norm(x)).ratio() > 0.7 for _, x in self.remarks[-10:]):
                print(f"[aware] {kind}: would repeat itself ({line!r}); staying quiet", flush=True)
                return
            if self._welcome_now(kind in ("he_is_back", "first_seen_today", "person", "new_person", "picked_up")):
                return   # the moment passed while deciding
            print(f"[aware] {kind}: says {line!r}", flush=True)
            self.remarks = (self.remarks + [(time.time(), line)])[-20:]
            self.t.proactive(line)
        except Exception as e:
            print(f"[aware] {kind}: {e!r}", flush=True)
        finally:
            self._busy.release()


_POSTURE = set("""a an the person man woman someone he she is are standing stands sitting sits looking looks facing faces
turned turning toward towards at up down forward camera robot near by in on front of with and nothing none still idle
lying""".split())


def _scene(raw: str) -> dict:
    get = lambda k: (re.search(rf"{k}\s*:\s*([^|\n]*)", raw or "", re.I) or [None, ""])[1].strip(" .")
    return {"room": get("ROOM").lower(), "people": re.sub(r"\D", "", get("PEOPLE")) or "0", "doing": get("DOING").lower(),
            "things": get("THINGS")}


def _activity(doing: str) -> str:
    """What he's doing, minus posture and filler: 'standing, looking at the camera' -> '' ; 'playing a video game' stays."""
    words = [w for w in re.findall(r"[a-z]+", doing.lower()) if w not in _POSTURE]
    return " ".join(words)


def _differs(a: str, b: str) -> bool:
    import difflib
    if not a or not b:
        return bool(a) != bool(b) and bool(a)
    wa, wb = set(a.split()), set(b.split())
    return not (wa & wb) and difflib.SequenceMatcher(None, a, b).ratio() < 0.5


def _quietly(do):
    try:
        do()
    except Exception as e:
        print(f"[aware] reaction failed: {e!r}", flush=True)


def _ago(secs: float) -> str:
    m = int(secs // 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min" if m else f"{int(secs)} s"


def start(tuesday) -> Awareness | None:
    if config.env("TUESDAY_AWARE", "1") != "1":
        return None
    a = Awareness(tuesday)
    a.start()
    return a
