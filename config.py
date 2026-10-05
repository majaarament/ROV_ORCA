"""
All the knobs in one place. Change these, then re-run main.py.
"""
import os
import sys

# ---------------- window ----------------
WIDTH, HEIGHT = 1280, 720
FPS = 60
FULLSCREEN = False
START_POV = "third"                # "third" = watch the ROV from outside, "first" = you are the robot

# ---------------- world ----------------
WORLD_W, WORLD_H = 3200, 1100      # bigger than the screen: you look around it
SEABED_Y = WORLD_H - 140

# ---------------- webcam tracking ----------------
CAMERA_INDEX = 0                   # try 1 if you have an external webcam
CAMERA_SIZE = (640, 480)

# Gaze -> view panning
GAZE_GAIN_X = 3.0                  # bigger = less eye/head movement needed
GAZE_GAIN_Y = 3.5
GAZE_SMOOTHING = 0.18              # 0..1, lower = smoother but laggier
GAZE_DEAD_ZONE = 0.35              # look this far from centre before the view pans
PAN_SPEED = 16                     # pixels per frame at full gaze
DWELL_SECONDS = 1.2                # look at a specimen this long to inspect it

# Hand -> gripper
HAND_BOX = (0.15, 0.10, 0.85, 0.80)  # part of the camera image mapped to the screen
PINCH_RATIO = 0.33                 # thumb-index distance / palm size: closer than this = pinch
PINCH_RELEASE = 0.43               # ...and it stays a pinch until they are this far apart
FIST_RATIO = 1.25                  # fingertip-wrist distance / palm size: closer than this = fist
FIST_RELEASE = 1.40                # ...and it stays a fist until the fingers open this far
GESTURE_FRAMES = 2                 # camera frames a gesture must hold before it counts
# Smoothing (rest, speed): "rest" = how much jitter gets through while your hand is still
# (lower = steadier), "speed" = how quickly fast movement switches the smoothing off
# (higher = less lag). Hand feels shaky: lower rest. Feels like it trails behind: raise speed.
SMOOTH_POSITION = (1.2, 6.0)
SMOOTH_FINGERS = (1.8, 1.2)
HAND_COAST = 0.25                  # seconds a hand keeps its pose after the camera loses it
FINE_SCALE = 0.25                  # motion scaling in fine mode

# ---------------- voice ----------------
WHISPER_MODEL = "base.en"          # tiny / base.en / small.en
WAKE_WORD_MODE = "--push" not in sys.argv   # say "Orca, ..." (default) or push-to-talk
WAKE_WORDS = r"\b(orca|orcas|orka|orker|okra|alca|orchid|or ca|hey orca)\b"
CONDITION = "wake_word" if WAKE_WORD_MODE else "push_to_talk"

# ---------------- assistant voice ----------------
# ORCA uses the best voice it can get, in this order:
#   1. OpenAI (needs a key with credit, see below): a voice that acts the lines
#   2. Piper: a free neural voice that runs on this Mac and sounds like a person
#   3. the built-in macOS voice: always there, sounds like a machine
CAN_SPEAK = sys.platform == "darwin"
PIPER_VOICE = "voices/en_US-ryan-high.onnx"   # other voices: https://rhasspy.github.io/piper-samples
PIPER_PACE = 1.08                  # above 1 = slower, more deadpan
PIPER_EXPRESSION = 0.75            # how much the pitch and rhythm vary: lower = flatter
PIPER_PAUSE = 0.30                 # seconds of silence between sentences (longer before a punchline)
PREFERRED_VOICES = ["Daniel", "Karen", "Samantha"]   # macOS voices, first found wins
VOICE_RATE = 185
COMMENT_GAP = 6                    # seconds of silence before ORCA volunteers another hint
ORCA_TALK = "less"                 # what ORCA says without being asked:
                                   #   "all"   everything: hints, praise, remarks about the fish
                                   #   "less"  mistakes, lost tracking, and each species' grip the first time you look at it
                                   #   "asked" nothing: it only answers you

# ---------------- OpenAI (optional): ORCA's voice and wit ----------------
# Put your key in a file called .env next to this one, as   openai=sk-...
# With a key, ORCA speaks with an OpenAI voice that can act (pauses, deadpan, timing) and
# thinks with a small OpenAI model. Without one, or offline, it falls back to the Mac voice
# and the options below. Both models here are the cheap ones.
def _key():
    for name in ("OPENAI_API_KEY", "openai"):
        if os.environ.get(name):
            return os.environ[name].strip()
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")) as f:
            for line in f:
                name, _, value = line.partition("=")
                if name.strip().lower() in ("openai", "openai_api_key", "openai_key"):
                    return value.strip().strip("'\"")
    except OSError:
        pass
    return ""

OPENAI_KEY = _key()
OPENAI_VOICE_MODEL = "gpt-4o-mini-tts"
OPENAI_VOICE = "ash"               # also worth trying: onyx (deeper), fable (British), sage, ballad
OPENAI_CHAT_MODEL = "gpt-4.1-mini"
VOICE_PREWARM = True               # record ORCA's stock lines once at start-up so they play instantly
# How the voice should act. This is a director's note to the voice model, not something it says.
VOICE_STYLE = """You are voicing ORCA, the AI aboard a small research submarine, talking to the one scientist you work with. Play ORCA as a person, not as a computer: a real, slightly tired colleague speaking quietly into a headset, with natural breath, natural rhythm and the small imperfections of someone talking off the cuff. Nothing announced, nothing read out.

Character: deadpan and sarcastic on the surface, fiercely loyal underneath. You have seen everything, you are impressed by very little, and you would still go down with the ship for this person.

Delivery: low, relaxed and conversational. Underplay every joke; the more casually you throw it away, the funnier it is. Timing matters more than emphasis: a short beat at each full stop, a slightly longer one before a punchline or a one-word sentence. Let sentences fall at the end instead of lifting. A quiet breath or a faint weary sigh now and then is welcome.

When the line is a warning or bad news, drop the act: say it plainly and a little quicker, like someone who has their back. When the line is praise, sound as if it costs you something to admit it."""

# Optional: a local LLM through Ollama (https://ollama.com) for free-form
# questions. If Ollama isn't running, ORCA uses its built-in answers.
USE_LLM = True
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = None                # None = pick the first installed model

# ---------------- phone VR mode (python3 main.py --vr) ----------------
# The Mac stays the robot: it runs the sea, ORCA and the logs, and draws the view for each eye.
# The phone in the cardboard viewer is the headset: it shows that view and sends back where your
# head points, what its rear camera sees of your hand, and what its microphone hears.
MODE = "standard"                  # "standard" or "vr": chosen when main.py starts (see startup.py)
VR_PORT = 8443                     # the phone opens https://<this Mac>:8443
VR_EYE_SIZE = (800, 720)           # pixels drawn for each eye
VR_STREAM_FPS = 30                 # pictures sent per second; the phone re-aims the last one in between
VR_JPEG_QUALITY = (38, 62)         # lowest and highest picture quality; drops when the Wi-Fi can't keep up
VR_EYE_SEPARATION = 16             # pixels between the two eyes' viewpoints: more = deeper, less = easier on the eyes
VR_EYE_NUDGE = 0.0                 # slides the two pictures together (+) or apart (-), as a share of one eye's width.
                                   # Seeing double? Tune it live with [ and ] on the Mac, then put the number here
VR_WORLD_H = 1800                  # the sea is taller in VR, so there is something to see when you look up or down
VR_SEABED_BELOW = 560              # how much of that is seabed under the seabed line: more = you can look further down
VR_OVERSCAN = 1.14                 # the phone shows the middle of each picture, so fast head turns have a margin
VR_LENS_K = (0.10, 0.04)           # counter-bulge for the viewer's lenses; (0, 0) = none

# Head -> view
VR_FOV_DEG = 80                    # how much of your surroundings one eye's picture should span
VR_HEAD_GAIN = 1.5                 # 1 = the sea turns exactly as far as your head; more = less neck needed
VR_ROLL_LIMIT_DEG = 20             # the horizon stays level up to this much head tilt
VR_HEAD_SIGNS = (1, 1, 1)          # flip yaw, pitch or roll with -1 if a phone reports it backwards
VR_HEAD_MOVE_DPS = 40              # turning faster than this is logged as a "significant head movement"
VR_HEAD_FAST_DPS = 170             # turning faster than this: the hand camera is a blur, so the gripper waits

# Hand -> gripper (the rear camera)
VR_CAMERA_SIZE = (480, 360)        # what the phone's hand tracker looks at: small is fast
VR_HAND_FPS = 20                   # hand detections per second; the gripper is smoothed in between
VR_CAMERA_HFOV_DEG = 68            # how wide the rear camera sees: used to tell a head turn from a hand move
VR_CAMERA_LAG = 0.04               # seconds the camera picture is behind the motion sensors
VR_SWAP_HANDEDNESS = True          # MediaPipe names hands for a mirrored selfie; the rear camera isn't one
VR_HAND_GAIN = 1.5                 # how far the gripper moves for a hand movement, at movement scale 1
VR_HAND_DEAD_ZONE = 0.004          # hand jitter smaller than this (radians of view) doesn't move the gripper
VR_HAND_MAX_JUMP = 0.30            # a bigger leap than this (radians) between two detections is a glitch
VR_HAND_FOLLOW = 0.35              # how quickly the gripper catches up between detections (0..1 per frame)
VR_HAND_LOST_WARNING = 1.0         # seconds without a hand before "HAND NOT VISIBLE" appears

# The research condition: what a pinch does. Only vr/teleop.py reads these.
#   "grip"      = pinch is the gentle grip (as on the desktop), movement always at the normal scale
#   "precision" = pinch slows the gripper to the precision scale; a fist is the only grip
VR_PINCH_ACTION = "grip"
MOVEMENT_SCALE_NORMAL = 1.0
MOVEMENT_SCALE_PRECISION = 0.2     # also used by "Orca, fine mode" in VR

# Voice in VR
VR_MIC = "phone"                   # "phone" = the headset's microphone, "mac" = this computer's
VR_AUDIO_OUT = "phone"             # where ORCA's voice plays
VR_LOG_HZ = 10                     # head and hand samples written to the log per second


def use_vr():
    """Switch every setting that differs in phone VR mode. Called once, before the game is built."""
    global MODE, WIDTH, HEIGHT, WORLD_H, SEABED_Y, START_POV, CONDITION
    MODE = "vr"
    WIDTH, HEIGHT = VR_EYE_SIZE
    WORLD_H, SEABED_Y = VR_WORLD_H, VR_WORLD_H - VR_SEABED_BELOW
    START_POV = "first"                # you are the robot
    CONDITION = f"vr_{VR_PINCH_ACTION}_{CONDITION}"
