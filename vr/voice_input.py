"""
VoiceInput: the phone's microphone -> Whisper -> ORCA, and ORCA's voice back to the phone.

Does the job of voice.py when the microphone is in the headset: the phone sends sound (16 kHz,
16-bit, mono) as it hears it, and this cuts it into phrases, then hands each one to the same
Whisper and the same ORCA.
"""
import os
import queue
import subprocess
import tempfile
import threading
import time
import wave

import config
import logger
from voice import load_model, transcribe

RATE = 16000
FRAME = RATE * 30 // 1000           # judge loudness 30 ms at a time
PAUSE = 0.8                         # this much quiet ends a phrase
LIMIT = 6.0                         # longest phrase, in seconds (as in voice.py)


class VoiceInput:
    def __init__(self, orca, server):
        self.orca, self.server = orca, server
        self.mic_on = config.WAKE_WORD_MODE
        self.ready = False
        self.error = ""
        self.mic_ok = None              # None = not known yet, False = the phone gave no microphone
        self._sound = queue.Queue()
        self._played = threading.Event()
        self._playing = 0

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def toggle(self):
        self.mic_on = not self.mic_on
        logger.log("key", "mic", "on" if self.mic_on else "off")
        return self.mic_on

    # ---------- from the phone ----------
    def feed(self, pcm):
        self.mic_ok = True
        self._sound.put(pcm)

    def played(self, clip):
        if clip == self._playing:
            self._played.set()

    # ---------- ORCA's voice, out through the phone ----------
    def play(self, path, text):
        """Play one spoken line on the phone and wait until it has finished (ORCA calls this instead
        of playing it here). False = no phone to play it on, so ORCA uses this Mac's speaker."""
        if not self.server.connected:
            return False
        made = None
        if path is None:                # no recorded voice for this line: have the Mac voice record one
            fd, made = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            voice = ["-v", self.orca.voice_name] if self.orca.voice_name else []
            try:
                subprocess.run(["say", "-r", str(config.VOICE_RATE), *voice, "-o", made,
                                "--data-format=LEI16@22050", text], check=True, timeout=20)
            except Exception:
                os.remove(made)
                return False
            path = made
        try:
            with open(path, "rb") as f:
                data = f.read()
            seconds = 20.0
            if path.endswith(".wav"):
                with wave.open(path) as w:
                    seconds = w.getnframes() / w.getframerate() + 1.5
            self._playing += 1
            self._played.clear()
            self.server.send_audio(self._playing, data)
            self._played.wait(seconds)      # the phone says when it is done; don't wait for ever
            return True
        finally:
            if made:
                os.remove(made)

    # ---------- listening ----------
    def _run(self):
        import numpy as np
        model = load_model(self)
        if model is None:
            return
        self.ready = True
        print("Voice ready (listening through the phone).")
        logger.log("voice", "ready", "phone microphone")

        buffer = np.zeros(0, np.int16)
        phrase, before = [], []         # frames of the phrase so far; the last few quiet frames before it
        floor, quiet, started = 200.0, 0.0, 0.0
        while True:
            try:
                pcm = self._sound.get(timeout=0.5)
            except queue.Empty:
                if not self.mic_on:
                    self.orca.status = "MIC OFF"
                continue
            if not self.mic_on or self.orca.speaking:
                self.orca.status = "SPEAKING" if self.orca.speaking else "MIC OFF"
                phrase, before, buffer = [], [], buffer[:0]
                continue
            if self.orca.status not in ("THINKING",):
                self.orca.status = "LISTENING"
            buffer = np.concatenate((buffer, np.frombuffer(pcm[:len(pcm) // 2 * 2], np.int16)))
            while len(buffer) >= FRAME:
                frame, buffer = buffer[:FRAME], buffer[FRAME:]
                level = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
                loud = level > max(300.0, floor * 3)
                if not phrase:
                    if not loud:
                        floor += 0.05 * (level - floor)         # the room's own noise, tracked while quiet
                        before = (before + [frame])[-10:]
                        continue
                    phrase, quiet, started = before + [frame], 0.0, time.time()
                    before = []
                    continue
                phrase.append(frame)
                quiet = 0.0 if loud else quiet + FRAME / RATE
                if quiet < PAUSE and len(phrase) * FRAME / RATE < LIMIT:
                    continue
                samples = np.concatenate(phrase).astype(np.float32) / 32768.0
                phrase = []
                if self.orca.speech_end > started or self.orca.speaking:
                    logger.log("voice", "dropped_orca_was_talking")
                    continue        # that was ORCA hearing itself
                if len(samples) < RATE * (PAUSE + 0.25):
                    continue        # a click or a cough, not a sentence
                try:
                    transcribe(self.orca, model, samples)
                except Exception as e:
                    print("Voice error:", e)
                self.orca.status = "LISTENING"
                while not self._sound.empty():      # skip what piled up while Whisper was working
                    self._sound.get_nowait()
                buffer = buffer[:0]
