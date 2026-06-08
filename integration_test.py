"""
Headless integration harness: drives the REAL physics+combat+fighter stack
through ~10s of AI-vs-AI combat without calling app.run() (no blocking window).
Verifies the modules actually interlock: damage lands, bodies stay in-arena,
states cycle, no exceptions.
"""
import math
from ursina import Ursina, Vec3, window
import constants as c
import physics
import fighter

# Create the real engine (needed for Entity visuals); we won't call app.run().
window.borderless = False
app = Ursina(title="riposte-integration", development_mode=False)

world = physics.PhysicsWorld()
# Both AI so combat is guaranteed without keyboard input. Mixed difficulty so the
# run exercises both profiles (and feints, which HIGH throws more of).
a = fighter.Fighter(world, Vec3(-1.8, 0, 0), team=0, color=c.PLAYER_COLOR, is_player=False)
b = fighter.Fighter(world, Vec3(1.8, 0, 0), team=1, color=c.ENEMY_COLOR, is_player=False)
a.difficulty = c.Difficulty.MEDIUM
b.difficulty = c.Difficulty.HIGH

dt = 1.0 / 60.0
FRAMES = 600  # ~10 seconds

states_seen = set()
hit_landed = False
max_dist_from_center = 0.0
err = None
# Count feints by watching the feint cooldown arm (0 -> >0), which fires on every
# feint (and on a caught-mid-feint guard-break). Confirms feints occur in real play.
feints = 0
_prev_fc = {id(a): 0.0, id(b): 0.0}

start_hp = (a.hp, b.hp)
try:
    for i in range(FRAMES):
        a.update_fighter(dt, b)
        b.update_fighter(dt, a)
        world.step(dt)

        for f in (a, b):
            if _prev_fc[id(f)] <= 0.0 < f.feint_cooldown:
                feints += 1
            _prev_fc[id(f)] = f.feint_cooldown
            states_seen.add(f.state)
            p = f.body.position
            # sanity: finite numbers
            for comp in (p.x, p.y, p.z):
                assert math.isfinite(comp), f"non-finite position {p}"
            d = math.hypot(p.x, p.z)
            max_dist_from_center = max(max_dist_from_center, d)
            assert d <= c.ARENA_RADIUS + 0.5, f"escaped arena: dist={d}"
            assert -1.0 <= f.hp <= c.MAX_HP + 0.01, f"hp out of range: {f.hp}"
            assert 0.0 <= f.stamina <= c.MAX_STAMINA + 0.01, f"stamina oob: {f.stamina}"

        if a.hp < start_hp[0] or b.hp < start_hp[1]:
            hit_landed = True
        if a.hp <= 0 or b.hp <= 0:
            break
except Exception as e:
    import traceback
    err = traceback.format_exc()

print("=" * 60)
if err:
    print("INTEGRATION FAILED:\n" + err)
else:
    print("INTEGRATION OK")
print(f"  frames run        : {i+1}")
print(f"  fighter A hp/stam : {a.hp:.1f} / {a.stamina:.1f}")
print(f"  fighter B hp/stam : {b.hp:.1f} / {b.stamina:.1f}")
print(f"  damage landed     : {hit_landed}")
print(f"  feints thrown     : {feints}")
print(f"  max dist center   : {max_dist_from_center:.2f} (arena {c.ARENA_RADIUS})")
print(f"  distinct states   : {sorted(s.name for s in states_seen)}")
print("=" * 60)

# Tear down without blocking.
try:
    app.userExit()
except Exception:
    pass
