"""Turn answers in data/questionnaire-2.md into memories and per-mind training pairs.

- Every answer becomes a memory (usable at once), keyed by its question number so re-running updates instead of duplicating.
- Non-private answers also become training pairs in data/training/qa2.jsonl, tagged with the minds their section trains:
  the question as the owner would ask it ("Where did I grow up?") and Tuesday's reply to him, rewritten from his answer
  by Tuesday's own model and checked against the answer's words (cached, so only new answers cost time).
- Private answers (the feelings section, anything marked private, or emotional/secret content) stay retrieval-only.

  python train/ingest_answers.py
"""
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tuesday import config, memory, roster  # noqa: E402
from tuesday.minds import local  # noqa: E402

SRC = config.DATA / "questionnaire-2.md"
QA2 = config.DATA / "training" / "qa2.jsonl"
CACHE = config.DATA / "training" / "qa2-cache.json"
PRIVATE_SECTIONS = ("feelings",)
SWAPS = [(r"\byou were\b", "I was"), (r"\bwere you\b", "was I"), (r"\byou are\b", "I am"), (r"\byou're\b", "I'm"), (r"\bare you\b", "am I"), (r"\bdo you\b", "do I"), (r"\bdid you\b", "did I"), (r"\bhave you\b", "have I"),
         (r"\bcan you\b", "can I"), (r"\bwould you\b", "would I"), (r"\bshould you\b", "should I"), (r"\byourself\b", "myself"),
         (r"\byours\b", "mine"), (r"\byour\b", "my"), (r"\byou\b", "I")]


def parse(text: str) -> list[dict]:
    """Ticked options ([x]) per question; 'Other: ...' carries the owner's own words. Unanswered questions are skipped."""
    out, section, minds, cur = [], "", [], None
    for line in text.splitlines():
        if line.startswith("## "):
            section, minds = line[3:].strip(), []
        elif line.startswith("*Trains:"):
            minds = [m.strip().lower() for m in line.strip("*").split(":", 1)[1].split(",") if m.strip().lower() in roster.MINDS]
        elif m := re.match(r"^(\d+)\.\s+(.*?)(\s*\*\(pick all that apply\)\*)?$", line):
            cur = {"n": int(m.group(1)), "q": m.group(2).strip(), "picks": [], "section": section, "minds": minds}
            out.append(cur)
        elif cur and (m := re.match(r"^\s+- \[[xX]\]\s*(.*)$", line)):
            opt = m.group(1).strip()
            if opt.lower().startswith("other"):
                opt = opt.split(":", 1)[1].strip() if ":" in opt else ""
            if opt:
                cur["picks"].append(opt)
        elif cur and (m := re.match(r"^\s+→\s?(\S.*)$", line)):   # free-text answers (older format) still count
            cur["picks"].append(m.group(1).strip())
    for r in out:
        r["a"] = "; ".join(r["picks"])
    return [r for r in out if r["a"]]


def as_owner(q: str) -> str:
    """The question as the owner would ask Tuesday: 'Where did you grow up?' -> 'Where did I grow up?'."""
    for rx, rep in SWAPS:
        q = re.sub(rx, rep, q, flags=re.I)
    return q[0].upper() + q[1:]


def is_private(r: dict) -> bool:
    return r["section"].lower().startswith(PRIVATE_SECTIONS) or "private" in r["q"].lower() or bool(memory.PRIVATE_HINTS.search(r["a"]))


def rewrite(q: str, a: str) -> str | None:
    """Tuesday's spoken reply to the owner, built only from his answer."""
    try:
        out = local.quick(f"{config.OWNER_NAME} was asked: {q}\nHis answer: {a}\n\nWrite what Tuesday would say if he later asked this, speaking TO him "
                          "('you'), in one or two natural sentences, using only facts from his answer.",
                          system="You rewrite facts faithfully. No extra facts.", model=config.env("TUESDAY_REWRITE_MODEL", roster.MINDS["thursday"].model)).strip().strip('"')
    except Exception:
        return None
    return out if out and memory._grounded(out, a + " " + q, need=0.5) else None


def main():
    if not SRC.exists():
        sys.exit("No data/questionnaire-2.md yet: run python train/questions.py")
    answers = parse(SRC.read_text())
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    pairs, n_private = [], 0
    for r in answers:
        private = is_private(r)
        n_private += private
        tag = f"[Q{r['n']}]"
        memory.forget(tag)  # replace the previous version of this answer
        memory.remember(f"{tag} {r['q']} {config.OWNER_NAME}: {r['a']}", ["questionnaire", re.sub(r'\W+', '-', r['section'].lower()).strip('-')] + r["minds"]
                        + (["private"] if private else []), source="questionnaire 2", dedupe=False)
        if private:
            continue
        key = hashlib.sha1(f"{r['q']}|{r['a']}".encode()).hexdigest()
        if key not in cache:
            cache[key] = rewrite(r["q"], r["a"])
        # A rewrite that drifted from his words was rejected; a plain, faithful reply keeps the answer anyway.
        reply = cache[key] or f"From what you told me: {r['a'].rstrip('.')}."
        pairs.append({"q": as_owner(r["q"]), "a": reply, "minds": r["minds"], "n": r["n"]})
    QA2.parent.mkdir(parents=True, exist_ok=True)
    QA2.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pairs))
    CACHE.write_text(json.dumps(cache))
    print(f"{len(answers)} answers -> memories ({n_private} private, retrieval-only); {len(pairs)} training pairs -> {QA2}")


if __name__ == "__main__":
    main()
