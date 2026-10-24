"""Tuesday Core: one shared Qwen3-8B kept in memory, with each mind on it as a LoRA adapter (~15 MB).

The minds that share the base (roster: shared=True) never load or swap: llama.cpp's server applies a mind's adapter
per request. On top:
- Flash attention and a q8 KV cache: half the memory per token of context, same answers.
- Speculative decoding: Qwen3-0.6B (same tokenizer) drafts a few tokens, the 8B checks them in one pass.
- A prompt-cache slot per mind: the stable start of each mind's prompt (persona, focus, profile) is computed once and
  reused, so only the new part of each message is read.
The server outlives Tuesday's own process (pid in data/core.pid) and is restarted only when its adapter set changes.
Without llama.cpp's server, the shared minds fall back to Ollama (tuesday/minds/local.py).

  TUESDAY_CORE=0                 use Ollama only
  TUESDAY_CORE_DRAFT=0           no speculative decoding
  TUESDAY_LORA_<MIND>=<path>     use this adapter for a mind (e.g. a candidate under test); empty = the base alone
"""
import json
import os
import signal
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from . import config

ROOT = config.ROOT
LIVE = ROOT / "train" / "out" / "live"
PORT = config.env_int("TUESDAY_CORE_PORT", 4350)
URL = f"http://127.0.0.1:{PORT}"
PID = Path(config.env("TUESDAY_CORE_PID", str(Path.home() / ".tuesday-core.pid")))   # one per machine, whatever TUESDAY_DATA says
LOG = config.DATA / "logs" / "core.log"
BASE = config.env("TUESDAY_CORE_BASE", "qwen3:8b")       # the shared base, as Ollama already has it
DRAFT = config.env("TUESDAY_CORE_DRAFT_MODEL", "qwen3:0.6b")
CTX = config.env_int("TUESDAY_CORE_CTX", 16384)      # shared by all slots (prompts are 1-3k tokens)
# one prompt cache per mind; the conversation planner (intent.py) has its own, so background work never queues it
SLOTS = ["tuesday", "monday", "wednesday", "thursday", "saturday", "planner", "misc"]
_lock = threading.Lock()
on_start: list = []   # called (in a thread) each time the core has (re)started: e.g. warming the planner's cache
_ok = {"t": 0.0, "v": False}


def server_bin() -> Path | None:
    p = config.env("TUESDAY_LLAMA_SERVER")
    if p:
        return Path(p)
    return next(iter(sorted((ROOT / "train" / "out" / "llama-bin").glob("*/llama-server"))), None)


def ollama_blob(tag: str) -> Path | None:
    """The GGUF file Ollama already downloaded for a model (no second copy on disk)."""
    name, ver = tag.split(":") if ":" in tag else (tag, "latest")
    man = Path.home() / ".ollama" / "models" / "manifests" / "registry.ollama.ai" / "library" / name / ver
    try:
        digest = next(l["digest"] for l in json.loads(man.read_text())["layers"] if l["mediaType"].endswith(".model"))
    except Exception:
        return None
    f = Path.home() / ".ollama" / "models" / "blobs" / digest.replace(":", "-")
    return f if f.exists() else None


def enabled() -> bool:
    return config.env("TUESDAY_CORE", "1") == "1" and bool(server_bin()) and bool(ollama_blob(BASE))


def adapters(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """mind -> adapter file: the promoted ones, overridden by TUESDAY_LORA_<MIND> or `overrides` (empty = base only)."""
    from . import roster
    out = {}
    for m in roster.MINDS.values():
        if not m.shared:
            continue
        key = f"TUESDAY_LORA_{m.name.upper()}"
        path = os.environ[key] if key in os.environ else str(LIVE / m.name / f"{m.name}-lora.gguf")
        path = (overrides or {}).get(m.name, path)
        if path and Path(path).exists():
            out[m.name] = str(Path(path).resolve())
    # the conversation planner (tuesday/intent.py, trained by train/planner_train.py): not a mind, same base
    planner = (overrides or {}).get("planner", os.environ.get("TUESDAY_LORA_PLANNER", str(LIVE / "planner" / "planner-lora.gguf")))
    if planner and Path(planner).exists():
        out["planner"] = str(Path(planner).resolve())
    return out


def _get(path: str, timeout: float = 2):
    with urllib.request.urlopen(URL + path, timeout=timeout) as r:
        return json.loads(r.read())


def running_adapters() -> list[str] | None:
    try:
        return [a["path"] for a in _get("/lora-adapters")]
    except Exception:
        return None


def ready() -> bool:
    """Cheap check (cached a few seconds) that the core is serving."""
    if time.time() - _ok["t"] < 5:
        return _ok["v"]
    try:
        v = _get("/health", timeout=1).get("status") == "ok"
    except Exception:
        v = False
    _ok.update(t=time.time(), v=v)
    return v


def _port_pids() -> list[int]:
    """Whatever listens on the core's port (a server whose pid file was lost)."""
    r = subprocess.run(["/usr/sbin/lsof", "-t", f"-iTCP:{PORT}", "-sTCP:LISTEN"], capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()]


def stop():
    for pid in _port_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        pid = int(PID.read_text())
        os.kill(pid, signal.SIGTERM)
        for _ in range(40):
            os.kill(pid, 0)
            time.sleep(0.25)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass
    PID.unlink(missing_ok=True)
    _ok.update(t=0.0, v=False)


def ensure(overrides: dict[str, str] | None = None, timeout: float = 120) -> dict[str, int] | None:
    """Start the core (or restart it if its adapters changed). Returns {mind: adapter id}, or None if unavailable."""
    if not enabled():
        return None
    want = adapters(overrides)
    with _lock:
        have = running_adapters()
        if have is not None and sorted(have) == sorted(want.values()):
            return {m: have.index(p) for m, p in want.items()}
        stop()
        args = [str(server_bin()), "-m", str(ollama_blob(BASE)), "--host", "127.0.0.1", "--port", str(PORT),
                "--jinja", "--chat-template-kwargs", '{"enable_thinking": false}',
                "-np", str(len(SLOTS)), "-c", str(CTX), "--kv-unified", "-fa", "on", "-ctk", "q8_0", "-ctv", "q8_0",
                "--cache-reuse", "256", "--no-cache-idle-slots", "--no-webui", "--lora-init-without-apply"]   # keep each mind's cached prompt while others run
        if want:
            args += ["--lora", ",".join(want.values())]
        draft = ollama_blob(DRAFT)
        if draft and config.env("TUESDAY_CORE_DRAFT", "1") == "1":
            args += ["-md", str(draft), "--spec-type", "draft-simple", "--spec-draft-n-max", config.env("TUESDAY_CORE_DRAFT_N", "8"),
                     "-ctkd", "q8_0", "-ctvd", "q8_0"]
        LOG.parent.mkdir(exist_ok=True)
        p = subprocess.Popen(args, stdout=LOG.open("a"), stderr=subprocess.STDOUT, start_new_session=True)
        PID.write_text(str(p.pid))
        end = time.time() + timeout
        while time.time() < end:
            if p.poll() is not None:
                raise RuntimeError(f"Tuesday Core failed to start; see {LOG}")
            _ok["t"] = 0.0
            if ready():
                have = running_adapters() or []
                for hook in on_start:   # e.g. warm the planner's prompt cache (after the lock is released)
                    threading.Timer(1.0, hook).start()
                return {m: have.index(path) for m, path in want.items() if path in have}
            time.sleep(0.5)
        raise RuntimeError("Tuesday Core took too long to start")


def slot(mind: str) -> int:
    return SLOTS.index(mind) if mind in SLOTS else SLOTS.index("misc")
