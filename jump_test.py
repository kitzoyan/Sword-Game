"""Headless verification of jump + aerial mechanics, plus animation screenshots.

Drives the real fighter/physics/combat stack (no app.run()): exercises a jump
arc, an aerial plunge onto a grounded target, and the new walk cycle. Asserts the
state machine + height-aware combat behave, and saves a few PNGs to eyeball poses.
"""
import os
from ursina import Ursina, window, Vec3, camera
from panda3d.core import Filename
import main
import constants as c
from constants import State, AttackType

app = Ursina(title="riposte-jump", borderless=False, development_mode=False)
window.color = c.WINDOW_BG
main.app = app
main.build_environment()
main.build_hud()
main.spawn_fighters()
p, e = main.player, main.enemy
# Treat both as "player" so the AI never fires; we drive p by hand.
p.is_player = True
e.is_player = True

DT = 1.0 / 60.0


def step(n=1):
    for _ in range(n):
        # Full real update path (handle_input is a no-op with no keys held), so
        # lock-on facing, timers, state machine, visuals all run as in-game.
        p.update_fighter(DT, e)
        e.update_fighter(DT, p)
        main.world.step(DT)
        main.update_camera(DT)


def shot(name):
    tm = getattr(app, 'taskMgr', None) or __import__('builtins').base.taskMgr
    for _ in range(6):
        tm.step()
    out = os.path.abspath(name)
    win = getattr(app, 'win', None) or __import__('builtins').base.win
    win.saveScreenshot(Filename.from_os_specific(out))
    print(f"  shot {name}: p.state={p.state.name} y={p.body.position.y:.2f}")


# Settle.
step(40)
print("settled: p.state =", p.state.name, "on_ground =", p.body.on_ground)

# ---- Jump arc ----
assert p.jump(), "jump() should succeed from idle on the ground"
assert p.state == State.JUMPING
max_y = 0.0
landed = False
for i in range(150):
    step(1)
    max_y = max(max_y, p.body.position.y)
    if i == 18:
        shot('shot_jump_apex.png')
    if p.state in (State.LANDING, State.IDLE) and i > 5:
        landed = True
        print(f"  landed after {i} steps, apex y={max_y:.2f}, state={p.state.name}")
        break
assert max_y > 1.5, f"jump should clear >1.5u, got apex {max_y:.2f}"
assert landed, "fighter should land back to LANDING/IDLE"

# ---- Aerial plunge onto the grounded enemy ----
step(20)  # let stamina/idle settle
# Move player above/near the enemy: place just in range.
p.body.position = Vec3(e.body.position.x - 1.6, 0.0, e.body.position.z)
p.body.velocity = Vec3(0, 0, 0)
step(3)
assert p.jump(), "jump before aerial"
step(10)   # rise a bit
hp_before = e.hp
threw = p.start_attack(AttackType.AERIAL)
print("  aerial thrown midair:", threw, "state:", p.state.name)
assert threw, "aerial should be allowed while JUMPING"
for i in range(120):
    step(1)
    if i == 6:
        shot('shot_aerial.png')
    if p.state in (State.IDLE, State.LANDING) and i > 5:
        break
print(f"  enemy hp {hp_before:.0f} -> {e.hp:.0f} (aerial {'CONNECTED' if e.hp < hp_before else 'whiffed'})")

# ---- Ground attack should WHIFF a jumped-over target (height-aware combat) ----
# (Direct resolve_hit calls don't run lock-on, so pin facing toward +z manually.)
import combat
p._forward = Vec3(0, 0, 1)
e.invulnerable = False; e.is_blocking = False; e.parry_active = False
p.body.position = Vec3(0, 0, 0); p.body.velocity = Vec3(0,0,0)
e.body.position = Vec3(0, 2.6, 1.5)  # enemy "in the air" right in front
res = combat.resolve_hit(p, e, c.ATTACKS[AttackType.LIGHT])
print("  ground light vs airborne target ->", res.name, "(expect MISSED)")
assert res == combat.HitResult.MISSED, "a jumped-over target must be out of vertical reach"
e.body.position = Vec3(0, 0.0, 1.5)
res2 = combat.resolve_hit(p, e, c.ATTACKS[AttackType.LIGHT])
print("  ground light vs grounded target ->", res2.name, "(expect HIT)")
assert res2 == combat.HitResult.HIT

# ---- Regression: an aerial interrupted/parried mid-air must NOT become an
#      actionable mid-air IDLE; it must stay airborne and land with lag. ----
import combat
p.body.position = Vec3(0, 0, 0); p.body.velocity = Vec3(0, 0, 0)
e.body.position = Vec3(0, 0, 1.5); e.body.velocity = Vec3(0, 0, 0)
step(20)
assert p.jump()
step(8)                      # rise toward apex
assert p.start_attack(AttackType.AERIAL)
step(2)                      # into the aerial windup, still high up
# Simulate a clean hit landing on the airborne attacker mid-windup.
y_before = p.body.position.y
assert y_before > 1.0, f"setup: attacker should be airborne, y={y_before:.2f}"
p.take_damage(5.0, None, 0.0)
print(f"  after mid-air clean hit: state={p.state.name} (expect JUMPING, not IDLE)")
assert p.state == State.JUMPING, f"interrupted aerial must stay airborne, got {p.state.name}"
assert p._airborne, "airborne flag must persist so landing detection still fires"
# Now let it fall and confirm it lands properly (LANDING or IDLE on the ground).
saw_landing = False
for i in range(120):
    step(1)
    if p.state == State.LANDING:
        saw_landing = True
    if p.body.on_ground and p.state in (State.IDLE, State.LANDING) and i > 3:
        break
assert abs(p.body.position.y) < 0.05, f"must end grounded, y={p.body.position.y:.2f}"
print(f"  recovered to grounded {p.state.name}; saw LANDING lag = {saw_landing}")
assert saw_landing, "an interrupted aerial must still pay landing lag on touchdown"
# Air-dodge must be impossible.
p.body.position = Vec3(0, 0, 0); p.body.velocity = Vec3(0, 0, 0); step(20)
assert p.jump()
step(8)
assert not p.dodge(Vec3(1, 0, 0)), "dodge() must be refused while airborne"
print("  air-dodge correctly refused")
step(80)  # let it land

# ---- Regression: an aerial must NOT be feintable (committed dive). ----
p.body.position = Vec3(0, 0, 0); p.body.velocity = Vec3(0, 0, 0); step(20)
assert p.jump(); step(6)
assert p.start_attack(AttackType.AERIAL)
step(1)
assert not p.feint(), "an aerial plunge must not be feintable"
assert not p.feint_pending, "feinting an aerial must not arm feint_pending"
print("  aerial-feint correctly rejected")
step(120)  # let it resolve + land

# ---- Walk-cycle screenshot ----
p.body.position = Vec3(-3, 0, 0); p.body.velocity = Vec3(c.MOVE_SPEED, 0, 0)
e.body.position = Vec3(3, 0, 0)
for i in range(20):
    p._tick_timers(DT)
    p.body.velocity = Vec3(c.MOVE_SPEED, p.body.velocity.y, 0)
    p.state = State.MOVING
    p.world_position = p.body.position
    p._update_sword_visual(DT); p._update_body_visual()
    main.update_camera(DT)
shot('shot_walk.png')

print("ALL JUMP/AERIAL CHECKS PASSED")
try:
    app.userExit()
except Exception:
    pass
