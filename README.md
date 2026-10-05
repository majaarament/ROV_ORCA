# ROV-6 · ORCA

**Eye-tracked, gesture-controlled telepresence prototype with a witty shipboard AI**
Group 6 · Telepresence System for Marine Ecologists

The ecologist "becomes" the robot:

| You do | The ROV does |
|---|---|
| **Look** towards an edge of the screen | The camera view pans that way |
| **Look** at a specimen for a second | ORCA identifies it and tells you how to grip it |
| **Move your hand** in front of the webcam | The gripper follows |
| **Pinch** (thumb + index) | Gentle grip, for fragile specimens |
| **Fist** | Firm grip. Urchins need it; it crushes everything else |
| **Open** your hand | Let go. Over the **sample drawer**, the sample is stored |
| **Talk**: "Orca, lights on" | ORCA handles lights, precision mode, view, markers, notes |

**ORCA** (Onboard Research & Collection Assistant) answers in a few words.
It comments when something matters (too much force, fish fleeing), answers
questions about the scene, and stops repeating a grip hint once you've got that
species right. Make a mistake and the hint comes back.

**Two points of view** (press **P**, or say "Orca, first person" / "Orca, outside view"):

- **Outside:** you watch the ROV fly over the seabed from a distance, arm and gripper below it.
- **First person:** you are the robot. Its two hands reach in from the sides of the
  screen and copy your own, joint for joint: the webcam follows every finger of both
  hands. The first hand you show drives the gripper; show the other one too and it
  moves alongside. In mouse mode the hands use stock open, pinch and fist poses.

`START_POV` in `config.py` picks which one you start in.

**Two ways to run it.** When it starts it asks: **phone VR mode** (a phone in a cardboard
viewer is the headset: your head looks around, see below) or **standard screen mode**
(everything on this page up to there: this screen, the webcam, your eyes).

**The sea** is built from real images: the distant forest is a NOAA photo of giant
kelp off the Channel Islands, and the seabed is photographed sand and rock, with
sunbeams and rippling light on top. They are free to use (see `assets/CREDITS.md`).
If the `assets` folder is missing the game still runs, with plain colours instead.

The HUD shows **disturbance** (lights + sediment + scared fish + thrusters),
view state (wide or narrow, panning, map of where you are), gaze and hand
tracking status, grip, and the sample counts.

---

## Setup in VS Code (once)

1. **File → Open Folder…** and choose this `ROV_ORCA` folder.
2. Open the terminal (**Terminal → New Terminal**) and run:
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
   If `pyaudio` fails, run `brew install portaudio` and repeat the last line.
3. Whisper model: this uses `base.en`. If you already put `base.en.pt` in
   `~/.cache/whisper/`, you're set. Otherwise download it in your browser
   (the university network blocks Python from downloading it):
   https://openaipublic.azureedge.net/main/whisper/models/25a8566e1d0c1e2231d1c762132cd20e0f96a85d16145c3a00adf5d1ac670ead/base.en.pt
   then run `mkdir -p ~/.cache/whisper && mv ~/Downloads/base.en.pt ~/.cache/whisper/`
4. When VS Code asks "Select interpreter", choose the one in `.venv`.

## Run

- Press **F5**, or use **Run → Start Debugging**, and pick
  *ORCA (wake word)* or *ORCA (push-to-talk)*.
- Or in the terminal: `python3 main.py` (or `python3 main.py --push`).
  Add `--standard` or `--vr` to skip the question about which mode.

The first time, macOS asks for **camera** and **microphone** access for VS Code.
Allow both. If you clicked "Don't allow", turn them on in
**System Settings → Privacy & Security → Camera / Microphone**, then restart VS Code.

**Calibration:** when the window opens, look at the pulsing circle in the middle.
It calibrates automatically. Say "Orca, calibrate" or press **C** to redo it, and
use **-** / **+** if the view pans too easily or not enough.

No webcam? It falls back to **mouse mode**: mouse = hand, left click = pinch,
right click = fist, arrow keys = look around. Press **K** to switch modes any time.

## Phone VR mode

The phone goes sideways into a cardboard viewer, with its rear camera uncovered. The Mac
stays the robot: it runs the sea, ORCA and the logs, draws the view for each eye and sends
it to the phone over Wi-Fi. The phone sends back where your head points, the joints of
the hand its rear camera sees, and what its microphone hears.

| You do | The ROV does |
|---|---|
| **Turn your head** | You look that way. Hold a specimen in the middle of your view for a second and ORCA identifies it |
| **Move your hand** in front of the headset | The gripper moves the same way, by the same amount. Turning your head alone does not move it |
| **Pinch** / **fist** / **open** | Gentle grip / firm grip / let go, as on the desktop |
| **Talk**: "Orca, what is this?" | ORCA answers in your ears, with a short subtitle in both eyes |

**Start it**

1. Phone and Mac on the same Wi-Fi. Run `python3 main.py --vr`. The window on the Mac is the
   experimenter's view: the left eye, the full console, and the address for the phone.
2. On the phone open that address (`https://<the Mac>:8443`). The certificate is home-made,
   so the browser warns once: *Advanced → Proceed* (Chrome) or *Show details → visit this
   website* (Safari).
3. Turn the phone sideways, press **START VR EXPERIENCE**, and allow motion, camera and
   microphone. Put the phone in the viewer.
4. A short calibration follows, spoken by ORCA and shown in the headset: look straight
   ahead, hold up a hand, open it, pinch. Then the dive begins.

Without taking the headset off: "Orca, centre view" makes wherever you face the new
straight ahead. On the Mac: **ENTER** skips a calibration step that will not pass,
**C** runs the calibration again, **X** recentres. The other keys work as usual.

**Debug view.** The link under the start button (or `https://<the Mac>:8443/?debug=1`) shows
the camera feed with the hand joints drawn on it, one eye's picture and the live readings,
for setting up without a viewer. It also works in a browser on the Mac itself
(`https://localhost:8443/?debug=1`), where dragging the picture stands in for turning your head.

**What the headset shows** is deliberately little: ORCA's state, its last line, the grip or
precision state, messages such as SPECIMEN SECURED or COLLISION, and HAND NOT VISIBLE when
the camera has lost your hand for a second (the gripper holds still meanwhile and picks up
smoothly when the hand is back). The statistics stay on the Mac.

**The research condition** is three lines in `config.py`:
```
VR_PINCH_ACTION = "grip"           # or "precision", or "grip+precision"
MOVEMENT_SCALE_NORMAL = 1.0
MOVEMENT_SCALE_PRECISION = 0.2
```
`"grip"`: pinch is the gentle grip. `"precision"`: pinch only slows the gripper to the
precision scale, and a fist is the only grip (so the fragile species cannot be collected
until you add another way to grip). `"grip+precision"`: pinch grips and slows. The condition
is part of the log file's name. It lives in one small class (`Mapping` in `vr/teleop.py`).

**Tuning**, also in `config.py`: `VR_HEAD_GAIN` (1 = the sea turns exactly as far as your
head), `VR_EYE_SEPARATION` (depth), `VR_LENS_K` (counter-bulge for the lenses),
`VR_HAND_GAIN`, `VR_HAND_DEAD_ZONE`, `VR_HEAD_SIGNS` (if a phone reports a direction
backwards), `VR_MIC` / `VR_AUDIO_OUT` (`"mac"` if the phone's sound is a problem).

**Hand tracking files.** MediaPipe runs on the phone, and the Mac serves it from
`vr/web/vendor/`. If that folder is missing the phone fetches the files from the internet
instead; to fetch them once so it works offline:
```
mkdir -p vr/web/vendor/wasm && cd vr/web/vendor
B=https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21
curl -L -O $B/vision_bundle.mjs
(cd wasm && for f in vision_wasm_internal vision_wasm_nosimd_internal; do curl -L -O $B/wasm/$f.js -O $B/wasm/$f.wasm; done)
curl -L -O https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
```

**Privacy.** The camera picture never leaves the phone and is never stored: only the 21
joints of each hand are sent to the Mac. Microphone sound goes to the Mac for Whisper, as
it does from the Mac's own microphone in standard mode.

## Voice

Start every sentence with **"Orca"**:

- lights on / lights off
- fine mode / normal mode (motion scaling ×0.25)
- wide view / narrow view (narrow follows your eyes)
- mark sample · calibrate · centre view · status · help · quiet / speak up
- first person / outside view / switch view
- note *kelp frond torn near the holdfast* (dictated field note)
- where is the specimen? · what should I do next? · are the fish okay? · what is that?

SPACE mutes the mic. With `--push`, SPACE turns listening on and off instead,
and you don't need to say "Orca".

**ORCA's voice.** ORCA speaks with a neural voice that runs on your Mac (Piper),
so it sounds like a person and needs no internet or account. The voice file is not
part of `pip install`; fetch it once with:
```
mkdir -p voices && cd voices
curl -L -O https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx
curl -L -O https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx.json
```
Listen to other voices at https://rhasspy.github.io/piper-samples, download the two
files for the one you like the same way, and set `PIPER_VOICE` in `config.py`.
Without the voice file ORCA falls back to the built-in Mac voice.

**ORCA's voice and wit (optional, OpenAI):** put your key in a file called `.env`
in this folder, as `openai=sk-...`. ORCA then speaks with an OpenAI voice that is
directed to be deadpan and sarcastic, with pauses before the punchline, and thinks
with a small OpenAI model. Both are the cheap ones (`gpt-4o-mini-tts` and
`gpt-4.1-mini`). Every spoken line is saved in `voice_cache/`, so each one is paid
for once; the stock lines are recorded in the background the first time you run.
What you say to ORCA and the state of the game are sent to OpenAI. The voice,
models and the acting note (`VOICE_STYLE`) are in `config.py`. With no key, no
credit or no internet, ORCA uses the Mac voice and the options below.

**Local brain (optional):** if [Ollama](https://ollama.com) is running with a
model installed (e.g. `ollama pull llama3.2`), ORCA uses it for "why" questions,
follow-ups, and requests in your own words ("it's too bright in here" turns the
lights off). Readings such as counts and positions still come straight from the
game, not the model. The HUD shows `brain: local LLM`. A bigger model gives better
answers: install one and set `OLLAMA_MODEL` in `config.py`.

## Keys

`SPACE` mic · `L` lights · `F` fine · `V` view · `M` mark · `C` calibrate ·
`X` centre view · `P` point of view · `K` mouse mode · `T` camera preview · `-`/`+` eye sensitivity ·
`H` help · `ESC` quit

## Experiment data

Every run writes `logs/session_<time>_<condition>.csv`:

- what Whisper heard, and which commands fired
- every gesture change and grip outcome (secured / damaged / slipped / stored / dropped)
- gaze and camera position twice a second, plus what you looked at
- keyboard vs voice use, and everything ORCA said
- contact with a specimen, collisions with the seabed, and the time to clear each set of specimens

In phone VR mode it also has: session, calibration and dive start and end; head orientation
and hand position ten times a second (`VR_LOG_HZ`); each significant head movement; hand
detected / lost / control frozen / resumed; pinch start and release; precision on and off.
Hand data is joints and events only, never video.

### Experiment ideas
1. **Gaze vs keys for looking around:** time to find and store 3 specimens.
2. **Gesture force:** how often do people crush fragile specimens or fail on urchins?
   Does ORCA's dwell hint ("fragile, pinch gently") reduce errors?
3. **Wake word vs push-to-talk:** count accidental commands while two people chat.
4. **Sensory load:** ORCA talking vs "Orca, quiet" (captions only), measured with
   workload and errors.
5. **Field of view:** wide vs narrow (gaze-following) view, measured with time,
   errors and cybersickness.
6. **Whisper accuracy:** dictate notes with species names across speakers and models.

## Files

| File | What it does |
|---|---|
| `main.py` | the game: world, gripper, specimens, HUD |
| `scene.py` | the underwater world: photo backdrop, light rays, kelp, seabed, fish |
| `assets/` | the kelp forest photo and seabed textures (credits in `assets/CREDITS.md`) |
| `tracking.py` | webcam → gaze + hand + gesture (MediaPipe) |
| `voice.py` | microphone → Whisper → text |
| `orca.py` | the assistant: commands, answers, personality, speech |
| `config.py` | all the settings (sensitivity, model, voice…) |
| `logger.py` | CSV experiment logs |
| `startup.py` | the question at the start: phone VR or standard screen |
| `vr/head_input.py` | HeadInput: the phone's orientation sensors → where you look |
| `vr/hand_input.py` | HandInput: the phone's rear camera → your hand's joints and shape |
| `vr/voice_input.py` | VoiceInput: the phone's microphone → Whisper → ORCA, and ORCA's voice back |
| `vr/teleop.py` | TeleoperationController: combines head, hand and the research condition |
| `vr/calibration.py` | the short sequence before the dive |
| `vr/server.py`, `vr/session.py` | the link to the phone, and its ties to the game |
| `vr/web/` | the page the phone opens |
