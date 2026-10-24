"""The real body: a Digital Dream Labs / Anki Vector, reached through wire-pod and the wirepod_vector_sdk
(imported as `anki_vector`). Setup steps are in tuesday/README.md, under "When the Vector arrives".

The SDK calls below follow the anki_vector API. Anything marked VERIFY should be checked against the real robot."""
import io
import re
import threading
import time
from pathlib import Path

from .. import config
from .base import Body


class VectorBody(Body):
    name = "vector"

    def __init__(self):
        try:
            import anki_vector  # noqa: F401  (provided by wirepod_vector_sdk)
        except ImportError as e:
            raise RuntimeError("wirepod_vector_sdk is not installed. Run: ./run.sh --robot-setup") from e
        self._av = __import__("anki_vector")
        self._lock = threading.RLock()
        self._robot = None
        self._voice_handlers = []
        self.last_action = time.time()   # idle docking counts from start-up, not from 1970
        from .. import privacy
        privacy.sleeping_incognito(False)   # Tuesday (re)starting means he's awake: sleep's incognito ends with it
        try:
            self.connect()
        except (Exception, SystemExit) as e:   # robot off or rebooting: stay a Vector body and keep trying in the background
            print(f"[vector] robot not reachable yet ({type(e).__name__}); will connect when it's back", flush=True)
            self._watchdog = threading.Thread(target=self._watch, daemon=True, name="vector-watchdog")
            self._watchdog.start()

    # ---------- connection ----------
    def connect(self):
        av = self._av
        kw = dict(
            behavior_control_level=None,   # connect without taking control: Vector keeps listening for "Hey Vector"
            enable_face_detection=True,
            enable_nav_map_feed=config.env("TUESDAY_VECTOR_NAVMAP") == "1",   # a constant stream: only for room mapping
            cache_animation_lists=False,
            behavior_activation_timeout=15,
        )
        if config.VECTOR_SERIAL:
            kw["serial"] = config.VECTOR_SERIAL
        # where the robot is now (its name on the network, or wire-pod's record) beats the address in .env, which goes
        # stale whenever the router hands it a new one
        self.ip = find_ip() or getattr(self, "ip", None) or config.VECTOR_IP
        if self.ip:
            kw["ip"] = self.ip
        elif config.VECTOR_NAME:   # a name makes the SDK search the network itself
            kw["name"] = config.VECTOR_NAME
        if getattr(self, "_with_control", False):   # following: control from the start (the only way faces stream)
            kw.pop("behavior_control_level", None)
        elif config.env("TUESDAY_VECTOR_CALM", "1") == "1":
            # calm mode holds the lowest control anyway: holding it from the connection's start is what keeps face
            # detection streaming (connected without control, Vector stops reporting faces after a while)
            from anki_vector.connection import ControlPriorityLevel
            kw["behavior_control_level"] = ControlPriorityLevel.RESERVE_CONTROL
        robot = av.Robot(**kw)
        try:
            robot.connect()
        except BaseException:   # never keep a half-open link around (its event loop is dead)
            try:
                robot.disconnect()
            except Exception:
                pass
            self._robot = None
            raise
        self._robot = robot
        if getattr(self, "_with_control", False):
            self._last_state = time.time()
            return
        # Vector lives his own life between commands; Tuesday takes control only while acting (see _act).
        # The SDK only turns face detection on when it connects *with* control, so turn it on here (briefly taking
        # control if the robot insists): faces drive guest mode, "who's here", and follow me.
        self._enable_faces()   # ends with _release(), which settles him (calm mode)
        self._subscribe_voice()
        # The robot's event stream (faces, pose, touch) can close on its own and the SDK never reopens it: watch the
        # robot_state events (several a second) and reconnect when they stop.
        self._last_state = time.time()
        try:
            self._robot.events.subscribe(lambda *a, **k: setattr(self, "_last_state", time.time()), av.events.Events.robot_state)
        except Exception as e:
            print(f"[vector] couldn't watch robot state: {e!r}", flush=True)
        if not getattr(self, "_watchdog", None):
            self._watchdog = threading.Thread(target=self._watch, daemon=True, name="vector-watchdog")
            self._watchdog.start()

    def _watch(self):
        backoff, net = 0, mac_address()
        while True:
            for _ in range(5 + backoff):   # a second at a time, so a network change is noticed at once
                time.sleep(1)
                if mac_address() != net:
                    break
            if (now := mac_address()) != net:   # the Mac joined another network (home, hotspot, ...): look again now
                print(f"[vector] the Mac's network changed ({net} -> {now}): finding the robot", flush=True)
                net, backoff = now, 0
                if self._robot is not None and not self.following.is_set():
                    with self._lock:
                        self._drop()
            if self._robot is None and not self.following.is_set():   # down: try again, more patiently each time
                with self._lock:
                    found = find_ip()
                    if found and found != getattr(self, "ip", None):
                        print(f"[vector] the robot is at {found} now (was {getattr(self, 'ip', None)})", flush=True)
                        self.ip = found
                    try:
                        self.connect()
                        print("[vector] reconnected", flush=True)
                        backoff = 0
                    except (Exception, SystemExit) as e:
                        backoff = min(120, max(15, backoff * 2))
                        print(f"[vector] robot unreachable ({type(e).__name__}); next try in {backoff + 5} s", flush=True)
                continue
            if self._robot is not None and not self.following.is_set():
                self._tidy()
            if self._robot is None or self.following.is_set() or time.time() - getattr(self, "_last_state", time.time()) < 10:
                continue
            if not self._lock.acquire(blocking=False):   # mid-action: not now
                continue
            self._lock.release()
            print("[vector] no news from the robot for 10 s (event stream closed?): reconnecting", flush=True)
            with self._lock:
                self._drop()
                try:
                    self.connect()
                    print("[vector] reconnected", flush=True)
                except (Exception, SystemExit) as e:
                    backoff = 15
                    print(f"[vector] reconnect failed ({type(e).__name__}); will keep trying", flush=True)

    def _tidy(self):
        """Back to the dock when there's nothing to do: off it for TUESDAY_DOCK_AFTER_MIN (10) minutes since Tuesday last
        moved or spoke, or the battery is low. Checked once a minute; never while he's busy or following."""
        now = time.time()
        if now - getattr(self, "_tidy_at", 0) < 60 or now < getattr(self, "speaking_until", 0) or getattr(self, "sleeping", False):
            return
        self._tidy_at = now
        try:
            st = self.status()
        except Exception:
            return
        if st.get("on_charger") is not False:
            return
        idle = now - max(getattr(self, "last_action", 0), getattr(self, "speaking_until", 0)) > 60 * config.env_int("TUESDAY_DOCK_AFTER_MIN", 10)
        low = st.get("battery_level") == 1
        if idle or low:
            print(f"[vector] {'battery low' if low else 'idle off the dock'}: heading back to the charger", flush=True)
            try:
                if low:
                    self.say("My battery's getting low, Sir. I'll head back to my dock.")
                self.to_charger()
            except Exception as e:
                print(f"[vector] couldn't dock: {e!r}", flush=True)

    def _enable_faces(self):
        r = self._robot
        for with_control in (False, True):
            if with_control and getattr(self, "sleeping", False):   # taking control would wake him
                return
            try:
                if with_control:
                    self._control()
                fut = r.vision.enable_face_detection(detect_faces=True, estimate_expression=False)
                if hasattr(fut, "result"):
                    fut.result(timeout=10)
                print(f"[vector] face detection on{' (took control briefly)' if with_control else ''}", flush=True)
                return
            except Exception as e:
                if with_control:
                    print(f"[vector] couldn't turn on face detection: {e!r}", flush=True)
            finally:
                if with_control:
                    self._release()

    def connected(self) -> bool:
        """True while the SDK link to the robot is up (Tuesday then speaks in its own voice and acts directly)."""
        return self._robot is not None

    def _robot_ok(self):
        if self._robot is None:
            try:
                self.connect()
            except SystemExit as e:   # the SDK exits on some failures: never let that take Tuesday down
                raise RuntimeError(f"robot unreachable: {e}") from None
        return self._robot

    def _control(self, override: bool = True):
        """Take Vector. Override priority is granted at once (speaking, moving); default priority keeps his perception
        running, which following needs (face detection pauses under override)."""
        try:
            from anki_vector.connection import ControlPriorityLevel
            level = ControlPriorityLevel.OVERRIDE_BEHAVIORS_PRIORITY if override else ControlPriorityLevel.DEFAULT_PRIORITY
            self._robot.conn.request_control(behavior_control_level=level, timeout=10.0)
        except Exception as e:
            print(f"[vector] couldn't take control: {e!r}", flush=True)

    def _release(self):
        """Hand Vector back to himself (while Tuesday holds control he ignores "Hey Vector"). Calm mode then keeps the
        lowest hold, which only switches off his idle wandering."""
        try:
            self._robot.conn.release_control(timeout=10.0)
        except Exception as e:
            print(f"[vector] couldn't release control: {e!r}", flush=True)
        self._calm()

    def _calm(self):
        """Calm mode (TUESDAY_VECTOR_CALM=1, the default): no aimless driving around. The SDK's reserve level sits below
        everything else (his wake word, Tuesday's own actions, falls and cliffs) and is meant for exactly this:
        switching off idle behaviours. He still looks around, blinks and reacts; he just doesn't wander off."""
        if config.env("TUESDAY_VECTOR_CALM", "1") != "1" or self._robot is None or self.following.is_set() or getattr(self, "sleeping", False):
            return
        try:
            from anki_vector.connection import ControlPriorityLevel
            self._robot.conn.request_control(behavior_control_level=ControlPriorityLevel.RESERVE_CONTROL, timeout=5.0)
        except Exception as e:
            print(f"[vector] couldn't settle him (calm mode): {e!r}", flush=True)

    def _act(self, fn, timeout: float = 30, exclusive: bool = True):
        """Take control, act, give Vector back. If the link died (robot restarted, Wi-Fi blip), reconnect once.
        The SDK's calls have no deadline of their own (a behaviour the robot never finishes blocks forever, and with
        it every later command): past `timeout` the link is dropped, which ends the call, and the watchdog reconnects.
        Movements take turns (exclusive); speech doesn't wait for them: he can talk while he looks or drives. Control
        is shared, and handed back only when the last action using it is done."""
        import concurrent.futures as cf
        import contextlib
        self.last_action = time.time()
        if not hasattr(self, "_woke"):
            self._woke = threading.Event()
            self._woke.set()
        was_asleep = getattr(self, "sleeping", False)
        self.sleeping = False   # acting wakes him; calm mode resumes afterwards
        if was_asleep:
            self._woke.clear()   # everything else waits until he's properly awake (see _wake)
        elif not self._woke.is_set():
            self._woke.wait(20)
        if not hasattr(self, "_holders"):
            self._holders, self._hold_lock = 0, threading.Lock()
        with self._lock if exclusive else contextlib.nullcontext():
            for attempt in range(2):
                r = self._robot_ok()
                with self._hold_lock:
                    self._holders += 1
                self._control()
                if was_asleep and attempt == 0:
                    self._wake(r)
                try:
                    pool = cf.ThreadPoolExecutor(1)
                    try:
                        return pool.submit(fn, r).result(timeout=timeout)
                    except cf.TimeoutError:
                        print(f"[vector] the robot didn't finish within {timeout:.0f} s: dropping the link", flush=True)
                        self._drop()
                        raise TimeoutError(f"the robot didn't finish within {timeout:.0f} s") from None
                    finally:
                        pool.shutdown(wait=False)
                except TimeoutError:
                    raise
                except Exception as e:
                    if attempt or not _link_lost(e):
                        raise
                    print(f"[vector] link lost ({e!r}); reconnecting", flush=True)
                    self._drop()
                finally:
                    with self._hold_lock:
                        self._holders -= 1
                        last = self._holders == 0
                    if last and self._robot is not None and not self.following.is_set():   # following keeps control
                        self._release()

    def _drop(self):
        try:
            if self._robot:
                self._robot.disconnect()
        except Exception:
            pass
        self._robot = None

    def close(self):
        try:
            if self._robot:
                self._robot.disconnect()
        except Exception:
            pass

    # ---------- interface ----------
    def status(self):
        r = self._robot_ok()
        out = {"robot": f"Vector {config.VECTOR_SERIAL or ''}".strip()}
        try:
            b = _bounded(r.get_battery_state, 5)
            b = b.result(timeout=5) if hasattr(b, "result") else b
            out.update(battery_volts=round(b.battery_volts, 2), battery_level=int(b.battery_level), charging=b.is_charging, on_charger=b.is_on_charger_platform)
        except Exception as e:
            out["battery_error"] = str(e)
        try:
            out["proximity_mm"] = r.proximity.last_sensor_reading.distance.distance_mm
        except Exception:
            pass
        try:
            out["being_touched"] = r.touch.last_sensor_reading.is_being_touched
        except Exception:
            pass
        try:
            p = r.pose
            out["pose"] = {"x": round(p.position.x), "y": round(p.position.y), "angle_deg": round(p.rotation.angle_z.degrees)}
        except Exception:
            pass
        return out

    speaking_until = 0.0   # the Mac listener ignores the microphone until then (Tuesday mustn't hear itself)
    following = threading.Event()
    _stop_follow = threading.Event()

    def follow(self, minutes: float = 3.0, on_lost=None) -> str:
        """Follow the owner: find his face, turn toward it, drive after him to ~40 cm; search by turning when he's out
        of view. Runs until stop_following(), `minutes` pass, or he can't be found. Vector holds still for nothing else
        meanwhile (he won't react to "Hey Vector"; "Hey Tuesday, stop" works through the Mac)."""
        import math
        from anki_vector.util import degrees as deg, distance_mm, speed_mmps
        from .. import privacy
        if self.following.is_set():
            return "already following"
        self._stop_follow.clear()
        self.following.set()
        end = time.time() + minutes * 60
        lost_since, searched = None, 0
        try:
            # Faces only stream to a connection that took control when it connected, so follow on one of those;
            # the usual connection (no control, so "Hey Vector" keeps working) comes back afterwards.
            with self._lock:
                self._drop()
                self._with_control = True
                self.connect()
            r = self._robot
            with self._lock:
                st = r.status
                if st.is_on_charger:
                    r.behavior.drive_off_charger()
                r.behavior.set_lift_height(0.0)
                r.behavior.set_head_angle(deg(40.0))   # from the floor, a standing person's face is high up
            while time.time() < end and not self._stop_follow.is_set():
                known = list(r.world.visible_faces)
                faces = [f for f in known if f.time_since_last_seen < 1.5]
                mine = [f for f in faces if (f.name or "").lower() in privacy.OWNER_FACES] or faces
                if not mine:
                    lost_since = lost_since or time.time()
                    if time.time() - lost_since > 2.5:   # look around for him
                        ages = ", ".join(f"{f.name or '?'} {f.time_since_last_seen:.1f}s ago" for f in known) or "none ever"
                        print(f"[follow] no face in view (faces known: {ages}): searching ({searched + 1}/8)", flush=True)
                        if searched >= 8:
                            if on_lost:
                                on_lost()
                            return "lost"
                        found = False
                        for tilt in (15.0, 40.0):   # look for someone sitting, then standing, before turning
                            with self._lock:
                                r.behavior.set_head_angle(deg(tilt))
                            time.sleep(1.5)   # face detection needs a few still frames
                            if any(f.time_since_last_seen < 1.5 for f in r.world.visible_faces):
                                found = True
                                break
                        if not found:
                            with self._lock:
                                r.behavior.turn_in_place(deg(45))
                        searched += 1
                        lost_since = time.time()
                    time.sleep(0.2)
                    continue
                lost_since, searched = None, 0
                f, me = mine[0], r.pose
                dx, dy = f.pose.position.x - me.position.x, f.pose.position.y - me.position.y
                bearing = math.degrees(math.atan2(dy, dx)) - me.rotation.angle_z.degrees
                bearing = (bearing + 180) % 360 - 180
                dist = math.hypot(dx, dy)
                height = f.pose.position.z - me.position.z
                keep = 1000 if height > 700 else 400   # standing: stay back so the face stays in view; seated: come close
                print(f"[follow] {f.name or 'someone'} at {dist:.0f} mm, {bearing:+.0f} deg, face {height:.0f} mm up", flush=True)
                with self._lock:
                    if abs(bearing) > 12:
                        r.behavior.turn_in_place(deg(bearing))
                    elif dist > keep + 100:
                        r.behavior.drive_straight(distance_mm(min(dist - keep, 400)), speed_mmps(170), should_play_anim=False)
                    else:
                        time.sleep(0.3)
            return "stopped" if self._stop_follow.is_set() else "time"
        except Exception as e:
            print(f"[follow] stopped by an error: {e!r}", flush=True)
            raise
        finally:
            self.following.clear()
            with self._lock:
                self._drop()
                self._with_control = False
                try:
                    self.connect()
                except (Exception, SystemExit) as e:
                    print(f"[vector] reconnect after following failed: {e!r}", flush=True)

    def face_check(self, seconds: float = 10.0) -> dict:
        """Diagnostics: on a following-style connection, without moving, which faces does Vector see?"""
        from anki_vector.util import degrees as deg
        seen = {}
        with self._lock:
            self._drop()
            self._with_control = True
            try:
                self.connect()
                r = self._robot
                r.behavior.set_lift_height(0.0)
                end = time.time() + seconds
                tilt = 15.0
                while time.time() < end:
                    r.behavior.set_head_angle(deg(tilt))
                    time.sleep(1.5)
                    for f in r.world.visible_faces:
                        seen[str(f.face_id)] = {"name": f.name or "?", "seen_s_ago": round(f.time_since_last_seen, 1),
                                                "x": round(f.pose.position.x), "y": round(f.pose.position.y), "z": round(f.pose.position.z)}
                    tilt = 40.0 if tilt == 15.0 else 15.0
            finally:
                self._drop()
                self._with_control = False
                self.connect()
        return seen

    def stop_following(self) -> bool:
        was = self.following.is_set()
        self._stop_follow.set()
        return was

    def say(self, text):
        """Speak, a sentence or two at a time, so he can cut in: hush() stops it at the end of the current piece (a
        couple of seconds at most), the way a person stops when interrupted. One speaker at a time."""
        if not hasattr(self, "_hushed"):
            self._hushed, self._say_lock, self.recent_said = threading.Event(), threading.Lock(), []
        with self._say_lock:
            self._hushed.clear()
            self.speaking_until = time.time() + 60
            out = ""
            try:
                for piece in _pieces(text):
                    if self._hushed.is_set():
                        print(f"[voice] interrupted; not said: {piece[:60]!r}", flush=True)
                        break
                    self.recent_said = (self.recent_said + [(time.time(), piece)])[-6:]   # the listener's echo check
                    out = self._say(piece)
            finally:
                self.speaking_until = time.time() + 0.8
            return out

    def hush(self):
        """He started talking: stop after the current piece; anything queued to be said is dropped too."""
        if hasattr(self, "_hushed"):
            self._hushed.set()

    def _say(self, text):
        from .. import voice
        if voice.enabled():
            # Tuesday's own voice (Piper), streamed to the speaker; Vector's built-in voice if that fails.
            wav = None
            try:
                wav = voice.to_file(text[:600])
                for attempt in range(3):   # wire-pod may still hold Vector for a moment after a voice request: retry
                    try:
                        self._act(lambda r: r.audio.stream_wav_file(str(wav), volume=config.env_int("TUESDAY_VOICE_VOLUME", 75)),
                                  timeout=180, exclusive=False)
                        return f'said "{text[:600]}" (Tuesday voice)'
                    except Exception as e:
                        if attempt == 2:
                            raise
                        print(f"[voice] playback failed ({e}); retrying", flush=True)
                        time.sleep(1.0 + attempt)
            except Exception as e:
                print(f"[voice] falling back to Vector's voice: {e}", flush=True)
            finally:
                if wav:
                    wav.unlink(missing_ok=True)
        try:
            self._act(lambda r: r.behavior.say_text(text[:400]), timeout=90)
        except Exception as e:
            print(f"[voice] robot unreachable, not spoken: {e}", flush=True)
            return f'not spoken (robot unreachable): "{text[:120]}"'
        return f'said "{text[:400]}"'

    def drive(self, distance_mm, speed_mmps=100):
        from anki_vector.util import distance_mm as dmm, speed_mmps as smm
        distance_mm = max(-500, min(500, distance_mm))
        def go(r):
            try:
                if r.status.is_on_charger:
                    r.behavior.drive_off_charger()
            except Exception:
                pass
            if distance_mm > 0:   # forward: look before moving; stop short of whatever is in the way
                reading = r.proximity.last_sensor_reading
                if reading.found_object:
                    room = reading.distance.distance_mm - 80
                    if room < 40:
                        raise RuntimeError("something's right in front of me")
                    distance_mm = min(distance_mm, room)
            r.behavior.drive_straight(dmm(distance_mm), smm(max(10, min(220, speed_mmps))))
        self._act(go, timeout=75)
        return f"drove {distance_mm:.0f} mm"

    def turn(self, degrees):
        from anki_vector.util import degrees as deg
        self._act(lambda r: r.behavior.turn_in_place(deg(degrees)))
        return f"turned {degrees:.0f}°"

    def head(self, degrees):
        from anki_vector.util import degrees as deg
        d = max(-22, min(45, degrees))
        self._act(lambda r: r.behavior.set_head_angle(deg(d)))
        return f"head at {d:.0f}°"

    def lift(self, height):
        h = max(0.0, min(1.0, height))
        self._act(lambda r: r.behavior.set_lift_height(h))
        return f"lift at {h:.2f}"

    def look(self):
        from anki_vector.util import degrees as deg

        def snap(r):
            r.behavior.set_lift_height(0.0)          # the lift otherwise blocks the bottom of the view
            r.behavior.set_head_angle(deg(10.0))     # eye level for someone at the desk
            img = r.camera.capture_single_image()
            buf = io.BytesIO()
            img.raw_image.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        data = self._act(snap)
        threading.Thread(target=self.refresh_faces, daemon=True).start()
        if config.env("TUESDAY_KEEP_PHOTOS") == "1":   # off by default: nothing reads them, so they'd only pile up
            Path(config.DATA / "camera" / f"{time.strftime('%Y%m%d-%H%M%S')}.jpg").write_bytes(data)
        return data

    def faces(self):
        r = self._robot_ok()
        out = []
        try:
            for f in r.world.visible_faces:
                out.append({"name": f.name or "unknown", "face_id": f.face_id, "expression": getattr(f, "expression", None)})
        except Exception:
            pass
        return out

    # ---------- senses and presence (awareness.py) ----------
    def senses(self) -> dict:
        """What the robot feels right now, from the state it streams anyway (no request to the robot): faces in view
        with their distance, touch, being held or picked up, the dock, a cliff, and what's straight ahead."""
        r = self._robot
        if r is None:
            return {}
        out = {"faces": []}
        try:
            me = r.pose.position
            for f in r.world.visible_faces:
                d = None
                try:
                    p = f.pose.position
                    d = round(((p.x - me.x) ** 2 + (p.y - me.y) ** 2) ** 0.5)
                except Exception:
                    pass
                out["faces"].append({"name": f.name or "", "id": f.face_id, "distance_mm": d,
                                     "expression": getattr(f, "expression", None)})
        except Exception:
            pass
        for key, get in (("touched", lambda: r.touch.last_sensor_reading.is_being_touched),
                         ("held", lambda: r.status.is_being_held), ("picked_up", lambda: r.status.is_picked_up),
                         ("on_charger", lambda: r.status.is_on_charger), ("cliff", lambda: r.status.is_cliff_detected),
                         ("moving", lambda: r.status.is_robot_moving),
                         ("ahead_mm", lambda: r.proximity.last_sensor_reading.distance.distance_mm
                          if r.proximity.last_sensor_reading.found_object else None),
                         ("head_deg", lambda: round(r.head_angle_rad * 57.3)),
                         ("x", lambda: round(r.pose.position.x)), ("y", lambda: round(r.pose.position.y)),
                         ("heading", lambda: round(r.pose.rotation.angle_z.degrees))):
            try:
                out[key] = get()
            except Exception:
                pass
        return out

    def refresh_faces(self):
        """Face recognition back on. Taking a photo (and some of Vector's own behaviours) resets his vision settings,
        after which faces silently stop being reported: so after every photo, and every half minute anyway."""
        r = self._robot
        if r is None:
            return
        try:
            fut = r.vision.enable_face_detection(detect_faces=True, estimate_expression=False)
            if hasattr(fut, "result"):
                fut.result(timeout=5)
        except Exception as e:
            print(f"[vector] couldn't refresh face detection: {e!r}", flush=True)

    def attend(self, face_id: int | None = None):
        """Look at him: head up and, if a face is in view, turn towards it. What a person does on hearing their name."""
        def go(r):
            from anki_vector.util import degrees as deg
            face = next((f for f in r.world.visible_faces if face_id is None or f.face_id == face_id), None)
            if face is not None:
                r.behavior.turn_towards_face(face)
            r.behavior.set_head_angle(deg(30))
        self._act(go, timeout=8)

    def peek(self) -> bytes | None:
        """A still from the camera as the robot is, without moving it (for noticing things, not for answering)."""
        def snap(r):
            img = r.camera.capture_single_image()
            buf = io.BytesIO()
            img.raw_image.save(buf, format="JPEG", quality=80)
            return buf.getvalue()
        try:
            return _bounded(lambda: snap(self._robot_ok()), 8)
        except Exception as e:
            print(f"[vector] peek failed: {e!r}", flush=True)
            return None
        finally:
            threading.Thread(target=self.refresh_faces, daemon=True).start()

    def approach(self, stop_mm: int = 300) -> str:
        """'Come here': find his face, turn to it, and drive towards him in short steps, watching the distance sensor,
        stopping about 30 cm away (or at anything in the way). Never drives blind."""
        from anki_vector.util import degrees as deg, distance_mm as dmm, speed_mmps as smm

        def go(r):
            try:
                if r.status.is_on_charger:
                    r.behavior.drive_off_charger()
            except Exception:
                pass
            r.behavior.set_head_angle(deg(25))
            face = None
            for turn in (0, 45, -90, 135, -180):
                face = next(iter(r.world.visible_faces), None)
                if face is not None:
                    break
                if turn:
                    r.behavior.turn_in_place(deg(turn))
                time.sleep(1.2)
            if face is None:
                return "couldn't see him"
            r.behavior.turn_towards_face(face)
            for _ in range(8):
                reading = r.proximity.last_sensor_reading
                ahead = reading.distance.distance_mm if reading.found_object else 1000
                if ahead <= stop_mm + 40 or r.status.is_cliff_detected:
                    return "arrived"
                r.behavior.drive_straight(dmm(min(200, ahead - stop_mm)), smm(120))
                face = next(iter(r.world.visible_faces), None)
                if face is not None:
                    r.behavior.turn_towards_face(face)   # he may have moved
            return "arrived"
        return self._act(go, timeout=60)

    def enroll_face(self, name):
        # Same as saying "Hey Vector, my name is <name>": Vector looks at the person and learns their face.
        self._act(lambda r: r.behavior.app_intent(intent="intent_meet_victor", param=name))  # VERIFY
        return f"started face enrollment for {name}; look at Vector for a few seconds"

    def known_faces(self):
        r = self._robot_ok()
        try:
            res = r.faces.request_enrolled_names()
            res = res.result() if hasattr(res, "result") else res
            return [f.name for f in res.faces]
        except Exception:
            return []

    def animate(self, trigger):
        from anki_vector.messaging import protocol
        # by name, straight to the robot: asking it for the full trigger list first takes so long the link times out
        self._act(lambda r: r.anim.play_animation_trigger(protocol.AnimationTrigger(name=trigger)))
        return f"played {trigger}"

    def off_charger(self):
        self._act(lambda r: r.behavior.drive_off_charger(), timeout=45)
        return "drove off the charger"

    def sleep(self):
        """Good night: onto the dock if he isn't there, then asleep the way Vector sleeps: eyes close (GoToSleepGetIn),
        then the snoozing loop (GoToSleepSleeping), until Tuesday needs him again (any action wakes him with
        GoToSleepOff). Vector's own "go to sleep" can't be triggered from outside (wire-pod's app intent is ignored),
        so Tuesday plays it. While he sleeps Tuesday holds him, so "Hey Vector" waits: "Tuesday, wake up" works."""
        try:
            on = self.status().get("on_charger")
        except Exception:
            on = None
        if on is False:
            self.to_charger()
        self.sleeping = True
        from .. import privacy
        privacy.sleeping_incognito(True)   # asleep = incognito: nothing seen, heard or remembered
        threading.Thread(target=self._sleeper, daemon=True, name="sleeper").start()
        return "asleep on the dock"

    def _sleeper(self):
        from anki_vector.connection import ControlPriorityLevel
        from anki_vector.messaging import protocol
        play = lambda r, name, loops=1: r.anim.play_animation_trigger(protocol.AnimationTrigger(name=name), loop_count=loops)
        first = True
        while getattr(self, "sleeping", False):
            try:
                r = self._robot_ok()
                r.conn.request_control(behavior_control_level=ControlPriorityLevel.DEFAULT_PRIORITY, timeout=10.0)
                if first:
                    _bounded(lambda: play(r, "GoToSleepGetIn"), 15)
                    print("[vector] asleep", flush=True)
                    first = False
                # then quietly: just held still (the snooze animation snores; drawing on his screen is suspected of
                # crashing him once, so nothing more until that's ruled out)
                for _ in range(30):
                    if not getattr(self, "sleeping", False):
                        break
                    time.sleep(1)
            except Exception as e:
                if getattr(self, "sleeping", False):   # not woken on purpose: the link hiccuped, keep sleeping
                    print(f"[vector] sleep interrupted ({type(e).__name__}); trying again in a minute", flush=True)
                    for _ in range(60):   # gently: hammering a robot that's restarting can keep it from coming up
                        if not getattr(self, "sleeping", False):
                            break
                        time.sleep(1)

    def wake(self):
        """'Wake up': eyes open (Vector's waking-up animation), head up, ready. Fine if he wasn't asleep."""
        from anki_vector.util import degrees as deg

        def go(r):   # (the waking-up animation itself plays as he's first needed: see _act)
            r.behavior.set_head_angle(deg(25))
        self._act(go, timeout=20)
        return "awake"

    def _wake(self, r):
        """Woken by Tuesday needing him: eyes open, he gets up, eyes back to normal, all the way through, before
        anything else moves him (a head turn mid-animation left his eyes stuck half-open)."""
        from anki_vector.messaging import protocol
        try:
            for name in ("GoToSleepOff", "WakeupGetout", "NeutralFace"):
                try:
                    _bounded(lambda n=name: r.anim.play_animation_trigger(protocol.AnimationTrigger(name=n)), 10)
                except Exception as e:
                    print(f"[vector] wake {name}: {e!r}", flush=True)
            print("[vector] awake", flush=True)
        finally:
            from .. import privacy
            privacy.sleeping_incognito(False)   # back on the record, unless he'd chosen incognito himself
            self._woke.set()

    def to_charger(self):
        self._act(lambda r: r.behavior.drive_on_charger(), timeout=180)
        return "drove onto the charger"

    def scan_room(self):
        """Look around in a slow circle and take a few careful steps, then render Vector's nav map."""
        from anki_vector.util import degrees as deg, distance_mm as dmm, speed_mmps as smm

        def explore(r):
            try:
                r.behavior.drive_off_charger()
            except Exception:
                pass
            for _ in range(3):
                for _ in range(8):
                    r.behavior.turn_in_place(deg(45))
                    time.sleep(0.4)
                try:
                    clear = r.proximity.last_sensor_reading.distance.distance_mm
                except Exception:
                    clear = 300
                if clear > 250:
                    r.behavior.drive_straight(dmm(min(250, clear - 100)), smm(80))
            return r.nav_map.latest_nav_map  # VERIFY: requires enable_nav_map_feed=True

        nav = self._act(explore, timeout=900)
        return self._render_map(nav)

    def _render_map(self, nav):
        try:
            from PIL import Image
        except ImportError:
            return {"ok": False, "summary": "Install pillow to render maps."}
        if nav is None:
            return {"ok": False, "summary": "Vector returned no navigation map yet."}
        av = self._av
        size = int(nav.size)
        res = 10  # mm per pixel
        n = max(1, size // res)
        img = Image.new("RGB", (n, n), (20, 20, 24))
        colors = {"CLEAR_OF_OBSTACLE": (90, 170, 90), "CLEAR_OF_CLIFF": (120, 190, 120), "OBSTACLE_UNRECOGNIZED": (220, 90, 70), "OBSTACLE_CUBE": (230, 180, 60), "OBSTACLE_CHARGER": (90, 140, 230), "CLIFF": (40, 40, 40), "VISION_BORDER": (180, 180, 180)}
        cx, cy = nav.center.x, nav.center.y
        counts = {}
        for i in range(n):
            for j in range(n):
                x = cx - size / 2 + i * res
                y = cy - size / 2 + j * res
                try:
                    c = nav.get_content(x, y)
                except Exception:
                    continue
                key = getattr(c, "name", str(c)).split(".")[-1]
                counts[key] = counts.get(key, 0) + 1
                if key in colors:
                    img.putpixel((i, n - 1 - j), colors[key])
        path = config.DATA / "maps" / f"room-{time.strftime('%Y%m%d-%H%M%S')}.png"
        img = img.resize((n * 4, n * 4), Image.NEAREST)
        img.save(path)
        explored_m2 = (counts.get("CLEAR_OF_OBSTACLE", 0) + counts.get("CLEAR_OF_CLIFF", 0)) * (res / 1000) ** 2
        return {"ok": True, "image": str(path), "summary": f"Mapped about {explored_m2:.1f} m² of floor; cells: {counts}"}

    def show_on_face(self, png, seconds=5):
        from PIL import Image
        img = Image.open(io.BytesIO(png)).convert("RGB").resize((184, 96))
        data = self._av.screen.convert_image_to_screen_data(img)
        self._act(lambda r: r.screen.set_screen_with_image_data(data, seconds))
        return "shown on face"

    # ---------- voice ----------
    def on_voice(self, handler):
        self._voice_handlers.append(handler)

    def _subscribe_voice(self):
        """Wire-pod turns 'Hey Vector, ...' into intents. Unknown questions are better routed through
        wire-pod's knowledge-graph setting pointed at Tuesday (see README). VERIFY event names on arrival.
        Off by default: these events carry intent data (e.g. built-in commands), not what was said, so speech
        reaches Tuesday through the wire-pod plugin instead. TUESDAY_VECTOR_INTENTS=1 turns this on for testing."""
        if config.env("TUESDAY_VECTOR_INTENTS") != "1":
            return
        try:
            Events = self._av.events.Events

            def on_intent(robot, event_type, event, *a):
                text = getattr(event, "json_data", "") or str(event)
                for h in self._voice_handlers:
                    try:
                        h(text)
                    except Exception as e:
                        print("voice handler error", e)

            self._robot.events.subscribe(on_intent, Events.user_intent)
        except Exception as e:
            print("Could not subscribe to Vector voice intents:", e)


def _link_lost(e: Exception) -> bool:
    text = f"{type(e).__name__} {e}".lower()
    return any(w in text for w in ("unavailable", "connection", "deadline", "not connected", "eof", "socket", "closed", "vectornotfound"))


_FACE = {}


def _sleeping_face():
    """Vector's eyes, closed: two soft arcs in his eye colour on a black face (made once)."""
    if "data" not in _FACE:
        from PIL import Image, ImageDraw
        from anki_vector.screen import convert_image_to_screen_data
        img = Image.new("RGB", (184, 96), (0, 0, 0))
        d = ImageDraw.Draw(img)
        for x in (32, 112):   # left and right eye, closed: a gentle downward curve
            d.arc([x, 34, x + 40, 62], start=20, end=160, fill=(0, 200, 170), width=5)
        _FACE["data"] = convert_image_to_screen_data(img)
    return _FACE["data"]


def _pieces(text: str, limit: int = 140) -> list[str]:
    """Sentences, joined while short: natural chunks to speak one after another."""
    parts = [x for x in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if x]
    out = []
    for x in parts:
        if out and len(out[-1]) + len(x) < limit // 2:
            out[-1] += " " + x
        else:
            out.append(x)
    return out or [text]


def _bounded(fn, timeout: float):
    """Call an SDK function that has no deadline of its own, giving up after `timeout` seconds (a half-dead link
    otherwise blocks the caller forever: Tuesday stopped answering once while asking the battery level)."""
    import concurrent.futures as cf
    pool = cf.ThreadPoolExecutor(1)
    try:
        return pool.submit(fn).result(timeout=timeout)
    except cf.TimeoutError:
        raise TimeoutError(f"the robot didn't answer within {timeout:.0f} s") from None
    finally:
        pool.shutdown(wait=False)


def find_ip() -> str | None:
    """Where the robot is on the current network: its own mDNS name (Vector-XXXX.local, through the system resolver,
    which can't crash Tuesday the way the SDK's zeroconf did), then the address wire-pod last saw. So a new router,
    a new DHCP lease or a phone hotspot needs no change in .env."""
    import json
    if config.VECTOR_NAME:
        import concurrent.futures as cf
        import socket
        try:   # the system resolver answers .local names over mDNS; bounded, so a missing robot never stalls the watchdog
            ip = cf.ThreadPoolExecutor(1).submit(socket.gethostbyname, f"{config.VECTOR_NAME}.local").result(timeout=4)
            if ip and not ip.startswith(("0.", "169.254.")):
                return ip
        except Exception:
            pass
    try:
        info = json.loads((Path.home() / "Library/Application Support/wire-pod/jdocs/botSdkInfo.json").read_text())
        for r in info.get("robots", []):
            if not config.VECTOR_SERIAL or r.get("esn") == config.VECTOR_SERIAL:
                return r.get("ip_address") or None
    except Exception:
        pass
    return config.VECTOR_IP or None


def mac_address() -> str:
    """The Mac's current IPv4 address on the network it uses: changes when it joins another network. A UDP socket's
    route lookup sends nothing and starts no process (forking next to gRPC every second spams its log)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))   # documentation address: never contacted, only routed
            return s.getsockname()[0]
    except OSError:
        return ""


def make_body():
    if config.ROBOT == "remote":
        from .remote import RemoteBody
        return RemoteBody()
    if config.ROBOT == "vector":
        try:
            return VectorBody()
        except (Exception, SystemExit) as e:   # the SDK calls sys.exit() on some connection failures
            print(f"[tuesday] Vector unavailable ({e!r}); using the mock body until Tuesday restarts.", flush=True)
    from .mock import MockBody
    return MockBody()
