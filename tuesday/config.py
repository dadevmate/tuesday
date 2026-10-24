"""Tuesday's configuration. Values come from tuesday/.env or the environment."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # the tuesday/ project folder


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")

# Python from python.org ships without CA certificates; use certifi's so HTTPS works everywhere.
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass


def env(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    return default if v is None or v == "" else v


def env_int(key: str, default: int) -> int:
    try:
        return int(env(key, str(default)))
    except ValueError:
        return default


# Private data lives here and is gitignored: profile, memories, conversations, reports.
DATA = Path(env("TUESDAY_DATA", str(ROOT / "data")))
DATA.mkdir(parents=True, exist_ok=True)
for sub in ("conversations", "camera", "maps"):
    (DATA / sub).mkdir(exist_ok=True)

HOST = env("TUESDAY_HOST", "127.0.0.1")
PORT = int(env("TUESDAY_PORT", "4343"))

# Minds: seven open-weight models served by Ollama (see tuesday/roster.py). LOCAL_MODEL is the main mind, Tuesday.
LOCAL_MODEL = env("TUESDAY_LOCAL_MODEL", "tuesday")
LOCAL_BASE = env("TUESDAY_LOCAL_BASE", "qwen3:8b")         # Tuesday's base model, used until the fine-tune exists
VISION_MODEL = env("TUESDAY_VISION_MODEL", "qwen3-vl:4b")                   # local camera understanding
OLLAMA_URL = env("OLLAMA_URL", "http://127.0.0.1:11434")

# Robot: "mock" until the Vector arrives, then "vector".
ROBOT = env("TUESDAY_ROBOT", "mock")
VECTOR_SERIAL = env("VECTOR_SERIAL", "")
VECTOR_NAME = env("VECTOR_NAME", "")
VECTOR_IP = env("VECTOR_IP", "")

# Who Tuesday works for. Personal details live in .env (never in the code): name, how to address them, their company,
# their city. The defaults are neutral.
OWNER_NAME = env("TUESDAY_OWNER_NAME", "Alex")
ADDRESS = [a.strip() for a in env("TUESDAY_ADDRESS", f"Sir|{OWNER_NAME}").split("|") if a.strip()]
COMPANY = env("TUESDAY_COMPANY", "")              # the owner's company or main project, if any
TZ_NAME = env("TUESDAY_TZ", "UTC")
CITY = env("TUESDAY_CITY", TZ_NAME.split("/")[-1].replace("_", " ") if "/" in TZ_NAME else "London")

# Extra origins allowed to call Tuesday from a browser, comma-separated.
EXTRA_ORIGINS = [o.strip() for o in env("TUESDAY_ALLOWED_ORIGINS", "").split(",") if o.strip()]

# Cloud deployment
BODY_TOKEN = env("BODY_TOKEN", "")         # shared secret between the cloud brain and the home robot relay
API_TOKEN = env("TUESDAY_API_TOKEN", "")   # when set, /chat and friends require "Authorization: Bearer <token>"
