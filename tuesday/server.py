"""Tuesday's local HTTP server.
- /chat                 talk to Tuesday (chat page, CLI, wire-pod plugin)
- /v1/chat/completions  OpenAI-compatible endpoint, so wire-pod can send Vector voice questions to Tuesday
- /health, and a small chat page at /"""
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .agent import Tuesday

ALLOWED_ORIGINS = set(config.EXTRA_ORIGINS)


def make_handler(tuesday: Tuesday):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            data = json.dumps(body, default=str).encode()
            self.send_response(code)
            origin = self.headers.get("Origin")
            if origin in ALLOWED_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_OPTIONS(self):
            self.send_response(204)
            origin = self.headers.get("Origin")
            if origin in ALLOWED_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                self.send_header("Access-Control-Allow-Private-Network", "true")  # cloud page -> a local Tuesday
            self.end_headers()

        def _api_ok(self):
            return not config.API_TOKEN or self.headers.get("Authorization") == f"Bearer {config.API_TOKEN}"

        def _body_ok(self):
            return bool(config.BODY_TOKEN) and self.headers.get("Authorization") == f"Bearer {config.BODY_TOKEN}"

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                page = (config.ROOT / "tuesday" / "chat.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                return self.wfile.write(page)
            if self.path == "/health":
                return self._send(200, tuesday.health() if self._api_ok() else {"ok": True})
            if self.path == "/robot/faces" and self.client_address[0] == "127.0.0.1":   # diagnostics: who the robot sees now
                try:
                    return self._send(200, {"faces": tuesday.body.faces()})
                except Exception as e:
                    return self._send(200, {"error": str(e)})
            if self.path == "/robot/senses" and self.client_address[0] == "127.0.0.1":   # diagnostics: what it feels now
                a = getattr(tuesday, "aware", None)
                return self._send(200, {"senses": tuesday.body.senses() if hasattr(tuesday.body, "senses") else {},
                                        "aware": bool(a) and a._body_ok(), "sleeping": getattr(tuesday.body, "sleeping", False)})
            if self.path == "/robot/facecheck" and self.client_address[0] == "127.0.0.1":   # diagnostics, robot stays put
                try:
                    return self._send(200, {"faces": tuesday.body.face_check()})
                except Exception as e:
                    return self._send(200, {"error": repr(e)})
            if self.path == "/listener/speaking" and self.client_address[0] == "127.0.0.1":
                # the Mac listener (a separate app) mutes the microphone while the robot speaks
                return self._send(200, {"until": getattr(tuesday.body, "speaking_until", 0), "now": time.time(),
                                        "answered": getattr(tuesday, "answered_at", 0),
                                        # what the robot just said: the listener tells its echo from him cutting in
                                        "said": [t for ts, t in getattr(tuesday.body, "recent_said", []) if time.time() - ts < 30]})
            if not self._api_ok():
                return self._send(401, {"error": "token required"})
            if self.path.startswith("/result/"):
                job = tuesday.jobs.get(self.path.split("/", 2)[2], {"status": "unknown"})
                return self._send(200, job)
            if self.path.startswith("/announcements"):
                since = float(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("since", ["0"])[0] or 0)
                return self._send(200, {"items": [a for a in tuesday.announcements if a["ts"] > since], "now": time.time()})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                b = self._body()
            except json.JSONDecodeError:
                return self._send(400, {"error": "bad json"})
            if self.path.startswith("/body/"):
                body = tuesday.body
                if not self._body_ok() or not hasattr(body, "poll"):
                    return self._send(401, {"error": "relay not authorised"})
                if self.path == "/body/poll":
                    return self._send(200, body.poll(b))
                if self.path == "/body/result":
                    body.result(b)
                    return self._send(200, {"ok": True})
                if self.path == "/body/voice":
                    body.voice_in(str(b.get("text", "")))
                    return self._send(200, {"ok": True})
            if not self._api_ok():
                return self._send(401, {"error": "token required"})
            if self.path == "/listener/cue" and self.client_address[0] == "127.0.0.1":
                # "I heard you": look at him (turn to his face if it's in view), the moment his name is said
                look = getattr(tuesday.body, "attend", None) or (lambda: tuesday.body.head(25))
                threading.Thread(target=look, daemon=True).start()
                return self._send(200, {"ok": True})
            if self.path == "/listener/ambient" and self.client_address[0] == "127.0.0.1":
                if getattr(tuesday, "aware", None):   # people talking nearby, not to Tuesday: it won't butt in
                    tuesday.aware.ambient_at = time.time()
                return self._send(200, {"ok": True})
            if self.path == "/chat":
                msg = (b.get("message") or "").strip()
                if not msg:
                    return self._send(400, {"error": "message required"})
                voice = b.get("voice")
                r = tuesday.handle(msg, channel=b.get("channel", "chat"), overheard=bool(b.get("overheard")),
                                   voice=float(voice) if isinstance(voice, (int, float)) else None, unclear=bool(b.get("unclear")))
                return self._send(200, {**r, "reply": ""} if r.get("spoken") else r)
            if self.path == "/v1/chat/completions":
                # OpenAI-compatible: wire-pod's knowledge graph can point here.
                msgs = b.get("messages") or []
                user = next((m.get("content") for m in reversed(msgs) if m.get("role") == "user"), "")
                if isinstance(user, list):
                    user = " ".join(p.get("text", "") for p in user if isinstance(p, dict))
                r = tuesday.handle(str(user), channel="wirepod")
                if r.get("spoken"):
                    # Already said in Tuesday's own voice through the robot link. wire-pod treats an empty reply as an
                    # error (and then speaks an error message), so it gets a silent full stop instead.
                    r = {**r, "reply": "."}
                if b.get("stream"):
                    # Streaming clients (wire-pod's knowledge graph may ask for it) get the reply as one SSE chunk.
                    base = {"id": f"tuesday-{int(time.time()*1000)}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "tuesday"}
                    chunks = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": r["reply"]}, "finish_reason": None}]},
                              {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
                    data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    return self.wfile.write(data)
                return self._send(200, {
                    "id": f"tuesday-{int(time.time()*1000)}", "object": "chat.completion", "created": int(time.time()), "model": "tuesday",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": r["reply"]}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                })
            self._send(404, {"error": "not found"})

    return H


def serve():
    tuesday = Tuesday()
    from . import capabilities, experiences, routines, runner, watches
    capabilities.start_reminder_loop(tuesday.announce)
    routines.start_loop(tuesday)
    runner.start_loop(tuesday)     # background jobs (and any a restart interrupted)
    if config.env("TUESDAY_LISTEN_INPROCESS") == "1":   # normally the Tuesday Listener app does this (microphone permission)
        from . import listener
        listener.start(tuesday)
    watches.start_loop(tuesday)
    tuesday.watcher = experiences.start_loop(tuesday)
    from . import awareness
    tuesday.aware = awareness.start(tuesday)

    def warm():   # the planner's long prompt into Tuesday Core's cache now, so the first thing he says isn't slow
        try:
            from . import intent
            intent.plan("hello", [], "owner", tuesday.body)
            print("[intent] warmed up", flush=True)
        except Exception as e:
            print(f"[intent] warm-up failed: {e}", flush=True)
    threading.Thread(target=warm, daemon=True, name="warm").start()
    from . import core
    core.on_start.append(warm)   # and again whenever the core restarts (it heals itself after GPU trouble)

    def warm_core():   # load the shared minds now, so the first reply doesn't wait for it
        from . import core
        try:
            core.ensure()
        except Exception as e:
            print(f"[core] unavailable, the shared minds use Ollama: {e}", flush=True)
    threading.Thread(target=warm_core, daemon=True).start()
    httpd = ThreadingHTTPServer((config.HOST, config.PORT), make_handler(tuesday))
    h = tuesday.health()
    print(f"\n  Tuesday is listening on http://{config.HOST}:{config.PORT}")
    print(f"  body: {h['robot']} · minds: {', '.join(f'{k}={v}' for k, v in h['minds'].items())}")
    print(f"  chat: http://{config.HOST}:{config.PORT}/\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        tuesday.body.close()
