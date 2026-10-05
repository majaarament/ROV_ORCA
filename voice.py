"""
Microphone + Whisper, in a background thread.

Wake-word mode (default): the mic is always on, but ORCA only reacts to
sentences that contain its name: "Orca, lights on".
Push-to-talk mode (python3 main.py --push): mic only on while toggled with SPACE.
SPACE in wake-word mode mutes / unmutes the mic.
"""
import threading
import time

import config
import logger

# Kept short on purpose: Whisper sometimes "repeats" its prompt on pure
# noise, and a prompt full of commands would trigger them by itself.
PROMPT = "A scientist talking to Orca, the robot's assistant."


def load_model(voice):
    """Whisper, ready to use, or None (with voice.error saying why)."""
    try:
        import whisper
        print("Loading Whisper model", config.WHISPER_MODEL, "...")
        return whisper.load_model(config.WHISPER_MODEL)
    except ImportError as e:
        voice.error = f"voice libraries missing ({e})"
    except Exception as e:
        voice.error = f"Whisper model failed to load: {e}"
    voice.orca.status = "NO VOICE"
    print(voice.error)
    return None


def transcribe(orca, model, samples):
    """One stretch of sound (16 kHz, -1..1) -> Whisper -> ORCA."""
    orca.status = "THINKING"
    started = time.time()
    result = model.transcribe(samples, language="en", fp16=False, initial_prompt=PROMPT)
    text = result["text"].strip()
    segs = result.get("segments") or []
    no_speech = sum(s.get("no_speech_prob", 0) for s in segs) / max(len(segs), 1)
    took = f"{len(samples) / 16000:.1f}s of sound, {time.time() - started:.1f}s to transcribe"
    if no_speech > 0.6 or "robot's assistant" in text.lower():
        logger.log("voice", "dropped_as_noise", took)
        return              # noise, or Whisper echoing its prompt
    logger.log("voice", "transcribed", took)
    if text:
        orca.heard(text)


class Voice:
    def __init__(self, orca):
        self.orca = orca
        self.mic_on = config.WAKE_WORD_MODE    # push-to-talk starts off
        self.ready = False
        self.error = ""

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def toggle(self):
        self.mic_on = not self.mic_on
        logger.log("key", "mic", "on" if self.mic_on else "off")
        return self.mic_on

    def _run(self):
        try:
            import numpy as np
            import speech_recognition as sr
        except Exception as e:
            self.error = f"voice libraries missing ({e})"
            self.orca.status = "NO VOICE"
            return
        model = load_model(self)
        if model is None:
            return

        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300
        recognizer.dynamic_energy_threshold = True
        try:
            microphone = sr.Microphone()
            while self.orca.speaking:           # measure the room, not ORCA's greeting
                time.sleep(0.1)
            with microphone as source:
                recognizer.adjust_for_ambient_noise(source, duration=1)
        except Exception as e:
            self.error = f"microphone problem: {e}"
            self.orca.status = "NO MIC"
            return

        self.ready = True
        print("Voice ready.")
        logger.log("voice", "ready", f"energy threshold {recognizer.energy_threshold:.0f}")
        while True:
            try:
                if not self.mic_on:
                    self.orca.status = "MIC OFF"
                    time.sleep(0.1)
                    continue
                if self.orca.speaking:
                    self.orca.status = "SPEAKING"
                    time.sleep(0.05)
                    continue
                self.orca.status = "LISTENING"

                rec_start = time.time()
                with microphone as source:
                    audio = recognizer.listen(source, timeout=4, phrase_time_limit=6)
                if not self.mic_on:
                    continue
                if self.orca.speech_end > rec_start or self.orca.speaking:
                    logger.log("voice", "dropped_orca_was_talking")
                    continue        # that was ORCA hearing itself

                raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                transcribe(self.orca, model, samples)
            except sr.WaitTimeoutError:
                pass
            except Exception as e:
                print("Voice error:", e)
                time.sleep(0.5)
