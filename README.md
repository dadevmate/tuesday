# Tuesday

A personal AI that lives in a small robot. Tuesday runs an Anki / Digital Dream Labs **Vector** robot and a team of
open-weight language models on a single 16 GB MacBook. You talk to it, it talks back in its own voice, notices
what's going on around it, remembers what matters, and acts for you: reminders, the robot itself, the web, email
and calendar.

It's a for-fun project built to answer one question: **how close can a $300 robot and a laptop get to feeling like a
real, present companion, while keeping everything private?**

## What it does

- **Conversation in real time.** Say "Tuesday" anywhere in a sentence and it answers out loud.
  - **Speed:** first words in about 2 seconds. Tuesday speaks each sentence as soon as it's generated.
  - **Interrupting:** you can cut in mid-sentence and it stops.
  - **Hard questions:** it gives a quick take at once, then a considered answer a little later, which it drops if
    you've moved on.
- **Understands intent, not phrases.** "That's it for today, I'm off to bed" makes it say good night, drive onto
  its dock and fall asleep. "I'm carrying you to the kitchen" keeps it still. A planner model decides what to say,
  what the robot should do, and whether the request needs more thought, research or your apps.
- **Aware of its surroundings.** Every second it reads the robot's senses: faces and distance, touch, being picked
  up, the dock.
  - **Small reactions, no words:** it glances at you when you walk over and looks up when you say its name.
  - **Speaking up on its own, rarely:** a hello when you're back after a while, a remark when you switch activity,
    a nudge at 2 a.m. Code rules keep it quiet mid-conversation, while others talk, at night, and soon after it last
    spoke.
- **Remembers.** Facts from conversations become memories. Short text notes of what it saw answer "where did I leave
  my keys?". No photos are kept.
- **Knows who's asking.** It recognises the owner by face (on the robot) or by voice (an offline speaker-ID model).
  Private things are told only to the owner. Anyone else gets a friendly assistant that knows nothing personal.

## How it's built

```
 mic (Mac) ─▶ Vosk endpointing ─▶ Whisper ─▶ ┐
 "Hey Vector" ─▶ WirePod ──────────────────▶ ├─▶ planner (intent.py) ─▶ say · act · think deeper
                                             │       │                      │
 robot senses ─▶ awareness.py ───────────────┘       ▼                      ▼
                                             Tuesday Core            council of minds
                                       (Qwen3 8B + LoRA adapters)    (+ Claude advisors)
                                                     │
                                       Vector SDK ◀──┘ voice (Piper TTS), motion, camera
```

- **Seven minds, one GPU.** The minds are open-weight models named after the days of the week:
  - Five of them are LoRA adapters on one shared Qwen3 8B, served by llama.cpp (`tuesday/core.py`). Switching
    between them costs nothing, so the whole team fits in 16 GB.
  - Two are a different model family (Mistral Ministral, on Ollama), for outside perspectives.
- **Planner first** (`tuesday/intent.py`). Each spoken message gets one short call that writes three lines: `DO`
  (robot or system actions), `NEED` (none / think / research / apps / job) and `SAY`. The `DO:` line is prefilled
  so actions are decided before anything is said.
  - **Facts come from code, never from the model:** time, battery, weather, what the camera sees.
  - **Physical movements are double-checked** before the robot moves.
- **A council for harder questions** (`tuesday/council.py`). Specialists draft, and Tuesday judges and answers in its
  own voice.
- **Claude as an advisor, never the boss** (`tuesday/claude.py`). Haiku, Sonnet, Opus and Fable are consulted by task
  complexity, within a daily budget per model.
  - **What Claude sees:** only a minimal brief, written on-device and scrubbed by a code-level privacy guard (names,
    contact details and IDs removed). Anything emotional or secret is blocked outright.
  - **When Claude is unavailable,** Tuesday works alone.
- **Robot control** (`tuesday/robot/vector.py`):
  - every SDK call has a deadline;
  - it reconnects by itself and finds the robot again on any network;
  - driving is obstacle-aware, and "come here" finds a face and approaches it;
  - a calm mode switches off aimless wandering;
  - real sleep and wake animations.
- **Learning** (`train/`).
  - **Weekly:** each mind is fine-tuned on the owner's data with MLX QLoRA. A candidate replaces the live mind only if
    it passes recall, voice, tool-use and privacy tests and scores no worse.
  - **Planner experiment:** an adapter trained on synthetic data wasn't better than the base model, so it's kept off.
    The training scripts stay in the repo.

## Privacy by design

- **Everything personal stays on the owner's machine,** in `data/` (gitignored): memories, conversations, the voiceprint,
  sighting notes, training data. This repository contains only code and generic examples.
  - Personal settings live in `.env`: name, forms of address, company, city.
  - The owner's own questionnaire lives in `private/` (also gitignored).
- **Incognito** ("Tuesday, incognito") turns off the camera, notes, logs and memory. It's also on automatically while
  the robot sleeps.
- **Audio is never saved.** Speech is transcribed in memory and discarded. Sentences not meant for Tuesday are dropped
  immediately.
- **Every prompt sent to Claude is logged locally** for audit.

## Hardware and stack

- MacBook Pro M1 Pro, 16 GB.
- DDL Vector 2.0 with [WirePod](https://github.com/kercre123/wire-pod) (self-hosted robot server).
- Models: Qwen3 8B (llama.cpp, LoRA adapters), Mistral Ministral 8B/3B (Ollama), Qwen3-VL 4B (vision),
  faster-whisper (speech), Vosk (endpointing), Piper (voice), WeSpeaker ResNet34 (speaker ID, ONNX).
- Python, MLX for fine-tuning, SearXNG for private web search.

## Running it

```bash
cp .env.example .env        # your name, city, time zone, robot details
./run.sh --train-minds      # once: fine-tune the minds (then weekly by itself)
./run.sh                    # start Tuesday on http://127.0.0.1:4343
./run.sh --install-listener # the "Tuesday…" microphone listener (macOS asks for mic access once)
```

Robot setup, step by step: [`deploy/VECTOR-SETUP.md`](deploy/VECTOR-SETUP.md). Without a robot, Tuesday runs on a
mock body that logs what the robot would do.

## Layout

```
tuesday/agent.py        conversation handling, privacy gate, persona
tuesday/intent.py       the planner: understanding what's said, acting, speaking
tuesday/awareness.py    senses, small reactions, unprompted remarks
tuesday/council.py      the minds answering together
tuesday/claude.py       Claude advisors: tiers, budgets, privacy guard
tuesday/core.py         Tuesday Core (shared base model + adapters)
tuesday/listener.py     microphone: wake-anywhere, interruptions, echo filtering
tuesday/voiceid.py      offline speaker verification
tuesday/sightings.py    text notes of what the robot saw
tuesday/robot/          Vector body, mock body, retrain stand-in
train/                  fine-tuning, evaluation, weekly retraining, planner experiment
wirepod/, deploy/       WirePod plugin and intent set, setup guide, optional server deployment
```

## Status

A working prototype in daily use. Known limits:
- **Model size:** an 8B planner sometimes misreads context.
- **Face recognition:** the robot only recognises faces it's pointed at.
- **Navigation:** Vector's SDK has no home map, so the robot can't go to a named room.
