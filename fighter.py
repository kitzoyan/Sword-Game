"""
fighter.py -- Fighter entity for Riposte.

Owns a PhysicsBody, a visual capsule + sword, a state machine over `State`,
hp/stamina, and exposes the Combatant interface (see constants.py).

Input wiring (player):
    Movement (WASD), dodge (SHIFT), and block-hold/parry-tap (F) are read
    from `held_keys` each frame inside handle_input().
    Discrete attack presses (J light, R heavy) and dodge edge are also read
    via edge detection on held_keys, so main.py does NOT need to forward
    `input(key)`. Main just calls `fighter.update_fighter(dt, opponent)` each
    frame and the Fighter handles everything.
"""

import math
import random

from ursina import (Entity, Mesh, Vec3, camera, color as ucolor, destroy,
                    held_keys, scene)

import constants
from constants import (
    AI_AGGRESSION,
    AI_BLOCK_SKILL,
    AI_CHARGE_CHANCE,
    AI_CHASE_CHARGE_RATE,
    AI_COUNTER_WINDOW,
    AI_DODGE_SKILL,
    AI_GAPCLOSE_STAMINA,
    AI_PARRY_SKILL,
    AI_PREFERRED_RANGE,
    AI_TURTLE_CHARGE_CHANCE,
    AI_TURTLE_HEAVY_CHANCE,
    ATTACKS,
    ATTENTION_DECAY,
    ATTENTION_GAIN_CHARGE,
    ATTENTION_GAIN_DODGE,
    ATTENTION_GAIN_FEINT,
    ATTENTION_GAIN_HEAVY,
    ATTENTION_GAIN_LIGHT,
    ATTENTION_GAIN_PARRY_WHIFF,
    ATTENTION_GAIN_RETREAT,
    ATTENTION_MAX,
    ATTENTION_SPACING_BONUS,
    ArtType,
    ART_COOLDOWN_DECAY_AMOUNT,
    ART_COOLDOWN_MIN,
    ART_COOLDOWN_START,
    ART_DECAY_INTERVAL,
    ART_FRAME_DURATIONS,
    ART_OVERCLOCK_MOVE_SPEED,
    ART_STAMINA_DECAY_AMOUNT,
    ART_STAMINA_MIN,
    ART_STAMINA_START,
    AI_ART_GLOBAL_COOLDOWN,
    AI_ART_STAMINA_BUFFER,
    AI_ART_PARRY_FRACTION,
    AI_ART_DODGE_LEAD,
    AI_ART_PARRY_LEAD,
    AI_ART_PANIC_CHANCE,
    AI_AIR_ART_RATE,
    AI_KAGURA_RANGE,
    AI_AIR_ART_APEX_VY,
    AI_WALL_MARGIN,
    ARENA_RADIUS,
    AttackType,
    CENTIPEDE_RING_EXPAND_SPEED,
    CENTIPEDE_RING_MAX_RADIUS,
    KAGURA_RING_EXPAND_SPEED,
    KAGURA_RING_MAX_RADIUS,
    HARMONIC_CRESCENT_SPEED,
    OVERCLOCK_CRESCENT_SPEED,
    OVERCLOCK_CRESCENT_MAX_DIST,
    OVERCLOCK_RING_MAX_RADIUS,
    BLOCK_STAMINA_PER_HIT,
    CHARGE_DASH_IMPULSE,
    DEFAULT_DIFFICULTY,
    DIFFICULTY_PROFILES,
    DODGE_COOLDOWN,
    DODGE_DURATION,
    DODGE_IFRAMES,
    DODGE_IMPULSE,
    DODGE_REFUND,
    DODGE_STAMINA,
    DODGE_SUCCESS_ENDLAG,
    FEINT_COOLDOWN,
    FEINT_FOLLOWUP_DELAY,
    FEINT_FOLLOWUP_DELAY_CHANCE,
    FEINT_FOLLOWUP_WINDOW,
    FEINT_GUARD_BREAK_STAGGER,
    FEINT_TYPE_MULT,
    FIGHTER_HEIGHT,
    FIGHTER_MASS,
    FIGHTER_RADIUS,
    MAX_HP,
    MAX_STAMINA,
    MOVE_ACCEL,
    MOVE_SPEED,
    PARRY_HEAVY_REFUND,
    PARRY_P1_DURATION,
    PARRY_P2_DURATION,
    PARRY_P3_DURATION,
    PARRY_REFUND,
    PARRY_STAGGER_TIME,
    PARRY_STAMINA,
    PARRY_WHIFF_RECOVERY,
    RIPOSTE_WINDOW,
    STAMINA_REGEN,
    STAMINA_REGEN_DELAY,
    SWORD_COLOR,
    State,
    TURN_SPEED,
    WINDUP_TURN_SPEED,
    # --- Platformer: jump / aerial / air-movement ---
    AERIAL_LANDING_LAG,
    AERIAL_SLAM_IMPULSE,
    AIR_CONTROL,
    AIR_MOVE_SPEED,
    AI_JUMP_IN_RATE,
    GROUND_VERTICAL_REACH,
    JUMP_IMPULSE,
    JUMP_STAMINA,
    LANDING_LAG,
    MIN_AIRTIME,
    # --- Platformer: procedural animation (pose smoothing + gait) ---
    ANIM_ATTACK_LERP_SPEED,
    ANIM_LERP_SPEED,
    GAIT_ARM_SWING,
    GAIT_BOB,
    GAIT_FREQ_BASE,
    GAIT_FREQ_PER_SPEED,
    GAIT_LEG_SWING,
    IDLE_BREATH_AMP,
    IDLE_BREATH_FREQ,
    GHOST_SWORD_ALPHAS,
)
import physics
import combat
import art_sprites


# ----------------------------------------------------------------------------- #
#  Small helpers
# ----------------------------------------------------------------------------- #
def _xz_len(v: Vec3) -> float:
    return math.hypot(v.x, v.z)


def _xz_unit(v: Vec3) -> Vec3:
    m = _xz_len(v)
    if m <= 1e-6:
        return Vec3(0, 0, 0)
    return Vec3(v.x / m, 0.0, v.z / m)


def _lerp_dir(a: Vec3, b: Vec3, t: float) -> Vec3:
    """Lerp two xz unit vectors, renormalize."""
    nx = a.x + (b.x - a.x) * t
    nz = a.z + (b.z - a.z) * t
    m = math.hypot(nx, nz)
    if m <= 1e-6:
        return a
    return Vec3(nx / m, 0.0, nz / m)


def _yaw_from_forward(fwd: Vec3) -> float:
    # Ursina y rotation: 0 looks +z, increases clockwise (toward +x).
    return math.degrees(math.atan2(fwd.x, fwd.z))


# Attack states (regular attacks and arts) during which the fighter is committed
# to its current facing: lock-on auto-turn is suspended until the attack ends.
_ATTACK_STATES = frozenset((
    State.ATTACK_WINDUP,
    State.ATTACK_ACTIVE,
    State.ATTACK_ACTIVE2,
    State.ATTACK_RECOVERY,
    State.ATTACK_ART,
))

# The three parry phases (p1 -> p2 -> p3). The deflect window (parry_active) spans
# all three; a successful parry follows through the remaining phases to idle.
_PARRY_STATES = frozenset((
    State.PARRYING,
    State.PARRYING2,
    State.PARRYING3,
))

# The art sub-frame on which projectiles spawn (A4, 0-indexed). Kept as a name so
# the AI's reaction timing tracks the actual spawn frame if the art pipeline moves.
_ART_SPAWN_SUBFRAME = 3

# Per-art (projectile_speed, max_reach) used ONLY by the AI to estimate when/whether
# an art will connect. Pulled straight from the sprite tuning constants, so changing
# a projectile's speed or range keeps the AI's read in sync. max_reach=None means the
# projectile crosses the whole arena (it will reach any in-bounds target).
_ART_REACH = {
    ArtType.CENTIPEDE: (CENTIPEDE_RING_EXPAND_SPEED, CENTIPEDE_RING_MAX_RADIUS),
    ArtType.KAGURA:    (KAGURA_RING_EXPAND_SPEED, KAGURA_RING_MAX_RADIUS),
    ArtType.HARMONIC:  (HARMONIC_CRESCENT_SPEED, None),
    ArtType.OVERCLOCK: (OVERCLOCK_CRESCENT_SPEED,
                        max(OVERCLOCK_CRESCENT_MAX_DIST, OVERCLOCK_RING_MAX_RADIUS)),
}

# Two-button art scheme. Each art key casts the GROUND variant while standing and
# the AIRBORNE variant while in the air; the two variants on a key share one
# cooldown (they are two forms of the same art). Key 1 -> CENTIPEDE / KAGURA,
# key 2 -> OVERCLOCK / HARMONIC.
GROUND_ART = {1: ArtType.CENTIPEDE, 2: ArtType.OVERCLOCK}
AIR_ART = {1: ArtType.KAGURA, 2: ArtType.HARMONIC}
_ART_PAIR = {
    ArtType.CENTIPEDE: ArtType.KAGURA,
    ArtType.KAGURA: ArtType.CENTIPEDE,
    ArtType.OVERCLOCK: ArtType.HARMONIC,
    ArtType.HARMONIC: ArtType.OVERCLOCK,
}
# Arts that are only ever cast in the air (they float the caller in place).
_AIR_ARTS = frozenset({ArtType.KAGURA, ArtType.HARMONIC})
# HUD slots: one diamond per art key, showing that key's shared cooldown.
ART_HUD_SLOTS = (ArtType.CENTIPEDE, ArtType.OVERCLOCK)


def _art_release_delay(art_type, sub_frame, frame_timer):
    """Seconds until `art_type`'s projectiles spawn, given the caster's current
    sub-frame and the time left in it. Summed from ART_FRAME_DURATIONS so it stays
    correct if those timings change. 0 once the spawn frame has been reached."""
    if sub_frame >= _ART_SPAWN_SUBFRAME:
        return 0.0
    durations = ART_FRAME_DURATIONS[art_type]
    t = max(0.0, frame_timer)                       # remainder of the current frame
    for i in range(sub_frame + 1, _ART_SPAWN_SUBFRAME):
        t += durations[i]                           # whole frames still to elapse
    return t


def _art_reach_eta(art_type, dist):
    """(travel_seconds, reachable) for an art's projectile to cover horizontal
    distance `dist` after it spawns. Approximate -- it only has to be close enough
    that a dodge's i-frames bracket the hit."""
    speed, max_reach = _ART_REACH[art_type]
    reachable = (max_reach is None) or (dist <= max_reach + 0.6)
    travel = dist / speed if speed > 1e-6 else 0.0
    return travel, reachable


def _weighted_pick(weights: dict):
    """Pick a key from {key: weight} proportional to weight. Returns None if empty
    or all-zero. Used to keep the AI's defensive choice a varied draw rather than a
    deterministic argmax (a predictable defense is itself exploitable)."""
    items = [(k, w) for k, w in weights.items() if w > 0.0]
    total = sum(w for _, w in items)
    if total <= 0.0:
        return None
    r = random.random() * total
    acc = 0.0
    for k, w in items:
        acc += w
        if r <= acc:
            return k
    return items[-1][0]


def _shade(c, f: float):
    """Lighten (f>0, toward white) or darken (f<0, toward black) a colour."""
    r, g, b = c.r, c.g, c.b
    if f >= 0:
        r += (1.0 - r) * f
        g += (1.0 - g) * f
        b += (1.0 - b) * f
    else:
        k = 1.0 + f
        r *= k
        g *= k
        b *= k
    return ucolor.rgb32(r * 255, g * 255, b * 255)


# Colour every body part (except the sword) turns while STAGGERED.
_STAGGER_COLOR = ucolor.rgb32(235, 215, 50)
# Colour every body part (except the sword) flashes briefly on taking damage.
_HIT_COLOR = ucolor.rgb32(220, 55, 55)
# Colour flash when a dodge cleanly evades an attack (successive dodge reward).
_DODGE_SUCCESS_COLOR = ucolor.rgb32(120, 240, 255)
# How long the successive-dodge colour flash lasts.
DODGE_SUCCESS_FLASH = 0.35


# ----------------------------------------------------------------------------- #
#  Sword swing trail
# ----------------------------------------------------------------------------- #
# Cyan-white streak left by the blade during its active frames. Flat/unlit to
# match the rest of the art, fades out fast so it reads as motion, not clutter.
TRAIL_COLOR = ucolor.rgba32(255, 255, 255, 255)
TRAIL_LIFE = 0.1        # seconds a segment takes to fade from full to gone
TRAIL_MAX_SEGMENTS = 20  # hard cap so a long combo can't pile up geometry
# Skip emitting if the blade tip barely moved -- avoids degenerate slivers when
# the sword is nearly still at the start/end of the active window.
TRAIL_MIN_STEP = 0.05
# The sword poses "teleport" between discrete frames, so the bridge between two
# samples is one long straight chord -- which cuts through the fighter. We split
# that chord into SUBDIVISIONS quads and bow the middle outward by BOW units
# (zero at the endpoints, max in the middle) so the streak arcs in FRONT of the
# body instead of slicing through it.
TRAIL_SUBDIVISIONS = 8
TRAIL_BOW = 1.5


class SwordTrail:
    """A short ribbon of fading quads swept between the blade's hilt and tip.

    Hand-rolled because Ursina has no built-in trail. Each frame during a swing
    the fighter calls emit() with the blade's current (root, tip) world points;
    we bridge them to the previous frame's points with a quad, then fade every
    live quad's alpha to zero and destroy it. Quads live in world space (parent
    = scene) so they stay put as the blade moves on.
    """

    def __init__(self, segment_color=TRAIL_COLOR, life=TRAIL_LIFE,
                 max_segments=TRAIL_MAX_SEGMENTS):
        self.color = segment_color
        self.life = life
        self.max_segments = max_segments
        self._prev = None          # (root_world, tip_world) from last emit
        self._segments = []        # list of [entity, age]

    def emit(self, root_world, tip_world, bow_dir=None, bow_amount=0.0,
             subdivisions=TRAIL_SUBDIVISIONS):
        root_world = Vec3(root_world.x, root_world.y, root_world.z)
        tip_world = Vec3(tip_world.x, tip_world.y, tip_world.z)
        if self._prev is not None:
            p_root, p_tip = self._prev
            if (tip_world - p_tip).length() >= TRAIL_MIN_STEP:
                # Fill the pose-to-pose jump with a bowed strip of quads. f runs
                # 0->1 across the jump; the sin(pi*f) bulge is zero at both ends
                # (so the strip stays anchored to the real poses) and peaks in
                # the middle, pushing the arc outward along bow_dir.
                steps = max(1, subdivisions)
                prev_r, prev_t = p_root, p_tip
                for i in range(1, steps + 1):
                    f = i / steps
                    r = p_root + (root_world - p_root) * f
                    t = p_tip + (tip_world - p_tip) * f
                    if bow_dir is not None and bow_amount:
                        bulge = bow_dir * (bow_amount * math.sin(math.pi * f))
                        r = r + bulge
                        t = t + bulge
                    self._add_quad(prev_r, prev_t, t, r)
                    prev_r, prev_t = r, t
        self._prev = (root_world, tip_world)

    def _add_quad(self, a_root, a_tip, b_tip, b_root):
        quad = Entity(
            parent=scene,
            model=Mesh(
                vertices=[a_root, a_tip, b_tip, b_root],
                triangles=[(0, 1, 2), (0, 2, 3)],
                mode='triangle',
            ),
            color=self.color,
            unlit=True,
            double_sided=True,
        )
        self._segments.append([quad, 0.0])
        if len(self._segments) > self.max_segments:
            old, _ = self._segments.pop(0)
            destroy(old)

    def update(self, dt):
        alive = []
        for ent, age in self._segments:
            age += dt
            t = age / self.life
            if t >= 1.0:
                destroy(ent)
            else:
                ent.alpha = 1.0 - t
                alive.append([ent, age])
        self._segments = alive

    def reset(self):
        # Break the ribbon so the next swing doesn't draw a streak bridging the
        # gap from where the blade rested. Existing segments keep fading.
        self._prev = None

    def clear(self):
        # Destroy every live segment immediately. Segments are parented to the
        # scene (not the fighter), so they must be torn down explicitly -- e.g.
        # on game restart, or they'd orphan in the world.
        for ent, _ in self._segments:
            destroy(ent)
        self._segments = []
        self._prev = None


# ----------------------------------------------------------------------------- #
#  Parry sparks
# ----------------------------------------------------------------------------- #
# A short burst of little cubes flung from the clash point on a successful parry.
# Warm yellow-white, unlit to match the art; they arc under gravity, drag to a
# stop, and shrink+fade out. Like the trail they live in world space (parent =
# scene), so they must be cleared explicitly on restart.
SPARK_COLOR = ucolor.rgba32(255, 225, 200, 255)
SPARK_COUNT = 16            # particles per parry burst
SPARK_LIFE = 0.32          # seconds each particle lives
SPARK_SPEED = (10, 20)   # initial speed range (units/sec)
SPARK_GRAVITY = -12.0      # downward accel so sparks arc and fall
SPARK_DRAG = 1.0           # per-second velocity damping
SPARK_SIZE = 0.1        # starting cube edge length
# A guard-break is a heavier impact than a parry tick -- bigger, chunkier sparks.
GUARD_BREAK_SPARK_COUNT = 26
GUARD_BREAK_SPARK_SIZE = 0.18


class SparkSystem:
    """Transient cube particles for clash sparks (no built-in particles worth the
    weight here). burst() spawns a fan from a point; update() ticks physics +
    fade and reaps dead ones; clear() tears all down on restart."""

    def __init__(self, spark_color=SPARK_COLOR):
        self.color = spark_color
        self._particles = []   # list of [entity, velocity(Vec3), age, size]

    def burst(self, position, direction=None, count=SPARK_COUNT, size=SPARK_SIZE):
        for _ in range(count):
            v = Vec3(random.uniform(-1.0, 1.0),
                     random.uniform(-0.2, 1.0),
                     random.uniform(-1.0, 1.0))
            if direction is not None:
                v = v + direction * 1.3          # bias the fan outward from the clash
            m = v.length() or 1.0
            v = v * (random.uniform(*SPARK_SPEED) / m)
            e = Entity(parent=scene, model='cube', color=self.color,
                       position=Vec3(position.x, position.y, position.z),
                       scale=size, unlit=True)
            self._particles.append([e, v, 0.0, size])

    def update(self, dt):
        alive = []
        for e, vel, age, size in self._particles:
            age += dt
            if age >= SPARK_LIFE:
                destroy(e)
                continue
            vel = Vec3(vel.x, vel.y + SPARK_GRAVITY * dt, vel.z)
            vel = vel * max(0.0, 1.0 - SPARK_DRAG * dt)
            e.position = e.position + vel * dt
            f = age / SPARK_LIFE
            e.alpha = 1.0 - f
            e.scale = size * (1.0 - 0.6 * f)
            alive.append([e, vel, age, size])
        self._particles = alive

    def clear(self):
        for entry in self._particles:
            destroy(entry[0])
        self._particles = []


# ----------------------------------------------------------------------------- #
#  Blade glint (shiny-metal sparkle)
# ----------------------------------------------------------------------------- #
# A brief star/glare sprite that pops on the blade when a HEAVY/CHARGE is thrown,
# the classic "shiny metal" twinkle. Built procedurally (a flat N-pointed star
# mesh) so we need no texture asset; billboarded to face the camera each frame.
GLINT_COLOR = ucolor.rgba32(255, 252, 235, 255)
GLINT_LIFE = 0.22          # seconds the twinkle lasts
GLINT_SIZE = 1.3           # peak width of the star (world units)
GLINT_POINTS = 4           # spikes on the star (4 = classic sparkle)


def _star_geometry(points, outer=0.5, inner=0.16):
    """Flat star polygon in the xy plane: a centre vertex fanned out to
    alternating long (outer) and short (inner) perimeter points."""
    verts = [Vec3(0, 0, 0)]
    n = points * 2
    for k in range(n):
        ang = math.pi * k / points + math.pi / 2   # first spike points up
        r = outer if k % 2 == 0 else inner
        verts.append(Vec3(math.cos(ang) * r, math.sin(ang) * r, 0))
    tris = []
    for k in range(1, n + 1):
        nxt = k + 1 if k < n else 1
        tris.append((0, k, nxt))
    return verts, tris


_STAR_VERTS, _STAR_TRIS = _star_geometry(GLINT_POINTS)


class GlintSystem:
    """Spawns short-lived star sprites that pop (scale up then down) and fade.
    Sprites are parented to a moving anchor (the blade) so the twinkle rides the
    sword, and look_at(camera) keeps them facing the viewer."""

    def __init__(self, glint_color=GLINT_COLOR):
        self.color = glint_color
        self._glints = []   # list of [entity, age, size, delay]

    def spawn(self, anchor, size=GLINT_SIZE, delay=0.0):
        """Pop a star at `anchor`. `delay` holds it invisible for that many seconds
        before it starts its pop -- letting a caller stack a second, later flash on
        the same spot for a double-pulse."""
        e = Entity(parent=anchor,
                   model=Mesh(vertices=list(_STAR_VERTS),
                              triangles=list(_STAR_TRIS), mode='triangle'),
                   color=self.color, unlit=True, double_sided=True,
                   scale=0.001)
        e.alpha = 0.0
        self._glints.append([e, 0.0, size, delay])

    def update(self, dt):
        alive = []
        for e, age, size, delay in self._glints:
            age += dt
            t = age - delay                    # time since this flash's pop began
            if t >= GLINT_LIFE:
                destroy(e)
                continue
            if t < 0.0:
                # Still waiting out its delay -- keep it hidden.
                e.scale = 0.001
                e.alpha = 0.0
                alive.append([e, age, size, delay])
                continue
            env = math.sin(math.pi * (t / GLINT_LIFE))   # 0 -> 1 -> 0 pop
            e.scale = max(0.001, size * env)
            e.alpha = env
            e.look_at(camera.world_position)               # face the viewer
            alive.append([e, age, size, delay])
        self._glints = alive

    def clear(self):
        for entry in self._glints:
            destroy(entry[0])
        self._glints = []


# ----------------------------------------------------------------------------- #
#  Attention model -- the AI's decaying read of the opponent's recent actions
# ----------------------------------------------------------------------------- #
class AttentionModel:
    """A decaying memory of what the opponent has been doing lately. Each
    observe_*() bumps a tally; every tally decays each frame (half-life ~1.2s).
    biases() distils the tallies into response leans the AI applies to its
    defense, spacing and offense -- so leaning on one tactic gets answered by its
    counter. Feints exist so these reads are punishable rather than a free wall.

    Tallies:
      light/heavy/charge -- attack-type usage (the swing's START, so a feinted
                            light still counts as a light attempt)
      dodge              -- evasive dodges
      retreat            -- accumulates while the opponent is actively backing off
      parry_whiff        -- parries that expired without deflecting anything
      feint              -- feints the opponent resolved (faked swings)
    """

    def __init__(self):
        self.light = 0.0
        self.heavy = 0.0
        self.charge = 0.0
        self.dodge = 0.0
        self.retreat = 0.0
        self.parry_whiff = 0.0
        self.feint = 0.0

    def decay(self, dt):
        k = ATTENTION_DECAY ** dt
        self.light *= k
        self.heavy *= k
        self.charge *= k
        self.dodge *= k
        self.retreat *= k
        self.parry_whiff *= k
        self.feint *= k

    def _bump(self, attr, amount):
        setattr(self, attr, min(ATTENTION_MAX, getattr(self, attr) + amount))

    def observe_attack(self, atype, gain_mult=1.0):
        if atype == AttackType.LIGHT:
            self._bump('light', ATTENTION_GAIN_LIGHT * gain_mult)
        elif atype == AttackType.HEAVY:
            self._bump('heavy', ATTENTION_GAIN_HEAVY * gain_mult)
        elif atype == AttackType.CHARGE:
            self._bump('charge', ATTENTION_GAIN_CHARGE * gain_mult)

    def observe_dodge(self, gain_mult=1.0):
        self._bump('dodge', ATTENTION_GAIN_DODGE * gain_mult)

    def observe_retreat(self, dt, gain_mult=1.0):
        self._bump('retreat', ATTENTION_GAIN_RETREAT * dt * gain_mult)

    def observe_parry_whiff(self, gain_mult=1.0):
        self._bump('parry_whiff', ATTENTION_GAIN_PARRY_WHIFF * gain_mult)

    def observe_feint(self, gain_mult=1.0):
        self._bump('feint', ATTENTION_GAIN_FEINT * gain_mult)

    def biases(self, attention_mult):
        """Distil tallies into response leans in roughly [0..1], scaled by the
        difficulty's attention_mult (0 = ignore the read entirely).

        Returns a dict:
          parry        -- favour parrying (the light-spam answer)
          dodge_def    -- favour dodging on defense (the guard-break-spam answer:
                          safe vs a possibly-feinted charge, no whiff recovery)
          spacing      -- want more distance (light or guard-break spam)
          feint_wary   -- distrust the read; pre-commit parries less, dodge more,
                          and sometimes don't commit at all (opponent feints a lot)
          press        -- pressure harder (a retreating/passive opponent)
          bait         -- throw feints to punish a jumpy opponent (over-dodges or
                          whiffs parries -- bait the reaction, hit the recovery)
        """
        total = self.light + self.heavy + self.charge + 1e-6
        light_lean = self.light / total
        heavy_lean = self.heavy / total
        charge_lean = self.charge / total
        gb_lean = (self.charge + 0.6 * self.heavy) / total
        feint_w = min(1.0, self.feint / 3.0)
        dodge_lean = min(1.0, self.dodge / 4.0)
        retreat_lean = min(1.0, self.retreat / 4.0)
        whiff_lean = min(1.0, self.parry_whiff / 3.0)
        # How one-note the opponent's attack mix is (1 = always the same type),
        # gated by how much attacking we've actually seen so a single early swing
        # doesn't read as "fully predictable". Drives DEFENSE VARIETY: the more
        # predictable they are, the more the AI flattens its own responses + breaks
        # pattern with counters, so it never becomes an exploitable parry-chain.
        commitment = min(1.0, (self.light + self.heavy + self.charge) / 4.0)
        predict = max(light_lean, heavy_lean, charge_lean) * commitment
        m = attention_mult
        # light_lean / gb_lean are RATIOS of decaying tallies, so they are scale-
        # invariant and DON'T fade on their own -- a stale read would otherwise
        # persist forever once the opponent goes quiet (the "AI retreats forever
        # until I attack" bug). Gate the attack-mix leans by `commitment` (which
        # IS absolute and decays) so these reads fade out when no swings are seen.
        return {
            'parry': light_lean * commitment * m,
            'dodge_def': gb_lean * commitment * m,
            'spacing': min(1.0, gb_lean * 0.7 + light_lean * 0.4) * commitment * m,
            'feint_wary': feint_w * m,
            'press': retreat_lean * m,
            'bait': min(1.0, dodge_lean * 0.6 + whiff_lean * 0.7) * m,
            'predict': predict * m,
        }


# ----------------------------------------------------------------------------- #
#  Fighter
# ----------------------------------------------------------------------------- #
class Fighter(Entity):
    def __init__(self, world, position, team, color, is_player=False):
        super().__init__()
        self.world = world
        self.team = team
        self.team_color = color
        self.is_player = is_player

        # Physics body (feet at `position`).
        self.body = physics.PhysicsBody(
            position=Vec3(*position),
            radius=FIGHTER_RADIUS,
            height=FIGHTER_HEIGHT,
            mass=FIGHTER_MASS,
        )
        world.add_body(self.body)

        # Combat / state.
        self.hp = MAX_HP
        self.stamina = MAX_STAMINA
        self.state = State.IDLE
        self._forward = Vec3(0, 0, 1) if team == 0 else Vec3(0, 0, -1)
        # Cached each frame in update_fighter so start_attack/start_art can snap to
        # face the opponent at commit time without threading it through every call.
        self._opponent = None

        # Flags read by combat.
        self.invulnerable = False
        self.is_blocking = False
        self.parry_active = False
        # True once the current parry has deflected something -- distinguishes a
        # successful parry (follow through the animation) from a whiff (flag it).
        self._parry_success = False
        # True while serving the post-whiff end-lag (stalled, punishable).
        self._parry_recovering = False
        self.riposte_ready = False
        # One-shot: set True the frame this fighter's attack breaks a guard, read
        # and cleared by main (for the guard-break camera shake).
        self.guard_break_event = False
        # Feinting. feint_pending is armed mid-swing (player presses I, or the AI
        # commits a fake); resolved at the stage-1 -> stage-2 boundary by skipping
        # the committed stage. feint_cooldown blocks back-to-back feinted swings.
        self.feint_pending = False
        self.feint_cooldown = 0.0
        # One-shot observation flags consumed by an observing AI (attention model):
        # feint_event fires the frame a feint resolves; parry_whiff_event fires the
        # frame a parry window expires without deflecting anything.
        self.feint_event = False
        self.parry_whiff_event = False

        # Timers (all seconds).
        self.state_timer = 0.0          # time remaining in current sub-state
        self.regen_delay_timer = 0.0    # >0 means stamina regen blocked
        self.riposte_timer = 0.0
        self.dodge_cooldown = 0.0
        self.stagger_timer = 0.0
        self.parry_feedback_timer = 0.0
        self.dodge_success_timer = 0.0   # >0 = recently nailed a dodge (colour cue)

        # Current attack (Attack) + per-swing hit guard.
        self.current_attack = None
        self.already_hit = False
        # Whether the current charge should lunge (forward dash on stage 2). Set
        # per-attack in start_attack: player lunges only when pressing W, the AI
        # lunges when charging to close a gap.
        self._charge_lunge = False
        # Stationary (no-input) dodge: dodges in place and spins the body 360
        # over the dodge duration as a visual telegraph. _dodge_spin_t accumulates
        # elapsed spin time (monotonic, so the angle never jumps if end-lag shortens
        # the remaining state_timer).
        self._dodge_spin = False
        self._dodge_spin_t = 0.0
        # Light attacks alternate slash direction: False=right, True=left.
        # Flipped each time a light attack starts; read by the sword visual.
        self._light_swing_left = False

        # Player input edge tracking.
        self._prev_light = False
        self._prev_parryblock = False
        self._prev_heavy = False
        self._prev_charge = False
        self._prev_dodge = False
        self._prev_feint = False
        self._prev_jump = False
        self._parryblock_held_time = 0.0

        # AI state.
        self._ai_defense_plan = None    # 'parry' | 'dodge' | 'block' | None,
                                        # decided when the opponent starts a swing,
                                        # executed when the hit is imminent.
        self._ai_attack_cooldown = 0.0
        self._ai_swing_handled = False  # decided a defense for the opponent's
                                        # current swing yet? (one decision/swing)
        self._ai_feint_followup = 0.0   # >0 = a feint just resolved; slam a
                                        # committed follow-up while it lasts.
        self._ai_feint_delay = 0.0      # >0 = baiting: hold the follow-up swing
                                        # this much longer before committing it.
        # Unpredictability: the AI refuses to repeat one defense forever (a parry-
        # chain is exploitable). Track the last defense + its streak, and a window
        # opened when it DECLINES to defend so it counters in the recovery instead.
        self._ai_last_defense = None
        self._ai_defense_streak = 0
        self._ai_counter_window = 0.0
        # Arts: a global throttle between the AI's own casts, plus a planned
        # reaction ('dodge' | 'parry' | None) to an incoming art and an edge flag
        # so the reaction is decided once per opponent art.
        self._ai_art_cooldown = 0.0
        self._ai_art_reaction = None
        self._obs_opp_arting = False
        # Intent set on the ground when the AI jumps meaning to cast an airborne
        # art; consumed near the jump's apex by the JUMPING handler. None = a plain
        # jump / aerial plunge.
        self._ai_air_art = None
        # Selectable difficulty (the AI reads its profile from this). Toggled by
        # main on the enemy; the player's value is unused.
        self.difficulty = DEFAULT_DIFFICULTY
        # The AI's running read of the opponent + edge-tracking for observations.
        self.attention = AttentionModel()
        self._obs_opp_attacking = False
        self._obs_opp_dodging = False

        # State-feedback timer (flash white/red briefly when hit).
        self.hit_flash_timer = 0.0

        # Jump / aerial bookkeeping (Platformer). _airborne is True from launch
        # until touchdown; _air_time gates landing detection (the launch frame
        # still reads on_ground from the previous physics step). _pending_land_lag
        # is the recovery applied on the next touchdown (longer after an aerial slam).
        self._airborne = False
        self._air_time = 0.0
        self._pending_land_lag = LANDING_LAG
        # The aerial slam impulse is applied once, when the plunge's hitbox goes live.
        self._aerial_slammed = False
        # One-shot: set the frame a heavy (aerial slam) landing occurs; read and
        # cleared by main to kick a small camera shake for the impact.
        self.land_shake_event = False
        # Procedural-animation phases (advanced in _tick_timers). _gait_phase drives
        # the walk cycle (legs/arms), _idle_phase the idle breathing sway.
        self._gait_phase = 0.0
        self._idle_phase = 0.0

        # ------------------------------------------------------------------- #
        # Visuals: a multi-part, colour-coded fighter so states read clearly.
        # Everything hangs off self.model_root so the whole character can be
        # tilted/faded (death, dodge i-frames) without disturbing facing
        # (self.rotation_y) or the feet sync (self.world_position).
        # ------------------------------------------------------------------- #
        self.model_root = Entity(parent=self)

        primary = color                      # team colour (blue / red)
        dark = _shade(primary, -0.45)        # legs / arms
        accent = _shade(primary, 0.35)       # sash / shoulders / trim
        bright = _shade(primary, 0.6)        # chest emblem
        skin = _shade(primary, 0.4)

        R = FIGHTER_RADIUS
        # (entity, base_color) for every non-sword part -> turned yellow while
        # STAGGERED, restored to base otherwise. (The sword is never recoloured.)
        self.recolor_parts = []
        self.body_parts = []   # every part -> faded during dodge i-frames

        def part(model, col, pos, scale, **kw):
            # unlit -> the flat colour renders vividly regardless of scene lights
            # (lit/PBR rendering was washing everything out to white).
            e = Entity(parent=self.model_root, model=model, color=col,
                       position=pos, scale=scale, unlit=True, **kw)
            self.body_parts.append(e)
            self.recolor_parts.append((e, col))
            return e

        # Legs: each hangs off a pivot placed at the HIP (top of the leg) so it
        # can swing from the hip joint (same pivot convention as the arms). The
        # leg cube is offset half its height below the pivot, so the visible cube
        # stays exactly where it was (centre y=0.35) while gaining a rotation
        # origin. Animated in _update_sword_visual alongside the arms/sword.
        leg_scale = (R * 0.55, 0.6, R * 0.7)
        leg_len = leg_scale[1]
        self.leg_l_base_pos = Vec3(-0.2, 0.65, 0)
        self.leg_r_base_pos = Vec3(0.2, 0.65, 0)
        self.leg_l_pivot = Entity(parent=self.model_root, position=self.leg_l_base_pos)
        self.leg_l = Entity(parent=self.leg_l_pivot, model='cube', color=dark,
                            position=(0, -leg_len * 0.5, 0), scale=leg_scale, unlit=True)
        self.leg_r_pivot = Entity(parent=self.model_root, position=self.leg_r_base_pos)
        self.leg_r = Entity(parent=self.leg_r_pivot, model='cube', color=dark,
                            position=(0, -leg_len * 0.5, 0), scale=leg_scale, unlit=True)
        self.body_parts.append(self.leg_l)
        self.body_parts.append(self.leg_r)
        self.recolor_parts.append((self.leg_l, dark))
        self.recolor_parts.append((self.leg_r, dark))
        # Upper-body rotation axis at the person's center (a vertical axis
        # through x=0,z=0). The torso assembly hangs off this so a light attack
        # can twist the whole upper body in sync with the sword swing.
        self.torso_pivot = Entity(parent=self.model_root)
        # Torso (primary team colour)
        self.torso = part('cube', primary, (0, 1.05, 0), (R * 1.7, 0.75, R * 0.75))
        # Sash / belt (accent)
        sash = part('cube', accent, (0, 0.78, 0), (R * 1.75, 0.16, R * 0.8))
        # Chest emblem (bright) on the front (+z local, before facing)
        emblem = part('cube', bright, (0, 1.12, R * 0.4), (R * 0.7, 0.32, R * 0.05))
        # Twist these together with the central axis (they sit on the torso).
        for _p in (self.torso, sash, emblem):
            _p.parent = self.torso_pivot
        # Arms: a single elongated rectangular prism per side (no shoulders).
        # Each arm hangs off a pivot placed at the shoulder (top of the prism)
        # so it swings from the shoulder joint, not about its own center. The
        # prism is offset half its length below the pivot. Animated alongside
        # the sword in _update_sword_visual.
        arm_scale = (R * 0.4, 0.8, R * 0.45)
        arm_len = arm_scale[1]
        self.arm_l_pivot = Entity(parent=self.model_root, position=(-R * 1.05, 1.5, 0))
        self.arm_l = Entity(parent=self.arm_l_pivot, model='cube', color=dark,
                            position=(0, -arm_len * 0.5, 0), scale=arm_scale, unlit=True)
        self.arm_r_pivot = Entity(parent=self.model_root, position=(R * 1.05, 1.5, 0))
        self.arm_r = Entity(parent=self.arm_r_pivot, model='cube', color=dark,
                            position=(0, -arm_len * 0.5, 0), scale=arm_scale, unlit=True)
        self.body_parts.append(self.arm_l)
        self.body_parts.append(self.arm_r)
        self.recolor_parts.append((self.arm_l, dark))
        self.recolor_parts.append((self.arm_r, dark))
        # Head: hangs off a pivot placed at the head's CENTRE so it rotates about
        # its own centre (same pivot convention as the arms; the cube sits at the
        # pivot origin rather than offset). Animation logic is handled elsewhere.
        head_size = R * 0.75
        self.head_pivot = Entity(parent=self.model_root, position=(0, 1.62, 0))
        self.head = Entity(parent=self.head_pivot, model='cube', color=skin,
                           position=(0, 0, 0), scale=head_size, unlit=True)
        self.body_parts.append(self.head)
        self.recolor_parts.append((self.head, skin))

        # Sword: a container that pivots at the hilt; blade extends +z (forward).
        self.sword_base_pos = Vec3(R * 0.95, 1.0, 0.2)
        self.sword_base_rot = Vec3(0, 0, 0)
        self.sword = Entity(parent=self.model_root, position=self.sword_base_pos,
                            rotation=self.sword_base_rot)
        sword_len = 1.5
        handle = Entity(parent=self.sword, model='cube', color=ucolor.rgb32(60, 45, 35),
                        position=(0, 0, -0.12), scale=(0.07, 0.07, 0.28), unlit=True)  # handle
        guard = Entity(parent=self.sword, model='cube', color=ucolor.rgb32(200, 170, 70),
                       position=(0, 0, 0.02), scale=(0.32, 0.07, 0.07), unlit=True)    # guard
        self._blade_base_scale = Vec3(0.09, 0.09, sword_len)
        self.blade = Entity(parent=self.sword, model='cube', color=SWORD_COLOR,
                            position=(0, 0, 0.02 + sword_len * 0.5),
                            scale=self._blade_base_scale, unlit=True)           # blade
        self.body_parts.append(self.blade)

        # Character geometry that should respond to scene lighting when the
        # lighting prototype is toggled on. Effects (trail/sparks/glints) are
        # deliberately NOT in here -- they always render unlit so they stay
        # bright regardless of lights.
        self.lit_parts = list(self.body_parts) + [handle, guard]

        # Ghost swords: 4 independent afterimage swords parented to model_root.
        # Each has handle/guard/blade geometry identical to the main sword.
        # Animated freely in the pose ladder; hidden when motion_blur_active=False.
        self.motion_blur_active = False
        self.ghost_swords = []
        for alpha in GHOST_SWORD_ALPHAS:
            gs = Entity(parent=self.model_root, position=self.sword_base_pos,
                        rotation=self.sword_base_rot)
            Entity(parent=gs, model='cube', color=ucolor.rgb32(60, 45, 35),
                   position=(0, 0, -0.12), scale=(0.07, 0.07, 0.28),
                   unlit=True, alpha=alpha)
            Entity(parent=gs, model='cube', color=ucolor.rgb32(200, 170, 70),
                   position=(0, 0, 0.02), scale=(0.32, 0.07, 0.07),
                   unlit=True, alpha=alpha)
            Entity(parent=gs, model='cube', color=SWORD_COLOR,
                   position=(0, 0, 0.02 + sword_len * 0.5),
                   scale=self._blade_base_scale, unlit=True, alpha=alpha)
            gs.enabled = False
            self.ghost_swords.append(gs)

        # Invisible markers at the blade's base and tip (sword-local space).
        # Their world_position is sampled each active frame to drive the trail.
        blade_tip_z = 0.02 + sword_len      # base of blade + full length
        self._trail_root = Entity(parent=self.sword, position=(0, 0, 0.02))
        self._trail_tip = Entity(parent=self.sword, position=(0, 0, blade_tip_z))
        self.sword_trail = SwordTrail()
        self.sparks = SparkSystem()
        self.glints = GlintSystem()
        # Anchor at chest height for the feint "shiny flash" -- a glint on the
        # CHARACTER (vs the blade glint that telegraphs a heavy/charge windup).
        self._body_glint_anchor = Entity(parent=self.model_root, position=(0, 1.1, 0))

        # ------------------------------------------------------------------- #
        # Arts system state.
        # ------------------------------------------------------------------- #
        # Per-art cooldown timers (keyed by ArtType).
        self.art_cooldowns = {atype: 0.0 for atype in ArtType}
        # Current stamina cost per art (decays as combat time accrues).
        self.art_stamina_cost = ART_STAMINA_START
        # Current cooldown base (decays as combat time accrues).
        self.art_cooldown_base = ART_COOLDOWN_START
        # Accumulated combat time for decay ticks.
        self._art_decay_accum = 0.0
        # Active art tracking.
        self.current_art = None          # ArtType or None
        self._art_sub_frame = 0          # 0-5 (A1-A6)
        self._art_frame_timer = 0.0      # time remaining in current sub-frame
        # Harmonic: target position captured at A4 entry.
        self._harmonic_target_pos = None
        # Forward-dash progress for OVERCLOCK (A1 onward).
        self._overclock_entered_art = False
        # Art projectile manager (shared across all arts this fighter fires).
        self.art_manager = art_sprites.ArtProjectileManager()
        # Whether this fighter's art was parried (so we skip on_staggered).
        self._art_was_parried = False
        # Input edge tracking for the two art keys.
        self._prev_art1 = False
        self._prev_art2 = False
        # True while a floating (airborne) art holds the fighter mid-air with
        # gravity suspended. Cleared when the art ends, dropping it from rest.
        self._art_floating = False

        # Initial facing.
        self.rotation_y = _yaw_from_forward(self._forward)

    # --------------------------------------------------------------------- #
    #  Combatant interface (used by combat.resolve_hit)
    # --------------------------------------------------------------------- #
    @property
    def position(self):
        return self.body.position

    @property
    def forward(self):
        return self._forward

    def set_lit(self, on, lit_shader):
        """Toggle scene lighting for this fighter's character geometry. On: bind
        the lit shader and let lights/shadows affect it. Off: revert to the flat
        unlit look. Effects are untouched (always unlit)."""
        for e in self.lit_parts:
            if on:
                e.shader = lit_shader
                e.unlit = False
            else:
                e.shader = None
                e.unlit = True

    def take_damage(self, amount, knockback_vec, stagger_time):
        if self.state == State.DEAD:
            return
        # If combat resolved this as a BLOCKED hit, charge the block-stamina cost.
        # Combat already applied the (block_dmg + chip) damage value; we just pay
        # stamina here. stagger_time>0 for blocked heavies (guard-break) and parries.
        was_blocking = (self.state == State.BLOCKING) or self.is_blocking
        if was_blocking:
            self.stamina = max(0.0, self.stamina - BLOCK_STAMINA_PER_HIT)
            self.regen_delay_timer = STAMINA_REGEN_DELAY
            # A blocked hit that staggers IS a guard-break (blocked lights pass
            # stagger_time=0). Spark burst on the broken blocker -- bigger and
            # chunkier than parry sparks to read as the heavier impact.
            if stagger_time > 0.0:
                fwd = self._forward
                clash = (self.world_position
                         + Vec3(fwd.x, 0.0, fwd.z) * 1.1
                         + Vec3(0.0, 1.1, 0.0))
                self.sparks.burst(clash, direction=Vec3(fwd.x, 0.5, fwd.z),
                                  count=GUARD_BREAK_SPARK_COUNT,
                                  size=GUARD_BREAK_SPARK_SIZE)
        self.hp = max(0.0, self.hp - amount)
        if amount > 0.0:
            self.hit_flash_timer = 0.2          # hue flashes red on any damage
        if knockback_vec is not None:
            self.body.apply_impulse(Vec3(knockback_vec.x, 0.0, knockback_vec.z))
        if self.hp <= 0.0:
            self._enter_dead()
            return
        # Stagger (blocked heavy / parry) disables the fighter. Otherwise a clean
        # hit leaves you free to act -- EXCEPT a hit taken during the pre-commit
        # part of a swing cancels the attack outright (forfeiting the stamina
        # already spent). The pre-commit window is WINDUP + stage-1 ACTIVE (a
        # hitbox-less wind-through); only stage-2 ACTIVE2 (hitbox live) is
        # committed and survives a hit. This pressures attackers to defend
        # instead of throwing unsafe swings.
        #
        # Arts are non-interruptable: damage + knockback (applied above) are felt,
        # but a hit during A1-A6 never staggers or cancels the art.
        if self.state == State.ATTACK_ART:
            return
        if stagger_time > 0.0:
            self._enter_staggered(stagger_time)
        elif amount > 0.0 and self.state in (State.ATTACK_WINDUP, State.ATTACK_ACTIVE):
            if self.feint_pending:
                # Caught mid-feint: a clean hit during a faked swing's pre-commit
                # window GUARD-BREAKS the feinter (long stagger) instead of just
                # cancelling the swing -- the risk that balances the feint. Arm the
                # cooldown and flag the guard-break so main runs the freeze-frame +
                # shake (the blocker-side guard-break uses the same flag).
                self.feint_pending = False
                self.feint_cooldown = FEINT_COOLDOWN
                self.guard_break_event = True
                fwd = self._forward
                clash = (self.world_position
                         + Vec3(fwd.x, 0.0, fwd.z) * 1.1
                         + Vec3(0.0, 1.1, 0.0))
                self.sparks.burst(clash, direction=Vec3(fwd.x, 0.5, fwd.z),
                                  count=GUARD_BREAK_SPARK_COUNT,
                                  size=GUARD_BREAK_SPARK_SIZE)
                self._enter_staggered(FEINT_GUARD_BREAK_STAGGER)
            else:
                # Pre-commit hit cancels the swing. If it was an airborne aerial,
                # recover to JUMPING so the fighter keeps falling + lands properly
                # rather than snapping to an actionable mid-air IDLE.
                self._recover_to_air_or_idle()
        elif amount > 0.0 and self.state == State.PARRYING:
            # Hit during the parry WINDUP (p1) -- not yet an active parry frame, so
            # it lands like a normal hit. Treat it like a cancelled attack windup:
            # damage is applied (above), the parry attempt and its stamina are
            # forfeit, and we drop to idle. (p2/p3 deflect via parry_active instead.)
            self._enter_idle()

    def on_parry_success(self, attack=None):
        # Refund stamina and arm the riposte window. Heavy-type attacks (HEAVY
        # and CHARGE) refund more -- timing a slow, committed swing is rewarded.
        if attack is not None and attack.atype in (AttackType.HEAVY, AttackType.CHARGE):
            refund = PARRY_HEAVY_REFUND
        else:
            refund = PARRY_REFUND
        self.stamina = min(MAX_STAMINA, self.stamina + refund)
        self.riposte_ready = True
        self.riposte_timer = RIPOSTE_WINDOW
        self.parry_feedback_timer = 0.25
        # Spark burst at the clash point: out in front of the parrier at sword
        # height, fanning forward (and a little up) toward the attacker.
        fwd = self._forward
        clash = (self.world_position
                 + Vec3(fwd.x, 0.0, fwd.z) * 1.2
                 + Vec3(0.0, 1.1, 0.0))
        self.sparks.burst(clash, direction=Vec3(fwd.x, 0.5, fwd.z))
        # Don't cut to idle. Mark the parry a success and close the deflect window
        # (one parry per press), but keep the current phase + timer so the parry
        # FOLLOWS THROUGH the rest of its animation (p1->p2->p3). The opponent is
        # staggered (PARRY_STAGGER_TIME) meanwhile, so the parrier still gets to act.
        self.parry_active = False
        self._parry_success = True
        # Safety: if somehow called from outside a parry state, fall back to p1 so
        # the follow-through still plays.
        if self.state not in _PARRY_STATES:
            self.state = State.PARRYING
            self.state_timer = PARRY_P1_DURATION

    def on_staggered(self):
        # If the stagger is from an art being parried, skip it (arts are guaranteed).
        if self.state == State.ATTACK_ART:
            return
        # You got parried.
        self._enter_staggered(PARRY_STAGGER_TIME)

    def on_dodge_success(self):
        # Successive dodge: a dodge that actually evaded an attack cancels MOST of
        # the dodge lockout so the fighter can counter quickly -- but leaves a short
        # punishable end-lag (DODGE_SUCCESS_ENDLAG) so the counter can't come out
        # frame-perfect. Without that lag a fast light's counter lands before the
        # whiffer can act (impossible to parry); the lag widens the telegraph enough
        # to answer it while long-recovery heavies stay punishable. A colour flash
        # signals the window. Drop i-frames now so the end-lag itself is punishable.
        if self.state != State.DODGING:
            return
        self.invulnerable = False
        self.dodge_success_timer = DODGE_SUCCESS_FLASH
        # Small partial refund (< DODGE_STAMINA) -- defending pays a little back.
        self.stamina = min(MAX_STAMINA, self.stamina + DODGE_REFUND)
        # Stay in DODGING for a brief end-lag, then the state machine -> IDLE.
        self.state = State.DODGING
        self.state_timer = DODGE_SUCCESS_ENDLAG

    def on_art_dodge_success(self):
        # Perfect-dodged an ART. Same blue flash as perfect-dodging any normal
        # attack (DODGE_SUCCESS_FLASH drives the colour cue). Unlike on_dodge_
        # success it keeps the i-frames and adds no end-lag, and resets the dodge
        # cooldown so the dodger can immediately evade the next hit of a multi-hit
        # art -- that's the art-dodge reward.
        self.dodge_success_timer = DODGE_SUCCESS_FLASH
        self.dodge_cooldown = 0.0

    def on_guard_break(self, attack):
        # Our heavy broke the opponent's block: refund the heavy's full stamina
        # cost so a successful guard-break is stamina-neutral (rewards offense
        # that punishes turtling).
        self.stamina = min(MAX_STAMINA, self.stamina + attack.stamina)
        self.guard_break_event = True   # consumed by main for the camera shake

    # --------------------------------------------------------------------- #
    #  State entry helpers
    # --------------------------------------------------------------------- #
    def _enter_staggered(self, t):
        self.state = State.STAGGERED
        self.stagger_timer = t
        self.current_attack = None
        self.already_hit = False
        self.is_blocking = False
        self.parry_active = False
        self._parry_recovering = False   # a guard-break ends the forced whiff block
        self.feint_pending = False
        # NB: do NOT clear _airborne here. A fighter can be staggered (parried/
        # guard-broken) mid-air; clearing the flag would disable landing detection
        # for the rest of the fall, stranding it as actionable mid-air when the
        # stagger expires. The landing block / _recover_to_air_or_idle manage it.
        self._aerial_slammed = False
        # A jump interrupted mid-air drops any pending airborne-art intent.
        self._ai_air_art = None
        # Note: keep riposte_ready in case we were parrying when hit by something
        # else? Standard: clear it on getting hit.
        self.riposte_ready = False
        self.riposte_timer = 0.0

    def _enter_dead(self):
        self.state = State.DEAD
        self.is_blocking = False
        self.parry_active = False
        self.invulnerable = False
        self.current_attack = None
        self.feint_pending = False
        self._airborne = False
        self._aerial_slammed = False
        self._ai_air_art = None
        # Killed mid-float: restore gravity so the corpse falls normally.
        if self._art_floating:
            self._art_floating = False
            self.body.gravity_enabled = True
        # Stop horizontal motion.
        self.body.velocity = Vec3(0.0, self.body.velocity.y, 0.0)

    def _enter_whiff_block(self):
        """Forced block served as the post-whiff parry end-lag. The fighter holds
        a guard (is_blocking -> all block mechanics in combat apply) for
        PARRY_WHIFF_RECOVERY and cannot act (gated by _parry_recovering in
        _can_act / dodge / stop_block), then returns to idle."""
        self.state = State.BLOCKING
        self.is_blocking = True
        self._parry_recovering = True
        self.state_timer = PARRY_WHIFF_RECOVERY

    def _enter_idle(self):
        self.state = State.IDLE
        self.state_timer = 0.0
        self.current_attack = None
        self.already_hit = False
        self.is_blocking = False
        self.parry_active = False
        self._parry_recovering = False
        self._dodge_spin = False
        # NB: _airborne is intentionally NOT cleared here (see _enter_staggered);
        # _enter_idle is only reached grounded once _recover_to_air_or_idle routes
        # mid-air recoveries to JUMPING instead.
        self._aerial_slammed = False
        # Clear art state if interrupted.
        if self.current_art is not None:
            self.current_art = None
            self._art_sub_frame = 0
            self._art_frame_timer = 0.0
            self.invulnerable = False
        # Safety net: never leave gravity suspended once we settle to idle.
        if self._art_floating:
            self._art_floating = False
            self.body.gravity_enabled = True

    def _recover_to_air_or_idle(self):
        """End a disabling/cancelled action by returning to the right resting state:
        if the fighter is still physically airborne, drop into JUMPING (so landing
        detection + lag still fire on touchdown) rather than IDLE -- otherwise an
        aerial interrupted/parried/staggered in the air would become fully
        actionable while still falling, with no landing recovery."""
        if self.body.on_ground:
            self._airborne = False
            self._air_time = 0.0
            self._enter_idle()
        else:
            self.state = State.JUMPING
            self._airborne = True
            self.current_attack = None
            self.already_hit = False
            self.is_blocking = False
            self.parry_active = False
            self._dodge_spin = False
            self._aerial_slammed = False

    def _enter_landing(self, lag):
        """Touchdown recovery: brief lockout so a whiffed jump-in / aerial is
        punishable. The landing dust + aerial shockwave are spawned by the caller."""
        self.state = State.LANDING
        self.state_timer = max(0.0, lag)
        self.current_attack = None
        self.already_hit = False
        self._airborne = False
        self._air_time = 0.0
        self._aerial_slammed = False
        self._ai_air_art = None

    # --------------------------------------------------------------------- #
    #  Main per-frame update
    # --------------------------------------------------------------------- #
    def update_fighter(self, dt, opponent):
        if dt <= 0.0:
            return

        # Tick global timers.
        self._tick_timers(dt)
        # Cache for commit-time snapping (start_attack/start_art).
        self._opponent = opponent

        # Lock-on: rotate forward toward opponent (unless dead, or committed to an
        # attack). The committed frames of a swing (ACTIVE/ACTIVE2/RECOVERY) and an
        # art lock the facing, so circling a swinging fighter can beat it. But the
        # WINDUP (startup/telegraph) still re-aims at WINDUP_TURN_SPEED -- without
        # this, an opponent can simply circle out of a punish's fixed arc before the
        # blade ever commits. Exceptions that fully track: the CHARGE attack and the
        # HARMONIC art.
        tracking_attack = (
            (self.current_attack is not None
             and self.current_attack.atype == AttackType.CHARGE)
            or self.current_art == ArtType.HARMONIC
        )
        free_turn = (self.state not in _ATTACK_STATES) or tracking_attack
        windup_aim = (self.state == State.ATTACK_WINDUP) and not tracking_attack
        if (self.state != State.DEAD
                and (free_turn or windup_aim)
                and opponent is not None and opponent.state != State.DEAD):
            to_opp = Vec3(opponent.position.x - self.position.x, 0.0,
                          opponent.position.z - self.position.z)
            if _xz_len(to_opp) > 1e-4:
                target_fwd = _xz_unit(to_opp)
                turn = WINDUP_TURN_SPEED if windup_aim else TURN_SPEED
                t = min(1.0, turn * dt)
                self._forward = _lerp_dir(self._forward, target_fwd, t)
                self.rotation_y = _yaw_from_forward(self._forward)

        # State-machine sub-timer (windup/active/recovery/parry/dodge).
        self._advance_state_machine(dt, opponent)

        # Movement / actions only if alive and not staggered/dead.
        if self.state not in (State.STAGGERED, State.DEAD):
            if self.is_player:
                self.handle_input(dt, opponent)
            else:
                self.ai_think(dt, opponent)
        else:
            # Zero horizontal acceleration while staggered/dead (let physics damp).
            pass

        # Sync visual to physics body (feet position).
        self.world_position = self.body.position

        # Visual sword pose + body state feedback.
        self._update_sword_visual()
        self._update_body_visual()
        self._update_trail(dt)
        self.sparks.update(dt)
        self.glints.update(dt)
        # Art projectiles are updated by the manager in main.py (so both fighters
        # share the same opponent reference). See main.update().

    # --------------------------------------------------------------------- #
    #  Timer ticking
    # --------------------------------------------------------------------- #
    def _tick_timers(self, dt):
        # Stamina regen delay & regen. Holding a block suspends regen entirely --
        # a raised guard only refills once you drop it (taking a blocked hit also
        # arms the regen delay, so a guard under pressure never recovers).
        if self.regen_delay_timer > 0.0:
            self.regen_delay_timer = max(0.0, self.regen_delay_timer - dt)
        elif (self.state not in (State.DEAD, State.BLOCKING)
                and self.stamina < MAX_STAMINA):
            self.stamina = min(MAX_STAMINA, self.stamina + STAMINA_REGEN * dt)

        # Riposte window.
        if self.riposte_timer > 0.0:
            self.riposte_timer -= dt
            if self.riposte_timer <= 0.0:
                self.riposte_timer = 0.0
                self.riposte_ready = False

        # Dodge cooldown.
        if self.dodge_cooldown > 0.0:
            self.dodge_cooldown = max(0.0, self.dodge_cooldown - dt)

        # Feint cooldown (blocks back-to-back feinted swings).
        if self.feint_cooldown > 0.0:
            self.feint_cooldown = max(0.0, self.feint_cooldown - dt)

        # Post-feint follow-up window (AI).
        if self._ai_feint_followup > 0.0:
            self._ai_feint_followup = max(0.0, self._ai_feint_followup - dt)
        # Post-feint bait delay (AI): counts down before the held follow-up fires.
        if self._ai_feint_delay > 0.0:
            self._ai_feint_delay = max(0.0, self._ai_feint_delay - dt)

        # Counter window: armed when the AI declines a defense to punish instead.
        if self._ai_counter_window > 0.0:
            self._ai_counter_window = max(0.0, self._ai_counter_window - dt)

        # Stagger timer.
        if self.stagger_timer > 0.0:
            self.stagger_timer -= dt
            if self.stagger_timer <= 0.0:
                self.stagger_timer = 0.0
                if self.state == State.STAGGERED:
                    # Recover to JUMPING (not IDLE) if the stagger expired while
                    # still falling -- e.g. a plunge parried near apex (stagger
                    # 0.3s < remaining ~0.5s fall).
                    self._recover_to_air_or_idle()

        # Parry feedback (cosmetic).
        if self.parry_feedback_timer > 0.0:
            self.parry_feedback_timer = max(0.0, self.parry_feedback_timer - dt)

        # Hit flash (cosmetic).
        if self.hit_flash_timer > 0.0:
            self.hit_flash_timer = max(0.0, self.hit_flash_timer - dt)

        # Successive-dodge flash (cosmetic).
        if self.dodge_success_timer > 0.0:
            self.dodge_success_timer = max(0.0, self.dodge_success_timer - dt)

        # AI attack cooldown.
        if self._ai_attack_cooldown > 0.0:
            self._ai_attack_cooldown = max(0.0, self._ai_attack_cooldown - dt)

        # AI art cooldown (throttles how often the AI unleashes its own arts).
        if self._ai_art_cooldown > 0.0:
            self._ai_art_cooldown = max(0.0, self._ai_art_cooldown - dt)

        # Art cooldowns (per-art, ticked down each frame).
        for atype in ArtType:
            if self.art_cooldowns[atype] > 0.0:
                self.art_cooldowns[atype] = max(0.0, self.art_cooldowns[atype] - dt)

        # Art stamina/cooldown decay: every ART_DECAY_INTERVAL seconds, decrease.
        self._art_decay_accum += dt
        while self._art_decay_accum >= ART_DECAY_INTERVAL:
            self._art_decay_accum -= ART_DECAY_INTERVAL
            if self.art_stamina_cost > ART_STAMINA_MIN:
                self.art_stamina_cost = max(ART_STAMINA_MIN,
                                            self.art_stamina_cost - ART_STAMINA_DECAY_AMOUNT)
            if self.art_cooldown_base > ART_COOLDOWN_MIN:
                self.art_cooldown_base = max(ART_COOLDOWN_MIN,
                                             self.art_cooldown_base - ART_COOLDOWN_DECAY_AMOUNT)

        # 360-spin progress for a stationary (no-input) dodge.
        if self._dodge_spin and self.state == State.DODGING:
            self._dodge_spin_t += dt

        # Procedural-animation phases. The gait cadence scales with ground speed so
        # a run steps faster than a walk; the idle phase ticks freely for breathing.
        speed_xz = math.hypot(self.body.velocity.x, self.body.velocity.z)
        self._gait_phase += dt * (GAIT_FREQ_BASE + speed_xz * GAIT_FREQ_PER_SPEED)
        self._idle_phase += dt

    # --------------------------------------------------------------------- #
    #  State machine for attack/parry/dodge windows
    # --------------------------------------------------------------------- #
    def _resolve_active_hit(self, opponent):
        """Try a single hit while in an active window. Shared by both attack
        stages so a swing still lands at most one hit across stage 1 + 2."""
        if not self.already_hit and opponent is not None and opponent.state != State.DEAD:
            result = combat.resolve_hit(self, opponent, self.current_attack)
            # Only mark "swing used" once we either land or whiff a real check.
            # Any non-MISSED result consumed the swing; MISSED keeps the hitbox
            # live so a moving target can still be clipped mid-swing.
            if result != combat.HitResult.MISSED:
                self.already_hit = True
                # Riposte consumed if it triggered the bonus.
                if self.riposte_ready and result == combat.HitResult.HIT:
                    self.riposte_ready = False
                    self.riposte_timer = 0.0

    def _advance_state_machine(self, dt, opponent):
        if self.state_timer > 0.0:
            self.state_timer -= dt

        # Airborne bookkeeping + landing detection. Covers a plain jump (State.
        # JUMPING) AND an aerial plunge (which reuses the ATTACK_* states while
        # _airborne stays True). _air_time gates out the launch frame, which still
        # reads on_ground from the previous physics step.
        if self._airborne:
            self._air_time += dt
            landed = (self.body.on_ground and self._air_time >= MIN_AIRTIME
                      and self.body.velocity.y <= 1e-3)
            if landed:
                if self.state == State.JUMPING:
                    self._spawn_land_dust(heavy=False)
                    self._enter_landing(self._pending_land_lag)
                elif self.state in (State.ATTACK_WINDUP, State.ATTACK_ACTIVE,
                                    State.ATTACK_ACTIVE2, State.ATTACK_RECOVERY):
                    # Aerial plunge touched down mid-swing: kick up a shockwave and
                    # clear the airborne flag, but stay in the swing so the hit still
                    # resolves; the attack's own recovery serves as the landing lag.
                    self._spawn_land_dust(heavy=True)
                    self._airborne = False
                    self._air_time = 0.0
                else:
                    self._airborne = False
                    self._air_time = 0.0

        st = self.state
        if st == State.ATTACK_WINDUP:
            if self.state_timer <= 0.0:
                # Enter ACTIVE.
                self.state = State.ATTACK_ACTIVE
                self.state_timer = self.current_attack.active
                self.already_hit = False
        elif st == State.ATTACK_ACTIVE:
            # Stage 1: wind-through only, no hitbox (the hit lands in stage 2).
            if self.state_timer <= 0.0:
                # If we were parried/staggered during ACTIVE, state changed already.
                if self.state == State.ATTACK_ACTIVE and self.feint_pending:
                    # FEINT resolves: skip the committed stage-2 entirely, pop the
                    # body "shiny flash" (the reveal that the swing was faked), arm
                    # the cooldown and snap to IDLE -- free to act again. No hitbox
                    # ever goes live, so a feint deals no damage. Notify observers.
                    self.feint_pending = False
                    self.feint_cooldown = FEINT_COOLDOWN
                    self.feint_event = True
                    # Open the AI's bait-then-strike window (player ignores this).
                    # Half the time the AI BAITS: it holds the follow-up an extra
                    # beat so the post-feint timing isn't fixed (you can't pre-load
                    # a parry/dodge on it). Extend the window so the delayed swing
                    # still has time to come out.
                    self._ai_feint_followup = FEINT_FOLLOWUP_WINDOW
                    if (not self.is_player
                            and random.random() < FEINT_FOLLOWUP_DELAY_CHANCE):
                        self._ai_feint_delay = FEINT_FOLLOWUP_DELAY
                        self._ai_feint_followup += FEINT_FOLLOWUP_DELAY
                    else:
                        self._ai_feint_delay = 0.0
                    self.glints.spawn(self._body_glint_anchor, size=GLINT_SIZE * 1.7)
                    # Aerials can't be feinted, so this is always grounded; use the
                    # height-aware recovery anyway to keep the "_enter_idle only
                    # grounded" invariant robust.
                    self._recover_to_air_or_idle()
                    # Feinting immediately re-orients the fighter to face the
                    # opponent (a hard snap, not the gradual lock-on lerp).
                    if opponent is not None and opponent.state != State.DEAD:
                        to_opp = Vec3(opponent.position.x - self.position.x, 0.0,
                                      opponent.position.z - self.position.z)
                        if _xz_len(to_opp) > 1e-4:
                            self._forward = _xz_unit(to_opp)
                            self.rotation_y = _yaw_from_forward(self._forward)
                elif self.state == State.ATTACK_ACTIVE:
                    self.state = State.ATTACK_ACTIVE2
                    self.state_timer = self.current_attack.active2
                    # Charge: lunge forward as the hitbox goes live, so the dash
                    # carries the strike across the gap (closes more than a dodge).
                    # Only when lunge intent was set (player held W / AI gap-close);
                    # otherwise the charge is a stationary strike.
                    if (self.current_attack.atype == AttackType.CHARGE
                            and self._charge_lunge):
                        self.body.apply_impulse(Vec3(
                            self._forward.x * CHARGE_DASH_IMPULSE, 0.0,
                            self._forward.z * CHARGE_DASH_IMPULSE))
                    # Aerial plunge: drive the slam DOWN as the hitbox goes live, so
                    # the dive crashes onto the target (carried by the big downward
                    # vertical_reach). Only while still airborne, and only once.
                    elif (self.current_attack.atype == AttackType.AERIAL
                            and self._airborne and not self._aerial_slammed):
                        self._aerial_slammed = True
                        self.body.apply_impulse(Vec3(0.0, -AERIAL_SLAM_IMPULSE, 0.0))
        elif st == State.ATTACK_ACTIVE2:
            # Stage 2: hitbox live.
            self._resolve_active_hit(opponent)
            if self.state_timer <= 0.0:
                if self.state == State.ATTACK_ACTIVE2:
                    self.state = State.ATTACK_RECOVERY
                    self.state_timer = self.current_attack.recovery
        elif st == State.ATTACK_RECOVERY:
            if self.state_timer <= 0.0:
                # An aerial whose recovery elapses while still airborne holds the
                # recovery pose until touchdown (the landing block above converts
                # it), so it never drops back to IDLE mid-air.
                if self._airborne:
                    self.state_timer = 0.0
                else:
                    self._enter_idle()
        elif st == State.JUMPING:
            # Rising/falling; landing is handled by the airborne block above and
            # air-steering by movement. Nothing to tick here.
            pass
        elif st == State.LANDING:
            if self.state_timer <= 0.0:
                self._enter_idle()
        elif st == State.PARRYING:
            # Phase 1 (p1) is the windup -- no deflect. When it ends, advance to p2
            # and OPEN the deflect window (active through p2 and p3).
            if self.state_timer <= 0.0:
                self.state = State.PARRYING2
                self.state_timer = PARRY_P2_DURATION
                self.parry_active = True
        elif st == State.PARRYING2:
            # Phase 2 (p2) done -> advance to p3.
            if self.state_timer <= 0.0:
                self.state = State.PARRYING3
                self.state_timer = PARRY_P3_DURATION
        elif st == State.PARRYING3:
            if self.state_timer <= 0.0:
                if self.parry_active and not self._parry_success:
                    # WHIFF: the deflect window expired without catching anything.
                    # Drop into a forced BLOCK end-lag -- all blocking mechanics
                    # apply (reduced damage, chip, block stamina, heavy/charge
                    # guard-break stagger), and the fighter can't act until it
                    # elapses. A mistimed/habitual parry is thus punishable, but
                    # softened to a guard rather than a fully exposed stall.
                    self.parry_active = False
                    self.parry_whiff_event = True
                    self._enter_whiff_block()
                else:
                    # Successful parry followed through -> clean exit, no end-lag.
                    self.parry_active = False
                    self._enter_idle()
        elif st == State.DODGING:
            # Drop i-frames partway through.
            elapsed = DODGE_DURATION - max(0.0, self.state_timer)
            if elapsed >= DODGE_IFRAMES:
                self.invulnerable = False
            if self.state_timer <= 0.0:
                self.invulnerable = False
                self._enter_idle()
        elif st == State.BLOCKING:
            if self._parry_recovering:
                # Forced post-whiff block end-lag: blocking mechanics apply while
                # it runs, but the fighter can't act until it elapses, then idle.
                if self.state_timer <= 0.0:
                    self._parry_recovering = False
                    self.is_blocking = False
                    self._enter_idle()
            else:
                # Voluntary block is held; exit handled by handle_input/ai_think.
                pass
        elif st == State.ATTACK_ART:
            self._advance_art_state_machine(dt, opponent)
        elif st in (State.IDLE, State.MOVING, State.STAGGERED, State.DEAD):
            pass

    # --------------------------------------------------------------------- #
    #  Movement
    # --------------------------------------------------------------------- #
    def _can_move(self):
        return self.state in (State.IDLE, State.MOVING)

    def _apply_move_intent(self, dt, intent_xz: Vec3):
        """intent_xz is desired velocity direction (xz, magnitude 0..1).
        Builds target velocity and accelerates body horizontal velocity toward it.
        On the ground this uses full MOVE_SPEED/MOVE_ACCEL; while airborne (JUMPING)
        it uses the reduced AIR_MOVE_SPEED/AIR_CONTROL so a jump keeps most of its
        launch momentum (a commitment) but can still be steered a little."""
        if self.state == State.JUMPING:
            speed, accel = AIR_MOVE_SPEED, MOVE_ACCEL * AIR_CONTROL
        elif self._can_move():
            speed, accel = MOVE_SPEED, MOVE_ACCEL
        else:
            return
        mag = _xz_len(intent_xz)
        if mag > 1.0:
            intent_xz = Vec3(intent_xz.x / mag, 0.0, intent_xz.z / mag)
            mag = 1.0
        target_vx = intent_xz.x * speed
        target_vz = intent_xz.z * speed
        # Accelerate toward target.
        max_delta = accel * dt
        dvx = target_vx - self.body.velocity.x
        dvz = target_vz - self.body.velocity.z
        dmag = math.hypot(dvx, dvz)
        if dmag > max_delta and dmag > 1e-6:
            dvx *= max_delta / dmag
            dvz *= max_delta / dmag
        self.body.velocity = Vec3(
            self.body.velocity.x + dvx,
            self.body.velocity.y,
            self.body.velocity.z + dvz,
        )
        if mag > 0.05 and self.state == State.IDLE:
            self.state = State.MOVING
        elif mag <= 0.05 and self.state == State.MOVING:
            self.state = State.IDLE

    # --------------------------------------------------------------------- #
    #  Actions
    # --------------------------------------------------------------------- #
    def _spend_stamina(self, amount):
        self.stamina = max(0.0, self.stamina - amount)
        self.regen_delay_timer = STAMINA_REGEN_DELAY

    def _can_act(self):
        # _parry_recovering = serving the forced post-whiff block: stalled, no acting.
        return (self.state in (State.IDLE, State.MOVING, State.BLOCKING)
                and not self._parry_recovering)

    def _face_opponent(self):
        """Hard-snap the facing toward the cached opponent (no lerp). Called the
        instant an attack/art commits so a punish always starts aimed -- crucial
        after an omnidirectional art (e.g. CENTIPEDE) leaves the user facing away
        from a now-staggered opponent standing behind them."""
        opp = self._opponent
        if opp is None or opp.state == State.DEAD:
            return
        to_opp = Vec3(opp.position.x - self.position.x, 0.0,
                      opp.position.z - self.position.z)
        if _xz_len(to_opp) > 1e-4:
            self._forward = _xz_unit(to_opp)
            self.rotation_y = _yaw_from_forward(self._forward)

    def start_attack(self, atype, lunge=False):
        # An AERIAL is thrown out of a jump, so it is allowed from JUMPING (which
        # _can_act() excludes); every other attack needs a grounded actionable
        # state. An aerial can ONLY be thrown airborne, and a ground attack can
        # never be an aerial.
        airborne_aerial = (atype == AttackType.AERIAL and self.state == State.JUMPING)
        if atype == AttackType.AERIAL and not airborne_aerial:
            return False
        if not airborne_aerial and not self._can_act():
            return False
        attack = ATTACKS[atype]
        if self.stamina < attack.stamina:
            return False
        # Drop block on attack.
        self.is_blocking = False
        self._charge_lunge = lunge
        self.feint_pending = False      # a fresh swing isn't feinted yet
        self._spend_stamina(attack.stamina)
        # Light attacks alternate slash direction each swing (right/left/...).
        if atype == AttackType.LIGHT:
            self._light_swing_left = not self._light_swing_left
        self.current_attack = attack
        self.already_hit = False
        self.state = State.ATTACK_WINDUP
        self.state_timer = attack.windup
        # Aerial: stay airborne through the plunge; arm the heavier touchdown lag
        # and re-arm the one-shot slam impulse (fired when the hitbox goes live).
        if airborne_aerial:
            self._airborne = True
            self._aerial_slammed = False
            self._pending_land_lag = AERIAL_LANDING_LAG
        # Heavy/charge/aerial: pop a star glint on the blade to read as shiny metal.
        if atype in (AttackType.HEAVY, AttackType.CHARGE, AttackType.AERIAL):
            self.glints.spawn(self._trail_tip)
        # Snap to face the opponent at commit so the swing starts aimed (WINDUP
        # tracking then keeps it honest against a circler).
        self._face_opponent()
        return True

    def jump(self):
        """Launch straight up. Only from a grounded actionable locomotion state;
        costs stamina. Sets _airborne so the state machine's landing detection takes
        over. Mass is 1.0, so the impulse magnitude is the launch velocity."""
        if self.state not in (State.IDLE, State.MOVING):
            return False
        if not self.body.on_ground:
            return False
        if self.stamina < JUMP_STAMINA:
            return False
        self._spend_stamina(JUMP_STAMINA)
        self.is_blocking = False
        self.state = State.JUMPING
        self._airborne = True
        self._air_time = 0.0
        self._pending_land_lag = LANDING_LAG
        # Clear on_ground now so the launch frame isn't mistaken for a landing
        # before the physics step re-evaluates it.
        self.body.on_ground = False
        self.body.apply_impulse(Vec3(0.0, JUMP_IMPULSE, 0.0))
        return True

    def _spawn_land_dust(self, heavy=False):
        """Kick up a burst of particles at the feet on touchdown. A heavy (aerial
        slam) landing throws a bigger, chunkier shockwave."""
        feet = self.world_position
        pos = Vec3(feet.x, feet.y + 0.12, feet.z)
        count = GUARD_BREAK_SPARK_COUNT if heavy else max(8, SPARK_COUNT // 2)
        size = GUARD_BREAK_SPARK_SIZE if heavy else SPARK_SIZE
        self.sparks.burst(pos, direction=Vec3(0.0, 0.7, 0.0),
                          count=count, size=size)
        if heavy:
            self.land_shake_event = True

    def start_art(self, art_type):
        """Attempt to execute an art. Ground arts trigger from IDLE/MOVING/BLOCKING
        (same as normal attacks); airborne arts (KAGURA/HARMONIC) trigger from a
        jump. Cannot cancel attacks. Arts cannot be feinted. Returns True on success."""
        airborne = art_type in _AIR_ARTS
        if airborne:
            # An airborne art can only be thrown while actually in the air (JUMPING),
            # and not while stalled in the forced post-whiff block.
            if self.state != State.JUMPING or self._parry_recovering:
                return False
        elif not self._can_act():
            return False
        cost = self.art_stamina_cost
        if self.stamina < cost:
            return False
        cd = self.art_cooldowns.get(art_type, 0.0)
        if cd > 0.0:
            return False
        self.is_blocking = False
        self._spend_stamina(cost)
        # Set art cooldown. The two variants on a key share it (one art, two forms).
        self.art_cooldowns[art_type] = self.art_cooldown_base
        pair = _ART_PAIR.get(art_type)
        if pair is not None:
            self.art_cooldowns[pair] = self.art_cooldown_base
        # Airborne cast: freeze in place and suspend gravity so the fighter floats
        # for the art's duration. _enter_art_done re-enables gravity and drops it
        # from rest. _airborne stays armed so landing detection fires on touchdown.
        if airborne:
            self._art_floating = True
            self._airborne = True
            self.body.gravity_enabled = False
            self.body.velocity = Vec3(0.0, 0.0, 0.0)
        self.current_art = art_type
        self._art_sub_frame = 0
        durations = ART_FRAME_DURATIONS[art_type]
        self._art_frame_timer = durations[0]
        self.state = State.ATTACK_ART
        # Arts are non-interruptable, NOT invulnerable: the fighter can still be
        # hit and take damage during A1-A6, but a hit won't cancel the art.
        self.invulnerable = False
        self._overclock_entered_art = (art_type == ArtType.OVERCLOCK)
        self._harmonic_target_pos = None
        # A1 telegraph: a BIG double flash at the sword tip (no sparks) -- the second
        # pop fires just as the first fades, reading as a two-beat burst.
        self.glints.spawn(self._trail_tip, size=GLINT_SIZE * 2.0)
        self.glints.spawn(self._trail_tip, size=GLINT_SIZE * 2.0,
                          delay=GLINT_LIFE * 0.6)
        # Snap to face the opponent at commit. Directional arts (KAGURA/HARMONIC/
        # OVERCLOCK) fire where the user faces, so this aims them; omnidirectional
        # CENTIPEDE is unaffected by facing but the snap leaves the user oriented
        # for the follow-up punish.
        self._face_opponent()
        return True

    def _advance_art_state_machine(self, dt, opponent):
        """Tick the art sub-frame state machine."""
        if self.state != State.ATTACK_ART or self.current_art is None:
            return
        self._art_frame_timer -= dt
        if self._art_frame_timer > 0.0:
            # Still in current sub-frame; handle per-frame effects.
            self._art_frame_ongoing(dt, opponent)
            return
        # Sub-frame complete -> advance to next, or finish.
        self._art_sub_frame += 1
        durations = ART_FRAME_DURATIONS[self.current_art]
        if self._art_sub_frame >= 6:
            # Art complete.
            self._enter_art_done()
            return
        self._art_frame_timer = durations[self._art_sub_frame]
        # On entering specific sub-frames, spawn projectiles.
        self._art_frame_enter(opponent)

    def _art_frame_ongoing(self, dt, opponent):
        """Per-frame effects while in a specific art sub-frame."""
        if self._art_floating:
            # Airborne art: pin the fighter mid-air (gravity is already suspended).
            self.body.velocity = Vec3(0.0, 0.0, 0.0)
            return
        if self.current_art == ArtType.OVERCLOCK:
            # Slowly drift forward during execution.
            fwd = self._forward
            self.body.velocity = Vec3(
                fwd.x * ART_OVERCLOCK_MOVE_SPEED,
                self.body.velocity.y,
                fwd.z * ART_OVERCLOCK_MOVE_SPEED,
            )

    def _art_frame_enter(self, opponent):
        """Called when entering a new sub-frame. Spawn projectiles at A4 (sub_frame==3),
        except OVERCLOCK, whose crescent spawns at A5 (sub_frame==4)."""
        sf = self._art_sub_frame
        art = self.current_art
        if sf == 3:   # A4 (0-indexed = 3)
            if art == ArtType.CENTIPEDE:
                self.art_manager.spawn_centipede(self)
            elif art == ArtType.KAGURA:
                self.art_manager.spawn_kagura(self)
            elif art == ArtType.HARMONIC:
                # Capture target position at A4 entry.
                if opponent is not None:
                    self._harmonic_target_pos = Vec3(opponent.position)
                else:
                    fwd = self._forward
                    pos = self.position
                    self._harmonic_target_pos = Vec3(pos.x + fwd.x * 8.0, pos.y, pos.z + fwd.z * 8.0)
                self.art_manager.spawn_harmonic(self, self._harmonic_target_pos)
        elif sf == 2:   # A5 (0-indexed = 4)
            if art == ArtType.OVERCLOCK:
                self.art_manager.spawn_overclock(self)

    def _enter_art_done(self):
        """Art sequence finished."""
        self.current_art = None
        self._art_sub_frame = 0
        self._art_frame_timer = 0.0
        self.invulnerable = False
        self._overclock_entered_art = False
        # End an airborne float: restore gravity and start the fall from rest
        # (velocity 0), then route to the right resting state -- JUMPING if still
        # airborne (so landing detection + lag fire on touchdown), else IDLE.
        if self._art_floating:
            self._art_floating = False
            self.body.gravity_enabled = True
            self.body.velocity = Vec3(0.0, 0.0, 0.0)
            self._recover_to_air_or_idle()
            return
        self._enter_idle()

    def feint(self):
        """Commit a feint on the current swing. Only valid during the pre-commit
        window (WINDUP or stage-1 ACTIVE), once per cooldown, and not already
        feinting. The fake resolves in the state machine at the stage-1 -> stage-2
        boundary (skips the hitbox, flashes the body, snaps to IDLE). Stamina was
        already spent at attack start and is NOT refunded."""
        if self.state not in (State.ATTACK_WINDUP, State.ATTACK_ACTIVE):
            return False
        if self.feint_pending or self.feint_cooldown > 0.0:
            return False
        if self.current_attack is None:
            return False
        # An aerial plunge is a committed dive -- not feintable. (The AI already
        # never feints it via FEINT_TYPE_MULT[AERIAL]=0; this also blocks a player
        # from feinting an aerial, which would otherwise resolve to a mid-air IDLE.)
        if self.current_attack.atype == AttackType.AERIAL:
            return False
        self.feint_pending = True
        return True

    def dodge(self, direction: Vec3 = None):
        if self.state not in (State.IDLE, State.MOVING, State.BLOCKING):
            return False
        if not self.body.on_ground:
            return False                 # no air-dodging -- dodge is a ground move
        if self._parry_recovering:   # can't dodge-cancel the forced post-whiff block
            return False
        if self.dodge_cooldown > 0.0:
            return False
        if self.stamina < DODGE_STAMINA:
            return False
        self._spend_stamina(DODGE_STAMINA)
        self.is_blocking = False
        self.state = State.DODGING
        self.state_timer = DODGE_DURATION
        self.invulnerable = True
        self.dodge_cooldown = DODGE_DURATION + DODGE_COOLDOWN
        # No directional input -> dodge in place and spin the body 360 (a telegraph
        # for the i-frames). A held direction -> dash that way (no spin).
        if direction is None or _xz_len(direction) < 1e-4:
            self._dodge_spin = True
            self._dodge_spin_t = 0.0
        else:
            self._dodge_spin = False
            d = _xz_unit(direction)
            self.body.apply_impulse(Vec3(d.x * DODGE_IMPULSE, 0.0, d.z * DODGE_IMPULSE))
        return True

    def start_parry(self):
        if not self._can_act():
            return False
        if self.stamina < PARRY_STAMINA:
            return False
        self.is_blocking = False
        self._spend_stamina(PARRY_STAMINA)
        self.state = State.PARRYING
        # p1 is a WINDUP: NOT an active parry frame. A hit landing during p1 is a
        # normal hit (deals damage, see take_damage). The deflect window opens at
        # p2 (see the PARRYING->PARRYING2 transition).
        self.parry_active = False
        self._parry_success = False
        self._parry_recovering = False
        self.state_timer = PARRY_P1_DURATION
        return True

    def start_block(self):
        if self.state not in (State.IDLE, State.MOVING) and self.state not in _PARRY_STATES:
            return False
        # If transitioning from parrying (held), turn parry off.
        self.parry_active = False
        self.state = State.BLOCKING
        self.is_blocking = True
        self.state_timer = 0.0
        return True

    def stop_block(self):
        if self._parry_recovering:
            return   # the forced post-whiff block can't be released early
        if self.state == State.BLOCKING:
            self.is_blocking = False
            self._enter_idle()

    # --------------------------------------------------------------------- #
    #  Player input
    # --------------------------------------------------------------------- #
    def _read_movement_intent(self, opponent):
        """WASD relative to the lock-on facing.
        W = toward opponent (forward), S = backward, A = strafe left, D = strafe right.
        """
        fx, fz = self._forward.x, self._forward.z
        # Right vector = forward rotated -90deg around y: (fz, 0, -fx).
        rx, rz = fz, -fx
        ix = 0.0
        iz = 0.0
        if held_keys['w']:
            ix += fx; iz += fz
        if held_keys['s']:
            ix -= fx; iz -= fz
        if held_keys['a']:
            ix -= rx; iz -= rz
        if held_keys['d']:
            ix += rx; iz += rz
        return Vec3(ix, 0.0, iz)

    def handle_input(self, dt, opponent):
        # Movement.
        intent = self._read_movement_intent(opponent)
        self._apply_move_intent(dt, intent)

        # Read held_keys; do edge detection here.
        light = bool(held_keys['j'])
        parryblock = bool(held_keys['f'])
        heavy = bool(held_keys['r'])
        charge = bool(held_keys['t'])
        dodge_key = bool(held_keys['shift']) or bool(held_keys['q'])
        feint_key = bool(held_keys['i'])
        jump_key = bool(held_keys['space'])

        # Jump: edge on SPACE (only takes when grounded -- jump() gates it).
        if jump_key and not self._prev_jump:
            self.jump()

        if self.state == State.JUMPING:
            # Airborne: J or R throws the aerial plunge (a single committed dive).
            if (light and not self._prev_light) or (heavy and not self._prev_heavy):
                self.start_attack(AttackType.AERIAL)
        else:
            # Grounded attacks.
            # Light attack: edge on J.
            if light and not self._prev_light:
                self.start_attack(AttackType.LIGHT)
            # Heavy attack: edge on R.
            if heavy and not self._prev_heavy:
                self.start_attack(AttackType.HEAVY)
            # Charge (dash) attack: edge on T. Lunges only if pressing forward (W);
            # any other / no movement key -> a stationary charge.
            if charge and not self._prev_charge:
                self.start_attack(AttackType.CHARGE, lunge=bool(held_keys['w']))
        # Dodge: edge on SHIFT/Q; direction = current move intent or backward.
        if dodge_key and not self._prev_dodge:
            dir_v = intent if _xz_len(intent) > 0.1 else None
            self.dodge(dir_v)
        # Feint: edge on I; only takes during a swing's pre-commit window.
        if feint_key and not self._prev_feint:
            self.feint()

        # F: tap = parry, hold-through-window = block.
        if parryblock and not self._prev_parryblock:
            # Begin: trigger parry.
            self.start_parry()
            self._parryblock_held_time = 0.0
        elif parryblock and self._prev_parryblock:
            self._parryblock_held_time += dt
            # Let the parry play out its full three-phase animation (the deflect
            # window spans all of it); only once it has returned to neutral does a
            # still-held F settle into BLOCKING.
            if self.state in (State.IDLE, State.MOVING):
                self.start_block()
        elif not parryblock and self._prev_parryblock:
            # Released F.
            self.stop_block()
            self._parryblock_held_time = 0.0

        # Art keys: two buttons, each with a ground and an air form. On the ground
        # 1 -> CENTIPEDE, 2 -> OVERCLOCK; in the air 1 -> KAGURA, 2 -> HARMONIC.
        art1 = bool(held_keys['1'])
        art2 = bool(held_keys['2'])
        grounded = self.body.on_ground
        if art1 and not self._prev_art1:
            self.start_art(GROUND_ART[1] if grounded else AIR_ART[1])
        if art2 and not self._prev_art2:
            self.start_art(GROUND_ART[2] if grounded else AIR_ART[2])

        self._prev_light = light
        self._prev_parryblock = parryblock
        self._prev_heavy = heavy
        self._prev_charge = charge
        self._prev_dodge = dodge_key
        self._prev_feint = feint_key
        self._prev_jump = jump_key
        self._prev_art1 = art1
        self._prev_art2 = art2

    # --------------------------------------------------------------------- #
    #  AI
    # --------------------------------------------------------------------- #
    def _ai_intensity(self, opponent):
        """Dynamic battle tempo driven by the AI's stamina ADVANTAGE over the
        opponent. >1 when the AI has more stamina (it presses + defends harder to
        throw the player off), <1 when it's gassed (it eases off to recover).
        1.0 at parity, so an even fight plays at the baseline skill values."""
        opp_stam = getattr(opponent, 'stamina', MAX_STAMINA)
        adv = (self.stamina - opp_stam) / MAX_STAMINA       # -1 .. +1
        return max(0.6, min(1.5, 1.0 + adv))

    def _wall_escape(self, move_intent, opp_dir):
        """If the AI is near the arena wall and `move_intent` points into it (a
        straight retreat backing into the boundary), redirect to a tangential
        escape arc: run ALONG the wall and AROUND the opponent (plus a little
        inward) toward open space, instead of pinning itself in the corner and
        eating hits. Returns the (possibly redirected) intent at the same speed."""
        pos = self.body.position
        center_dist = math.hypot(pos.x, pos.z)
        if center_dist < ARENA_RADIUS - AI_WALL_MARGIN or center_dist < 1e-4:
            return move_intent
        outward = Vec3(pos.x / center_dist, 0.0, pos.z / center_dist)  # center -> AI
        # Only intervene if the intent actually drives toward the wall.
        if move_intent.x * outward.x + move_intent.z * outward.z <= 0.0:
            return move_intent
        inward = Vec3(-outward.x, 0.0, -outward.z)
        # Tangent along the wall; choose the side heading AWAY from the opponent so
        # the AI circles out rather than back into them.
        tangent = Vec3(-outward.z, 0.0, outward.x)
        if tangent.x * opp_dir.x + tangent.z * opp_dir.z > 0.0:
            tangent = Vec3(-tangent.x, 0.0, -tangent.z)
        escape = Vec3(tangent.x + inward.x * 0.5, 0.0, tangent.z + inward.z * 0.5)
        return _xz_unit(escape) * _xz_len(move_intent)

    def _plan_defense(self, opponent, dist, intensity, biases, cap):
        """Decide whether/how to defend the opponent's current swing, executed later
        (see ai_think) once the hit is imminent. The attention read shapes the choice
        but -- per the user -- it drives UNPREDICTABILITY, not a deterministic wall:

          - The read TILTS a weighted draw across {parry, dodge, block}; it never
            collapses to one option. The more predictable the opponent (biases
            ['predict']), the more the weights are FLATTENED toward even, so a
            spammer can't rely on "they always parry me".
          - An anti-streak forbids repeating the same defense too many times in a row.
          - The AI doesn't defend EVERY swing: the leftover (1 - commit) is a DECLINE
            that arms a counter window, so spam gets punished in the recovery instead
            of meeting an endless parry-chain.
          - A buffered punish (riposte / perfect-dodge counter) is CASHED rather than
            buried under another parry -- the old chain never spent its riposte, which
            is exactly why spam felt free.
          - Charge is heavy-TYPE here: dodge/parry only, never block (blocking a
            guard-breaker staggers the AI)."""
        self._ai_defense_plan = None
        atk = opponent.current_attack
        if atk is None or dist > atk.range + 0.7:
            return  # not a credible threat -> don't waste stamina committing.

        can_parry = self.stamina >= PARRY_STAMINA
        can_dodge = self.stamina >= DODGE_STAMINA and self.dodge_cooldown <= 0.0
        can_block = self.stamina >= BLOCK_STAMINA_PER_HIT
        feint_wary = biases['feint_wary']
        predict = biases['predict']

        # Cash a buffered punish instead of re-defending: if a parry's riposte or a
        # perfect-dodge counter is live, mostly DECLINE this swing and strike in the
        # gap (start_attack applies the riposte bonus). This is the core fix for
        # "the parry-chain isn't punishing" -- the chain used to never spend it.
        if (self.riposte_ready or self.dodge_success_timer > 0.0) and random.random() < 0.7:
            self._ai_counter_window = AI_COUNTER_WINDOW
            return

        # Wary of feints -> sometimes refuse to pre-commit at all (a parry whiffs on
        # a fake and eats recovery). Decline + counter instead.
        if feint_wary > 0.0 and random.random() < feint_wary * 0.45:
            self._ai_counter_window = AI_COUNTER_WINDOW
            return

        is_heavy_type = atk.atype in (
            AttackType.HEAVY, AttackType.CHARGE, AttackType.AERIAL)
        weights = {}
        if is_heavy_type:
            # Heavy/charge: NEVER block (guard-break). Dodge favoured, parry viable.
            if can_dodge:
                weights['dodge'] = AI_DODGE_SKILL * (1.0 + 0.5 * biases['dodge_def'] + 0.4 * feint_wary)
            if can_parry:
                weights['parry'] = AI_PARRY_SKILL * 0.6 * (1.0 - 0.4 * feint_wary)
        else:
            # Lights: a varied mix of parry (-> riposte), dodge (-> i-frames +
            # counter) and block. The read tilts but never dominates.
            if can_parry:
                weights['parry'] = AI_PARRY_SKILL * (1.0 + 0.4 * biases['parry'])
            if can_dodge:
                weights['dodge'] = AI_DODGE_SKILL * (1.0 + 0.3 * feint_wary + 0.3 * predict)
            if can_block:
                weights['block'] = AI_BLOCK_SKILL + 0.15 * predict
        if not weights:
            return

        # Flatten the distribution toward even as the opponent gets more predictable,
        # so no single response dominates -> the defense reads as unpredictable.
        if predict > 0.0 and len(weights) > 1:
            mean = sum(weights.values()) / len(weights)
            f = min(0.8, predict)
            for k in weights:
                weights[k] += (mean - weights[k]) * f

        # WHETHER to defend at all -- separate from WHICH defense (the weights above
        # only shape the pick). Scales with the stamina lead so a gassed AI defends
        # less (stays beatable) and capped so it's never a literal wall. The leftover
        # is a DECLINE that arms a counter -- the AI is deliberately not 100%.
        read = (biases['dodge_def'] if is_heavy_type else biases['parry'])
        base_commit = AI_DODGE_SKILL + 0.20 if is_heavy_type else AI_PARRY_SKILL + 0.15
        commit_p = min(cap, base_commit * intensity * (1.0 + 0.25 * read)
                       * (1.0 - 0.3 * feint_wary))
        if random.random() >= commit_p:
            self._ai_counter_window = AI_COUNTER_WINDOW
            return

        plan = _weighted_pick(weights)
        # Anti-streak: the more we've repeated this defense (and the more predictable
        # they are), the more we force a switch to a different one.
        if plan == self._ai_last_defense and len(weights) > 1:
            switch_p = min(0.85, 0.30 + 0.20 * self._ai_defense_streak + 0.30 * predict)
            if random.random() < switch_p:
                others = {k: v for k, v in weights.items() if k != plan}
                alt = _weighted_pick(others)
                if alt is not None:
                    plan = alt
        if plan == self._ai_last_defense:
            self._ai_defense_streak += 1
        else:
            self._ai_defense_streak = 1
            self._ai_last_defense = plan
        self._ai_defense_plan = plan

    # --------------------------------------------------------------------- #
    #  AI: arts
    # --------------------------------------------------------------------- #
    def _ai_react_to_art(self, opponent, dist, intensity, prof):
        """React to an opponent's in-progress art. The reaction (dodge/parry) is
        chosen once -- scaled by the difficulty's art_react_skill -- then fired with
        timing derived from the art's own frame durations so the dodge i-frames /
        parry window straddle the projectile's arrival. Robust to retuned art
        timings. Block is never chosen (blocking an art is a long stagger)."""
        art = opponent.current_art
        if art is None:
            return

        # Decide the reaction once, on the first frame we see this art.
        if not self._obs_opp_arting:
            self._obs_opp_arting = True
            self._ai_art_reaction = None
            _, reachable = _art_reach_eta(art, dist)
            if reachable and random.random() < min(0.97, prof['art_react_skill'] * intensity):
                can_parry = self.stamina >= PARRY_STAMINA
                can_dodge = self.stamina >= DODGE_STAMINA and self.dodge_cooldown <= 0.0
                # Dodge is the default (i-frames + a perfect-dodge cooldown reset);
                # a fraction parry instead (knocks the caster's projectile back).
                parry_pref = AI_ART_PARRY_FRACTION * (0.5 + 0.5 * prof['art_react_skill'])
                if can_parry and random.random() < parry_pref:
                    self._ai_art_reaction = 'parry'
                elif can_dodge:
                    self._ai_art_reaction = 'dodge'
                elif can_parry:
                    self._ai_art_reaction = 'parry'

        if self._ai_art_reaction is None or not self._can_act():
            return

        # Execute when the projectile is about to connect. The ETA is (time until it
        # spawns) + (time for it to travel to us), both read from the live art state.
        release = _art_release_delay(art, opponent._art_sub_frame, opponent._art_frame_timer)
        travel, _ = _art_reach_eta(art, dist)
        impact_eta = release + travel
        if self._ai_art_reaction == 'parry':
            if impact_eta <= AI_ART_PARRY_LEAD:
                self.start_parry()
                self._ai_art_reaction = None
        else:  # dodge -- any dodge grants the i-frames that beat the art
            if (impact_eta <= AI_ART_DODGE_LEAD
                    and self.dodge_cooldown <= 0.0
                    and self.stamina >= DODGE_STAMINA):
                rx, rz = self._forward.z, -self._forward.x
                side = 1.0 if random.random() < 0.5 else -1.0
                self.dodge(Vec3(rx * side, 0.0, rz * side))
                self._ai_art_reaction = None

    def _ai_pick_art(self, dist, require_reach):
        """Pick an art to cast for the current gap, or None. Only arts off cooldown
        with stamina to spare (keeping AI_ART_STAMINA_BUFFER in reserve); when
        require_reach is set, only arts whose projectile can actually cover `dist`."""
        # The AI fights from the ground, so it only casts the ground arts (the air
        # forms KAGURA/HARMONIC require a jump, which the AI never does for an art).
        ready = []
        for a in GROUND_ART.values():
            if self.art_cooldowns.get(a, 0.0) > 0.0:
                continue
            if self.stamina < self.art_stamina_cost + AI_ART_STAMINA_BUFFER:
                continue
            if require_reach:
                _, reachable = _art_reach_eta(a, dist)
                if not reachable:
                    continue
            ready.append(a)
        if not ready:
            return None
        # Range-fit: OVERCLOCK closes distance from afar; either works up close.
        if dist > AI_PREFERRED_RANGE + 1.5 and ArtType.OVERCLOCK in ready:
            return ArtType.OVERCLOCK
        return random.choice(ready)

    def _ai_try_art(self, dt, opponent, dist, intensity, prof, opp_attacking):
        """Maybe unleash an art. Two triggers, both gated by the difficulty's
        art_use_rate and a global cast throttle:
          - PANIC: an imminent swing with no committed defense -> an art's instant,
            full-duration i-frames are an escape that also threatens back.
          - OFFENSE: in a lull, mix a reaching ranged/AoE art into the pressure.
        Returns True if an art was started."""
        if not self._can_act() or self._ai_art_cooldown > 0.0:
            return False
        if opp_attacking:
            # Only the panic escape applies while they're swinging.
            if self._ai_defense_plan is not None:
                return False
            threat = opponent.current_attack
            if (threat is None or dist > threat.range + 0.5):
                return False
            if random.random() >= AI_ART_PANIC_CHANCE * prof['art_use_rate'] * intensity:
                return False
            art = self._ai_pick_art(dist, require_reach=False)
        else:
            # Ordinary offense: per-second use rate, only arts that can connect.
            if random.random() >= prof['art_use_rate'] * intensity * dt * 2.0:
                return False
            art = self._ai_pick_art(dist, require_reach=True)
        if art is not None and self.start_art(art):
            self._ai_art_cooldown = AI_ART_GLOBAL_COOLDOWN
            return True
        return False

    def _ai_pick_air_art(self, dist):
        """Choose the airborne art that FITS the current gap, or None. Only the
        matching art per band -- so a mismatched art is avoided rather than forced:
        KAGURA (self-centred AoE) up close, HARMONIC (travelling crescents) at
        range. Returns None when the fitting art is on cooldown (air arts share a
        cooldown with their ground pair CENTIPEDE / OVERCLOCK), leaving the AI to
        fall back on its ground options instead of a poor aerial cast."""
        if dist <= AI_KAGURA_RANGE:
            if self.art_cooldowns.get(ArtType.KAGURA, 0.0) <= 0.0:
                return ArtType.KAGURA
            return None
        if self.art_cooldowns.get(ArtType.HARMONIC, 0.0) <= 0.0:
            return ArtType.HARMONIC
        return None

    def _ai_try_air_art(self, dt, opponent, dist, intensity, prof,
                        opp_attacking, opp_too_high):
        """Maybe leap and cast an airborne art. AVOIDED when: mid-cooldown/throttle,
        the opponent is swinging (answer that on the ground) or has jumped clear
        (the arts aim where they are now), stamina can't cover the jump + art, or
        nothing fits the gap. On commit it jumps and records the intent; the
        JUMPING handler fires the art near apex. Returns True if it jumped."""
        if not self._can_act() or not self.body.on_ground:
            return False
        if self._ai_art_cooldown > 0.0 or self._ai_attack_cooldown > 0.0:
            return False
        if opp_attacking or opp_too_high:
            return False
        if self.stamina < JUMP_STAMINA + self.art_stamina_cost + AI_ART_STAMINA_BUFFER:
            return False
        art = self._ai_pick_air_art(dist)
        if art is None:
            return False
        rate = (AI_AIR_ART_RATE * prof['art_use_rate'] * intensity
                * prof['aggression_mult'])
        if random.random() >= rate * dt:
            return False
        if self.jump():
            self._ai_air_art = art
            self._ai_art_cooldown = AI_ART_GLOBAL_COOLDOWN
            self._ai_attack_cooldown = 1.2   # same post-jump throttle as the plunge
            return True
        return False

    def ai_think(self, dt, opponent):
        if opponent is None or opponent.state == State.DEAD:
            self._apply_move_intent(dt, Vec3(0, 0, 0))
            return

        to_opp = Vec3(opponent.position.x - self.position.x, 0.0,
                      opponent.position.z - self.position.z)
        dist = _xz_len(to_opp)
        opp_dir = _xz_unit(to_opp)

        intensity = self._ai_intensity(opponent)
        prof = DIFFICULTY_PROFILES[self.difficulty]

        # How far above us the opponent is. A ground swing can't reach a target who
        # has jumped clear (combat enforces this), so the AI must not whiff into it.
        opp_too_high = (opponent.position.y - self.position.y) > GROUND_VERTICAL_REACH

        opp_attacking = opponent.state in (
            State.ATTACK_WINDUP, State.ATTACK_ACTIVE, State.ATTACK_ACTIVE2)

        # ---- Attention: update the running read of the opponent's recent actions.
        # Decay every frame, then bump tallies on the edges/events we can observe.
        # The resulting `biases` steer defense, spacing and offense below.
        self.attention.decay(dt)
        gain = prof['adapt_speed']
        if (opp_attacking and not self._obs_opp_attacking
                and opponent.current_attack is not None):
            self.attention.observe_attack(opponent.current_attack.atype, gain)
        self._obs_opp_attacking = opp_attacking
        opp_dodging = opponent.state == State.DODGING
        if opp_dodging and not self._obs_opp_dodging:
            self.attention.observe_dodge(gain)
        self._obs_opp_dodging = opp_dodging
        # One-shot events (consume so each is counted once). Only the observing AI
        # clears them; the player never observes, so its own flags simply lapse.
        if opponent.feint_event:
            self.attention.observe_feint(gain)
            opponent.feint_event = False
        if opponent.parry_whiff_event:
            self.attention.observe_parry_whiff(gain)
            opponent.parry_whiff_event = False
        # Retreat: accrue while the opponent is actively backing away from us.
        _ov = opponent.body.velocity
        if _ov.x * opp_dir.x + _ov.z * opp_dir.z > MOVE_SPEED * 0.35:
            self.attention.observe_retreat(dt, gain)
        biases = self.attention.biases(prof['attention_mult'])

        # Airborne (the AI jumped): steer toward the opponent with air control and
        # commit the plunge -- or a planned airborne art -- once at/after apex, then
        # bail out (the ground logic below assumes a grounded fighter). (Placed
        # AFTER the attention update so the read keeps decaying/observing.)
        if self.state == State.JUMPING:
            self._apply_move_intent(dt, opp_dir)
            if self._ai_air_art is not None:
                # Fire the aerial art near apex so the float holds us aloft through
                # it. Re-pick for the LIVE gap first: if the opponent has slipped
                # out of the intended art's range we swap to the fitting one, and
                # if nothing fits we abort the art (the jump becomes a plain hop /
                # plunge next frame) -- this is the "avoid when it would whiff".
                if self.body.velocity.y <= AI_AIR_ART_APEX_VY:
                    art = self._ai_pick_air_art(dist) or self._ai_air_art
                    self._ai_air_art = None
                    self.start_art(art)
                return
            if (self.stamina >= ATTACKS[AttackType.AERIAL].stamina
                    and dist <= ATTACKS[AttackType.AERIAL].range
                    and self.body.velocity.y <= 2.5):
                self.start_attack(AttackType.AERIAL)
            return

        # Opponent unleashing an art takes over our decision-making: time a
        # dodge/parry to its projectile and never walk into it. Arts aren't tracked
        # as `current_attack`, so this is handled before normal swing-defense. While
        # waiting to time the reaction we keep spacing (ease away + strafe); once a
        # reaction commits, the state is no longer free and we just bail out.
        opp_arting = (opponent.state == State.ATTACK_ART
                      and opponent.current_art is not None)
        if not opp_arting:
            self._obs_opp_arting = False
            self._ai_art_reaction = None
        else:
            self._ai_react_to_art(opponent, dist, intensity, prof)
            if self.state in (State.IDLE, State.MOVING, State.BLOCKING):
                rx, rz = opp_dir.z, -opp_dir.x
                side = 1.0 if (int(self._ai_attack_cooldown * 3) % 2 == 0) else -1.0
                move_intent = Vec3(-opp_dir.x * 0.7 + rx * side * 0.4, 0.0,
                                   -opp_dir.z * 0.7 + rz * side * 0.4)
                move_intent = self._wall_escape(move_intent, opp_dir)
                self._apply_move_intent(dt, move_intent)
            return

        # Decide a defense ONCE per opponent swing, at the first frame we're able
        # to act while the hit is still upcoming (WINDUP or the hitbox-less stage-1
        # ACTIVE). Deciding at the first *actionable* frame -- rather than only on
        # the windup edge -- means we can still defend a swing whose windup we sat
        # through disabled (e.g. staggered after being parried). That closes the
        # "parry -> free riposte" loop: the parried AI now tries to parry back.
        if not opp_attacking:
            self._ai_swing_handled = False
            self._ai_defense_plan = None
        elif (not self._ai_swing_handled
                and self._can_act()
                and opponent.state in (State.ATTACK_WINDUP, State.ATTACK_ACTIVE)):
            # Only lock in a decision once the swing is a CREDIBLE threat (in or
            # near reach). If the opponent baited an attack from out of range,
            # leave the swing UNHANDLED so we keep re-evaluating as the gap
            # closes. Latching a "no defense" verdict while still far is the
            # retreat-then-heavy bug: the AI would then blindly trudge into the
            # developing hitbox instead of reacting once it arrives in range.
            threat = opponent.current_attack
            if threat is not None and dist <= threat.range + 0.7:
                self._plan_defense(opponent, dist, intensity, biases,
                                   prof['defense_cap'])
                self._ai_swing_handled = True

        # Execute the planned defense once the hit is IMMINENT: the opponent is in
        # stage-1 ACTIVE (a hitbox-less wind-through that immediately precedes the
        # hitbox-live ACTIVE2). A parry window / dodge i-frames started here bridge
        # straight into the hit. (Main updates the player before the AI, so we must
        # commit during ACTIVE -- by ACTIVE2 the hit has already resolved.)
        threat = opponent.current_attack
        if (self._ai_defense_plan is not None
                and threat is not None
                and opponent.state in (State.ATTACK_ACTIVE, State.ATTACK_ACTIVE2)
                and dist <= threat.range + 0.5
                and self._can_act()):
            plan = self._ai_defense_plan
            if plan == 'parry':
                self.start_parry()
            elif plan == 'dodge':
                rx, rz = self._forward.z, -self._forward.x
                side = 1.0 if random.random() < 0.5 else -1.0
                self.dodge(Vec3(rx * side, 0.0, rz * side))
            elif plan == 'block':
                self.start_block()
            self._ai_defense_plan = None

        # Arts (offense + panic escape). Either burn an art's instant i-frames to
        # escape an imminent swing we left undefended, or -- in a lull -- mix a
        # reaching ranged/AoE art into the pressure. Gated by difficulty art_use_rate.
        if self._ai_try_art(dt, opponent, dist, intensity, prof, opp_attacking):
            return

        # Airborne arts: occasionally leap to cast KAGURA (close AoE) or HARMONIC
        # (ranged crescents). A committed change-up, avoided while the opponent is
        # swinging or has jumped clear (see _ai_try_air_art). The cast itself is
        # fired near apex by the JUMPING handler above.
        if self._ai_try_air_art(dt, opponent, dist, intensity, prof,
                                opp_attacking, opp_too_high):
            return

        # Aggressive gap-close: the opponent baited an attack from beyond our
        # reach (classic retreat-then-heavy). Rather than trudge into the
        # developing hitbox, dash FORWARD under dodge i-frames once they've
        # committed (ACTIVE/ACTIVE2) -- safely closing the distance to land in
        # range and punish the long recovery. Gated by aggression + stamina so
        # it's a read, not a reflex; the i-frames cover the active hitbox.
        gap_close = opponent.current_attack
        if (self._can_act()
                and gap_close is not None
                and opponent.state in (State.ATTACK_ACTIVE, State.ATTACK_ACTIVE2)
                and gap_close.range < dist <= gap_close.range + 1.4
                and self.dodge_cooldown <= 0.0
                and self.stamina >= AI_GAPCLOSE_STAMINA   # a real reserve, not a near-gassed dodge
                and random.random() < AI_AGGRESSION * intensity):
            if self.dodge(Vec3(opp_dir.x, 0.0, opp_dir.z)):  # dodge INTO the attack
                # Commit to PUNISHING out of it: arm a counter so that once the dodge
                # recovers and we're in range we actually swing -- even if there was
                # no hit to perfect-dodge (the opponent baited a whiff from range).
                self._ai_counter_window = AI_COUNTER_WINDOW
                return

        # Chase charge: the opponent is actively RETREATING (backing off to regen,
        # or kiting). Both move at MOVE_SPEED, so a straight footrace never closes
        # -- the answer is a lunging CHARGE whose stage-2 dash overtakes the kiter
        # AND lands a hit, forcing the engagement. Two deliberate properties so it
        # doesn't read as a free parry:
        #   (1) Commit probabilistically over TIME (rate * intensity * dt), NOT the
        #       instant the player crosses into a fixed band. The old version rolled
        #       AI_AGGRESSION (~0.6) every frame, so it fired within a frame or two
        #       of entering the band EVERY time -- a fixed, pre-parryable tell. Now
        #       the charge lands at a varying moment in the pursuit.
        #   (2) Trigger across the WHOLE chase, including once it has closed into
        #       melee. The old band quit at light range, so a chase that caught up
        #       stopped threatening the charge -- the user's "moving forward + in
        #       range + no action = a chase, so charge". `chasing` (opponent moving
        #       away) is what flags the pursuit; a standstill far-off opponent is
        #       just walked down by the movement intent instead.
        opp_vel = opponent.body.velocity
        opp_receding = opp_vel.x * opp_dir.x + opp_vel.z * opp_dir.z
        chasing = opp_receding > MOVE_SPEED * 0.35
        if (self._can_act()
                and not opp_attacking
                and not opponent.is_blocking
                and self._ai_attack_cooldown <= 0.0
                and chasing
                and dist <= ATTACKS[AttackType.CHARGE].range + 3.5
                and self.stamina >= AI_GAPCLOSE_STAMINA
                and random.random() < (AI_CHASE_CHARGE_RATE * intensity * dt
                                       * prof['aggression_mult']
                                       * (1.0 + 0.6 * biases['press']))):
            if self.start_attack(AttackType.CHARGE, lunge=True):
                self._ai_attack_cooldown = (
                    ATTACKS[AttackType.CHARGE].total_time + random.uniform(0.10, 0.40))
                return

        # Aerial jump-in mix-up: occasionally leap in with a plunge -- a committed,
        # readable approach that breaks a turtle's guard and changes the attack
        # angle. Gated so it's an occasional change-up, not a reflex.
        if (self._can_act()
                and self.body.on_ground
                and not opp_attacking
                and not opp_too_high
                and self._ai_attack_cooldown <= 0.0
                and self.stamina >= ATTACKS[AttackType.AERIAL].stamina + JUMP_STAMINA
                and ATTACKS[AttackType.LIGHT].range < dist <= ATTACKS[AttackType.CHARGE].range + 2.0
                and random.random() < (AI_JUMP_IN_RATE * intensity
                                       * prof['aggression_mult'] * dt)):
            if self.jump():
                self._ai_attack_cooldown = 1.2
                return

        # Punish windows -- this is what makes blind offense costly. (Skip while the
        # opponent is airborne above us -- a ground swing would whiff into nothing.)
        in_light_range = dist <= ATTACKS[AttackType.LIGHT].range + 0.2
        if self._can_act() and in_light_range and not opp_too_high:
            if self.riposte_ready:
                self.start_attack(AttackType.LIGHT)        # cash in the 2x riposte
            elif self.dodge_success_timer > 0.0:
                self.start_attack(AttackType.LIGHT)        # successive-dodge counter
            elif opponent.state == State.STAGGERED:
                # Free hit while they can't defend -> heavy for max punish.
                use_heavy = self.stamina > ATTACKS[AttackType.HEAVY].stamina
                self.start_attack(AttackType.HEAVY if use_heavy else AttackType.LIGHT)

        # Feint follow-up: a feint only adds pressure if it's CASHED IN. While the
        # post-feint window is open and the opponent is open + in reach, slam a
        # committed swing (never another feint -- the cooldown forbids that anyway)
        # to catch them mid-reaction to the fake. Bypasses the attack-cooldown on
        # purpose; skipped if they counter-attacked (handled by the defense logic).
        if (self._ai_feint_followup > 0.0
                and self._ai_feint_delay <= 0.0
                and self._can_act()
                and not opp_attacking
                and dist <= ATTACKS[AttackType.LIGHT].range + 0.3):
            self._ai_feint_followup = 0.0
            followup = (AttackType.HEAVY
                        if (self.stamina > ATTACKS[AttackType.HEAVY].stamina
                            and random.random() < 0.30)
                        else AttackType.LIGHT)
            if self.start_attack(followup):
                self._ai_attack_cooldown = (
                    ATTACKS[followup].total_time + random.uniform(0.10, 0.40))
                return

        # Counter window: the AI DECLINED to defend this swing (refusing to be a
        # predictable wall, or cashing a buffered punish). Once the swing recovers
        # and they're open + in reach, break pattern with a VARIED counter -- a
        # light/heavy/charge, sometimes itself a feint -- so a spammer actually pays.
        # Bypasses the attack-cooldown; deferred while they're still mid-swing.
        if (self._ai_counter_window > 0.0
                and self._can_act()
                and not opp_attacking
                and dist <= ATTACKS[AttackType.LIGHT].range + 0.35):
            self._ai_counter_window = 0.0
            r = random.random()
            if self.stamina >= ATTACKS[AttackType.CHARGE].stamina and r < 0.15:
                catype = AttackType.CHARGE
            elif self.stamina > ATTACKS[AttackType.HEAVY].stamina and r < 0.45:
                catype = AttackType.HEAVY
            else:
                catype = AttackType.LIGHT
            if self.start_attack(catype):
                fc = prof['feint_rate'] * 0.6 * FEINT_TYPE_MULT.get(catype, 1.0)
                if self.feint_cooldown <= 0.0 and random.random() < fc:
                    self.feint_pending = True
                    self._ai_attack_cooldown = (
                        ATTACKS[catype].windup + ATTACKS[catype].active + 0.03)
                else:
                    self._ai_attack_cooldown = (
                        ATTACKS[catype].total_time + random.uniform(0.10, 0.40))
                return

        # If we just committed to an action (attack/parry/dodge), don't also move.
        if self.state not in (State.IDLE, State.MOVING, State.BLOCKING):
            return
        # Release a block once the threat has passed so we can move/attack again.
        if self.state == State.BLOCKING and not opp_attacking:
            self.stop_block()

        # Low stamina -> back off to regen.
        low_stamina = self.stamina < MAX_STAMINA * 0.30
        # Guard-break-spam read + no stamina lead -> deliberately disengage and
        # wait to win the stamina war (the user's "retreat farther, play defense,
        # wait to gain stamina advantage" answer to charge/heavy spam).
        defensive_wait = (biases['dodge_def'] > 0.35
                          and self.stamina <= opponent.stamina + 5.0)

        # Movement intent. The spacing read pushes the preferred range outward, so a
        # light- or guard-break-spamming opponent gets fought from farther out.
        prefer = AI_PREFERRED_RANGE + biases['spacing'] * ATTENTION_SPACING_BONUS
        if opp_too_high:
            # Opponent is airborne above us (likely setting up a plunge). Don't walk
            # under them; give ground and strafe so we can read + dodge the dive.
            rx, rz = opp_dir.z, -opp_dir.x
            side = 1.0 if (int(self._ai_attack_cooldown * 3) % 2 == 0) else -1.0
            move_intent = Vec3(-opp_dir.x * 0.5 + rx * side * 0.4, 0.0,
                               -opp_dir.z * 0.5 + rz * side * 0.4)
        elif low_stamina or defensive_wait:
            move_intent = Vec3(-opp_dir.x, 0.0, -opp_dir.z)              # retreat
        elif opp_attacking and self._ai_defense_plan is None:
            # Opponent is swinging and we have no committed defense. NEVER walk
            # into it (the retreat-then-heavy trap). If we're in/near reach, back
            # out; if we're already safely outside reach, hold ground and strafe
            # -- then close in to punish once the swing recovers.
            threat = opponent.current_attack
            reach = (threat.range if threat is not None
                     else ATTACKS[AttackType.HEAVY].range)
            if dist <= reach + 0.3:
                move_intent = Vec3(-opp_dir.x, 0.0, -opp_dir.z)
            else:
                rx, rz = opp_dir.z, -opp_dir.x
                side = 1.0 if (int(self._ai_attack_cooldown * 3) % 2 == 0) else -1.0
                move_intent = Vec3(rx * side * 0.5, 0.0, rz * side * 0.5)
        elif dist > prefer + 0.4:
            move_intent = opp_dir                                        # approach
        elif dist < prefer - 0.4:
            move_intent = Vec3(-opp_dir.x * 0.5, 0.0, -opp_dir.z * 0.5)  # back off a bit
        else:
            # Circle-strafe but keep a slight inward press so we stay inside our
            # own attack range and actually threaten (not orbit just out of reach).
            rx, rz = opp_dir.z, -opp_dir.x
            side = 1.0 if (int(self._ai_attack_cooldown * 3) % 2 == 0) else -1.0
            move_intent = Vec3(rx * side * 0.6 + opp_dir.x * 0.25, 0.0,
                               rz * side * 0.6 + opp_dir.z * 0.25)

        # Don't back into the wall: if cornered and this intent drives into the
        # boundary, convert it to a tangential escape arc (run around the opponent).
        move_intent = self._wall_escape(move_intent, opp_dir)
        self._apply_move_intent(dt, move_intent)

        # Offense: attack when in range, off cooldown, healthy on stamina, and the
        # opponent is NOT mid-swing (don't trade into their active hitbox).
        if (not low_stamina
                and not opp_attacking
                and not opp_too_high
                and self._ai_attack_cooldown <= 0.0
                and dist <= ATTACKS[AttackType.LIGHT].range + 0.1
                and self._can_act()):
            # Pressure scales with the difficulty's aggression and the "press" read
            # (a retreating/passive opponent gets pressured harder).
            off_mult = prof['aggression_mult'] * (1.0 + 0.5 * biases['press'])
            if random.random() < AI_AGGRESSION * intensity * off_mult * dt * 3.0:
                if opponent.is_blocking:
                    # Don't reflexively heavy a held guard -- a single guard-break
                    # timing is readable and gets perfect-dodged into a free punish.
                    # Randomize across TWO guard-breakers (charge + heavy) so the
                    # player can't pattern-match one timing; otherwise chip with a
                    # fast light. A feint may be layered on below to beat a pre-loaded
                    # dodge. The charge is stationary (lunge=False) -- turtle's in reach.
                    can_charge = self.stamina >= ATTACKS[AttackType.CHARGE].stamina
                    can_heavy = self.stamina > ATTACKS[AttackType.HEAVY].stamina
                    roll = random.random()
                    if can_charge and roll < AI_TURTLE_CHARGE_CHANCE:
                        atype = AttackType.CHARGE
                    elif can_heavy and roll < AI_TURTLE_CHARGE_CHANCE + AI_TURTLE_HEAVY_CHANCE:
                        atype = AttackType.HEAVY
                    else:
                        atype = AttackType.LIGHT
                else:
                    # Open opponent: mix charge in alongside light/heavy. Charge
                    # is a slow guard-breaking gap-closer, but at this range we
                    # want a stationary strike (lunge=False, set in start_attack)
                    # so it doesn't dash past a target already in reach.
                    roll = random.random()
                    can_charge = self.stamina >= ATTACKS[AttackType.CHARGE].stamina
                    can_heavy = self.stamina > ATTACKS[AttackType.HEAVY].stamina + 5
                    if can_charge and roll < AI_CHARGE_CHANCE:
                        atype = AttackType.CHARGE
                    elif can_heavy and roll < AI_CHARGE_CHANCE + 0.25:
                        atype = AttackType.HEAVY
                    else:
                        atype = AttackType.LIGHT
                if self.start_attack(atype):
                    # Maybe throw it as a FEINT to bait a reaction (then punish the
                    # whiff). More likely against a jumpy opponent (over-dodges or
                    # whiffs parries -> biases['bait']) and at higher difficulty, and
                    # scaled by attack type (lights fake more, charges less).
                    feint_chance = ((prof['feint_rate'] * intensity + 0.45 * biases['bait'])
                                    * FEINT_TYPE_MULT.get(atype, 1.0))
                    if self.feint_cooldown <= 0.0 and random.random() < feint_chance:
                        self.feint_pending = True
                        # A feint ends at stage-1 (windup+active), NOT total_time --
                        # cool down only for its real duration so the AI is free to
                        # slam the follow-up the instant it resolves.
                        self._ai_attack_cooldown = (
                            ATTACKS[atype].windup + ATTACKS[atype].active + 0.03)
                    else:
                        self._ai_attack_cooldown = (
                            ATTACKS[atype].total_time + random.uniform(0.10, 0.40))

    # --------------------------------------------------------------------- #
    #  Sword trail
    # --------------------------------------------------------------------- #
    def _update_trail(self, dt):
        # Emit only while the hitbox is live (the actual swing); break the ribbon
        # otherwise so resting/winding-up poses don't smear a streak. Runs after
        # _update_sword_visual so the blade markers are at their posed positions.
        if self.state in (State.ATTACK_ACTIVE, State.ATTACK_ACTIVE2):
            # Bow the arc mostly along facing with a little lift, so the swept
            # strip bulges out in front of (and slightly over) the body.
            fwd = self._forward
            bow = Vec3(fwd.x, 0.45, fwd.z)
            bl = bow.length()
            if bl > 1e-6:
                bow = bow * (1.0 / bl)
            self.sword_trail.emit(self._trail_root.world_position,
                                  self._trail_tip.world_position,
                                  bow_dir=bow, bow_amount=TRAIL_BOW)
        else:
            self.sword_trail.reset()
        self.sword_trail.update(dt)

    # --------------------------------------------------------------------- #
    #  Sword visual pose
    # --------------------------------------------------------------------- #
    def _update_sword_visual(self):
        bp = self.sword_base_pos

        # Default: no motion blur. Any pose branch that wants ghosts sets this True.
        self.motion_blur_active = False

        # Blade stays one constant colour (heavy/charge "shine" is the glint
        # sprite spawned in start_attack, not a blade recolour).
        self.blade.color = SWORD_COLOR

        # Blade grows during the active window so the swing is unmissable.
        if self.state in (State.ATTACK_ACTIVE, State.ATTACK_ACTIVE2):
            self.blade.scale = self._blade_base_scale * 1.3
        else:
            self.blade.scale = self._blade_base_scale

        # Pose by state. Heavy attacks chop vertically (up->down); light
        # attacks slash horizontally, alternating right->left and left->right.
        is_heavy = (self.current_attack is not None
                    and self.current_attack.atype == AttackType.HEAVY)
        is_charge = (self.current_attack is not None
                    and self.current_attack.atype == AttackType.CHARGE)
        is_aerial = (self.current_attack is not None
                    and self.current_attack.atype == AttackType.AERIAL)
        # s mirrors the light slash across the centerline: +1 = rightward
        # (wind over right shoulder, sweep left), -1 = leftward (mirror).
        s = -1.0 if self._light_swing_left else 1.0
        # Torso twist about the central vertical axis, synced to a light swing:
        # coil on windup, uncoil through active/recovery. Heavy and all other
        # states keep the upper body square. (Flip signs if it reads backwards.)
        


        #s = -1
        
        # Arm pivots: the right (sword) arm swings from the shoulder in step
        # with the blade; the off-hand arm stays neutral but keeps its pivot
        # for future use. (Tweak these angles to taste.)
        ra_rot = Vec3(0, 0, 0)
        la_rot = Vec3(0, 0, 0)
        ra_pos = Vec3(0.5, bp.y + 0.4, 0)
        la_pos = Vec3(-0.5, bp.y + 0.4, 0)
        h_rot = Vec3(0, 0, 0)
        h_pos = Vec3(0, 1.62, 0)
        b_rot = Vec3(0, 0, 0)
        b_pos = Vec3(0, 0, 0)
        # Leg pivots (hip joints). Default = neutral stance. Placeholder slots are
        # filled in per attack stage below -- all neutral for now, tune to taste.
        ll_rot = Vec3(0, 0, 0)
        rl_rot = Vec3(0, 0, 0)
        ll_pos = self.leg_l_base_pos
        rl_pos = self.leg_r_base_pos
        root_rot = Vec3(0, 0, 0)

        _in_attack_state = self.state in (
            State.ATTACK_WINDUP, State.ATTACK_ACTIVE,
            State.ATTACK_ACTIVE2, State.ATTACK_RECOVERY,
        )
        _in_any_attack = _in_attack_state or self.state == State.ATTACK_ART
        if _in_attack_state and is_charge:
            if self.state == State.ATTACK_WINDUP:
                ra_rot = Vec3(-60, -130, 0)
                ra_pos = Vec3(-0.2, 1.2, 0.55)
                la_rot = Vec3(-30, -20, 30)
                la_pos = Vec3(-0.3, 1.35, -0.35)
                h_rot = Vec3(10, -10, 0)
                h_pos = Vec3(-0.2, 1.5, 0.25)
                b_rot = Vec3(20, -45, 0)
                b_pos = Vec3(0.2, 0, -0.2)
                ll_rot = Vec3(30, -90, 0)
                ll_pos = Vec3(-0.4, 0.6, -0.25)
                rl_rot = Vec3(0, -45, 0)
                rl_pos = Vec3(0, 0.65, 0.3)
                self.sword.rotation = Vec3(10, -180, 0)
                self.sword.position = Vec3(-0.75, 0.75, -0.1)
            elif self.state == State.ATTACK_ACTIVE:
                b_rot = Vec3(25, -60, 0)

                ra_rot = Vec3(-50, -150, 0)     
                ra_pos = Vec3(-0.4, bp.y + 0.2, 0.9)  

                la_rot = Vec3(-30, -90 , 0)
                la_pos = Vec3(-0.7, bp.y + 0.3, -0.1)

                h_rot = Vec3(10, -20, 0)
                h_pos = Vec3(-0.6, 1.4, 0.5)
                self.sword.rotation = Vec3(20, -140, 0)
                self.sword.position = Vec3(-0.8, bp.y - 0.3, 0.3)

                ll_rot = Vec3(30, -30, 0)
                rl_rot = Vec3(40, -20, 0)
                ll_pos = Vec3(-0.4, 0.6, -0.1)
                rl_pos = Vec3(-0.3, 0.65, 0.6)
            elif self.state == State.ATTACK_ACTIVE2:
                b_rot = Vec3(0, 20, 0)
                ra_rot = Vec3(-140, 80, 0)
                ra_pos = Vec3(0.5, bp.y + 0.4, 0)
                la_rot = Vec3(50, 50, 0)
                la_pos = Vec3(-0.5, bp.y + 0.4, -0.1)           
                self.sword.rotation = Vec3(-45, 100, 0)
                self.sword.position = Vec3(1, bp.y + 1, 0.1)

                ll_rot = Vec3(40, 0, 0)
                rl_rot = Vec3(10, 0, 0)
                ll_pos = Vec3(-0.25, 0.55, 0)
                rl_pos = Vec3(0.25, 0.65, 0.2)
            elif self.state == State.ATTACK_RECOVERY:
                b_rot = Vec3(10, 30, 0)
                b_pos = Vec3(0, 0, -0.2)
                ra_rot = Vec3(-130, 75, 0)
                ra_pos = Vec3(0.5, bp.y + 0.4, 0)
                la_rot = Vec3(60, 40, 0)
                la_pos = Vec3(-0.3, bp.y + 0.4, 0.2) 
                h_pos = Vec3(0.1, 1.6, 0.15)
                self.sword.rotation = Vec3(-35, 80, 0)
                self.sword.position = Vec3(1.15, bp.y + 0.9, 0.2)

                ll_rot = Vec3(30, 0, 10)
                rl_rot = Vec3(0, -10, -10)
                ll_pos = Vec3(-0.25, 0.65, 0)
                rl_pos = Vec3(0.3, 0.65, 0.1)

        elif _in_attack_state and is_heavy:
            if self.state == State.ATTACK_WINDUP:
                b_rot = Vec3(-10, 0, 0)

                ra_rot = Vec3(-140, -90, 0)        # cock straight overhead
                la_rot = Vec3(-140, 90, 0)

                h_rot = Vec3(-5, 0, 0)
                h_pos = Vec3(0, 1.62, -0.1)

                self.sword.rotation = Vec3(-170, 0, 90)
                self.sword.position = Vec3(0.0, bp.y + 1.1, -0.2)

                ll_rot = Vec3(10, 0, 5)                  # heavy windup -- tune me
                rl_rot = Vec3(10, 0, -5)
                ll_pos = Vec3(-0.20, 0.65, -0.05)
                rl_pos = Vec3(0.20, 0.65, -0.05)
            elif self.state == State.ATTACK_ACTIVE:
                b_rot = Vec3(-5, 0, 0)
                b_pos = Vec3(0, -0.1, 0)
                ra_rot = Vec3(-140, -90, 10)       
                la_rot = Vec3(-140, 90, -10)

                ra_pos = Vec3(0.5, bp.y + 0.4, 0)
                la_pos = Vec3(-0.5, bp.y + 0.4, 0)

                h_rot = Vec3(0, 0, 0)
                h_pos = Vec3(0, 1.46, 0)
                self.sword.rotation = Vec3(-190, 0, 90)
                self.sword.position = Vec3(0.0, bp.y + 1, -0.3)

                ll_rot = Vec3(10, 0, 0)                  
                rl_rot = Vec3(30, 0, 0)
                ll_pos = Vec3(-0.20, 0.65, 0.0)
                rl_pos = Vec3(0.20, 0.8, 0.3)
            elif self.state == State.ATTACK_ACTIVE2:
                b_rot = Vec3(20, 0, 0)

                ra_rot = Vec3(-5, 0, 30)           # attack stage 2 -- tune me
                la_rot = Vec3(-5, 0, -30) 

                ra_pos = Vec3(0.4, bp.y + 0.2, 0.55) 
                la_pos = Vec3(-0.4, bp.y + 0.2, 0.55) 

                h_rot = Vec3(20, 0, 0)
                h_pos = Vec3(0, 1.45, 0.7)
                self.sword.rotation = Vec3(40, 0, -90)
                self.sword.position = Vec3(0.0, bp.y * 0.5, 0.75)

                ll_rot = Vec3(45, 0, 5)                  
                rl_rot = Vec3(0, 5, 0)
                ll_pos = Vec3(-0.20, 0.6, 0.2)
                rl_pos = Vec3(0.25, 0.65, 0.55)
            elif self.state == State.ATTACK_RECOVERY:
                b_rot = Vec3(10, 0, 0)
                b_pos = Vec3(0, -0.1, 0)
                ra_rot = Vec3(0, 0, 30)           # attack stage 2 -- tune me
                la_rot = Vec3(0, 0, -30) 

                ra_pos = Vec3(0.45, bp.y + 0.3, 0.35) 
                la_pos = Vec3(-0.45, bp.y + 0.3, 0.35) 
                h_rot = Vec3(5, 0, 0)
                h_pos = Vec3(0, 1.5, 0.35)
                self.sword.rotation = Vec3(30, 0, 90)
                self.sword.position = Vec3(0, bp.y * 0.7, 0.4)

                ll_rot = Vec3(40, 0, 5)                  
                rl_rot = Vec3(-15, 0, 0)
                ll_pos = Vec3(-0.20, 0.6, 0.15)
                rl_pos = Vec3(0.25, 0.6, 0.3)

        elif _in_attack_state and is_aerial:
            # Airborne plunge: cock the blade overhead, pitch forward into the dive,
            # then slam it straight down; land in a crouch. Legs tuck on the way down.
            if self.state == State.ATTACK_WINDUP:
                ra_rot = Vec3(-225, 355, 0)
                ra_pos = Vec3(-0.15, 1.15, -0.45)
                la_rot = Vec3(-185, 85, -45)
                la_pos = Vec3(0.1, 1.15, 0.6)
                h_rot = Vec3(-42, 30, -55)
                h_pos = Vec3(-0.4, 1.22, 0.1)
                b_rot = Vec3(-63, 105, 0)
                b_pos = Vec3(1, 0.5, -0.2)
                ll_rot = Vec3(-73, 95, 5)
                ll_pos = Vec3(0.5, 1.15, 0.2)
                rl_rot = Vec3(-3, 90, -10)
                rl_pos = Vec3(0.4, 0.7, -0.25)
                self.sword.rotation = Vec3(-205, 0, 75)
                self.sword.position = Vec3(-0.1, 1.7, -1.1)
            elif self.state == State.ATTACK_ACTIVE:
                ra_rot = Vec3(-215, 355, -5)
                ra_pos = Vec3(-0.05, 1.5, -0.25)
                la_rot = Vec3(-170, 90, 160)
                la_pos = Vec3(-0.35, 0.85, 0.3)
                h_rot = Vec3(-12, 0, -55)
                h_pos = Vec3(-0.4, 1.22, -0.1)
                b_rot = Vec3(-43, 5, -75)
                b_pos = Vec3(1.15, 0.8, 0.05)
                ll_rot = Vec3(-93, 35, 0)
                ll_pos = Vec3(0.35, 0.85, 0.4)
                rl_rot = Vec3(-28, 55, -80)
                rl_pos = Vec3(0.5, 1.05, -0.2)
                self.sword.rotation = Vec3(-175, -10, 100)
                self.sword.position = Vec3(0.05, 2.2, -0.8)
            elif self.state == State.ATTACK_ACTIVE2:
                offs = -0.2
                ra_rot = Vec3(-55, 370, 15)
                ra_pos = Vec3(0.05, 1.4 + offs, 0.25)
                la_rot = Vec3(-170, 90, 230)
                la_pos = Vec3(-0.2, 1.05 + offs, -0.5)
                h_rot = Vec3(18, 10, -35)
                h_pos = Vec3(-0.25, 1.32 + offs, -0)
                b_rot = Vec3(52, 5, -75)
                b_pos = Vec3(1.2, 1 + offs, -0.5)
                ll_rot = Vec3(17, -90, 15)
                ll_pos = Vec3(0.3, 0.7 + offs, -0.4)
                rl_rot = Vec3(12, -60, -90)
                rl_pos = Vec3(0.65, 1.25 + offs, 0.1)
                self.sword.rotation = Vec3(-25, -10, 90)
                self.sword.position = Vec3(-0.05, 1 + offs, 1)
            elif self.state == State.ATTACK_RECOVERY:
                ra_rot = Vec3(-30, 370, 15)
                ra_pos = Vec3(-0.2, 0.7, 0.3)
                la_rot = Vec3(-140, 90, 315)
                la_pos = Vec3(0.1, 1, -0.6)
                h_rot = Vec3(28, -30, -25)
                h_pos = Vec3(-0.1, 1.02, -0.1)
                b_rot = Vec3(47, -115, 20)
                b_pos = Vec3(1.1, -0, -0.15)
                ll_rot = Vec3(-23, -125, 10)
                ll_pos = Vec3(0.35, 0.5, -0.65)
                rl_rot = Vec3(92, -135, 5)
                rl_pos = Vec3(0.15, 0.15, 0.2)
                self.sword.rotation = Vec3(0, -10, 90)
                self.sword.position = Vec3(-0.35, 0.05, 0.8)

        elif _in_attack_state and self.current_attack is not None:    # light
            if self.state == State.ATTACK_WINDUP:
                b_rot = Vec3(-10, -50 * s, 0)
                if s > 0: # Left Right Swing
                    ra_rot = Vec3(-100, -80, 0)   # raise and cock back
                    ra_pos = Vec3(0.35, bp.y + 0.3, 0.4)

                    la_rot = Vec3(-110, -10, 0)  
                    la_pos = Vec3(-0.3, bp.y + 0.1, -0.3)

                    h_rot = Vec3(-5, 0, 0)
                    h_pos = Vec3(0.2, 1.6, -0.2)

                    self.sword.rotation = Vec3(-30, -150 * s, 0)
                    self.sword.position = Vec3(bp.x * -1 * s - 0, bp.y + 0.5, 0.4)

                    ll_rot = Vec3(0, -35, 0)
                    rl_rot = Vec3(0, -35, -10)
                    ll_pos = Vec3(-0.1, 0.65, -0.25)
                    rl_pos = Vec3(0.25, 0.65, 0.1)
                else: # Right Left Swing
                    ra_rot = Vec3(-110, -5, 0)   # raise and cock back
                    ra_pos = Vec3(0.3, bp.y + 0.1, -0.4)

                    la_rot = Vec3(-100, 80, 0)  
                    la_pos = Vec3(-0.6, bp.y + 0.2, 0.2)

                    h_rot = Vec3(-5, 0, 0)
                    h_pos = Vec3(-0.2, 1.6, -0.2)

                    self.sword.rotation = Vec3(-30, -120 * s, 0)
                    self.sword.position = Vec3(bp.x * -1 * s - 0.1, bp.y + 0.5, 0.35)

                    rl_rot = Vec3(0, 35, 0)
                    ll_rot = Vec3(0, 35, 10)
                    rl_pos = Vec3(0.1, 0.65, -0.25)
                    ll_pos = Vec3(-0.25, 0.65, 0.1)
            elif self.state == State.ATTACK_ACTIVE:
                b_rot = Vec3(0, -30 * s, 0)
                if s > 0: # Left Right Swing
                    ra_rot = Vec3(-100, -80, 0)   # raise and cock back
                    ra_pos = Vec3(0.3, bp.y + 0.3, 0.3)

                    la_rot = Vec3(-100, 10, 0)  
                    la_pos = Vec3(-0.5, bp.y + 0.2, -0.3)

                    h_rot = Vec3(0, 0, 0)
                    h_pos = Vec3(0, 1.6, 0)

                    self.sword.rotation = Vec3(-0, -170 * s, 0)
                    self.sword.position = Vec3(bp.x - 1., bp.y + 0.45, 0.4)

                    ll_rot = Vec3(15, -20, 10)
                    rl_rot = Vec3(20, -10, 10)
                    ll_pos = Vec3(-0.25, 0.65, -0.05)
                    rl_pos = Vec3(0.3, 0.8, 0.4)
                else:
                    ra_rot = Vec3(-110, 20, 0)
                    ra_pos = Vec3(0.4, bp.y + 0.35, -0.25)

                    la_rot = Vec3(-105, 90, 0)  
                    la_pos = Vec3(-0.1, bp.y + 0.4, 0.4)

                    h_rot = Vec3(0, 0, -5)
                    h_pos = Vec3(0, 1.6, 0.05)

                    self.sword.rotation = Vec3(-10, -130 * s, 0)
                    self.sword.position = Vec3(bp.x + 0.4, bp.y + 0.65, 0.35)

                    rl_rot = Vec3(15, 20, -10)
                    ll_rot = Vec3(20, 10, -10)
                    rl_pos = Vec3(0.25, 0.65, -0.05)
                    ll_pos = Vec3(-0.3, 0.8, 0.4)
            elif self.state == State.ATTACK_ACTIVE2:
                b_rot = Vec3(20, 50 * s , 0)
                if s > 0: # Left Right Swing
                    ra_rot = Vec3(-45, 80, 0)   # raise and cock back
                    ra_pos = Vec3(0.6, bp.y + 0.3, 0)

                    la_rot = Vec3(-55, 120, 0)
                    la_pos = Vec3(0.1, bp.y + 0.25, 0.7)

                    h_rot = Vec3(5, 0, 0)
                    h_pos = Vec3(0.4, 1.5, 0.5)

                    self.sword.rotation = Vec3(15, 60 * s, 0)
                    self.sword.position = Vec3(1 * s + 0.2, bp.y * 0.75, 0.2)

                    ll_rot = Vec3(40, 10, 10)
                    rl_rot = Vec3(-10, 10, -10)
                    ll_pos = Vec3(-0.1, 0.6, 0.25)
                    rl_pos = Vec3(0.50, 0.65, 0.3)
                else:
                    ra_rot = Vec3(-60, -100, 0)
                    ra_pos = Vec3(-0.2, bp.y + 0.2, 0.7)

                    la_rot = Vec3(-80, -10, 0)
                    la_pos = Vec3(-0.7, bp.y - 0, -0.2)

                    h_rot = Vec3(5, -10, 0)
                    h_pos = Vec3(-0.4, 1.5, 0.4)

                    self.sword.rotation = Vec3(15, 60 * s, 0)
                    self.sword.position = Vec3(1 * s, bp.y * 0.75, 0.75)

                    rl_rot = Vec3(40, -10, -10)
                    ll_rot = Vec3(-10, -10, 10)
                    rl_pos = Vec3(0.1, 0.6, 0.25)
                    ll_pos = Vec3(-0.50, 0.65, 0.3)
            elif self.state == State.ATTACK_RECOVERY:
                b_rot = Vec3(10, 30 * s, 0)
                if s > 0: # Left Right Swing
                    ra_rot = Vec3(-30, 0, 0)   # raise and cock back
                    ra_pos = Vec3(0.5, bp.y + 0.35, -0.2)

                    la_rot = Vec3(-40, 130, 0)
                    la_pos = Vec3(-0.1, bp.y + 0.3, 0.6)

                    h_rot = Vec3(0, 0, 0)
                    h_pos = Vec3(0.2, 1.6, 0.3)

                    self.sword.rotation = Vec3(8, 50 * s, 0)
                    self.sword.position = Vec3(bp.x * 1 * s + 0.1, bp.y * 0.75, 0.25)

                    ll_rot = Vec3(30, 5, 0)
                    rl_rot = Vec3(-15, 10, -10)
                    ll_pos = Vec3(-0.2, 0.6, 0.2)
                    rl_pos = Vec3(0.50, 0.65, 0.2)
                else:
                    ra_rot = Vec3(-50, -120, 0)
                    ra_pos = Vec3(0.05, bp.y + 0.3, 0.6)

                    la_rot = Vec3(-40, 0, 0)
                    la_pos = Vec3(-0.55, bp.y + 0.3, -0.25)

                    h_rot = Vec3(0, -5, 0)
                    h_pos = Vec3(-0.15, 1.6, 0.25)

                    self.sword.rotation = Vec3(8, 30 * s, 0)
                    self.sword.position = Vec3(bp.x * 1.1 * s, bp.y * 0.75, 0.4)

                    rl_rot = Vec3(30, -5, 0)
                    ll_rot = Vec3(-15, -10, 10)
                    rl_pos = Vec3(0.2, 0.6, 0.2)
                    ll_pos = Vec3(-0.50, 0.65, 0.2)


        # ------------------------------------------------------------------- #
        # ART animation block -- separate from light/heavy/charge above.
        # Poses are placeholder; tune A1-A6 values to taste.
        # ------------------------------------------------------------------- #
        if self.state == State.ATTACK_ART and self.current_art is not None:
            sf = self._art_sub_frame
            art = self.current_art
            if art == ArtType.CENTIPEDE:
                # Spinning horizontal strike -- body turns progressively.
                spin = sf * 60.0   # 360 over 6 frames
                if sf == 0:   # A1: telegraph, sword held wide to right
                    ra_rot = Vec3(100, 125, 0)
                    ra_pos = Vec3(0.2, 0.95, 0.5)
                    la_rot = Vec3(5, -85, -95)
                    la_pos = Vec3(-0.55, 1.05, 0.1)
                    h_rot = Vec3(10, 15, 0)
                    h_pos = Vec3(-0.05, 1.47, 0.35)
                    b_rot = Vec3(10, -15, 0)
                    b_pos = Vec3(-0, -0.1, -0)
                    ll_rot = Vec3(0, -20, 20)
                    ll_pos = Vec3(-0.35, 0.6, 0.35)
                    rl_rot = Vec3(35, -10, -10)
                    rl_pos = Vec3(0.25, 0.55, 0.2)
                    self.sword.rotation = Vec3(-30, 105, 120)
                    self.sword.position = Vec3(-0.35, 1.15, 1)
                elif sf == 1:   # A2-A3: windup spin
                    ra_rot = Vec3(115, 125, 0)
                    ra_pos = Vec3(0.2, 0.75, 0.6)
                    la_rot = Vec3(0, -80, -110)
                    la_pos = Vec3(-0.65, 0.85, 0.35)
                    h_rot = Vec3(10, 20, -10)
                    h_pos = Vec3(-0.25, 1.32, 0.6)
                    b_rot = Vec3(20, -25, 0)
                    b_pos = Vec3(-0, -0.15, -0)
                    ll_rot = Vec3(-20, -10, 30)
                    ll_pos = Vec3(-0.4, 0.6, 0.5)
                    rl_rot = Vec3(50, -10, -20)
                    rl_pos = Vec3(0.2, 0.45, 0.4)
                    self.sword.rotation = Vec3(-10, 105, 130)
                    self.sword.position = Vec3(-0.2, 1.15, 1)
                elif sf == 2:
                    self.motion_blur_active = True
                    ra_rot = Vec3(-85, 40, 5)
                    ra_pos = Vec3(0.9, 1.2, -0.15)
                    la_rot = Vec3(-70, 0, 30)
                    la_pos = Vec3(-0.05, 1.3, 0.25)
                    h_rot = Vec3(10, 85, 0)
                    h_pos = Vec3(0.5, 1.47, 0.2)
                    b_rot = Vec3(25, 45, 0)
                    b_pos = Vec3(0, -0, -0.3)
                    ll_rot = Vec3(-10, -45, 25)
                    ll_pos = Vec3(-0.15, 0.65, 0.1)
                    rl_rot = Vec3(-15, 45, -20)
                    rl_pos = Vec3(0.6, 0.65, -0.05)
                    self.sword.rotation = Vec3(-170, 145, 175)
                    self.sword.position = Vec3(1.3, 1.15, 0.55)
                    self.ghost_swords[0].rotation = Vec3(-0, 160, 0)
                    self.ghost_swords[0].position = Vec3(0.475, 1.2, -1.8)
                    self.ghost_swords[1].rotation = Vec3(-0, 120, 0)
                    self.ghost_swords[1].position = Vec3(1.275, 1.2, -1.5)
                    self.ghost_swords[2].rotation = Vec3(-0, 80, 0)
                    self.ghost_swords[2].position = Vec3(1.525, 1.2, -0.7)
                    self.ghost_swords[3].rotation = Vec3(-0, 45, 0)
                    self.ghost_swords[3].position = Vec3(1.625, 1.2, 0.1)

                elif sf == 3:   # A4: strike release
                    self.motion_blur_active = True
                    ra_rot = Vec3(-85, -115, 5)
                    ra_pos = Vec3(-0.55, 1.2, -0.05)
                    la_rot = Vec3(-100, -210, 75)
                    la_pos = Vec3(0.1, 1.3, -0.65)
                    h_rot = Vec3(15, 290, 0)
                    h_pos = Vec3(-0.35, 1.47, -0.4)
                    b_rot = Vec3(25, 235, 0)
                    b_pos = Vec3(0.25, 0, -0.05)
                    ll_rot = Vec3(-20, -180, 20)
                    ll_pos = Vec3(0.15, 0.65, -0.6)
                    rl_rot = Vec3(0, -80, -30)
                    rl_pos = Vec3(-0.4, 0.65, -0.1)
                    self.sword.rotation = Vec3(-180, 340, 170)
                    self.sword.position = Vec3(-1.2, 1.15, -0.55)
                    self.ghost_swords[0].rotation = Vec3(0, 30, 0)
                    self.ghost_swords[0].position = Vec3(0.725, 1.15, 0.65)
                    self.ghost_swords[1].rotation = Vec3(0, -10, 0)
                    self.ghost_swords[1].position = Vec3(-0.075, 1.2, 0.85)
                    self.ghost_swords[2].rotation = Vec3(0, 305, 0)
                    self.ghost_swords[2].position = Vec3(-0.875, 1.2, 0.6)
                    self.ghost_swords[3].rotation = Vec3(0, 260, 0)
                    self.ghost_swords[3].position = Vec3(-1.125, 1.2, -0.1)
                elif sf == 4:
                    ra_rot = Vec3(-115, 170, 25)
                    ra_pos = Vec3(0.45, 1.2, -0.15)
                    la_rot = Vec3(-40, 55, 30)
                    la_pos = Vec3(-0.35, 0.9, 0.4)
                    h_rot = Vec3(10, 0, 0)
                    h_pos = Vec3(0.05, 1.27, 0.4)
                    b_rot = Vec3(40, 30, -10)
                    b_pos = Vec3(-0.15, 0.05, -0.7)
                    ll_rot = Vec3(70, -20, 40)
                    ll_pos = Vec3(-0.25, 0.3, 0.2)
                    rl_rot = Vec3(-20, 25, -25)
                    rl_pos = Vec3(0.45, 0.6, -0.1)
                    self.sword.rotation = Vec3(-185, 255, 160)
                    self.sword.position = Vec3(1, 1.5, -0.7)
                else:   # A5-A6: recovery
                    ra_rot = Vec3(-125, 190, 40)
                    ra_pos = Vec3(0.6, 1.25, -0.15)
                    la_rot = Vec3(-15, 95, 20)
                    la_pos = Vec3(0.05, 0.75, 0.4)
                    h_rot = Vec3(10, 0, 0)
                    h_pos = Vec3(0.45, 1.17, 0.25)
                    b_rot = Vec3(40, 55, -25)
                    b_pos = Vec3(0.05, 0.05, -0.85)
                    ll_rot = Vec3(90, 15, 30)
                    ll_pos = Vec3(0.15, 0.2, -0.05)
                    rl_rot = Vec3(-25, 45, -5)
                    rl_pos = Vec3(0.6, 0.55, -0.3)
                    self.sword.rotation = Vec3(-180, 270, 155)
                    self.sword.position = Vec3(1.1, 1.6, -0.75)

            elif art == ArtType.KAGURA:
                # Aerial: arms raised high overhead.
                if sf == 0:   # A1: gather
                    ra_rot = Vec3(-175, -5, -105)
                    ra_pos = Vec3(0.4, 1.6, -0.1)
                    la_rot = Vec3(-280, 145, 145)
                    la_pos = Vec3(-0.05, 0.5, 0)
                    h_rot = Vec3(-10, 105, -65)
                    h_pos = Vec3(0.15, 1.27, 0.25)
                    b_rot = Vec3(15, 85, -70)
                    b_pos = Vec3(0.1, 0.7, -1.25)
                    ll_rot = Vec3(0, 85, -30)
                    ll_pos = Vec3(0.05, 0.65, -0.6)
                    rl_rot = Vec3(10, 90, -35)
                    rl_pos = Vec3(0.55, 1.3, -0.65)
                    self.sword.rotation = Vec3(90, 0, 5)
                    self.sword.position = Vec3(1.15, 1.3, -0.05)
                elif sf == 1:   # A2-A3: spread
                    offs = 0.1
                    ra_rot = Vec3(-190, 0, -45)
                    ra_pos = Vec3(-0.15, 1.65, -0.05 + offs)
                    la_rot = Vec3(-360, 170, 180 + offs)
                    la_pos = Vec3(0.5, 1, -0.05 + offs)
                    h_rot = Vec3(-75, 135, -55)
                    h_pos = Vec3(0.2, 1.37, 0.15 + offs)
                    b_rot = Vec3(-55, 115, -70)
                    b_pos = Vec3(-0.1, 1, -1.35 + offs)
                    ll_rot = Vec3(-40, 185, 0)
                    ll_pos = Vec3(0.3, 1, -0.8 + offs)
                    rl_rot = Vec3(-35, 140, -40)
                    rl_pos = Vec3(0.05, 1.55, -0.7 + offs)
                    self.sword.rotation = Vec3(390, 100, 95)
                    self.sword.position = Vec3(0.5, 2.15, -0.05)
                elif sf == 2:
                    ra_rot = Vec3(-180, 10, -5)
                    ra_pos = Vec3(-0.3, 1.45, 0.1)
                    la_rot = Vec3(-380, 190, 245)
                    la_pos = Vec3(0.4, 1.65, 0.05)
                    h_rot = Vec3(-145, 95, -90)
                    h_pos = Vec3(0.1, 1.42, 0.3)
                    b_rot = Vec3(-90, 120, -65)
                    b_pos = Vec3(-0.05, 1.4, -1.3)
                    ll_rot = Vec3(-20, 185, 0)
                    ll_pos = Vec3(0.2, 1.35, -0.7)
                    rl_rot = Vec3(-60, 180, 0)
                    rl_pos = Vec3(-0.25, 1.75, -0.45)
                    self.sword.rotation = Vec3(370, 95, 95)
                    self.sword.position = Vec3(-0.1, 2.25, 0.1)
                elif sf == 3:   # A4: release
                    self.motion_blur_active = True
                    offs = 0.3
                    ra_rot = Vec3(-340, -90, 0)
                    ra_pos = Vec3(0.3, 0.85 + offs, 0.2)
                    la_rot = Vec3(-120, 90, 0)
                    la_pos = Vec3(-0.8, 1.2 + offs, -0.25)
                    h_rot = Vec3(45, -25, 25)
                    h_pos = Vec3(0, 1.27 + offs, 0.35)
                    b_rot = Vec3(40, -70, 60)
                    b_pos = Vec3(0.05, 0.7 + offs, -1.15)
                    ll_rot = Vec3(110, -5, -45)
                    ll_pos = Vec3(-0.1, 1 + offs, -0.75)
                    rl_rot = Vec3(55, -40, 45)
                    rl_pos = Vec3(0, 0.65 + offs, -0.15)
                    self.sword.rotation = Vec3(45, -90, 90)
                    self.sword.position = Vec3(0.4, 0.3, 0.2)
                    self.ghost_swords[0].rotation = Vec3(165, 95, 90)
                    self.ghost_swords[0].position = Vec3(-0.975, 0.8, 0.2)
                    self.ghost_swords[1].rotation = Vec3(200, 95, 90)
                    self.ghost_swords[1].position = Vec3(-0.975, 1.55, 0.2)
                    self.ghost_swords[2].rotation = Vec3(235, 95, 90)
                    self.ghost_swords[2].position = Vec3(-0.525, 2.1, 0.2)
                    self.ghost_swords[3].rotation = Vec3(270, 100, 85)
                    self.ghost_swords[3].position = Vec3(0.125, 2.3, 0.25)
                elif sf == 4:
                    self.motion_blur_active = True
                    ra_rot = Vec3(-255, -90, 0)
                    ra_pos = Vec3(0.35, 1.75, 0.1)
                    la_rot = Vec3(-85, -85, 5)
                    la_pos = Vec3(-0.45, 1.35, 0.35)
                    h_rot = Vec3(115, -25, 40)
                    h_pos = Vec3(0.05, 1.62, 0.4)
                    b_rot = Vec3(145, 265, 120)
                    b_pos = Vec3(-0.3, 0.95, -1)
                    ll_rot = Vec3(475, 25, 50)
                    ll_pos = Vec3(-0.3, 0.95, -0.4)
                    rl_rot = Vec3(-15, 45, -50)
                    rl_pos = Vec3(0.15, 1.3, -0.45)
                    self.sword.rotation = Vec3(175, -90, 90)
                    self.sword.position = Vec3(1.15, 2, 0.1)
                    self.ghost_swords[0].rotation = Vec3(25, 95, 90)
                    self.ghost_swords[0].position = Vec3(1.075, 1.35, 0.15)
                    self.ghost_swords[1].rotation = Vec3(50, 95, 90)
                    self.ghost_swords[1].position = Vec3(0.675, 0.75, 0.2)
                    self.ghost_swords[2].rotation = Vec3(85, 95, 90)
                    self.ghost_swords[2].position = Vec3(-0.075, 0.5, 0.2)
                    self.ghost_swords[3].rotation = Vec3(105, 95, 85)
                    self.ghost_swords[3].position = Vec3(-0.825, 0.65, 0.2)
                else:
                    ra_rot = Vec3(-240, -85, 0)
                    ra_pos = Vec3(0.3, 1.75, 0.1)
                    la_rot = Vec3(-60, -75, 5)
                    la_pos = Vec3(-0.35, 1.3, 0.3)
                    h_rot = Vec3(125, -25, 35)
                    h_pos = Vec3(0.05, 1.67, 0.4)
                    b_rot = Vec3(155, 265, 120)
                    b_pos = Vec3(-0.2, 0.95, -1)
                    ll_rot = Vec3(425, 40, 30)
                    ll_pos = Vec3(-0.2, 1.05, -0.25)
                    rl_rot = Vec3(-40, 40, -50)
                    rl_pos = Vec3(0.2, 1.4, -0.3)
                    self.sword.rotation = Vec3(195, -85, 90)
                    self.sword.position = Vec3(1, 2.25, 0)

            elif art == ArtType.HARMONIC:
                # Aerial diagonal downward slashes.
                if sf == 0:   # A1: rise
                    ra_rot = Vec3(-60, -65, 0)
                    ra_pos = Vec3(0.35, 1.2, 0.3)
                    la_rot = Vec3(-190, -10, 150)
                    la_pos = Vec3(-0.3, 1.45, -0.35)
                    h_rot = Vec3(15, 0, 0)
                    h_pos = Vec3(0, 1.52, 0.25)
                    b_rot = Vec3(30, 0, 0)
                    b_pos = Vec3(-0, 0.15, -0.6)
                    ll_rot = Vec3(25, -5, -15)
                    ll_pos = Vec3(-0.35, 0.8, 0.15)
                    rl_rot = Vec3(5, 15, 5)
                    rl_pos = Vec3(0.25, 0.6, -0.1)
                    self.sword.rotation = Vec3(180, 25, 65)
                    self.sword.position = Vec3(-0.35, 0.8, 0.5)
                elif sf == 1:   # A2-A3: aim
                    ra_rot = Vec3(-45, -95, 10)
                    ra_pos = Vec3(0.1, 1.1, 0.4)
                    la_rot = Vec3(-150, 70, 155)
                    la_pos = Vec3(-0.1, 1.3, -0.5)
                    h_rot = Vec3(20, 0, 5)
                    h_pos = Vec3(-0.05, 1.47, 0.3)
                    b_rot = Vec3(40, -20, 0)
                    b_pos = Vec3(0.3, 0.2, -0.7)
                    ll_rot = Vec3(40, -25, -15)
                    ll_pos = Vec3(-0.35, 0.65, 0.05)
                    rl_rot = Vec3(0, -5, 10)
                    rl_pos = Vec3(0.3, 0.55, 0.05)
                    self.sword.rotation = Vec3(190, -10, 110)
                    self.sword.position = Vec3(-0.5, 0.6, 0.1)
                elif sf == 2:
                    offs = -0.2
                    ra_rot = Vec3(-55, -110, 10)
                    ra_pos = Vec3(-0.05 + offs, 1, 0.5)
                    la_rot = Vec3(-150, 35, 160)
                    la_pos = Vec3(-0.05 + offs, 1.3, -0.3)
                    h_rot = Vec3(30, -5, 5)
                    h_pos = Vec3(-0.15 + offs, 1.32, 0.4)
                    b_rot = Vec3(50, -35, 0)
                    b_pos = Vec3(0.5 + offs, 0.25, -0.65)
                    ll_rot = Vec3(30, -20, 20)
                    ll_pos = Vec3(-0.15 + offs, 0.65, 0.05)
                    rl_rot = Vec3(-25, 15, 5)
                    rl_pos = Vec3(0.35 + offs, 0.5, 0.2)
                    self.sword.rotation = Vec3(195, -30, 110)
                    self.sword.position = Vec3(-0.6 + offs, 0.6, 0)
                elif sf == 3:   # A4: slash release
                    self.motion_blur_active = True
                    offs = 0.5
                    ra_rot = Vec3(-180, 5, -45)
                    ra_pos = Vec3(0.35 + offs, 1.4, 0.05)
                    la_rot = Vec3(-55, -140, -5)
                    la_pos = Vec3(-0.5 + offs, 1.15, 0.2)
                    h_rot = Vec3(15, 5, -5)
                    h_pos = Vec3(-0.05 + offs, 1.47, 0.25)
                    b_rot = Vec3(20, 35, -30)
                    b_pos = Vec3(0.3 + offs, 0.15, -0.6)
                    ll_rot = Vec3(-20, -35, -5)
                    ll_pos = Vec3(-0.15 + offs, 0.5, 0.05)
                    rl_rot = Vec3(-15, 0, 10)
                    rl_pos = Vec3(0.5 + offs, 0.9, 0.05)
                    self.sword.rotation = Vec3(-10, 30, -50)
                    self.sword.position = Vec3(0.95 + offs, 2, 0.1)
                    self.ghost_swords[0].rotation = Vec3(-5, 20, -35)
                    self.ghost_swords[0].position = Vec3(0.675 + offs, 1.45, 0.75)
                    self.ghost_swords[1].rotation = Vec3(15, 0, -35)
                    self.ghost_swords[1].position = Vec3(0.075 + offs, 0.9, 0.8)
                    self.ghost_swords[2].rotation = Vec3(35, -40, -20)
                    self.ghost_swords[2].position = Vec3(-0.475 + offs, 0.5, 0.7)
                    self.ghost_swords[3].rotation = Vec3(40, -100, 0)
                    self.ghost_swords[3].position = Vec3(-0.925 + offs, 0.35, 0)
                elif sf == 4:   # A5-A6: land
                    self.motion_blur_active = True
                    ra_rot = Vec3(-20, -10, 120)
                    ra_pos = Vec3(0.05, 1.2, 0.45)
                    la_rot = Vec3(-130, -15, 10)
                    la_pos = Vec3(-0.35, 0.95, -0.35)
                    h_rot = Vec3(10, 0, 5)
                    h_pos = Vec3(0, 1.57, 0.05)
                    b_rot = Vec3(-10, -45, 0)
                    b_pos = Vec3(-0.15, -0.05, 0.2)
                    ll_rot = Vec3(-15, -15, -20)
                    ll_pos = Vec3(-0.4, 0.8, 0.15)
                    rl_rot = Vec3(-20, 10, -20)
                    rl_pos = Vec3(0.15, 0.6, 0.35)
                    self.sword.rotation = Vec3(-30, -35, 35)
                    self.sword.position = Vec3(-0.65, 1.65, 0.25)
                    self.ghost_swords[0].rotation = Vec3(-10, -15, 40)
                    self.ghost_swords[0].position = Vec3(-0.425, 1.4, 0.75)
                    self.ghost_swords[1].rotation = Vec3(0, 0, 35)
                    self.ghost_swords[1].position = Vec3(0.125, 0.95, 0.9)
                    self.ghost_swords[2].rotation = Vec3(20, 25, 210)
                    self.ghost_swords[2].position = Vec3(0.625, 0.6, 0.8)
                    self.ghost_swords[3].rotation = Vec3(40, 60, 5)
                    self.ghost_swords[3].position = Vec3(0.975, 0.45, 0.45)
                else:
                    offs = -0.2
                    ra_rot = Vec3(-20, -25, 120)
                    ra_pos = Vec3(0 + offs, 1.25, 0.4)
                    la_rot = Vec3(-130, -35, 5)
                    la_pos = Vec3(-0.35 + offs, 0.95, -0.4)
                    h_rot = Vec3(10, -5, 10)
                    h_pos = Vec3(-0.05 + offs, 1.57, 0.05)
                    b_rot = Vec3(-20, -55, -5)
                    b_pos = Vec3(-0.3 + offs, 0, 0.4)
                    ll_rot = Vec3(-15, -15, -20)
                    ll_pos = Vec3(-0.45 + offs, 0.9, 0.1)
                    rl_rot = Vec3(-5, 15, 0)
                    rl_pos = Vec3(0.05 + offs, 0.65, 0.5)
                    self.sword.rotation = Vec3(-40, -75, 0)
                    self.sword.position = Vec3(-0.65 + offs, 1.7, 0)

            elif art == ArtType.OVERCLOCK:
                # Rotating flip while moving forward.
                flip_y = sf * 60.0
                if sf == 0:   # A1-A2: start spin
                    offs = 0.2
                    ra_rot = Vec3(-150, 140, 0)
                    ra_pos = Vec3(-0.25, 0.95 + offs, -0.65)
                    la_rot = Vec3(-195, 80, -50)
                    la_pos = Vec3(-0.05, 1.2 + offs, 0.4)
                    h_rot = Vec3(40, 355, 35)
                    h_pos = Vec3(-0.4, 1.17 + offs, -0.15)
                    b_rot = Vec3(-70, -185, 80)
                    b_pos = Vec3(1.15, 0.95 + offs, -0.25)
                    ll_rot = Vec3(-45, 90, 15)
                    ll_pos = Vec3(0.6, 1.05 + offs, 0.1)
                    rl_rot = Vec3(-100, 85, 10)
                    rl_pos = Vec3(0.25, 1.25 + offs, -0.5)
                    self.sword.rotation = Vec3(20, 170, 90)
                    self.sword.position = Vec3(0.05, 1.6 + offs, -1.1)
                elif sf == 1:
                    offs = 0.4
                    ra_rot = Vec3(-210, 175, 0)
                    ra_pos = Vec3(-0.05, 1.25 + offs, -0.5)
                    la_rot = Vec3(-190, 70, -40)
                    la_pos = Vec3(-0.25, 1.1 + offs, 0.5)
                    h_rot = Vec3(65, 330, 35)
                    h_pos = Vec3(-0.4, 1.17 + offs, -0.05)
                    b_rot = Vec3(-115, -190, 80)
                    b_pos = Vec3(1.25, 1.2 + offs, -0.05)
                    ll_rot = Vec3(-15, 50, -40)
                    ll_pos = Vec3(0.55, 1.05 + offs, 0.25)
                    rl_rot = Vec3(-110, 85, -15)
                    rl_pos = Vec3(0.3, 1.5 + offs, -0.25)
                    self.sword.rotation = Vec3(-20, 170, 90)
                    self.sword.position = Vec3(-0.05, 2 + offs, -0.2)
                elif sf == 2:   # A3: mid spin, first slash
                    offs = 0
                    offs_y = 0.3
                    self.motion_blur_active = True
                    ra_rot = Vec3(-165, 160, 185)
                    ra_pos = Vec3(-0.05 + offs, 1.4 + offs_y, 0.25)
                    la_rot = Vec3(-65, -55, -45)
                    la_pos = Vec3(-0.15 + offs, 0.85 + offs_y, -0.35)
                    h_rot = Vec3(40, -25, 35)
                    h_pos = Vec3(-0.35 + offs, 1.37 + offs_y, 0)
                    b_rot = Vec3(55, 340, -70)
                    b_pos = Vec3(1.25 + offs, 1 + offs_y, 0.05)
                    ll_rot = Vec3(85, -100, 10)
                    ll_pos = Vec3(0.55 + offs, 1 + offs_y, -0.2)
                    rl_rot = Vec3(125, -100, 45)
                    rl_pos = Vec3(0.5 + offs, 1 + offs_y, 0.5)
                    self.sword.rotation = Vec3(155, 180, 90)
                    self.sword.position = Vec3(-0.15 + offs, 0.65 + offs_y, 0.55)
                    self.ghost_swords[0].rotation = Vec3(5, -0, 90)
                    self.ghost_swords[0].position = Vec3(-0.125 + offs, 0.9 + offs_y, 0.8)
                    self.ghost_swords[1].rotation = Vec3(-20, -0, 90)
                    self.ghost_swords[1].position = Vec3(-0.125 + offs, 1.35 + offs_y, 0.7)
                    self.ghost_swords[2].rotation = Vec3(-45, -0, 90)
                    self.ghost_swords[2].position = Vec3(-0.125 + offs, 1.8 + offs_y, 0.4)
                    self.ghost_swords[3].rotation = Vec3(-70, -0, 90)
                    self.ghost_swords[3].position = Vec3(-0.125 + offs, 2.05 + offs_y, -0.1)
                elif sf == 3:   # A4: second slash
                    offs = -0.2
                    self.motion_blur_active = True
                    ra_rot = Vec3(-175, 480, 340)
                    ra_pos = Vec3(0 + offs, 0.95, -0.4)
                    la_rot = Vec3(0, -80, 90)
                    la_pos = Vec3(0.1 + offs, 1.65, 0.2)
                    h_rot = Vec3(-115, -10, -20)
                    h_pos = Vec3(-0.25 + offs, 1.32, -0.05)
                    b_rot = Vec3(230, 350, -105)
                    b_pos = Vec3(1.35 + offs, 1, -0.1)
                    ll_rot = Vec3(-50, -200, 60)
                    ll_pos = Vec3(0.7 + offs, 1.35, 0)
                    rl_rot = Vec3(185, -50, 240)
                    rl_pos = Vec3(0.55 + offs, 1.1, -0.5)
                    self.sword.rotation = Vec3(25, 180, 95)
                    self.sword.position = Vec3(-0.05 + offs, 1.65, -0.8)
                    self.ghost_swords[0].rotation = Vec3(125, 0, 90)
                    self.ghost_swords[0].position = Vec3(-0.025 + offs, 0.95, -1.05)
                    self.ghost_swords[1].rotation = Vec3(100, 0, 90)
                    self.ghost_swords[1].position = Vec3(-0.075 + offs, 0.5, -0.6)
                    self.ghost_swords[2].rotation = Vec3(75, 0, 90)
                    self.ghost_swords[2].position = Vec3(-0.125 + offs, 0.45, 0.05)
                    self.ghost_swords[3].rotation = Vec3(50, 0, 85)
                    self.ghost_swords[3].position = Vec3(-0.225 + offs, 0.75, 0.55)

                elif sf == 4:
                    offs = -0.2
                    self.motion_blur_active = True
                    ra_rot = Vec3(-175, 630, 250)
                    ra_pos = Vec3(-0.2, 1.6 + offs, 0.05)
                    la_rot = Vec3(-5, -80, 235)
                    la_pos = Vec3(-0.3, 0.85 + offs, 0.25)
                    h_rot = Vec3(-100, -30, -20)
                    h_pos = Vec3(-0.4, 1.22 + offs, -0.05)
                    b_rot = Vec3(340, 360, -75)
                    b_pos = Vec3(1.05, 0.85 + offs, 0.1)
                    ll_rot = Vec3(0, -365, -35)
                    ll_pos = Vec3(0.35, 0.75 + offs, 0.25)
                    rl_rot = Vec3(210, 240, 120)
                    rl_pos = Vec3(0.45, 1.45 + offs, 0.4)
                    self.sword.rotation = Vec3(-115, 180, 95)
                    self.sword.position = Vec3(-0.2, 1.35 + offs, 0.95)
                    self.ghost_swords[0].rotation = Vec3(285, 5, 90)
                    self.ghost_swords[0].position = Vec3(-0.175, 2.05 + offs, 0.7)
                    self.ghost_swords[1].rotation = Vec3(260, 0, 90)
                    self.ghost_swords[1].position = Vec3(-0.125, 2.15 + offs, 0.15)
                    self.ghost_swords[2].rotation = Vec3(235, 0, 90)
                    self.ghost_swords[2].position = Vec3(-0.125, 1.95 + offs, -0.45)
                    self.ghost_swords[3].rotation = Vec3(200, 0, 90)
                    self.ghost_swords[3].position = Vec3(-0.175, 1.5 + offs, -0.8)
                else:
                    offs = 0.2
                    ra_rot = Vec3(20, 0, 5)
                    ra_pos = Vec3(-0.3 + offs, 0.85, 0.65)
                    la_rot = Vec3(-40, 0, 0)
                    la_pos = Vec3(-0.35 + offs, 0.65, -0.2)
                    h_rot = Vec3(20, -25, -40)
                    h_pos = Vec3(-0.4 + offs, 1.02, 0.2)
                    b_rot = Vec3(50, -55, -40)
                    b_pos = Vec3(0.95 + offs, 0.2, 0.45)
                    ll_rot = Vec3(80, -75, 0)
                    ll_pos = Vec3(0.05 + offs, 0.25, 0.05)
                    rl_rot = Vec3(15, -85, -30)
                    rl_pos = Vec3(0.1 + offs, 0.6, 0.65)
                    self.sword.rotation = Vec3(-180, 180, 90)
                    self.sword.position = Vec3(-0.35 + offs, 0.1, 0.55)

        if _in_any_attack:
            pass   # sword.position/rotation already set by attack or art block above
        elif self.state in _PARRY_STATES or self.state == State.BLOCKING:
            # Raise upright in front (guard stance).
            ra_rot = Vec3(-130, 30, 40)
            ra_pos = Vec3(0.4, 1.15, -0.35)
            la_rot = Vec3(30, 215, -70)
            la_pos = Vec3(-0.5, 0.95, -0.3)
            h_rot = Vec3(10, -20, 5)
            h_pos = Vec3(-0.05, 1.5, -0.05)
            b_rot = Vec3(-10, 30, 0)
            b_pos = Vec3(0, -0.1, 0)
            ll_rot = Vec3(-5, -10, 10)
            ll_pos = Vec3(-0.3, 0.65, 0.2)
            rl_rot = Vec3(0, 70, -15)
            rl_pos = Vec3(0.25, 0.65, -0.3)
            self.sword.rotation = Vec3(35, -95, 540)
            self.sword.position = Vec3(0.05, 1.45, 0.35)

            # Per-phase parry flourish so the three parry states read distinctly.
            # These are the hooks for the parry animation -- tweak freely. BLOCKING
            # keeps the plain guard pose set above.
            if self.state == State.PARRYING:        # p1: catch / raise
                ra_rot = Vec3(-100, -35, 0)
                ra_pos = Vec3(0.7, 1.15, -0.3)
                la_rot = Vec3(-95, -35, -5)
                la_pos = Vec3(-0.3, 1.05, -0.5)
                h_rot = Vec3(0, -20, 0)
                h_pos = Vec3(-0, 1.5, -0.15)
                b_rot = Vec3(-20, 0, 0)
                b_pos = Vec3(0.05, -0.05, 0.35)
                ll_rot = Vec3(-5, -40, 10)
                ll_pos = Vec3(-0.3, 0.6, 0.1)
                rl_rot = Vec3(30, -35, -5)
                rl_pos = Vec3(0.35, 0.6, 0.15)
                self.sword.rotation = Vec3(15, -100, -10)
                self.sword.position = Vec3(0.15, 1.3, 0.35)
            elif self.state == State.PARRYING2:     # p2: deflect (sweep blade across)
                ra_rot = Vec3(-90, -40, -5)
                ra_pos = Vec3(0.35, 1.35, 0.65)
                la_rot = Vec3(-95, -35, -5)
                la_pos = Vec3(-0.65, 1.1, 0.25)
                h_rot = Vec3(10, -20, 0)
                h_pos = Vec3(-0.3, 1.4, 0.65)
                b_rot = Vec3(10, -20, -10)
                b_pos = Vec3(0.2, -0.1, 0.2)
                ll_rot = Vec3(-5, -40, 10)
                ll_pos = Vec3(-0.35, 0.6, 0.15)
                rl_rot = Vec3(25, -40, -5)
                rl_pos = Vec3(0.35, 0.6, 0.3)
                self.sword.rotation = Vec3(5, -110, -5)
                self.sword.position = Vec3(-0.25, 1.35, 1.25)
            elif self.state == State.PARRYING3:     # p3: follow-through / return
                ra_rot = Vec3(-100, -35, -20)
                ra_pos = Vec3(0.25, 1.2, 0.3)
                la_rot = Vec3(-95, -10, 25)
                la_pos = Vec3(-0.55, 1.1, 0.05)
                h_rot = Vec3(10, -30, 5)
                h_pos = Vec3(-0.2, 1.45, 0.4)
                b_rot = Vec3(10, -15, 0)
                b_pos = Vec3(-0.05, -0.1, 0)
                ll_rot = Vec3(-5, -20, 10)
                ll_pos = Vec3(-0.3, 0.6, 0.2)
                rl_rot = Vec3(0, 30, -10)
                rl_pos = Vec3(0.3, 0.6, 0.05)
                self.sword.rotation = Vec3(10, -80, -10)
                self.sword.position = Vec3(-0.1, 1.3, 1)

            
        elif self.state == State.DODGING:
            if not self._dodge_spin:
                b_rot = Vec3(10, 30, 0)
                b_pos = Vec3(0, -0.2, 0)

                ra_rot = Vec3(30, -20, 0)
                ra_pos = Vec3(0.5, bp.y + 0.1, 0)

                la_rot = Vec3(-60, 80, 0)
                la_pos = Vec3(-0.4, bp.y + 0.1, 0.5)

                h_pos = Vec3(0.1, 1.4, 0.2)

                self.sword.rotation = Vec3(10, 40, 0)
                self.sword.position = Vec3(0.7, bp.y - 0.6, -0.2)
                ll_rot = Vec3(70, 0, 0)
                rl_rot = Vec3(0, 0, -10)
                ll_pos = Vec3(-0.2, 0.4, 0.3)
                rl_pos = Vec3(0.3, 0.65, 0.3)
            else:
                ra_rot = Vec3(25, -55, 0)
                ra_pos = Vec3(0.5, 1.1, 0.05)
                la_rot = Vec3(-10, 75, 0)
                la_pos = Vec3(-0.4, 0.85, 0.35)
                h_rot = Vec3(5, 0, 0)
                h_pos = Vec3(0.15, 1.25, 0.35)
                b_rot = Vec3(40, 25, 0)
                b_pos = Vec3(-0.25, 0, -0.55)
                ll_rot = Vec3(70, 15, 0)
                ll_pos = Vec3(-0.2, 0.35, 0.25)
                rl_rot = Vec3(5, 20, -20)
                rl_pos = Vec3(0.3, 0.6, 0)
                self.sword.rotation = Vec3(5, 15, 0)
                self.sword.position = Vec3(0.85, 0.4, -0)
        elif self.state == State.STAGGERED:
            ra_rot = Vec3(25, -10, -150)
            ra_pos = Vec3(0.45, 1.25, 0.1)
            la_rot = Vec3(-20, 45, 105)
            la_pos = Vec3(-0.5, 1.2, 0)
            h_rot = Vec3(-5, -10, 0)
            h_pos = Vec3(-0, 1.52, 0.05)
            b_rot = Vec3(-10, 5, 0)
            b_pos = Vec3(0, -0.1, 0.3)
            ll_rot = Vec3(-10, -20, 5)
            ll_pos = Vec3(-0.3, 0.55, 0.2)
            rl_rot = Vec3(10, 15, -25)
            rl_pos = Vec3(0.3, 0.6, 0.1)
            self.sword.rotation = Vec3(570, 70, 210)
            self.sword.position = Vec3(0.67, 1.9, 0.45)
        elif self.state == State.JUMPING:
            # Airborne tuck: knees up, arms raised for balance, slight lean.
            rising = self.body.velocity.y > 0.0
            if rising:
                ra_rot = Vec3(-12, -7, -25)
                ra_pos = Vec3(0.5, 1.3, -0.2)
                la_rot = Vec3(-7, -3, 20)
                la_pos = Vec3(-0.45, 1.35, -0.15)
                h_rot = Vec3(5, 0, 0)
                h_pos = Vec3(-0, 1.62, -0.1)
                b_rot = Vec3(4, 0, 0)
                b_pos = Vec3(-0, 0, -0.2)
                ll_rot = Vec3(24, 0, 0)
                ll_pos = Vec3(-0.25, 0.9, 0.35)
                rl_rot = Vec3(15, 0, 0)
                rl_pos = Vec3(0.2, 0.6, -0.1)
                self.sword.rotation = Vec3(20, 35, 0)
                self.sword.position = Vec3(0.85, 0.55, 0.1)
            else:
                ra_rot = Vec3(-52, 23, -25)
                ra_pos = Vec3(0.5, 1.3, -0.2)
                la_rot = Vec3(-47, -33, 20)
                la_pos = Vec3(-0.45, 1.35, -0.15)
                h_rot = Vec3(15, 0, 0)
                h_pos = Vec3(0, 1.62, -0.1)
                b_rot = Vec3(-11, 0, 0)
                b_pos = Vec3(0, 0, 0.1)
                ll_rot = Vec3(9, 0, 0)
                ll_pos = Vec3(-0.25, 0.65, 0)
                rl_rot = Vec3(5, 0, 5)
                rl_pos = Vec3(0.2, 0.85, 0.25)
                self.sword.rotation = Vec3(-10, 25, 0)
                self.sword.position = Vec3(1.1, 0.9, 0.3)
        elif self.state == State.LANDING:
            # Touchdown crouch: absorb the landing, knees bent, body dips.
            ra_rot = Vec3(-6, 10, -54)
            ra_pos = Vec3(0.5, 1.2, 0.1)
            la_rot = Vec3(-16, -40, 49)
            la_pos = Vec3(-0.5, 1.15, 0.1)
            h_rot = Vec3(8, 0, 0)
            h_pos = Vec3(0, 1.35, 0.35)
            b_rot = Vec3(34, 0, 0)
            b_pos = Vec3(0, 0.02, -0.6)
            ll_rot = Vec3(23, -15, 5)
            ll_pos = Vec3(-0.3, 0.55, 0.1)
            rl_rot = Vec3(-2, 10, 0)
            rl_pos = Vec3(0.25, 0.6, 0.05)
            self.sword.rotation = Vec3(15, 20, -10)
            self.sword.position = Vec3(1.2, 0.7, 0.1)
        elif self.state == State.DEAD:
            ra_rot = Vec3(-70, 0, -45)
            ra_pos = Vec3(0.5, 1.4, 0)
            la_rot = Vec3(-70, 0, 45)
            la_pos = Vec3(-0.5, 1.4, 0)
            h_rot = Vec3(-15, 0, 0)
            h_pos = Vec3(0, 1.62, -0.05)
            b_rot = Vec3(-0, -0, 0)
            b_pos = Vec3(0, 0, 0)
            ll_rot = Vec3(-30, -10, 10)
            ll_pos = Vec3(-0.25, 0.65, 0.05)
            rl_rot = Vec3(-60, 10, -5)
            rl_pos = Vec3(0.25, 0.65, 0.1)
            self.sword.rotation = Vec3(-25, 10, 105)
            self.sword.position = Vec3(1.075, 1.25, 0.7)
        else:
            # IDLE / MOVING -- procedural locomotion. Legs swing fore/aft from the
            # hip at a cadence that scales with ground speed; arms counter-swing; the
            # upper body bobs on the gait. At rest it eases to a subtle breath sway.
            # (Driven by continuously-advancing phases, so direct assignment each
            # frame stays smooth without lerp.)
            speed_xz = math.hypot(self.body.velocity.x, self.body.velocity.z)
            amt = min(1.0, speed_xz / MOVE_SPEED)
            swing = math.sin(self._gait_phase)
            leg = GAIT_LEG_SWING * amt
            ll_rot = Vec3(swing * leg, 0, 0)
            rl_rot = Vec3(-swing * leg, 0, 0)
            arm = GAIT_ARM_SWING * amt
            ra_rot = Vec3(-swing * arm, 0, 0)
            la_rot = Vec3(swing * arm, 0, 0)
            # Bob rises twice per stride (|sin|); breath sways gently while idle.
            bob = GAIT_BOB * amt * abs(swing)
            breath = (IDLE_BREATH_AMP * (1.0 - amt)
                      * math.sin(self._idle_phase * IDLE_BREATH_FREQ))
            lift = bob + breath
            b_pos = Vec3(0, lift, 0)
            h_pos = Vec3(0, 1.62 + lift, 0)
            ra_pos = Vec3(0.5, bp.y + 0.4 + lift, 0)
            la_pos = Vec3(-0.5, bp.y + 0.4 + lift, 0)
            self.sword.rotation = Vec3(self.sword_base_rot.x, self.sword_base_rot.y,
                                       self.sword_base_rot.z)
            self.sword.position = Vec3(bp.x, bp.y - 0.4 + lift, 0.2)

        self.arm_r_pivot.rotation = ra_rot
        self.arm_l_pivot.rotation = la_rot
        self.arm_r_pivot.position = ra_pos
        self.arm_l_pivot.position = la_pos
        self.head_pivot.rotation = h_rot
        self.head_pivot.position = h_pos
        self.torso_pivot.rotation = b_rot
        self.torso_pivot.position = b_pos
        self.leg_l_pivot.rotation = ll_rot
        self.leg_r_pivot.rotation = rl_rot
        self.leg_l_pivot.position = ll_pos
        self.leg_r_pivot.position = rl_pos

    def _update_body_visual(self):
        """Fade/tilt by state plus two colour cues only: yellow while STAGGERED
        (disabled by a heavy hit or parry), and a brief red flash on taking any
        damage. No anticipatory action telegraphs are coloured."""
        for gs in self.ghost_swords:
            gs.enabled = self.motion_blur_active
        st = self.state
        root_rot = Vec3(0, 0, 0)
        sf = self._art_sub_frame
        art = self.current_art
        # Translucent only while i-frames are live; a dodge's end-lag (incl. the
        # perfect-dodge end-lag) renders solid so the punishable window reads.
        alpha = 0.3 if (st == State.DODGING and self.invulnerable) else 1.0
        if st == State.STAGGERED:
            root_rot = Vec3(-12, 0, 8)
        elif st == State.DEAD:
            root_rot = Vec3(-45, 0, 12)                 # fall over
            self.arm_r_pivot.rotation = Vec3(-70, 0, -45)
            self.arm_l_pivot.rotation = Vec3(-70, 0, 45)
            self.sword.rotation = Vec3(0, 0, 0)
        elif st == State.DODGING and self._dodge_spin:
            # Stationary dodge: spin the body a full 360 over the dodge duration.
            spin_y = min(1.0, self._dodge_spin_t / 1) * 360 * 6
            spin_y = min (360, spin_y)
            root_rot = Vec3(0, spin_y, 0)
        elif st in _PARRY_STATES or st == State.BLOCKING:
            # Twist a touch further through the parry phases so the follow-through
            # reads on the body too (p1 -> p2 -> p3). Another animation hook.
            root_rot = Vec3(0, 40, 0)
        elif art == ArtType.CENTIPEDE and sf == 2:
            root_rot = Vec3(-0, 50, 0)
        else:
            root_rot = Vec3(0, 0, 0)

        # Colour cue priority: stagger (yellow) > successive-dodge (cyan) >
        # damage flash (red). A clean dodge takes no damage, so cyan normally
        # shows alone; the ordering just keeps things deterministic.
        if st == State.STAGGERED:
            cue = _STAGGER_COLOR
        elif self.dodge_success_timer > 0.0:
            cue = _DODGE_SUCCESS_COLOR
        elif self.hit_flash_timer > 0.0:
            cue = _HIT_COLOR
        else:
            cue = None
        for e, base in self.recolor_parts:
            e.color = cue if cue is not None else base
        for e in self.body_parts:
            if e.alpha != alpha:
                e.alpha = alpha
        self.model_root.rotation = root_rot
        


# ----------------------------------------------------------------------------- #
#  Headless smoke test
# ----------------------------------------------------------------------------- #
if __name__ == "__main__":
    # We can't fully construct a Fighter without an Ursina app (Entity needs it),
    # but we can verify the module imports and that the helper logic is sane.
    print("fighter module imports OK")

    # Spot-check helper math.
    u = _xz_unit(Vec3(3, 99, 4))
    assert abs(math.hypot(u.x, u.z) - 1.0) < 1e-6, f"_xz_unit broken: {u}"
    assert abs(u.y) < 1e-6
    print(f"PASS _xz_unit: {u}")

    a = Vec3(1, 0, 0)
    b = Vec3(0, 0, 1)
    mid = _lerp_dir(a, b, 0.5)
    assert abs(math.hypot(mid.x, mid.z) - 1.0) < 1e-6
    print(f"PASS _lerp_dir: {mid}")

    yaw = _yaw_from_forward(Vec3(1, 0, 0))
    assert abs(yaw - 90.0) < 1e-3, f"yaw for +x forward should be 90, got {yaw}"
    yaw = _yaw_from_forward(Vec3(0, 0, 1))
    assert abs(yaw - 0.0) < 1e-3
    print("PASS _yaw_from_forward")

    print("Full Fighter() instantiation requires an Ursina app -- deferred to main.py integration.")
    print("ALL FIGHTER HEADLESS SELF-TESTS PASSED")
