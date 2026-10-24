"""Tuesday's private memory about its owner. Stays on this machine and is only shown to the local model."""
import json
import re
import time
from pathlib import Path

from . import config

PROFILE = config.DATA / "profile.md"
MEMORIES = config.DATA / "memories.jsonl"

_WORD = re.compile(r"[a-z0-9']+")
_STOP = set("the a an and or of to in on for is are was were be been it this that with as at by from i you me my your we our they them he she his her its what who how when where why do does did can could should would will not no yes".split())


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def profile() -> str:
    return PROFILE.read_text() if PROFILE.exists() else ""


def all_memories() -> list[dict]:
    if not MEMORIES.exists():
        return []
    out = []
    for line in MEMORIES.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _similar(a: str, b: str) -> float:
    ta, tb = _terms(a), _terms(b)
    return len(ta & tb) / max(1, len(ta | tb))


def remember(text: str, tags: list[str] | None = None, source: str = "conversation", dedupe: bool = True) -> dict:
    """Save a memory. A near-duplicate of an existing memory replaces it (newer information wins), unless dedupe=False
    (journal entries: each day is its own memory, however alike)."""
    m = {"ts": time.strftime("%Y-%m-%d %H:%M"), "text": text.strip(), "tags": tags or [], "source": source}
    if not m["text"]:
        return m
    existing = all_memories()
    keep = [e for e in existing if not dedupe or _similar(e["text"], m["text"]) < 0.6]
    m["replaced"] = len(existing) - len(keep)
    keep.append({k: v for k, v in m.items() if k != "replaced"})
    MEMORIES.write_text("".join(json.dumps(e) + "\n" for e in keep))
    return m


def forget(contains: str) -> int:
    keep, dropped = [], 0
    for m in all_memories():
        if contains.lower() in m["text"].lower():
            dropped += 1
        else:
            keep.append(m)
    MEMORIES.write_text("".join(json.dumps(m) + "\n" for m in keep))
    return dropped


def is_private(m: dict) -> bool:
    return "private" in (m.get("tags") or [])


# ---------------- retrieval: meaning (local embeddings) + keywords ----------------
EMBED_MODEL = config.env("TUESDAY_EMBED_MODEL", "nomic-embed-text")
EMBED_CACHE = config.DATA / "embeddings.json"
_vecs: dict[str, list[float]] | None = None


def _embed(texts: list[str], kind: str) -> list[list[float]] | None:
    """Embed on-device with Ollama; None if the embedding model isn't available (keywords still work)."""
    import urllib.request
    body = json.dumps({"model": EMBED_MODEL, "input": [f"{kind}: {t}" for t in texts]}).encode()
    try:
        req = urllib.request.Request(config.OLLAMA_URL + "/api/embed", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["embeddings"]
    except Exception:
        return None


def _doc_vectors(texts: list[str]) -> dict[str, list[float]] | None:
    """Cached document embeddings, keyed by a hash of the text; new texts are embedded in one batch."""
    import hashlib
    global _vecs
    if _vecs is None:
        try:
            _vecs = json.loads(EMBED_CACHE.read_text())
        except Exception:
            _vecs = {}
    key = lambda t: hashlib.sha1(t.encode()).hexdigest()
    missing = sorted({t for t in texts if key(t) not in _vecs})
    if missing:
        got = _embed(missing, "search_document")
        if got is None:
            return None
        _vecs.update({key(t): v for t, v in zip(missing, got)})
        live = {key(t) for t in texts}
        _vecs = {k: v for k, v in _vecs.items() if k in live} if len(_vecs) > 4 * max(1, len(live)) else _vecs
        EMBED_CACHE.write_text(json.dumps(_vecs))
    return {t: _vecs[key(t)] for t in texts}


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _rank(query: str, pool: list[dict], k: int) -> list[dict]:
    """Best matches by meaning plus keyword overlap; keywords alone if embeddings are unavailable."""
    q = _terms(query)
    docs = [m["text"] + " " + " ".join(m.get("tags", [])) for m in pool]
    kw = [len(q & _terms(d)) for d in docs]
    qv = _embed([query], "search_query")
    dv = _doc_vectors(docs) if qv else None
    if not dv:
        order = sorted(range(len(pool)), key=lambda i: (-kw[i], -i))
        return [pool[i] for i in order[:k] if kw[i]]
    scores = [_cos(qv[0], dv[d]) + 0.05 * min(kw[i], 4) for i, d in enumerate(docs)]
    order = sorted(range(len(pool)), key=lambda i: -scores[i])
    return [pool[i] for i in order[:k] if scores[i] >= 0.5 or kw[i] >= 2]


def recall(query: str, k: int = 8, include_private: bool = True) -> list[dict]:
    return _rank(query, [m for m in all_memories() if include_private or not is_private(m)], k)


# Profile sections always in context; the rest of the profile is recalled per question like memories.
ALWAYS = ("identity", f"how {config.OWNER_NAME.lower()} wants to be spoken to", "how he wants to be spoken to", "private")


def _profile_sections() -> list[tuple[str, list[str]]]:
    out, title = [], None
    for line in profile().splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            out.append((title, []))
        elif title and line.strip().startswith(("- ", "1", "2", "3", "4", "5", "6")):
            out[-1][1].append(line.strip().lstrip("-0123456789. ").strip())
    return out


def profile_facts() -> list[dict]:
    """Profile bullets outside the always-on sections, shaped like memories so recall can rank them."""
    return [{"ts": "profile", "text": f, "tags": [t.lower()]} for t, fs in _profile_sections()
            if not t.lower().startswith(ALWAYS) for f in fs]


def context_parts(query: str, include_private: bool = True, k: int = 10) -> tuple[str, str]:
    """(core, recalled): the always-on core of the profile (stable, so the model's prompt cache can reuse it) and the
    facts and memories relevant to this request (changes per message)."""
    core = [f"{t}:\n" + "\n".join(f"- {f}" for f in fs) for t, fs in _profile_sections() if t.lower().startswith(ALWAYS)]
    hits = _rank(query, profile_facts() + [m for m in all_memories() if include_private or not is_private(m)], k)
    return ("## Your owner\n" + "\n".join(core) if core else "",
            "## Relevant facts and memories\n" + "\n".join(f"- {m['text']}" for m in hits) if hits else "")


def context_for(query: str, include_private: bool = True, k: int = 10) -> str:
    """Personal context for the minds: the always-on core of the profile plus the facts and memories relevant to this
    request. Private memories are left out when someone else may be listening."""
    return "\n\n".join(x for x in context_parts(query, include_private, k) if x)


# Words that suggest a request touches the owner's personal information.
PERSONAL_HINTS = re.compile(
    r"\b(my|mine|about me|remember|forget|password|address|phone|email|bank|health|doctor|family|mom|dad|"
    r"girlfriend|boyfriend|wife|husband|birthday|schedule|calendar|private|personal|secret|salary|money|"
    r"who am i|about me|my name|where do i|preference|prefer)\b",
    re.I,
)


def is_personal(text: str) -> bool:
    return bool(PERSONAL_HINTS.search(text))


# ---------------- learning from conversations ----------------
EXPLICIT = re.compile(r"^\s*(please\s+)?remember(\s+that)?\s+(.+)$", re.I | re.S)


def explicit_memory(text: str) -> str | None:
    m = EXPLICIT.match(text)
    return m.group(3).strip().rstrip(".") if m else None


# Things the owner shares on a personal level stay private: retrieval-only, never trained, never spoken to others.
def private_names() -> set[str]:
    """Names that appear in private memories (people he dates, confides in, …): never said to anyone but him."""
    from pathlib import Path
    words = Path("/usr/share/dict/words")
    english = {w.strip() for w in words.read_text().splitlines() if w[:1].islower()} if words.exists() else set()
    mems = all_memories()
    corpus = "\n".join(m["text"] for m in mems)
    ordinary = english | set(re.findall(r"\b[a-z]{3,}\b", corpus)) | {config.OWNER_NAME.lower(), "private", "never", "tuesday", "sir", "martian"}
    plain = lambda w: w.lower() in ordinary or (w.endswith("s") and w[:-1].lower() in ordinary)
    return {w for m in mems if is_private(m) for w in re.findall(r"\b[A-Z][a-z]{2,}\b", m["text"]) if not plain(w)}


PRIVATE_HINTS = re.compile(
    r"\b(feel|feeling|felt|sad|depressed|lonely|anxious|anxiety|stressed|upset|hurt|cry|cried|heartbroken|rejected|"
    r"crush|date|dating|girlfriend|boyfriend|secret|don'?t tell|do not tell|between us|nobody knows|mental health|therapy)\b",
    re.I,
)

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"memories": {"type": "array", "items": {"type": "object", "properties": {
        "text": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "kind": {"type": "string", "enum": ["about_owner", "learned"]}},
        "required": ["text", "tags", "kind"]}}},
    "required": ["memories"],
}

EXTRACT_PROMPT = """Today is {today}. Extract durable memories from this exchange between the owner ({owner}) and Tuesday.
- "about_owner": facts the OWNER states about himself: plans, decisions, preferences, people, events, holdings, schedules, projects, corrections.
- "learned": substantial new knowledge contained in TUESDAY's reply (research findings, facts about the robot or the world). Not promises or acknowledgements.
Rules: only what is explicitly said below; never infer or add outside facts. One self-contained sentence each. Convert relative dates ("next Friday") to absolute dates.
Skip greetings, questions, small talk and acknowledgements. Return {{"memories": []}} if nothing qualifies.

OWNER: {user}
TUESDAY: {reply}"""


_DATEISH = re.compile(r"^(\d+(st|nd|rd|th)?|jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sep(tember)?|oct(ober)?|nov(ember)?|dec(ember)?|monday|tuesday|wednesday|thursday|friday|saturday|sunday)$")


def _grounded(text: str, source: str, need: float = 0.6) -> bool:
    t = {w for w in _terms(text) if not _DATEISH.match(w)} - {config.OWNER_NAME.lower(), "owner", "tuesday"}
    return bool(t) and len(t & _terms(source)) / len(t) >= need


def learn_from_exchange(user: str, reply: str, quick) -> list[dict]:
    """Ask the local model which durable facts this exchange contains, and store them. Runs on-device only."""
    private = bool(PRIVATE_HINTS.search(user))
    fact = explicit_memory(user)
    if fact:
        return [remember(fact, ["explicit"] + (["private"] if private else []), source="owner said")]
    if len(user) < 12:
        return []
    try:
        raw = quick(EXTRACT_PROMPT.format(today=time.strftime("%A %Y-%m-%d"), owner=config.OWNER_NAME, user=user[:3000], reply=reply[:3000]),
                    system="You extract memories. Output JSON only.", fmt=EXTRACT_SCHEMA)
        items = json.loads(raw).get("memories", [])
    except Exception:
        return []
    existing = all_memories()
    saved = []
    for it in items[:5]:
        text = str(it.get("text", "")).strip()
        if len(text) < 8:
            continue
        # Decide the kind by where the words came from (small models mislabel it).
        if _grounded(text, user):
            kind = "about_owner"
        elif len(reply) >= 200 and _grounded(text, reply):
            kind = "learned"
        else:
            continue  # not actually said, or only an acknowledgement
        if any(_similar(text, e["text"]) >= 0.6 for e in existing) and not re.search(r"\b(now|no longer|instead|changed|sold|moved|switched)\b", user, re.I):
            continue  # already known, and nothing in the message suggests it changed
        tags = [str(t)[:30] for t in (it.get("tags") or [])][:4] + [kind] + (["private"] if private and kind == "about_owner" else [])
        if kind == "about_owner" and not re.match(rf"({re.escape(config.OWNER_NAME)}|the owner)\b", text, re.I):
            text = f"{config.OWNER_NAME}: {text[0].lower() + text[1:]}"
        text = f"{text.rstrip('.')} (noted {time.strftime('%Y-%m-%d')})."
        saved.append(remember(text, tags, source="learned from conversation"))
    return saved
