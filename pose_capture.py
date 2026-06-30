"""Capture specific animation poses for visual review."""
import os
from ursina import Ursina, window, Vec3
from panda3d.core import Filename
import main, constants as c
from constants import State, AttackType

app = Ursina(title="riposte-pose", development_mode=False)
window.color = c.WINDOW_BG
main.app = app
main.build_environment(); main.build_hud(); main.spawn_fighters()
p, e = main.player, main.enemy
p.is_player = True; e.is_player = True
DT = 1/60

def step(n=1):
    for _ in range(n):
        p.update_fighter(DT, e); e.update_fighter(DT, p)
        main.world.step(DT); main.update_camera(DT)

def shot(name):
    tm = app.taskMgr
    for _ in range(6): tm.step()
    app.win.saveScreenshot(Filename.from_os_specific(os.path.abspath(name)))
    print(f"  {name}: p={p.state.name}")

step(40)
# Light attack mid-swing (active2 = the follow-through).
p.start_attack(AttackType.LIGHT)
step(int((c.ATTACKS[AttackType.LIGHT].windup + c.ATTACKS[AttackType.LIGHT].active + 0.04) / DT))
shot('shot_light.png')
step(40)
# Landing crouch: jump, then sample the LANDING frame.
p.jump()
for i in range(150):
    step(1)
    if p.state == State.LANDING:
        shot('shot_landing.png'); break
print("POSES CAPTURED")
try: app.userExit()
except Exception: pass
