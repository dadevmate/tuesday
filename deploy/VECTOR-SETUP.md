# Setting up Tuesday on an Anki / DDL Vector

```
"Hey Vector…" ──Wi-Fi──> YOUR MAC (home, plugged in, awake)
Vector ◀──────────────────  ├─ WirePod.app: the Vector's server; hears it and turns speech into text
                            ├─ Tuesday (http://127.0.0.1:4343): the seven minds, memory, routines, robot control
                            └─ Fable: deep research and your connected apps (Gmail, Calendar, Drive, IBKR), only when needed
```

**Everything runs on your MacBook.** The Vector has no brain of its own here: when the Mac is asleep, off or away,
the robot can't answer anything, even "Hey Vector, what time is it?". Keep the Mac **at home, plugged in, lid open**.

**What to expect:** casual replies in about 3–5 seconds, in one sentence. Bigger questions get "give me a moment, Sir"
first, then the full answer is spoken when it's ready.

Every WirePod step below follows WirePod's official guide and its settings page (checked 14 Oct 2026). If a screen
looks different (WirePod updates now and then), compare with https://github.com/kercre123/wire-pod/wiki/Installation
or check the WirePod wiki.

**At a glance**
- [ ] **B1** Mac stays awake on power (2 min)
- [ ] **B2** Install WirePod (5 min)
- [ ] **B3** Point WirePod at Tuesday (3 min)
- [x] **B4** Tuesday's voice: female Irish (already set; listen and pick a favourite if you like)
- [ ] **C1** Unbox and charge the Vector (30 min)
- [ ] **C2** Check Tuesday answers on http://127.0.0.1:4343 (1 min)
- [ ] **C3** Pair the Vector with WirePod (20 min)
- [ ] **C4** Note the robot's serial, name and IP (2 min)
- [ ] **C5** Connect Tuesday to the robot (5 min)
- [x] **C5b** Tuesday answers everything, "Hey Tuesday", follow-ups (already set up)
- [ ] **C6** Final checks (5 min)

---

## Part A. Already done (no action needed)
- ✅ Tuesday starts at login and restarts itself if it stops (`com.tuesday.assistant`).
- ✅ The minds retrain every Saturday at 02:00, with exams before anything replaces the live version.
- ✅ Private web search (SearXNG) starts at login.
- ✅ Ollama's speed settings are applied at login.
- ✅ Gmail, Google Calendar, Drive, IBKR and Stocktwits work through Fable and your Claude connectors.
- ✅ The robot's voice (Piper) and the Vector SDK (for WirePod) are installed and checked.

## Part B. Before the robot arrives (10 minutes, any time today)

### B1. Keep the Mac awake on power
1. Open **System Settings → Battery → Options…**
2. Turn on **Prevent automatic sleeping on power adapter when the display is off**. Click **Done**.
3. Keep the lid **open**. A closed lid always sleeps the Mac, and the Vector goes silent.

### B2. Install WirePod (the Vector's server, on your Mac)
1. Open **https://github.com/kercre123/WirePod/releases/latest** (note: *WirePod*, not wire-pod) and, under
   **Assets**, download **`WirePod-v1.2.18.dmg`** (or whatever the newest `WirePod-v….dmg` is).
2. Open the `.dmg` and drag **WirePod** into **Applications**. Close the window.
3. Open **WirePod** from Applications. macOS warns about an unidentified developer: go to **System Settings →
   Privacy & Security**, scroll down, click **Open Anyway** (then **Open**).
4. If macOS asks *"Do you want WirePod to accept incoming network connections?"*, click **Allow** (otherwise the
   Vector can't reach it).
5. Follow WirePod's prompts, then click **Open browser**. Its setup page opens (bookmark it; it's usually
   **http://localhost:8080**).
6. On that page, under **STT Language**, choose **English (US)** and click **Set Language**. (Speech is turned into
   text on your Mac; nothing goes to the internet.)
7. **System Settings → General → Login Items → +** → add **WirePod**, so it starts with the Mac.
8. Make sure nothing else on your network is running WirePod or is named `escapepod`.

### B3. Make Tuesday the Vector's brain
On WirePod's page, open **Server Settings**, scroll to **Knowledge Graph Setup**, and set exactly:

| Field (as WirePod labels it) | Set to |
|---|---|
| Knowledge Graph API Provider | **Custom** |
| API Key | `tuesday` |
| API Endpoint | `http://127.0.0.1:4343/v1` |
| LLM Model Name | `tuesday` |
| LLM Prompt | leave as it is (Tuesday uses its own personality) |
| ☑ "Enable intent-graph. This forwards the request to the LLM if the regular intent processor didn't understand what you said." | **ticked** |
| ☐ "Allow the LLM to make the robot perform animations and other actions during the response." | **unticked** (Tuesday moves the robot itself) |
| ☐ 'Enable conversations via "I have a question".' | **unticked** |

Click **Apply Settings**.

### B4. Choose Tuesday's voice (female, Irish) ✅ already set
Tuesday speaks through the Vector in its own voice, made on your Mac by **Piper** (offline, free). It's set to a
**female Irish voice**: speaker **p288** of the `en_GB-vctk-medium` voice, recorded by real Irish speakers.
These lines are already in `~/tuesday/.env`:
```
TUESDAY_VOICE=en_GB-vctk-medium
TUESDAY_VOICE_SPEAKER=p288
```
**Hear it** on the Mac's speakers (Terminal):
```bash
cd ~/tuesday
.venv/bin/python -m tuesday say "Evening, Sir. Systems are green and I'm all yours." en_GB-vctk-medium p288
```
**Try the other Irish women** and pick the one you like best:

| Speaker | Notes |
|---|---|
| `p288` | the default |
| `p266`, `p283`, `p295`, `p313`, `p340` | Irish |
| `p293`, `p351` | Northern Irish |

Ready-made samples are in `~/tuesday/data/voices/samples/` (double-click any `.wav` to listen), or run the command
above with another speaker, e.g. `… en_GB-vctk-medium p340`.

**To change it:** `open -e ~/tuesday/.env`, change `TUESDAY_VOICE_SPEAKER=` to your pick, save, then restart Tuesday:
```bash
launchctl kickstart -k gui/$(id -u)/com.tuesday.assistant
```
(Louder or softer on the robot: `TUESDAY_VOICE_VOLUME=` in `.env`, 0–100, default 75.)

---

## Part C. Robot day (about 1 hour)

### C1. Unbox and charge (30 minutes, mostly waiting)
1. Put the charger on your desk, **away from edges**, near the Mac's Wi-Fi.
2. Plug it in and place the Vector on it. It wakes by itself.
3. Let it charge for **at least 30 minutes**.
4. **Don't** set it up in DDL's Vector app and don't buy a membership: WirePod replaces DDL's servers.

### C2. Check Tuesday is up
1. Open **http://127.0.0.1:4343** and ask **"who am I?"**. You should get an answer about you within a few seconds.
2. If the page doesn't load, see **Troubleshooting** at the bottom.

### C3. Pair the Vector with WirePod (about 20 minutes)
This puts WirePod's special "escape pod" firmware on the robot and links it to your Mac. Keep the Vector **on its
charger** and the Mac **near it** (setup uses the Mac's Bluetooth).
1. **Recovery mode:** with the Vector on the charger, **hold its back button for about 15 seconds**, until it turns
   off and the lights come back on. (This doesn't erase anything.)
2. Wait until its face shows **`anki.com/v`** or **`ddl.io/v`**.
3. On the Mac, open **Google Chrome** (not Safari) and go to **https://wpsetup.keriganc.com/**
   - If Chrome shows an error: go to `chrome://flags`, turn on **Experimental Web Platform features**, click
     **Relaunch**, and open the page again.
4. Follow the page: connect to the Vector over Bluetooth (if it asks for a PIN, it's shown on the robot's face),
   choose **your home Wi-Fi** (the same one the Mac is on) and enter the password.
5. The Vector **downloads and installs an update** (several minutes). Leave it alone on the charger until it
   restarts.
6. **Clear its user data** (WirePod's instructions say this avoids odd behaviour):
   1. On the charger, **press the back button twice quickly**.
   2. **Lift the lift up, then down** (by hand).
   3. Take the Vector **off the charger** and **turn one wheel** until the cursor is on **RESET** (or
      **CLEAR USER DATA**).
   4. **Lift up, then down.**
   5. Turn the wheel to **CONFIRM**, then **lift up, then down** again. Put it back on the charger.
7. **Authenticate:** on WirePod's page, open **Bot Setup** → the **Authenticate** section. Follow it and click
   **ACTIVATE** (or **AUTHENTICATE**). If you get an error, wait about 20 seconds and click it again. If it sends you
   to a settings page, choose your settings (location and units can change later) and click **SAVE SETTINGS**.
8. Done when you see **"Vector setup is complete!"**
9. **Check the firmware:** press the back button twice, then lift the lift up and down. A debug screen appears; its
   firmware version should **end in `ep`**. (This screen also shows the robot's **IP**: note it for C4.) Put the
   lift down or put it back on the charger to leave.
10. Test:
    - **"Hey Vector, what time is it?"**: WirePod answers by itself.
    - **"Hey Vector, who am I?"**: this one is answered by **Tuesday**. Expect a moment's pause the first time.
      Until it has learned your face (C5 step 4), Tuesday treats the voice like a guest's and keeps private things
      to itself. That's normal.

### C4. Find the robot's details
You need three things for the next step:
1. **Serial number:** on the sticker **under the robot** (8 characters, e.g. `00e20100`).
2. **Name:** e.g. `Vector-A1B2`. Press the back button twice: it's shown on the face.
3. **IP address:** press the back button twice, then raise and lower the lift by hand: the debug screen shows the
   IP (e.g. `192.168.1.57`), the same screen as C3 step 9.

**Fix the robot's address:** in your router's admin page (often http://192.168.1.1, or the router app), find the
Vector in the device list and add a **DHCP reservation** for it, so its IP never changes. Do the same for the Mac.

### C5. Connect Tuesday to the robot
1. Open **Terminal** and run:
   ```bash
   cd ~/tuesday
   ./run.sh --robot-setup
   ```
   It asks, in this order:

   | Question | Answer |
   |---|---|
   | Do you wish to proceed? (y/n) | `y` |
   | Enter robot name | the name from C4, e.g. `Vector-A1B2` |
   | Enter robot ip | the IP from C4, e.g. `192.168.1.57` |
   | Enter robot serial number | the serial from C4, e.g. `00e20100` |
   | Enter wire-pod ip | just press **Enter** (it finds WirePod on this Mac by itself) |

   It ends with **SUCCESS!** (No Anki account or password is needed.)
2. Open Tuesday's settings file:
   ```bash
   open -e ~/tuesday/.env
   ```
   Add these lines at the bottom, with your values, then save (⌘S) and close:
   ```
   TUESDAY_ROBOT=vector
   VECTOR_SERIAL=00e20100
   VECTOR_NAME=Vector-A1B2
   VECTOR_IP=192.168.1.57
   ```
   (The voice lines are already in the file: see B4.)
3. Restart Tuesday, then test the robot link:
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.tuesday.assistant
   ./run.sh --robot-check
   ```
   The Vector says **"Hello. I am Tuesday."** in Tuesday's own voice: the female Irish voice from B4.
4. Teach it your face: look at the robot and say **"Hey Vector, my name is <your name>"** (the same spelling as `TUESDAY_OWNER_FACE` in `.env`: guest
   mode relies on it).

### C5b. Tuesday answers everything, "Hey Tuesday", and follow-ups ✅ already set up
- **Every question goes to Tuesday.** WirePod now keeps only robot actions (with exact phrases): "my name is…",
  "go to your charger", "go to sleep", volume, "look at me", "come here", "dance", "fist bump", "do a trick",
  "take a photo", "turn left/right/around", "move forward", "back up", "be quiet", eye colour, cube games.
  Everything else (time, weather, greetings, timers, "who am I", any question) is answered by Tuesday in its voice.
  After **updating WirePod**, run `cd ~/tuesday && ./run.sh --wirepod-tuesday` once to reapply this.
- **Say "Tuesday" anywhere in a sentence**, near the Mac: "Tuesday, how's the space industry looking?", "how's the
  space industry looking today, Tuesday?", or just "Tuesday…" and then the question. The robot looks up while you're
  still talking and answers. If you only mention the day ("my exam is on Tuesday") to someone else, Tuesday works out
  it wasn't meant for it and stays quiet. It all runs offline in the small **Tuesday Listener** app (starts at login;
  nothing is recorded). "Hey Vector" keeps working too.
- **Talk like you would to a person.** Tuesday says something within ~2 s, gives its quick take, and may follow up
  with a better answer within a minute. Cut in any time ("wait…", "actually…", a new question): it stops at the end
  of the sentence it's on and answers you. Once you've moved on, an older answer still being worked out is never
  said out of the blue (it's kept in the chat log). Deeper thinking is capped at about a minute.
- **Follow-ups need no wake word.** After the robot answers (whichever wake word you used), just keep talking for
  about **7 seconds**: "and tomorrow?", "tell me more". Silence for 7 s ends the conversation.
- **Just talk; no command phrases.** Tuesday understands what you mean and decides what to say and what the robot
  should do: "that's it for today, I'm going to sleep" → "Good night, Sir" and it drives onto its dock; "come off your
  dock and stretch your legs" → it leaves the dock and takes a short stroll; "give me a little spin"; "how much juice
  have you got?" (reads the real battery); "will it rain later?" (live forecast); "nudge me in 2 minutes to drink
  water" (a reminder); "I'm heading out for a run" → a goodbye, and it stays put. Talking about your day is just
  conversation: it never moves unless you want it to. "Go to sleep" / "good night" puts it on its dock and properly
  to sleep (eyes close, then quiet), and **incognito turns on while he sleeps**: nothing seen, heard or remembered.
  "Tuesday, wake up" (or anything said to Tuesday) wakes him: eyes open, he gets up, and incognito turns off again
  unless you had switched it on yourself. While he sleeps, "Hey Vector" doesn't wake him; Tuesday's name does. It speaks first, then moves, so you can keep talking.
  Questions that need thinking, research or your apps get a short "one moment", then the full answer.
- **It notices things (awareness):** when not incognito, Tuesday keeps track of who's around and what's going on.
  - **Small reactions, no words:** it glances at you when you appear, looks up as you come over, looks at you when
    you say its name, and reacts when petted.
  - **A quiet look now and then:** while you're around, it takes a still from the camera without moving and notices
    when you start doing something different, or when it's carried to a new room.
  - **Speaking up, rarely:** it says something only when a friend would. For example: hello when you're back after a
    while, a light remark when you switch to a game, a nudge if it's 2 am, a hello to a new person.
  - **When it stays quiet:**
    - you're mid-conversation, or people nearby are talking;
    - quiet hours, or it's asleep;
    - you're focused on work;
    - it spoke up in the last 8 minutes, or has already done so 5 times this hour.
  - Turn it off with `TUESDAY_AWARE=0`. Its log lines start with `[aware]`.
- **It remembers what it saw, as text notes:** "Tuesday, where did I leave my keys?", "have you seen my phone?", "what
  was I doing earlier?". Its looks become one-line notes (room, what you were doing, everyday things it could clearly
  see). No photos are kept. Notes are deleted after 30 days and aren't taken in incognito.
- **Private things are only for you:** your memories, its notes, anything personal are shared only when your face is
  in view at that moment, or the voice asking is yours.
  - **Voice ID:** a small offline speaker-ID model turns your voice into a voiceprint, a list of numbers and not a
    recording.
  - **How it learns you:** only from sentences you say while it can clearly see your face, so nobody else can teach it.
  - **Anyone else** (or you, unseen and before it knows your voice) gets a friendly Tuesday who knows nothing
    personal.
  - **Setup:** after a few conversations face-to-face it knows your voice. `[listener] voice match 0.71 (him)` in
    `data/logs/listener.log` shows it working.
- **"Come here"** finds your face, turns to you and drives over in short steps, stopping about 30 cm away. Any drive
  checks the distance sensor first and stops short of obstacles.
- **No random wandering:** Tuesday keeps Vector calm (his idle roaming is switched off; `TUESDAY_VECTOR_CALM=0` to
  allow it). Left off the dock for 10 minutes with nothing to do, or on low battery, he goes back by himself.
- **How Tuesday and Claude share the work** (see Day to day): Tuesday's own minds always lead. Claude Haiku only
  double-checks a request when the robot would move, or when Tuesday's two readings of it disagree. Plain chat never
  uses Claude.
- **"What do you see?"**, "what am I doing?", "what do you think of my room?": the robot looks up, takes a photo
  and answers you like someone in the room would (Qwen3-VL, offline), in about 20–60 s.
- **"Follow me"** (or "follow my face", "come with me"): the robot finds your face, turns toward you and drives
  after you, staying ~1 m back from a standing person (~40 cm if you're sitting). It searches by tilting its head and
  turning if it loses you, and says so after a full circle. **"Stop"** / "stay" ends it (it also stops after 3 min).
  While following, Tuesday holds the robot, so it ignores "Hey Vector": say **"Hey Tuesday, stop"**, or just "stop"
  right after it speaks. It needs to see your face: best on the floor, in a lit room, walking slowly.

### C6. Final checks
| Say | Expect |
|---|---|
| "Hey Vector, how's it going?" | One sentence in Tuesday's female Irish voice, in about 3–5 s |
| "Hey Vector, what's on my calendar tomorrow?" | "Give me a moment…", then your events (about 30 s, through Fable) |
| "Hey Vector, should I raise my app's price?" | "Give me a moment, Sir…", then a fuller answer a little later |
| "Hey Vector, turn around" | The robot turns |
| "Hey Vector, incognito" / "unincognito" | Camera and memory off / back on |
| Someone else's face, asking about you | Friendly, but nothing personal |
| 20:30 | The evening check-in, spoken by the robot |

If all of that works, you're done. 🎉

### C7 (later, optional). "Hey Tuesday" on the robot itself ⚠️
"Hey Tuesday" already works through the Mac's microphone (C5b). To have the **robot's own** microphone respond to it,
the robot needs community firmware. The stock firmware only knows "Hey Vector". Community firmware (**WireOS**) lets you choose your own wake word, but it
needs the robot **unlocked** first. That can void the warranty, and a failed flash needs recovery mode. Do this only
after everything above has worked for a few days, following the WirePod wiki's unlocking and WireOS pages. Then set
the wake word to **"Hey Tuesday"** in WireOS's settings page (`http://<vector-ip>:8080`).

---

## Troubleshooting
| Problem | Fix |
|---|---|
| The Vector ignores "Hey Vector" or says it can't connect | Is the Mac awake? Is **WirePod** running (menu bar)? Are the Mac and robot on the same Wi-Fi (see "Away from home")? |
| The robot does something you didn't mean | Tell it plainly ("stop", "no, stay there"); the log line `[intent] …` in `data/logs/tuesday.log` shows how it read you |
| "Hey Vector, who am I?" gives a generic answer | Check B3: Provider **Custom**, endpoint `http://127.0.0.1:4343/v1`, intent-graph **ticked**, then **Apply Settings** |
| http://127.0.0.1:4343 doesn't load | `launchctl kickstart -k gui/$(id -u)/com.tuesday.assistant`, wait 1 minute; log: `~/tuesday/data/logs/tuesday.log` |
| Answers come in the Vector's robot voice, not Tuesday's | C5 isn't finished: check `.env`, run `./run.sh --robot-check`, restart Tuesday |
| `--robot-check` can't find the robot | Recheck the robot's IP (C4), make sure WirePod is running, run `./run.sh --robot-setup` again |
| WirePod's Activate/Authenticate fails | Wait ~20 s and try again; check the debug screen's firmware ends in `ep` (redo C3 from step 1 if not) |
| wpsetup.keriganc.com won't connect | Use **Chrome**, turn on `chrome://flags` → Experimental Web Platform features, make sure the Mac's Bluetooth is on |
| The robot's face shows an error code (e.g. **980**) | An internal robot error: wait 30 s; if it stays, hold the back button 5 s (off), wait 10 s, tap it (on), on the charger. Tuesday reconnects by itself when it's back. If it happens several times a day, tell me (recovery mode is the next step) |
| Tuesday treats you like a guest (won't share personal things) | It doesn't recognise your face at that angle: look at the robot for a second. Once it's recognised you, an unrecognised face in the next 2 minutes counts as you |
| "Hey Tuesday" does nothing | Check `tail ~/tuesday/data/logs/listener.log` says "microphone check … (OK)"; if "silent", allow **Tuesday Listener** in System Settings → Privacy & Security → Microphone |
| Robot answers in its own voice | Tuesday's robot link is down: `launchctl kickstart -k gui/$(id -u)/com.tuesday.assistant` |
| Very slow replies (over 30 s) | It may be retraining (Saturday 02:00, or a run I started): check `tail ~/tuesday/data/logs/auto-retrain.log` |

## Day to day
- **Keep the Mac home, plugged in, lid open.** That's the Vector's brain.
- **Retraining:** Saturdays at 02:00 Tuesday pauses for a few hours while minds retrain on new memories and
  conversations. Each is tested before it replaces the live one (privacy failures are never promoted), then
  Tuesday restarts by itself. Log: `data/logs/auto-retrain.log`. Meanwhile the robot is kept asleep and still on
  his dock (a small stand-in holds him: `data/logs/hold.log`), so he doesn't wander while Tuesday is away.
- **Fable and connectors:** use your Claude subscription through Claude Code on this Mac, so keep it signed in.
  Reading your apps is free; sending, trading, deleting or creating events always asks for your yes.
- **Tuesday + Claude:** Tuesday's open minds run everything and write every answer. Claude models act as advisors
  and are only used when a task needs them:

  | Model | Used for |
  |---|---|
  | Haiku | double-checks robot commands; advises on moderate questions |
  | Sonnet | harder questions: technical work, several topics, decisions |
  | Opus | big decisions (money, career, studies) |
  | Fable | deep research and critical work |

  - **What Claude sees:** never your memories, profile, feelings or names. It gets a short brief written on the Mac,
    with names, places and contact details scrubbed out. Anything private is never sent. Every prompt actually sent
    is logged in `data/claude-log.jsonl`.
  - **What it costs:** a daily cap on calls per model (Haiku 150, Sonnet 30, Opus 6, Fable 4); today's count is in
    `data/claude-usage.json`. A model that runs out steps down to a cheaper one, then Tuesday works alone. It does
    the same when Claude is offline or over its limit. Change the caps with
    `TUESDAY_CLAUDE_BUDGET=haiku=150,sonnet=30,opus=6,fable=4` in `.env`. `TUESDAY_CLAUDE=0` turns Claude off.
- **Away from home: the Mac and the robot must share one Wi-Fi.** The Vector has no mobile data. It can only use
  2.4 GHz Wi-Fi, and WirePod on the Mac has to reach it directly. Tuesday finds the robot by itself on whatever
  network both are on (by its name `Vector-XXXX.local` or WirePod's record), and reconnects when the Mac changes
  networks. So there's no IP to edit.
  - **To use the robot away from home, use your phone's hotspot (one-time setup):**
    1. iPhone → Settings → Personal Hotspot → turn on **Maximize Compatibility** (2.4 GHz).
    2. Put the robot on the hotspot. It's on its charger, double-press the back button, and in Chrome at
       wpsetup.keriganc.com redo just the Wi-Fi step (as in C3) with the hotspot's name and password.
    3. Join the same hotspot on the Mac.
  - The Vector remembers the networks it was set up on and rejoins whichever is in range (⚠️ not yet verified on
    this robot; if it doesn't come back at home, redo step 2 with your home Wi-Fi).
  - Never-changing setup: let the Mac **auto-join** only your home Wi-Fi and your hotspot (System Settings → Wi-Fi → a network’s **Details…** →
    **Automatically join this network**), so both always land on the same network.
- **Updating Tuesday:** `cd ~/tuesday && git pull && launchctl kickstart -k gui/$(id -u)/com.tuesday.assistant`
- **Memory:** Tuesday Core uses about 6–7 GB all the time; Friday and Sunday load on Ollama when needed.
