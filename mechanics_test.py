"""
Focused mechanics test: drives scripted actions through the real
fighter+combat+physics stack to prove dodge i-frames, parry->riposte, block,
and a baseline clean hit all resolve correctly. No app.run() (non-blocking).
"""
import random
from ursina import Ursina, Vec3, window
import constants as c
import physics
import fighter

window.borderless = False
app = Ursina(title="riposte-mechanics", development_mode=False)

DT = 1.0 / 60.0


def fresh_pair():
    """Two fighters on the z-axis, 2.0 apart, already facing each other.
    Both flagged is_player so ai_think never fires; we script actions."""
    world = physics.PhysicsWorld()
    atk = fighter.Fighter(world, Vec3(0, 0, -1.0), team=0, color=c.PLAYER_COLOR, is_player=True)
    dfn = fighter.Fighter(world, Vec3(0, 0, 1.0), team=1, color=c.ENEMY_COLOR, is_player=True)
    return world, atk, dfn


def tick(world, atk, dfn):
    atk.update_fighter(DT, dfn)
    dfn.update_fighter(DT, atk)
    world.step(DT)


results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"{'PASS' if cond else 'FAIL'} {name}  {detail}")


# --- Test 1: clean HIT baseline -------------------------------------------- #
world, atk, dfn = fresh_pair()
atk.start_attack(c.AttackType.LIGHT)
for _ in range(40):
    tick(world, atk, dfn)
dmg = c.MAX_HP - dfn.hp
check("clean HIT deals light damage", abs(dmg - c.ATTACKS[c.AttackType.LIGHT].damage) < 0.5,
      f"(dealt {dmg:.1f}, expected {c.ATTACKS[c.AttackType.LIGHT].damage})")

# --- Test 2: DODGE i-frames negate damage ---------------------------------- #
world, atk, dfn = fresh_pair()
dfn.dodge(Vec3(1, 0, 0))          # sideways dodge -> i-frames on
atk.start_attack(c.AttackType.LIGHT)
for _ in range(40):
    tick(world, atk, dfn)
check("DODGE negates damage", abs(dfn.hp - c.MAX_HP) < 0.01, f"(hp={dfn.hp:.1f})")

# --- Test 3: PARRY deflects + arms riposte --------------------------------- #
world, atk, dfn = fresh_pair()
atk.start_attack(c.AttackType.LIGHT)
parried_seen = False
attacker_staggered = False
for i in range(40):
    if i == 8:                    # tap parry just before the swing goes active
        dfn.start_parry()
    tick(world, atk, dfn)
    if dfn.riposte_ready:
        parried_seen = True
    if atk.state == c.State.STAGGERED:
        attacker_staggered = True
check("PARRY took no damage", abs(dfn.hp - c.MAX_HP) < 0.01, f"(hp={dfn.hp:.1f})")
check("PARRY arms riposte", parried_seen)
check("PARRY staggers attacker", attacker_staggered)

# --- Test 4: RIPOSTE deals bonus (2x) damage ------------------------------- #
# Continue from test 3 state: defender has riposte_ready, attacker staggered.
if dfn.riposte_ready:
    dfn.start_attack(c.AttackType.LIGHT)
    atk_hp_before = atk.hp
    for _ in range(40):
        tick(world, atk, dfn)
    rdmg = atk_hp_before - atk.hp
    expected = c.ATTACKS[c.AttackType.LIGHT].damage * c.RIPOSTE_DAMAGE_MULT
    check("RIPOSTE deals 2x damage", abs(rdmg - expected) < 0.6,
          f"(dealt {rdmg:.1f}, expected {expected})")
else:
    check("RIPOSTE deals 2x damage", False, "(riposte was never armed)")

# --- Test 5: BLOCK reduces damage + costs stamina -------------------------- #
world, atk, dfn = fresh_pair()
dfn.start_block()
stam_before = dfn.stamina
atk.start_attack(c.AttackType.LIGHT)
for _ in range(40):
    tick(world, atk, dfn)
blocked_dmg = c.MAX_HP - dfn.hp
expected_block = c.ATTACKS[c.AttackType.LIGHT].damage * (c.BLOCK_DAMAGE_MULT + c.BLOCK_CHIP)
check("BLOCK reduces damage", abs(blocked_dmg - expected_block) < 0.6,
      f"(took {blocked_dmg:.1f}, expected {expected_block:.1f})")
check("BLOCK costs stamina", dfn.stamina < stam_before - 0.5,
      f"(stam {stam_before:.0f}->{dfn.stamina:.0f})")

# --- Test 6: clean HEAVY hit deals big damage but does NOT stagger ---------- #
world, atk, dfn = fresh_pair()
atk.start_attack(c.AttackType.HEAVY)
heavy_staggered = False
for _ in range(80):
    tick(world, atk, dfn)
    if dfn.state == c.State.STAGGERED:
        heavy_staggered = True
hdmg = c.MAX_HP - dfn.hp
check("clean HEAVY deals big damage", abs(hdmg - c.ATTACKS[c.AttackType.HEAVY].damage) < 0.6,
      f"(dealt {hdmg:.1f}, expected {c.ATTACKS[c.AttackType.HEAVY].damage})")
check("clean HEAVY does NOT stagger", not heavy_staggered)

# --- Test 7: blocking a HEAVY staggers the blocker (a light does not) ------- #
world, atk, dfn = fresh_pair()
dfn.start_block()
block_heavy_staggered = False
for _ in range(80):
    if atk.state in (c.State.IDLE, c.State.MOVING):
        atk.start_attack(c.AttackType.HEAVY)
    tick(world, atk, dfn)
    if dfn.state == c.State.STAGGERED:
        block_heavy_staggered = True
check("blocking a HEAVY staggers", block_heavy_staggered)

# --- Test 8: successive dodge cancels end-lag (free to act immediately) ----- #
# Geometry of a real swing vs a moving dodger is fiddly (the dodge can drift out
# of arc -> MISSED), so drive the documented combat callback directly against a
# real Fighter that is genuinely mid-dodge.
world, atk, dfn = fresh_pair()
dfn.dodge(Vec3(1, 0, 0))
tick(world, atk, dfn)             # one frame in: should be locked into DODGING
mid_dodge = (dfn.state == c.State.DODGING and dfn.invulnerable)
dfn.on_dodge_success()           # combat.resolve_hit fires this on a DODGED result
# Perfect dodge now drops i-frames + cancels MOST of the lockout, but leaves a
# short PUNISHABLE end-lag (still DODGING, no longer invulnerable) so a counter
# can't come out frame-perfect. After the end-lag it frees up.
endlag = (dfn.state == c.State.DODGING and not dfn.invulnerable)
cue_set = dfn.dodge_success_timer > 0.0
for _ in range(120):
    tick(world, atk, dfn)
    if dfn.state != c.State.DODGING:
        break
freed = dfn.state != c.State.DODGING
check("dodge enters DODGING with i-frames", mid_dodge, f"(state={dfn.state.name})")
check("perfect dodge leaves a short punishable end-lag", endlag,
      f"(state={dfn.state.name}, invuln={dfn.invulnerable})")
check("dodger frees up after end-lag", freed, f"(state={dfn.state.name})")
check("successive dodge sets colour cue", cue_set)

# --- Test 9: successful parry refunds a *partial* amount of stamina --------- #
world, atk, dfn = fresh_pair()
s0 = dfn.stamina
dfn.start_parry()
s_spent = dfn.stamina
dfn.on_parry_success()
s_refunded = dfn.stamina
check("parry costs stamina", abs(s_spent - (s0 - c.PARRY_STAMINA)) < 1e-6,
      f"({s0:.0f}->{s_spent:.0f})")
check("parry refund is partial (net loss)", s_spent < s_refunded < s0,
      f"(spent {s_spent:.0f} -> refunded {s_refunded:.0f} < start {s0:.0f})")

# --- Test 10: successful dodge refunds a *partial* amount of stamina -------- #
world, atk, dfn = fresh_pair()
s0 = dfn.stamina
dfn.dodge(Vec3(1, 0, 0))
s_spent = dfn.stamina
dfn.on_dodge_success()
s_refunded = dfn.stamina
check("dodge costs stamina", abs(s_spent - (s0 - c.DODGE_STAMINA)) < 1e-6,
      f"({s0:.0f}->{s_spent:.0f})")
check("dodge refund is partial (net loss)", s_spent < s_refunded < s0,
      f"(spent {s_spent:.0f} -> refunded {s_refunded:.0f} < start {s0:.0f})")

# --- Test 11: heavy guard-break refunds the attacker the heavy's full cost -- #
world, atk, dfn = fresh_pair()
atk.start_attack(c.AttackType.HEAVY)      # spends heavy stamina
s_spent = atk.stamina
dfn.is_blocking = True                     # defender holds a block
fighter.combat.resolve_hit(atk, dfn, c.ATTACKS[c.AttackType.HEAVY])
s_refunded = atk.stamina
expected = min(c.MAX_STAMINA, s_spent + c.ATTACKS[c.AttackType.HEAVY].stamina)
check("guard-break refunds full heavy cost", abs(s_refunded - expected) < 1e-6,
      f"(spent {s_spent:.0f} -> refunded {s_refunded:.0f})")

# --- Test 12: getting hit during windup cancels the attack ----------------- #
world, atk, dfn = fresh_pair()
atk.start_attack(c.AttackType.HEAVY)
in_windup = atk.state == c.State.ATTACK_WINDUP
atk.take_damage(5.0, None, 0.0)            # clean hit, no stagger, mid-windup
canceled = atk.state != c.State.ATTACK_WINDUP and atk.current_attack is None
check("attack starts in windup", in_windup, f"(state={atk.state.name})")
check("hit during windup cancels the attack", canceled, f"(state={atk.state.name})")

# --- Test 13: hit during stage-1 ACTIVE also cancels (pre-commit window) ---- #
# Windup + stage-1 ACTIVE (a hitbox-less wind-through) count as one cancellable
# window; only stage-2 ACTIVE2 (hitbox live) is committed.
world, atk, dfn = fresh_pair()
atk.start_attack(c.AttackType.HEAVY)
canceled_active = None
for _ in range(60):
    atk.update_fighter(DT, dfn)
    if atk.state == c.State.ATTACK_ACTIVE:
        atk.take_damage(5.0, None, 0.0)
        canceled_active = (atk.state == c.State.IDLE and atk.current_attack is None)
        break
    world.step(DT)
check("hit during stage-1 ACTIVE cancels", canceled_active is True, f"(result={canceled_active})")

# --- Test 14: hit during committed stage-2 ACTIVE2 does NOT cancel ---------- #
world, atk, dfn = fresh_pair()
atk.start_attack(c.AttackType.HEAVY)
committed = None
for _ in range(80):
    atk.update_fighter(DT, dfn)
    if atk.state == c.State.ATTACK_ACTIVE2:
        atk.take_damage(5.0, None, 0.0)
        committed = (atk.state == c.State.ATTACK_ACTIVE2 and atk.current_attack is not None)
        break
    world.step(DT)
check("hit during stage-2 ACTIVE2 does NOT cancel", committed is True, f"(result={committed})")

# --- Test 15: AI executes its planned defense when the hit is imminent ------- #
# The old AI reacted off WINDUP and could never time a parry; the new AI decides
# at windup but EXECUTES at stage-1 ACTIVE so the parry actually connects.
world = physics.PhysicsWorld()
human = fighter.Fighter(world, Vec3(0, 0, -1.0), team=0, color=c.PLAYER_COLOR, is_player=True)
ai = fighter.Fighter(world, Vec3(0, 0, 1.0), team=1, color=c.ENEMY_COLOR, is_player=False)
human.current_attack = c.ATTACKS[c.AttackType.LIGHT]
human.state = c.State.ATTACK_ACTIVE           # hit is imminent (next stage hits)
ai._ai_defense_plan = 'parry'                  # decided back at windup
ai._ai_swing_handled = True                    # already decided -> just execute
ai.ai_think(DT, human)
# p1 is a windup (parry_active opens at p2), so executing the plan means the AI
# has ENTERED the parry; the deflect frames come a moment later.
check("AI executes planned parry on imminent hit",
      ai.state == c.State.PARRYING, f"(state={ai.state.name})")

# --- Test 16: AI defends the majority of a spammed light offense ------------ #
# Pin the distance so this measures defensive skill, not spacing. Both get huge
# HP so neither dies and we get a clean sample over many swings.
random.seed(20240601)
world = physics.PhysicsWorld()
human = fighter.Fighter(world, Vec3(0, 0, -0.8), team=0, color=c.PLAYER_COLOR, is_player=True)
ai = fighter.Fighter(world, Vec3(0, 0, 0.8), team=1, color=c.ENEMY_COLOR, is_player=False)
human.hp = ai.hp = 1.0e9
swings = 0
clean_hits = 0
prev_ai_hp = ai.hp
for _ in range(1500):
    human.body.position = Vec3(0, 0, -0.8)    # pin spacing each frame
    ai.body.position = Vec3(0, 0, 0.8)
    if human.state in (c.State.IDLE, c.State.MOVING):
        if human.start_attack(c.AttackType.LIGHT):
            swings += 1
    human.update_fighter(DT, ai)
    ai.update_fighter(DT, human)
    world.step(DT)
    if prev_ai_hp - ai.hp > 6.0:              # full (unmitigated) light hit got through
        clean_hits += 1
    prev_ai_hp = ai.hp
defended = 1.0 - clean_hits / max(1, swings)
check("AI defends most player swings", swings >= 5 and defended > 0.5,
      f"(swings={swings}, clean hits={clean_hits}, defended={defended:.0%})")

# --- Test 17: AI defends a swing whose WINDUP it missed (parry->riposte fix) - #
# The reported exploit: parry the AI, then m1 -- the riposte was "never parried
# back" because the staggered AI sat through the riposte's windup and only the
# windup edge armed a defense. Now it decides at the first frame it can act, so a
# swing first seen mid-flight (already in stage-1 ACTIVE) still gets defended.
random.seed(99)
defended_missed = 0
trials = 40
for _ in range(trials):
    w = physics.PhysicsWorld()
    h = fighter.Fighter(w, Vec3(0, 0, -1.0), team=0, color=c.PLAYER_COLOR, is_player=True)
    ai = fighter.Fighter(w, Vec3(0, 0, 1.0), team=1, color=c.ENEMY_COLOR, is_player=False)
    h.current_attack = c.ATTACKS[c.AttackType.LIGHT]
    h.state = c.State.ATTACK_ACTIVE        # AI first observes the swing mid-flight
    ai.ai_think(DT, h)                      # one frame: must decide AND execute
    if ai.state in (c.State.PARRYING, c.State.DODGING) or ai.is_blocking:
        defended_missed += 1
check("AI defends a windup-missed attack", defended_missed > trials * 0.4,
      f"(defended {defended_missed}/{trials})")

# --- Test 18: dynamic tempo -- a stamina lead makes the AI defend more often - #
def _defend_rate(ai_stam, opp_stam, trials=40, seed=7):
    random.seed(seed)
    d = 0
    for _ in range(trials):
        w = physics.PhysicsWorld()
        h = fighter.Fighter(w, Vec3(0, 0, -1.0), team=0, color=c.PLAYER_COLOR, is_player=True)
        ai = fighter.Fighter(w, Vec3(0, 0, 1.0), team=1, color=c.ENEMY_COLOR, is_player=False)
        ai.stamina = ai_stam
        h.stamina = opp_stam
        h.current_attack = c.ATTACKS[c.AttackType.LIGHT]
        h.state = c.State.ATTACK_ACTIVE
        ai.ai_think(DT, h)
        if ai.state in (c.State.PARRYING, c.State.DODGING) or ai.is_blocking:
            d += 1
    return d / trials
hi_rate = _defend_rate(100, 20)     # AI flush, player gassed
lo_rate = _defend_rate(35, 100)     # AI gassed, player flush
check("higher stamina -> AI defends more", hi_rate > lo_rate + 0.1,
      f"(ahead={hi_rate:.0%}, behind={lo_rate:.0%})")

# --- Test 19: FEINT skips stage-2, deals no damage, returns to IDLE ---------- #
# A feint animates WINDUP + stage-1 ACTIVE, then skips the committed stage-2
# (ACTIVE2, hitbox-live) and snaps to IDLE -- so it never deals damage, arms the
# feint cooldown, and fires the one-shot feint_event (observed by an AI).
world, atk, dfn = fresh_pair()
atk.start_attack(c.AttackType.LIGHT)
feint_ok = atk.feint()
reached_active = reached_active2 = feint_seen = False
for _ in range(40):
    tick(world, atk, dfn)
    if atk.state == c.State.ATTACK_ACTIVE:
        reached_active = True
    if atk.state == c.State.ATTACK_ACTIVE2:
        reached_active2 = True
    if atk.feint_event:
        feint_seen = True
check("feint commits during windup", feint_ok)
check("feinted swing still animates stage-1 ACTIVE", reached_active)
check("feinted swing SKIPS committed stage-2", not reached_active2)
check("feint deals no damage", abs(dfn.hp - c.MAX_HP) < 0.01, f"(hp={dfn.hp:.1f})")
check("feint returns to IDLE", atk.state == c.State.IDLE, f"(state={atk.state.name})")
check("feint arms its cooldown", atk.feint_cooldown > 0.0)
check("feint fires feint_event (observable)", feint_seen)

# --- Test 20: getting HIT mid-feint GUARD-BREAKS the feinter ----------------- #
# The risk that balances the feint: a clean hit during a faked swing's pre-commit
# window staggers (guard-break) instead of just cancelling, and flags the event
# that drives main's freeze-frame + shake.
world, atk, dfn = fresh_pair()
atk.start_attack(c.AttackType.HEAVY)
atk.feint()
in_precommit = atk.state in (c.State.ATTACK_WINDUP, c.State.ATTACK_ACTIVE)
atk.take_damage(5.0, None, 0.0)            # clean hit, no stagger arg, mid-feint
check("feint pre-commit window entered", in_precommit, f"(state={atk.state.name})")
check("hit mid-feint guard-breaks (staggered)", atk.state == c.State.STAGGERED,
      f"(state={atk.state.name})")
check("mid-feint guard-break flags the cinematic event", atk.guard_break_event)

# --- Test 21: no two consecutive attacks may both be feinted (cooldown) ------- #
world, atk, dfn = fresh_pair()
atk.start_attack(c.AttackType.LIGHT)
atk.feint()
for _ in range(20):                         # let the feint resolve
    tick(world, atk, dfn)
on_cd = atk.feint_cooldown > 0.0
atk.start_attack(c.AttackType.LIGHT)        # next swing, still within cooldown
blocked = not atk.feint()
check("feint on cooldown after a feint", on_cd, f"(cd={atk.feint_cooldown:.2f})")
check("consecutive feint blocked by cooldown", blocked)

# --- Test 22: attention model distils distinct reads per play style ----------- #
A = fighter.AttentionModel
light_read = A(); light_read.light = 5.0
charge_read = A(); charge_read.charge = 5.0; charge_read.heavy = 1.0
feint_read = A(); feint_read.light = 3.0; feint_read.feint = 4.0
retreat_read = A(); retreat_read.retreat = 5.0; retreat_read.light = 1.0
lb = light_read.biases(1.0)
cb = charge_read.biases(1.0)
fb = feint_read.biases(1.0)
rb = retreat_read.biases(1.0)
check("light-spam read favours PARRY", lb['parry'] > 0.8 and lb['dodge_def'] < 0.1,
      f"(parry={lb['parry']:.2f}, dodge_def={lb['dodge_def']:.2f})")
check("charge-spam read favours DODGE + spacing",
      cb['dodge_def'] > 0.7 and cb['spacing'] > lb['spacing'],
      f"(dodge_def={cb['dodge_def']:.2f}, spacing={cb['spacing']:.2f})")
check("feint-spam read raises feint-wariness", fb['feint_wary'] > 0.8,
      f"(feint_wary={fb['feint_wary']:.2f})")
check("retreater read raises press", rb['press'] > 0.8, f"(press={rb['press']:.2f})")
mb = light_read.biases(0.6)
check("MEDIUM difficulty softens the read", mb['parry'] < lb['parry'],
      f"(medium={mb['parry']:.2f} < high={lb['parry']:.2f})")

# --- Test 23: a CHARGE is never BLOCKED on defense (anti block-break exploit) - #
# Blocking a charge guard-breaks the blocker -- the user's "easy to block-break
# with a charge" exploit came from the AI reflexively parry/blocking charges.
# Now a charge is heavy-TYPE for defense: dodge/parry only, never block.
random.seed(424242)
block_plans = 0
plans = 0
for _ in range(400):
    w = physics.PhysicsWorld()
    h = fighter.Fighter(w, Vec3(0, 0, -1.0), team=0, color=c.PLAYER_COLOR, is_player=True)
    ai = fighter.Fighter(w, Vec3(0, 0, 1.0), team=1, color=c.ENEMY_COLOR, is_player=False)
    ai.difficulty = c.Difficulty.HIGH
    ai.attention.charge = 5.0                 # AI has read a charge-spammer
    h.current_attack = c.ATTACKS[c.AttackType.CHARGE]
    h.state = c.State.ATTACK_ACTIVE
    ai.ai_think(DT, h)
    if ai.state in (c.State.PARRYING, c.State.DODGING) or ai.is_blocking:
        plans += 1
    if ai.is_blocking:
        block_plans += 1
check("AI never blocks a charge", block_plans == 0, f"(blocked {block_plans}/400)")
check("AI still defends charges (dodge/parry)", plans > 200, f"(defended {plans}/400)")

# --- Test 24: HIGH difficulty feints more often than MEDIUM ------------------- #
def _feint_rate(diff, trials=300, seed=11):
    random.seed(seed)
    feints = swings = 0
    for _ in range(trials):
        w = physics.PhysicsWorld()
        ai = fighter.Fighter(w, Vec3(0, 0, 0.7), team=1, color=c.ENEMY_COLOR, is_player=False)
        op = fighter.Fighter(w, Vec3(0, 0, -0.7), team=0, color=c.PLAYER_COLOR, is_player=True)
        ai.difficulty = diff
        for _ in range(150):
            ai.body.position = Vec3(0, 0, 0.7)   # pin spacing in range, open target
            op.body.position = Vec3(0, 0, -0.7)
            ai.update_fighter(DT, op)
            op.update_fighter(DT, ai)
            w.step(DT)
            if ai.state == c.State.ATTACK_WINDUP:
                swings += 1
                if ai.feint_pending:
                    feints += 1
                break
    return feints / max(1, swings), swings
hi_feint, hi_n = _feint_rate(c.Difficulty.HIGH)
lo_feint, lo_n = _feint_rate(c.Difficulty.MEDIUM)
check("HIGH difficulty feints more than MEDIUM", hi_feint > lo_feint + 0.04,
      f"(high={hi_feint:.0%} n={hi_n}, medium={lo_feint:.0%} n={lo_n})")

# --- Test 25: vs a light-SPAMMER the AI is UNPREDICTABLE and punishes ---------- #
# The old read converged on a deterministic parry-chain: itself exploitable, and
# (worse) it never cashed the buffered riposte, so spam was free. Now the read
# drives VARIETY (mix parry/dodge/block, no streaks) and the AI cashes punishes /
# counters, so spamming one move actually costs HP.
from collections import Counter
random.seed(31415)
world = physics.PhysicsWorld()
human = fighter.Fighter(world, Vec3(0, 0, -0.8), team=0, color=c.PLAYER_COLOR, is_player=True)
ai = fighter.Fighter(world, Vec3(0, 0, 0.8), team=1, color=c.ENEMY_COLOR, is_player=False)
ai.difficulty = c.Difficulty.HIGH
human.hp = ai.hp = 1.0e9                      # nobody dies; long clean sample
defc = Counter()
prev_ai = ai.state
maxstreak = curstreak = 0
last_def = None
start_human_hp = human.hp
for _ in range(3000):
    human.body.position = Vec3(0, 0, -0.8)   # pin spacing -> measure behaviour
    ai.body.position = Vec3(0, 0, 0.8)
    if human.state in (c.State.IDLE, c.State.MOVING):
        human.start_attack(c.AttackType.LIGHT)
    human.update_fighter(DT, ai)
    ai.update_fighter(DT, human)
    world.step(DT)
    if ai.state != prev_ai and ai.state in (c.State.PARRYING, c.State.DODGING, c.State.BLOCKING):
        defc[ai.state.name] += 1
        if ai.state.name == last_def:
            curstreak += 1
        else:
            curstreak = 1
            last_def = ai.state.name
        maxstreak = max(maxstreak, curstreak)
    prev_ai = ai.state
dmg_to_spammer = start_human_hp - human.hp
tot = sum(defc.values())
dominant = max(defc.values()) / tot if tot else 1.0
check("AI varies its defense vs spam (>=2 types)", len(defc) >= 2, f"({dict(defc)})")
check("no single defense dominates the response", dominant < 0.75, f"(dominant={dominant:.0%})")
check("AI doesn't chain one defense (anti-streak)", maxstreak <= 6, f"(max streak={maxstreak})")
check("spamming one move is punished (AI deals damage back)", dmg_to_spammer > 80,
      f"(dealt {dmg_to_spammer:.0f} over 50s)")

print("=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
print(f"MECHANICS: {passed}/{len(results)} checks passed")
print("=" * 60)

try:
    app.userExit()
except Exception:
    pass
