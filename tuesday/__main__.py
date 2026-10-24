"""python -m tuesday [serve|chat|relay|robot-check|say "text" [voice]]"""
import json
import sys
import urllib.request

from . import config


def _brain() -> str:
    """Where Tuesday runs: the cloud server once TUESDAY_BRAIN_URL is set (same value the relay uses), else this Mac."""
    return config.env("TUESDAY_BRAIN_URL").rstrip("/") or f"http://{config.HOST}:{config.PORT}"


def _post(path, body):
    headers = {"Content-Type": "application/json"}
    if config.API_TOKEN:
        headers["Authorization"] = f"Bearer {config.API_TOKEN}"
    req = urllib.request.Request(_brain() + path, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())


def _get(path):
    headers = {"Authorization": f"Bearer {config.API_TOKEN}"} if config.API_TOKEN else {}
    with urllib.request.urlopen(urllib.request.Request(_brain() + path, headers=headers), timeout=60) as r:
        return json.loads(r.read())


def chat():
    print(f"Talk to Tuesday at {_brain()} (Ctrl+C to quit). Prefix /council, /quick or a day (e.g. /thursday) to choose who answers; /long for a full answer.\n")
    while True:
        try:
            msg = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not msg:
            continue
        try:
            r = _post("/chat", {"message": msg, "channel": "cli"})
            print(f"tuesday › {r['reply']}\n          ({r.get('model')} · {r.get('route')})\n")
            if r.get("pending"):  # Tuesday is thinking in the background: wait for the full answer
                import time
                while True:
                    time.sleep(2)
                    j = _get(f"/result/{r['pending']}")
                    if j.get("status") == "done":
                        print(f"tuesday › {j['reply']}\n          ({j.get('model')})\n")
                        break
        except Exception as e:
            print(f"(can't reach Tuesday at {_brain()}: {e})")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "serve":
        from .server import serve
        serve()
    elif cmd == "relay":
        from .robot.remote import run_relay
        run_relay()
    elif cmd == "chat":
        chat()
    elif cmd == "robot-check":
        from .robot.vector import make_body
        b = make_body()
        print(json.dumps(b.status(), indent=1, default=str))
        print(b.say("Hello. I am Tuesday."))
        b.close()
    elif cmd == "say":
        # Preview Tuesday's voice on this Mac: python -m tuesday say "Evening, Sir." [voice] [speaker]
        #   e.g. python -m tuesday say "Evening, Sir." en_GB-vctk-medium p288
        import os
        import subprocess
        if len(sys.argv) > 3:
            os.environ["TUESDAY_VOICE"] = sys.argv[3]
            os.environ["TUESDAY_VOICE_SPEAKER"] = sys.argv[4] if len(sys.argv) > 4 else ""
        from . import voice
        voice.VOICE = os.environ.get("TUESDAY_VOICE") or "en_US-lessac-medium"
        voice.SPEAKER = os.environ.get("TUESDAY_VOICE_SPEAKER", "")
        wav = voice.to_file(sys.argv[2] if len(sys.argv) > 2 else "Evening, Sir. Systems green, sarcasm calibrated.")
        subprocess.run(["afplay", str(wav)], check=False)
        wav.unlink(missing_ok=True)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
