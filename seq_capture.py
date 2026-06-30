"""Capture a single swing as a numbered frame sequence from a fixed front camera,
so the blade's sweep DIRECTION is unambiguous. Usage: python seq_capture.py <tag>"""
import os, sys
from ursina import Ursina, window, Vec3, camera
from panda3d.core import Filename
import main, constants as c
from constants import State, AttackType

tag = sys.argv[1] if len(sys.argv) > 1 else 'x'
app = Ursina(title="riposte-seq", development_mode=False)
window.color = c.WINDOW_BG
main.app = app
main.build_environment(); main.build_hud(); main.spawn_fighters()
p, e = main.player, main.enemy
p.is_player = True; e.is_player = True
e.body.position = Vec3(0, 0, 40)
p.body.position = Vec3(0, 0, 0)
DT = 1.0 / 60.0

def sim(n=1):
    for _ in range(n):
        p.update_fighter(DT, None)
        p._forward = Vec3(0, 0, 1); p.rotation_y = 0.0
        main.world.step(DT)

def shot(name):
    # Camera in FRONT of the player (player faces +z toward cam), slightly above.
    camera.world_position = Vec3(0.0, 2.2, 6.5)
    camera.look_at(Vec3(0, 1.2, 0.6))
    tm = app.taskMgr
    for _ in range(3): tm.step()
    app.win.saveScreenshot(Filename.from_os_specific(os.path.abspath(f'seq_{tag}_{name}.png')))
    sw = p.sword.rotation
    print(f"  {name}: {p.state.name:14s} sword.rot=({sw.x:.0f},{sw.y:.0f},{sw.z:.0f})")

def settle():
    p._enter_idle(); p.stamina = c.MAX_STAMINA
    p.body.position = Vec3(0, 0, 0); p.body.velocity = Vec3(0, 0, 0)
    p._forward = Vec3(0, 0, 1); p.rotation_y = 0.0
    sim(2)

def capture_swing(atype, label):
    settle()
    p.start_attack(atype)
    idx = 0
    last = None
    for _ in range(80):
        sim(1)
        st = p.state
        if st in (State.ATTACK_WINDUP, State.ATTACK_ACTIVE, State.ATTACK_ACTIVE2,
                  State.ATTACK_RECOVERY):
            shot(f"{label}_{idx:02d}")
            idx += 1
        if st in (State.IDLE, State.MOVING) and idx > 0:
            break

capture_swing(AttackType.LIGHT, 'light1')
capture_swing(AttackType.HEAVY, 'heavy')
print(f"SEQ DONE tag={tag}")
try: app.userExit()
except Exception: pass
