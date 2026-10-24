#!/usr/bin/env bash
# Tuesday launcher.
#   ./run.sh                 start Tuesday
#   ./run.sh chat            chat with Tuesday in the terminal
#   ./run.sh --train-minds [monday ...]   fine-tune the seven minds on your data (MLX, Apple Silicon; default all)
#   ./run.sh --ingest        turn answers in data/questionnaire-2.md into memories and training data
#   ./run.sh --browser       install Tuesday's web browser (Chromium), once
#   ./run.sh --browser-login log in to websites Tuesday may use for you (opens a visible browser)
#   ./run.sh --robot-setup   install the Vector SDK and pair with your robot via wire-pod
#   ./run.sh --robot-check   say hello through the robot
#   ./run.sh --install-listener               "Hey Tuesday" and follow-ups on the Mac's microphone
#   ./run.sh --wirepod-tuesday                WirePod sends every question to Tuesday (reapply after a WirePod update)
#   ./run.sh --email-setup | --calendar-setup | --install-search   connect email and calendar; private search (SearXNG)
#   ./run.sh --install-service | --uninstall-service   run Tuesday at login (macOS LaunchAgent), with Ollama's speed settings
#   ./run.sh --autotrain [--force]          retrain on new data, exam, promote only if better
#   ./run.sh --install-autotrain | --uninstall-autotrain   do that every Saturday 02:00 (macOS LaunchAgent)
#   ./run.sh --install-relay | --uninstall-relay       link the robot to a cloud Tuesday from this Mac (macOS LaunchAgent)
set -euo pipefail
cd "$(dirname "$0")"

PY=python3.11
command -v $PY >/dev/null || PY=python3
if [ ! -d .venv ]; then
  echo "Creating Python environment…"
  $PY -m venv .venv
fi
source .venv/bin/activate
# Package installs are best-effort so Tuesday still starts offline.
pip install -q --upgrade pip >/dev/null 2>&1 || true
pip install -q -r requirements.txt >/dev/null 2>&1 || echo "(pip install skipped — offline?)"
export SSL_CERT_FILE="$(python -m certifi)"

case "${1:-serve}" in
  # caffeinate keeps the Mac awake (idle and, on power, system sleep) until training ends.
  --train-minds)
    pip install -q -r requirements-train.txt
    if ! ls train/out/llama-bin/*/llama-quantize >/dev/null 2>&1; then   # llama.cpp's prebuilt quantizer (4-bit minds)
      mkdir -p train/out/llama-bin && T=$(curl -s https://api.github.com/repos/ggml-org/llama.cpp/releases/latest | python -c "import sys,json;print(json.load(sys.stdin)['tag_name'])")
      curl -sL "https://github.com/ggml-org/llama.cpp/releases/download/$T/llama-$T-bin-macos-arm64.tar.gz" | tar -xz -C train/out/llama-bin || echo "(quantizer download failed; minds stay 8-bit)"
    fi
    python train/ingest_answers.py; caffeinate -i -s python train/build_core.py --train "${@:2}" ;;
  --ingest)     python train/ingest_answers.py ;;
  --browser)    python -m playwright install chromium ;;
  --browser-login) python -c "from tuesday import browser; browser.login()" ;;
  --email-setup)  # IMAP/SMTP straight to your provider; the app password goes into the macOS Keychain, not a file
    read -r -p "Email address: " ADDR
    grep -q '^TUESDAY_EMAIL_ADDRESS=' .env 2>/dev/null && sed -i '' "s|^TUESDAY_EMAIL_ADDRESS=.*|TUESDAY_EMAIL_ADDRESS=$ADDR|" .env || echo "TUESDAY_EMAIL_ADDRESS=$ADDR" >> .env
    echo "Gmail: Google Account -> Security -> 2-Step Verification -> App passwords -> create one called Tuesday."
    echo "Paste the app password when asked (it's stored in the Keychain as 'tuesday-email')."
    security add-generic-password -U -s tuesday-email -a "$ADDR" -w
    python -c "from tuesday import mail; print('Inbox OK:', len(mail.search(limit=3)), 'recent emails read')" ;;
  --calendar-setup)  # macOS's own calendar store: add Google/iCloud in System Settings -> Internet Accounts first
    pip install -q pyobjc-framework-EventKit
    # "Tuesday Calendar": a background applet that macOS can grant calendar access to (tuesday/agenda.py explains why)
    A=vendor/TuesdayCalendar.app; mkdir -p vendor; rm -rf "$A"
    osacompile -l JavaScript -o "$A" -e "function run() { const a = Application.currentApplication(); a.includeStandardAdditions = true;
      a.doShellScript(\"cd '$PWD' && TUESDAY_CAL_HELPER=1 ./.venv/bin/python -m tuesday.agenda --serve\"); }"
    PB=/usr/libexec/PlistBuddy; I="$A/Contents/Info.plist"; WHY="Tuesday reads your calendar to answer questions about your schedule and adds events when you ask."
    for K in CFBundleIdentifier CFBundleName LSUIElement NSCalendarsFullAccessUsageDescription NSCalendarsUsageDescription; do $PB -c "Delete :$K" "$I" 2>/dev/null || true; done
    $PB -c "Add :CFBundleIdentifier string com.tuesday.calendar" -c "Add :CFBundleName string Tuesday Calendar" -c "Add :LSUIElement bool true" \
        -c "Add :NSCalendarsFullAccessUsageDescription string $WHY" -c "Add :NSCalendarsUsageDescription string $WHY" "$I"
    codesign -s - --force "$A"
    echo "macOS will ask to let Tuesday Calendar access your calendars: click Allow."
    python -m tuesday.agenda ;;
  --wirepod-tuesday)  # WirePod keeps only robot actions; every question goes to Tuesday (reapply after updating WirePod)
    F=/Applications/WirePod.app/Contents/Frameworks/chipper/intent-data/en-US.json
    [ -f data/wirepod-en-US.original.json ] || cp "$F" data/wirepod-en-US.original.json
    cp deploy/wirepod-en-US.tuesday.json "$F"
    killall WirePod 2>/dev/null; sleep 3; open -g -a WirePod
    echo "WirePod now sends every question to Tuesday (original kept in data/wirepod-en-US.original.json)." ;;
  --install-listener)  # "Hey Tuesday" + follow-ups on the Mac's microphone, in an app macOS can grant the microphone to
    pip install -q vosk sounddevice
    A=vendor/TuesdayListener.app; mkdir -p vendor data/logs; rm -rf "$A"
    osacompile -l JavaScript -o "$A" -e "function run() { const a = Application.currentApplication(); a.includeStandardAdditions = true;
      a.doShellScript(\"cd '$PWD' && export SSL_CERT_FILE=\$(./.venv/bin/python -m certifi) && exec ./.venv/bin/python -m tuesday.listener >> data/logs/listener.log 2>&1\"); }"
    PB=/usr/libexec/PlistBuddy; I="$A/Contents/Info.plist"
    for K in CFBundleIdentifier CFBundleName LSUIElement NSMicrophoneUsageDescription; do $PB -c "Delete :$K" "$I" 2>/dev/null || true; done
    $PB -c "Add :CFBundleIdentifier string com.tuesday.listener" -c "Add :CFBundleName string Tuesday Listener" -c "Add :LSUIElement bool true" \
        -c "Add :NSMicrophoneUsageDescription string Tuesday listens for \"Hey Tuesday\" and your follow-up questions, on this Mac only." "$I"
    codesign -s - --force "$A"
    LP="$HOME/Library/LaunchAgents/com.tuesday.listener.plist"
    cat > "$LP" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tuesday.listener</string>
  <key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-W</string><string>-g</string><string>-a</string><string>$PWD/$A</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>30</integer>
</dict></plist>
P
    launchctl unload "$LP" 2>/dev/null || true; launchctl load -w "$LP"
    echo "Tuesday Listener started: macOS asks once to allow the microphone (click OK). Log: data/logs/listener.log" ;;
  --install-search)  # SearXNG on this Mac: your searches spread over several engines, no account, no tracking
    mkdir -p vendor data/logs
    [ -d vendor/searxng ] || git clone -q --depth=1 https://github.com/searxng/searxng vendor/searxng
    ( cd vendor/searxng && python3.11 -m venv .venv && .venv/bin/pip install -q -U pip setuptools wheel pyyaml msgspec typing_extensions \
      && .venv/bin/pip install -q --use-pep517 --no-build-isolation -e . )
    cat > vendor/searxng-settings.yml <<Y
use_default_settings:
  engines:
    keep_only: [bing, startpage, mojeek, yahoo, wikipedia, google cse]   # the ones that don't block a home instance
engines:
  - {name: bing, disabled: false}
  - {name: startpage, disabled: false}
  - {name: mojeek, disabled: false}
  - {name: yahoo, disabled: false}
  - {name: wikipedia, disabled: false}
general: {instance_name: "Tuesday search", enable_metrics: false}
server: {bind_address: "127.0.0.1", port: 8888, secret_key: "$(openssl rand -hex 32)", limiter: false, image_proxy: false, public_instance: false}
search: {formats: [html, json], safe_search: 0}
Y
    SP="$HOME/Library/LaunchAgents/com.tuesday.search.plist"
    cat > "$SP" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tuesday.search</string>
  <key>ProgramArguments</key><array><string>$PWD/vendor/searxng/.venv/bin/python</string><string>-m</string><string>searx.webapp</string></array>
  <key>WorkingDirectory</key><string>$PWD/vendor/searxng</string>
  <key>EnvironmentVariables</key><dict><key>SEARXNG_SETTINGS_PATH</key><string>$PWD/vendor/searxng-settings.yml</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$PWD/data/logs/search.log</string>
  <key>StandardErrorPath</key><string>$PWD/data/logs/search.log</string>
</dict></plist>
P
    launchctl unload "$SP" 2>/dev/null || true; launchctl load -w "$SP"; sleep 6
    python -c "from tuesday import capabilities as c; import json,urllib.request; json.load(urllib.request.urlopen(c.SEARXNG+'/search?q=test&format=json',timeout=15)); print('Private search running on', c.SEARXNG)" ;;
  --robot-setup)
    pip install -q -r requirements-robot.txt
    echo
    echo "Pairing with your Vector through wire-pod. You'll need the robot's serial (on its underside),"
    echo "its name (e.g. Vector-A1B2) and IP (shown on Vector's face: double-click his back button, raise and lower the lift),"
    echo "and the wire-pod web UI address. Then set TUESDAY_ROBOT=vector and VECTOR_SERIAL in tuesday/.env."
    python -m anki_vector.configure
    ;;
  --robot-check) pip install -q -r requirements-robot.txt && TUESDAY_ROBOT=vector python -m tuesday robot-check ;;
  --install-service)
    mkdir -p data/logs
    PL="$HOME/Library/LaunchAgents/com.tuesday.assistant.plist"
    cat > "$PL" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tuesday.assistant</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$PWD/run.sh</string><string>serve</string></array>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$PWD/data/logs/tuesday.log</string>
  <key>StandardErrorPath</key><string>$PWD/data/logs/tuesday.log</string>
</dict></plist>
P
    # Ollama (Friday, Sunday, embeddings, vision) reads these at start: flash attention, a q8 KV cache (half the memory
    # per token) and at most two models in memory next to Tuesday Core.
    OE="$HOME/Library/LaunchAgents/com.tuesday.ollama-env.plist"
    cat > "$OE" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tuesday.ollama-env</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string>
    <string>launchctl setenv OLLAMA_FLASH_ATTENTION 1; launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0; launchctl setenv OLLAMA_MAX_LOADED_MODELS 2</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
P
    launchctl unload "$OE" 2>/dev/null || true; launchctl load -w "$OE"
    launchctl load -w "$PL" && echo "Tuesday runs at login: http://127.0.0.1:4343 (quit and reopen Ollama once to apply its settings)" ;;
  --autotrain)  # retrain on new memories/conversations, exam, promote only if better ("--autotrain --force" ignores the checks)
    pip install -q -r requirements-train.txt && caffeinate -i -s python train/auto_retrain.py "${@:2}" ;;
  --install-autotrain)
    mkdir -p data/logs
    PL="$HOME/Library/LaunchAgents/com.tuesday.autotrain.plist"
    cat > "$PL" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tuesday.autotrain</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$PWD/run.sh</string><string>--autotrain</string></array>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>6</integer><key>Hour</key><integer>2</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$PWD/data/logs/auto-retrain.out</string>
  <key>StandardErrorPath</key><string>$PWD/data/logs/auto-retrain.out</string>
</dict></plist>
P
    launchctl unload "$PL" 2>/dev/null || true
    launchctl load -w "$PL" && echo "Tuesday retrains itself Saturdays 02:00 (needs power; log: data/logs/auto-retrain.log)" ;;
  --uninstall-autotrain)
    launchctl unload -w "$HOME/Library/LaunchAgents/com.tuesday.autotrain.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.tuesday.autotrain.plist"; echo "Removed." ;;
  --install-relay)
    # The Mac as the robot's home link: connects out to the cloud brain whenever the Mac is home and awake.
    pip install -q -r requirements-robot.txt
    mkdir -p data/logs
    PL="$HOME/Library/LaunchAgents/com.tuesday.relay.plist"
    cat > "$PL" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tuesday.relay</string>
  <key>ProgramArguments</key><array><string>$PWD/.venv/bin/python</string><string>-m</string><string>tuesday</string><string>relay</string></array>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$PWD/data/logs/relay.log</string>
  <key>StandardErrorPath</key><string>$PWD/data/logs/relay.log</string>
</dict></plist>
P
    launchctl unload "$PL" 2>/dev/null || true
    launchctl load -w "$PL" && echo "Relay runs at login; log: data/logs/relay.log" ;;
  --uninstall-relay)
    launchctl unload -w "$HOME/Library/LaunchAgents/com.tuesday.relay.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.tuesday.relay.plist"; echo "Relay removed." ;;
  --uninstall-service)
    launchctl unload -w "$HOME/Library/LaunchAgents/com.tuesday.assistant.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.tuesday.assistant.plist"
    launchctl unload -w "$HOME/Library/LaunchAgents/com.tuesday.ollama-env.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.tuesday.ollama-env.plist"
    python -c "from tuesday import core; core.stop()"; echo "Removed." ;;
  chat)  python -m tuesday chat ;;
  serve) python -m tuesday serve ;;
  *)     python -m tuesday "$@" ;;
esac
