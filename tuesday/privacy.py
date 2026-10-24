"""Incognito: while on, the camera is off and nothing is logged, remembered or photographed.
Toggled by the owner saying "incognito" / "unincognito". The flag survives restarts."""
from . import config

FLAG = config.DATA / "incognito"


def incognito() -> bool:
    return FLAG.exists()


def set_incognito(on: bool) -> None:
    if on:
        FLAG.touch()
    else:
        FLAG.unlink(missing_ok=True)
        SLEEP_FLAG.unlink(missing_ok=True)


# While Tuesday sleeps it's always incognito: nothing seen, heard or remembered. Waking turns it back off, but only if
# sleep is what turned it on (if he'd chosen incognito himself, it stays until he says "unincognito").
SLEEP_FLAG = FLAG.with_name(FLAG.name + ".by-sleep")


def sleeping_incognito(asleep: bool) -> None:
    if asleep:
        if not incognito():
            FLAG.touch()
            SLEEP_FLAG.touch()
    elif SLEEP_FLAG.exists():
        SLEEP_FLAG.unlink(missing_ok=True)
        FLAG.unlink(missing_ok=True)


# ---------------- who is Tuesday talking to? ----------------
# Text channels (chat page, CLI, API) are the owner's own devices. Spoken requests could come from anyone in the room,
# so for those the robot's camera decides: the owner's face -> "owner"; only other faces -> "guest"; no face -> "unknown".
# The name(s) the robot knows his face by. Speech recognition can mishear a name when he enrols ("Alex" -> "alec"),
# so TUESDAY_OWNER_FACE takes several, comma-separated.
OWNER_FACES = {n.strip().lower() for n in config.env("TUESDAY_OWNER_FACE", config.OWNER_NAME).split(",") if n.strip()}
OWNER_FACE = next(iter(sorted(OWNER_FACES)), config.OWNER_NAME.lower())
SPOKEN = ("voice", "wirepod")


def audience(channel: str, body, voice: float | None = None) -> tuple[str, list[str]]:
    """Return (owner | unknown | guest, names of the other people seen). Spoken, it's him only on real evidence: his
    face in view right now, or his voice (voiceid.py score). A face it can't make out, or anyone else, is never enough
    for private things; last_basis says which it was ("face", "voice" or "")."""
    global last_basis
    last_basis = ""
    if channel not in SPOKEN:
        return "owner", []
    if incognito():
        return "unknown", []  # camera is off
    try:
        names = [str(f.get("name") or "unknown") for f in body.faces()]
    except Exception:
        names = []
    import time
    from . import voiceid
    voice_ok = voice is not None and voice >= voiceid.MATCH
    if any(n.lower() in OWNER_FACES for n in names) or time.time() - _seen.get("owner", 0) < OWNER_FRESH_S:
        if any(n.lower() in OWNER_FACES for n in names):
            _seen["owner"] = time.time()
        last_basis = "face"
        return "owner", [n for n in names if n.lower() not in OWNER_FACES]
    known_others = [n for n in names if n.lower() != "unknown"]
    if voice_ok:
        last_basis = "voice"
        return "owner", names if known_others else []
    if names and not known_others and time.time() - _seen.get("owner", 0) < 120:
        return "unknown", []   # probably him at an awkward angle: an ordinary conversation, nothing private
    if names:
        return "guest", names
    return "unknown", []


last_basis = ""
_seen: dict = {}       # last time his face was recognised (also set by awareness, every second)
OWNER_FRESH_S = 5      # recognised this recently still counts as "his face in view now"
