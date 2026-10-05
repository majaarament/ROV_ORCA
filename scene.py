"""
The underwater world you look at.

  * far away: a real photo of a giant kelp forest (NOAA), out of focus like distant water
  * shafts of sunlight coming down from the surface
  * kelp with blades and gas bladders, hazier the further away it is
  * a seabed made from photographed sand and rock, with light rippling over it
  * fish with proper bodies, fins and countershading

The photo and textures live in assets/ (see assets/CREDITS.md). If they are missing,
everything still runs with plain colours instead.
"""
import math
import os
import random

import numpy as np
import pygame

import config

W, H = config.WIDTH, config.HEIGHT
WORLD_W, WORLD_H = config.WORLD_W, config.WORLD_H
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

FAR = 0.25                          # how much the photo moves when the view pans (1 = with the world)
HAZE = (14, 70, 92)                 # colour of distant water
WATER_TINT = (0.46, 0.70, 0.70)     # what sea water does to colours near the seabed
STRIP_TOP = config.SEABED_Y - 110   # top of the pre-drawn seabed strip, in world coordinates

# per kelp layer (far, middle, near): stipe, blade low down, blade high up
KELP_COLOURS = [((24, 80, 88), (26, 86, 92), (40, 108, 106)),
                ((84, 76, 34), (96, 92, 38), (176, 152, 60)),
                ((16, 34, 30), (20, 42, 32), (40, 62, 36))]

# name, back, belly, body depth, blotchy?
FISH_KINDS = [("garibaldi", (226, 92, 22), (255, 150, 56), 0.25, False),
              ("blue rockfish", (38, 56, 84), (130, 150, 170), 0.19, True),
              ("senorita", (186, 136, 60), (240, 216, 156), 0.11, False),
              ("kelp bass", (66, 76, 48), (196, 194, 152), 0.17, True),
              ("blacksmith", (40, 62, 104), (98, 128, 168), 0.21, False)]


def seabed_at(x):
    return config.SEABED_Y + 14 * math.sin(x * 0.004) + 8 * math.sin(x * 0.013 + 1.3)


def _load(name):
    try:
        return pygame.image.load(os.path.join(ASSETS, name)).convert()
    except Exception:
        return None


def _mix(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


# =====================================================================
def fish_frames(length, kind):
    """Three frames of one fish facing right, tail swinging. Drawn big, then shrunk so it's smooth."""
    _, back, belly, depth, blotchy = kind
    back, belly = _mix(back, HAZE, 0.22), _mix(belly, HAZE, 0.22)      # seen through water
    fin = _mix(back, belly, 0.35)
    k = 3
    L, Hh = int(length * k * 1.25), int(length * k * 0.8)
    cy, b = Hh / 2, length * k * depth                                 # b = half the body height
    x0, x1 = L * 0.16, L - 2                                           # tail root, nose

    # body: a tapering profile, dark on the back and pale on the belly
    xs, ys = np.mgrid[0:L, 0:Hh].astype("float32")
    u = np.clip((xs - x0) / (x1 - x0), 0, 1)
    half = b * np.maximum(np.clip(np.sin(np.pi * u ** 0.72), 0, 1) ** 0.62, 0.17 * (u < 0.3))
    v = (ys - cy) / np.maximum(half, 0.01)
    inside = (np.abs(v) < 1) & (xs >= x0) & (xs <= x1)
    shade = np.clip(v * 0.75 + 0.42, 0, 1) ** 1.3                      # 0 on the back, 1 on the belly
    rgb = np.array(back, "float32") + (np.array(belly, "float32") - np.array(back, "float32")) * shade[..., None]
    rgb *= (1.0 + 0.28 * np.exp(-((v + 0.25) / 0.22) ** 2))[..., None]   # sheen along the flank
    if blotchy:
        rgb *= (0.86 + 0.14 * np.sin(xs * 0.11) * np.sin(ys * 0.17 + xs * 0.05))[..., None]
    rgb *= (0.72 + 0.28 * np.sqrt(np.clip(1 - v * v, 0, 1)))[..., None]  # rounder at the edges
    body = pygame.Surface((L, Hh), pygame.SRCALPHA)
    pygame.surfarray.pixels3d(body)[:] = np.clip(rgb, 0, 255).astype("uint8")
    pygame.surfarray.pixels_alpha(body)[:] = inside * 255

    frames = []
    for sway in (-1, 0, 1):
        s = pygame.Surface((L, Hh), pygame.SRCALPHA)
        dy = sway * L * 0.045
        pygame.draw.polygon(s, fin + (225,), [(x0 + L * 0.06, cy), (2, cy - b * 1.15 + dy),
                                              (L * 0.07, cy + dy * 0.6), (2, cy + b * 1.15 + dy)])
        pygame.draw.polygon(s, fin + (215,), [(L * 0.36, cy - b * 0.8), (L * 0.50, cy - b * 1.5),
                                              (L * 0.66, cy - b * 1.35), (L * 0.78, cy - b * 0.7)])
        pygame.draw.polygon(s, fin + (215,), [(L * 0.36, cy + b * 0.8), (L * 0.44, cy + b * 1.35),
                                              (L * 0.58, cy + b * 0.85)])
        s.blit(body, (0, 0))
        pygame.draw.polygon(s, _mix(fin, belly, 0.4) + (190,), [(L * 0.66, cy + b * 0.15), (L * 0.52, cy + b * 0.75),
                                                              (L * 0.58, cy + b * 0.2)])
        ex, ey = L * 0.88, cy - b * 0.28
        pygame.draw.circle(s, (225, 220, 190), (ex, ey), L * 0.028)
        pygame.draw.circle(s, (8, 10, 12), (ex + 1, ey), L * 0.017)
        frames.append(pygame.transform.smoothscale(s, (L // k, Hh // k)))
    return frames


# =====================================================================
class Scene:
    def __init__(self):
        self.far = self._backdrop()
        self.rays = [self._rays(seed) for seed in (3, 11)]
        self.caustics = self._caustics()
        self.strip = self._seabed()

    # ---------------- built once ----------------
    def _backdrop(self):
        """The distant kelp forest: a real photo, softened and coloured like deep water."""
        fw, fh = int(W + (WORLD_W - W) * FAR) + 2, int(H + (WORLD_H - H) * FAR) + 2
        surf = pygame.Surface((fw, fh))
        photo = _load("kelp_forest.jpg")
        if photo is None:
            for y in range(fh):
                k = y / fh
                pygame.draw.line(surf, (int(20 - 12 * k), int(110 - 70 * k), int(140 - 80 * k)), (0, y), (fw, y))
            return surf
        ph = int(photo.get_height() * fw / photo.get_width())
        surf.blit(pygame.transform.smoothscale(photo, (fw, ph)), (0, -int(ph * 0.105)))
        # distant water is never sharp
        surf = pygame.transform.smoothscale(pygame.transform.smoothscale(surf, (fw // 2, fh // 2)), (fw, fh))
        rgb = pygame.surfarray.pixels3d(surf)
        a = rgb.astype("float32")
        a *= np.array((0.9, 0.82, 0.70), "float32")                    # less swimming-pool, more sea
        a = a * 0.86 + np.array(HAZE, "float32") * 0.34                # haze lifts the black shadows
        rgb[:] = np.clip(a, 0, 255).astype("uint8")
        del rgb
        return surf

    def _rays(self, seed):
        """Slanting shafts of sunlight, added on top of the water."""
        rnd = random.Random(seed)
        w = W + 500
        x, y = np.mgrid[0:w, 0:H].astype("float32")
        u = x + y * 0.45
        v = np.zeros((w, H), "float32")
        for _ in range(8):
            centre, width = rnd.uniform(0, w + H * 0.45), rnd.uniform(30, 130)
            v += rnd.uniform(0.3, 1.0) * np.exp(-((u - centre) / width) ** 2)
        v = np.clip(v * (1 - y / H) ** 1.6, 0, 1.2)
        return pygame.surfarray.make_surface((v[..., None] * np.array((30, 50, 46), "float32")).astype("uint8"))

    def _caustics(self, frames=16, size=256):
        """The net of light that ripples over a shallow seabed. Tiles seamlessly and loops."""
        x, y = np.mgrid[0:size, 0:size].astype("float32") * (2 * math.pi / size)
        out = []
        for f in range(frames):
            t = 2 * math.pi * f / frames
            v = (np.sin(5 * x + 1.5 * np.sin(3 * y + t)) + np.sin(4 * y + 1.5 * np.sin(3 * x - t))
                 + np.sin(3 * (x + y) + 1.3 * np.sin(4 * x - 3 * y + t)) + 0.6 * np.sin(7 * x - 6 * y - 2 * t))
            c = (1 - np.abs(v) / 3.6) ** 9
            # squashed flat, because the seabed is seen from the side
            tile = pygame.surfarray.make_surface((c[..., None] * np.array((52, 60, 44), "float32")).astype("uint8"))
            out.append(pygame.transform.smoothscale(tile, (size, size // 2)))
        return out

    def _rock(self, rnd, texture, w, h):
        """One boulder: photographed rock, lit from above, with a lumpy outline."""
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        if texture is not None:
            tw, th = texture.get_size()
            ox, oy = rnd.randrange(0, tw - 260), rnd.randrange(0, th - 260)
            s.blit(pygame.transform.smoothscale(texture.subsurface((ox, oy, 260, 260)), (w, h)), (0, 0))
        else:
            s.fill((96, 100, 92))
        x, y = np.mgrid[0:w, 0:h].astype("float32")
        px, py = (x - w / 2) / (w / 2), (y - h / 2) / (h / 2)
        ang = np.arctan2(py, px)
        edge = 0.86 + 0.08 * np.sin(3 * ang + rnd.uniform(0, 6)) + 0.05 * np.sin(5 * ang + rnd.uniform(0, 6))
        r = np.sqrt(px * px + py * py) / edge
        light = np.clip(0.78 - py * 0.55, 0.25, 1.25) * np.clip(1.25 - r * 0.55, 0.5, 1)
        rgb = pygame.surfarray.pixels3d(s)
        rgb[:] = np.clip(rgb * light[..., None] * np.array(WATER_TINT, "float32") * 1.05, 0, 255).astype("uint8")
        pygame.surfarray.pixels_alpha(s)[:] = (np.clip((1 - r) * 14, 0, 1) * 255).astype("uint8")
        del rgb
        return s

    def _seabed(self):
        """The whole seabed, drawn once: sand photo, shaded, with rocks and tufts of weed on it."""
        h = WORLD_H - STRIP_TOP
        surf = pygame.Surface((WORLD_W, h), pygame.SRCALPHA)
        sand = _load("coast_sand_03.jpg")
        if sand is not None:
            sand = pygame.transform.smoothscale(sand, (640, 640))
            for tx in range(0, WORLD_W, 640):
                for ty in range(0, h, 640):
                    surf.blit(sand, (tx, ty))
        else:
            surf.fill((128, 120, 100))
        xs = np.arange(WORLD_W, dtype="float32")
        line = config.SEABED_Y + 14 * np.sin(xs * 0.004) + 8 * np.sin(xs * 0.013 + 1.3) - STRIP_TOP
        depth = np.arange(h, dtype="float32")[None, :] - line[:, None]     # pixels below the seabed line
        # brightest along the top where the light lands, patchy like real sand, darker towards the viewer
        patches = 1 + 0.10 * np.sin(xs * 0.021)[:, None] * np.sin(depth * 0.07 + xs[:, None] * 0.006)
        light = np.clip(1.12 - depth / 230, 0.42, 1.12) * patches
        rgb = pygame.surfarray.pixels3d(surf)
        rgb[:] = np.clip(rgb * light[..., None] * np.array(WATER_TINT, "float32"), 0, 255).astype("uint8")
        del rgb
        pygame.surfarray.pixels_alpha(surf)[:] = (np.clip(depth + 1, 0, 1) * 255).astype("uint8")

        rnd = random.Random(6)
        rock = _load("mossy_rock.jpg")
        for i in range(0, WORLD_W, 120):
            x = i + rnd.randrange(0, 90)
            down = rnd.uniform(-0.25, 1) ** 2 * 110                    # how far towards the viewer it lies
            w = int(rnd.uniform(36, 84) * (1 + down / 90))
            hh = int(w * rnd.uniform(0.45, 0.7))
            y = seabed_at(x) - STRIP_TOP + down - hh * 0.62
            shadow = pygame.Surface((w + 20, hh // 2), pygame.SRCALPHA)
            pygame.draw.ellipse(shadow, (0, 10, 14, 90), shadow.get_rect())
            surf.blit(shadow, (x - w / 2 - 10, y + hh * 0.72))
            surf.blit(self._rock(rnd, rock, w, hh), (x - w / 2, y))
        for _ in range(150):                                            # tufts of red and green weed
            x = rnd.uniform(0, WORLD_W)
            y = seabed_at(x) - STRIP_TOP + rnd.uniform(0, 1) ** 2 * 120
            col = rnd.choice(((96, 40, 46), (40, 86, 50), (120, 96, 40), (70, 30, 50)))
            for _ in range(rnd.randrange(4, 9)):
                lean, tall = rnd.uniform(-9, 9), rnd.uniform(7, 20)
                pygame.draw.lines(surf, _mix(col, HAZE, 0.25), False,
                                  [(x, y), (x + lean * 0.4, y - tall * 0.6), (x + lean, y - tall)], 2)
        return surf

    # ---------------- drawn every frame ----------------
    def draw_backdrop(self, scr, camx, camy, tick):
        scr.blit(self.far, (0, 0), (int(camx * FAR), int(camy * FAR), W, H))
        for i, rays in enumerate(self.rays):                            # two sets drifting past each other
            ox = 250 + 230 * math.sin(tick * 0.0035 * (1 + i * 0.7) + i * 2.1)
            scr.blit(rays, (0, 0), (int(ox), 0, W, H), special_flags=pygame.BLEND_RGB_ADD)

    def draw_seabed(self, scr, camx, camy, tick):
        top = STRIP_TOP - camy
        if top >= H:
            return
        top = int(top)
        y0 = max(0, -top)                                               # only the rows that are in view
        y1 = min(self.strip.get_height(), H - top)
        if y1 <= y0:
            return
        view = self.strip.subsurface((int(camx), y0, W, y1 - y0)).copy()
        frame = self.caustics[(tick // 4) % len(self.caustics)]
        for tx in range(-(int(camx) % 256), W, 256):
            for ty in range(-(y0 % 128), view.get_height(), 128):
                view.blit(frame, (tx, ty), special_flags=pygame.BLEND_RGB_ADD)
        scr.blit(view, (0, top + y0))

    def draw_kelp(self, scr, k, camx, camy, tick):
        layer = k["layer"]
        par = (0.6, 1.0, 1.15)[layer]
        x0 = k["x"] - camx * par
        if x0 < -160 or x0 > W + 160:
            return
        base = seabed_at(k["x"]) - camy * (par if layer != 1 else 1)
        scale = (0.7, 1.0, 1.5)[layer]
        if "blades" not in k:                                           # where its blades grow, decided once
            rnd = random.Random(k["x"])
            n = int(k["h"] / (20, 22, 34)[layer])
            k["blades"] = [(0.08 + 0.92 * (i + rnd.random()) / n, rnd.choice((-1, 1)), rnd.uniform(0.6, 1.4),
                            rnd.uniform(0, 6.28), rnd.uniform(-0.5, 0.9)) for i in range(n)]
        stipe, low, high = KELP_COLOURS[layer]

        # the stipe sways and leans with the current
        pts = []
        for s in range(13):
            tt = s / 12
            pts.append((x0 + math.sin(tick * 0.015 + k["ph"] + tt * 2.5) * 32 * tt + 22 * tt * tt * scale,
                        base - tt * k["h"]))
        pygame.draw.lines(scr, stipe, False, pts, (3, 5, 9)[layer])
        for tt, side, size, ph, droop in k["blades"]:
            f = tt * 12
            i = min(11, int(f))
            bx = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * (f - i)
            by = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * (f - i)
            # a long ribbon: it leaves the stipe sideways, sags under its own weight, trails in the
            # current and ripples along its length
            length, wide = 78 * size * scale, 5.5 * size * scale
            sway = math.sin(tick * 0.02 + ph)
            dx, dy = side * 0.8 + 0.35 + 0.12 * sway, droop * 0.5
            upper, lower = [], []
            for j in range(7):
                s = j / 6
                x = bx + dx * length * s + math.sin(s * 5 + tick * 0.03 + ph) * 3 * s * scale
                y = by + (dy * s + 0.55 * s * s) * length + math.cos(s * 4 + tick * 0.025 + ph) * 2.5 * s * scale
                w = wide * math.sin(math.pi * min(1, s * 0.9 + 0.1)) ** 0.6
                upper.append((x, y - w))
                lower.append((x, y + w))
            col = _mix(low, high, min(1, tt * 0.8 + size * 0.15))
            pygame.draw.polygon(scr, col, upper + lower[::-1])
            if layer == 1:                                              # close enough to see the lit edge
                pygame.draw.lines(scr, _mix(col, (236, 214, 120), 0.35), False, upper, 1)
            pygame.draw.circle(scr, _mix(col, high, 0.5), (bx + dx * 4 * scale, by + 1), 2.4 * scale)
