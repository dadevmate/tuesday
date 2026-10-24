"""Weekly self-improvement: fold new answers, memories and conversations into the minds' weights, safely.

1. Skip unless the Mac is on power and there is new data since the last run (or --force).
2. With TUESDAY_SYNC_SERVER=<user>@<server> in tuesday/.env, pull the live memories and conversations from the server first.
3. Ingest new questionnaire answers (train/ingest_answers.py).
4. This week's minds (Tuesday and Friday every week, plus one other specialist in rotation; all seven with --all):
   fine-tune each into `<mind>-candidate` (the live mind is untouched), then test it (train/evaluate.py).
5. Promote a candidate only if it scores at least 80% and no worse than the live mind; upload promoted minds to the server.

Runs from a LaunchAgent (./run.sh --install-autotrain), Saturdays 02:00; macOS runs a missed job on next wake.
  --minds a,b       train exactly these minds
  --adopt a,b       no training: finish these minds from their last run (a shared mind's adapter is exported for
                    Tuesday Core; any other mind is imported from its GGUF) and test them
"""
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "train"))
from tuesday import config  # noqa: E402

STATE = config.DATA / "training" / "auto-retrain.json"
ROTATION = ["monday", "wednesday", "thursday", "saturday", "sunday"]
SERVER = config.env("TUESDAY_SYNC_SERVER")
MIN_SCORE = 0.8


def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M')}] {msg}"
    print(line, flush=True)
    with (config.DATA / "logs" / "auto-retrain.log").open("a") as f:
        f.write(line + "\n")


def notify(msg: str):
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Tuesday"'], check=False)


def on_power() -> bool:
    return "AC Power" in subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout


def pull_from_server():
    """The cloud Tuesday is the source of truth for memories and conversations."""
    sh = lambda c: subprocess.run(c, shell=True, check=True)
    sh(f"ssh {SERVER} 'cd ~/tuesday/deploy && rm -rf /tmp/td && docker compose cp tuesday:/data /tmp/td'")
    sh(f"rsync -a {SERVER}:/tmp/td/memories.jsonl {config.DATA}/")
    sh(f"rsync -a {SERVER}:/tmp/td/conversations/ {config.DATA}/conversations/")


def data_hash() -> str:
    h = hashlib.sha256()
    for f in [config.DATA / "memories.jsonl", config.DATA / "profile.md", config.DATA / "training" / "qa.jsonl", config.DATA / "questionnaire-2.md",
              *sorted((config.DATA / "conversations").glob("*.jsonl"))]:
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()


def main():
    force = "--force" in sys.argv
    (config.DATA / "logs").mkdir(exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not on_power() and not force:
        return log("skipped: on battery")
    if SERVER:
        try:
            pull_from_server()
            log(f"pulled memories and conversations from {SERVER}")
        except Exception as e:
            return log(f"skipped: could not reach {SERVER} ({e})")
    digest = data_hash()
    if digest == state.get("data_hash") and not force:
        return log("skipped: nothing new since the last training")
    try:
        import ingest_answers
        ingest_answers.main()
    except SystemExit:
        pass
    week = int(time.strftime("%V"))
    arg = lambda flag: [x for x in sys.argv[sys.argv.index(flag) + 1].split(",") if x] if flag in sys.argv else []
    adopt = arg("--adopt")
    minds = arg("--minds") or (["tuesday", "friday", "sunday", "wednesday", "monday", "thursday", "saturday"] if "--all" in sys.argv
                               else [] if adopt else ["tuesday", "friday", ROTATION[week % len(ROTATION)]])
    minds = [m for m in minds if m not in adopt]
    log(f"training this week: {', '.join(minds) or 'none'}" + (f"; adopting {', '.join(adopt)}" if adopt else ""))
    # Training needs most of the 16 GB: pause the Tuesday service (and unload the minds) while it runs.
    plist = Path.home() / "Library" / "LaunchAgents" / "com.tuesday.assistant.plist"
    paused = plist.exists() and subprocess.run(["launchctl", "unload", str(plist)], capture_output=True).returncode == 0
    from tuesday import core
    core.stop()
    for name in ("qwen3:8b", "tuesday", "friday", "sunday", "monday", "wednesday", "ministral-3:8b", "ministral-3:3b"):
        subprocess.run(["ollama", "stop", name], capture_output=True)
    # With Tuesday paused nothing keeps the robot calm: a tiny stand-in keeps him asleep on his dock meanwhile
    hold = None
    if paused and config.env("TUESDAY_ROBOT", "") == "vector":
        hold = subprocess.Popen([sys.executable, "-m", "tuesday.robot.hold"], cwd=str(ROOT),
                                stdout=(config.DATA / "logs" / "hold.log").open("a"), stderr=subprocess.STDOUT)
        log("robot held asleep while training")
    try:
        _train(minds, state, digest, adopt)
    finally:
        if hold:
            hold.terminate()   # let go just before Tuesday comes back
            try:
                hold.wait(timeout=20)
            except subprocess.TimeoutExpired:
                hold.kill()
        if paused:
            subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True)
            log("Tuesday service restarted")


def _train(minds: list[str], state: dict, digest: str, adopt: list[str] = ()):
    import build_core
    import evaluate
    from tuesday import core, roster
    from tuesday.minds import local
    promoted, kept = [], []
    for mind in list(adopt) + minds:
        shared = roster.MINDS[mind].shared
        cand = f"core:{mind}@{build_core.lora_file(mind)}" if shared else f"{mind}-candidate"
        try:
            if mind in adopt:   # already trained: just finish it
                build_core.export_lora(mind) if shared else build_core.import_candidate(mind, cand)
            else:
                build_core.finetune(mind, cand)
        except Exception as e:
            log(f"[{mind}] training failed: {e}")
            kept.append(mind)
            continue
        score = (lambda m: evaluate.run(m, verbose=False)) if mind == "tuesday" else (lambda m, n=mind: evaluate.check_mind(n, m, verbose=False))
        live_model = roster.MINDS[mind].model   # the live mind (or its untrained base on a first run)
        if mind in adopt and mind in local.installed_names():
            live_model = mind                   # adopting: compare against the fused model it replaces
        try:
            live, new = score(live_model), score(cand)
        finally:
            core.stop()                         # never hold the 8B in memory while the next mind trains
        log(f"[{mind}] test: live {live['passed']}/{live['total']}, candidate {new['passed']}/{new['total']}")
        for f in new["failures"]:
            log(f"[{mind}]   candidate failed: {f}")
        # Privacy is never traded for score: one privacy failure and the candidate stays out.
        if any(re.search(r"private leak|shared feelings|private spoken", f) for f in new["failures"]):
            log(f"[{mind}] candidate failed a privacy test: not promoted")
            kept.append(mind)
            if not shared:
                subprocess.run(["ollama", "rm", cand], check=False, capture_output=True)
            continue
        # An adopted adapter is the same training as the fused model it replaces, so one case of quantization noise is allowed.
        if new["score"] >= live["score"] and new["score"] >= min(MIN_SCORE, live["score"]) or \
                (mind in adopt and new["score"] >= min(MIN_SCORE, live["score"] - 1 / max(1, live["total"]))):
            if shared:
                build_core.save_live(mind)
            else:
                subprocess.run(["ollama", "cp", mind, f"{mind}-previous"], check=False, capture_output=True)
                subprocess.run(["ollama", "cp", cand, mind], check=True)
                build_core.save_live(mind)
            promoted.append(mind)
        else:
            kept.append(mind)
        if not shared:
            subprocess.run(["ollama", "rm", cand], check=False, capture_output=True)
    state.update({"last_run": time.strftime("%Y-%m-%d %H:%M"), "data_hash": digest, "promoted": promoted, "kept": kept})
    msg = f"Retrained {', '.join(list(adopt) + minds)}: promoted {', '.join(promoted) or 'none'}" + (f"; kept the current {', '.join(kept)}" if kept else "") + "."
    if SERVER and promoted:
        r = subprocess.run([str(ROOT / "deploy" / "push-model.sh"), SERVER, *promoted])
        msg += " Uploaded to the server." if r.returncode == 0 else " Upload to the server FAILED; see the log."
    log(msg)
    notify(msg)
    STATE.write_text(json.dumps(state, indent=1))


if __name__ == "__main__":
    main()
