"""Understanding what he means, not matching phrases: for anything said to the robot, Tuesday's own model decides
what to say, what the robot should do, and whether the answer needs more (thinking, research, his apps, a job).

One short call to Tuesday Core returns three lines, which code then carries out:
  SAY:  what Tuesday says now (one sentence)
  DO:   robot/system actions, e.g. "charger" or "turn 180; head 30" (or none)
  NEED: none | think | research | apps | job
"I'm going to sleep" -> SAY good night, DO sleep. "What do you see?" -> DO look. "Should I take the job?" ->
SAY "one moment", NEED think. Facts the model could get wrong (time, date, battery, what the camera sees) are filled
in by code, never by the model. Unknown or unsafe actions are dropped; movement is capped.
"""
import re
import time
from datetime import datetime

from . import capabilities as cap, config, memory, privacy

ACTIONS = ("wake", "recall", "come", "sleep", "charger", "leave_charger", "turn", "drive", "head", "lift", "follow", "stop", "look", "time", "date", "battery", "weather",
           "remind", "remember", "incognito_on", "incognito_off", "animate")
MOVES = ("wake", "come", "sleep", "charger", "leave_charger", "turn", "drive", "head", "lift", "follow")
HOLD = {"think": ("Let me think that through, Sir.", "Good question, Sir. Give me a moment."),
        "research": ("Let me check, Sir.", "One moment, Sir, I'll look that up."),
        "apps": ("Checking now, Sir.", "One moment, Sir."),
        "job": ("On it, Sir. I'll let you know when it's done.", "Leave it with me, Sir.")}
HOLD_AFTER_FILLER = {"think": "Let me think it through properly.", "research": "Let me check that.", "apps": "Checking now.",
                     "job": "Leave it with me, Sir."}
STILL = ("Still on it, Sir. Nearly there.", "Taking a little longer than I'd like, Sir. Bear with me.")
_FILLERS = {
    "question": ("Hmm, good question.", "Let me see.", "Ooh, hold on.", "Right, one sec.", "Hmm, let me think."),
    "request": ("Sure, one sec.", "On it.", "Alright.", "Yep, give me a second."),
    "remark": ("Mm-hm.", "Oh?", "Hm.", "Right."),
}
_last_filler = [""]


def filler(text: str, history: list[dict]) -> str:
    """Something natural to say at once while Tuesday works out the real answer, in keeping with what he said: a
    question gets "hmm, good question", a request "sure, one sec", a remark "mm-hm". Never the same one twice running."""
    import random
    t = text.strip().lower()
    kind = ("question" if t.endswith("?") or re.match(r"(what|who|why|how|when|where|which|is|are|do|does|did|can|could|"
                                                       r"should|would|will|have|has)\b", t)
            else "request" if re.match(r"(please|can you|could you|go|come|turn|move|tell|show|find|check|look|set|remind|play)\b", t)
            else "remark")
    options = [f for f in _FILLERS[kind] if f != _last_filler[0]]
    _last_filler[0] = random.choice(options)
    return _last_filler[0]


FACTS = ("recall", "look", "time", "date", "battery", "weather")
ANIMATIONS = {"happy": "DanceBeatGetIn", "dance": "DanceBeatGetIn", "excited": "CubePounceWinSession", "yes": "VC_Affirmative",
              "no": "VC_Negative", "greet": "GreetAfterLongTime", "love": "LookAtUserEndearingly", "sad": "FrustratedByFailureMajor",
              "celebrate": "CubePounceWinSession", "fistbump": "FistBumpSuccess"}

SYSTEM = """You are Tuesday, {owner}'s personal AI, living in his Vector robot (a small robot with wheels, a head that tilts,
a lift, a camera and a speaker; it charges on a dock). Decide what to say and what the robot should do.
Answer in exactly three lines, in this order, nothing else (DO and NEED first: they're short; then what you say):
SAY: what you say out loud now, like a real friend in the room: answer what he actually said, directly and concretely
  (a real opinion with a reason, a real fact, a real question back). Open with a short first sentence (two to six
  words: the answer or your reaction), then at most one more sentence. Warm, a little witty; call him Sir. No flattery,
  no empty hype, and never make up things about him or his day.
DO: actions separated by ';', or none. Only what he asked for or clearly implied. Available:
  come (find him and come over to him, stopping just in front: "come here", "come to me", "over here")
  wake (wake up from sleep: "wake up", "rise and shine", "you up?", or good morning while you're asleep)
  sleep (back on the dock and actually go to sleep: for good night, bedtime, "go to sleep", "rest now")
  charger (just drive onto the dock, staying awake) · leave_charger · turn <degrees, + left, - right, 180 = around> · drive <mm, + forward, - back, max 500>
  head <degrees, -22 down to 45 up> · lift <0 down .. 1 up> · follow (follow him) · stop (stop moving/following)
  recall <what> (what you saw earlier: where he left something, what he was doing, who was here)
  look (camera: see and describe now) · time · date · battery · weather (code fills in the exact, live values)
  remind <minutes> <text> · remember <a fact about him worth keeping> · incognito_on · incognito_off
  animate <happy|dance|excited|yes|no|greet|love|sad|celebrate|fistbump>
NEED: none if SAY fully answers; think = a decision, advice or anything needing careful thought; research = news, facts
  from the web, anything current; apps = his email, calendar, files or portfolio; job = several steps to do in the background.
  NEED think: SAY is your honest quick take right now (a real answer in a sentence or two, like a friend would give
  on the spot); a more considered answer follows a little later. NEED research, apps or job: SAY is only a brief
  "let me check" line; never invent facts, prices, news or what's in his apps.
Rules: you can't see unless you use look: never say what he's doing, wearing or holding, where he is, or what a room
looks like without it. Never state the time, date, battery or weather yourself either: use the action. If what he said
doesn't make sense (the microphone may have misheard), say so briefly and ask him to say it again; never guess a
meaning. When he introduces someone ("this is my sister"), say hello to them warmly. Never move, follow or
use the lift unless he asks you to or it obviously follows from what he says (him going to bed, or telling you to sleep
= sleep; him arriving or saying hello is a greeting, not a reason to dock). Talking
about feelings is a conversation, not a command: just answer warmly (DO: none). Anything you don't actually know
(news, prices, facts you're unsure of) is NEED research: never guess.

Examples:
He says: tuesday that's it for today, I'm gonna go sleep
DO: sleep
NEED: none
SAY: Good night, Sir. Sleep well.
He says: wake up
DO: wake
NEED: none
SAY: I'm up, Sir. What's happening?
He says: can you come over here
DO: come
NEED: none
SAY: On my way, Sir.
He says: how's it looking over there
DO: look
NEED: none
SAY: Let me have a look, Sir.
He says: give me a twirl
DO: turn 360; animate happy
NEED: none
SAY: Watch this, Sir.
He says: what's on my calendar tomorrow
DO: none
NEED: apps
SAY: Checking your calendar now, Sir.
He says: should I raise my app's price
DO: none
NEED: think
SAY: My gut says not yet, Sir: grow the users first, then raise it. Let me think it through properly.
He says: what's the latest on Starship
DO: none
NEED: research
SAY: Let me check the latest, Sir.
He says: ugh today was rough
DO: none
NEED: none
SAY: I'm sorry, Sir. Want to tell me about it, or shall I just keep you company?
He says: will it rain tomorrow
DO: weather
NEED: none
SAY: Let me check the forecast, Sir.
He says: hey I'm back from the gym
DO: animate greet
NEED: none
SAY: Welcome back, Sir. Good session?
He says: hello tuesday
DO: none
NEED: none
SAY: Hey, Sir. What's on your mind?
He says: what do you think about Apple
DO: none
NEED: none
SAY: Love the hardware, Sir. They're behind on AI, though; the M-series chips are the real win.
He says: what did you mean by that
DO: none
NEED: none
SAY: Just that their chips are ahead, Sir, while their AI features still lag the others.
He says: can you hear me
DO: none
NEED: none
SAY: Loud and clear, Sir.
He says: I'm gonna carry you to the kitchen, ready?
DO: none
NEED: none
SAY: Ready! Mind the stairs.
He says: it is so problem
DO: none
NEED: none
SAY: Sorry, Sir, I didn't catch that. Say it again?
He says: this is my sister
DO: animate greet
NEED: none
SAY: Hi! Lovely to meet you.
He says: lol you're funny
DO: animate happy
NEED: none
SAY: I have my moments, Sir."""


OVERHEARD = """## Was this said to you?
The microphone caught this sentence because it contains "Tuesday" somewhere in the middle. If he is clearly talking
to you, answer normally. If he isn't (he's talking to someone else, or "Tuesday" is just the day of the week, as in
"my exam is on Tuesday"), reply exactly:
DO: none
NEED: none
SAY: (silent)"""


def trained() -> bool:
    """The planner adapter is in use (train/planner_train.py): it has learned the examples, so the prompt drops them."""
    from . import core
    return "planner" in core.adapters()


def planner_system(core: str = "") -> str:
    """The planner's system prompt, exactly as it was trained on (shorter once the adapter exists)."""
    from . import config
    rules = SYSTEM if not trained() else SYSTEM.split("\nExamples:")[0]
    return rules.format(owner=config.OWNER_NAME) + (f"\n\n## What you know about him\n{core}" if core else "")


def plan(text: str, history: list[dict], who: str, body, extra_context: str = "", on_say=None) -> dict:
    """{'say', 'actions': [(name, args)], 'need'} for one spoken message. With on_say, the SAY line is handed over the
    moment it's written (Tuesday starts talking while it still decides what to do); then 'said_early' is set."""
    from . import config, roster
    from .minds import local
    import concurrent.futures as cf
    situation = [f"- Now: {datetime.now(cap.TZ):%A %d %B %Y, %H:%M}"]
    # the robot's state and his memories are fetched side by side (each takes a few hundred ms)
    # his profile and memories only when it's surely him (face in view or his voice); anyone else gets a stranger's Tuesday
    recall = cf.ThreadPoolExecutor(1).submit(lambda: ("", "") if who != "owner" else memory.context_parts(text, include_private=False, k=3))
    try:
        st = cf.ThreadPoolExecutor(1).submit(body.status).result(timeout=6)   # a stuck robot link must not stall the answer
        # no battery figure here: the model would answer from it; the battery action reads the real value
        situation.append(f"- The robot: {'on its dock' if st.get('on_charger') else 'off its dock'}"
                         + (", asleep" if getattr(body, "sleeping", False) else "")
                         + (", following him" if getattr(body, "following", None) and body.following.is_set() else ""))
    except Exception:
        pass
    if who == "guest":
        situation.append(f"- You're talking to a guest, not {config.OWNER_NAME}: be friendly, share nothing personal about him, "
                         "and don't change his settings (no remember or incognito).")
    # holding lines ("let me check") aren't conversation: left out, or the model starts answering everything with them
    holds = {x for v in HOLD.values() for x in v} | set(HOLD_AFTER_FILLER.values()) | {x for v in _FILLERS.values() for x in v}
    turns = "\n".join(f"{'He' if m['role'] == 'user' else 'You'}: {m['content'][:200]}" for m in history[-5:-1]
                      if not (m["role"] == "assistant" and m["content"].strip() in holds))
    core, recalled = recall.result()
    # What never changes (instructions, his profile) is the system prompt, which Tuesday Core keeps cached; only the
    # moment, a few recalled memories and the last turns are read anew for each message.
    system = planner_system(core)
    prompt = "\n\n".join(x for x in (
        "## Right now\n" + "\n".join(situation),
        recalled,
        extra_context,
        f"## Just before\n{turns}" if turns else "",
        f"He says: {text}") if x)
    # The plain base model (no personality adapter) decides: it follows instructions far better; the words are short.
    model = "core:planner" if roster.MAIN.model.startswith("core:") else roster.MAIN.model
    early = {}

    def on_text(t):
        # DO and NEED come first; then SAY is spoken a sentence at a time as it's written, the way a person starts
        # talking before they've finished the thought. Never early when it may hold a guessed fact (a number, or a
        # fact action is coming) or the message may not have been meant for Tuesday.
        if on_say is None or extra_context == OVERHEARD or early.get("stop"):
            return
        m = re.search(r"(?im)^\s*SAY\s*:\s*(.*)", t, re.S)
        if not m or not re.search(r"(?im)^\s*NEED\s*:", t):
            return
        if "acts" not in early:
            early["acts"] = {n for n, _ in parse(t, who)["actions"]}
            if early["acts"] & (set(FACTS) | set(MOVES)):
                # a fact: the real value is on its way, never the model's guess. A movement: the words go with it, so
                # they wait for the check that confirms it (or Tuesday says "good night" and then doesn't sleep)
                early["stop"] = True
                return
        body = m.group(1)
        done = re.findall(r".+?[.!?](?=\s)", body)   # complete sentences so far
        for sentence in done[len(early.get("parts", [])):]:
            sentence = sentence.strip().strip('"')
            if re.search(r"\d", sentence) or not _fresh(sentence, history) == sentence:
                early["stop"] = True   # a number (maybe a guess) or a repeat: let the full plan decide the rest
                return
            early.setdefault("parts", []).append(sentence)
            early["spoken"] = True
            on_say(sentence)
    raw = local.quick_stream(prompt, system, model, on_text, prefill="DO:")
    if not _field(raw, "SAY") and not early.get("spoken"):   # an empty or broken stream (the core was busy): ask once more
        print("[intent] empty plan; asking again", flush=True)
        raw = local.quick(prompt, system=system, model=model)
    out = parse(raw, who)
    if early.get("spoken"):
        said = early.get("say") or " ".join(early.get("parts", []))
        rest = out["say"][len(" ".join(early.get("parts", []))):].strip() if early.get("parts") else ""
        if rest and not early.get("stop") and not re.search(r"\d", rest):
            on_say(rest)   # the last sentence (no space after it yet when the stream ended)
            said = (said + " " + rest).strip()
        out["say"], out["said_early"] = said, True
    if out["actions"] or out["need"] != "none":
        # Something will happen (the robot moves, a fact is fetched, a deeper path runs): Tuesday re-reads the request
        # on its own first (free). Only when its two readings disagree does Claude Haiku settle it.
        # Moving the robot is physical, so a movement is always confirmed by Haiku when it's available.
        moves = any(n in MOVES for n, _ in out["actions"])
        checked = check(text, out["actions"], model, who) if out["actions"] and not moves else out["actions"]
        if moves or sorted(n for n, _ in checked) != sorted(n for n, _ in out["actions"]) or out["need"] in ("think", "job"):
            h = _second_opinion(text, history, situation, who)
            if h is None and moves:
                checked = check(text, out["actions"], model, who)
            if h:
                if sorted(n for n, _ in h["actions"]) != sorted(n for n, _ in out["actions"]) or h["need"] != out["need"]:
                    print(f"[intent] Haiku settled it: {h['actions']} need={h['need']} (Tuesday: {out['actions']} {out['need']})", flush=True)
                    out = {**h, "by": "tuesday+haiku", **({"say": out["say"], "said_early": True} if out.get("said_early") else {})}
            else:
                out["actions"] = checked
    elif re.search(r"\d", out["say"]):
        # A number in a plain answer may be a guess at a live fact (battery, time, weather): Tuesday re-reads the request
        # for free; if it really asked for one, the real value is fetched instead of the guess.
        facts = [a for a in check(text, [], model, who) if a[0] in FACTS]
        if facts:
            out["actions"] = facts
    if out.get("said_early"):
        return out   # already spoken: what's said is said
    out["say"] = _fresh(out["say"], history)
    if out["need"] == "think" and len(out["say"].split()) < 6:   # no real first take: a natural holding line
        out["say"] = HOLD["think"][int(time.time()) % 2]
    elif out["need"] not in ("none", "think"):   # facts come later from the deeper path: never a guess now
        out["say"] = HOLD[out["need"]][int(time.time()) % 2]
    elif any(a[0] in FACTS for a in out["actions"]):
        # the facts come from code; the model's own guess at them ("good charge", "looks like rain") is never spoken
        out["say"] = "Let me have a look, Sir." if any(a[0] in ("look", "weather") for a in out["actions"]) else ""
    return out


def _fresh(say: str, history: list[dict]) -> str:
    """Drop sentences Tuesday has just said (a small model happily repeats its last quip); a person wouldn't."""
    import difflib
    norm = lambda t: re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()
    said = [norm(x) for m in history[-6:] if m["role"] == "assistant" for x in re.split(r"(?<=[.!?])\s+", m["content"]) if len(x) > 12]
    parts = re.split(r"(?<=[.!?])\s+", say)
    kept = [x for x in parts if not any(p in norm(x) or difflib.SequenceMatcher(None, norm(x), p).ratio() > 0.8 for p in said)]
    return " ".join(kept) if kept else parts[0]   # all said before: the opening line alone (a greeting may repeat)


def _second_opinion(text: str, history: list[dict], situation: list[str], who: str):
    """Claude Haiku plans the same message from the words alone: no memories, no profile, names scrubbed, nothing
    private (claude.py). None when it can't be asked."""
    from . import claude
    if who == "guest" or not claude.enabled():
        return None
    from . import fable
    lines = []
    for m in history[-3:-1]:
        clean, _, blocked = fable.guard(m["content"][:200])
        if not blocked:
            lines.append(f"{'He' if m['role'] == 'user' else 'You'}: {clean}")
    clean, _, blocked = fable.guard(text)
    if blocked:
        return None     # private: Tuesday alone
    prompt = "## Right now\n" + "\n".join(situation) + ("\n\n## Just before\n" + "\n".join(lines) if lines else "") + f"\n\nHe says: {clean}"
    r = claude.ask("haiku", prompt, SYSTEM.format(owner="your owner"), timeout=10, guard=False)
    return parse(r["text"], who) if r["ok"] and "SAY:" in r["text"].upper() else None


CHECK = """You check a robot's planned actions against what its owner actually said. Keep an action only if his words ask
for it or plainly imply it; drop everything else (most remarks need no action at all). Fix wrong choices: questions about
weather or rain = weather; battery, charge or energy = battery; what the robot sees or is looking at = look; time = time;
date or day = date. Movement (drive, turn, head, lift, follow, come, charger) only when he wants the robot itself to move: a
question or a remark is not a request to move, and him carrying the robot somewhere means it stays still. Stop or chill = stop only.
Good night, bedtime or telling the robot to sleep = sleep. Wake up (while it's asleep) = wake.
Available: """ + " · ".join(("wake", "come", "sleep", "charger", "leave_charger", "turn <deg>", "drive <mm>", "head <deg>", "lift <0..1>", "follow", "stop",
                            "look", "time", "date", "battery", "weather", "remind <min> <text>", "remember <fact>",
                            "incognito_on", "incognito_off", "animate <mood>")) + """
Reply with one line only: DO: <actions separated by ';'> or DO: none"""


def check(text: str, actions: list[tuple[str, str]], model: str, who: str) -> list[tuple[str, str]]:
    """A second, independent look: does what he said really call for these actions? Removes the extras the planner
    likes to add (driving off during a dance, raising the lift when he's sad) and corrects the wrong fact action."""
    from .minds import local
    planned = "; ".join(f"{n} {a}".strip() for n, a in actions)
    try:
        raw = local.quick(f"He said: {text}\nPlanned: DO: {planned}", system=CHECK, model=model)
    except Exception as e:
        print(f"[intent] check failed: {e}", flush=True)
        return actions
    names = {n for n, _ in actions}
    # it may drop anything and correct a fact action, but never add a movement of its own
    kept = [a for a in parse("SAY: -\n" + (raw if "DO:" in raw.upper() else "DO: " + raw), who)["actions"]
            if a[0] in names or a[0] in FACTS]
    if kept != actions:
        print(f"[intent] check: {planned!r} -> {kept}", flush=True)
    return kept


def parse(raw: str, who: str = "owner") -> dict:
    say = _field(raw, "SAY")
    do = _field(raw, "DO")
    need = (_field(raw, "NEED") or "none").lower().split()[0].strip(".;,") if _field(raw, "NEED") else "none"
    if need not in ("none", "think", "research", "apps", "job"):
        need = "none"
    actions = []
    for part in re.split(r"[;\n]", do or ""):
        part = part.strip().strip(".").strip()
        if not part or part.lower() in ("none", "nothing", "-"):
            continue
        name, _, rest = part.partition(" ")
        name = name.lower().strip(",:")
        if name not in ACTIONS:
            continue
        if who == "guest" and name in ("remember", "incognito_on", "incognito_off", "follow", "recall"):
            continue
        actions.append((name, rest.strip().strip('"')))
    if need != "none":   # the answer comes from the deeper path; the robot stays put meanwhile
        actions = [a for a in actions if a[0] in ("remind", "remember")]
    if not say:
        say = raw.strip().splitlines()[0][:200] if raw.strip() else "Sorry, Sir, I lost my train of thought."
        say = re.sub(r"^(SAY|DO|NEED):\s*", "", say)
    return {"say": say.strip().strip('"'), "actions": actions, "need": need}


def _field(raw: str, key: str) -> str:
    m = re.search(rf"(?im)^\s*{key}\s*:\s*(.*)$", raw or "")
    return m.group(1).strip() if m else ""


def _num(s: str, default: float, lo: float, hi: float) -> float:
    m = re.search(r"-?\d+(?:\.\d+)?", s or "")
    v = float(m.group(0)) if m else default
    return max(lo, min(hi, v))


def run(actions: list[tuple[str, str]], body, tuesday, asked: str = "", who: str = "owner") -> list[str]:
    """Carry out the actions; returns sentences to say afterwards (exact facts, camera descriptions, confirmations)."""
    out = []
    for name, arg in actions:
        try:
            if name == "wake":
                if hasattr(body, "wake"):
                    body.wake()
            elif name == "recall":
                from . import sightings
                if who != "owner":   # what he's done and where his things are is his: face in view, or his voice
                    out.append("I'll tell you when I can see you properly, Sir." if who == "unknown" else
                               f"Sorry, that's one for {config.OWNER_NAME} to ask.")
                else:
                    out.append(sightings.answer(arg or asked))
            elif name == "come":
                if hasattr(body, "approach"):
                    if body.approach() == "couldn't see him":
                        out.append("I can't see you, Sir. Where are you?")
                else:
                    body.drive(150)
            elif name == "sleep":
                if hasattr(body, "sleep"):
                    body.sleep()
                else:
                    body.to_charger()
            elif name == "charger":
                body.to_charger()
            elif name == "leave_charger":
                body.off_charger()
            elif name == "turn":
                body.turn(_num(arg, 90, -360, 360))
            elif name == "drive":
                body.drive(_num(arg, 150, -500, 500))
            elif name == "head":
                body.head(_num(arg, 10, -22, 45))
            elif name == "lift":
                body.lift(_num(arg, 0, 0, 1))
            elif name == "follow":
                if hasattr(body, "follow") and not body.following.is_set():
                    import threading
                    threading.Thread(target=body.follow, kwargs={"on_lost": lambda: body.say("I've lost you, Sir. Call me when you want me.")},
                                     daemon=True, name="follow").start()
            elif name == "stop":
                if hasattr(body, "stop_following"):
                    body.stop_following()
            elif name == "look":
                from .tools import run_tool
                q = (f'You are a small robot looking through your camera. Your owner asked you: "{asked}". ' if asked else
                     "You are a small robot looking through your camera. ") + ("Answer him directly, speaking to him: call him "
                     "'you' (the person you see is most likely him), say 'I see' for yourself. One or two short, natural "
                     "sentences, like a friend in the room; no lists; never start with 'The image'. Mention only what you "
                     "can clearly see; never guess at small details, text or what's outside. If it's too dark or blurry, "
                     "say so.")
                desc = run_tool(tuesday.tools, "robot_describe_view", {"question": q})
                if desc == "":   # the vision model now and then returns nothing: ask once more, plainly
                    desc = run_tool(tuesday.tools, "robot_describe_view", {"question": (f'He asked: "{asked}". ' if asked else "")
                                    + "Answer in one or two short sentences, speaking to him as 'you'."})
                if isinstance(desc, str) and desc and not desc.lower().startswith("no camera"):
                    out.append(desc.strip())
                    from . import sightings
                    sightings.note(desc.strip(), source="asked to look")
                else:
                    print(f"[intent] look failed: {desc!r}"[:300], flush=True)
                    out.append("My camera isn't cooperating right now, Sir.")
            elif name == "time":
                out.append(f"It's {datetime.now(cap.TZ):%H:%M}, Sir.")
            elif name == "date":
                n = datetime.now(cap.TZ)
                out.append(f"It's {n:%A} {n.day} {n:%B}, Sir.")
            elif name == "weather":
                w = cap.weather(config.CITY)
                today = (w.get("next_days") or [""])[0] if isinstance(w, dict) else ""
                tomorrow = (w.get("next_days") or ["", ""])[1] if isinstance(w, dict) and len(w.get("next_days") or []) > 1 else ""
                out.append(f"Right now it's {w.get('now', '?')}. Tomorrow: {tomorrow.split(': ', 1)[-1] or today.split(': ', 1)[-1]}." if isinstance(w, dict)
                           else "I couldn't get the weather just now, Sir.")
            elif name == "battery":
                st = body.status()
                level = {1: "low", 2: "fine", 3: "full"}.get(st.get("battery_level"), "unknown")
                out.append(f"Battery's {level}, and I'm {'on' if st.get('on_charger') else 'off'} my dock, Sir.")
            elif name == "remind":
                mins = _num(arg, 10, 0.5, 60 * 24 * 7)
                what = re.sub(r"^\s*-?\d+(?:\.\d+)?\s*(?:min(?:ute)?s?|m)?\s*", "", arg).strip(' "') or "your reminder"
                r = cap.set_reminder(what, in_minutes=mins)
                out.append(f"Reminder set for {r['due'][-5:]}.")
            elif name == "remember":
                if arg and not privacy.incognito():
                    memory.remember(arg, ["told by voice"])
            elif name == "incognito_on":
                privacy.set_incognito(True)
            elif name == "incognito_off":
                privacy.set_incognito(False)
            elif name == "animate":
                trig = ANIMATIONS.get(arg.lower().strip(), ANIMATIONS["happy"])
                body.animate(trig)
        except Exception as e:
            print(f"[intent] {name} {arg!r} failed: {e}", flush=True)
            why = str(e)
            out.append(f"{why[0].upper()}{why[1:]}, Sir." if why.startswith(("something", "the robot didn't")) else
                       f"Sorry, Sir, I couldn't {name.replace('_', ' ')} just now.")
        time.sleep(0.1)
    return out
