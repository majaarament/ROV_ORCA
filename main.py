"""
ROV-6 · ORCA  —  eye-tracked, gesture-controlled telepresence prototype
Group 6 · Telepresence System for Marine Ecologists

  LOOK      towards the edge of the screen   -> the view pans that way
  HAND      move it in front of the webcam   -> moves the robot gripper
  PINCH     thumb + index together           -> gentle grip (fragile specimens)
  FIST      close your hand                  -> firm grip  (sturdy urchins only!)
  OPEN      open your hand                   -> let go
  TALK      "Orca, lights on" / "Orca, where is the specimen?"

  TWO POINTS OF VIEW (P, or "Orca, first person" / "Orca, outside view"):
    outside       you watch the ROV fly over the seabed from a distance
    first person  you are the robot: its two hands reach in from the sides

  TASK: collect specimens into the SAMPLE DRAWER (top right of the arm)
        without damaging them or disturbing the ecosystem.

  PHONE VR MODE (python3 main.py --vr): the phone in a cardboard viewer is the headset.
    Your HEAD turns the view, your HAND in front of its rear camera moves the gripper,
    and you TALK to ORCA through its microphone. See vr/ and the README.

Run:   python3 main.py            (asks: phone VR or standard screen; wake word "Orca")
       python3 main.py --vr       (phone VR mode)
       python3 main.py --standard (standard screen mode)
       python3 main.py --push     (push-to-talk with SPACE)
Keys:  SPACE mic   L lights   F fine   V view   M mark   C calibrate
       X centre view   P point of view   K mouse mode   T camera preview
       - / + eye sensitivity   H help   ESC quit
"""
import math
import random
import sys
import time

import pygame

import config
import startup

if __name__ == "__main__" and startup.choose() == "vr":
    config.use_vr()                 # before the rest is imported: the size of the view depends on it

import logger
from orca import Orca, FACTS
from scene import FISH_KINDS, Scene, fish_frames, seabed_at
from tracking import Tracker
from voice import Voice

W, H = config.WIDTH, config.HEIGHT
WORLD_W, WORLD_H = config.WORLD_W, config.WORLD_H

SPECIES = {
    "glass anemone":      dict(colour=(200, 140, 230), glow=(70, 30, 100), sturdy=False),
    "lantern polyp":      dict(colour=(255, 190, 90), glow=(110, 60, 10), sturdy=False),
    "ribbon kelp sprout": dict(colour=(130, 220, 110), glow=(25, 80, 25), sturdy=False),
    "velvet urchin":      dict(colour=(170, 90, 190), glow=(55, 15, 70), sturdy=True),
}

DRAWER = pygame.Rect(W // 2 + 110, 64, 180, 90)
CYAN, ORANGE, GREY = (0, 220, 255), (255, 150, 40), (190, 195, 200)


# First-person hands are drawn from 21 joints, numbered like MediaPipe's: 0 wrist, 1-4 thumb,
# 5-8 index, 9-12 middle, 13-16 ring, 17-20 pinky. With the webcam they are your own joints.
# Without it (mouse mode, or a hand out of view) these stock poses stand in: a right hand seen
# from behind, in palm lengths from the middle knuckle, fingers pointing up the screen.
_PALM = [(0.0, 1.0), (-0.32, 0.72), None, None, None, (-0.27, 0.03), None, None, None, (0.0, 0.0),
         None, None, None, (0.24, 0.05), None, None, None, (0.44, 0.18), None, None, None]
_DIGITS = {
    "OPEN":  [(-0.58, 0.42), (-0.78, 0.15), (-0.92, -0.10),   (-0.36, -0.42), (-0.41, -0.70), (-0.45, -0.93),
              (0.0, -0.50), (0.0, -0.82), (0.0, -1.08),       (0.30, -0.40), (0.34, -0.70), (0.37, -0.94),
              (0.56, -0.16), (0.63, -0.40), (0.69, -0.60)],
    "PINCH": [(-0.56, 0.42), (-0.64, 0.05), (-0.57, -0.29),   (-0.36, -0.40), (-0.52, -0.55), (-0.58, -0.35),
              (0.0, -0.46), (0.0, -0.74), (0.0, -0.95),       (0.29, -0.36), (0.32, -0.62), (0.34, -0.82),
              (0.54, -0.14), (0.60, -0.34), (0.64, -0.50)],
    "FIST":  [(-0.52, 0.42), (-0.50, 0.12), (-0.30, -0.02),   (-0.30, -0.28), (-0.30, -0.05), (-0.26, 0.12),
              (0.0, -0.32), (0.0, -0.06), (0.0, 0.14),        (0.26, -0.26), (0.25, -0.02), (0.22, 0.16),
              (0.46, -0.08), (0.44, 0.12), (0.40, 0.26)],
}
POSES = {}
for _name, _digits in _DIGITS.items():
    _rest = iter(_digits)
    POSES[_name] = [q if q is not None else next(_rest) for q in _PALM]
FINGERS = {"pinky": (17, 18, 19, 20), "ring": (13, 14, 15, 16), "middle": (9, 10, 11, 12),
           "index": (5, 6, 7, 8), "thumb": (1, 2, 3, 4)}
# finger thickness in palm lengths: radius at the knuckle and at the tip
GIRTH = {"pinky": (0.085, 0.066), "ring": (0.098, 0.075), "middle": (0.105, 0.08),
         "index": (0.10, 0.078), "thumb": (0.15, 0.10)}


def capsule(surf, colour, a, b, ra, rb):
    """A rounded, tapering limb segment from a to b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy) or 1
    nx, ny = -dy / d, dx / d
    pygame.draw.polygon(surf, colour, [(a[0] + nx * ra, a[1] + ny * ra), (b[0] + nx * rb, b[1] + ny * rb),
                                       (b[0] - nx * rb, b[1] - ny * rb), (a[0] - nx * ra, a[1] - ny * ra)])
    pygame.draw.circle(surf, colour, a, ra)
    pygame.draw.circle(surf, colour, b, rb)


def make_glow(radius, colour):
    """Radial glow sprite, used with additive blending (black = invisible)."""
    s = pygame.Surface((radius * 2, radius * 2))
    s.fill((0, 0, 0))
    for r in range(radius, 0, -2):
        k = (1 - r / radius) ** 2
        pygame.draw.circle(s, [int(c * k) for c in colour], (radius, radius), r)
    return s


# =====================================================================
class Specimen:
    def __init__(self, species, x):
        self.species = species
        self.info = SPECIES[species]
        self.x = x
        self.y = seabed_at(x) - 4
        self.state = "idle"          # idle / held / falling / damaged / gone
        self.vy = 0.0
        self.t = 0.0
        self.phase = random.uniform(0, 6.28)
        self.glow = make_glow(46, self.info["glow"])

    @property
    def sturdy(self):
        return self.info["sturdy"]

    def grab_point(self):
        return self.x, self.y - 36

    def draw(self, surf, cx, cy, tick):
        x, y = self.x - cx, self.y - cy
        if x < -80 or x > W + 80 or y < -100 or y > H + 100:
            return
        col = self.info["colour"]
        if self.state == "damaged":
            k = min(1, self.t / 2.5)
            for i in range(8):
                a = i * 6.28 / 8
                px, py = x + math.cos(a) * k * 45, y - 30 + math.sin(a) * k * 30 + k * 30
                c = [int(v * (1 - k) + 40 * k) for v in col]
                pygame.draw.ellipse(surf, c, (px - 4, py - 3, 8, 6))
            return
        sway = 0 if self.state == "held" else math.sin(tick * 0.03 + self.phase) * 3
        if self.species == "glass anemone":
            pygame.draw.line(surf, (190, 150, 210), (x, y), (x + sway, y - 28), 3)
            pygame.draw.ellipse(surf, col, (x + sway - 13, y - 46, 26, 20))
            for i in range(-2, 3):
                pygame.draw.line(surf, (225, 180, 240), (x + sway + i * 4, y - 44),
                                 (x + sway + i * 7 + math.sin(tick * 0.05 + i) * 3, y - 58), 1)
        elif self.species == "lantern polyp":
            pygame.draw.line(surf, (160, 120, 80), (x, y), (x + sway, y - 24), 3)
            pygame.draw.ellipse(surf, col, (x + sway - 11, y - 50, 22, 28))
            pygame.draw.ellipse(surf, (255, 240, 190), (x + sway - 5, y - 42, 10, 12))
        elif self.species == "ribbon kelp sprout":
            for i, off in enumerate((-8, 0, 8)):
                pts = [(x + off * 0.3 + math.sin(tick * 0.04 + j * 0.8 + i) * j * 1.5 + (off * j / 6),
                        y - j * 9) for j in range(7)]
                pygame.draw.lines(surf, col, False, pts, 3)
        else:  # velvet urchin
            for i in range(16):
                a = i * 6.28 / 16 + tick * 0.003
                pygame.draw.line(surf, (200, 150, 220), (x, y - 22),
                                 (x + math.cos(a) * 22, y - 22 + math.sin(a) * 22), 2)
            pygame.draw.circle(surf, col, (int(x), int(y - 22)), 13)

    def draw_glow(self, surf, cx, cy, tick):
        if self.state in ("damaged", "gone"):
            return
        x, y = self.x - cx, self.y - 36 - cy
        if -60 < x < W + 60 and -60 < y < H + 60:
            g = self.glow.copy()
            g.set_alpha(int(170 + 85 * math.sin(tick * 0.06 + self.phase)))
            surf.blit(g, (x - 46, y - 46), special_flags=pygame.BLEND_RGB_ADD)


class Fish:
    def __init__(self):
        self.x = random.uniform(0, WORLD_W)
        self.y = random.uniform(200, config.SEABED_Y - 90)
        a = random.uniform(0, 6.28)
        self.vx, self.vy = math.cos(a) * 0.8, math.sin(a) * 0.4
        self.len = random.uniform(18, 36)
        frames = fish_frames(self.len, random.choice(FISH_KINDS))
        self.frames = {1: frames, -1: [pygame.transform.flip(f, True, False) for f in frames]}
        self.beat = random.uniform(0, 6.28)
        self.fleeing = False

    def update(self, lights, gx, gy):
        self.fleeing = False
        self.vx += random.uniform(-0.06, 0.06)
        self.vy += random.uniform(-0.04, 0.04)
        d = math.hypot(self.x - gx, self.y - gy)
        if lights and d < 360:
            self.vx += (self.x - gx) / max(d, 1) * 0.4
            self.vy += (self.y - gy) / max(d, 1) * 0.4
            self.fleeing = True
        sp = math.hypot(self.vx, self.vy)
        lim = 4.5 if self.fleeing else max(1.1, sp * 0.97)
        if sp > lim:
            self.vx, self.vy = self.vx / sp * lim, self.vy / sp * lim
        self.x = (self.x + self.vx) % WORLD_W
        self.y += self.vy
        if self.y < 150: self.vy += 0.1
        if self.y > config.SEABED_Y - 70: self.vy -= 0.1

    def draw(self, surf, cx, cy):
        x, y = self.x - cx, self.y - cy
        if not (-50 < x < W + 50 and -50 < y < H + 50):
            return
        # the tail beats faster the faster it swims
        self.beat += 0.12 + math.hypot(self.vx, self.vy) * 0.12
        face = 1 if self.vx >= 0 else -1
        img = self.frames[face][(0, 1, 2, 1)[int(self.beat) % 4]]
        pitch = max(-35, min(35, math.degrees(math.atan2(self.vy, abs(self.vx) + 0.01))))
        img = pygame.transform.rotozoom(img, -pitch * face, 1)
        surf.blit(img, (x - img.get_width() / 2, y - img.get_height() / 2))


class Mote:
    def __init__(self, x, y, silt):
        self.x, self.y, self.silt = x, y, silt
        self.life = 255
        if silt:
            self.vx, self.vy, self.r = random.uniform(-1.3, 1.3), random.uniform(-2.3, -0.4), random.uniform(1.5, 3.5)
        else:
            self.vx, self.vy, self.r = 0, random.uniform(0.15, 0.5), random.uniform(0.8, 1.6)

    def update(self, tick):
        if self.silt:
            self.x += self.vx
            self.y = min(self.y + self.vy, seabed_at(self.x))
            self.vx *= 0.97
            self.vy = (self.vy + 0.025) * 0.98
            self.life -= 1.1
        else:
            self.y += self.vy
            self.x += math.sin((tick + self.y) * 0.01) * 0.2
            if self.y > H:
                self.y, self.x = -5, random.uniform(0, W)


# =====================================================================
class Game:
    def __init__(self):
        pygame.init()
        flags = pygame.FULLSCREEN if config.FULLSCREEN else 0
        self.screen = pygame.display.set_mode((W, H), flags)
        pygame.display.set_caption("ROV-6 · ORCA console" + (" · experimenter's view" if config.MODE == "vr" else ""))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("menlo,monaco,consolas,couriernew", 15)
        self.font_small = pygame.font.SysFont("menlo,monaco,consolas,couriernew", 12)
        self.font_big = pygame.font.SysFont("menlo,monaco,consolas,couriernew", 28, bold=True)
        self.tick = 0

        # helpers
        self.orca = Orca()
        self.tracker = Tracker()
        self.vr = None
        if config.MODE == "vr":
            # the phone is the headset: head, hand and (usually) voice come from it, not the webcam
            from vr.session import VRSession
            self.vr = VRSession(self)
            self.voice = self.vr.voice or Voice(self.orca)
        else:
            self.tracker.start()
            self.voice = Voice(self.orca)
        self.voice.start()

        # control state
        self.lights = False
        self.fine = False
        self.narrow = False
        self.mouse_mode = False
        self.show_preview = True
        self.show_help = False
        self.help_until = 0
        self.pov = config.START_POV       # "third" = outside view, "first" = you are the robot

        # view
        self.camx = WORLD_W / 2 - W / 2
        self.camy = config.SEABED_Y - H + 170
        self.pan_speed = 0.0
        self.gaze_screen = (W / 2, H / 2)

        # gripper (screen coords)
        self.hx, self.hy = W / 2, H / 2
        self.prev_target = None
        self.grip_at = None               # VR: where the gripper is in the world
        self.gesture = "OPEN"
        self.held = None

        # outside view: the ROV body hovers above the gripper
        self.rovx, self.rovy = W / 2, H / 2 - 170
        self.rov_tilt, self.rov_face = 0.0, 1
        # first person: left (-1) and right (+1) hand. skeletons = your real joints from the webcam.
        # Per hand: k = how far it has taken over the gripper, w = stock-pose mix when there are no
        # real joints, pts = the 21 joints as drawn, off = where thumb and index meet, size = pixels
        # per palm length, free = where it is when it isn't the one holding the gripper
        self.active_hand = 1
        self.skeletons = {}
        self.hands = {side: dict(k=float(side == 1), w={"OPEN": 1.0, "PINCH": 0.0, "FIST": 0.0},
                                 pts=[(x * side, y) for x, y in POSES["OPEN"]], off=(-0.68 * side, -0.5),
                                 size=100.0, free=(W / 2 + side * 330, H - 190))
                      for side in (-1, 1)}

        # world
        self.specimens = []
        self.spawn_specimens()
        self.fish = [Fish() for _ in range(22)]
        self.snow = [Mote(random.uniform(0, W), random.uniform(0, H), False) for _ in range(170)]
        self.silt = []
        self.kelp = []
        for i in range(70):
            layer = random.choice((0, 0, 1, 1, 2))
            self.kelp.append(dict(x=random.uniform(0, WORLD_W), h=random.uniform(250, 560),
                                  ph=random.uniform(0, 6.28), layer=layer))
        self.kelp.sort(key=lambda k: k["layer"])
        self.markers = []
        self.notes = []
        self.stored = self.damaged = self.slipped = 0
        self.disturbance = 0.0
        self.fleeing = 0

        # gaze dwell
        self.dwell_target = None
        self.dwell_time = 0.0
        self.gazed = None
        self.spoken_dwell = {}

        # transitions for proactive comments
        self.was_reach = self.was_bay = self.was_scared = self.was_collision = False
        self.was_calibrated = False
        self.face_lost_since = None
        self.hand_lost_since = None
        self.all_done_at = None
        self.last_gaze_log = 0

        # on-screen messages
        self.flash_text, self.flash_col, self.flash_at = "", CYAN, -1e9

        # pre-rendered surfaces
        self.scene = Scene()
        self.dark_on = pygame.Surface((W, H), pygame.SRCALPHA)
        self.dark_on.fill((0, 12, 26, 55))
        self.dark_off = pygame.Surface((W, H), pygame.SRCALPHA)
        self.dark_off.fill((0, 12, 26, 150))
        self.beam = pygame.Surface((W, H))
        self.hotspot = make_glow(230, (70, 66, 45))
        self.headlamp = make_glow(400, (46, 43, 30))      # first person: the light comes from you
        self.lamp_glow = make_glow(34, (120, 110, 70))
        self.rov_label = self.font_small.render("ROV-6", True, (70, 55, 15))
        self.vignette = self.make_vignette()

        self.start_time = self.task_start = time.time()
        self.greeted = self.vr is not None        # in VR the calibration does the talking

    def make_vignette(self):
        """Narrow field of view: a soft window that follows your gaze."""
        import numpy as np
        vw, vh = W * 2, H * 2
        s = pygame.Surface((vw, vh), pygame.SRCALPHA)
        s.fill((0, 0, 0, 255))
        yy, xx = np.mgrid[0:vh, 0:vw]
        d = np.sqrt(((xx - vw / 2) / (W * 0.24)) ** 2 + ((yy - vh / 2) / (H * 0.33)) ** 2)
        alpha = np.clip((d - 0.82) / 0.32, 0, 1) * 255
        pa = pygame.surfarray.pixels_alpha(s)
        pa[:, :] = alpha.T.astype("uint8")
        del pa
        return s

    def spawn_specimens(self):
        names = ["glass anemone", "lantern polyp", "ribbon kelp sprout", "glass anemone",
                 "velvet urchin", "lantern polyp", "ribbon kelp sprout", "velvet urchin"]
        random.shuffle(names)
        xs = [250 + i * (WORLD_W - 500) / (len(names) - 1) + random.uniform(-80, 80) for i in range(len(names))]
        self.specimens += [Specimen(n, x) for n, x in zip(names, xs)]

    # -------------------------------------------------------------- flash
    def flash(self, text, col=CYAN):
        self.flash_text, self.flash_col, self.flash_at = text, col, time.time()

    # -------------------------------------------------------------- commands
    def run_command(self, cmd, source):
        logger.log(source, "command", cmd)
        if cmd == "lights_on": self.lights = True; self.flash("LIGHTS ON", (255, 230, 150))
        elif cmd == "lights_off": self.lights = False; self.flash("LIGHTS OFF", (150, 180, 200))
        elif cmd == "fine":
            self.fine = True
            scale = config.MOVEMENT_SCALE_PRECISION if self.vr else config.FINE_SCALE
            self.flash(f"FINE CONTROL  x{scale:.2f}", (255, 220, 120))
        elif cmd == "normal": self.fine = False; self.flash("NORMAL CONTROL", GREY)
        elif cmd == "narrow": self.narrow = True; self.flash("NARROW VIEW", GREY)
        elif cmd == "wide": self.narrow = False; self.flash("WIDE VIEW", GREY)
        elif cmd == "pov_first": self.pov = "first"; self.flash("FIRST PERSON", CYAN)
        elif cmd == "pov_third": self.pov = "third"; self.flash("OUTSIDE VIEW", CYAN)
        elif cmd == "centre" and self.vr:
            self.vr.recenter()                # wherever you are facing becomes straight ahead
            self.flash("VIEW CENTRED", CYAN)
        elif cmd == "centre":
            self.camx = WORLD_W / 2 - W / 2
            self.camy = config.SEABED_Y - H + 170
        elif cmd == "mark":
            self.markers.append((self.camx + self.hx, self.camy + self.hy + 34))
            self.flash(f"MARKER M{len(self.markers)}", (255, 220, 0))
            logger.log(source, "marker", f"M{len(self.markers)} at {int(self.camx + self.hx)},{int(self.camy + self.hy)}")
        elif cmd == "calibrate":
            if self.vr:
                self.vr.recenter()
                self.flash("VIEW CENTRED", CYAN)
            elif self.tracker.available and not self.mouse_mode:
                self.tracker.calibrate()
                self.was_calibrated = False       # announce when done
                self.flash("LOOK AT THE CENTRE", CYAN)
            else:
                self.orca.say("Nothing to calibrate, scientist. We're in mouse mode.")
        elif cmd == "help":
            self.show_help, self.help_until = True, time.time() + 14

        if source == "key" and cmd in ("lights_on", "lights_off", "fine", "normal", "narrow", "wide", "centre",
                                       "pov_first", "pov_third"):
            self.orca.say(self.orca.line(cmd))
        if source == "key" and cmd == "mark":
            self.orca.say(self.orca.line("mark", n=len(self.markers)))

    def handle_orca_queue(self):
        while not self.orca.commands.empty():
            kind, value = self.orca.commands.get()
            if kind == "cmd":
                self.run_command(value, "voice")
            elif kind == "note":
                self.notes.append(time.strftime("%H:%M ") + value)
                self.flash("FIELD NOTE LOGGED", CYAN)

    # -------------------------------------------------------------- input
    def read_inputs(self):
        tr = self.tracker
        use_mouse = self.mouse_mode or not tr.available
        if use_mouse:
            mx, my = pygame.mouse.get_pos()
            target = (mx, my)
            b = pygame.mouse.get_pressed()
            gesture = "FIST" if b[2] else "PINCH" if b[0] else "OPEN"
            keys = pygame.key.get_pressed()
            kx = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (keys[pygame.K_LEFT] or keys[pygame.K_a])
            ky = (keys[pygame.K_DOWN] or keys[pygame.K_s]) - (keys[pygame.K_UP] or keys[pygame.K_w])
            gaze = (kx, ky)
            self.gaze_screen = (mx, my)
            face_ok = hand_ok = True
            self.skeletons = {}
        else:
            with tr.lock:
                gaze, face_ok = tr.gaze, tr.face_ok
                hand, hand_ok, gesture = tr.hand, tr.hand_ok, tr.gesture
                self.skeletons = tr.skeletons
                if hand_ok:
                    self.active_hand = tr.hand_side       # your real left or right hand
            target = (hand[0] * W, hand[1] * H) if hand_ok else None
            if not hand_ok:
                gesture = self.gesture          # keep last gesture while hand is lost
            self.gaze_screen = (W / 2 + gaze[0] * W * 0.45, H / 2 + gaze[1] * H * 0.45)
            if not face_ok:
                gaze = (0, 0)
        return target, gesture, gaze, face_ok, hand_ok, use_mouse

    # -------------------------------------------------------------- update
    def update(self, dt):
        self.tick += 1
        self.handle_orca_queue()
        # greet once we know whether the camera works
        if not self.greeted and (self.tracker.available or self.tracker.error
                                 or time.time() - self.start_time > 4):
            self.greeted = True
            mouse = self.mouse_mode or not self.tracker.available
            self.orca.say(self.orca.line("greeting_mouse" if mouse else "greeting"), "greeting")
        if self.vr:
            gesture, hand_ok, speed = self.steer_vr(dt)
            gaze, face_ok, use_mouse = (0.0, 0.0), True, False
        else:
            target, gesture, gaze, face_ok, hand_ok, use_mouse = self.read_inputs()

            # ---- look around: gaze near the edge pans the view ----
            dz = config.GAZE_DEAD_ZONE
            vx = vy = 0.0
            for g, axis in ((gaze[0], 0), (gaze[1], 1)):
                if abs(g) > dz:
                    v = math.copysign(((abs(g) - dz) / (1 - dz)) ** 1.5, g) * config.PAN_SPEED
                    if axis == 0: vx = v
                    else: vy = v * 0.6
            if use_mouse:
                vx, vy = gaze[0] * config.PAN_SPEED * 0.7, gaze[1] * config.PAN_SPEED * 0.4
            self.camx = max(0, min(WORLD_W - W, self.camx + vx))
            self.camy = max(0, min(WORLD_H - H, self.camy + vy))
            self.pan_speed = math.hypot(vx, vy)

            # ---- hand -> gripper (with motion scaling in fine mode) ----
            old = (self.hx, self.hy)
            if target is not None:
                if self.fine and self.prev_target is not None:
                    self.hx += (target[0] - self.prev_target[0]) * config.FINE_SCALE
                    self.hy += (target[1] - self.prev_target[1]) * config.FINE_SCALE
                elif not self.fine:
                    # the tracker already steadies the hand, so follow it closely; the mouse stays soft
                    follow = 0.25 if use_mouse else 0.5
                    self.hx += (target[0] - self.hx) * follow
                    self.hy += (target[1] - self.hy) * follow
                self.prev_target = target
            else:
                self.prev_target = None
            self.keep_in_reach()
            speed = math.hypot(self.hx - old[0], self.hy - old[1])
        gwx, gwy = self.camx + self.hx, self.camy + self.hy      # gripper in the world
        claw = (gwx, gwy + 32)
        self.animate_pov()

        # ---- gestures: grip / release ----
        if gesture != self.gesture:
            self.on_gesture(self.gesture, gesture, claw)
            self.gesture = gesture

        # ---- specimens ----
        for s in self.specimens:
            if s.state == "held":
                s.x, s.y = gwx, gwy + 72
            elif s.state == "falling":
                s.vy += 0.12
                s.y += s.vy
                if s.y >= seabed_at(s.x) - 4:
                    s.y, s.vy, s.state = seabed_at(s.x) - 4, 0, "idle"
                    self.silt += [Mote(s.x + random.uniform(-15, 15), s.y, True) for _ in range(18)]
            elif s.state == "damaged":
                s.t += dt
                if s.t > 2.5:
                    s.state = "gone"
        self.specimens = [s for s in self.specimens if s.state != "gone"]
        active = [s for s in self.specimens if s.state in ("idle", "held", "falling")]
        if not active:
            if self.all_done_at is None:
                self.all_done_at = time.time()
                logger.log("world", "task_complete", f"{self.all_done_at - self.task_start:.1f}s,stored={self.stored},"
                                                     f"damaged={self.damaged},slipped={self.slipped}")
                self.orca.event("all_done")
            elif time.time() - self.all_done_at > 4:
                self.spawn_specimens()
                self.all_done_at = None
                self.task_start = time.time()

        # ---- sediment: fast moves near the seabed ----
        if speed > 6 and gwy > seabed_at(gwx) - 110 and len(self.silt) < 700:
            self.silt += [Mote(gwx + random.uniform(-25, 25), seabed_at(gwx) - random.uniform(0, 5), True)
                          for _ in range(int(speed / 3))]
        for m in self.silt:
            m.update(self.tick)
        self.silt = [m for m in self.silt if m.life > 0]
        for m in self.snow:
            m.update(self.tick)

        # ---- fish ----
        self.fleeing = 0
        for f in self.fish:
            f.update(self.lights, gwx, gwy)
            self.fleeing += f.fleeing

        # ---- disturbance (lights + sediment + scared fish + thrusters) ----
        target_d = ((0.22 if self.lights else 0) + min(len(self.silt) / 450, 0.4)
                    + self.fleeing / len(self.fish) * 0.4 + min(self.pan_speed / 60, 0.15))
        self.disturbance += (min(1, target_d) - self.disturbance) * 0.03

        # ---- gaze dwell: look at a specimen to inspect it ----
        gx, gy = self.gaze_screen
        look = None
        for s in self.specimens:
            if s.state in ("idle", "falling"):
                sx, sy = s.x - self.camx, s.y - 36 - self.camy
                if math.hypot(sx - gx, sy - gy) < 75:
                    look = s
                    break
        if look is self.dwell_target and look is not None:
            self.dwell_time += dt
        else:
            self.dwell_target, self.dwell_time = look, 0.0
        self.gazed = look.species if look and self.dwell_time >= config.DWELL_SECONDS else None
        if self.gazed and time.time() - self.spoken_dwell.get(id(look), -1e9) > 40:
            self.spoken_dwell[id(look)] = time.time()
            logger.log("gaze", "inspect", look.species)
            self.orca.event("dwell", species=look.species)

        # ---- calibration announcement ----
        if self.tracker.is_calibrated() and not self.was_calibrated and not use_mouse:
            self.was_calibrated = True
            self.flash("EYES CALIBRATED", CYAN)
            self.orca.event("calibrated")
            logger.log("gaze", "calibrated")

        # ---- proactive comments ----
        self.proactive(claw, face_ok, hand_ok, use_mouse)

        # ---- log gaze twice a second (experiment data) ----
        if time.time() - self.last_gaze_log > 0.5 and not use_mouse and not self.vr:   # VR logs head and hand itself
            self.last_gaze_log = time.time()
            logger.log("gaze", "sample", f"{gaze[0]:.2f},{gaze[1]:.2f},cam={int(self.camx)},{int(self.camy)},"
                                          f"face={int(face_ok)},hand={int(hand_ok)},gesture={self.gesture}")

        # ---- tell ORCA what's going on ----
        if self.tick % 10 == 0:
            self.orca.world = self.snapshot(claw, face_ok, hand_ok)

    def keep_in_reach(self):
        """The gripper can't leave the view or go through the seabed. Pushing it into the seabed is a collision."""
        self.hx = max(30, min(W - 30, self.hx))
        floor = seabed_at(self.camx + self.hx) - self.camy - 40
        if self.hy > floor + 1.5 and not self.was_collision:
            self.was_collision = True
            logger.log("world", "collision", f"seabed at {int(self.camx + self.hx)}")
            if self.vr:
                self.flash("COLLISION", (255, 80, 60))
        elif self.hy < floor - 6:
            self.was_collision = False
        self.hy = max(70, min(min(H - 40, floor), self.hy))

    def steer_vr(self, dt):
        """Phone VR: your head turns the view and your hand moves the gripper, and neither moves the other."""
        c = self.vr.control(dt)
        # ---- head -> view ----
        camx, camy = self.vr.camera(c.yaw, c.pitch)       # if the phone goes quiet, the view holds still
        self.pan_speed = math.hypot(camx - self.camx, camy - self.camy)
        self.camx, self.camy = camx, camy
        self.gaze_screen = (W / 2, H / 2)         # you inspect whatever is in the middle of your view

        # ---- hand -> gripper. It has a place in the world, so turning your head leaves it there;
        # the arm is only dragged along once it would leave the view ----
        if self.grip_at is None:
            self.grip_at = (self.camx + self.hx, self.camy + self.hy)
        old = self.grip_at
        self.hx = old[0] + c.move[0] - self.camx
        self.hy = old[1] + c.move[1] - self.camy
        self.keep_in_reach()
        self.grip_at = (self.camx + self.hx, self.camy + self.hy)
        self.skeletons = c.skeletons
        if c.hand != "lost":
            self.active_hand = c.side
        hand_ok = c.hand == "tracking" or self.vr.phase != "dive"     # nothing to complain about before the dive
        return c.gesture, hand_ok, math.hypot(self.grip_at[0] - old[0], self.grip_at[1] - old[1])

    def animate_pov(self):
        """Move the ROV body (outside view) and the two hands (first person) after the gripper."""
        # the ROV hovers above the gripper, trails behind it and leans into the turn
        vx = (self.hx - self.rovx) * 0.07
        self.rovx += vx
        self.rovy += (max(24, self.hy - 170) - self.rovy) * 0.07     # tucks up out of the way at the drawer
        self.rov_tilt += (max(-16, min(16, vx * 2.2)) - self.rov_tilt) * 0.15
        if abs(vx) > 0.6:
            self.rov_face = 1 if vx > 0 else -1

        # with the webcam each robot hand copies your own; without it, the hand on the gripper's
        # side of the screen does the work and the other one rests
        if not self.skeletons and self.held is None:
            if self.hx < W / 2 - 70: self.active_hand = -1
            elif self.hx > W / 2 + 70: self.active_hand = 1
        mix = lambda a, b, k: (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
        for side, h in self.hands.items():
            active = side == self.active_hand
            h["k"] += (active - h["k"]) * 0.18
            real = self.skeletons.get(side)
            if real:
                goal, speed = real["pts"], 0.7
                size = max(80, min(130, real["palm"] * H * 0.6))
                free = (real["pos"][0] * W, real["pos"][1] * H)
            else:
                pose = self.gesture if active else "OPEN"
                for name in h["w"]:
                    h["w"][name] += ((name == pose) - h["w"][name]) * 0.3
                goal = [(side * sum(POSES[g][i][0] * k for g, k in h["w"].items()),
                         sum(POSES[g][i][1] * k for g, k in h["w"].items())) for i in range(21)]
                size, speed = 100, 1
                free = (W / 2 + side * 330, H - 190 + 8 * math.sin(self.tick * 0.03 + side))
            h["pts"] = [mix(a, b, speed) for a, b in zip(h["pts"], goal)]
            h["size"] += (size - h["size"]) * 0.2
            h["free"] = mix(h["free"], free, 0.25)
            # the gripper's grab point sits between thumb tip and index tip; follow it slowly so the
            # hand doesn't jump every time a finger moves
            h["off"] = mix(h["off"], mix(h["pts"][4], h["pts"][8], 0.5), 0.15)

    def nearest(self, claw):
        best = None
        for s in self.specimens:
            if s.state in ("idle", "falling"):
                gx, gy = s.grab_point()
                d = math.hypot(gx - claw[0], gy - claw[1])
                if best is None or d < best[0]:
                    best = (d, s)
        return best

    def on_gesture(self, old, new, claw):
        logger.log("gesture", "gesture", f"{old}->{new}")
        if new in ("PINCH", "FIST") and self.held is None:
            near = self.nearest(claw)
            if not near or near[0] > 48:
                return
            s = near[1]
            if s.sturdy and new == "PINCH":
                self.slipped += 1
                self.flash("IT SLIPPED - TOO SPINY FOR A PINCH", ORANGE)
                logger.log("gesture", "slipped", s.species)
                self.orca.event("slipped", species=s.species)
            elif not s.sturdy and new == "FIST":
                self.crush(s)
            else:
                s.state, self.held = "held", s
                self.flash("SPECIMEN SECURED", (0, 255, 150))
                logger.log("gesture", "secured", s.species)
                self.orca.event("secured", species=s.species)
        elif self.held is not None:
            s = self.held
            if new == "FIST" and not s.sturdy:          # squeezed it
                self.held = None
                self.crush(s)
            elif new == "PINCH" and s.sturdy:            # loosened on an urchin
                self.held = None
                s.state, s.vy = "falling", 0
                self.slipped += 1
                logger.log("gesture", "slipped", s.species)
                self.orca.event("slipped", species=s.species)
            elif new == "OPEN":
                self.held = None
                if DRAWER.inflate(30, 40).collidepoint(claw[0] - self.camx, claw[1] - self.camy):
                    s.state = "gone"
                    self.stored += 1
                    self.flash(f"SAMPLE STORED ({self.stored})", (0, 255, 150))
                    logger.log("gesture", "stored", s.species)
                    self.orca.event("stored", stored=self.stored)
                else:
                    s.state, s.vy = "falling", 0
                    logger.log("gesture", "dropped", s.species)
                    self.orca.event("dropped")

    def crush(self, s):
        s.state, s.t = "damaged", 0
        self.damaged += 1
        self.flash("TOO MUCH FORCE - SPECIMEN DAMAGED", (255, 80, 60))
        logger.log("gesture", "damaged", s.species)
        self.orca.event("damaged", species=s.species)
        self.silt += [Mote(s.x + random.uniform(-20, 20), s.y - random.uniform(0, 40), True) for _ in range(30)]

    def proactive(self, claw, face_ok, hand_ok, use_mouse):
        near = self.nearest(claw)
        reach = self.held is None and self.gesture == "OPEN" and near is not None and near[0] < 48
        if reach and not self.was_reach:
            s = near[1]
            logger.log("world", "contact", s.species)
            self.orca.event("in_reach_sturdy" if s.sturdy else "in_reach", species=s.species)
        self.was_reach = reach

        bay = self.held is not None and DRAWER.inflate(30, 40).collidepoint(claw[0] - self.camx, claw[1] - self.camy)
        if bay and not self.was_bay:
            self.orca.event("near_bay")
        self.was_bay = bay

        scared = self.lights and self.fleeing >= 4
        if scared and not self.was_scared:
            self.orca.event("fish_fleeing")
        self.was_scared = scared

        if self.disturbance > 0.6:
            self.orca.event("high_disturbance")

        if self.vr or (not use_mouse and self.tracker.available):
            now = time.time()
            if not face_ok:
                self.face_lost_since = self.face_lost_since or now
                if now - self.face_lost_since > 3:
                    self.orca.event("face_lost")
            else:
                self.face_lost_since = None
            if not hand_ok:
                self.hand_lost_since = self.hand_lost_since or now
                if now - self.hand_lost_since > 5:
                    self.orca.event("hand_lost")
            else:
                self.hand_lost_since = None

    def snapshot(self, claw, face_ok, hand_ok):
        n = self.nearest(claw)
        nearest = None
        if n:
            s = n[1]
            gx, gy = s.grab_point()
            onscreen = self.camx < s.x < self.camx + W and self.camy < s.y < self.camy + H
            nearest = dict(species=s.species, dx=gx - claw[0], dy=gy - claw[1], onscreen=onscreen)
        return dict(lights=self.lights, fine=self.fine, narrow=self.narrow,
                    stored=self.stored, damaged=self.damaged, slipped=self.slipped,
                    disturbance=round(self.disturbance, 2), fleeing=self.fleeing,
                    markers=len(self.markers), pov=self.pov,
                    remaining=sum(s.state in ("idle", "falling") for s in self.specimens),
                    held=self.held.species if self.held else None, gazed=self.gazed,
                    nearest=nearest, face_ok=face_ok, hand_ok=hand_ok, gesture=self.gesture)

    # -------------------------------------------------------------- draw
    def draw(self):
        self.draw_world()
        self.draw_gaze()
        self.draw_hud()

    def draw_eye(self, target, dx):
        """VR: the world as one eye sees it, from dx pixels to the side. Near things shift more than
        far ones between the two eyes, and that difference is the depth you see. The robot's hands
        and the drawer shift with the seabed, because that is where they work."""
        keep = self.screen, self.camx, self.hx, self.rovx, self.gaze_screen
        self.screen, self.camx, self.hx, self.rovx = target, self.camx + dx, self.hx - dx, self.rovx - dx
        self.gaze_screen = (self.gaze_screen[0] - dx, self.gaze_screen[1])
        DRAWER.x -= dx
        for h in self.hands.values():
            h["free"] = (h["free"][0] - dx, h["free"][1])
        try:
            self.draw_world()
            self.draw_gaze()
        finally:
            self.screen, self.camx, self.hx, self.rovx, self.gaze_screen = keep
            DRAWER.x += dx
            for h in self.hands.values():
                h["free"] = (h["free"][0] + dx, h["free"][1])

    def draw_world(self):
        scr, cx, cy, t = self.screen, self.camx, self.camy, self.tick
        self.scene.draw_backdrop(scr, cx, cy, t)
        for k in self.kelp:
            if k["layer"] < 2:
                self.scene.draw_kelp(scr, k, cx, cy, t)
        self.scene.draw_seabed(scr, cx, cy, t)

        for s in self.specimens:
            if s.state != "held":
                s.draw(scr, cx, cy, t)
        for f in self.fish:
            f.draw(scr, cx, cy)
        for k in self.kelp:
            if k["layer"] == 2:
                self.scene.draw_kelp(scr, k, cx, cy, t)
        for m in self.snow:
            pygame.draw.circle(scr, (120, 160, 170), (int(m.x), int(m.y)), m.r)
        for m in self.silt:
            x, y = m.x - cx, m.y - cy
            if 0 <= x < W and 0 <= y < H:
                c = int(m.life / 255 * 120)
                pygame.draw.circle(scr, (40 + c // 2, 38 + c // 3, 30 + c // 5), (int(x), int(y)), m.r)
        for i, (mx, my) in enumerate(self.markers):
            x, y = mx - cx, my - cy
            pygame.draw.line(scr, (255, 220, 0), (x, y), (x, y - 26), 2)
            pygame.draw.polygon(scr, (255, 220, 0), [(x, y - 26), (x + 14, y - 21), (x, y - 16)])
            scr.blit(self.font_small.render(f"M{i + 1}", True, (255, 220, 0)), (x + 4, y - 42))

        self.draw_drawer()

        # lighting
        scr.blit(self.dark_on if self.lights else self.dark_off, (0, 0))
        if self.lights and self.pov == "first":
            # you are the robot, so the light spreads out from where you are looking
            scr.blit(self.headlamp, (self.hx - 400, self.hy + 40 - 400), special_flags=pygame.BLEND_RGB_ADD)
            scr.blit(self.hotspot, (self.hx - 230, self.hy + 40 - 230), special_flags=pygame.BLEND_RGB_ADD)
        elif self.lights:
            self.beam.fill((0, 0, 0))
            # soft beam from the ROV lamp, through the gripper, to the bottom of the screen
            ox, oy = self.rov_point(30, 22)
            k = min(2.2, (H + 40 - oy) / max(self.hy + 40 - oy, 1))
            bx = ox + (self.hx - ox) * k
            for i in range(8):
                top, spread = 3 + i * 3, (50 + i * 40) * k * 0.6
                pygame.draw.polygon(self.beam, (8, 8, 6),
                                    [(ox - top, oy), (ox + top, oy),
                                     (bx + spread, H + 40), (bx - spread, H + 40)])
            scr.blit(self.beam, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
            scr.blit(self.hotspot, (self.hx - 230, self.hy + 40 - 230), special_flags=pygame.BLEND_RGB_ADD)
        for s in self.specimens:
            s.draw_glow(scr, cx, cy, t)
        if self.pov == "first":
            self.draw_hands()
        if self.held:
            self.held.draw(scr, cx, cy, t)
        if self.pov != "first":
            self.draw_rov()
        if self.fine:
            x, y = self.hx, self.hy
            pygame.draw.circle(scr, (255, 220, 120), (int(x), int(y + 34)), 11, 1)
            pygame.draw.line(scr, (255, 220, 120), (x - 17, y + 34), (x + 17, y + 34), 1)
            pygame.draw.line(scr, (255, 220, 120), (x, y + 17), (x, y + 51), 1)

        if self.narrow:
            gx, gy = self.gaze_screen
            scr.blit(self.vignette, (gx - W, gy - H))

    def draw_drawer(self):
        scr = self.screen
        r = DRAWER
        pygame.draw.rect(scr, (40, 48, 52), r, border_radius=6)
        pygame.draw.rect(scr, (150, 160, 165), r, 2, border_radius=6)
        for i in range(1, 5):
            x = r.x + i * r.w / 5
            pygame.draw.line(scr, (90, 100, 105), (x, r.y + 18), (x, r.bottom - 6), 1)
        scr.blit(self.font_small.render("SAMPLE DRAWER", True, (210, 215, 220)), (r.x + 10, r.y + 4))
        if self.was_bay:
            k = int(150 + 100 * math.sin(self.tick * 0.2))
            pygame.draw.rect(scr, (0, k, 150), r.inflate(12, 12), 3, border_radius=8)

    def shade(self, colour):
        """The robot is dimmer with the floodlights off, but never lost in the dark."""
        k = 1.0 if self.lights else 0.62
        return tuple(int(c * k) for c in colour)

    def rov_point(self, px, py):
        """A point on the ROV body (px towards its nose, py down) -> screen."""
        a = math.radians(self.rov_tilt)
        px *= self.rov_face
        return (self.rovx + px * math.cos(a) - py * math.sin(a),
                self.rovy + 4 * math.sin(self.tick * 0.05) + px * math.sin(a) + py * math.cos(a))

    def draw_rov(self):
        """Outside view: the whole ROV, seen from a distance, flying above the gripper."""
        scr, at, shade, f = self.screen, self.rov_point, self.shade, self.rov_face
        steel, dark, yellow = shade((120, 130, 135)), shade((52, 60, 66)), shade((236, 188, 44))

        # tether up to the ship
        p0, p2 = at(-40, -32), (self.rovx - f * 170, -20)
        p1 = (p0[0] - f * 30, p0[1] - 130)
        cable = [((1 - u) ** 2 * p0[0] + 2 * u * (1 - u) * p1[0] + u * u * p2[0],
                  (1 - u) ** 2 * p0[1] + 2 * u * (1 - u) * p1[1] + u * u * p2[1]) for u in [i / 10 for i in range(11)]]
        pygame.draw.lines(scr, shade((190, 160, 60)), False, cable, 2)

        # thruster wash
        rx, ry = at(-84, 4)
        for i in range(6):
            u = (self.tick * 0.02 + i / 6) % 1
            pygame.draw.circle(scr, shade((150, 190, 205)),
                               (int(rx - f * u * 60), int(ry - u * 26 + 5 * math.sin(self.tick * 0.1 + i * 2))),
                               int(1 + 3 * (1 - u)), 1)

        # arm: shoulder under the body, elbow, then the gripper
        sx, sy = at(18, 26)
        wx, wy = self.hx, self.hy - 14
        ex, ey = (sx + wx) / 2 - f * 34, (sy + wy) / 2
        for colour, width in ((shade((60, 68, 72)), 14), (steel, 5)):
            pygame.draw.line(scr, colour, (sx, sy), (ex, ey), width)
            pygame.draw.line(scr, colour, (ex, ey), (wx, wy), width)
        pygame.draw.circle(scr, shade((90, 100, 105)), (int(ex), int(ey)), 9)

        # rear thruster with a spinning blade
        pygame.draw.polygon(scr, dark, [at(-56, -8), at(-80, -12), at(-80, 20), at(-56, 16)])
        blade = 13 * math.sin(self.tick * 0.6)
        pygame.draw.line(scr, steel, at(-83, 4 - blade), at(-83, 4 + blade), 3)
        # frame, skids, float
        pygame.draw.polygon(scr, dark, [at(-56, -6), at(58, -6), at(58, 24), at(-56, 24)])
        pygame.draw.polygon(scr, steel, [at(-56, -6), at(58, -6), at(58, 24), at(-56, 24)], 2)
        for x in (-36, 36):
            pygame.draw.line(scr, steel, at(x, 24), at(x, 34), 3)
        pygame.draw.line(scr, steel, at(-50, 34), at(50, 34), 4)
        pygame.draw.polygon(scr, dark, [at(-10, -34), at(10, -34), at(8, -42), at(-8, -42)])
        pygame.draw.polygon(scr, yellow, [at(-60, -34), at(44, -34), at(62, -24), at(62, -6), at(-60, -6)])
        pygame.draw.polygon(scr, shade((200, 150, 30)), [at(-60, -13), at(62, -13), at(62, -6), at(-60, -6)])
        label = pygame.transform.rotate(self.rov_label, -self.rov_tilt)
        lx, ly = at(-8, -23)
        scr.blit(label, (lx - label.get_width() / 2, ly - label.get_height() / 2))
        # camera dome and lamp
        dx, dy = at(50, 9)
        pygame.draw.circle(scr, shade((18, 40, 56)), (int(dx), int(dy)), 14)
        pygame.draw.circle(scr, shade((150, 205, 225)), (int(dx), int(dy)), 14, 2)
        hx, hy = at(54, 4)
        pygame.draw.circle(scr, shade((200, 235, 245)), (int(hx), int(hy)), 3)
        lx, ly = at(30, 22)
        pygame.draw.circle(scr, (255, 235, 160) if self.lights else (80, 80, 70), (int(lx), int(ly)), 5)
        if self.lights:
            scr.blit(self.lamp_glow, (lx - 34, ly - 34), special_flags=pygame.BLEND_RGB_ADD)

        # gripper
        col = {"OPEN": GREY, "PINCH": CYAN, "FIST": ORANGE}[self.gesture]
        o = 1.0 if self.gesture == "OPEN" else 0.25 if self.gesture == "PINCH" else 0.1
        x, y = self.hx, self.hy
        pygame.draw.line(scr, shade((150, 160, 165)), (x, y - 16), (x, y + 4), 6)
        for s in (-1, 1):
            pygame.draw.lines(scr, col, False, [(x, y + 4), (x + s * 20 * o, y + 18), (x + s * 7 * o, y + 34)], 4)
        pygame.draw.circle(scr, shade((90, 100, 105)), (int(x), int(y + 2)), 7)

    def draw_hands(self):
        """First person: you are the robot, and its two hands are your hands, joint for joint."""
        scr, shade = self.screen, self.shade
        skin, line, light = shade((222, 196, 178)), shade((126, 98, 86)), shade((242, 224, 210))
        nail, sleeve, cuff = shade((248, 234, 228)), shade((52, 60, 68)), shade((74, 84, 94))
        grip = (self.hx, self.hy + 32)
        for side in (-self.active_hand, self.active_hand):          # the working hand goes on top
            h = self.hands[side]
            S, k = h["size"], h["k"]
            # middle knuckle on screen; the working hand is placed so thumb and index close on the grab point
            ax = h["free"][0] + (grip[0] - h["off"][0] * S - h["free"][0]) * k
            ay = h["free"][1] + (grip[1] - h["off"][1] * S - h["free"][1]) * k
            P = [(ax + x * S, ay + y * S) for x, y in h["pts"]]

            # wrist corners, thumb side first
            d = math.dist(P[0], P[9]) or 1
            ux, uy = (P[9][0] - P[0][0]) / d, (P[9][1] - P[0][1]) / d
            w1 = (P[0][0] - uy * 0.3 * S, P[0][1] + ux * 0.3 * S)
            w2 = (P[0][0] + uy * 0.3 * S, P[0][1] - ux * 0.3 * S)
            if math.dist(w2, P[1]) < math.dist(w1, P[1]):
                w1, w2 = w2, w1

            # forearm: carries on from the hand and leaves the screen at that hand's side
            fx, fy = -ux + side * 0.7, -uy + 0.5
            d = math.hypot(fx, fy) or 1
            ex, ey = P[0][0] + fx / d * 1300, P[0][1] + fy / d * 1300
            nx, ny = -fy / d * 0.55 * S, fx / d * 0.55 * S
            if math.dist((ex + nx, ey + ny), w1) > math.dist((ex - nx, ey - ny), w1):
                nx, ny = -nx, -ny
            pygame.draw.polygon(scr, sleeve, [w1, w2, (ex - nx, ey - ny), (ex + nx, ey + ny)])
            pygame.draw.line(scr, shade((92, 104, 114)), P[0], (ex, ey), 4)

            # palm, the muscle at the base of the thumb, and the web between thumb and index
            palm = [w1, P[1], P[5], P[9], P[13], P[17], w2]
            for grow, colour in ((2.5, line), (0, skin)):
                r = 0.09 * S + grow
                pygame.draw.polygon(scr, colour, palm)
                for a, b in zip(palm, palm[1:] + palm[:1]):
                    capsule(scr, colour, a, b, r, r)
                pygame.draw.polygon(scr, colour, [P[1], P[2], P[5]])
                capsule(scr, colour, P[2], P[5], 0.05 * S + grow, 0.05 * S + grow)
            for i in (5, 9, 13, 17):                                  # tendons on the back of the hand
                pygame.draw.line(scr, shade((204, 176, 158)), P[i],
                                 (P[i][0] + (P[0][0] - P[i][0]) * 0.5, P[i][1] + (P[0][1] - P[i][1]) * 0.5), 2)

            accent = {"OPEN": GREY, "PINCH": CYAN, "FIST": ORANGE}[self.gesture] if side == self.active_hand \
                else shade((110, 150, 170))
            capsule(scr, cuff, w1, w2, 0.12 * S, 0.12 * S)
            pygame.draw.line(scr, accent, w1, w2, 3)

            for name, ids in FINGERS.items():
                j = [P[i] for i in ids]
                r0, r1 = GIRTH[name][0] * S, GIRTH[name][1] * S
                rad = [r0 + (r1 - r0) * i / 3 for i in range(4)]
                for grow, colour in ((2.5, line), (0, skin)):
                    for i in range(3):
                        capsule(scr, colour, j[i], j[i + 1], rad[i] + grow, rad[i + 1] + grow)
                for i in range(3):
                    # light catches the upper left of each segment
                    o = rad[i + 1] * 0.35
                    pygame.draw.line(scr, light, (j[i][0] - o, j[i][1] - o), (j[i + 1][0] - o, j[i + 1][1] - o),
                                     max(1, int(rad[i + 1] * 0.4)))
                for i in (1, 2):                                      # creases across the knuckles
                    d = math.dist(j[i - 1], j[i]) or 1
                    cx_, cy_ = -(j[i][1] - j[i - 1][1]) / d * rad[i] * 0.7, (j[i][0] - j[i - 1][0]) / d * rad[i] * 0.7
                    pygame.draw.line(scr, line, (j[i][0] + cx_, j[i][1] + cy_), (j[i][0] - cx_, j[i][1] - cy_), 1)
                # fingernail, unless the finger is folded away under the hand
                tx, ty = j[3][0] - j[2][0], j[3][1] - j[2][1]
                if tx * (j[1][0] - j[0][0]) + ty * (j[1][1] - j[0][1]) > 0:
                    d = math.hypot(tx, ty) or 1
                    tx, ty, r = tx / d * rad[3], ty / d * rad[3], rad[3] * 0.62
                    a, b = (j[3][0] - tx * 1.1, j[3][1] - ty * 1.1), (j[3][0] - tx * 0.1, j[3][1] - ty * 0.1)
                    capsule(scr, line, a, b, r + 1, r + 1)
                    capsule(scr, nail, a, b, r, r)

    def draw_gaze(self):
        gx, gy = self.gaze_screen
        if not (0 <= gx <= W and 0 <= gy <= H):
            return
        s = pygame.Surface((70, 70), pygame.SRCALPHA)
        pygame.draw.circle(s, (120, 220, 255, 90), (35, 35), 16, 2)
        if self.dwell_target is not None and self.dwell_time > 0.1:
            k = min(1, self.dwell_time / config.DWELL_SECONDS)
            pygame.draw.arc(s, (120, 220, 255, 230), (11, 11, 48, 48), math.pi / 2, math.pi / 2 + k * 6.283, 3)
        self.screen.blit(s, (gx - 35, gy - 35))
        if self.gazed and self.dwell_target is not None:
            d = self.dwell_target
            x, y = d.x - self.camx, d.y - self.camy - 80
            label = self.font.render(d.species.upper(), True, (230, 240, 245))
            sub = self.font_small.render("sturdy - firm grip (fist)" if d.sturdy else "fragile - gentle grip (pinch)",
                                         True, ORANGE if d.sturdy else CYAN)
            w = max(label.get_width(), sub.get_width()) + 20
            x = max(w / 2 + 6, min(W - w / 2 - 6, x))
            y = max(56, y)
            self.panel(pygame.Rect(x - w / 2, y - 44, w, 46))
            self.screen.blit(label, (x - label.get_width() / 2, y - 38))
            self.screen.blit(sub, (x - sub.get_width() / 2, y - 18))

    def panel(self, rect, alpha=205):
        s = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(s, (0, 20, 30, alpha), s.get_rect(), border_radius=7)
        self.screen.blit(s, rect.topleft)

    def text(self, txt, pos, col=(220, 225, 230), font=None, alpha=255, right=False, centre=False):
        img = (font or self.font).render(txt, True, col)
        if alpha < 255:
            img.set_alpha(alpha)
        x, y = pos
        if right: x -= img.get_width()
        if centre: x -= img.get_width() / 2
        self.screen.blit(img, (x, y))

    def draw_hud(self):
        tr = self.tracker
        use_mouse = self.mouse_mode or not tr.available

        # ---------- left: stats ----------
        self.panel(pygame.Rect(14, 14, 420, 300))
        self.text("ROV-6 · ORCA CONSOLE", (28, 24), (120, 220, 255))
        if self.vr:
            gaze_row, hand_row = self.vr.status_rows()
        else:
            gaze_row = ("GAZE", "MOUSE/ARROWS" if use_mouse else
                        ("TRACKING" if tr.face_ok else "LOST") + ("" if tr.is_calibrated() else " · calibrating"),
                        GREY if use_mouse or tr.face_ok else (255, 120, 100))
            hand_row = ("HAND", "MOUSE" if use_mouse else ("TRACKING" if tr.hand_ok else "LOST"),
                        GREY if use_mouse or tr.hand_ok else (255, 120, 100))
        rows = [
            ("LIGHTS", "ON" if self.lights else "OFF", (255, 230, 150) if self.lights else GREY),
            ("CONTROL", f"FINE   x{config.FINE_SCALE:.2f}" if self.fine else "NORMAL x1.00",
             (255, 220, 120) if self.fine else GREY),
            ("GRIP", {"OPEN": "OPEN", "PINCH": "PINCH · gentle", "FIST": "FIST  · firm"}[self.gesture],
             {"OPEN": GREY, "PINCH": CYAN, "FIST": ORANGE}[self.gesture]),
            ("VIEW", ("FIRST PERSON" if self.pov == "first" else "OUTSIDE") + (" · NARROW" if self.narrow else " · WIDE")
             + (" · panning" if self.pan_speed > 0.5 else ""), GREY),
            gaze_row, hand_row,
            ("SAMPLES", f"{self.stored} stored · {self.damaged} damaged · {self.slipped} slipped", GREY),
        ]
        for i, (k, v, c) in enumerate(rows):
            self.text(k, (28, 52 + i * 22), (140, 150, 155))
            self.text(v, (122, 52 + i * 22), c)

        y = 52 + len(rows) * 22 + 6
        self.text("DISTURBANCE", (28, y), (140, 150, 155))
        bar = pygame.Rect(150, y + 4, 270, 10)
        pygame.draw.rect(self.screen, (40, 60, 70), bar, border_radius=3)
        d = self.disturbance
        col = (int(60 + 195 * d), int(220 - 140 * d), int(140 - 80 * d))
        pygame.draw.rect(self.screen, col, (bar.x, bar.y, bar.w * d, bar.h), border_radius=3)

        # minimap: whole world, your view, specimens
        y += 26
        self.text("MAP", (28, y - 2), (140, 150, 155))
        mm = pygame.Rect(70, y, 350, 38)
        pygame.draw.rect(self.screen, (10, 35, 48), mm, border_radius=3)
        sx, sy = mm.w / WORLD_W, mm.h / WORLD_H
        pygame.draw.line(self.screen, (60, 66, 54), (mm.x, mm.y + config.SEABED_Y * sy),
                         (mm.right, mm.y + config.SEABED_Y * sy), 2)
        for s in self.specimens:
            if s.state != "gone":
                c = (255, 80, 60) if s.state == "damaged" else s.info["colour"]
                pygame.draw.circle(self.screen, c, (int(mm.x + s.x * sx), int(mm.y + s.y * sy) - 2), 2)
        for mx, my in self.markers:
            pygame.draw.circle(self.screen, (255, 220, 0), (int(mm.x + mx * sx), int(mm.y + my * sy)), 1)
        pygame.draw.rect(self.screen, (230, 240, 245),
                         (mm.x + self.camx * sx, mm.y + self.camy * sy, W * sx, H * sy), 1)

        # ---------- right: ORCA + camera ----------
        self.panel(pygame.Rect(W - 300, 14, 286, 112 if self.voice.error else 92))
        status = self.orca.status
        dot = {"LISTENING": (0, 255, 120), "SPEAKING": (120, 220, 255), "THINKING": (255, 220, 120)}.get(
            status, (255, 90, 80))
        if self.orca.speaking:
            status, dot = "SPEAKING", (120, 220, 255)
        r = 7 + (2 * math.sin(self.tick * 0.2) if status in ("LISTENING", "SPEAKING") else 0)
        pygame.draw.circle(self.screen, dot, (W - 36, 34), int(r))
        self.text(f"ORCA · {status}", (W - 286, 24), (120, 220, 255))
        hint = 'Say "Orca, ..."   SPACE = mute' if config.WAKE_WORD_MODE else "SPACE = push to talk"
        self.text(hint, (W - 286, 48), GREY, self.font_small)
        brain = f"brain: {self.orca.llm_model}" if self.orca.llm_model else "brain: built-in"
        self.text(brain, (W - 286, 66), (140, 150, 155), self.font_small)
        if self.voice.error:
            self.text(self.voice.error[:40], (W - 286, 84), (255, 120, 100), self.font_small)
            self.text(self.voice.error[40:80], (W - 286, 98), (255, 120, 100), self.font_small)

        if self.vr:
            lines = self.vr.monitor_lines()
            self.panel(pygame.Rect(14, 322, 420, 14 + 18 * len(lines)))
            for i, l in enumerate(lines):
                self.text(l[:56], (28, 330 + i * 18), (120, 220, 255) if i == 0 else GREY, self.font_small)

        if self.show_preview:
            box = pygame.Rect(W - 300, 134, 286, 210)
            self.panel(box)
            if self.vr:
                # the hand's joints as the phone reports them: the camera picture never leaves the phone
                data, pw, ph = self.vr.hand.preview()
                self.screen.blit(pygame.image.frombuffer(data, (pw, ph), "RGB"), (box.x + 31, box.y + 30))
                self.text(f"HAND JOINTS FROM THE PHONE  {self.vr.hand.fps:4.1f} fps", (box.x + 14, box.y + 8),
                          (140, 150, 155), self.font_small)
            elif not use_mouse and tr.preview:
                with tr.lock:
                    data, pw, ph = tr.preview
                img = pygame.image.frombuffer(data, (pw, ph), "RGB")
                self.screen.blit(img, (box.x + 31, box.y + 30))
                self.text(f"EYE + HAND TRACKING  {tr.fps:4.1f} fps", (box.x + 14, box.y + 8), (140, 150, 155),
                          self.font_small)
            else:
                lines = ["MOUSE MODE", "", "move mouse   = hand", "left click   = pinch (gentle)",
                         "right click  = fist (firm)", "arrows/WASD  = look around"]
                if tr.error:
                    lines += ["", tr.error[:38], tr.error[38:76]]
                for i, l in enumerate(lines):
                    self.text(l, (box.x + 14, box.y + 12 + i * 18), (120, 220, 255) if i == 0 else GREY,
                              self.font_small)

        # ---------- field notes ----------
        if self.notes:
            n = self.notes[-4:]
            top = 352 if self.show_preview else 134
            self.panel(pygame.Rect(W - 300, top, 286, 30 + 18 * len(n)))
            self.text("FIELD NOTES", (W - 286, top + 8), (120, 220, 255), self.font_small)
            for i, note in enumerate(n):
                self.text(note[:40], (W - 286, top + 28 + i * 18), GREY, self.font_small)

        # ---------- calibration target ----------
        if not use_mouse and tr.available and not tr.is_calibrated():
            k = int(150 + 100 * math.sin(self.tick * 0.15))
            pygame.draw.circle(self.screen, (0, k, 255), (W // 2, H // 2), 22, 2)
            pygame.draw.circle(self.screen, (0, k, 255), (W // 2, H // 2), 4)
            self.text("LOOK HERE", (W / 2, H / 2 + 32), (120, 220, 255), centre=True)

        # ---------- captions ----------
        now = time.time()
        a1, a2 = now - self.orca.you_time, now - self.orca.orca_time
        if a1 < 7 or a2 < 9:
            self.panel(pygame.Rect(14, H - 74, W - 28, 60), 170)
            if a1 < 7:
                self.text("YOU   › " + self.orca.you_text[:120], (28, H - 64),
                          alpha=int(255 * min(1, (7 - a1) / 2)))
            if a2 < 9:
                self.text("ORCA  › " + self.orca.orca_text[:120], (28, H - 40), (120, 220, 255),
                          alpha=int(255 * min(1, (9 - a2) / 2)))
        else:
            self.text("H = help", (20, H - 24), (255, 255, 255), alpha=90)

        # ---------- flash ----------
        fa = now - self.flash_at
        if fa < 1.8:
            self.text(self.flash_text, (W / 2, 150), self.flash_col, self.font_big,
                      alpha=int(255 * min(1, (1.8 - fa) / 0.6)), centre=True)

        # ---------- help ----------
        if self.show_help and now > self.help_until:
            self.show_help = False
        if self.show_help:
            self.draw_help()

    def draw_help(self):
        lines = [
            "ROV-6 · ORCA  —  HOW TO DRIVE",
            "",
            "LOOK at an edge of the screen      pan the view",
            "LOOK at a specimen for a second    ORCA identifies it",
            "MOVE your hand                     move the gripper",
            "PINCH (thumb + index)              gentle grip  - fragile specimens",
            "FIST                               firm grip    - urchins only",
            "OPEN hand                          let go (over the drawer = store)",
            "",
            'VOICE  "Orca, ..."  lights on/off · fine/normal mode · wide/narrow view',
            "       mark sample · note <text> · calibrate · centre view · status",
            "       first person / outside view · quiet · help · where is the specimen?",
            "",
            "KEYS   SPACE mic  L lights  F fine  V view  M mark  C calibrate",
            "       X centre  P point of view  K mouse mode  T camera",
            "       - / + eye sensitivity  H help  ESC quit",
        ]
        w, h = 700, 34 + len(lines) * 22
        r = pygame.Rect(W / 2 - w / 2, H / 2 - h / 2, w, h)
        self.panel(r, 238)
        for i, l in enumerate(lines):
            self.text(l, (r.x + 24, r.y + 18 + i * 22), (120, 220, 255) if i == 0 else (225, 230, 235))

    # -------------------------------------------------------------- loop
    def key(self, k):
        cmd = None
        if self.vr and self.vr.key(k):
            pass
        elif k == pygame.K_SPACE:
            on = self.voice.toggle()
            self.flash("MIC ON" if on else "MIC OFF", (0, 255, 120) if on else (255, 90, 80))
        elif k == pygame.K_l: cmd = "lights_off" if self.lights else "lights_on"
        elif k == pygame.K_f: cmd = "normal" if self.fine else "fine"
        elif k == pygame.K_v: cmd = "wide" if self.narrow else "narrow"
        elif k == pygame.K_m: cmd = "mark"
        elif k == pygame.K_c: cmd = "calibrate"
        elif k == pygame.K_x: cmd = "centre"
        elif k == pygame.K_p: cmd = "pov_third" if self.pov == "first" else "pov_first"
        elif k == pygame.K_k:
            self.mouse_mode = not self.mouse_mode
            self.flash("MOUSE MODE" if self.mouse_mode else "TRACKING MODE", GREY)
            logger.log("key", "mouse_mode", str(self.mouse_mode))
        elif k == pygame.K_t: self.show_preview = not self.show_preview
        elif k in (pygame.K_MINUS, pygame.K_EQUALS):   # eye sensitivity - / +
            f = 1.15 if k == pygame.K_EQUALS else 1 / 1.15
            config.GAZE_GAIN_X *= f
            config.GAZE_GAIN_Y *= f
            self.flash(f"EYE SENSITIVITY {config.GAZE_GAIN_X:.1f}", CYAN)
            logger.log("key", "gaze_gain", f"{config.GAZE_GAIN_X:.2f}")
        elif k == pygame.K_h:
            self.show_help = not self.show_help
            self.help_until = time.time() + 3600
        if cmd:
            self.run_command(cmd, "key")

    def run(self):
        running = True
        while running:
            dt = self.clock.tick(config.FPS) / 1000
            for e in pygame.event.get():
                if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                    running = False
                elif e.type == pygame.KEYDOWN:
                    self.key(e.key)
            self.update(dt)
            if not self.vr:
                self.draw()
                pygame.display.flip()
            elif self.vr.render():          # both eyes drawn and sent to the phone...
                self.screen.blit(self.vr.left, (0, 0))      # ...and the left one shown here, with the full console
                self.draw_hud()
                pygame.display.flip()
        if self.vr:
            self.vr.close()
        self.tracker.stop()
        logger.close()
        print("Session log saved to", logger.PATH)
        pygame.quit()


if __name__ == "__main__":
    Game().run()
