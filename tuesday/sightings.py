"""What the robot has seen, as short text notes ("14:05, bedroom: keys and phone on the desk; he was typing"), so it
can answer "where did I leave my keys?" or "what was I doing earlier?". Text only, never photos; kept 30 days, on the
Mac (data/sightings.jsonl); nothing while incognito. Answers come only to him (see agent / intent: face or voice)."""
import json
import re
import time
from datetime import datetime

from . import config, privacy

LOG = config.DATA / "sightings.jsonl"
KEEP_DAYS = 30


def note(text: str, room: str = "", things: str = "", doing: str = "", source: str = "glance"):
    if privacy.incognito() or not (text or things):
        return
    with LOG.open("a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M"), "room": room, "things": things, "doing": doing,
                            "text": text[:300], "source": source}, ensure_ascii=False) + "\n")
    _prune()


def _prune():
    if not LOG.exists() or LOG.stat().st_size < 200_000:
        return
    cut = time.strftime("%Y-%m-%d", time.localtime(time.time() - KEEP_DAYS * 86400))
    rows = [l for l in LOG.read_text().splitlines() if l[9:19] >= cut]
    LOG.write_text("\n".join(rows) + "\n")


def _rows() -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for l in LOG.read_text().splitlines():
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out


_STOP = set("where did i leave my the a an have you seen is are was were what when who earlier today yesterday last "
            "put find lost any did do my me of in on at it to".split())


def search(question: str, limit: int = 6) -> list[dict]:
    """Notes that mention what he's asking about (newest first); the most recent notes if it's a general question."""
    words = {w.rstrip("s") for w in re.findall(r"[a-z]+", question.lower()) if w not in _STOP and len(w) > 2}
    rows = _rows()
    if not words:
        return rows[-limit:][::-1]
    hits = [r for r in rows if words & {w.rstrip("s") for w in re.findall(r"[a-z]+", (r["things"] + " " + r["text"] + " "
                                                                                       + r["doing"] + " " + r["room"]).lower())}]
    return hits[-limit:][::-1]


def answer(question: str) -> str:
    """One natural sentence from the notes (Tuesday Core, on the Mac); honest when it hasn't seen it."""
    from .minds import local
    hits = search(question)
    if not hits:
        return "I haven't seen that, Sir. I only notice things when I'm looking around."
    notes = "\n".join(f"- {_when(r['ts'])}, {r['room'] or 'room unknown'}: {r['things'] or r['text']}"
                      + (f" (you were {r['doing']})" if r.get("doing") else "") for r in hits)
    try:
        out = local.quick(f"Your notes of what you saw:\n{notes}\n\nHe asks: {question}",
                          system="You are Tuesday, his home robot. Answer from the notes only, in one short spoken "
                                 "sentence, saying when you saw it. If the notes don't say, say you haven't seen it. "
                                 "Call him Sir.").strip()
    except Exception:
        out = ""
    return out or f"Last I saw: {hits[0]['things'] or hits[0]['text']}, {_when(hits[0]['ts'])}."


def _when(ts: str) -> str:
    try:
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    except ValueError:
        return ts
    days = (datetime.now().date() - t.date()).days
    return f"today at {t:%H:%M}" if days == 0 else f"yesterday at {t:%H:%M}" if days == 1 else f"{t:%A} at {t:%H:%M}"
