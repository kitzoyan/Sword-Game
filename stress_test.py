"""Long AI-vs-AI stress run to catch soft-locks / exceptions with jumping active.

Runs many rounds (respawning on KO), tracking per-fighter state dwell time. Flags
any fighter that stays airborne or in one non-idle state implausibly long (a likely
soft-lock), and any exception / NaN / arena escape.
"""
import math
from ursina import Ursina, Vec3, window
import constants as c
import physics
import fighter
from constants import State

window.borderless = False
app = Ursina(title="riposte-stress", development_mode=False)

DT = 1.0 / 60.0
ROUNDS = 6
FRAMES_PER_ROUND = 1200  # 20s each

worst_air = 0.0
worst_state_dwell = {}
err = None
states_seen = set()
jumps = 0

try:
    for rnd in range(ROUNDS):
        world = physics.PhysicsWorld()
        a = fighter.Fighter(world, Vec3(-2.5, 0, 0), team=0, color=c.PLAYER_COLOR, is_player=False)
        b = fighter.Fighter(world, Vec3(2.5, 0, 0), team=1, color=c.ENEMY_COLOR, is_player=False)
        a.difficulty = c.Difficulty.HIGH
        b.difficulty = c.Difficulty.HIGH
        air_time = {id(a): 0.0, id(b): 0.0}
        dwell = {id(a): [None, 0.0], id(b): [None, 0.0]}
        prev_state = {id(a): None, id(b): None}
        for i in range(FRAMES_PER_ROUND):
            a.update_fighter(DT, b)
            b.update_fighter(DT, a)
            world.step(DT)
            for f in (a, b):
                states_seen.add(f.state)
                if f.state == State.JUMPING and prev_state[id(f)] != State.JUMPING:
                    jumps += 1
                prev_state[id(f)] = f.state
                # airborne dwell
                if getattr(f, '_airborne', False) or f.body.position.y > 0.05:
                    air_time[id(f)] += DT
                else:
                    worst_air = max(worst_air, air_time[id(f)])
                    air_time[id(f)] = 0.0
                # per-state dwell (excluding the resting/locomotion states)
                d = dwell[id(f)]
                if f.state == d[0]:
                    d[1] += DT
                else:
                    if d[0] not in (None, State.IDLE, State.MOVING, State.DEAD):
                        worst_state_dwell[d[0]] = max(worst_state_dwell.get(d[0], 0.0), d[1])
                    d[0], d[1] = f.state, 0.0
                p = f.body.position
                for comp in (p.x, p.y, p.z):
                    assert math.isfinite(comp), f"non-finite pos {p}"
                assert math.hypot(p.x, p.z) <= c.ARENA_RADIUS + 0.6, f"escaped arena {p}"
                assert p.y >= -0.01, f"fell through floor: y={p.y}"
                assert -1.0 <= f.hp <= c.MAX_HP + 0.01
                assert 0.0 <= f.stamina <= c.MAX_STAMINA + 0.01
            if a.hp <= 0 or b.hp <= 0:
                break
except Exception as e:
    import traceback
    err = traceback.format_exc()

print("=" * 60)
print("STRESS FAILED:\n" + err if err else "STRESS OK")
print(f"  rounds run         : {ROUNDS}")
print(f"  jumps initiated    : {jumps}")
print(f"  worst airborne time: {worst_air:.2f}s  (a long jump+fall is ~1.0s)")
print(f"  worst non-idle dwell per state:")
for s, t in sorted(worst_state_dwell.items(), key=lambda kv: -kv[1]):
    print(f"    {s.name:16s} {t:.2f}s")
print(f"  states seen        : {sorted(s.name for s in states_seen)}")
print("=" * 60)
try:
    app.userExit()
except Exception:
    pass
