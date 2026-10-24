"""Keep Vector asleep and still while Tuesday is paused (weekly retraining, or any time the service is stopped).

Without Tuesday nothing holds calm mode, so he'd wake and roam the house. This small stand-in (a few MB, no models)
connects to the robot, plays his going-to-sleep animation, and holds him quietly until Tuesday is back, then lets go.
Tuesday takes over from there (he wakes as it's next needed).

  python -m tuesday.robot.hold          (train/auto_retrain.py starts and stops it)
"""
import signal
import sys
import time
import urllib.request

from .. import config


def tuesday_back() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{config.PORT}/health", timeout=3)
        return True
    except Exception:
        return False


def main():
    import anki_vector
    from anki_vector.connection import ControlPriorityLevel
    from anki_vector.messaging import protocol
    from .vector import find_ip
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    for attempt in range(20):   # the robot may be rebooting: keep trying for a few minutes
        try:
            with anki_vector.Robot(serial=config.VECTOR_SERIAL, ip=find_ip(), cache_animation_lists=False,
                                   behavior_control_level=ControlPriorityLevel.DEFAULT_PRIORITY) as r:
                try:
                    r.anim.play_animation_trigger(protocol.AnimationTrigger(name="GoToSleepGetIn"))
                except Exception:
                    pass
                print("[hold] Vector asleep and held while Tuesday is paused", flush=True)
                while not stop["now"]:
                    time.sleep(10)
                    if tuesday_back():
                        break
            print("[hold] let go", flush=True)
            return
        except Exception as e:
            if stop["now"] or tuesday_back():
                return
            print(f"[hold] robot not reachable ({type(e).__name__}); retrying", flush=True)
            time.sleep(15)


if __name__ == "__main__":
    sys.exit(main())
