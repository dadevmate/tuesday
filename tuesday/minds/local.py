"""Tuesday's minds, served on hardware the owner controls: the shared-base minds by Tuesday Core (llama.cpp, see
tuesday/core.py; model names "core:<mind>" or "core:<mind>@<adapter.gguf>"), the others by Ollama.
Every model here is open-weight; nothing leaves the owner's machines."""
import json
import time
import re
import urllib.error
import urllib.request

from .. import config
from ..tools import Tool, run_tool, as_text


def _post(path, body, timeout=900):
    req = urllib.request.Request(config.OLLAMA_URL + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get(path, timeout=5):
    with urllib.request.urlopen(config.OLLAMA_URL + path, timeout=timeout) as r:
        return json.loads(r.read())


def installed_models() -> list[str]:
    try:
        return [m["name"] for m in _get("/api/tags").get("models", [])]
    except Exception:
        return []


def installed_names() -> set[str]:
    """Installed model names, with and without the ':latest' tag."""
    names = set(installed_models())
    return names | {n.removesuffix(":latest") for n in names}


def model_name() -> str | None:
    """Tuesday's own model for background work (memories, summaries, routing): on Tuesday Core when it's there, so a
    second copy of Tuesday never loads into Ollama next to it (16 GB holds one)."""
    from .. import roster
    main = roster.MAIN.model
    if main.startswith("core:"):
        return main
    names = installed_models()
    for want in (config.LOCAL_MODEL, f"{config.LOCAL_MODEL}:latest", config.LOCAL_BASE):
        if want in names:
            return want
    return None


def available() -> bool:
    return model_name() is not None




def _ollama_tool(t: Tool) -> dict:
    s = t.schema()
    return {"type": "function", "function": {"name": s["name"], "description": s["description"], "parameters": s["input_schema"]}}


def vision_available() -> bool:
    names = installed_models()
    return config.VISION_MODEL in names or f"{config.VISION_MODEL}:latest" in names


def _core(model: str) -> tuple[str, list[dict]]:
    """'core:<mind>[@adapter]' -> (mind, the per-request adapter list for that mind)."""
    from .. import core
    mind, _, path = model[5:].partition("@")
    ids = core.ensure({mind: path} if path else None)
    if ids is None:
        raise RuntimeError("Tuesday Core is unavailable")
    return mind, [{"id": i, "scale": 1.0 if m == mind else 0.0} for m, i in ids.items()]


def _core_post(body: dict, timeout=900) -> dict:
    """One request to Tuesday Core. A server error (e.g. the GPU ran out of memory once and the server is stuck in an
    error state) restarts the core and tries once more, so it heals itself instead of failing until someone notices."""
    from .. import core
    data = json.dumps(body).encode()
    for attempt in range(2):
        req = urllib.request.Request(core.URL + "/v1/chat/completions", data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt:
                raise
            print(f"[core] server error {e.code}: restarting Tuesday Core", flush=True)
            core.stop()
            if core.ensure() is None:   # same adapter files in the same order, so the request's adapter ids still hold
                raise


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    if "</think>" in text:   # a stray closing tag (the tuned adapters sometimes emit one): the answer is before it, and
        before, _, after = text.partition("</think>")   # what follows is the model rambling on (often old turns)
        text = before if before.strip() else after
    return text.replace("<think>", "").strip()


def _think_core(model: str, system: str, history: list[dict], usable: list[Tool], tools: dict[str, Tool], max_tokens: int | None) -> str:
    from .. import core
    mind, lora = _core(model)
    messages = [{"role": "system", "content": system}] + [{"role": m["role"], "content": m["content"] if isinstance(m["content"], str) else ""} for m in history]
    for _ in range(8):
        body = {"messages": messages, "temperature": 0.6, "lora": lora, "id_slot": core.slot(mind), "cache_prompt": True,
                **({"max_tokens": max_tokens} if max_tokens else {})}
        if usable:
            body["tools"] = [_ollama_tool(t) for t in usable]
        msg = _core_post(body)["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": calls} if calls else {})})
        if not calls:
            return _clean(msg.get("content"))
        for c in calls:
            fn = c.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            out = run_tool(tools, fn.get("name", ""), args if isinstance(args, dict) else {})
            messages.append({"role": "tool", "content": as_text(out), "tool_call_id": c.get("id", "")})
    return _clean(messages[-1].get("content")) or "Done."


def think(system: str, history: list[dict], tools: dict[str, Tool], use_tools: bool = True, model: str | None = None,
          max_tokens: int | None = None) -> str:
    model = model or model_name()
    if model and model.startswith("core:"):
        usable = [t for t in tools.values() if t.name != "robot_describe_view" or vision_available()] if use_tools else []
        try:
            return _with_core(lambda: _think_core(model, system, history, usable, tools, max_tokens))
        except (OSError, RuntimeError) as e:   # the core stayed down: a small mind on Ollama (still private)
            print(f"[core] {e}; falling back to Ollama", flush=True)
            model = _fallback(model)
    if not model:
        raise RuntimeError("No mind installed: run ./run.sh --minds to pull them (or ./run.sh --train-minds to fine-tune).")
    messages = [{"role": "system", "content": system}] + [{"role": m["role"], "content": m["content"] if isinstance(m["content"], str) else ""} for m in history]
    usable = [t for t in tools.values() if t.name != "robot_describe_view" or vision_available()]
    no_think = True  # hybrid-thinking models (Qwen3.5) answer directly: fast enough for conversation on a CPU
    for _ in range(8):
        body = {"model": model, "messages": messages, "stream": False, "keep_alive": config.env("TUESDAY_KEEP_ALIVE", "30m"), "options": {"temperature": 0.6, "num_ctx": 8192, **({"num_predict": max_tokens} if max_tokens else {})}}
        if no_think:
            body["think"] = False
        if use_tools and usable:
            body["tools"] = [_ollama_tool(t) for t in usable]
        try:
            r = _post("/api/chat", body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace") if e.code == 400 else ""
            if e.code == 400 and no_think and "think" in detail:   # model without a thinking switch
                no_think = False
                continue
            if use_tools and e.code == 400:   # model without tool support
                use_tools = False
                continue
            raise
        msg = r.get("message", {})
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content", ""), **({"tool_calls": calls} if calls else {})})
        if not calls:
            return (msg.get("content") or "").strip()
        for c in calls:
            fn = c.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            out = run_tool(tools, fn.get("name", ""), args)
            messages.append({"role": "tool", "content": as_text(out), "tool_name": fn.get("name", "")})
    return (messages[-1].get("content") or "Done.").strip()


QUICK_MAX_TOKENS = 700


def quick(prompt: str, system: str = "You are Tuesday. Be brief.", fmt: dict | None = None, model: str | None = None) -> str:
    """One-shot local completion (no tools), e.g. for routing and summaries. Costs nothing."""
    model = model or model_name()
    if not model:
        raise RuntimeError("local model unavailable")
    if model.startswith("core:"):
        try:
            return _with_core(lambda: _quick_core(model, prompt, system, fmt))
        except (OSError, RuntimeError) as e:
            print(f"[core] {e}; falling back to Ollama", flush=True)
            model = _fallback(model)
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "stream": False,
            "options": {"temperature": 0.2, "num_predict": QUICK_MAX_TOKENS}}
    if fmt:
        body["format"] = fmt
    return _post("/api/chat", body)["message"]["content"]


def quick_stream(prompt: str, system: str, model: str, on_text, prefill: str = "") -> str:
    """Like quick(), but streamed: on_text(text_so_far) is called as tokens arrive, so a caller can act on the first
    line before the rest is written (Tuesday starts speaking while it's still deciding what to do). Falls back to
    quick() when the core isn't in use."""
    if not model.startswith("core:"):
        out = quick(prompt, system=system, model=model)
        on_text(out)
        return out
    from .. import core
    mind, lora = _core(model)
    # prefill: the start of the answer, written for the model (e.g. "DO:"), so it can't choose another order
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
            + ([{"role": "assistant", "content": prefill}] if prefill else []), "temperature": 0.2,
            "lora": lora, "id_slot": core.slot("planner" if mind == "planner" else "misc"), "max_tokens": QUICK_MAX_TOKENS,
            "stream": True, "cache_prompt": True}
    req = urllib.request.Request(core.URL + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    text = ""
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            for line in r:
                line = line.decode(errors="replace").strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                try:
                    delta = json.loads(line[5:])["choices"][0].get("delta", {}).get("content") or ""
                except Exception:
                    continue
                if delta:
                    text += delta
                    if prefill and not text.lstrip().startswith(prefill.strip()[:3]):
                        text = prefill + text   # the server sends only the continuation
                    on_text(text)
    except (OSError, urllib.error.HTTPError) as e:
        if text:
            return _clean(text)
        print(f"[core] stream failed ({e}); one-shot instead", flush=True)
        text = quick(prompt, system=system, model=model)
        on_text(text)
    return _clean(text)


def _with_core(call, wait: float = 25):
    """A core call that survives the core restarting (it heals itself after a GPU error): wait for it to be ready and
    try once more, rather than loading another big model next to it."""
    from .. import core
    try:
        return call()
    except (OSError, RuntimeError) as e:
        print(f"[core] {e}; waiting for Tuesday Core", flush=True)
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(core.URL + "/health", timeout=3) as r:
                    if json.loads(r.read()).get("status") == "ok":
                        break
            except Exception:
                pass
            time.sleep(1)
        return call()


def _fallback(model: str) -> str:
    """The core is down for good: a small mind on Ollama. Never another 8B copy (Tuesday Core already holds one; two
    don't fit in 16 GB, and the GPU running out crashed the core again: a vicious circle)."""
    small = config.env("TUESDAY_FALLBACK_MODEL", "sunday")
    names = installed_names()
    return small if small in names else "ministral-3:3b" if "ministral-3:3b" in names else config.LOCAL_BASE


def _quick_core(model: str, prompt: str, system: str, fmt: dict | None) -> str:
    from .. import core
    mind, lora = _core(model)
    # Capped: a one-shot answer that runs away (it happened: 3,000+ tokens) would otherwise hold the slot for minutes.
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "temperature": 0.2,
            "lora": lora, "id_slot": core.slot("planner" if mind == "planner" else "misc"), "max_tokens": QUICK_MAX_TOKENS}
    if fmt:
        body["response_format"] = {"type": "json_schema", "json_schema": {"schema": fmt}}
    return _clean(_core_post(body)["choices"][0]["message"]["content"])
