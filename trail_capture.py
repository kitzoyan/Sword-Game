"""Capture sword-trail frames for each attack type from a fixed inspection camera.

Drives the player through each swing and screenshots a couple of mid-active frames
so the trail ribbon is visible and can be eyeballed for 'does it arc with the blade'.
Usage: python trail_capture.py <tag>   ->  writes trail_<tag>_<name>.png
"""
import os, sys
from ursina import Ursina, window, Vec3, camera
from panda3d.core import Filename
import main, constants as c
from constants import State, AttackType

tag = sys.argv[1] if len(sys.argv) > 1 else 'x'
app = Ursina(title="riposte-trail", development_mode=False)
window.color = c.WINDOW_BG
main.app = app
main.build_environment(); main.build_hud(); main.spawn_fighters()
p, e = main.player, main.enemy
p.is_player = True; e.is_player = True
# Park the enemy far away and make the player face +z so swings sweep in front.
e.body.position = Vec3(0, 0, 40)
p.body.position = Vec3(0, 0, 0)
DT = 1.0 / 60.0

def face_z():
    p._forward = Vec3(0, 0, 1)
    p.rotation_y = 0.0

def sim(n=1):
    for _ in range(n):
        p.update_fighter(DT, None)
        face_z()
        main.world.step(DT)

def render_shot(name):
    # Fixed 3/4 inspection camera looking at the player's chest, in front-right.
    camera.world_position = Vec3(4.2, 2.6, 3.6)
    camera.look_at(Vec3(0, 1.1, 0.6))
    tm = app.taskMgr
    for _ in range(4):
        tm.step()
    out = os.path.abspath(f'trail_{tag}_{name}.png')
    app.win.saveScreenshot(Filename.from_os_specific(out))
    seg = len(p.sword_trail._segments)
    print(f"  {name}: state={p.state.name} segs={seg}")

def settle():
    p._enter_idle(); p.stamina = c.MAX_STAMINA
    p.body.position = Vec3(0, 0, 0); p.body.velocity = Vec3(0, 0, 0)
    face_z(); sim(2)

def do_ground(atype, name):
    settle()
    assert p.start_attack(atype), f"{name} start failed"
    # Step to first ACTIVE frame, capture across the swing.
    guard = 0
    while p.state != State.ATTACK_ACTIVE and guard < 120:
        sim(1); guard += 1
    sim(2); render_shot(name + '_a')          # early arc
    while p.state != State.ATTACK_ACTIVE2 and guard < 240:
        sim(1); guard += 1
    sim(1); render_shot(name + '_b')          # late arc

# Two light directions (they alternate), heavy, charge.
do_ground(AttackType.LIGHT, 'light1')
do_ground(AttackType.LIGHT, 'light2')
do_ground(AttackType.HEAVY, 'heavy')
do_ground(AttackType.CHARGE, 'charge')

# Aerial: jump then plunge.
settle()
assert p.jump()
sim(6)
assert p.start_attack(AttackType.AERIAL)
g = 0
while p.state != State.ATTACK_ACTIVE2 and g < 120:
    sim(1); g += 1
sim(2); render_shot('aerial')

print(f"TRAIL CAPTURE DONE (tag={tag})")
try: app.userExit()
except Exception: pass
