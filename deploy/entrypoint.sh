#!/bin/sh
# Wait for Ollama, make sure every mind can answer, then start the brain.
# A mind uses its fine-tuned model once deploy/push-model.sh has uploaded it; until then its open base model stands in.
set -e
until curl -s "$OLLAMA_URL/api/tags" >/dev/null; do echo "waiting for ollama..."; sleep 2; done
have() { curl -s "$OLLAMA_URL/api/tags" | grep -q "\"name\":\"$1[\":]"; }
pull() { echo "pulling $1 ..."; curl -s "$OLLAMA_URL/api/pull" -d "{\"name\":\"$1\"}" >/dev/null; }
python - <<'PY' > /tmp/minds.txt
from tuesday import roster
for m in roster.enabled():
    print(m.name, m.base_ollama)
PY
while read -r NAME BASE; do have "$NAME" || have "$BASE" || pull "$BASE"; done < /tmp/minds.txt
have "${TUESDAY_EMBED_MODEL:-nomic-embed-text}" || pull "${TUESDAY_EMBED_MODEL:-nomic-embed-text}"   # memory search by meaning
have "${TUESDAY_VISION_MODEL:-moondream}" || pull "${TUESDAY_VISION_MODEL:-moondream}"                 # seeing through the robot's camera
exec python -m tuesday serve
