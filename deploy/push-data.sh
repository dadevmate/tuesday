#!/usr/bin/env bash
# Copy Tuesday's private data (profile, memories, conversations) to the cloud server. The model: deploy/push-model.sh.
#   deploy/push-data.sh user@server
set -euo pipefail
cd "$(dirname "$0")/.."
SERVER=$1
ssh "$SERVER" 'mkdir -p ~/tuesday-upload'
rsync -av --exclude cli-sandbox data/ "$SERVER:~/tuesday-upload/data/"
echo "Now on the server: cd ~/tuesday/deploy && docker compose cp ~/tuesday-upload/data/. tuesday:/data/ && docker compose restart tuesday"
echo "Fine-tuned model: deploy/push-model.sh $SERVER"
