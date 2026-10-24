"""Tuesday's own voice. Vector's built-in voice is fixed in its firmware, so Tuesday synthesises speech itself with
Piper (open-source, offline, free) and streams the audio to Vector's speaker through the SDK.

TUESDAY_VOICE picks the Piper voice; empty means Vector's own voice. TUESDAY_VOICE_SPEAKER picks one speaker of a
multi-speaker voice, by name (e.g. "p288") or number.
  Female Irish:  TUESDAY_VOICE=en_GB-vctk-medium with TUESDAY_VOICE_SPEAKER=p288 (or another Irish woman from VCTK)
  Female US:     en_US-lessac-medium · en_US-amy-medium · en_US-hfc_female-medium · en_US-kristin-medium
Voices download once into data/voices/. Runs wherever the robot body lives (this Mac)."""
import audioop  # deprecated in 3.13, fine on 3.11; used only to resample to Vector's rate
import io
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from . import config

VOICE = config.env("TUESDAY_VOICE", "")
SPEAKER = config.env("TUESDAY_VOICE_SPEAKER", "")
DIR = config.DATA / "voices"
VECTOR_RATE = 16000  # Vector plays 16-bit mono WAV at 8000-16025 Hz
_voice = None


def enabled() -> bool:
    return bool(VOICE)


def _load():
    global _voice
    if _voice is None:
        from piper import PiperVoice
        model = DIR / f"{VOICE}.onnx"
        if not model.exists():
            DIR.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, "-m", "piper.download_voices", VOICE, "--download-dir", str(DIR)], check=True)
        _voice = PiperVoice.load(str(model))
    return _voice


def _speaker_id(v) -> int | None:
    """TUESDAY_VOICE_SPEAKER as the voice's speaker number (a VCTK name like 'p288' is looked up in the voice's map)."""
    if not SPEAKER:
        return None
    if SPEAKER.isdigit():
        return int(SPEAKER)
    ids = getattr(v.config, "speaker_id_map", None) or {}
    if SPEAKER not in ids:
        raise ValueError(f"voice {VOICE} has no speaker {SPEAKER!r}")
    return ids[SPEAKER]


def synthesize(text: str) -> bytes:
    """Text -> 16 kHz, 16-bit mono WAV bytes, ready for Vector's speaker."""
    from piper import SynthesisConfig
    v = _load()
    raw = io.BytesIO()
    with wave.open(raw, "wb") as w:
        v.synthesize_wav(text, w, syn_config=SynthesisConfig(speaker_id=_speaker_id(v)))
    raw.seek(0)
    with wave.open(raw, "rb") as r:
        frames, rate, width, ch = r.readframes(r.getnframes()), r.getframerate(), r.getsampwidth(), r.getnchannels()
    if ch == 2:
        frames = audioop.tomono(frames, width, 0.5, 0.5)
    if width != 2:
        frames = audioop.lin2lin(frames, width, 2)
    if rate != VECTOR_RATE:
        frames, _ = audioop.ratecv(frames, 2, 1, rate, VECTOR_RATE, None)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(VECTOR_RATE)
        w.writeframes(frames)
    return out.getvalue()


def to_file(text: str) -> Path:
    f = Path(tempfile.mkstemp(prefix="tuesday-say-", suffix=".wav")[1])
    f.write_bytes(synthesize(text))
    return f
