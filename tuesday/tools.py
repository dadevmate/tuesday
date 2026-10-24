"""Everything Tuesday can do: robot body, everyday abilities, the web browser, and its private memory.
Tools marked personal=True are never offered to guests."""
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from . import agenda, browser as web, capabilities as cap, config, experiences, mail, memory, privacy, routines, runner, tasks, watches


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    fn: Callable
    required: list = field(default_factory=list)
    personal: bool = False

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {"type": "object", "properties": self.params, "required": self.required, "additionalProperties": False},
        }


def build_tools(body) -> dict[str, Tool]:
    S, N, I, B = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
    OFF = "Incognito is on: camera and data collection are off until the owner says 'unincognito'."

    def watched(fn):  # camera and data-collecting tools go dark while incognito
        return lambda a: OFF if privacy.incognito() else fn(a)

    t = [
        # ----- robot -----
        Tool("robot_status", "The robot body's status: battery, charger, pose, sensors.", {}, lambda a: body.status()),
        Tool("robot_say", "Speak out loud through the robot.", {"text": S}, lambda a: body.say(a["text"]), ["text"]),
        Tool("robot_drive", "Drive straight (negative = backwards), max 500 mm per call.", {"distance_mm": N, "speed_mmps": N}, lambda a: body.drive(a["distance_mm"], a.get("speed_mmps", 100)), ["distance_mm"]),
        Tool("robot_turn", "Turn in place by degrees (positive = left).", {"degrees": N}, lambda a: body.turn(a["degrees"]), ["degrees"]),
        Tool("robot_head", "Tilt the head (-22 to 45 degrees).", {"degrees": N}, lambda a: body.head(a["degrees"]), ["degrees"]),
        Tool("robot_lift", "Move the lift (0 = down, 1 = up).", {"height": N}, lambda a: body.lift(a["height"]), ["height"]),
        Tool("robot_faces", "Which faces the robot can see right now (named if enrolled).", {}, watched(lambda a: body.faces())),
        Tool("robot_known_faces", "Names of people the robot has learned to recognise.", {}, lambda a: body.known_faces()),
        Tool("robot_learn_face", "Teach the robot to recognise the person in front of it.", {"name": S}, watched(lambda a: _learned_face(body, a["name"])), ["name"]),
        Tool("robot_animate", "Play a Vector animation trigger, e.g. GreetAfterLongTime, CubePounceWinSession, LookAtUserEndearingly.", {"trigger": S}, lambda a: body.animate(a["trigger"]), ["trigger"]),
        Tool("robot_charger", "Drive onto (on=true) or off (on=false) the charger.", {"on": B}, lambda a: body.to_charger() if a["on"] else body.off_charger(), ["on"]),
        Tool("robot_map_room", "Explore the room slowly and build a map from the robot's navigation sensors.", {}, watched(lambda a: _mapped(body.scan_room()))),
        # ----- everyday abilities (work without the cloud) -----
        Tool("search_web", "Search the internet (private SearXNG on this Mac, else DuckDuckGo). Returns titles, URLs and snippets.", {"query": S, "max_results": I}, lambda a: cap.search_web(a["query"], a.get("max_results", 6)), ["query"]),
        Tool("fetch_page", "Read the text of a web page.", {"url": S}, lambda a: cap.fetch_page(a["url"]), ["url"]),
        Tool("weather", f"Current weather and 3-day forecast for a place (default {config.CITY}).", {"place": S}, lambda a: cap.weather(a.get("place") or "Singapore")),
        Tool("current_time", "The current date and time in the owner's timezone.", {}, lambda a: cap.now()),
        Tool("calculate", "Evaluate a math expression exactly (e.g. '1.29*450', 'sqrt(2)*pi').", {"expression": S}, lambda a: cap.calculate(a["expression"]), ["expression"]),
        Tool("set_reminder", "Set a reminder that Tuesday will say out loud. Give in_minutes, or at ('HH:MM' or ISO datetime).", {"text": S, "in_minutes": N, "at": S}, lambda a: cap.set_reminder(a["text"], a.get("in_minutes"), a.get("at")), ["text"]),
        Tool("list_reminders", "List pending reminders.", {}, lambda a: cap.list_reminders()),
        Tool("cancel_reminder", "Cancel a reminder by id.", {"reminder_id": I}, lambda a: cap.cancel_reminder(a["reminder_id"]), ["reminder_id"]),
        # ----- web browser: do things online (risky clicks and submits need the owner's yes) -----
        Tool("browse_open", "Open a web page in Tuesday's browser. Returns its text and numbered clickable/typeable elements.", {"url": S}, lambda a: web.browser().open(a["url"]), ["url"], personal=True),
        Tool("browse_read", "Read the current page again: text and numbered elements.", {}, lambda a: web.browser().read(), personal=True),
        Tool("browse_click", "Click an element on the current page by its number or visible text. Buying, sending, posting, booking, deleting or signing up needs confirmed=true, which you may set only after the owner explicitly said yes.",
             {"target": S, "confirmed": B}, lambda a: web.browser().click(a["target"], a.get("confirmed", False)), ["target"], personal=True),
        Tool("browse_type", "Type into a field (by number or label) on the current page; submit=true presses Enter, which needs confirmed=true after the owner's yes.",
             {"target": S, "text": S, "submit": B, "confirmed": B}, lambda a: web.browser().type(a["target"], a["text"], a.get("submit", False), a.get("confirmed", False)), ["target", "text"], personal=True),
        Tool("robot_describe_view", "Look through the robot's camera and describe it with the local vision model (optionally answer a question about it).", {"question": S}, watched(lambda a: cap.describe_image(body.look(), a.get("question") or "Describe what you see, briefly and concretely."))),
        Tool("robot_recent_events", "What the robot experienced recently: people it saw and when, visitors while the owner was away, faces learned, rooms mapped. Optional kind: person_seen, visitor, owner_seen, face_learned, room_mapped.",
             {"kind": S, "limit": I}, lambda a: experiences.recent(a.get("limit") or 20, a.get("kind")), personal=True),
        Tool("task_add", "Track a follow-up: something the owner asked you to do later, or that you promised. Optional due ('HH:MM' or ISO) also sets a reminder.",
             {"text": S, "due": S, "notes": S}, lambda a: tasks.add(a["text"], a.get("due"), a.get("notes")), ["text"], personal=True),
        Tool("task_list", "Open follow-up tasks.", {}, lambda a: tasks.open_tasks(), personal=True),
        Tool("task_update", "Add progress notes to a follow-up task, or mark it done.", {"task_id": I, "done": B, "notes": S},
             lambda a: tasks.update(a["task_id"], a.get("done"), a.get("notes")), ["task_id"], personal=True),
        Tool("list_routines", "Tuesday's proactive routines (briefings, check-in, nudges) and quiet hours.", {}, lambda a: routines.list_routines(), personal=True),
        Tool("set_routine", "Change a routine: time 'HH:MM', days ('daily', 'mon-fri', 'sat,sun'), or enabled true/false. Use routine_id 'quiet' with at='22:00-06:00' for quiet hours.",
             {"routine_id": S, "at": S, "days": S, "enabled": B}, lambda a: routines.set_routine(a["routine_id"], a.get("at"), a.get("days"), a.get("enabled")), ["routine_id"], personal=True),
        # ----- long tasks and watches (tuesday/runner.py, tuesday/watches.py) -----
        Tool("job_start", "Start a multi-step task that runs in the background (plan, act, check) and reports back when done. Optional when: 'tonight', 'at 3am', or ISO time; default now.",
             {"goal": S, "when": S}, lambda a: runner.start(a["goal"], runner.when_from(a["when"]) if a.get("when") and not a["when"][:4].isdigit() else a.get("when")), ["goal"], personal=True),
        Tool("job_list", "Background jobs that are queued, running or waiting for the owner.", {}, lambda a: runner.list_jobs(), personal=True),
        Tool("job_cancel", "Cancel a background job by id.", {"job_id": I}, lambda a: runner.cancel(a["job_id"]), ["job_id"], personal=True),
        Tool("watch_add", "Keep an eye on a web page and tell the owner when something happens (a price drops below X, an item is back in stock, a page changes). every_minutes default 60; repeat=true keeps watching after the first alert.",
             {"url": S, "what": S, "every_minutes": N, "repeat": B}, lambda a: watches.add(a["url"], a["what"], a.get("every_minutes", 60), a.get("repeat", False)), ["url", "what"], personal=True),
        Tool("watch_list", "Pages Tuesday is watching for the owner.", {}, lambda a: watches.list_watches(), personal=True),
        Tool("watch_remove", "Stop watching a page, by watch id.", {"watch_id": I}, lambda a: watches.remove(a["watch_id"]), ["watch_id"], personal=True),
        # ----- email and calendar (on this Mac; sending needs the owner's yes) -----
        Tool("email_list", "List or search the owner's emails, newest first: from, subject, date, unread, snippet, id. query uses Gmail search syntax (from:, subject:, newer_than:2d).",
             {"query": S, "unread_only": B, "limit": I}, lambda a: mail.search(a.get("query", ""), a.get("unread_only", False), a.get("limit", 10)), personal=True),
        Tool("email_read", "Read one email in full by its id (from email_list).", {"email_id": S}, lambda a: mail.read(a["email_id"]), ["email_id"], personal=True),
        Tool("email_draft", "Save a draft email in the owner's mailbox for him to review (nothing is sent). reply_to_id makes it a reply.",
             {"to": S, "subject": S, "body": S, "reply_to_id": S}, lambda a: mail.draft(a.get("to", ""), a.get("subject", ""), a["body"], a.get("reply_to_id")), ["body"], personal=True),
        Tool("email_send", "Send an email. Needs confirmed=true, which you may set only after the owner explicitly said yes to this exact email.",
             {"to": S, "subject": S, "body": S, "reply_to_id": S, "confirmed": B},
             lambda a: mail.send(a.get("to", ""), a.get("subject", ""), a["body"], a.get("reply_to_id"), a.get("confirmed", False)), ["body"], personal=True),
        Tool("calendar_events", "The owner's calendar events: the next `days` days (default 7) from `start` (ISO date, default today).",
             {"days": N, "start": S}, lambda a: agenda.events(a.get("days", 7), a.get("start")), personal=True),
        Tool("calendar_add", "Add an event to the owner's calendar (no invitations are sent). start: ISO datetime or 'HH:MM'; end or minutes (default 60).",
             {"title": S, "start": S, "end": S, "minutes": N, "calendar": S, "location": S, "notes": S},
             lambda a: agenda.add(a["title"], a["start"], a.get("end"), a.get("minutes", 60), a.get("calendar"), a.get("location"), a.get("notes")), ["title", "start"], personal=True),
        # ----- the owner's connected apps, through Fable (Gmail, Drive, IBKR, Stocktwits, Composio) -----
        Tool("connected_apps", "Do something in the owner's connected apps through Fable: Gmail (search, read, draft), Google Drive (find, read files), "
             "Interactive Brokers (portfolio, positions, prices, alerts), Stocktwits (sentiment, trending), Composio (other apps, e.g. Google Calendar). "
             "Write the task plainly with every detail it needs. Slow (about a minute). Sending, trading, deleting or sharing needs confirmed=true after his yes.",
             {"task": S, "confirmed": B}, _connected_apps, ["task"], personal=True),
        # ----- private memory (local model only) -----
        Tool("memory_remember", "Save a fact about your owner for the future.", {"text": S, "tags": {"type": "array", "items": S}}, watched(lambda a: memory.remember(a["text"], a.get("tags"))), ["text"], personal=True),
        Tool("memory_recall", "Search what you remember about your owner.", {"query": S}, lambda a: memory.recall(a["query"]), ["query"], personal=True),
        Tool("memory_forget", "Forget memories containing some text.", {"contains": S}, lambda a: {"forgotten": memory.forget(a["contains"])}, ["contains"], personal=True),
    ]
    return {x.name: x for x in t}


def _connected_apps(a: dict):
    from . import fable
    yes = bool(a.get("confirmed") and web.YES.search(web.owner_said.get("text", "")))
    task = a["task"] + (f"\n\nThe owner approved exactly this: {a['approved']}" if yes and a.get("approved") else "")
    r = fable.connector_run(task, allow_writes=yes)
    if not r["ok"]:
        return {"error": r["reason"]}
    res = r["result"]
    if res.startswith("NEEDS_APPROVAL") and not yes:
        return {"needs_confirmation": True, "plan": res.split(":", 1)[-1].strip(), "say": "Tell him exactly what will happen and ask for his yes."}
    if yes:
        web.owner_said["text"] = ""   # one yes, one action
    return res


def _learned_face(body, name):
    out = body.enroll_face(name)
    experiences.face_learned(name)
    return out


def _mapped(result):
    if isinstance(result, dict) and result.get("ok", True):
        experiences.room_mapped(str(result.get("summary", ""))[:200])
    return result


# The last action that stopped for the owner's yes (a risky click, a form submit, an email). When his next message is
# a yes, the agent carries it out in code: a one-word "yes" never depends on a model remembering to call the tool again.
PENDING: dict = {}


def run_tool(tools: dict[str, Tool], name: str, args: dict):
    tool = tools.get(name)
    if not tool:
        return {"error": f"unknown tool {name}"}
    try:
        out = tool.fn(args or {})
    except Exception as e:  # tool errors go back to the model, not up the stack
        return {"error": str(e)}
    if isinstance(out, dict) and out.get("needs_confirmation"):
        PENDING.clear()
        PENDING.update(tool=name, args=dict(args or {}), ts=time.time(), what=out)
    return out


def as_text(result) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str)[:20000]
