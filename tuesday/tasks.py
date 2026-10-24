"""Follow-up tasks: things the owner asked Tuesday to do later, or that Tuesday promised. They persist where Tuesday
runs (data/tasks.json), appear in Tuesday Core's context until done, and can carry a due time (also set as a reminder)."""
import json
import threading
import time

from . import config

FILE = config.DATA / "tasks.json"
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return []


def _save(ts: list[dict]):
    FILE.write_text(json.dumps(ts, indent=1))


def add(text: str, due: str | None = None, notes: str | None = None) -> dict:
    with _lock:
        ts = _load()
        t = {"id": max([x["id"] for x in ts] or [0]) + 1, "text": text.strip(), "created": time.strftime("%Y-%m-%d %H:%M"),
             "due": due, "notes": notes, "done": None}
        ts.append(t)
        _save(ts)
    if due:  # a due time also becomes a spoken reminder
        from . import capabilities as cap
        try:
            cap.set_reminder(f"task: {text}", at=due)
        except Exception:
            pass
    return t


def open_tasks() -> list[dict]:
    return [t for t in _load() if not t["done"]]


def update(task_id: int, done: bool | None = None, notes: str | None = None) -> dict:
    with _lock:
        ts = _load()
        for t in ts:
            if t["id"] == int(task_id):
                if done:
                    t["done"] = time.strftime("%Y-%m-%d %H:%M")
                if notes:
                    t["notes"] = ((t.get("notes") or "") + "\n" + notes).strip()[-2000:]
                _save(ts)
                return t
    return {"error": f"no task {task_id}"}


def context() -> str:
    ts = open_tasks()
    if not ts:
        return ""
    return "## Open follow-ups\n" + "\n".join(f"- #{t['id']} {t['text']}" + (f" (due {t['due']})" if t.get("due") else "") +
                                              (f" — notes: {t['notes'][-200:]}" if t.get("notes") else "") for t in ts[-10:])
