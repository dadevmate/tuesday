"""Claude as Tuesday's advisors: Haiku, Sonnet, Opus and Fable make Tuesday's own minds smarter without ever being
in charge or seeing anything personal.

Tuesday's open minds stay the main system: they hold his memories, write every final answer in Tuesday's voice, and
carry on alone whenever Claude is off, offline, over its usage limit or the message is private. Claude only advises:
  haiku   settles what a spoken request means when Tuesday's own two readings disagree (intent.py); advises on
          moderate questions
  sonnet  expert draft on harder team questions (several fields, technical work, decisions)
  opus    expert draft on the council path (decisions that matter)
  fable   deep research and critical work (fable.py), with web search
Most messages use no Claude at all (tier()): chat, quick questions, robot commands Tuesday is sure of. Each model has
a daily call budget (BUDGET); a spent tier steps down to a cheaper one, then to Tuesday alone.
Tuesday's judge (local) reads the advisors' drafts like any other team member's and decides.

Privacy, enforced in code for every call:
- Only the request itself, briefed by Tuesday on-device (fable.BRIEF): never memories, his profile, feelings or names.
- fable.guard() then scrubs names, places, emails, phone numbers and IDs, and blocks anything emotional or secret.
- Nothing goes out for guests, in incognito, or with TUESDAY_CLAUDE=0.
- Each call is an isolated Claude CLI run: no settings, no connectors, no history, an empty sandbox folder.
- Every prompt actually sent is appended to data/claude-log.jsonl.
After a failure (limit reached, offline) Claude is skipped for a while, so Tuesday never waits on it."""
import json
import os
import subprocess
import threading
import time

from . import config, fable, privacy

TIERS = ("haiku", "sonnet", "opus")   # cheapest first; an exhausted tier steps down, never up
# Calls a day per model (TUESDAY_CLAUDE_BUDGET="haiku=150,sonnet=30,opus=6,fable=4"): enough to make Tuesday sharper,
# never enough to eat his Claude plan. Fable's deep research is the costliest, so it gets the fewest.
BUDGET = {"haiku": 150, "sonnet": 30, "opus": 6, "fable": 4} | {
    k.strip(): int(v) for k, _, v in (x.partition("=") for x in config.env("TUESDAY_CLAUDE_BUDGET", "").split(",")) if v.strip().isdigit()}
USAGE = config.DATA / "claude-usage.json"
MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "fable": fable.MODEL}
LOG = config.DATA / "claude-log.jsonl"
COOLDOWN = 15 * 60          # seconds to leave Claude alone after it failed
_down_until = 0.0
_lock = threading.Lock()

ADVISOR = ("You are an outside expert advising a personal assistant about its client's request. You know nothing about the "
           "client and must not ask for personal details. Give your best concrete answer: key facts, the reasoning that "
           "matters, a clear recommendation. Be concise; no preamble. Say plainly when something is uncertain.")


def enabled(generic: bool = False) -> bool:
    """generic=True: a request with nothing about him in it at all (e.g. writing synthetic training data), which
    incognito doesn't need to stop: incognito is about him, his room and his conversations."""
    return (config.env("TUESDAY_CLAUDE", "1") == "1" and fable.available() and (generic or not privacy.incognito())
            and time.time() >= _down_until)


def used_today() -> dict:
    try:
        u = json.loads(USAGE.read_text())
    except Exception:
        u = {}
    return u.get("counts", {}) if u.get("day") == time.strftime("%Y-%m-%d") else {}


def _count(model: str) -> None:
    with _lock:
        counts = used_today()
        counts[model] = counts.get(model, 0) + 1
        USAGE.write_text(json.dumps({"day": time.strftime("%Y-%m-%d"), "counts": counts}))


def afford(model: str | None, generic: bool = False) -> str | None:
    """The model to actually use: the one asked for if today's budget allows, else the next cheaper tier, else None
    (Tuesday answers alone)."""
    if not model or not enabled(generic):
        return None
    used = used_today()
    if model not in TIERS:
        return model if used.get(model, 0) < BUDGET.get(model, 0) else None
    for m in reversed(TIERS[:TIERS.index(model) + 1]):
        if used.get(m, 0) < BUDGET.get(m, 0):
            return m
    return None


def tier(text: str, path: str, team: list[str], spoken: bool = False) -> str | None:
    """Which Claude model, if any, a message deserves. Most messages deserve none: chat, quick questions and anything
    one specialist mind handles well stay entirely on Tuesday's minds. Harder work earns a stronger advisor:
      haiku   a real question in one field (advice, a plan, a technical explanation)
      sonnet  several fields at once, long or multi-part asks, technical work, decisions
      opus    the council path: decisions that matter (money, career, studies, health, legal)
    Fable (deep research, critical work) is chosen by the council's own path, not here."""
    if path in ("quick", "action", "fable"):
        return None
    if path == "council":
        return "opus"
    from .council import DECIDE, STAKES
    score = (len(team) >= 2) + (len(text) > 220) + bool(DECIDE.search(text)) + bool(STAKES.search(text)) \
        + ("thursday" in team) + (text.count("?") >= 2)
    if score == 0:
        return None if not spoken else "haiku"   # a spoken "think" question was already judged worth a deeper look
    return "haiku" if score == 1 else "sonnet"


def ask(model: str, prompt: str, system: str, tools: tuple[str, ...] = (), timeout: int = 60, guard: bool = True,
        generic: bool = False) -> dict:
    """One isolated Claude run. {ok, text | reason}. The prompt passes the privacy guard first (unless the caller already
    guarded every part of it)."""
    if afford(model, generic) != model:
        return {"ok": False, "reason": f"{model}: off, cooling down or over today's budget"}
    sent = prompt
    if guard:
        sent, _, blocked = fable.guard(prompt)
        if blocked:
            return {"ok": False, "reason": f"private: {blocked}"}
    fable.SANDBOX.mkdir(parents=True, exist_ok=True)
    empty_mcp = fable.SANDBOX / "no-mcp.json"
    empty_mcp.write_text('{"mcpServers": {}}')
    args = [fable.cli(), "-p", "--model", MODELS.get(model, model), "--output-format", "json",
            "--setting-sources", "", "--strict-mcp-config", "--mcp-config", str(empty_mcp),
            "--tools", ",".join(tools), "--disable-slash-commands", "--no-session-persistence",
            "--exclude-dynamic-system-prompt-sections", "--system-prompt", system]
    if tools:
        args += ["--allowedTools", ",".join(tools)]
    env = {k: v for k, v in os.environ.items() if not k.startswith("TUESDAY_")}
    started = time.time()
    try:
        r = subprocess.run(args, input=sent, capture_output=True, text=True, timeout=timeout, cwd=fable.SANDBOX, env=env)
        out = json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"{model} took longer than {timeout}s"}
    except (json.JSONDecodeError, OSError) as e:
        _trip(f"{model} failed: {e}")
        return {"ok": False, "reason": f"{model} failed"}
    _count(model)
    secs = round(time.time() - started, 1)
    _log(model=model, sent=sent, secs=secs, cost=out.get("total_cost_usd"))
    if out.get("is_error"):
        _trip(f"{model}: {str(out.get('result'))[:120]}")
        return {"ok": False, "reason": str(out.get("result"))[:200]}
    return {"ok": True, "text": (out.get("result") or "").strip(), "secs": secs}


SPOKEN = (" Your answer will be read aloud to the client in conversation: two or three short spoken sentences, plain words, "
          "no lists, no markdown, no links or citations, no preamble.")


def advise(request: str, model: str = "sonnet", web: bool = False, timeout: int = 120, spoken: bool = False) -> dict:
    """An expert draft for Tuesday's judge: Tuesday writes the minimal brief on-device, the guard scrubs it, the model
    answers knowing nothing about him. {ok, result | reason, brief}."""
    model = afford(model)
    if not model:
        return {"ok": False, "reason": "Claude is off, offline, cooling down or over today's budget"}
    brief = fable.brief_for(request)
    if brief.strip().upper().startswith("BLOCKED"):
        return {"ok": False, "reason": "needs private details", "brief": ""}
    res = ask(model, brief, ADVISOR + (SPOKEN if spoken else ""), tools=("WebSearch", "WebFetch") if web else (), timeout=timeout)
    return {"ok": res["ok"], "result": res.get("text", ""), "reason": res.get("reason", ""), "brief": brief, "model": model}


def _trip(why: str) -> None:
    global _down_until
    with _lock:
        _down_until = time.time() + COOLDOWN
    print(f"[claude] {why}; Tuesday carries on alone for {COOLDOWN // 60} min", flush=True)


def _log(**entry):
    with LOG.open("a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}, ensure_ascii=False) + "\n")
