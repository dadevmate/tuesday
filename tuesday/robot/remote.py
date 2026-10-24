"""Split deployment: Tuesday's brain in the cloud, the robot at home.

The cloud can't reach a Vector inside a home network, so a small relay on the LAN (Raspberry Pi, Jetson, any
always-on box next to wire-pod) connects OUT to the brain over HTTPS, long-polls for robot commands, runs them on
the real Vector and posts results back. No ports are opened at home and no models run there.

  brain (cloud):  TUESDAY_ROBOT=remote BODY_TOKEN=... python -m tuesday serve
  relay (home):   TUESDAY_BRAIN_URL=https://... BODY_TOKEN=... TUESDAY_ROBOT=vector python -m tuesday relay
"""
import base64
import itertools
import json
import threading
import time
import urllib.request

from .. import config
from .base import Body

POLL_SECONDS = 25


class RemoteBody(Body):
    """Brain side: every Body call becomes a command for the relay."""
    name = "remote"

    def __init__(self):
        self._ids = itertools.count(1)
        self._queue: list[dict] = []
        self._results: dict[int, dict] = {}
        self._cv = threading.Condition()
        self.last_seen = 0.0
        self.relay_info = {}
        self._voice = []

    # ----- relay endpoints (called by the server) -----
    def poll(self, info: dict) -> dict | None:
        self.last_seen = time.time()
        self.relay_info = info or self.relay_info
        with self._cv:
            if not self._queue:
                self._cv.wait(timeout=POLL_SECONDS)
            return self._queue.pop(0) if self._queue else None

    def result(self, r: dict):
        with self._cv:
            self._results[r["id"]] = r
            self._cv.notify_all()

    def connected(self) -> bool:
        return time.time() - self.last_seen < POLL_SECONDS + 15

    # ----- command plumbing -----
    def _call(self, method: str, timeout: float = 60, **args):
        if not self.connected():
            raise RuntimeError("Robot relay is offline (the home relay isn't connected).")
        cid = next(self._ids)
        with self._cv:
            self._queue.append({"id": cid, "method": method, "args": args})
            self._cv.notify_all()
            end = time.time() + timeout
            while cid not in self._results:
                left = end - time.time()
                if left <= 0:
                    raise RuntimeError(f"robot did not answer {method} in time")
                self._cv.wait(timeout=left)
            r = self._results.pop(cid)
        if not r.get("ok"):
            raise RuntimeError(r.get("error", "robot error"))
        res = r.get("result")
        if isinstance(res, dict) and "__bytes__" in res:
            return base64.b64decode(res["__bytes__"])
        return res

    # ----- Body interface -----
    def status(self):
        if not self.connected():
            return {"robot": "remote (relay offline)", "last_seen_s_ago": round(time.time() - self.last_seen) if self.last_seen else None}
        return {**self._call("status", timeout=20), "via": "home relay", "relay": self.relay_info}

    def say(self, text): return self._call("say", text=text)
    def drive(self, distance_mm, speed_mmps=100): return self._call("drive", distance_mm=distance_mm, speed_mmps=speed_mmps)
    def turn(self, degrees): return self._call("turn", degrees=degrees)
    def head(self, degrees): return self._call("head", degrees=degrees)
    def lift(self, height): return self._call("lift", height=height)
    def look(self): return self._call("look", timeout=30)
    def faces(self): return self._call("faces", timeout=6)  # checked on every spoken request (guest mode): keep it snappy
    def enroll_face(self, name): return self._call("enroll_face", name=name)
    def known_faces(self): return self._call("known_faces", timeout=20)
    def animate(self, trigger): return self._call("animate", trigger=trigger)
    def off_charger(self): return self._call("off_charger")
    def to_charger(self): return self._call("to_charger", timeout=120)
    def scan_room(self): return self._call("scan_room", timeout=300)
    def show_on_face(self, png, seconds=5): return self._call("show_on_face", png={"__bytes__": base64.b64encode(png).decode()}, seconds=seconds)

    def on_voice(self, handler):
        self._voice.append(handler)

    def voice_in(self, text: str):
        for h in self._voice:
            threading.Thread(target=h, args=(text,), daemon=True).start()


# ---------------- relay (runs at home, next to the robot) ----------------
def run_relay():
    from .vector import make_body

    brain = config.env("TUESDAY_BRAIN_URL").rstrip("/")
    token = config.env("BODY_TOKEN")
    if not brain or not token:
        raise SystemExit("Set TUESDAY_BRAIN_URL and BODY_TOKEN.")
    body = make_body()
    hdr = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

    def post(path, payload, timeout=POLL_SECONDS + 10):
        req = urllib.request.Request(brain + path, data=json.dumps(payload).encode(), headers=hdr)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")

    # Voice commands heard by the robot go up to the brain; its reply comes back down as a `say` command.
    body.on_voice(lambda text: post("/body/voice", {"text": text}, timeout=30))
    print(f"[relay] body: {body.name} -> brain {brain}", flush=True)
    backoff = 1
    while True:
        try:
            cmd = post("/body/poll", {"body": body.name})
            backoff = 1
            if not cmd:
                continue
            try:
                fn = getattr(body, cmd["method"])
                args = {k: (base64.b64decode(v["__bytes__"]) if isinstance(v, dict) and "__bytes__" in v else v) for k, v in cmd["args"].items()}
                res = fn(**args)
                if isinstance(res, (bytes, bytearray)):
                    res = {"__bytes__": base64.b64encode(res).decode()}
                post("/body/result", {"id": cmd["id"], "ok": True, "result": res}, timeout=30)
            except Exception as e:
                post("/body/result", {"id": cmd["id"], "ok": False, "error": str(e)}, timeout=30)
        except Exception as e:
            print(f"[relay] brain unreachable ({e}); retrying in {backoff}s", flush=True)
            time.sleep(backoff)
            backoff = min(60, backoff * 2)
