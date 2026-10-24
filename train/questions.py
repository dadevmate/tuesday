"""The questionnaire: multiple-choice questions that teach the Tuesday system who its owner is. Every question has
specific options plus "Other" for a free answer; "+" marks pick-all-that-apply. Each section names the minds its
answers train.

The questions are personal by nature, so the real set lives outside this repo, in private/questions.py (a list called
SECTIONS in the same format). Without it, the short generic sample below is used. `python train/questions.py` writes data/questionnaire-2.md to fill in;
train/ingest_answers.py turns ticked answers into memories and per-mind training data."""
from pathlib import Path

FREQ = ["Never", "Rarely (a few times a year)", "About monthly", "About weekly", "A few times a week", "Daily"]
SCALE = ["1–2 (very low)", "3–4 (low)", "5–6 (medium)", "7–8 (high)", "9–10 (very high)"]
AGREE = ["Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"]
SCALES = {"@FREQ": FREQ, "@SCALE": SCALE, "@AGREE": AGREE}


def Q(spec: str):
    """'Question?|opt|opt' or '+Question?|opt|opt' (pick all that apply) or 'Question?|@SCALE'."""
    multi = spec.startswith("+")
    q, *opts = spec.lstrip("+").split("|")
    if len(opts) == 1 and opts[0] in SCALES:
        opts = SCALES[opts[0]]
    return q.strip(), [o.strip() for o in opts], multi


SAMPLE_SECTIONS = [
    ("Daily life", ["tuesday", "sunday"], [
        "When do you usually wake up?|Before 6|6–7|7–8|8–9|After 9",
        "+What does a good evening look like?|Building something|Gaming|Reading|Time with friends|Exercise|Rest",
        "How do you like reminders?|Gentle and early|Right on time|Only for important things|Persistent until done",
        "How much small talk do you want from Tuesday?|Lots|Some|Only when I start it|None",
    ]),
    ("Work and projects", ["wednesday", "thursday"], [
        "+What are you working on right now?|A startup|Studies|A job|Side projects|Open source",
        "Which programming language do you use most?|Python|TypeScript / JavaScript|Go|Rust|C / C++|Other",
        "How should Tuesday give feedback on your ideas?|Blunt|Balanced|Encouraging first|Ask questions first",
    ]),
    ("Money", ["saturday"], [
        "How would you describe your risk appetite?|Very cautious|Cautious|Balanced|Aggressive|Very aggressive",
        "+What do you invest in?|Index funds|Individual stocks|Crypto|Savings only|Nothing yet",
    ]),
    ("Talking with Tuesday", ["tuesday", "friday"], [
        "What tone do you like?|Sharp and witty|Warm and calm|Straight to the point|Playful",
        "+What should Tuesday never bring up in front of guests?|My plans|My money|My feelings|My schedule|Nothing specific",
    ]),
]


def _sections():
    """The owner's own questionnaire (private/questions.py) if it's there, else the generic sample."""
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "private" / "questions.py"
    if path.exists():
        spec = importlib.util.spec_from_file_location("private_questions", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.SECTIONS
    return SAMPLE_SECTIONS


SECTIONS = _sections()


def render(path: Path) -> int:
    lines = ["# Tuesday questionnaire 2: 1,000 questions", "",
             "Private: this file stays in `tuesday/data/` and never leaves your machines.",
             "- Tick your answer with an `x`: `[x]`. Questions marked *(pick all that apply)* can take several ticks.",
             "- None fit? Tick **Other** and write your own after the colon: `[x] Other: my answer`.",
             "- Skip anything, in any order, over days or weeks. Answers become memories when ingested, and train the minds later.", ""]
    n = 0
    for title, minds, qs in SECTIONS:
        lines += ["---", "", f"## {title}", f"*Trains: {', '.join(m.capitalize() for m in minds)}*", ""]
        for spec in qs:
            q, opts, multi = Q(spec)
            n += 1
            lines.append(f"{n}. {q}" + (" *(pick all that apply)*" if multi else ""))
            lines += [f"   - [ ] {o}" for o in opts] + ["   - [ ] Other: ", ""]
    path.write_text("\n".join(lines))
    return n


if __name__ == "__main__":
    import re
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tuesday import config
    out = config.DATA / "questionnaire-2.md"
    if out.exists() and re.search(r"(?m)^ +(→ *\S|- \[[xX]\])", out.read_text()) and "--force" not in sys.argv:
        sys.exit(f"{out} already has answers; not overwriting (use --force)")
    print(render(out), "questions ->", out)
