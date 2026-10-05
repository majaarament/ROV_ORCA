"""
VRSession: ties the phone to the game.

The game asks it three things every frame: what the robot should do (control), to draw and send
the view for both eyes (render), and what the headset's small HUD should say (hud). Everything
else here is bookkeeping: who is connected, which phase we are in, and the experiment log.
"""
import json
import time

import pygame

import config
import logger
from vr.calibration import Calibration
from vr.hand_input import HandInput
from vr.head_input import HeadInput
from vr.server import Server
from vr.teleop import Controls, TeleoperationController
from vr.voice_input import VoiceInput


class VRSession:
    def __init__(self, game):
        self.game = game
        self.head, self.hand = HeadInput(), HandInput()
        self.teleop = TeleoperationController(self.head, self.hand)
        self.server = Server(self)
        phone_voice = VoiceInput(game.orca, self.server)
        self.voice = phone_voice if config.VR_MIC == "phone" else None      # None = use this Mac's microphone
        if config.VR_AUDIO_OUT == "phone":
            game.orca.player = phone_voice.play
        self._phone_voice = phone_voice
        self.calibration = Calibration(self.head, self.hand, game.orca)
        self.phase = "waiting"          # waiting (no headset yet) / calibrating / dive
        self.controls = Controls()

        # head -> view: so many world pixels per degree, around the usual starting view
        w, h = config.VR_EYE_SIZE
        self.eye = config.VR_EYE_SEPARATION // 2
        self.ppd = w / config.VR_FOV_DEG * config.VR_HEAD_GAIN
        self.xmin, self.xmax = self.eye, config.WORLD_W - w - self.eye
        self.ymin, self.ymax = 0, config.WORLD_H - h
        self.cx0 = config.WORLD_W / 2 - w / 2
        self.cy0 = max(self.ymin, min(self.ymax, config.SEABED_Y - h + 170))

        # both eyes side by side: this is the picture the phone gets
        self.stereo = pygame.Surface((2 * w, h))
        self.left, self.right = self.stereo.subsurface((0, 0, w, h)), self.stereo.subsurface((w, 0, w, h))

        self._todo = []                 # things the phone asked for, done on the game's own thread
        self._sent_frame = self._sent_state = self._logged = 0.0
        self._state = ""
        self._started = time.time()
        self._dive_at = None
        logger.log("vr", "session_start", f"pinch={config.VR_PINCH_ACTION},normal=x{config.MOVEMENT_SCALE_NORMAL},"
                                          f"precision=x{config.MOVEMENT_SCALE_PRECISION},head_gain={config.VR_HEAD_GAIN}")
        self.server.start()

    # ================= from the phone (the server's thread) =================
    def client_config(self):
        return dict(t="config", w=config.VR_EYE_SIZE[0], h=config.VR_EYE_SIZE[1], ppd=self.ppd,
                    cx0=self.cx0, cy0=self.cy0, xmin=self.xmin, xmax=self.xmax, ymin=self.ymin, ymax=self.ymax,
                    signs=config.VR_HEAD_SIGNS, rollLimit=config.VR_ROLL_LIMIT_DEG, overscan=config.VR_OVERSCAN,
                    lens=config.VR_LENS_K, handFps=config.VR_HAND_FPS, camSize=config.VR_CAMERA_SIZE,
                    camLag=config.VR_CAMERA_LAG * 1000, mic=config.VR_MIC == "phone")

    def on_connect(self, address):
        logger.log("vr", "headset_connected", str(address))
        self._state = ""                # send the HUD again from scratch

    def on_disconnect(self):
        logger.log("vr", "headset_disconnected")

    def on_audio(self, pcm):
        if self.voice:
            self.voice.feed(pcm)

    def on_message(self, m):
        kind = m.get("t")
        if kind == "head":
            self.head.push(float(m["y"]), float(m["p"]), float(m["r"]), float(m["ts"]))
        elif kind == "hand":
            self.hand.push(m)
        elif kind == "played":
            self._phone_voice.played(m.get("id"))
        elif kind == "start":           # START VR EXPERIENCE was pressed and the permissions answered
            perms = m.get("perms", {})
            logger.log("vr", "headset_start", json.dumps(perms) + " " + str(m.get("ua", ""))[:120])
            if perms.get("motion") != "granted":
                self.head.available = False
            if perms.get("camera") != "granted":
                self.hand.available, self.hand.error = False, "no rear camera: " + str(perms.get("camera"))
            if self.voice and perms.get("mic") != "granted":
                self.voice.mic_ok, self.voice.error = False, "the phone gave no microphone"
                self.game.orca.status = "NO MIC"
            self._todo.append("start")
        elif kind == "cmd" and m.get("cmd") in ("recenter", "calibrate", "skip"):
            self._todo.append(m["cmd"])
        elif kind == "error":
            logger.log("vr", "headset_error", str(m.get("text", ""))[:200])
            print("Phone:", m.get("text"))
            if m.get("what") == "hand":
                self.hand.available, self.hand.error = False, str(m.get("text", ""))[:80]

    # ================= for the game (its own thread) =================
    def camera(self, yaw, pitch):
        """Head direction in degrees -> the top-left corner of the view in the world."""
        return (max(self.xmin, min(self.xmax, self.cx0 + yaw * self.ppd)),
                max(self.ymin, min(self.ymax, self.cy0 - pitch * self.ppd)))

    def recenter(self):
        self.head.recenter()
        self.teleop.reset_hand()

    def calibrate(self):
        self.phase = "calibrating"
        self.calibration.begin()

    def control(self, dt):
        """One frame of instructions for the robot, from head, hand and the research condition."""
        while self._todo:
            what = self._todo.pop(0)
            if what == "start" and self.phase == "waiting" or what == "calibrate":
                self.calibrate()
            elif what == "recenter":
                self.recenter()
            elif what == "skip":
                self.calibration.skip()
        if self.phase == "calibrating" and self.calibration.update():
            self.phase, self._dive_at = "dive", time.time()
            self.game.task_start = self._dive_at
            self.teleop.reset_hand()
            logger.log("vr", "dive_start")
        self.teleop.enabled = self.phase == "dive"
        self.teleop.fine = self.game.fine
        c = self.controls = self.teleop.step(dt)

        now = time.time()
        if now - self._logged >= 1 / config.VR_LOG_HZ and self.server.connected:
            self._logged = now
            g = self.game
            logger.log("head", "sample", f"yaw={c.yaw:.1f},pitch={c.pitch:.1f},roll={c.roll:.1f},"
                                         f"speed={self.head.speed:.0f},cam={int(g.camx)},{int(g.camy)}")
            if c.hand == "tracking":
                u, v = self.hand.anchor
                logger.log("hand", "sample", f"u={u:.3f},v={v:.3f},palm={self.hand.palm:.3f},shape={self.hand.gesture},"
                                             f"scale={c.scale:g},gripper={int(g.camx + g.hx)},{int(g.camy + g.hy)}")
        return c

    def render(self):
        """Draw the view for each eye and send it to the phone. True when new pictures were drawn."""
        now = time.time()
        self._send_state(now)
        if now - self._sent_frame < 1 / config.VR_STREAM_FPS - 0.004:
            return False
        self._sent_frame = now
        g = self.game
        g.draw_eye(self.left, -self.eye)
        g.draw_eye(self.right, self.eye)
        self.server.send_frame(g.camx, g.camy, pygame.image.tobytes(self.stereo, "RGB"), self.stereo.get_size())
        return True

    def hud(self):
        """The little that is shown inside the headset. No statistics: those stay on the Mac."""
        g, c, now = self.game, self.controls, time.time()
        orca = g.orca
        warn = None
        if self.phase == "dive":
            if self.hand.available is False:
                warn = ["NO HAND CAMERA", "The rear camera is not available"]
            elif c.hand == "lost":
                warn = ["HAND NOT VISIBLE", "Move your hand in front of the headset"]
        return dict(
            orca="SPEAKING" if orca.speaking else orca.status,
            sub=orca.orca_text if (orca.speaking or now - orca.orca_time < 6) else "",
            chip=f"PRECISION {c.scale:g}×" if c.precision and self.phase == "dive" else "",
            hold=g.held.species.upper() if g.held else "",
            flash=[g.flash_text, list(g.flash_col)] if now - g.flash_at < 1.8 else None,
            warn=warn,
            card=self.calibration.card() if self.phase == "calibrating" else None)

    def _send_state(self, now):
        if not self.server.connected:
            return
        state = dict(t="state", phase=self.phase, yaw0=round(self.head.yaw0, 2), pitch0=round(self.head.pitch0, 2),
                     hud=self.hud())
        text = json.dumps(state)
        if text != self._state or now - self._sent_state > 0.25:
            self._state, self._sent_state = text, now
            c = self.controls           # for the debug page only
            state["dbg"] = dict(shape=self.hand.gesture, hand=c.hand, gesture=c.gesture, scale=c.scale,
                                yaw=round(c.yaw, 1), pitch=round(c.pitch, 1), roll=round(c.roll, 1),
                                fps=round(self.server.fps), q=int(self.server.quality))
            self.server.send(state)

    # ---------- the experimenter's screen and keys ----------
    def status_rows(self):
        """The HEAD and HAND rows of the console on the Mac."""
        ok, bad = (190, 195, 200), (255, 120, 100)
        c = self.controls
        if not self.server.connected:
            return ("HEAD", "NO PHONE", bad), ("HAND", "NO PHONE", bad)
        head = ("HEAD", "NO SENSOR" if self.head.available is False else
                f"yaw {c.yaw:+4.0f}  pitch {c.pitch:+3.0f}" if c.head_ok else "LOST",
                ok if c.head_ok else bad)
        hand = ("HAND", "NO CAMERA" if self.hand.available is False else
                {"tracking": "TRACKING", "frozen": "FROZEN", "lost": "NOT VISIBLE"}[c.hand]
                + (f" · x{c.scale:g}" if c.hand == "tracking" else ""), ok if c.hand == "tracking" else bad)
        return head, hand

    def monitor_lines(self):
        s = self.server
        step = f" · step {self.calibration.step}" if self.phase == "calibrating" else ""
        return ["PHONE VR · " + (s.error or s.url or "starting..."),
                ("phone connected" if s.connected else "waiting for the phone: open the address above") +
                (f" · {s.fps:.0f} fps q{int(s.quality)}" if s.connected else ""),
                f"phase: {self.phase}{step} · pinch = {config.VR_PINCH_ACTION}",
                "ENTER skip step · C calibrate again · X recentre"]

    def key(self, k):
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.calibration.skip()
        elif k == pygame.K_c:
            self.calibrate()
        elif k == pygame.K_k:
            pass                        # there is no mouse mode in VR
        else:
            return False
        logger.log("key", "vr", pygame.key.name(k))
        return True

    def close(self):
        dive = f",dive={time.time() - self._dive_at:.1f}s" if self._dive_at else ""
        logger.log("vr", "session_end", f"total={time.time() - self._started:.1f}s{dive}")
