"""Knowing his voice: an offline speaker check, so private things are only told to him.

A small speaker-verification model (WeSpeaker ResNet34, trained on VoxCeleb; data/models/, via sherpa-onnx) turns a
sentence of speech into 256 numbers that describe the voice, not the words. His voiceprint is the average of such
vectors, learned only from sentences he said while the robot clearly saw his face, so it can't learn someone else.
Only these vectors are kept (data/voiceprint.npy), never audio. Everything runs on the Mac's CPU in ~0.1 s.

score(pcm) -> similarity to his voiceprint (about 0.6+ for him, under 0.4 for others), or None until it has heard
him often enough (ENOUGH samples)."""
import json
import threading
from pathlib import Path

import numpy as np

from . import config

MODEL = Path(config.env("TUESDAY_VOICEID_MODEL", str(config.DATA / "models" / "wespeaker_en_voxceleb_resnet34_LM.onnx")))
PRINT = config.DATA / "voiceprint.npy"
MATCH = float(config.env("TUESDAY_VOICE_MATCH", "0.55"))   # at or above: his voice
ENOUGH = 3            # samples before it trusts the voiceprint
KEEP = 40             # newest samples kept
MIN_SECONDS = 1.0     # shorter speech says too little about the voice
_lock = threading.Lock()
_x = {}


def available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        return False
    return MODEL.exists()


def _extractor():
    if "e" not in _x:
        import sherpa_onnx
        _x["e"] = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL), num_threads=2))
    return _x["e"]


def embed(pcm: np.ndarray) -> np.ndarray | None:
    """pcm: float32 mono at 16 kHz. A unit vector for the voice, or None if too short."""
    if not available() or pcm is None or len(pcm) < 16000 * MIN_SECONDS:
        return None
    e = _extractor()
    s = e.create_stream()
    s.accept_waveform(16000, pcm.astype(np.float32))
    s.input_finished()
    v = np.array(e.compute(s), dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n else None


def _samples() -> np.ndarray:
    try:
        return np.load(PRINT)
    except Exception:
        return np.zeros((0, 256), dtype=np.float32)


def score(v: np.ndarray | None) -> float | None:
    """Similarity of this voice to his (cosine, -1..1); None if there's no usable voiceprint or speech."""
    if v is None:
        return None
    s = _samples()
    if len(s) < ENOUGH:
        return None
    mean = s.mean(axis=0)
    mean /= np.linalg.norm(mean) or 1
    return float(np.dot(v, mean))


def learn(v: np.ndarray | None) -> int:
    """Add a sample of his voice (only call this when his face confirmed it was him). Returns the sample count."""
    if v is None:
        return len(_samples())
    with _lock:
        s = _samples()
        if len(s) >= ENOUGH:   # an outlier (someone else talking at the same moment) isn't learned
            mean = s.mean(axis=0)
            mean /= np.linalg.norm(mean) or 1
            if float(np.dot(v, mean)) < MATCH - 0.15:
                return len(s)
        s = np.vstack([s, v[None, :]])[-KEEP:]
        np.save(PRINT, s)
        return len(s)


def status() -> dict:
    n = len(_samples())
    return {"samples": n, "ready": n >= ENOUGH, "match_at": MATCH}


def forget():
    PRINT.unlink(missing_ok=True)
    (config.DATA / "voiceprint.json").unlink(missing_ok=True)
    return json.dumps(status())
