"""Run real AI-vs-AI combat with the game's own follow-camera and screenshot live
swings, so the sword trails can be eyeballed as they actually appear in play.

Usage: python game_trail_capture.py <tag> [seed]
Captures up to MAXSHOTS frames where a fighter is mid-active-swing, spaced out.
"""
import os, sys, random
from ursina import Ursina, window, Vec3
from panda3d.core import Filename
import main, constants as c
from constants import State

tag = sys.argv[1] if len(sys.argv) > 1 else 'g'
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
random.seed(seed)

app = Ursina(title="riposte-gtrail", development_mode=False)
window.color = c.WINDOW_BG
main.app = app
main.build_environment(); main.build_hud(); main.spawn_fighters()
p, e = main.player, main.enemy
# Both AI so swings happen on their own; HIGH difficulty for lots of action.
p.is_player = False; e.is_player = False
p.difficulty = c.Difficulty.HIGH; e.difficulty = c.Difficulty.HIGH
DT = 1.0 / 60.0
ACTIVE = (State.ATTACK_ACTIVE, State.ATTACK_ACTIVE2)

def render(name):
    tm = app.taskMgr
    for _ in range(3):
        tm.step()
    out = os.path.abspath(f'gtrail_{tag}_{name}.png')
    app.win.saveScreenshot(Filename.from_os_specific(out))

MAXSHOTS = 6
shots = 0
cooldown = 0
seg_peak = 0
for i in range(1400):
    p.update_fighter(DT, e); e.update_fighter(DT, p)
    main.world.step(DT); main.update_camera(DT)
    seg_peak = max(seg_peak, len(p.sword_trail._segments), len(e.sword_trail._segments))
    if p.hp <= 0 or e.hp <= 0:
        # respawn to keep the action going
        main.spawn_fighters(); p, e = main.player, main.enemy
        p.is_player = False; e.is_player = False
        p.difficulty = c.Difficulty.HIGH; e.difficulty = c.Difficulty.HIGH
        continue
    cooldown = max(0, cooldown - 1)
    # Capture when a fighter is a few frames into a swing (so the ribbon exists).
    if cooldown == 0 and shots < MAXSHOTS:
        for f in (p, e):
            n = len(f.sword_trail._segments)
            if f.state in ACTIVE and n >= 3:
                render(f"{shots:02d}_{f.team}_segs{n}")
                shots += 1
                cooldown = 40
                break
    if shots >= MAXSHOTS:
        break

print(f"GAME TRAIL CAPTURE done tag={tag} shots={shots} seg_peak={seg_peak}")
try: app.userExit()
except Exception: pass
