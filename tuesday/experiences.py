"""What Tuesday lives through with the robot becomes memory: who it saw and when, visitors while the owner was out,
faces it learned, rooms it mapped. Raw events go to data/experiences.jsonl; the useful ones also become memories, so
"when did Sam last come by?" or "did anyone come in while I was out?" can be answered.

Nothing is observed or recorded while incognito. Needs a real robot body (the Mac relay when Tuesday is in the cloud)."""
import json
import threading
import time

from . import config, memory, privacy

LOG = config.DATA / "experiences.jsonl"
POLL_S = 30
SEEN_GAP_S = 30 * 60      # a person counts as "seen again" after 30 min out of view
VISITOR_GAP_S = 30 * 60   # at most one unknown-visitor alert per 30 min
OWNER_AWAY_S = 10 * 60    # the owner counts as away after 10 min unseen


def _stamp(t: float | None = None) -> str:
    return time.strftime("%a %d %b %Y at %H:%M", time.localtime(t or time.time()))


def record(kind: str, text: str, memory_text: str | None = None, tags: list[str] | None = None, replace: str | None = None) -> dict | None:
    """Log an event; optionally keep it as a memory (replacing an older one that contains `replace`)."""
    if privacy.incognito():
        return None
    e = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "text": text}
    with LOG.open("a") as f:
        f.write(json.dumps(e) + "\n")
    if memory_text:
        if replace:
            memory.forget(replace)
        memory.remember(memory_text, ["robot", "experience"] + (tags or []), source="robot experience")
    return e


def recent(limit: int = 20, kind: str | None = None) -> list[dict]:
    if not LOG.exists():
        return []
    events = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    return [e for e in events if not kind or e["kind"] == kind][-limit:]


# ----- hooks for robot tools -----
def face_learned(name: str):
    record("face_learned", f"Learned {name}'s face.", f"The robot learned {name}'s face on {_stamp()}.", ["people"], replace=f"learned {name}'s face")


def room_mapped(summary: str):
    record("room_mapped", f"Mapped a room: {summary}"[:300], f"The robot mapped a room on {_stamp()}: {summary}"[:300], ["home", "map"])


# ----- watching -----
class Watcher:
    """Polls the faces the robot can see and turns sightings into events and memories."""

    def __init__(self, tuesday):
        self.t = tuesday
        self.last_seen: dict[str, float] = {}
        self.last_visitor = 0.0
        self.owner_home_day = ""

    def present(self, within_s: int = 120) -> list[str]:
        """Names the robot has seen in the last couple of minutes (for Tuesday's 'right now' context)."""
        now = time.time()
        return sorted(n.capitalize() if n != privacy.OWNER_FACE else config.OWNER_NAME for n, t in self.last_seen.items() if now - t <= within_s)

    def _robot_ready(self) -> bool:
        b = self.t.body
        if b.name == "mock":
            return False
        return b.connected() if hasattr(b, "connected") else True

    def tick(self, now: float | None = None):
        now = now or time.time()
        if privacy.incognito() or not self._robot_ready():
            return
        try:
            names = [str(f.get("name") or "unknown") for f in self.t.body.faces()]
        except Exception:
            return
        owner = privacy.OWNER_FACE
        for n in set(names):
            key = n.lower()
            if key == "unknown":
                continue
            if key in privacy.OWNER_FACES:   # any name his face is enrolled under
                key = owner
            first_in_a_while = now - self.last_seen.get(key, 0) > SEEN_GAP_S
            self.last_seen[key] = now
            if key == owner:
                day = time.strftime("%Y-%m-%d", time.localtime(now))
                if day != self.owner_home_day:
                    self.owner_home_day = day
                    record("owner_seen", f"First saw {config.OWNER_NAME} today.",
                           f"{config.OWNER_NAME} was first seen at home by the robot on {_stamp(now)}.", ["home"], replace="was first seen at home by the robot")
            elif first_in_a_while:
                record("person_seen", f"Saw {n}.", f"Last saw {n} (via the robot) on {_stamp(now)}.", ["people"], replace=f"Last saw {n} (via the robot)")
        owner_away = now - self.last_seen.get(owner, 0) > OWNER_AWAY_S
        if "unknown" in names and owner_away and now - self.last_visitor > VISITOR_GAP_S:
            self.last_visitor = now
            others = sorted({n for n in names if n != "unknown"})
            text = f"Unrecognised person seen by the robot while you were away ({_stamp(now)})" + (f", with {', '.join(others)}" if others else "") + "."
            if record("visitor", text, text, ["security", "visitors"]):
                self.t.notify(text)


def start_loop(tuesday):
    w = Watcher(tuesday)

    def loop():
        while True:
            try:
                w.tick()
            except Exception as e:
                print("[experiences]", e, flush=True)
            time.sleep(POLL_S)
    threading.Thread(target=loop, daemon=True, name="experiences").start()
    return w
