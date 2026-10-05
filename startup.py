"""
Which way to run: on this screen, or as a phone VR headset.

  python3 main.py              asks
  python3 main.py --vr         phone VR mode
  python3 main.py --standard   standard screen mode
"""
import sys

import pygame

import config


def choose():
    """-> "vr" or "standard". Has to run before the game is imported: the view size depends on it."""
    if "--vr" in sys.argv:
        return "vr"
    if "--standard" in sys.argv:
        return "standard"
    pygame.init()
    screen = pygame.display.set_mode((720, 420))
    pygame.display.set_caption("ROV-6 · ORCA")
    big = pygame.font.SysFont("menlo,monaco,consolas,couriernew", 26, bold=True)
    font = pygame.font.SysFont("menlo,monaco,consolas,couriernew", 18, bold=True)
    small = pygame.font.SysFont("menlo,monaco,consolas,couriernew", 12)
    buttons = [("vr", "PHONE VR MODE", "phone in a cardboard viewer: head, hand and voice  [1]", pygame.Rect(110, 140, 500, 76)),
               ("standard", "STANDARD SCREEN MODE", "this screen: eyes, hand and voice  [2]", pygame.Rect(110, 240, 500, 76))]
    clock = pygame.time.Clock()
    while True:
        mouse = pygame.mouse.get_pos()
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                pygame.quit()
                sys.exit()
            if e.type == pygame.KEYDOWN and e.key in (pygame.K_1, pygame.K_2):
                return buttons[e.key - pygame.K_1][0]
            if e.type == pygame.MOUSEBUTTONDOWN:
                for mode, _, _, rect in buttons:
                    if rect.collidepoint(e.pos):
                        return mode
        screen.fill((4, 26, 40))
        title = big.render("UNDERWATER TELEPRESENCE", True, (120, 220, 255))
        screen.blit(title, (360 - title.get_width() / 2, 62))
        for _, name, hint, rect in buttons:
            over = rect.collidepoint(mouse)
            pygame.draw.rect(screen, (0, 60, 84) if over else (0, 36, 52), rect, border_radius=8)
            pygame.draw.rect(screen, (120, 220, 255) if over else (60, 120, 150), rect, 2, border_radius=8)
            label = font.render(f"[ {name} ]", True, (230, 240, 245))
            screen.blit(label, (rect.centerx - label.get_width() / 2, rect.y + 16))
            sub = small.render(hint, True, (140, 170, 185))
            screen.blit(sub, (rect.centerx - sub.get_width() / 2, rect.y + 48))
        pygame.display.flip()
        clock.tick(30)
