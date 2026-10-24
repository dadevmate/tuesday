"""'Hey Tuesday' on the Mac's microphone, and follow-ups without any wake word.

The Vector's own wake word ("Hey Vector") is fixed in its firmware. So Tuesday also listens on the Mac:
1. No fixed wake phrase: every sentence is followed offline (Vosk, WirePod's English model on this Mac); any sentence
   that mentions Tuesday, anywhere in it, goes to Tuesday. Just "Tuesday" (or "hey Tuesday") on its own waits for
   the question. Tuesday decides whether it was really talking to it (or about Tuesday the day).
2. The words: the sentence is transcribed offline by Whisper (small.en, faster-whisper), far more accurate than
   Vosk; Vosk decides where each sentence starts and ends.
3. The answer: Tuesday handles it like any spoken request and replies through the robot, in its own voice.
4. Follow-ups: after each answer it listens ~7 s more, no wake word needed, then goes back to waiting.
While the robot is speaking, the microphone input is ignored (so Tuesday never answers itself).
Nothing is recorded or stored; audio lives only in memory for the second it's being transcribed.

macOS gives background services no microphone, so this runs inside "Tuesday Listener" (vendor/TuesdayListener.app,
built by ./run.sh --install-listener), which macOS asks you to allow once, and talks to Tuesday over 127.0.0.1:
  python -m tuesday.listener

  TUESDAY_LISTEN=0           off
  TUESDAY_FOLLOWUP_S=7       seconds to wait for a follow-up after each answer (0 = off)
"""
import json
import queue
import re
import threading
import time
from pathlib import Path

from . import config

MODEL = Path(config.env("TUESDAY_VOSK_MODEL", str(Path.home() / "Library/Application Support/wire-pod/vosk/models/en-US/model")))
RATE = 16000
WAKE = re.compile(r"\b(?:(?:hey|hi|okay|ok|yo) )?tuesday\b")   # "Tuesday, ..." on its own works too
FOLLOWUP_S = float(config.env("TUESDAY_FOLLOWUP_S", "15"))   # a conversation doesn't end at the first pause
FOLLOWUP_CLARITY = -0.6   # without its name, only clearly heard speech counts as talking to Tuesday
UNCLEAR = -0.75            # below this, Tuesday asks him to say it again instead of guessing
QUESTION_S = 8.0     # longest pause allowed before the question after the wake word
state = {"status": "off", "last": ""}


def enabled() -> bool:
    return config.env("TUESDAY_LISTEN", "1") == "1" and MODEL.exists()


def start(tuesday):
    if not enabled():
        return
    threading.Thread(target=_run, args=(tuesday,), daemon=True, name="listener").start()


class Remote:
    """Tuesday as seen from the listener app: the local HTTP API."""
    def __init__(self):
        self.url = f"http://127.0.0.1:{config.PORT}"

    def _req(self, path, body=None, timeout=10):
        import urllib.request
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    answered = 0.0

    def speaking(self) -> bool:
        try:
            s = self._req("/listener/speaking", timeout=2)
            self.answered = s.get("answered", 0)
            self.said = s.get("said", [])
            return s["now"] < s["until"]
        except Exception:
            return False

    said: list = []

    def cue(self):
        try:
            self._req("/listener/cue", {}, timeout=2)
        except Exception:
            pass

    def ambient(self):
        try:
            self._req("/listener/ambient", {}, timeout=2)
        except Exception:
            pass

    def handle(self, text, overheard=False, voice=None, clarity=None):
        # A longer answer may still be on its way (r["pending"]): don't wait for it. He can keep talking meanwhile; it's
        # spoken when ready, and a follow-up window opens after it like after any answer.
        return self._req("/chat", {"message": text, "channel": "voice", "overheard": overheard, "voice": voice,
                                   "unclear": clarity is not None and clarity < UNCLEAR}, timeout=300)


def _speaking(tuesday) -> bool:
    if isinstance(tuesday, Remote):
        return tuesday.speaking()
    return time.time() < getattr(tuesday.body, "speaking_until", 0)


def _run(tuesday):
    try:
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model, SetLogLevel
    except ImportError as e:
        state["status"] = f"off: {e}"
        return
    SetLogLevel(-1)
    model = Model(str(MODEL))
    threading.Thread(target=lambda: _transcribe([b"\0\0" * 8000]), daemon=True).start()   # load Whisper now, not on the first question
    audio: queue.Queue = queue.Queue()

    import audioop
    stream = {"levels": [], "last": time.time()}

    def callback(indata, frames, t, status):
        b = bytes(indata)
        stream["last"] = time.time()
        lv = stream["levels"]
        if len(lv) < 40:   # the first ~10 s of each stream: is there any sound? (macOS gives silence without permission)
            lv.append(audioop.rms(b, 2))
            if len(lv) == 40:
                peak = max(lv)
                state["mic_peak"] = peak
                _log(f"[listener] microphone check: peak level {peak}" + (" (silent: allow the microphone for Tuesday Listener "
                      "in System Settings > Privacy & Security > Microphone)" if peak == 0 else " (OK)"), flush=True)
        audio.put(b)

    while True:
        try:
            stream["levels"], stream["last"] = [], time.time()
            with sd.RawInputStream(samplerate=RATE, blocksize=4000, dtype="int16", channels=1, callback=callback):
                state["status"] = "listening for sentences that mention Tuesday"
                _log("[listener] listening for any sentence that mentions Tuesday, on the Mac microphone", flush=True)
                _loop(tuesday, model, KaldiRecognizer, audio, stream)
        except Exception as e:   # microphone stuck or unavailable (device change, audio error): reset audio and reopen
            state["status"] = f"microphone unavailable: {e}"
            _log(f"[listener] {state['status']}; reopening", flush=True)
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
            _fresh(audio)
            time.sleep(2)


class _Stuck(Exception):
    pass


def _get(audio, stream, timeout=0.5):
    """Next chunk of sound. A microphone that has gone quiet for 5 s (no chunks at all) is stuck: start it again."""
    try:
        return audio.get(timeout=timeout)
    except queue.Empty:
        if time.time() - stream["last"] > 5:
            raise _Stuck("no sound from the microphone for 5 s")
        return None


def _log(msg, flush=True):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=flush)


def _fresh(audio):
    while not audio.empty():
        audio.get_nowait()


def _loop(tuesday, model, KaldiRecognizer, audio, stream):
    """Every sentence said near the Mac is followed (offline, nothing kept). One that mentions Tuesday anywhere ("how's
    the space industry looking today, Tuesday?", "Tuesday, lights off", "what do you think, Tuesday") goes to Tuesday
    whole; Tuesday itself judges whether it was really meant for it (not "my exam is on Tuesday" to someone else)."""
    from collections import deque
    rec = KaldiRecognizer(model, RATE)   # full vocabulary: finds where each sentence starts and ends
    seen_answer = getattr(tuesday, "answered", 0)
    before = deque(maxlen=6)   # ~1.5 s: speech is noticed a moment after it starts, so keep what came just before
    buf, cued = [], False
    while True:
        data = _get(audio, stream)
        if data is None:
            continue
        talking = _speaking(tuesday)   # the robot is speaking: he may cut in (any words that aren't its own echo)
        # The robot just answered something (asked via "Hey Vector" too): listen for a follow-up, no wake word needed.
        answered = getattr(tuesday, "answered", 0)
        if FOLLOWUP_S > 0 and answered > seen_answer:
            seen_answer = _conversation(tuesday, model, KaldiRecognizer, audio, stream, None, answered)
            rec.Reset()
            buf, cued = [], False
            continue
        if buf:
            buf.append(data)
        else:
            before.append(data)
        if rec.AcceptWaveform(data):
            heard = json.loads(rec.Result()).get("text", "")
            sentence, buf, cued = (buf or list(before)), [], False
            before.clear()
            if talking and len(heard.split()) >= 2:   # words while the robot talks: its echo, or him cutting in?
                text = _transcribe(sentence, keep_name=True)
                if text and not _echo(text, tuesday):
                    _log(f"[listener] interrupted by: {text!r}", flush=True)
                    question = _addressed(text) or text
                    seen_answer = _conversation(tuesday, model, KaldiRecognizer, audio, stream, question, seen_answer,
                                                overheard=not MENTION.search(text.lower()))
                    rec.Reset()
                    _fresh(audio)
                continue
            # Vosk only finds where sentences start and end; it often mishears the name ("'s day"). Whisper, which
            # knows "Tuesday", reads every sentence and decides. Sentences without the name are dropped on the spot.
            if not heard.strip():
                continue
            text = _transcribe(sentence, keep_name=True)
            if not text or not MENTION.search(text.lower()):
                if text and len(text.split()) >= 3 and hasattr(tuesday, "ambient"):   # people talking, not to Tuesday
                    tuesday.ambient()
                continue
            question = _addressed(text)
            overheard = question == text   # its name mid-sentence: maybe him talking about Tuesday, not to it
            if not question:   # just "Hey Tuesday": the question comes next
                _cue(tuesday)
                question = _hear(model, KaldiRecognizer, audio, QUESTION_S, tuesday, stream)
            if question:
                seen_answer = _conversation(tuesday, model, KaldiRecognizer, audio, stream, question, seen_answer, overheard)
            rec.Reset()
            _fresh(audio)
        else:
            partial = json.loads(rec.PartialResult()).get("partial", "")
            if partial and not buf:
                buf = list(before) + [data]
            if not cued and MENTION.search(partial):
                cued = True
                _cue(tuesday)   # he's looking up while you finish the sentence
            if len(buf) > 30 * RATE * 2 // 8000:   # a 30-second monologue isn't a question for Tuesday: start over
                rec.Reset()
                buf, cued = [], False


def _conversation(tuesday, model, KaldiRecognizer, audio, stream, question, seen_answer, overheard=False) -> float:
    """Answer, then keep listening for follow-ups (no name needed) until he goes quiet."""
    if question is None:   # the robot answered something asked another way: a follow-up may come
        _fresh(audio)
        question = _hear(model, KaldiRecognizer, audio, FOLLOWUP_S, tuesday, stream)
    while question:
        answered_before = getattr(tuesday, "answered", 0)
        _answer(tuesday, question, overheard)
        if overheard and getattr(tuesday, "answered", 0) == answered_before:
            break   # it wasn't for Tuesday, and Tuesday stayed quiet: no follow-up window
        overheard = False
        if FOLLOWUP_S <= 0:
            break
        seen_answer = max(seen_answer, getattr(tuesday, "answered", 0))
        _log(f"[listener] listening for a follow-up ({FOLLOWUP_S:.0f} s after it stops talking; cutting in works too)", flush=True)
        question = _hear(model, KaldiRecognizer, audio, FOLLOWUP_S, tuesday, stream)
        if not question:
            _log("[listener] follow-up window closed (nothing said)", flush=True)
    return max(seen_answer, getattr(tuesday, "answered", 0))


def _echo(text: str, tuesday) -> bool:
    """True if what the microphone caught is (most likely) the robot's own voice coming back, not him talking:
    - it calls someone "Sir" (Tuesday's word; he doesn't call Tuesday that),
    - it's too short to be a real interruption (fillers like "Hm." come back as "H.M."),
    - or it's close to something the robot said in the last 30 s (same words, or a garbled version of them)."""
    import difflib
    said = getattr(tuesday, "said", None)
    if said is None:   # in-process
        said = [t for ts, t in getattr(tuesday.body, "recent_said", []) if time.time() - ts < 30]
    words = re.findall(r"[a-z']+", text.lower())
    if not words:
        return True
    if "sir" in words:
        return True
    if not said:
        return False
    if len(words) < 3 and getattr(tuesday, "talking", True):
        return True
    vocab = set(re.findall(r"[a-z']+", " ".join(said).lower()))
    if sum(w in vocab for w in words) / len(words) >= 0.5:
        return True
    norm = " ".join(words)
    return any(difflib.SequenceMatcher(None, norm, " ".join(re.findall(r"[a-z']+", p.lower()))).ratio() >= 0.55 for p in said)


# "Tuesday" anywhere, plus how the offline models sometimes hear it
MENTION = re.compile(r"\b(?:tuesday|tuesdays|tues day|two s day|chews day|choose day)\b", re.I)
NAME_ONLY = re.compile(r"^\W*(?:(?:hey|hi|okay|ok|yo|a|alright|so)\W+)*(?:tuesday)\W*$", re.I)


def _addressed(text: str) -> str:
    """The sentence as said to Tuesday, minus a leading or trailing call of its name ("Hey Tuesday, ..." /
    "..., Tuesday?"); '' when it was only the name."""
    if NAME_ONLY.match(text):
        return ""
    t = re.sub(r"^\W*(?:(?:hey|hi|okay|ok|yo|a|alright|so)\W+)*tuesday\b\W*", "", text, flags=re.I)
    t = re.sub(r",\s*tuesday\s*([?.!]*)\s*$", r"\1", t, flags=re.I)   # "..., Tuesday?" (not "on Tuesday.")
    return t.strip() or text


_whisper = {}
_last = {}
HINT = config.env("TUESDAY_WHISPER_HINT", f"Tuesday, {config.OWNER_NAME}, Vector" + (f", {config.COMPANY}" if config.COMPANY else "") + ".")
LEAD = re.compile(r"^\W*(?:(?:a|hey|he|hi|okay|ok|yo)\W+)?tuesday\b\W*|^\W*(?:um+|uh+|so|and then)\b\W*", re.I)


def _transcribe(chunks: list[bytes], keep_name: bool = False) -> str:
    """Whisper (offline) for the words; '' when it judges it wasn't really speech (noise, the TV, mumbling)."""
    import numpy as np
    if "m" not in _whisper:
        from faster_whisper import WhisperModel
        _whisper["m"] = WhisperModel(config.env("TUESDAY_WHISPER", "small.en"), device="cpu", compute_type="int8")
    pcm = np.frombuffer(b"".join(chunks), dtype=np.int16).astype(np.float32) / 32768
    _last["pcm"] = pcm   # for the voice check of the sentence about to be sent (kept in memory only, until the next)
    # The names he uses (Tuesday, his own, his company) as a hint, so his name isn't misheard as two random words.
    segs, _ = _whisper["m"].transcribe(pcm, language="en", beam_size=1, vad_filter=False, condition_on_previous_text=False,
                                       initial_prompt=HINT)
    segs = [g for g in segs if g.no_speech_prob < 0.7 and g.avg_logprob > -1.15]
    text = " ".join(g.text.strip() for g in segs).strip()
    # how sure Whisper is of these words (avg log-probability: about -0.2 clear speech, below -0.7 a guess)
    _last["clarity"] = (sum(g.avg_logprob * len(g.text) for g in segs) / max(1, sum(len(g.text) for g in segs))) if segs else -9
    for _ in range(0 if keep_name else 2):
        text = LEAD.sub("", text).strip()
    return text if len(re.findall(r"[a-zA-Z]+", text)) >= 1 else ""


def _hear(model, KaldiRecognizer, audio, timeout, tuesday, stream, prefix=()) -> str:
    """One utterance, transcribed; '' if nothing clear is said within `timeout` seconds. Vosk finds where it starts
    and ends; Whisper reads it. `prefix`: the sound just before (the wake word and whatever came straight after)."""
    rec = KaldiRecognizer(model, RATE)
    rec.SetWords(True)
    buf = list(prefix)
    for chunk in prefix:
        rec.AcceptWaveform(chunk)
    from collections import deque
    start = time.time()
    spoke = bool(json.loads(rec.PartialResult()).get("partial"))
    before = deque(maxlen=6)   # ~1.5 s: speech is only noticed a moment after it starts, so keep what came just before
    while time.time() - start < timeout + (12 if spoke else 0):
        data = _get(audio, stream)
        if data is None:
            continue
        if _speaking(tuesday) and not spoke:   # the window starts once the robot is quiet; he can still cut in
            start = time.time()
        if spoke:
            buf.append(data)
        else:
            before.append(data)
        if rec.AcceptWaveform(data):
            if json.loads(rec.Result()).get("text"):
                text = _transcribe(buf)
                if text and not _echo(text, tuesday) and _last.get("clarity", -9) > FOLLOWUP_CLARITY:
                    return text
                if text and _last.get("clarity", -9) <= FOLLOWUP_CLARITY:
                    _log(f"[listener] follow-up too unclear to trust ({_last.get('clarity', -9):.2f}); ignored", flush=True)
                if not text:
                    _log("[listener] ignored (not clear speech)", flush=True)
                buf, spoke = [], False
        elif json.loads(rec.PartialResult()).get("partial") and not spoke:
            spoke = True
            buf.extend(before)
    return _transcribe(buf) if spoke and buf else ""


def _clear(result: dict, min_conf: float = 0.75) -> str:
    """The utterance, if it was clearly spoken: at least two words, recognised with good confidence on average."""
    words = result.get("result") or []
    text = WAKE.sub("", result.get("text", "")).strip()
    if not text or len(text.split()) < 2 and text not in ("stop", "stay", "yes", "no", "thanks"):
        return ""
    conf = sum(w.get("conf", 0) for w in words) / len(words) if words else 0
    if conf < min_conf:
        _log(f"[listener] ignored (unclear, confidence {conf:.2f}): {text!r}", flush=True)
        return ""
    return text


def _cue(tuesday):
    """Show he's been heard: the robot looks up at him (no sound, so the question isn't drowned out)."""
    if isinstance(tuesday, Remote):
        return tuesday.cue()
    try:
        threading.Thread(target=lambda: tuesday.body.head(25), daemon=True).start()
    except Exception:
        pass


def _answer(tuesday, text: str, overheard: bool = False):
    state["last"] = text
    _log(f"[listener] heard: {text!r} (clarity {_last.get('clarity', 0):.2f})", flush=True)
    t0 = time.time()
    # Whose voice? A score against his voiceprint (numbers describing the voice; the audio itself is never kept).
    from . import voiceid
    clarity = _last.pop("clarity", None)
    vec = voiceid.embed(_last.pop("pcm", None)) if voiceid.available() else None
    voice = voiceid.score(vec)
    if voice is not None:
        _log(f"[listener] voice match {voice:.2f}" + (" (him)" if voice >= voiceid.MATCH else ""), flush=True)
    if isinstance(tuesday, Remote):
        def ask():
            try:
                r = tuesday.handle(text, overheard, voice, clarity)
                if r.get("basis") == "face" and vec is not None:   # the robot clearly saw him: learn his voice
                    n = voiceid.learn(vec)
                    _log(f"[listener] learned his voice ({n} samples)", flush=True)
            except Exception as e:
                _log(f"[listener] couldn't reach Tuesday: {e}", flush=True)
            _log(f"[listener] answered in {time.time() - t0:.1f} s", flush=True)
        if overheard:   # quick, and whether Tuesday answers decides if a follow-up window opens
            return ask()
        # Keep listening while Tuesday answers, so he can cut in or ask something else straight away.
        threading.Thread(target=ask, daemon=True, name="ask").start()
        return
    try:
        tuesday.handle(text, channel="voice", overheard=overheard)
    except Exception as e:
        _log(f"[listener] couldn't answer: {e}", flush=True)


if __name__ == "__main__":   # inside "Tuesday Listener": the app macOS lets use the microphone
    _run(Remote())
