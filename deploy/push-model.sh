#!/usr/bin/env bash
# Upload Tuesday's fine-tuned minds to your server and register them in its Ollama.
#   deploy/push-model.sh user@server              # all minds that have a promoted version in train/out/live/
#   deploy/push-model.sh user@server tuesday friday
# Each mind goes up as the 4-bit (q4_K_M) file built on the Mac, so the server needs no extra tools.
set -euo pipefail
cd "$(dirname "$0")/.."
SERVER=$1; shift
MINDS=("$@")
[ ${#MINDS[@]} -eq 0 ] && MINDS=($(ls train/out/live 2>/dev/null))
[ ${#MINDS[@]} -eq 0 ] && { echo "No promoted minds yet: run ./run.sh --train-minds first."; exit 1; }
ssh "$SERVER" 'mkdir -p ~/tuesday-upload'
for M in "${MINDS[@]}"; do
  SRC=$(ls train/out/live/$M/$M-q*.gguf 2>/dev/null | head -1)
  [ -n "$SRC" ] || { echo "skip $M: no promoted model"; continue; }
  echo "== $M ($(du -h "$SRC" | cut -f1))"
  rsync -a --progress "$SRC" "$SERVER:~/tuesday-upload/$M.gguf"
  sed "s|^FROM .*|FROM /root/$M.gguf|" train/out/live/$M/Modelfile > /tmp/Modelfile.$M
  rsync -a /tmp/Modelfile.$M "$SERVER:~/tuesday-upload/Modelfile.$M"
  ssh "$SERVER" "set -e; cd ~/tuesday/deploy
    docker compose cp ~/tuesday-upload/$M.gguf ollama:/root/
    docker compose cp ~/tuesday-upload/Modelfile.$M ollama:/root/
    docker compose exec ollama ollama create $M -f /root/Modelfile.$M
    docker compose exec ollama rm -f /root/$M.gguf
    rm -f ~/tuesday-upload/$M.gguf"
done
ssh "$SERVER" 'cd ~/tuesday/deploy && docker compose restart tuesday'
echo "Minds live on $SERVER: ${MINDS[*]}"
