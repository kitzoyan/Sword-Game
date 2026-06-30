"""Pure combat resolution for Riposte. See constants.py for the interface contract."""

import math

from ursina import Vec3

import constants
from constants import (
    ATTACKS,
    AttackType,
    BLOCK_CHIP,
    BLOCK_DAMAGE_MULT,
    BLOCK_HEAVY_STAGGER_TIME,
    HitResult,
    PARRY_STAGGER_TIME,
    RIPOSTE_DAMAGE_MULT,
    State,
)


def _xz(v):
    return (v.x, v.z)


def in_attack_arc(att_pos, att_forward, target_pos, rng, arc_deg):
    dx = target_pos.x - att_pos.x
    dz = target_pos.z - att_pos.z
    dist = math.hypot(dx, dz)
    if dist > rng:
        return False
    if dist <= 1e-6:
        return True
    fx, fz = att_forward.x, att_forward.z
    fmag = math.hypot(fx, fz)
    if fmag <= 1e-6:
        return False
    dot = (fx * dx + fz * dz) / (fmag * dist)
    dot = max(-1.0, min(1.0, dot))
    angle = math.degrees(math.acos(dot))
    return angle <= arc_deg * 0.5


def _horizontal_unit(from_pos, to_pos):
    dx = to_pos.x - from_pos.x
    dz = to_pos.z - from_pos.z
    mag = math.hypot(dx, dz)
    if mag <= 1e-6:
        return Vec3(0, 0, 0)
    return Vec3(dx / mag, 0.0, dz / mag)


def resolve_hit(attacker, target, attack):
    if not in_attack_arc(attacker.position, attacker.forward,
                        target.position, attack.range, attack.arc_deg):
        return HitResult.MISSED

    # Vertical reach: the arc test is purely xz, so without this a grounded swing
    # would still connect with a target who has jumped clear above it. A ground
    # attack reaches only attack.vertical_reach in |dy| (a well-timed jump floats
    # over it); the aerial plunge carries a big downward reach so a dive lands on a
    # grounded foe. Feet-to-feet dy (position.y is the feet height).
    if abs(target.position.y - attacker.position.y) > attack.vertical_reach:
        return HitResult.MISSED

    if target.invulnerable:
        # Successive dodge: a clean dodge frees the dodger to act immediately
        # (cancels dodge end-lag). The dodger decides what that reward means.
        target.on_dodge_success()
        return HitResult.DODGED

    direction = _horizontal_unit(attacker.position, target.position)
    if direction.x == 0.0 and direction.z == 0.0:
        # Stacked exactly (e.g. an aerial plunge landing dead-overhead): fall back to
        # the attacker's facing so the hit still imparts knockback in a sane direction.
        f = attacker.forward
        direction = Vec3(f.x, 0.0, f.z)

    if target.parry_active:
        target.on_parry_success(attack)
        attacker.on_staggered()
        return HitResult.PARRIED

    if target.is_blocking:
        blocked_damage = attack.damage * BLOCK_DAMAGE_MULT
        chip = attack.damage * BLOCK_CHIP
        total = blocked_damage + chip
        knockback_vec = direction * (attack.knockback * 0.4)
        # Heavy-type attacks (HEAVY, the CHARGE dash, and the AERIAL plunge)
        # guard-break: long stagger. Blocking a light is safe.
        breaks_guard = attack.atype in (
            AttackType.HEAVY, AttackType.CHARGE, AttackType.AERIAL)
        stagger = BLOCK_HEAVY_STAGGER_TIME if breaks_guard else 0.0
        target.take_damage(total, knockback_vec, stagger)
        # Breaking a guard refunds the attacker the attack's stamina cost.
        if breaks_guard:
            attacker.on_guard_break(attack)
        return HitResult.BLOCKED

    damage = attack.damage
    if attacker.riposte_ready:
        damage *= RIPOSTE_DAMAGE_MULT
    knockback_vec = direction * attack.knockback
    # A clean unguarded hit never staggers -- heavies trade their old stagger for
    # big damage; you stay free to act. Only parries and blocked heavies stagger.
    target.take_damage(damage, knockback_vec, 0.0)
    return HitResult.HIT


# --------------------------------------------------------------------------- #
#  Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    class FakeCombatant:
        def __init__(self, pos=(0, 0, 0), fwd=(0, 0, 1)):
            self.position = Vec3(*pos)
            self.forward = Vec3(*fwd)
            self.state = State.IDLE
            self.stamina = 100.0
            self.invulnerable = False
            self.is_blocking = False
            self.parry_active = False
            self.riposte_ready = False
            self.damage_calls = []
            self.parry_success_calls = 0
            self.staggered_calls = 0
            self.dodge_success_calls = 0
            self.guard_break_calls = 0

        def take_damage(self, amount, knockback_vec, stagger_time):
            self.damage_calls.append((amount, knockback_vec, stagger_time))

        def on_parry_success(self, attack=None):
            self.parry_success_calls += 1

        def on_staggered(self):
            self.staggered_calls += 1

        def on_dodge_success(self):
            self.dodge_success_calls += 1

        def on_guard_break(self, attack):
            self.guard_break_calls += 1

    light = ATTACKS[AttackType.LIGHT]
    heavy = ATTACKS[AttackType.HEAVY]

    # 1) MISSED: target out of arc (behind attacker)
    a = FakeCombatant(pos=(0, 0, 0), fwd=(0, 0, 1))
    t = FakeCombatant(pos=(0, 0, -2.0), fwd=(0, 0, -1))
    result = resolve_hit(a, t, light)
    assert result is HitResult.MISSED, f"expected MISSED got {result}"
    assert t.damage_calls == [] and t.parry_success_calls == 0 and a.staggered_calls == 0
    print("PASS MISSED (target behind attacker)")

    # 1b) MISSED: target out of range
    a = FakeCombatant(pos=(0, 0, 0), fwd=(0, 0, 1))
    t = FakeCombatant(pos=(0, 0, 5.0))
    result = resolve_hit(a, t, light)
    assert result is HitResult.MISSED
    assert t.damage_calls == []
    print("PASS MISSED (out of range)")

    # 2) DODGED: target invulnerable (i-frames)
    a = FakeCombatant(pos=(0, 0, 0), fwd=(0, 0, 1))
    t = FakeCombatant(pos=(0, 0, 1.5))
    t.invulnerable = True
    result = resolve_hit(a, t, light)
    assert result is HitResult.DODGED, f"expected DODGED got {result}"
    assert t.damage_calls == []
    assert t.parry_success_calls == 0
    assert a.staggered_calls == 0
    assert t.dodge_success_calls == 1, "successive dodge must notify the dodger"
    print("PASS DODGED (successive-dodge callback fired)")

    # 3) PARRIED: target has parry_active
    a = FakeCombatant(pos=(0, 0, 0), fwd=(0, 0, 1))
    t = FakeCombatant(pos=(0, 0, 1.5))
    t.parry_active = True
    result = resolve_hit(a, t, heavy)
    assert result is HitResult.PARRIED, f"expected PARRIED got {result}"
    assert t.damage_calls == [], "parry should not deal damage to target"
    assert t.parry_success_calls == 1, "on_parry_success must fire on defender"
    assert a.staggered_calls == 1, "on_staggered must fire on attacker"
    print("PASS PARRIED (defender parried, attacker staggered, no damage)")

    # 4) BLOCKED light: target is blocking -- safe, no stagger
    a = FakeCombatant(pos=(0, 0, 0), fwd=(0, 0, 1))
    t = FakeCombatant(pos=(0, 0, 1.5))
    t.is_blocking = True
    result = resolve_hit(a, t, light)
    assert result is HitResult.BLOCKED, f"expected BLOCKED got {result}"
    assert len(t.damage_calls) == 1
    amount, kb, stagger = t.damage_calls[0]
    expected = light.damage * BLOCK_DAMAGE_MULT + light.damage * BLOCK_CHIP
    assert abs(amount - expected) < 1e-6, f"blocked damage {amount} != {expected}"
    assert kb.y == 0.0
    assert kb.z > 0, "knockback should push target away from attacker (+z)"
    assert stagger == 0.0, "blocking a light must not stagger"
    assert a.guard_break_calls == 0, "a blocked light is not a guard break"
    print(f"PASS BLOCKED light (damage={amount:.2f}, kb={kb}, no stagger, no guard-break)")

    # 4b) BLOCKED heavy: blocking a heavy guard-breaks -> long stagger
    a = FakeCombatant(pos=(0, 0, 0), fwd=(0, 0, 1))
    t = FakeCombatant(pos=(0, 0, 1.5))
    t.is_blocking = True
    result = resolve_hit(a, t, heavy)
    assert result is HitResult.BLOCKED, f"expected BLOCKED got {result}"
    amount, kb, stagger = t.damage_calls[0]
    assert abs(stagger - BLOCK_HEAVY_STAGGER_TIME) < 1e-6, f"heavy-block stagger {stagger}"
    assert stagger > PARRY_STAGGER_TIME, "heavy-block stagger must exceed parry stagger"
    assert a.guard_break_calls == 1, "blocked heavy must refund the attacker (guard break)"
    print(f"PASS BLOCKED heavy (stagger={stagger:.2f} > parry {PARRY_STAGGER_TIME}, guard-break refund fired)")

    # 4c) BLOCKED charge: a charge is heavy-type -> guard-breaks (stagger + refund)
    a = FakeCombatant(pos=(0, 0, 0), fwd=(0, 0, 1))
    t = FakeCombatant(pos=(0, 0, 1.5))
    t.is_blocking = True
    charge = ATTACKS[AttackType.CHARGE]
    result = resolve_hit(a, t, charge)
    assert result is HitResult.BLOCKED, f"expected BLOCKED got {result}"
    amount, kb, stagger = t.damage_calls[0]
    assert abs(stagger - BLOCK_HEAVY_STAGGER_TIME) < 1e-6, f"charge-block stagger {stagger}"
    assert a.guard_break_calls == 1, "blocked charge must guard-break (refund attacker)"
    print(f"PASS BLOCKED charge (stagger={stagger:.2f}, guard-break refund fired)")

    # 4d) HIT charge: clean charge hit deals light damage, no stagger
    a = FakeCombatant(pos=(0, 0, 0), fwd=(0, 0, 1))
    t = FakeCombatant(pos=(0, 0, 1.5))
    result = resolve_hit(a, t, charge)
    assert result is HitResult.HIT, f"expected HIT got {result}"
    amount, kb, stagger = t.damage_calls[0]
    assert abs(amount - charge.damage) < 1e-6, f"charge damage {amount} != {charge.damage}"
    assert charge.damage <= ATTACKS[AttackType.LIGHT].damage, "charge deals light-tier damage (gap-closer, not a damage tool)"
    assert stagger == 0.0, "clean charge hit must not stagger"
    print(f"PASS HIT charge (damage={amount:.2f}, no stagger)")

    # 5a) HIT: clean light hit -- damage but NO stagger (light never staggers)
    a = FakeCombatant(pos=(0, 0, 0), fwd=(0, 0, 1))
    t = FakeCombatant(pos=(0, 0, 1.5))
    result = resolve_hit(a, t, light)
    assert result is HitResult.HIT, f"expected HIT got {result}"
    assert len(t.damage_calls) == 1
    amount, kb, stagger = t.damage_calls[0]
    assert abs(amount - light.damage) < 1e-6, f"clean hit damage {amount} != {light.damage}"
    assert kb.y == 0.0
    assert abs(kb.z - light.knockback) < 1e-6, f"knockback z {kb.z} != {light.knockback}"
    assert stagger == 0.0, f"light hit must not stagger (stagger={stagger})"
    print(f"PASS HIT light (damage={amount:.2f}, kb={kb}, no stagger)")

    # 5a-2) HIT: clean heavy hit -- big damage, but NO stagger anymore
    a = FakeCombatant(pos=(0, 0, 0), fwd=(0, 0, 1))
    t = FakeCombatant(pos=(0, 0, 1.5))
    result = resolve_hit(a, t, heavy)
    assert result is HitResult.HIT, f"expected HIT got {result}"
    amount, kb, stagger = t.damage_calls[0]
    assert abs(amount - heavy.damage) < 1e-6, f"heavy damage {amount} != {heavy.damage}"
    assert stagger == 0.0, f"clean heavy hit must not stagger ({stagger})"
    print(f"PASS HIT heavy (damage={amount:.2f}, no stagger)")

    # 5b) HIT with riposte bonus
    a = FakeCombatant(pos=(0, 0, 0), fwd=(0, 0, 1))
    a.riposte_ready = True
    t = FakeCombatant(pos=(0.5, 0, 1.5))   # offset target to test direction
    result = resolve_hit(a, t, light)
    assert result is HitResult.HIT, f"expected HIT got {result}"
    amount, kb, stagger = t.damage_calls[0]
    expected = light.damage * RIPOSTE_DAMAGE_MULT
    assert abs(amount - expected) < 1e-6, f"riposte hit {amount} != {expected}"
    assert kb.y == 0.0
    # direction (1,0,1) normalized -> (~0.707, 0, ~0.707), magnitude == knockback
    kb_mag = math.hypot(kb.x, kb.z)
    assert abs(kb_mag - light.knockback) < 1e-6, f"kb mag {kb_mag} != {light.knockback}"
    assert kb.x > 0 and kb.z > 0
    print(f"PASS HIT+RIPOSTE (damage={amount:.2f}, kb={kb})")

    # bonus: in_attack_arc unit checks
    assert in_attack_arc(Vec3(0,0,0), Vec3(0,0,1), Vec3(0,0,2.0), 2.3, 85.0) is True
    assert in_attack_arc(Vec3(0,0,0), Vec3(0,0,1), Vec3(0,0,2.5), 2.3, 85.0) is False
    assert in_attack_arc(Vec3(0,0,0), Vec3(0,0,1), Vec3(2.0,0,0.1), 2.3, 85.0) is False
    assert in_attack_arc(Vec3(0,0,0), Vec3(0,0,1), Vec3(0.5,0,1.0), 2.3, 85.0) is True
    # y is ignored
    assert in_attack_arc(Vec3(0,0,0), Vec3(0,0,1), Vec3(0,99,1.5), 2.3, 85.0) is True
    print("PASS in_attack_arc edge cases")

    print("ALL COMBAT TESTS PASSED")
