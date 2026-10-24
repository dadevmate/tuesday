"""Training data for Tuesday's planner adapter (tuesday/intent.py): what to do, whether more is needed, and what to say,
for anything said to the robot.

The plain base model copies its prompt's examples and pads replies with filler; a small adapter trained on many
varied, high-quality moments fixes both. Claude Sonnet writes the moments, generically (an owner called "Sir", no
names, nothing about the real owner), in the exact three-line format. Every line is checked by intent.parse before
it's kept. Output: train/out/planner/data.jsonl (one moment per line).

  python train/planner_data.py [batches]
"""
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tuesday import claude, intent  # noqa: E402

OUT = Path(__file__).resolve().parent / "out" / "planner"
CATEGORIES = [
    "casual small talk and greetings at different times of day (hi, hello Tuesday, morning, what's up, how are you, good night)",
    "quick general-knowledge questions with short, correct answers (science, geography, history, definitions, maths)",
    "his opinions questions: what do you think about X (companies, foods, films, music, ideas): a real view with a reason",
    "him sharing feelings or his day (tired, stressed, happy, bored, rough day, excited): warm, human, no robot movement",
    "natural-language requests to move the robot: come over, turn around, look up, back up a bit, spin, go to your "
    "charger, get off the dock, follow me, stop, lift up, dance, go to sleep / good night (sleep)",
    "remarks that mention moving or places but must NOT move the robot: he's carrying it somewhere, he's going out, "
    "talking about a trip, asking if it wants to come along",
    "asking what the robot sees (look): what do you see, look at this, what am I holding, how do I look, describe the room",
    "time, date, battery and weather questions in many phrasings (actions time, date, battery, weather; never guess)",
    "current-events and live facts needing research: news, prices, scores, launches, releases (NEED research)",
    "his email, calendar, files, portfolio, watchlists (NEED apps)",
    "decisions and advice that deserve thought (should I..., which is better..., help me plan...): NEED think, with an "
    "honest quick take in SAY",
    "reminders and things to remember (remind me in N minutes to..., remember that I...)",
    "misheard or garbled speech-to-text input (nonsense fragments, half sentences): briefly ask him to repeat",
    "introducing people and guests (this is my friend, meet my mom, my sister is here): warm hello to them",
    "follow-up questions that only make sense with the conversation before (include 2-4 earlier turns in history)",
    "jokes, banter, teasing, compliments to the robot, playful challenges",
    "multi-step background tasks (look into X and tell me tonight, keep an eye on Y): NEED job",
    "privacy: incognito, stop recording, unincognito; and 'stop' / 'never mind' / 'cancel that'",
    "mixed: a question plus an action in one sentence (come here and tell me the time; turn around and look)",
    "things a friend would answer honestly they don't know or can't do, without making anything up",
]

ASK = """Write {n} varied training examples for a small home robot's planner, all in the category:
  {category}

The robot is "Tuesday", a warm, witty personal AI living in a little Vector robot (wheels, a tilting head, a lift,
camera, speaker; it charges on a dock). Its owner is called "Sir". Use NO real names for the owner, no personal
details; guests can have generic first names.

The planner's instructions (follow them exactly in the outputs):
---
{rules}
---

Each example is one JSON object on its own line, nothing else:
{{"time": "<weekday HH:MM>", "dock": "on its dock" | "off its dock", "history": [["He"|"You", "<line>"], ...],
  "message": "<what he said, as speech-to-text would write it: lowercase is fine, little punctuation>",
  "output": "DO: <actions or none>\\nNEED: <none|think|research|apps|job>\\nSAY: <what Tuesday says>"}}

Make them feel real: everyday wording, typos and fillers sometimes, different lengths and moods. SAY must sound like a
person, not an assistant: open with a short first sentence (2-6 words), at most one more; no filler like "let me know
if you need anything"; no flattery; never invent what it sees, the time, battery, weather, news or his schedule.
History is usually empty; use it when the category needs it. Output only the JSON lines."""


def rules() -> str:
    return intent.SYSTEM.split("\nExamples:")[0].format(owner="his owner")


def batch(category: str, n: int = 60) -> list[dict]:
    r = claude.ask("sonnet", ASK.format(n=n, category=category, rules=rules()),
                   "You write precise, varied training data. Output only JSON lines.", timeout=600, guard=False,
                   generic=True)   # nothing about the owner is in it: synthetic, generic moments only
    if not r["ok"]:
        print(f"  failed: {r.get('reason')}", flush=True)
        return []
    out = []
    for line in r["text"].splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            ex = json.loads(line)
            plan = intent.parse(ex["output"])
        except Exception:
            continue
        # kept only if it's well-formed: real actions, a known need, something said (or nothing, for a fact action)
        if not re.match(r"DO:.*\nNEED:.*\nSAY:", ex["output"].strip()):
            continue
        if not plan["say"] and not any(a[0] in intent.FACTS for a in plan["actions"]):
            continue
        ex["category"] = category
        out.append(ex)
    return out


def main(batches: int):
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / "data.jsonl"
    cats = (CATEGORIES * (batches // len(CATEGORIES) + 1))[:batches]
    random.Random(7).shuffle(cats)
    total = sum(1 for _ in f.open()) if f.exists() else 0
    for i, c in enumerate(cats, 1):
        got = batch(c)
        with f.open("a") as w:
            for ex in got:
                w.write(json.dumps(ex, ensure_ascii=False) + "\n")
        total += len(got)
        print(f"[{i}/{len(cats)}] {len(got)} kept ({total} total): {c[:60]}", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 25)
