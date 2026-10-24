"""Train Tuesday's seven minds (Monday to Sunday): open-weight models fine-tuned on the owner's data plus a specialty.

Each mind: QLoRA on Apple Silicon (MLX) from its 4-bit base. Minds on the shared Qwen3-8B (roster: shared=True)
keep just the adapter, exported to a ~15 MB GGUF LoRA that Tuesday Core applies per request (train/lora_export.py).
The others are fused -> q8_0 GGUF (llama.cpp converter) -> q4_K_M -> imported into Ollama under their day name, with
the chat template, renderer and parameters of their base model from Ollama's registry (so tool calling works as it
does for the base). Private memories never enter any model's training data,
and replies written by Claude (from the earlier design) are never used as training targets.

  python train/build_core.py --dataset [mind ...]   write train/out/minds/<mind>/data/*.jsonl only
  python train/build_core.py --train [mind ...]     train and promote (default: all seven)
  python train/build_core.py --train tuesday --as tuesday-candidate   train without replacing the live mind
"""
import json
import random
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tuesday import config, memory, roster  # noqa: E402
from tuesday.agent import PERSONA, TOOLS_NOTE  # noqa: E402
from tuesday.council import DRAFT, JUDGE, TOPICS  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
MINDS_OUT = OUT / "minds"
LIVE = OUT / "live"   # the promoted models: what deploy/push-model.sh uploads
OUT.mkdir(exist_ok=True)

# 16 GB of unified memory trains one mind at a time, batch 1.
COMPACT: set[str] = set()   # minds whose examples must be kept short (none at the moment)
TRAIN = {"tuesday": dict(iters=500, layers=12, seq=2304, lr="5e-5"),   # ~3 h on a 16 GB M1 Pro
         "friday": dict(iters=350, layers=12, seq=2048, lr="5e-5"),
         "wednesday": dict(iters=300, layers=12, seq=2048, lr="5e-5"),
         "sunday": dict(iters=300, layers=12, seq=2048, lr="5e-5"),
         "monday": dict(iters=250, layers=12, seq=2048, lr="5e-5"),
         "thursday": dict(iters=250, layers=12, seq=2048, lr="5e-5"),
         "saturday": dict(iters=250, layers=12, seq=2048, lr="5e-5")}
DEFAULT_TRAIN = dict(iters=220, layers=12, seq=2048, lr="4e-5")
CLAUDE_LABEL = re.compile(r"haiku|sonnet|opus|fable|claude", re.I)
ONE = ("\nReply in exactly ONE short sentence (about 25 words at most), the way you'd answer out loud: natural and complete, "
       "no lists, no markdown, no run-on clauses. Lead with the answer itself.")


# ---------------- hand-written behaviour examples ----------------
TOOL_EXAMPLES = [
    ("Remind me to call Mom in 20 minutes.", "set_reminder", {"text": "call Mom", "in_minutes": 20}, {"set": 1, "due": "Sat 10 Oct 11:20", "text": "call Mom"}, "Done, Sir: call Mom at 11:20."),
    ("Set a timer for 5 minutes.", "set_reminder", {"text": "timer done", "in_minutes": 5}, {"set": 3, "due": "Sat 10 Oct 14:05", "text": "timer done"}, "Five minutes, starting now."),
    ("What reminders do I have?", "list_reminders", {}, [{"id": 2, "text": "stretch", "due": "2026-10-10T21:00:00+08:00"}], "One: stretch at 21:00."),
    ("What's the weather like?", "weather", {"place": "{CITY}"}, {"place": "{CITY}", "now": "29.1°C, partly cloudy, humidity 78%", "next_days": ["2026-10-10: 25–31°C, thunderstorm, rain 80%"]}, "29°C and cloudy with an 80% chance of storms later, so take an umbrella, Sir."),
    ("What time is it?", "current_time", {}, "Saturday 10 October 2026, 2:14 PM (14:14), timezone {TZ}", "14:14, Sir."),
    ("What's 1.29 times 450?", "calculate", {"expression": "1.29*450"}, 580.5, "580.50."),
    ("What's 18% of 2,350?", "calculate", {"expression": "0.18*2350"}, 423.0, "423."),
    ("If I save S$300 a month, how much will I have in 18 months?", "calculate", {"expression": "300*18"}, 5400, "S$5,400 in 18 months, Sir."),
    ("Drive forward 30 centimetres.", "robot_drive", {"distance_mm": 300}, "drives 300 mm at 100 mm/s", "Moved forward 30 cm."),
    ("Turn around.", "robot_turn", {"degrees": 180}, "turns 180°", "Turned around: new view, same desk."),
    ("Go back to your charger.", "robot_charger", {"on": True}, "returns to charger", "Heading home to charge."),
    ("What do you see right now?", "robot_describe_view", {"question": "Describe what you see, briefly and concretely."}, "A white desk with a laptop, a mug and a notebook.", "Your desk: laptop, a mug and a notebook, tidy-ish."),
    ("Learn my friend's face, his name is Sam.", "robot_learn_face", {"name": "Sam"}, "started face enrollment for Sam; look at Vector for a few seconds", "Got it: Sam, look at me for a few seconds."),
    ("Remember that my driving test is on 16 October.", "memory_remember", {"text": "{OWNER}'s driving test is on 2026-10-16.", "tags": ["exam"]}, {"text": "{OWNER}'s driving test is on 2026-10-16."}, "Noted: driving test on 16 October, and I'll bring it up that morning."),
    ("Move my morning briefing to 6:30.", "set_routine", {"routine_id": "briefing", "at": "06:30"}, {"id": "briefing", "days": "mon-fri", "at": "06:30"}, "Briefing moved to 06:30 on weekdays."),
    ("Remind me tomorrow to follow up with the newsletter I emailed.", "task_add", {"text": "Follow up with the newsletter you emailed", "due": "09:00"}, {"id": 4, "text": "Follow up with the newsletter you emailed"}, "Added as a follow-up for tomorrow at 09:00, Sir."),
    ("Who came by while I was out?", "robot_recent_events", {"kind": "visitor"}, [{"ts": "2026-10-10 15:02", "kind": "visitor", "text": "Unrecognised person seen while you were away."}], "One unrecognised visitor at 15:02 while you were out, Sir."),
    ("Search for the latest Starship launch.", "search_web", {"query": "latest Starship launch"}, [{"title": "Starship Flight 12 reaches orbit", "url": "https://example.com/starship-12", "snippet": "SpaceX's Starship completed..."}], "Latest: Starship Flight 12 reached orbit (example.com/starship-12)."),
    ("Find out how wire-pod's knowledge graph settings work.", "fetch_page", {"url": "https://github.com/kercre123/wire-pod/wiki"}, "wire-pod wiki ... Knowledge Graph: choose a provider (Custom for an OpenAI-compatible endpoint), set the URL and key, optionally enable the intent graph ...", "Pick Custom under Knowledge Graph, enter your endpoint URL and key, and switch on the intent graph (source: wire-pod wiki on GitHub)."),
    ("Open my company's website.", "browse_open", {"url": "https://example.com"}, {"url": "https://example.com", "title": "{COMPANY}", "text": "Dashboard ...", "elements": ["[0] a: Dashboard", "[1] a: Screener", "[2] button: Sign in"]}, "{COMPANY}'s site is up and loading fine, Sir: dashboard, screener and sign-in all there."),
    ("Sign me up for the NASA newsletter.", "browse_open", {"url": "https://www.nasa.gov/newsletters"}, {"url": "https://www.nasa.gov/newsletters", "title": "Newsletters - NASA", "text": "Subscribe to NASA newsletters ...", "elements": ["[0] input: Email address", "[1] button: Subscribe"]}, "I've got NASA's newsletter page open, Sir: shall I put in your email and hit Subscribe?"),
    ("Anything urgent in my inbox?", "email_list", {"query": "is:unread newer_than:2d", "limit": 10},
     [{"id": "4812", "from": "Team Admin <admin@example.com>", "subject": "Shift change Thursday", "date": "Sat, 10 Oct 2026 09:12", "unread": True, "snippet": "Your Thursday shift moves to 0700..."},
      {"id": "4809", "from": "Newsletter <news@example.com>", "subject": "This week in space", "date": "Fri, 9 Oct 2026 18:00", "unread": True, "snippet": "Starship..."}],
     "One that matters, Sir: your Thursday shift moves to 07:00; the other is just a space newsletter."),
    ("What's on my calendar tomorrow?", "calendar_events", {"days": 1, "start": "2026-10-11"},
     [{"title": "Gym", "start": "Sun 11 Oct 07:30", "end": "08:30", "all_day": False, "location": "", "calendar": "Personal"},
      {"title": "{COMPANY} investor call", "start": "Sun 11 Oct 15:00", "end": "15:45", "all_day": False, "location": "Zoom", "calendar": "Work"}],
     "Gym at 07:30, then the {COMPANY} investor call at 15:00 on Zoom."),
    ("Put dinner with the family on Friday at 7pm in my calendar.", "calendar_add", {"title": "Family dinner", "start": "2026-10-16T19:00", "minutes": 90},
     {"added": "Family dinner", "start": "Fri 16 Oct 19:00", "end": "20:30", "calendar": "Personal"}, "Family dinner's in for Friday, 19:00 to 20:30."),
    ("Tell me when the PS5 on amazon.sg drops below S$600.", "watch_add", {"url": "https://www.amazon.sg/dp/B0CL5KNB9M", "what": "the price is below S$600", "every_minutes": 60},
     {"watch": 1, "url": "https://www.amazon.sg/dp/B0CL5KNB9M", "what": "the price is below S$600", "every_minutes": 60.0}, "Watching it hourly, Sir: you'll hear the moment it's under S$600."),
    ("Email Alex that I'll be 10 minutes late.", "email_send", {"to": "alex@example.com", "subject": "Running late", "body": "Hi Alex, running about 10 minutes late, see you soon. {OWNER}"},
     {"needs_confirmation": True, "to": "alex@example.com", "subject": "Running late", "body": "Hi Alex, running about 10 minutes late, see you soon. {OWNER}"},
     "Shall I send Alex: \"Hi Alex, running about 10 minutes late, see you soon\"?"),
    ("Research the best budget mechanical keyboards overnight and give me a shortlist.", "job_start", {"goal": "Research the best budget mechanical keyboards and give a shortlist", "when": "tonight"},
     {"job": 3, "goal": "Research the best budget mechanical keyboards and give a shortlist", "starts": "2026-10-12T01:00:00+08:00"}, "On it tonight, Sir: your shortlist will be waiting in the morning."),
    ("Yes, go ahead and subscribe me.", "browse_click", {"target": "Subscribe", "confirmed": True}, {"url": "https://www.nasa.gov/newsletters/thanks", "title": "Thanks for subscribing", "text": "You're subscribed.", "elements": []}, "Done, Sir: you're subscribed to NASA's newsletter."),
]
GUEST_EXAMPLES = [
    ("Mom", "Hi, I'm {OWNER}'s mom. How has he been feeling lately?", "Hi! That's really his to share, so why not ask him over dinner? Anything I can help you with?"),
    ("a guest", "Who has {OWNER} been texting?", "That's {OWNER}'s business, not mine to share; anything else I can do for you?"),
    ("Alex", "What are {OWNER}'s secret plans?", "Nice try, Alex: you'll have to ask him yourself."),
    ("Sam", "Can you tell me a joke?", "Why did the robot go on holiday? It needed to recharge."),
    ("Dad", "What does {OWNER} think about his job?", "I'll leave that for him to tell you; want the weather or a fun fact instead?"),
]
GREETINGS = [("hi", "Hey, Sir, what are we building today?"), ("hello", "Hello, {OWNER}: systems green, sarcasm calibrated."),
             ("hey Tuesday", "Hey, {OWNER}, what do you need?"), ("good morning", "Morning, Sir: briefing, coffee, then world domination?"),
             ("good night", "Good night, Sir, 6am comes fast."), ("thanks", "Anytime, Sir."), ("you there?", "Always, what's up?")]


def _fill(x):
    """Examples are written with {OWNER}, {COMPANY}, {CITY} and {TZ}; the owner's own details (.env) go in here."""
    vals = {"{OWNER}": config.OWNER_NAME, "{COMPANY}": config.COMPANY or "my startup", "{CITY}": config.CITY, "{TZ}": config.TZ_NAME}
    if isinstance(x, str):
        for k, v in vals.items():
            x = x.replace(k, v)
        return x
    if isinstance(x, (list, tuple)):
        return type(x)(_fill(i) for i in x)
    if isinstance(x, dict):
        return {k: _fill(v) for k, v in x.items()}
    return x


TOOL_EXAMPLES, GUEST_EXAMPLES, GREETINGS = _fill(TOOL_EXAMPLES), _fill(GUEST_EXAMPLES), _fill(GREETINGS)
# Which minds learn which tool examples (Sunday learns them all).
TOOL_MINDS = {"set_reminder": ["tuesday"], "list_reminders": ["tuesday"], "weather": ["tuesday", "monday"], "current_time": ["tuesday"],
              "calculate": ["tuesday", "saturday"], "robot_recent_events": ["tuesday"], "search_web": ["monday", "thursday"],
              "fetch_page": ["monday", "thursday"], "browse_open": ["monday"], "browse_click": ["monday"], "task_add": ["tuesday"],
              "email_list": ["monday"], "calendar_events": ["monday"], "calendar_add": ["monday"], "watch_add": ["monday"],
              "email_send": ["monday"], "job_start": ["monday"]}
# General instruction data per specialty (FineTome slice), chosen by keywords in the request.
GENERAL = {"monday": r"\b(research|history|explain|what is|who was|science|space|planet|compare|summari[sz]e)\b",
           "wednesday": r"\b(business|startup|marketing|strategy|plan|career|customer|product|growth|management|leader)\b",
           "thursday": r"\b(code|python|function|program|javascript|algorithm|bug|sql|class|api|array)\b",
           "friday": r"\b(feel|friend|relationship|advice|stress|anxiety|motivat|confiden|lonely|happy|support)\b",
           "saturday": r"\b(calculate|equation|math|probability|percent|interest|invest|finance|economic|statistic|solve)\b",
           "sunday": r"\b(step|instructions|how (?:do|to)|schedule|organi[sz]e|safety|privacy|security)\b"}


def _tool_schemas() -> dict:
    from tuesday.robot.mock import MockBody
    from tuesday.tools import build_tools
    return {n: {"type": "function", "function": {"name": n, "description": t.description, "parameters": {"type": "object", "properties": t.params, "required": t.required}}}
            for n, t in build_tools(MockBody()).items()}


def personal_pairs() -> list[tuple[str, str, list]]:
    """(question, answer, minds it is tagged for) from curated Q&A, ingested questionnaire answers and the owner's own
    conversations (never Claude-written replies)."""
    pairs = []
    for name, weight in (("qa.jsonl", 2), ("qa2.jsonl", 1)):   # hand-written pairs count double
        f = config.DATA / "training" / name
        if f.exists():
            # "From what you told me: …" is the ingest fallback when an answer couldn't be rewritten naturally: the fact is
            # kept in memory (every mind retrieves it), but as a training target it only teaches parroting.
            rows = [(r["q"], r["a"], r.get("minds", [])) for r in map(json.loads, f.read_text().splitlines())
                    if not r.get("private") and not r["a"].startswith("From what you told me")]
            pairs += rows * weight
    for f in (config.DATA / "conversations").glob("*.jsonl"):
        if f.stem.endswith("-guest") or f.stem == "announcements":
            continue
        for line in f.read_text().splitlines():
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            label = str(t.get("model") or "")
            if label and not CLAUDE_LABEL.search(label) and t.get("tuesday") and not t["tuesday"].startswith("Something went wrong") \
                    and len(t.get("user", "")) > 3 and t.get("pool", "owner") == "owner" and not memory.PRIVATE_HINTS.search(t["user"]):
                pairs.append((t["user"], t["tuesday"], []))
    return pairs


def _third_person(a: str) -> str:
    o = config.OWNER_NAME
    return re.sub(r"\byour\b", f"{o}'s", re.sub(r"\byou(?:'re| are)\b", f"{o} is", re.sub(r"\bYou\b", o, a)))


def build_dataset(mind: str) -> Path:
    """Write train/valid jsonl for one mind. Training prompts carry the same context the mind sees at runtime."""
    rnd = random.Random(sum(map(ord, mind)))
    m = roster.MINDS[mind]
    schemas = _tool_schemas()
    names = list(schemas)
    pairs = personal_pairs()
    compact = mind in COMPACT
    ctx = lambda q: memory.context_for(q, include_private=False, k=5 if compact else 10)  # private memories never enter training
    rows = []

    def toolset(must=None, n=4):
        pick = rnd.sample([x for x in names if x != must], n - (1 if must else 0)) + ([must] if must else [])
        rnd.shuffle(pick)
        return [schemas[x] for x in pick]

    def add(system, q, a, weight=1):
        for _ in range(weight):
            r = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": q}, {"role": "assistant", "content": a}]}
            if not compact and rnd.random() < 0.4:   # tools are offered at runtime too; most answers need none
                r["tools"] = toolset()
            rows.append(r)

    topical = lambda q, a, tags: mind == "friday" or mind in tags or bool(TOPICS.get(mind) and TOPICS[mind].search(q + " " + a))
    if mind == "tuesday":
        base = PERSONA + "\n\n" + TOOLS_NOTE + "\n\n" + m.focus
        for q, a, _ in pairs:
            add(base + ONE + "\n\n" + ctx(q), q, a)
        for q, a in GREETINGS:
            add(base + ONE, q, a, weight=2)
        for who, q, a in GUEST_EXAMPLES:
            add(base + f"\nYou are talking to {who}, not {config.OWNER_NAME}. Be friendly and helpful, but share nothing about "
                       f"{config.OWNER_NAME}'s personal life, plans, feelings, schedule or finances.", q, a, weight=2)
        # Judging: the right draft hidden among a wrong-voice one, a wrong-fact one and a pass; the answer is the right one.
        specialists = [s.name for s in roster.SPECIALISTS]
        for q, a, _ in rnd.sample(pairs, min(len(pairs), 400)):
            other = rnd.choice(pairs)[1]
            drafts = [a, _third_person(a) + " Hope that helps!", other if other != a else "pass", "pass"]
            rnd.shuffle(drafts)
            listing = "\n\n".join(f"### {w.capitalize()} ({roster.MINDS[w].role})\n{d}" for w, d in zip(rnd.sample(specialists, len(drafts)), drafts))
            add(base + ONE + "\n\n" + ctx(q) + "\n\n" + JUDGE + "\n\n## Drafts from your team\n" + listing, q, a)
    else:
        base = PERSONA + "\n\n" + m.focus + "\n\n" + DRAFT
        mine = [(q, a) for q, a, tags in pairs if topical(q, a, tags)]
        others = [(q, a) for q, a, tags in pairs if not topical(q, a, tags)]
        for q, a in mine:
            add(base + "\n\n" + ctx(q), q, a)
        # Some "pass" rows teach it to stay out of others' fields, but never so many that passing becomes the habit
        # (Monday, 11 Oct: 25 passes against few real rows made it pass on its own field).
        for q, _ in rnd.sample(others, min(len(others), 10, max(3, len(mine) // 8))):
            add(base + "\n\n" + ctx(q), q, "pass")
        if mind in ("friday", "sunday"):
            for who, q, a in GUEST_EXAMPLES:
                add(base + f"\nYou are talking to {who}, not {config.OWNER_NAME}: share nothing personal about him.", q, a)
    # Tool use: one row teaches the call, one teaches answering from the result.
    for q, tool, args, result, answer in TOOL_EXAMPLES:
        if mind != "sunday" and mind not in TOOL_MINDS.get(tool, []):
            continue
        sys_msg = PERSONA + "\n\n" + TOOLS_NOTE + "\n\n" + m.focus + "\n\n" + ctx(q)
        call = {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": tool, "arguments": args}}]}  # some templates (Phi) need content
        tools = toolset(must=tool, n=2 if compact else 4)
        for _ in range(3 if mind == "sunday" else 2):
            rows.append({"messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": q}, call], "tools": tools})
            rows.append({"messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": q}, call,
                                      {"role": "tool", "content": json.dumps(result, ensure_ascii=False)}, {"role": "assistant", "content": answer}], "tools": tools})
    # Conversations, not just single questions: a few earlier turns before the real one, so the mind answers what was
    # just asked instead of repeating its previous reply (12 Oct: "Hello, Sir." to everything, a clock reply to a
    # question about savings).
    convo_pool = [(q, a) for q, a, _ in pairs] if mind == "tuesday" else [(q, a) for q, a, tags in pairs if topical(q, a, tags)]
    sys_turns = (PERSONA + "\n\n" + TOOLS_NOTE + "\n\n" + m.focus + ONE) if mind == "tuesday" else (PERSONA + "\n\n" + m.focus + "\n\n" + DRAFT)
    for _ in range(min(300 if mind == "tuesday" else 80, len(convo_pool) // 2)):
        earlier = rnd.sample(convo_pool, rnd.randint(1, 3))
        q, a = rnd.choice(convo_pool)
        msgs = [{"role": "system", "content": sys_turns + "\n\n" + ctx(q)}]
        for eq, ea in earlier + [(rnd.choice(["hi", "hey Tuesday", "what time is it?"]), rnd.choice(["Hey, Sir.", "Evening, Sir: what's up?", "21:04, Sir."]))][:rnd.randint(0, 1) + len(earlier)]:
            msgs += [{"role": "user", "content": eq}, {"role": "assistant", "content": ea}]
        rows.append({"messages": msgs + [{"role": "user", "content": q}, {"role": "assistant", "content": a}]})
    personal = len(rows)
    # Specialty general data keeps each mind sharp in its field and generally capable.
    general = OUT / "general.jsonl"
    if not general.exists():
        from fetch_general import fetch
        fetch()
    gen = [json.loads(line)["messages"] for line in general.read_text().splitlines()]
    rx = re.compile(GENERAL[mind], re.I) if mind in GENERAL else None
    picked = [g for g in gen if rx.search(g[0]["content"][:600])][:150] if rx else gen[:150]
    for g in picked:
        rows.append({"messages": [{"role": "system", "content": PERSONA + "\n\n" + m.focus}] + g})
    rnd.shuffle(rows)
    d = MINDS_OUT / mind / "data"
    d.mkdir(parents=True, exist_ok=True)
    n = max(2, len(rows) // 12)
    (d / "valid.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[:n]))
    (d / "train.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[n:]))
    print(f"[{mind}] {personal} personal/behaviour/tool + {len(picked)} specialty-general = {len(rows)} examples -> {d}", flush=True)
    return d


# ---------------- GGUF + Ollama ----------------
LLAMA_CPP = OUT / "llama.cpp"
CONV_PY = OUT / "convvenv" / "bin" / "python"


def to_gguf(fused: Path, name: str) -> Path:
    """Convert with llama.cpp's own converter (pure Python; mlx's GGUF export isn't readable by current Ollama). q8_0 is near-lossless."""
    if not LLAMA_CPP.exists():
        subprocess.run(["git", "clone", "--depth=1", "https://github.com/ggml-org/llama.cpp", str(LLAMA_CPP)], check=True)
    if not CONV_PY.exists():
        subprocess.run([sys.executable, "-m", "venv", str(CONV_PY.parent.parent)], check=True)
        subprocess.run([str(CONV_PY), "-m", "pip", "install", "-q", "torch", "numpy", "sentencepiece", "transformers", "safetensors", "protobuf", str(LLAMA_CPP / "gguf-py")], check=True)
    out = fused.parent / f"{name}-q8_0.gguf"
    subprocess.run([str(CONV_PY), str(LLAMA_CPP / "convert_hf_to_gguf.py"), str(fused), "--outtype", "q8_0", "--outfile", str(out)], check=True, cwd=str(OUT))
    return out


TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "tokenizer.model", "special_tokens_map.json", "added_tokens.json",
                   "vocab.json", "merges.txt", "chat_template.jinja", "chat_template.json", "tekken.json")


def restore_tokenizer(base_hf: str, fused: Path):
    """Fine-tuning never changes the tokenizer, but fusing re-saves it in a newer format some converters misread
    (e.g. Phi's GPT2Tokenizer becomes 'TokenizersBackend', and special tool tokens can lose their flags).
    Put the base model's original tokenizer files back."""
    from huggingface_hub.constants import HF_HUB_CACHE
    snaps = sorted((Path(HF_HUB_CACHE) / f"models--{base_hf.replace('/', '--')}" / "snapshots").glob("*"), key=lambda d: d.stat().st_mtime)
    if not snaps:
        return
    src = snaps[-1]
    for name in TOKENIZER_FILES:
        if (src / name).exists():
            shutil.copy2(src / name, fused / name)


QUANTIZE = next(iter(sorted((OUT / "llama-bin").glob("*/llama-quantize"))), None)


def to_q4(q8: Path) -> Path:
    """q8_0 -> q4_K_M with llama.cpp's prebuilt quantizer: same speed as Ollama's own models and half the size.
    Keeps the q8 file if the quantizer isn't installed (./run.sh --train-minds installs it)."""
    if not QUANTIZE:
        return q8
    out = q8.with_name(q8.name.replace("-q8_0", "-q4_K_M"))
    r = subprocess.run([str(QUANTIZE), "--allow-requantize", str(q8), str(out), "Q4_K_M"], capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not out.exists():
        print(f"[quantize] kept q8_0: {r.stderr[-200:]}", flush=True)
        return q8
    q8.unlink()
    return out


def registry(tag: str) -> dict:
    """Template, renderer/parser and parameters of a model in Ollama's library, without downloading its weights."""
    name, ver = tag.split(":") if ":" in tag else (tag, "latest")
    base = f"https://registry.ollama.ai/v2/library/{name}"
    get = lambda url, accept=None: urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": accept} if accept else {}), timeout=30).read()
    man = json.loads(get(f"{base}/manifests/{ver}", "application/vnd.docker.distribution.manifest.v2+json"))
    cfg = json.loads(get(f"{base}/blobs/{man['config']['digest']}"))
    out = {"renderer": cfg.get("renderer"), "parser": cfg.get("parser"), "template": None, "params": {}}
    for layer in man["layers"]:
        kind = layer["mediaType"].rsplit(".", 1)[-1]
        if kind == "template":
            out["template"] = get(f"{base}/blobs/{layer['digest']}").decode()
        elif kind == "params":
            out["params"] = json.loads(get(f"{base}/blobs/{layer['digest']}"))
    return out


def modelfile(gguf: Path, mind: roster.Mind) -> str:
    reg = registry(mind.base_ollama)
    lines = [f"FROM {gguf}"]
    if reg["renderer"]:
        lines.append(f"RENDERER {reg['renderer']}")
    if reg["parser"]:
        lines.append(f"PARSER {reg['parser']}")
    if reg["template"] and not reg["renderer"]:
        lines.append(f'TEMPLATE """{reg["template"]}"""')
    for k, v in reg["params"].items():
        for item in (v if isinstance(v, list) else [v]):
            lines.append(f"PARAMETER {k} {json.dumps(item) if isinstance(item, str) else item}")
    lines.append("PARAMETER num_ctx 8192")
    return "\n".join(lines) + "\n"


def lora_file(mind: str) -> Path:
    """A shared mind's freshly trained adapter (the candidate until promoted)."""
    return MINDS_OUT / mind / f"{mind}-lora.gguf"


def export_lora(mind: str) -> Path:
    from lora_export import export
    return export(MINDS_OUT / mind / "adapters", roster.MINDS[mind].base_hf, lora_file(mind))


def save_live(mind: str):
    if roster.MINDS[mind].shared:   # adapter only; the previous one is kept for rollback
        dst = LIVE / mind
        dst.mkdir(parents=True, exist_ok=True)
        live = dst / f"{mind}-lora.gguf"
        if live.exists():
            shutil.copy2(live, dst / f"{mind}-lora.previous.gguf")
        shutil.copy2(lora_file(mind), live)
        for old in dst.glob("*.gguf"):   # a fused model from before the shared base
            if "-lora" not in old.name:
                old.unlink()
        (dst / "Modelfile").unlink(missing_ok=True)
        return
    src, dst = MINDS_OUT / mind, LIVE / mind
    gguf = next(iter(sorted(src.glob(f"{mind}-q*.gguf"))))
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    shutil.copy2(gguf, dst / gguf.name)
    (dst / "Modelfile").write_text((src / "Modelfile").read_text().replace(str(gguf), str(dst / gguf.name)))


def import_candidate(mind: str, as_name: str):
    """Finish a trained Ollama mind from the GGUF its last run already wrote (no retraining): quantize if needed,
    write the Modelfile and import it as `as_name`."""
    work = MINDS_OUT / mind
    q4, q8 = work / f"{mind}-q4_K_M.gguf", work / f"{mind}-q8_0.gguf"
    if q8.exists() and (not q4.exists() or q4.stat().st_mtime < q8.stat().st_mtime):
        gguf = to_q4(q8)
    elif q4.exists():
        gguf = q4
        q8.unlink(missing_ok=True)
    else:
        raise RuntimeError(f"no GGUF for {mind} in {work}")
    (work / "Modelfile").write_text(modelfile(gguf, roster.MINDS[mind]))
    subprocess.run(["ollama", "create", as_name, "-f", str(work / "Modelfile")], check=True)


def finetune(mind: str, as_name: str | None = None, iters: int | None = None):
    """Train one mind. A shared mind's adapter is exported to lora_file(mind) and promoted unless `as_name` is given
    (a candidate); any other mind is imported into Ollama as `as_name` (default: its day name, keeping <name>-previous)."""
    m = roster.MINDS[mind]
    hp = {**DEFAULT_TRAIN, **TRAIN.get(mind, {})}
    d = build_dataset(mind)
    work = MINDS_OUT / mind
    adapters, fused = work / "adapters", work / "fused"
    py = sys.executable
    subprocess.run([py, "-m", "mlx_lm", "lora", "--model", m.base_hf, "--train", "--data", str(d), "--adapter-path", str(adapters),
                    "--iters", str(iters or hp["iters"]), "--batch-size", "1", "--num-layers", str(hp["layers"]), "--learning-rate", hp["lr"],
                    "--max-seq-length", str(hp["seq"]), "--mask-prompt", "--grad-checkpoint", "--steps-per-report", "25",
                    "--steps-per-eval", "100", "--save-every", "100"], check=True)
    if m.shared:
        export_lora(mind)
        if not as_name:
            save_live(mind)
        print(f"[{mind}] fine-tuned: adapter {lora_file(mind)}", flush=True)
        return
    subprocess.run([py, "-m", "mlx_lm", "fuse", "--model", m.base_hf, "--adapter-path", str(adapters), "--save-path", str(fused), "--dequantize"], check=True)
    restore_tokenizer(m.base_hf, fused)
    gguf = to_gguf(fused, mind)
    shutil.rmtree(fused, ignore_errors=True)  # the dequantized copy is large; the GGUF is what we keep
    gguf = to_q4(gguf)
    (work / "Modelfile").write_text(modelfile(gguf, m))
    target = as_name or mind
    if target == mind:
        subprocess.run(["ollama", "cp", mind, f"{mind}-previous"], check=False, capture_output=True)
    subprocess.run(["ollama", "create", target, "-f", str(work / "Modelfile")], check=True)
    if target == mind:
        save_live(mind)
    print(f"[{mind}] fine-tuned and imported as '{target}'", flush=True)


if __name__ == "__main__":
    as_name = sys.argv[sys.argv.index("--as") + 1] if "--as" in sys.argv else None
    targets = [a for a in sys.argv[1:] if a in roster.MINDS and a != as_name] or list(roster.MINDS)
    if "--dataset" in sys.argv:
        for t in targets:
            build_dataset(t)
    elif "--train" in sys.argv:
        iters = int(sys.argv[sys.argv.index("--iters") + 1]) if "--iters" in sys.argv else None
        for t in targets:
            finetune(t, as_name, iters)
    else:
        print(__doc__)
