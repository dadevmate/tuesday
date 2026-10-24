"""The owner's calendar, through macOS's own calendar store (EventKit): whatever accounts the Mac's Calendar app has
(Google, iCloud, Exchange) with their logins kept by macOS, read and written locally. Recurring events are expanded.

macOS only lets an app that says why it needs calendars ask for them, which a plain Python process can't. So the
EventKit work runs inside "Tuesday Calendar" (vendor/TuesdayCalendar.app), a tiny background applet that carries that
explanation and runs this module for one request: Tuesday writes the request to data/calendar/, opens the applet, and
reads the answer. Built by `./run.sh --calendar-setup`, which also asks for access (click Allow once).

Setup (once): add the account in System Settings -> Internet Accounts (Calendars on), then `./run.sh --calendar-setup`.
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta

from . import config

APP = config.ROOT / "vendor" / "TuesdayCalendar.app"
IO = config.DATA / "calendar"
INSIDE = os.environ.get("TUESDAY_CAL_HELPER") == "1"   # running inside the applet, with calendar permission
_one = threading.Lock()



def _ek():
    try:
        import EventKit
        import Foundation
    except ImportError as e:
        raise RuntimeError("the calendar needs pyobjc's EventKit: run ./run.sh --calendar-setup") from e
    return EventKit, Foundation


_store = {}


def store():
    """The event store, asking macOS for access the first time (a one-off Allow dialog)."""
    if "s" in _store:
        return _store["s"]
    EventKit, _ = _ek()
    s = EventKit.EKEventStore.alloc().init()
    status = EventKit.EKEventStore.authorizationStatusForEntityType_(EventKit.EKEntityTypeEvent)
    if status not in (3,):   # 3 = full access (macOS 14+) / authorized
        done, box = threading.Event(), {}

        def cb(granted, err):
            box["ok"] = bool(granted)
            done.set()
        if hasattr(s, "requestFullAccessToEventsWithCompletion_"):
            s.requestFullAccessToEventsWithCompletion_(cb)
        else:
            s.requestAccessToEntityType_completion_(EventKit.EKEntityTypeEvent, cb)
        done.wait(120)
        if not box.get("ok"):
            raise RuntimeError("no access to the calendar: allow it in System Settings -> Privacy & Security -> Calendars")
    _store["s"] = s
    return s


def _ns(dt: datetime):
    _, Foundation = _ek()
    return Foundation.NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def _py(nsdate) -> datetime:
    from . import capabilities as cap
    return datetime.fromtimestamp(nsdate.timeIntervalSince1970(), cap.TZ)


def _when(value: str) -> datetime:
    """ISO ('2026-10-14T15:00') or 'HH:MM' today (tomorrow if already past), in the owner's timezone."""
    from . import capabilities as cap
    now = datetime.now(cap.TZ)
    if len(value) <= 5 and ":" in value:
        h, m = map(int, value.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return t if t > now else t + timedelta(days=1)
    t = datetime.fromisoformat(value)
    return t if t.tzinfo else t.replace(tzinfo=cap.TZ)


def _events(days: float = 7, start: str | None = None) -> list[dict]:
    from . import capabilities as cap
    s = store()
    t0 = _when(start) if start else datetime.now(cap.TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    t1 = t0 + timedelta(days=max(0.1, min(float(days or 7), 62)))
    pred = s.predicateForEventsWithStartDate_endDate_calendars_(_ns(t0), _ns(t1), None)
    out = []
    for e in sorted(s.eventsMatchingPredicate_(pred) or [], key=lambda e: e.startDate().timeIntervalSince1970()):
        out.append({"title": str(e.title() or ""), "start": _py(e.startDate()).strftime("%a %d %b %H:%M"),
                    "end": _py(e.endDate()).strftime("%H:%M"), "all_day": bool(e.isAllDay()),
                    "location": str(e.location() or ""), "calendar": str(e.calendar().title() if e.calendar() else "")})
    return out[:80]


def _calendars() -> list[str]:
    EventKit, _ = _ek()
    return [str(c.title()) for c in store().calendarsForEntityType_(EventKit.EKEntityTypeEvent)]


def _add(title: str, start: str, end: str | None = None, minutes: float = 60, calendar: str | None = None,
          location: str | None = None, notes: str | None = None) -> dict:
    """Add an event to his calendar (no invitations are sent to anyone)."""
    EventKit, _ = _ek()
    s = store()
    t0 = _when(start)
    t1 = _when(end) if end else t0 + timedelta(minutes=float(minutes or 60))
    ev = EventKit.EKEvent.eventWithEventStore_(s)
    ev.setTitle_(title)
    ev.setStartDate_(_ns(t0))
    ev.setEndDate_(_ns(t1))
    cal = next((c for c in s.calendarsForEntityType_(EventKit.EKEntityTypeEvent) if calendar and str(c.title()).lower() == calendar.lower()), None)
    ev.setCalendar_(cal or s.defaultCalendarForNewEvents())
    if location:
        ev.setLocation_(location)
    if notes:
        ev.setNotes_(notes)
    ok, err = s.saveEvent_span_error_(ev, EventKit.EKSpanThisEvent, None)
    if not ok:
        raise RuntimeError(f"couldn't save the event: {err}")
    return {"added": title, "start": t0.strftime("%a %d %b %H:%M"), "end": t1.strftime("%H:%M"), "calendar": str(ev.calendar().title())}


# ---------------- through the applet ----------------
def _via_app(cmd: str, **kw):
    if INSIDE:
        return {"events": _events, "calendars": _calendars, "add": _add}[cmd](**kw)
    if not APP.exists():
        raise RuntimeError("the calendar isn't set up: run ./run.sh --calendar-setup")
    with _one:   # one request at a time through the applet
        IO.mkdir(parents=True, exist_ok=True)
        rid = uuid.uuid4().hex
        (IO / "request.json").write_text(json.dumps({"id": rid, "cmd": cmd, "kw": kw}))
        (IO / "response.json").unlink(missing_ok=True)
        for attempt in range(4):   # the applet may still be quitting from the last request (LaunchServices error -600)
            r = subprocess.run(["open", "-W", "-g", "-a", str(APP)], timeout=150, capture_output=True, text=True)
            if r.returncode == 0:
                break
            time.sleep(0.5 * (attempt + 1))
        else:
            raise RuntimeError(f"couldn't start the calendar helper: {r.stderr.strip()[:200]}")
        try:
            out = json.loads((IO / "response.json").read_text())
        except FileNotFoundError:
            raise RuntimeError("the calendar helper didn't answer") from None
    if out.get("id") != rid:
        raise RuntimeError("the calendar helper answered a different request")
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out["result"]


SYSTEM_CALENDARS = {"Birthdays", "Siri Suggestions", "Scheduled Reminders"}   # plus any "Holidays in …" calendar


def _mac_has_his() -> bool:
    try:
        return any(c not in SYSTEM_CALENDARS and not c.startswith("Holidays in ") for c in calendars())
    except Exception:
        return False


def _connector(task: str, writes: bool | str = False) -> str:
    from . import fable
    r = fable.connector_run("In my Google Calendar: " + task, allow_writes=writes)
    if not r["ok"]:
        raise RuntimeError(r["reason"])
    return r["result"]


def events(days: float = 7, start: str | None = None):
    if not _mac_has_his():
        from . import capabilities as cap
        t0 = _when(start) if start else datetime.now(cap.TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        t1 = t0 + timedelta(days=max(0.1, min(float(days or 7), 62)))
        return _connector(f"list every event from {t0:%a %d %b %Y %H:%M} to {t1:%a %d %b %Y %H:%M} ({cap.TZ.key}): time, title, location.")
    return _via_app("events", days=days, start=start)


def calendars() -> list[str]:
    return _via_app("calendars")


def add(title: str, start: str, end: str | None = None, minutes: float = 60, calendar: str | None = None,
        location: str | None = None, notes: str | None = None) -> dict:
    """Add an event to his calendar (no invitations are sent to anyone)."""
    if not _mac_has_his():
        t0 = _when(start)
        t1 = _when(end) if end else t0 + timedelta(minutes=float(minutes or 60))
        return _connector(f"add an event '{title}' from {t0:%a %d %b %Y %H:%M} to {t1:%H:%M}" + (f" at {location}" if location else "")
                          + (f", notes: {notes}" if notes else "") + ". Invite no one.", writes="Google_Calendar__create_event")
    return _via_app("add", title=title, start=start, end=end, minutes=minutes, calendar=calendar, location=location, notes=notes)


def _serve():
    """Inside the applet: answer the one waiting request."""
    req = json.loads((IO / "request.json").read_text())
    try:
        out = {"id": req["id"], "result": _via_app(req["cmd"], **req["kw"])}
    except Exception as e:
        out = {"id": req["id"], "error": str(e)}
    (IO / "response.json").write_text(json.dumps(out, default=str))


if __name__ == "__main__":
    if "--serve" in sys.argv:
        _serve()
    else:   # ./run.sh --calendar-setup: check it works
        print("Calendars:", ", ".join(calendars()) or "(none: add an account in System Settings -> Internet Accounts)")
        for e in events(2):
            print(f"  {e['start']}-{e['end']}  {e['title']}  [{e['calendar']}]")
