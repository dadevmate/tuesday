"""Train Tuesday's planner adapter on train/out/planner/data.jsonl (written by planner_data.py) and put it live.

The adapter sits on Tuesday Core's shared Qwen3-8B like the minds' adapters, and only the planner (tuesday/intent.py)
uses it. Training prompts carry exactly what the planner sees at runtime: its rules (without the examples it has now
learned), his profile, the moment, recent turns, his words. Tuesday has to be stopped while it trains (16 GB can't hold
Tuesday Core and the trainer at once).

  python train/planner_train.py            build the dataset, train, export, put live
  python train/planner_train.py --dry      only build the dataset
"""
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "train"))
from tuesday import config, intent, memory, roster  # noqa: E402

WORK = ROOT / "train" / "out" / "planner"
LIVE = ROOT / "train" / "out" / "live" / "planner"
ITERS, LAYERS, SEQ, LR = 900, 12, 1280, "5e-5"


def rows() -> list[dict]:
    rules = intent.SYSTEM.split("\nExamples:")[0].format(owner=config.OWNER_NAME)
    core, _ = memory.context_parts("", include_private=False, k=0)
    system = rules + (f"\n\n## What you know about him\n{core}" if core else "")
    out = []
    for line in (WORK / "data.jsonl").read_text().splitlines():
        ex = json.loads(line)
        if not isinstance(ex.get("message"), str) or not isinstance(ex.get("output"), str) or not ex["message"].strip():
            continue   # an example Claude left incomplete
        turns = [t for t in ex.get("history") or [] if isinstance(t, (list, tuple)) and len(t) == 2]
        hist = "\n".join(f"{who}: {said}" for who, said in turns)
        user = "\n\n".join(x for x in (f"## Right now\n- Now: {ex.get('time', '')}\n- The robot: {ex.get('dock', 'off its dock')}",
                                       f"## Just before\n{hist}" if hist else "", f"He says: {ex['message']}") if x)
        out.append({"messages": [{"role": "system", "content": system}, {"role": "user", "content": user},
                                 {"role": "assistant", "content": ex["output"].strip()}]})
    return out


def build() -> Path:
    data = rows()
    random.Random(3).shuffle(data)
    d = WORK / "dataset"
    d.mkdir(parents=True, exist_ok=True)
    cut = max(20, len(data) // 20)
    (d / "valid.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in data[:cut]) + "\n")
    (d / "train.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in data[cut:]) + "\n")
    print(f"[planner] {len(data) - cut} training rows, {cut} for validation", flush=True)
    return d


def train(d: Path):
    base = roster.MINDS["tuesday"].base_hf
    adapters = WORK / "adapters"
    subprocess.run([sys.executable, "-m", "mlx_lm", "lora", "--model", base, "--train", "--data", str(d), "--adapter-path", str(adapters),
                    "--iters", str(ITERS), "--batch-size", "1", "--num-layers", str(LAYERS), "--learning-rate", LR,
                    "--max-seq-length", str(SEQ), "--mask-prompt", "--grad-checkpoint", "--steps-per-report", "25",
                    "--steps-per-eval", "150", "--save-every", "150"], check=True)
    from lora_export import export
    gguf = export(adapters, base, WORK / "planner-lora.gguf")
    LIVE.mkdir(parents=True, exist_ok=True)
    live = LIVE / "planner-lora.gguf"
    if live.exists():
        shutil.copy2(live, LIVE / "planner-lora.previous.gguf")
    shutil.copy2(gguf, live)
    print(f"[planner] trained and live: {live}", flush=True)


if __name__ == "__main__":
    d = build()
    if "--dry" not in sys.argv:
        train(d)
