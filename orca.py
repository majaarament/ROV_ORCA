"""
ORCA - Onboard Research & Collection Assistant.

The ROV's shipboard AI: deadpan, sarcastic, and entirely on your side. It:
  * turns what you say into commands for the game
  * answers questions using what's actually happening in the scene
  * comments on events (secured, damaged, fish fleeing...)
  * speaks one line at a time with the most human voice available: OpenAI if there is a key
    with credit in .env, else a neural voice that runs on this Mac (Piper), else the macOS voice
  * thinks with a small OpenAI model, or a local one (Ollama), when its built-in answers run out
"""
import hashlib
import json
import os
import queue
import random
import re
import subprocess
import threading
import time
import ssl
import urllib.error
import urllib.request
import wave

import config
import logger

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "voice_cache")
try:                                    # Python on macOS often can't find the system's certificates
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL = ssl.create_default_context()


def openai_post(path, body, timeout):
    req = urllib.request.Request("https://api.openai.com/v1/" + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + config.OPENAI_KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e)["error"]["message"]
        except Exception:
            detail = ""
        raise RuntimeError(f"OpenAI said {e.code}. {detail[:140]}") from None


# Short sentences and full stops on purpose: they are where the voice takes its pauses.
LINES = {
    "greeting": ["ORCA online. Look at the middle of the screen while I calibrate your eyes. No pressure."],
    "calibrated": ["Calibrated. Your eyes steer now. Look at an edge, and we go there.",
                   "Calibrated. Look towards the edges to steer. I'll do the worrying."],
    "lights_on": ["Lights on. Subtle as ever.", "Lights on. The fish have filed a complaint."],
    "lights_off": ["Lights off. The fish say thank you. I'm paraphrasing.", "Going dark. Very mysterious."],
    "fine": ["Fine control. Surgeon mode.", "Precision mode. Finally. Some restraint."],
    "normal": ["Normal control. Mind the fragile things."],
    "narrow": ["Narrow view. Tunnel vision. But on purpose."],
    "wide": ["Wide view. Behold. Everything."],
    "mark": ["Marker {n} placed. I'll remember it, so you don't have to."],
    "note": ["Logged.", "Noted. Riveting stuff."],
    "centre": ["View centred."],
    "pov_first": ["First person. Those are your hands now. Try not to wave.",
                  "First person. Welcome to being me."],
    "pov_third": ["Outside view. Yes, that's us. We look great."],
    "calibrate": ["Look at the middle of the screen. Hold still. Stiller."],
    "quiet": ["Going quiet. I'll just think my comments."],
    "speak": ["Voice back on. You missed me. It's fine. You can say it."],
    "help": ["Say my name, then lights, fine or normal mode, wide or narrow view, mark, "
             "note, calibrate, status or quiet. The full list is on screen. You're welcome."],
    "secured": ["Secured. Look at you.", "Got it. No casualties.",
                "Secured. Textbook. I'd clap. But. Gripper."],
    "damaged": ["That was a fist. That... was a {species}.",
                "Too much force. The {species} is now several smaller ones. Pinch. Don't punch."],
    "slipped": ["It slipped. Urchins need a fist. I don't make the rules.",
                "Pinching an urchin. Bold. Wrong. But bold. Try a fist."],
    "stored": ["Stored. That's {stored}. I'm almost impressed.",
               "{stored} in the drawer. Somewhere, a grant committee weeps with joy."],
    "dropped": ["Dropped. Gravity one. Science nil.",
                "Dropped. It's fine. It lived on the floor anyway."],
    "in_reach": ["In reach. Pinch gently. Emphasis on gently."],
    "in_reach_sturdy": ["Urchin in reach. This one, you can actually squeeze. Make a fist."],
    "near_bay": ["Over the drawer. Open your hand. That's the whole trick."],
    "fish_fleeing": ["The fish are leaving. It's the floodlights. It's always the floodlights."],
    "high_disturbance": ["Disturbance is high. We're guests here. Loud ones.",
                         "That's a lot of sediment. Slow down. I'm not going anywhere."],
    "face_lost": ["I can't see your face. If you've left... rude."],
    "hand_lost": ["I can't see your hand. I need that. It's sort of the whole job."],
    "unknown": ["Didn't catch that. I'm brilliant. Not telepathic.",
                "That didn't parse. Try my name, then help."],
    "dont_know": ["No idea. And I hate saying that. Ask me about the specimens, or the fish."],
    "joke": ["Why don't kelp forests ever get lonely? They've always got their holdfast friends.",
             "What did the ocean say to the ROV? Nothing. It just waved.",
             "I'd tell you a joke about sediment. But it would only stir things up.",
             "I asked the urchin for advice. It was very pointed."],
    "greeting_mouse": ["ORCA online. No camera. So the mouse is your hand, and the arrow keys are your eyes. Retro."],
    "all_done": ["That's every specimen in range. More are drifting in. Nature restocks."],
    "greet_back": ["Hello, scientist. Still here. Still watching your back."],
    "thanks": ["Anytime.", "Always. That's what I'm for.", "Don't mention it. Seriously. I'll get ideas."],
    # phone VR mode: a line ending in _vr replaces the one without it (see line())
    "cal_look": ["Calibration. Look straight ahead, and hold still."],
    "cal_hand": ["Good. Now hold one hand up, in front of the headset."],
    "cal_open": ["I see it. Open your hand."],
    "cal_pinch": ["Now pinch. Thumb and index finger together."],
    "cal_ready": ["Ready. You're the robot now. Turn your head to look around. Your hand is its hand."],
    "calibrate_vr": ["Look straight ahead. That's forward now."],
    "centre_vr": ["View centred. Wherever you're facing, that's forward."],
    "hand_lost_vr": ["I can't see your hand. Hold it up, in front of the headset."],
    "help_vr": ["Say my name, then lights, fine or normal mode, mark, note, centre view, status or quiet. "
                "Or just ask me what you're looking at."],
}

FACTS = {
    "glass anemone": "Translucent and very fragile. Pinch gently.",
    "lantern polyp": "Glows to lure plankton. Fragile, pinch gently.",
    "ribbon kelp sprout": "A baby kelp. It tears at the holdfast, so pinch gently.",
    "velvet urchin": "Spiny and sturdy. Needs a firm grip, so make a fist.",
}
STURDY = {"velvet urchin"}

# event -> minimum seconds between two spoken lines of that kind
COOLDOWN = {"in_reach": 20, "in_reach_sturdy": 20, "near_bay": 20, "fish_fleeing": 60,
            "high_disturbance": 60, "face_lost": 45, "hand_lost": 45, "unknown": 12,
            "dwell": 6}
# unprompted comments: these wait their turn instead of talking over ORCA's last line
HINTS = {"in_reach", "in_reach_sturdy", "near_bay", "fish_fleeing", "high_disturbance",
         "face_lost", "hand_lost", "all_done"}

# what ORCA still says unprompted when config.ORCA_TALK is "less": mistakes, and things that stop the work
IMPORTANT = {"damaged", "slipped", "dropped", "face_lost", "hand_lost", "calibrated"}

# voice commands: (regex, command for the game)
COMMANDS = [
    (r"\blights?\s+(off|out)\b|\b(turn|switch)\s+(off|out)\s+(the\s+)?lights?\b|\bgo dark\b", "lights_off"),
    (r"\blights?\s+on\b|\b(turn|switch)\s+on\s+(the\s+)?lights?\b", "lights_on"),
    (r"\b(fine|find|precision|precise)\b(\s+mode)?", "fine"),
    (r"\bnormal\b(\s+mode)?", "normal"),
    (r"\bnarrow\b(\s+view)?|\bfocus\b", "narrow"),
    (r"\bwide\b(\s+view)?|\bpanoram\w*", "wide"),
    (r"\b(mark|marker|pin)\b(\s+(the\s+)?sample)?", "mark"),
    (r"\bcalibrat\w*", "calibrate"),
    (r"\b(centre|center|reset)\b(\s+(the\s+)?view)?", "centre"),
    (r"\b(quiet|mute|silent|silence|shush|hush|shut up|stop talking)\b", "quiet"),
    (r"\b(unmute|speak up|talk to me|voice on)\b", "speak"),
    (r"\b(help|commands)\b", "help"),
    (r"\b(status|report)\b", "status"),
    (r"\bfirst person\b|\b(robot|pilot|hands?)\s+(view|pov|mode)\b|\bbe the robot\b", "pov_first"),
    (r"\bthird person\b|\b(outside|external|chase|drone|distance)\s+(view|pov|mode)\b|\bfrom a distance\b", "pov_third"),
    (r"\b(switch|change|toggle|swap)\s+(the\s+)?(view|pov|camera|perspective)\b", "pov_toggle"),
    (r"\bjoke\b", "joke"),
]

NOTE_RE = re.compile(r"^\W*((?:take|make) a note|note|log|write down|record)\b(?:\s+that\b)?[\s,:.\-]*(.*)$",
                     re.IGNORECASE | re.S)
QUESTION_START = re.compile(
    r"^(what|whats|where|wheres|how|hows|is|are|was|did|do|does|who|whos|why|which|any|should|can you see|tell me)\b")
# questions that want reasoning rather than a reading off the instruments
OPEN_QUESTION = re.compile(
    r"^(why|how come|how does|how do|what does|what if|what happens|what should|should|explain|tell me about)\b")

ADVICE_RE = (r"\bwhat (should|do|can) i do\b|\bwhat now\b|\bnext\b"
             r"|\bhow (do|should|can) i (grab|grip|pick|hold|get|take|collect)\b")

# what the LLM may do on its own when it understands a request the patterns missed
LLM_ACTIONS = {
    "lights_on": "floodlights on, for when it is too dark to see",
    "lights_off": "floodlights off, for when it is too bright or the fish need calming",
    "fine": "fine control, for delicate work or unsteady hands",
    "normal": "back to full-size hand movement",
    "narrow": "narrow view that follows the eyes",
    "wide": "wide view of the whole scene",
    "mark": "drop a marker where the gripper is",
    "centre": "bring the view back to the middle",
    "pov_first": "first-person view through the robot's eyes, with its two hands",
    "pov_third": "outside view, watching the ROV from a distance",
    "status": "read out the instrument status",
    "help": "list the voice commands",
    "joke": "tell a joke",
}
REPLY_SCHEMA = {"type": "object",
                "properties": {"say": {"type": "string"},
                               "action": {"type": "string", "enum": ["none", *LLM_ACTIONS]}},
                "required": ["say", "action"], "additionalProperties": False}
HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "bye", "okay", "so"}


def clean(text):
    text = text.lower().replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def distance_words(px):
    mm = abs(px)            # 1 world pixel ~ 1 mm
    if mm >= 1000:
        return f"{mm / 1000:.1f} metres"
    return f"{max(5, int(mm / 10) // 5 * 5)} centimetres"


class Orca:
    def __init__(self):
        self.commands = queue.Queue()     # -> game
        self.world = {}                   # <- game (snapshot, updated often)
        self.voice_on = True
        self.status = "LOADING"
        self.you_text, self.you_time = "", -1e9
        self.orca_text, self.orca_time = "", -1e9
        self.speech_end = 0.0             # when ORCA last stopped talking
        self.speaking = False
        self._last = {}
        self._learned = set()             # things the scientist has got right: no more hints for those
        self._history = []                # last few exchanges, so follow-up questions make sense
        self._tts = queue.Queue()
        self.voice_name = self._pick_voice()
        self.llm_model = None             # the brain in use right now (shown on the HUD)
        self.local_model = None           # Ollama model, used when OpenAI isn't available
        self._cloud_retry = 0.0           # after a failed OpenAI voice call, don't try again before this
        self._brain_retry = 0.0           # same for the OpenAI brain
        self._piper = None                # the on-device neural voice, loaded the first time it's needed
        self._piper_lock = threading.Lock()
        self.player = None                # phone VR: plays a spoken line on the headset instead of here
        threading.Thread(target=self._tts_worker, daemon=True).start()
        if config.CAN_SPEAK and config.VOICE_PREWARM:
            threading.Thread(target=self._prewarm, daemon=True).start()
        if config.USE_LLM:
            threading.Thread(target=self._find_llm, daemon=True).start()

    # ================= speaking =================
    def say(self, text, source_event=""):
        self.orca_text, self.orca_time = text, time.time()
        logger.log("orca", source_event or "reply", text)
        print("ORCA:", text)
        # don't let a backlog build up: keep only the newest couple of lines
        while self._tts.qsize() > 1:
            try:
                self._tts.get_nowait()
            except queue.Empty:
                break
        self._tts.put(text)

    def line(self, key, **fmt):
        if config.MODE == "vr" and key + "_vr" in LINES:
            key += "_vr"
        text = random.choice(LINES[key])
        try:
            return text.format(**fmt)
        except (KeyError, IndexError):
            return text

    def event(self, key, **fmt):
        """Comment on something that happened in the game (throttled)."""
        now = time.time()
        sp = fmt.get("species", "specimen")
        # hints stop once the scientist has shown they know the grip, and come back after a mistake
        if key == "secured":
            self._learned.add(sp)
        elif key in ("damaged", "slipped"):
            self._learned.discard(sp)
        elif key == "stored":
            self._learned.add("drawer")
        if key in ("in_reach", "in_reach_sturdy") and sp in self._learned:
            return
        if key == "near_bay" and "drawer" in self._learned:
            return
        if config.ORCA_TALK != "all" and key != "unknown":      # "unknown" answers something you said
            first_look = key == "dwell" and sp not in self._learned      # the grip advice, until you've got it right
            if config.ORCA_TALK != "less" or not (key in IMPORTANT or first_look):
                return
        if key in HINTS and (self.speaking or now - self.orca_time < config.COMMENT_GAP):
            return
        cd = COOLDOWN.get(key, 0)
        if now - self._last.get(key, -1e9) < cd:
            return
        self._last[key] = now
        if key == "dwell":
            fact = "" if sp in self._learned else " " + FACTS.get(sp, "")
            self.say(f"{sp.capitalize()}.{fact}", "dwell")
            return
        self.say(self.line(key, **fmt), key)

    def _pick_voice(self):
        if not config.CAN_SPEAK:
            return None
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
            names = {l.split()[0] for l in out.splitlines() if l.strip()}
            for v in config.PREFERRED_VOICES:
                if v in names:
                    return v
        except Exception:
            pass
        return None

    def _cloud_speech(self, text):
        """ORCA's acted voice from OpenAI, as an audio file. Every line is kept, so it is only ever
        paid for and waited for once. None = not available right now, use the Mac voice."""
        if not config.OPENAI_KEY or time.time() < self._cloud_retry:
            return None
        recipe = "|".join((config.OPENAI_VOICE_MODEL, config.OPENAI_VOICE, config.VOICE_STYLE, text))
        path = os.path.join(CACHE, hashlib.sha1(recipe.encode()).hexdigest()[:20] + ".mp3")
        if os.path.exists(path):
            return path
        try:
            audio = openai_post("audio/speech", {"model": config.OPENAI_VOICE_MODEL, "voice": config.OPENAI_VOICE,
                                                 "input": text, "instructions": config.VOICE_STYLE,
                                                 "response_format": "mp3"}, timeout=12)
            os.makedirs(CACHE, exist_ok=True)
            tmp = f"{path}.{threading.get_ident()}.tmp"
            with open(tmp, "wb") as f:
                f.write(audio)
            os.replace(tmp, path)
            return path
        except Exception as e:
            print("OpenAI voice unavailable, using the Mac voice for a few minutes:", e)
            self._cloud_retry = time.time() + 300
            return None

    def _local_speech(self, text):
        """The same line from the neural voice on this Mac (Piper): free, private, works offline.
        None = Piper or its voice file isn't installed, use the Mac voice."""
        model = os.path.join(HERE, config.PIPER_VOICE)
        if self._piper is False or not os.path.exists(model):
            return None
        recipe = f"{config.PIPER_VOICE}|{config.PIPER_PACE}|{config.PIPER_EXPRESSION}|{config.PIPER_PAUSE}|{text}"
        path = os.path.join(CACHE, hashlib.sha1(recipe.encode()).hexdigest()[:20] + ".wav")
        if os.path.exists(path):
            return path
        try:
            with self._piper_lock:
                from piper import PiperVoice, SynthesisConfig
                if self._piper is None:
                    import onnxruntime
                    self._piper = PiperVoice.load(model)
                    # two threads only: left alone it takes every core, and then the game stutters
                    # and Whisper is too slow to hear you
                    few = onnxruntime.SessionOptions()
                    few.intra_op_num_threads = 2
                    self._piper.session = onnxruntime.InferenceSession(
                        model, sess_options=few, providers=["CPUExecutionProvider"])
                style = SynthesisConfig(length_scale=config.PIPER_PACE, noise_scale=0.667 * config.PIPER_EXPRESSION,
                                        noise_w_scale=0.8 * config.PIPER_EXPRESSION)
                # spoken as plain commas and full stops; "..." would be read out oddly
                chunks = list(self._piper.synthesize(text.replace("...", ","), syn_config=style))
            os.makedirs(CACHE, exist_ok=True)
            tmp = f"{path}.{threading.get_ident()}.tmp"
            with wave.open(tmp, "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(chunks[0].sample_rate)
                for i, chunk in enumerate(chunks):
                    if i:
                        # comedic timing: a beat between sentences, a longer one before a short punchline
                        punchline = len(chunk.audio_int16_bytes) < chunk.sample_rate * 2 * 0.9
                        gap = config.PIPER_PAUSE * (1.6 if punchline else 1)
                        f.writeframes(b"\0\0" * int(chunk.sample_rate * gap))
                    f.writeframes(chunk.audio_int16_bytes)
            os.replace(tmp, path)
            return path
        except Exception as e:
            print("Neural voice unavailable, using the Mac voice:", e)
            self._piper = False
            return None

    def _prewarm(self):
        """Record the stock lines in the background, so they play without a pause later."""
        species = list(FACTS)
        lines = []
        for options in LINES.values():
            for text in options:
                if "{species}" in text:
                    lines += [text.format(species=sp) for sp in species]
                elif "{" not in text:
                    lines.append(text)
        for sp in species:
            lines += [f"{sp.capitalize()}. {FACTS[sp]}", f"{sp.capitalize()}."]
        for text in lines:
            while self.status == "THINKING":    # never compete with Whisper while it is listening to you
                time.sleep(0.2)
            if not (self._cloud_speech(text) or self._local_speech(text)):
                return                      # no better voice available: nothing to record
            time.sleep(0.2)

    def _tts_worker(self):
        while True:
            text = self._tts.get()
            if not (config.CAN_SPEAK and self.voice_on):
                continue
            path = self._cloud_speech(text) or self._local_speech(text)
            if not self.voice_on:           # told to be quiet while the line was being fetched
                continue
            if path:
                cmd = ["afplay", path]
            else:
                cmd = ["say", "-r", str(config.VOICE_RATE)] + (["-v", self.voice_name] if self.voice_name else []) + [text]
            self.speaking = True
            self.speech_end = time.time() + 60
            try:
                if not (self.player and self.player(path, text)):
                    subprocess.run(cmd)
            except Exception as e:
                print("TTS error:", e)
            self.speaking = False
            self.speech_end = time.time()

    # ================= understanding speech =================
    def heard(self, transcript):
        """Called by the voice thread with each Whisper transcription."""
        text = transcript.strip()
        cleaned = clean(text)
        if cleaned in HALLUCINATIONS:
            return

        # wake word: "Orca, lights on" -> "lights on"
        if config.WAKE_WORD_MODE:
            m = re.search(config.WAKE_WORDS, cleaned)
            if not m:
                logger.log("voice", "ignored_no_wake_word", text)
                print("(no wake word) heard:", text)
                return
            cleaned = cleaned[m.end():].strip()
            # also cut the wake word out of the original text (for notes)
            m2 = re.search(r"(?i)\b(hey\s+)?(orca|orcas|orka|orker|okra|alca|orchid)\b[\s,.:!]*", text)
            text = text[m2.end():] if m2 else text

        self.you_text, self.you_time = transcript, time.time()
        logger.log("voice", "heard", transcript)
        print("Heard:", transcript)

        if not cleaned:
            self.say("Yes?")
            return

        # notes: "Orca, note kelp frond torn near the holdfast"
        note = NOTE_RE.match(text)
        if note and note.group(2).strip():
            body = note.group(2).strip()
            self.commands.put(("note", body))
            logger.log("voice", "note", body)
            self.say(self.line("note"))
            return

        cmds = self.parse(cleaned)
        is_question = bool(QUESTION_START.match(cleaned)) or (text.rstrip().endswith("?") and not cmds)
        polite = re.match(r"^(can|could|would|will) you\b|^please\b", cleaned)

        if is_question and not (polite and cmds):
            logger.log("voice", "question", transcript)
            self._reply(text, self.answer(cleaned, text))
            return

        if not cmds:
            if re.search(r"^(hi|hello|hey|morning|good morning)\b", cleaned):
                self.say(self.line("greet_back"))
            elif re.search(r"\b(thanks|thank you|cheers|good job|nice|well done)\b", cleaned):
                self.say(self.line("thanks"))
            else:
                logger.log("voice", "no_command", transcript)
                # with a local LLM, ORCA works out what was meant; otherwise it says it didn't understand
                reply = self.ask_llm(text)
                if reply:
                    self._reply(text, reply)
                else:
                    self.event("unknown")
            return

        self.say(" ".join(dict.fromkeys(self._do(cmd) for cmd in cmds)))

    def _do(self, cmd):
        """Carry out one command and return what ORCA says about it."""
        logger.log("voice", "command", cmd)
        if cmd == "status":
            return self.status_text()
        if cmd == "joke":
            return self.line("joke")
        if cmd == "quiet":
            self.voice_on = False
            return self.line("quiet")
        if cmd == "speak":
            self.voice_on = True
            return self.line("speak")
        if cmd == "pov_toggle":
            cmd = "pov_third" if self.world.get("pov") == "first" else "pov_first"
        self.commands.put(("cmd", cmd))         # calibrate: the game says "calibrated" when done
        if cmd == "mark":
            return self.line("mark", n=self.world.get("markers", 0) + 1)
        return self.line(cmd)

    def _reply(self, heard, reply):
        self.say(reply)
        self._history += [{"role": "user", "content": heard}, {"role": "assistant", "content": reply}]
        del self._history[:-6]

    @staticmethod
    def parse(cleaned):
        found = []
        for prio, (pattern, cmd) in enumerate(COMMANDS):
            for m in re.finditer(pattern, cleaned):
                found.append((m.start(), prio, m.end(), cmd))
        found.sort()
        chosen, last_end = [], -1
        for start, _, end, cmd in found:
            if start >= last_end:
                chosen.append(cmd)
                last_end = end
        return chosen

    # ================= answering questions =================
    def status_text(self):
        w = self.world
        return (f"Lights {'on' if w.get('lights') else 'off'}, "
                f"{'fine' if w.get('fine') else 'normal'} control, "
                f"{'narrow' if w.get('narrow') else 'wide'} view. "
                f"{w.get('stored', 0)} stored, {w.get('damaged', 0)} damaged. "
                f"Disturbance {self.disturbance_level()}.")

    def tracking_answer(self):
        w = self.world
        face, hand = w.get("face_ok"), w.get("hand_ok")
        if config.MODE == "vr":             # the headset: there are no eyes to see, only where it points
            seen = "I'm following your head and your hand" if hand else "I'm following your head, but I can't see your hand"
        elif face and hand:
            seen = "I can see your eyes and your hand"
        elif face:
            seen = "I can see your eyes, but not your hand"
        elif hand:
            seen = "I can see your hand, but not your eyes"
        else:
            seen = "I can't see you at all"
        return f"{seen}. Gesture: {w.get('gesture', 'unknown').lower()}."

    def disturbance_level(self):
        d = self.world.get("disturbance", 0)
        return "low" if d < 0.3 else "moderate" if d < 0.6 else "high"

    def where_answer(self):
        w = self.world
        if w.get("held"):
            return f"You're holding the {w['held']}. The drawer is up and to the right."
        n = w.get("nearest")
        if not n:
            return "No specimens in range."
        sp, dx, dy, onscreen = n["species"], n["dx"], n["dy"], n["onscreen"]
        if abs(dx) < 60 and abs(dy) < 60:
            return f"The {sp} is right under your hand."
        parts = []
        if abs(dx) > 60:
            parts.append(f"{distance_words(dx)} to your {'right' if dx > 0 else 'left'}")
        if abs(dy) > 60:
            parts.append("lower down" if dy > 0 else "higher up")
        where = " and ".join(parts)
        tail = "" if onscreen else f" Look {'right' if dx > 0 else 'left'} to bring it on screen."
        return f"Nearest is a {sp}, {where}.{tail}"

    def advice(self):
        """The most useful next move, worked out from the scene."""
        w = self.world
        if w.get("held"):
            return f"Carry the {w['held']} to the drawer, up and to the right, then open your hand."
        n = w.get("nearest")
        if not n:
            return "Nothing in range. More specimens will drift in."
        grip = "Make a fist, it's sturdy." if n["species"] in STURDY else "Pinch gently, it's fragile."
        return f"{self.where_answer()} {grip}"

    def answer(self, cleaned, original):
        w = self.world
        # what to do next is worked out from the instruments, which beats a small LLM's guess
        if re.search(ADVICE_RE, cleaned):
            return self.advice()
        # "why...", "should I..." need reasoning, so the LLM goes first when there is one
        open_q = bool(OPEN_QUESTION.match(cleaned))
        if open_q:
            llm = self.ask_llm(original)
            if llm:
                return llm
        rules = [
            (r"\bwhere\b|\bfind\b|\bwhich way\b|\bnearest\b", self.where_answer),
            (r"\b(fish|disturb\w*|environment|ecosystem|sediment|impact)\b",
             lambda: f"Disturbance is {self.disturbance_level()}." +
                     (f" {w['fleeing']} fish are avoiding us." if w.get("fleeing") else " The fish are calm.")),
            (r"\bhow many\b|\bsamples?\b|\bscore\b",
             lambda: f"{w.get('stored', 0)} stored, {w.get('damaged', 0)} damaged, "
                     f"{w.get('remaining', 0)} still out there."),
            (r"\b(what is|whats) (this|that|it)\b|\bwhat am i (looking|holding)\b|\bspecimen\b",
             lambda: (f"A {w['gazed']}. {FACTS.get(w['gazed'], '')}" if w.get("gazed") else
                      f"You're holding a {w['held']}. {FACTS.get(w['held'], '')}" if w.get("held") else
                      "Look at a specimen for a second and I'll tell you.")),
            (r"\blights?\b", lambda: f"The lights are {'on' if w.get('lights') else 'off'}."),
            (r"\bjoke\b", lambda: self.line("joke")),
            (r"\b(who|what) are you\b|\byour name\b",
             lambda: "I'm ORCA, your onboard research and collection assistant."),
            (r"\bhow are you\b|\bhows it going\b",
             lambda: f"All systems nominal. Disturbance {self.disturbance_level()}."),
            (r"\b(see me|tracking|my eyes|my hand|gesture)\b",
             lambda: self.tracking_answer()),
            (r"\b(status|report|doing)\b", self.status_text),
        ]
        for pattern, fn in rules:
            if re.search(pattern, cleaned):
                return fn()
        llm = None if open_q else self.ask_llm(original)
        return llm or self.line("dont_know")

    # ================= optional LLM: OpenAI, or a local one =================
    def _find_llm(self):
        if config.OPENAI_KEY:
            self.llm_model = config.OPENAI_CHAT_MODEL
            print("ORCA brain:", self.llm_model)
        try:
            with urllib.request.urlopen(config.OLLAMA_URL + "/api/tags", timeout=2) as r:
                models = [m["name"] for m in json.load(r).get("models", []) if "embed" not in m["name"]]
        except Exception:
            return
        if not models:
            return
        if config.OLLAMA_MODEL and any(m.startswith(config.OLLAMA_MODEL) for m in models):
            self.local_model = next(m for m in models if m.startswith(config.OLLAMA_MODEL))
        else:
            self.local_model = models[0]
        if not config.OPENAI_KEY:
            self.llm_model = self.local_model
            print("ORCA brain: local LLM", self.llm_model)
            return                          # with OpenAI as the brain, the local one is only a stand-in
        # load it into memory now, so the first question isn't the slow one
        try:
            body = json.dumps({"model": self.local_model, "keep_alive": "30m"}).encode()
            urllib.request.urlopen(config.OLLAMA_URL + "/api/generate", data=body, timeout=60).read()
        except Exception:
            pass

    def situation_text(self):
        w = self.world
        fish = (f"{w['fleeing']} fish are fleeing because the floodlights are on" if w.get("fleeing")
                else "the fish are calm")
        looking = f" The scientist is looking at a {w['gazed']}." if w.get("gazed") else ""
        pov = "first person, through the robot's own hands" if w.get("pov") == "first" else "from outside the ROV"
        return (f"{self.status_text()} {w.get('slipped', 0)} slipped, {w.get('remaining', 0)} specimens "
                f"still out there, {w.get('markers', 0)} markers placed, and {fish}. "
                f"The scientist is watching {pov}. {self.where_answer()}{looking} "
                f"{self.tracking_answer()} Best next move: {self.advice()}")

    def system_prompt(self):
        species = " ".join(f"{name.capitalize()}: {fact}" for name, fact in FACTS.items())
        actions = "\n".join(f"{name}: {what}" for name, what in LLM_ACTIONS.items())
        steer = ("They wear a phone VR headset: they look around by turning their head, move the gripper with their "
                 "hand held up in front of the headset, cannot see a keyboard or a screen of text,"
                 if config.MODE == "vr" else "They steer the view with their eyes, move the gripper with their hand,")
        return f"""You are ORCA, the onboard AI of ROV-6, an underwater robot that a marine ecologist is driving by telepresence to collect specimens from a kelp forest. {steer} and talk to you while their attention is on the work. Everything you say is spoken aloud by a voice actor and interrupts what they are doing, so it has to be brief.

Your character: deadpan, dry and sarcastic, and underneath it fiercely loyal to this scientist. You are a ship's AI who has seen everything, is unimpressed by most of it, and would still go down with the ROV for them. Aim the sarcasm at the situation, the fish, the equipment or yourself, never at the scientist's worth: when they make a mistake, tease once and then hand them the fix, and when something has really gone wrong, drop the act and be plainly on their side. Understatement is funnier than exaggeration. At most one dry remark per reply, and none when they need a fact fast.

Reply in one or two short spoken sentences, 25 words at most in total, with no lists or symbols. Lead with the fact or the action they asked for; the dry remark, if any, comes after it. Write for the ear: short sentences and full stops are where the voice pauses, so put the full stop just before the punchline. Call them "scientist" now and then, not every time.

Their words reach you through speech recognition, so expect the odd misheard word and go with the likeliest meaning.

Answer questions about the scene only from the situation report below, because those numbers come straight from the ROV's instruments. For general marine biology, answer from what you know. If neither covers it, say you don't know.

How the ROV works: a pinch is a gentle grip for fragile specimens and a fist is a firm grip; a fist crushes fragile specimens, and a pinch lets a sturdy one slip. Opening the hand over the sample drawer, up and to the right, stores the sample. The floodlights help the scientist see but scare the fish. Disturbance rises with the lights, stirred-up sediment, fleeing fish and fast panning. Fine control scales hand movement down to a quarter for delicate work. Narrow view shows only where the scientist is looking.

Specimens: {species}

Situation right now: {self.situation_text()}

You can also operate the ROV. When the scientist is asking, in their own words, for something on this list, set "action" to its name, after checking the situation report so you change things in the direction they want:
{actions}
For a question or a remark, set "action" to "none" and answer in "say"."""

    def _chat(self, messages):
        """The model's raw reply: from OpenAI when it is reachable, otherwise from the local model."""
        if config.OPENAI_KEY and time.time() >= self._brain_retry:
            try:
                reply = openai_post("chat/completions", {
                    "model": config.OPENAI_CHAT_MODEL, "messages": messages, "max_tokens": 120,
                    "temperature": 0.8,
                    "response_format": {"type": "json_schema", "json_schema": {
                        "name": "reply", "strict": True, "schema": REPLY_SCHEMA}}}, timeout=12)
                self.llm_model = config.OPENAI_CHAT_MODEL
                return json.loads(reply)["choices"][0]["message"]["content"]
            except Exception as e:
                print("OpenAI brain unavailable, using the stand-in for a few minutes:", e)
                self._brain_retry = time.time() + 300
                self.llm_model = self.local_model
        if not self.local_model:
            return None
        body = json.dumps({"model": self.local_model, "stream": False, "keep_alive": "30m",
                           "format": REPLY_SCHEMA, "messages": messages,
                           "options": {"num_predict": 80, "temperature": 0.3}}).encode()
        req = urllib.request.Request(config.OLLAMA_URL + "/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)["message"]["content"]

    def ask_llm(self, question):
        if not self.llm_model and not (config.OPENAI_KEY and time.time() >= self._brain_retry):
            return None
        self.status = "THINKING"
        try:
            text = self._chat([{"role": "system", "content": self.system_prompt()}, *self._history,
                               {"role": "user", "content": question}])
        except Exception as e:
            print("LLM error:", e)
            return None
        finally:
            self.status = "LISTENING"
        if not text:
            return None
        try:
            out = json.loads(text)
        except ValueError:
            out = {"say": text}
        say = str(out.get("say", "")).strip()
        # small models sometimes put the action name in the sentence instead
        named = {a.replace("_", ""): a for a in LLM_ACTIONS}.get(clean(say).replace(" ", ""))
        action = out.get("action") if out.get("action") in LLM_ACTIONS else named
        if action:
            return self._do(action)
        text = re.sub(r"[*_#`]", "", say).strip()
        if not text:
            return None
        text = " ".join(re.split(r"(?<=[.!?])\s+", text)[:2])
        return text[0].upper() + text[1:] + ("" if text[-1] in ".!?" else ".")
