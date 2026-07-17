"""
Riposte - 3D Sword Duel : SHARED CONTRACT
=========================================

This module is the single source of truth that every other module depends on.
It defines tunable gameplay/physics constants and the enums + data tables that
form the interface between the four modules. DO NOT put game logic here.

MODULE INTERFACE CONTRACT (read this before editing your module)
---------------------------------------------------------------

physics.py  -- pure kinematic physics engine (imports: ursina.Vec3, this module)
    class PhysicsBody:
        position: Vec3        # center of the capsule's base-aligned body (feet at position.y)
        velocity: Vec3
        radius: float
        height: float
        mass: float
        on_ground: bool
        is_static: bool
        def apply_impulse(self, impulse: Vec3) -> None   # velocity += impulse / mass
    class PhysicsWorld:
        gravity: Vec3
        bodies: list[PhysicsBody]
        def add_body(self, body: PhysicsBody) -> PhysicsBody
        def step(self, dt: float) -> None    # fixed-timestep accumulator inside; integrates
                                             # gravity, resolves ground (y>=GROUND_Y), circular
                                             # arena wall (radius ARENA_RADIUS), and body-vs-body
                                             # push-out + applies FRICTION/AIR_DAMPING.
    Notes: position.y is the feet height. on_ground True when resting on GROUND_Y.

combat.py  -- pure combat resolution (imports: ursina.Vec3, this module)
    Operates on a duck-typed "Combatant" interface (provided by fighter.py):
        .position: Vec3            # feet position
        .forward: Vec3            # unit facing direction (xz plane)
        .state: State
        .stamina: float
        .invulnerable: bool        # True during dodge i-frames
        .is_blocking: bool
        .parry_active: bool        # True during the parry window
        .riposte_ready: bool       # True if a successful parry primed a riposte
        def take_damage(self, amount, knockback_vec, stagger_time) -> None
        def on_parry_success(self, attack: 'Attack') -> None   # called on the defender who parried
        def on_staggered(self) -> None         # called on the attacker who got parried
    Public functions:
        def in_attack_arc(att_pos, att_forward, target_pos, rng, arc_deg) -> bool
        def resolve_hit(attacker: Combatant, target: Combatant, attack: 'Attack') -> HitResult
            # decides MISSED / DODGED / PARRIED / BLOCKED / HIT, applies damage+knockback
            # +stagger via target.take_damage / on_parry_success / attacker.on_staggered,
            # returns the HitResult. Pure aside from those callbacks.

fighter.py -- Fighter entity (imports: ursina, physics, combat, this module)
    class Fighter(Entity):
        # owns a PhysicsBody (added to the shared PhysicsWorld), a visual capsule + sword,
        # a state machine over `State`, hp/stamina, and exposes the Combatant interface above.
        def __init__(self, world: PhysicsWorld, position, team, color, is_player=False)
        def update_fighter(self, dt, opponent) -> None   # call every frame from main
        def handle_input(self, dt, opponent)             # player only
        def ai_think(self, dt, opponent)                 # ai only
        # syncs Entity world_position from physics body each frame; faces opponent (lock-on).

main.py    -- app/arena/camera/UI/loop (imports: ursina, physics, fighter, this module)
    Builds Ursina app, ground+arena visuals, third-person lock-on camera, HUD
    (HP bars, stamina bars, prompts), spawns player + ai Fighter, drives the loop,
    handles win/lose + restart (R) + quit (Esc).

Keep modules import-clean: physics & combat must NOT import fighter or main.
"""

from enum import Enum
from ursina import Vec3, color


# ----------------------------------------------------------------------------- #
#  Window / display
# ----------------------------------------------------------------------------- #
GAME_TITLE = "Riposte - 3D Sword Duel"
WINDOW_BG = color.rgb32(18, 20, 28)
FULLSCREEN = True
TARGET_FPS = 60


# ----------------------------------------------------------------------------- #
#  Arena
# ----------------------------------------------------------------------------- #
GROUND_Y = 0.0
ARENA_RADIUS = 12.0           # circular arena; bodies are constrained inside this
GROUND_COLOR = color.rgb32(60, 70, 60)
ARENA_RING_COLOR = color.rgb32(120, 110, 90)
SKY_COLOR = color.rgb32(200, 230, 255)


# ----------------------------------------------------------------------------- #
#  Physics
# ----------------------------------------------------------------------------- #
GRAVITY = Vec3(0, -22.0, 0)
FIXED_DT = 1.0 / 120.0        # physics sub-step for stability
MAX_SUBSTEPS = 8
GROUND_FRICTION = 9.0         # per-second velocity damping on ground (horizontal)
AIR_DAMPING = 0.4             # per-second velocity damping in air (horizontal)
RESTITUTION = 0.0             # bounciness off walls/bodies (duel => no bounce)
BODY_PUSH_STIFFNESS = 1.0     # 0..1 how strongly overlapping bodies separate per step


# ----------------------------------------------------------------------------- #
#  Fighter physical body
# ----------------------------------------------------------------------------- #
FIGHTER_RADIUS = 0.5
FIGHTER_HEIGHT = 1.9
FIGHTER_MASS = 1.0
MOVE_SPEED = 5.5              # target ground speed (units/sec)
MOVE_ACCEL = 60.0            # how fast we reach MOVE_SPEED (units/sec^2)
TURN_SPEED = 12.0            # lock-on facing lerp speed
WINDUP_TURN_SPEED = 9.0      # facing lerp during an attack's WINDUP (re-aim/startup);
                             # slightly slower than free lock-on so it reads as aim
                             # assist, not perfect homing -- the active frames commit.


# ----------------------------------------------------------------------------- #
#  Health / stamina
# ----------------------------------------------------------------------------- #
MAX_HP = 200.0
MAX_STAMINA = 100.0
STAMINA_REGEN = 26.0         # per second
STAMINA_REGEN_DELAY = 0.55   # seconds after a stamina-spending action before regen


# ----------------------------------------------------------------------------- #
#  Dodge
# ----------------------------------------------------------------------------- #
DODGE_IMPULSE = 15.0         # impulse magnitude applied in dodge direction
DODGE_IFRAMES = 0.36         # seconds of invulnerability
DODGE_DURATION = 0.45        # locked-in dodge state length
DODGE_STAMINA = 24.0
DODGE_COOLDOWN = 0.15
# Small stamina refund for a dodge that actually evaded an attack. Must stay
# below DODGE_STAMINA so a clean dodge is still a net stamina loss (defending
# is rewarded, but not free).
DODGE_REFUND = 8.0
# A perfect (successive) dodge cancels MOST of the dodge's lockout so the dodger
# can counter quickly -- but NOT all of it. This short end-lag keeps the counter
# from coming out frame-perfect, so the whiffing attacker can still parry the
# follow-up. Without it a fast light's counter lands before the whiffer can act
# (impossible to answer); long-recovery heavies stay punishable either way. Keep
# it small -- too large guts the perfect-dodge reward.
DODGE_SUCCESS_ENDLAG = 0.15


# ----------------------------------------------------------------------------- #
#  Jump / aerial
# ----------------------------------------------------------------------------- #
# Upward impulse on jump. With GRAVITY -22, v=sqrt(2*g*h): ~11 reaches ~2.75u apex
# (~0.5s up, ~1.0s round trip) -- a readable hop you can attack out of or use to
# float over a ground swing. Mass is 1.0 so impulse == launch velocity.
JUMP_IMPULSE = 8.0
JUMP_STAMINA = 14.0
# Horizontal control while airborne, as a fraction of the ground accel/speed. Low
# enough that a jump is a commitment (you mostly keep your launch momentum), high
# enough to steer a jump-in.
AIR_CONTROL = 0.45           # fraction of MOVE_ACCEL usable in the air
AIR_MOVE_SPEED = MOVE_SPEED * 0.85
# Brief recovery on touchdown -- you can't act for this long after landing, so a
# whiffed jump-in / aerial is punishable.
LANDING_LAG = 0.12
# A heavier landing (after an aerial slam) locks you out a touch longer.
AERIAL_LANDING_LAG = 0.22
# Minimum airtime before a landing can be detected, so the launch frame (still
# flagged on_ground from the previous physics step) doesn't instantly "land".
MIN_AIRTIME = 0.06
# Downward slam impulse added when an aerial strike commits -- a fast, committed
# dive rather than a floaty drop.
AERIAL_SLAM_IMPULSE = 16.0
# Vertical reach (feet-to-feet |dy| tolerance) for a *ground* attack. A target
# that has jumped above this is out of reach -- so a well-timed jump floats over a
# ground swing. Aerial attacks carry a much larger downward reach (see ATTACKS).
GROUND_VERTICAL_REACH = 1.7


# ----------------------------------------------------------------------------- #
#  Animation (procedural pose smoothing + gait)
# ----------------------------------------------------------------------------- #
# Pose pivots ease toward their per-state targets instead of snapping. The lerp is
# frame-rate-independent: t = 1 - exp(-speed*dt). A higher speed during attacks
# keeps swings crisp; the lower base speed smooths idle/move/transition.
ANIM_LERP_SPEED = 15.0          # base easing for idle/move/defend/transition
ANIM_ATTACK_LERP_SPEED = 30.0   # snappier easing while an attack pose is live
# Walk cycle: legs/arms swing at a frequency that scales with ground speed, so a
# fast run steps faster than a slow walk. Phase advances in _tick_timers.
GAIT_FREQ_BASE = 2.0            # rad/sec baseline cadence
GAIT_FREQ_PER_SPEED = 1.6       # extra rad/sec per unit of horizontal speed
GAIT_LEG_SWING = 38.0          # peak hip swing (degrees) at full run
GAIT_ARM_SWING = 22.0          # peak counter-swing of the arms (degrees)
GAIT_BOB = 0.07                # vertical body bob amplitude (units) at full run
IDLE_BREATH_FREQ = 1.6         # rad/sec idle breathing cadence
IDLE_BREATH_AMP = 0.025        # idle vertical sway amplitude (units)


# ----------------------------------------------------------------------------- #
#  Charge attack (dash)
# ----------------------------------------------------------------------------- #
# Forward impulse applied at the start of the charge's stage-2 (ACTIVE2). Must be
# larger than DODGE_IMPULSE so the charge closes more distance than a dodge --
# that's its whole purpose as a gap-closer.
CHARGE_DASH_IMPULSE = 90.0


# ----------------------------------------------------------------------------- #
#  Parry / block / riposte
# ----------------------------------------------------------------------------- #
# Parry plays out over three animatable phases (p1 -> p2 -> p3, the PARRYING /
# PARRYING2 / PARRYING3 states). p1 is a WINDUP -- NOT an active parry frame: a
# hit landing during p1 connects like a normal hit (and cancels the parry). The
# deflect window is active for p2 + p3 only; a hit during either is parried. A
# successful parry follows through the rest of the animation instead of snapping
# to idle, and the opponent is staggered (PARRY_STAGGER_TIME) meanwhile, so the
# parrier still has time to act/riposte afterward.
PARRY_P1_DURATION = 0.05   # phase 1: WINDUP (no deflect)
PARRY_P2_DURATION = 0.1    # phase 2: deflect (active)
PARRY_P3_DURATION = 0.08   # phase 3: deflect / follow-through (active)
# Active deflect window = p2 + p3 (p1 is windup).
PARRY_WINDOW = PARRY_P2_DURATION + PARRY_P3_DURATION
# End-lag after a WHIFFED parry (deflect window expired without catching anything).
# The fighter is stalled and cannot act for this long -- the punish that keeps a
# reactive/habitual parry honest. A SUCCESSFUL parry pays no end-lag.
PARRY_WHIFF_RECOVERY = 0.3
PARRY_STAMINA = 10.0
# Small refund on a successful parry. Must stay below PARRY_STAMINA so a parry
# is a net stamina loss -- defending is rewarded, but not free. (A heavy that
# breaks a guard is the exception: it refunds its full cost, see on_guard_break.)
PARRY_REFUND = 5.0
# Parrying a heavy-type attack (HEAVY or CHARGE) refunds more stamina than a
# light parry -- reading and timing a slow, committed swing is rewarded harder.
PARRY_HEAVY_REFUND = 20
# Short stagger: a parried fighter recovers fast -- by design, fast enough to
# parry the punishing riposte. Must stay well below BLOCK_HEAVY_STAGGER_TIME.
PARRY_STAGGER_TIME = 0.3
RIPOSTE_WINDOW = 1.20        # seconds after a parry during which a riposte is buffed
RIPOSTE_DAMAGE_MULT = 2.0

BLOCK_DAMAGE_MULT = 0.25     # damage taken while blocking
BLOCK_STAMINA_PER_HIT = 12.0 # stamina cost when a block absorbs a hit
BLOCK_CHIP = 0.10            # fraction of damage that still chips HP through a block
# Blocking a HEAVY staggers the blocker for a long time (heavy attacks are now a
# guard-breaking threat). A clean unguarded hit never staggers -- it just deals
# big damage. Blocking a light never staggers.
BLOCK_HEAVY_STAGGER_TIME = 1.50


# ----------------------------------------------------------------------------- #
#  Feint
# ----------------------------------------------------------------------------- #
# A feint fakes an attack: WINDUP and stage-1 ACTIVE animate fully, but the
# committed stage-2 (ACTIVE2, hitbox-live) is SKIPPED -- the fighter snaps back to
# IDLE, free to act again. The stamina was already spent at attack start and is
# NOT refunded, so a feint costs the full attack's stamina. Its purpose is mind-
# games: a defender who pre-commits a parry/read to your windup whiffs on a feint
# and eats the recovery. Two caveats balance it:
#   1. Getting HIT during a feinted swing's pre-commit window (WINDUP / stage-1
#      ACTIVE) GUARD-BREAKS the feinter (long stagger) instead of just cancelling
#      the swing -- feinting into an incoming attack is punished hard.
#   2. No two consecutive attacks may both be feinted: a feint arms a cooldown.
FEINT_COOLDOWN = 1.0            # seconds before another swing may be feinted
# Being caught mid-feint is a guard-break -- reuse the heavy-block stagger length
# so all guard-breaks feel equally punishing.
FEINT_GUARD_BREAK_STAGGER = BLOCK_HEAVY_STAGGER_TIME
# After the AI resolves a feint it gets a brief window to slam out a committed
# follow-up swing while the opponent is still reacting to the fake -- this is what
# makes a feint a threat instead of a wasted action. Window from the feint resolve.
FEINT_FOLLOWUP_WINDOW = 0.55
# Half the time, instead of slamming the follow-up immediately, the AI baits: it
# waits this much longer before the follow-up swing, so the opponent can't treat
# the post-feint timing as fixed (and can't pre-load a parry/dodge on it).
FEINT_FOLLOWUP_DELAY_CHANCE = 0.5
FEINT_FOLLOWUP_DELAY = 0.5


# ----------------------------------------------------------------------------- #
#  Enums (interface types)
# ----------------------------------------------------------------------------- #
class State(Enum):
    IDLE = 0
    MOVING = 1
    ATTACK_WINDUP = 2
    ATTACK_ACTIVE = 3          # attack stage 1
    ATTACK_RECOVERY = 4
    DODGING = 5
    PARRYING = 6
    BLOCKING = 7
    STAGGERED = 8
    DEAD = 9
    ATTACK_ACTIVE2 = 10        # attack stage 2 (windup -> active -> active2 -> recovery)
    JUMPING = 11               # airborne (rising or falling), free to steer + air-attack
    LANDING = 12               # brief touchdown recovery (cannot act)
    ATTACK_ART = 13            # art execution (A1-A6 sub-frames, tracked separately)
    PARRYING2 = 14             # parry follow-through phase 2 (p2, animate)
    PARRYING3 = 15             # parry follow-through phase 3 (p3, animate)
    # NOTE: the airborne plunge reuses the ATTACK_* states (with current_attack =
    # ATTACKS[AERIAL]) while _airborne stays True -- so hit resolution, riposte and
    # AI "is-attacking" detection all apply unchanged. No separate air-attack state.


class AttackType(Enum):
    LIGHT = 0
    HEAVY = 1
    CHARGE = 2      # dash attack: lunges forward on stage 2 (see CHARGE_DASH_IMPULSE)
    AERIAL = 3      # airborne plunge: a downward slam thrown out of a jump


class ArtType(Enum):
    CENTIPEDE = 0   # horizontal 360 strike, expanding ring
    KAGURA = 1      # aerial sphere of rings around the user
    HARMONIC = 2    # aerial, two diagonal crescent slashes toward opponent
    OVERCLOCK = 3   # horizontal flip, two short-range crescent slashes while moving


class HitResult(Enum):
    MISSED = 0
    HIT = 1
    BLOCKED = 2
    PARRIED = 3
    DODGED = 4


class Difficulty(Enum):
    MEDIUM = 0     # the established baseline: less exploitable, leaves clear openings
    HIGH = 1       # leans on the attention read to press harder, feint more, adapt fast


# ----------------------------------------------------------------------------- #
#  Attack data table
# ----------------------------------------------------------------------------- #
class Attack:
    """Static description of a sword attack (timings in seconds)."""
    def __init__(self, atype, damage, windup, active, recovery, rng, arc_deg,
                 knockback, stamina, active2=0.10,
                 vertical_reach=GROUND_VERTICAL_REACH):
        self.atype = atype
        self.damage = damage
        self.windup = windup        # ATTACK_WINDUP duration (telegraph)
        self.active = active        # ATTACK_ACTIVE duration (stage 1, hitbox live)
        self.active2 = active2      # ATTACK_ACTIVE2 duration (stage 2, hitbox live)
        self.recovery = recovery    # ATTACK_RECOVERY duration (vulnerable)
        self.range = rng            # reach from attacker center
        self.arc_deg = arc_deg      # half? -> full cone angle in degrees
        self.knockback = knockback  # impulse magnitude applied to target
        self.stamina = stamina
        # Feet-to-feet |dy| tolerance for the hit to connect. Ground attacks use a
        # short reach (a jump floats over them); the aerial plunge reaches far down.
        self.vertical_reach = vertical_reach

    @property
    def total_time(self):
        return self.windup + self.active + self.active2 + self.recovery


ATTACKS = {
    AttackType.LIGHT: Attack(AttackType.LIGHT, damage=9.0, windup=0.14, active=0.12,
                             active2=0.18, recovery=0.12, rng=2.3, arc_deg=85.0,
                             knockback=4.0, stamina=10.0),
    AttackType.HEAVY: Attack(AttackType.HEAVY, damage=20.0, windup=0.32, active=0.2,
                             active2=0.1, recovery=0.2, rng=2.7, arc_deg=70.0,
                             knockback=9.5, stamina=20.0),
    # Charge: a heavy-type dash attack. Slow heavy windup (0.42) telegraphs it, but
    # it lunges forward on stage 2 and only deals light damage (9.0) with light end
    # lag (0.12 recovery) -- it's a gap-closer, not a damage tool. Like the heavy it
    # staggers a blocker (guard-break). active2 is held a touch long so the dash
    # carries the hitbox across the closed distance.
    AttackType.CHARGE: Attack(AttackType.CHARGE, damage=8.0, windup=0.6, active=0.12,
                              active2=0.1, recovery=0.2, rng=2.5, arc_deg=75.0,
                              knockback=10.0, stamina=30.0),
    # Aerial plunge: thrown out of a jump. Short windup, then a long active window
    # that stays live through the dive (so the downward slam connects as you fall),
    # a wide arc and a big DOWNWARD vertical reach so it hits a grounded target. A
    # clean unguarded hit does solid damage with strong knockback; it is heavy-TYPE
    # for guard interactions (blocking it guard-breaks), like the charge. Landing it
    # also kicks up a shockwave (see fighter on touchdown).
    AttackType.AERIAL: Attack(AttackType.AERIAL, damage=16.0, windup=0.20, active=0.12,
                              active2=0.12, recovery=0.3, rng=2.4, arc_deg=180.0,
                              knockback=9.0, stamina=16.0, vertical_reach=3.2),
}


# Per-attack-type multiplier on the AI's feint chance (see the Feint section and
# Fighter.ai_think). A light is fast and cheap -- the natural fake to bait a
# reaction -- so it feints more; a charge is slow and stamina-expensive, so
# faking one is wasteful and happens slightly less. Heavy stays at baseline.
FEINT_TYPE_MULT = {
    AttackType.LIGHT: 3,
    AttackType.HEAVY: 1.0,
    AttackType.CHARGE: 0.8,
    AttackType.AERIAL: 0.0,   # aerials are committed dives -- never feinted
}


# ----------------------------------------------------------------------------- #
#  Arts system
# ----------------------------------------------------------------------------- #
# Arts are special moves with i-frames, projectile hitboxes, and their own cooldowns.
# The fighter state ATTACK_ART carries a sub-frame index 0-5 (A1-A6).

# Starting stamina cost for all arts. Decays per combat round.
ART_STAMINA_START = 50.0
ART_STAMINA_MIN = 20.0           # floor after decay
ART_STAMINA_DECAY_AMOUNT = 4.0   # stamina units reduced every ART_DECAY_INTERVAL seconds
ART_DECAY_INTERVAL = 15.0        # seconds of combat time before each decay tick

# Starting cooldown (seconds) for all arts. Decays per combat round.
ART_COOLDOWN_START = 15.0
ART_COOLDOWN_MIN = 7.0           # floor after decay
ART_COOLDOWN_DECAY_AMOUNT = 1.0  # seconds reduced from cooldown every ART_DECAY_INTERVAL

# When an art projectile is parried: art-user is NOT staggered; parrier is knocked back.
ART_PARRY_KNOCKBACK = 20.0        # units of knockback impulse to the parrier

# Blocking an art staggers (long). Placeholder reuses BLOCK_HEAVY_STAGGER_TIME.
# When a dodge perfectly avoids an art projectile: reset dodge cooldown immediately.

# OVERCLOCK: character moves forward slowly during A1-A6.
ART_OVERCLOCK_MOVE_SPEED = 4.0   # units/frame of forward drift during execution

# Art frame durations (6 frames A1-A6, in seconds) per art type.
# A1 = telegraph (glint + sparks), A2-A3 = wind-up, A4 = spawn projectile(s),
# A5 = projectile travel, A6 = recovery.
ART_FRAME_DURATIONS = {
    ArtType.CENTIPEDE: [0.2, 0.3, 0.1, 0.1, 0.1, 0.3],  # total ~0.98s
    ArtType.KAGURA:    [0.15, 0.2, 0.3, 0.10, 0.1, 0.3],  # total ~1.02s
    ArtType.HARMONIC:  [0.3, 0.3, 0.2, 0.1, 0.1, 0.4],  # total ~1.04s
    ArtType.OVERCLOCK: [0.15, 0.15, 0.1, 0.1, 0.1, 0.3],  # total ~0.88s
}

# CENTIPEDE: expanding ring sprite radius (starts at ORIGIN_RADIUS, expands to MAX_RADIUS).
CENTIPEDE_RING_ORIGIN_RADIUS = 0.8  # ring start radius around user
CENTIPEDE_RING_MAX_RADIUS = 6.0     # ~half the arena (ARENA_RADIUS=12)
CENTIPEDE_RING_EXPAND_SPEED = 20.0  # units/sec expansion
CENTIPEDE_RING_HEIGHT = 0.9         # height above ground

# KAGURA: many ring sprites expanding locally. One sphere hitbox.
KAGURA_RING_COUNT = 10             # number of ring sprites
KAGURA_RING_ORIGIN_RADIUS = 0.1
KAGURA_RING_MAX_RADIUS = 5.0        # ~quarter arena
KAGURA_RING_EXPAND_SPEED = 12.0
KAGURA_RING_HEIGHT_BASE = 1.7      # vertical offset from character root (feet) to spawn rings; ~body height. Tunable.
KAGURA_RING_SPREAD = 1            # vertical spread of the ring planes

# HARMONIC: two crescent slashes fired at A4 toward opponent's last known position.
HARMONIC_CRESCENT_SPEED = 20.0      # units/sec travel speed (same as charge dash feel)
HARMONIC_CRESCENT_HEIGHT = 1.0     # vertical offset from character root (feet) to spawn crescents; ~body centre. Tunable.
HARMONIC_TARGET_HEIGHT = 1.1        # body altitude the diagonal descent aims for
HARMONIC_DELAY_BETWEEN = 0.2       # seconds between first and second crescent fire

# OVERCLOCK: a stationary vertical ring slash, then a vertical crescent slash.
OVERCLOCK_TORSO_HEIGHT = 1.05       # both sprites sit at the torso altitude
OVERCLOCK_RING_MAX_RADIUS = 3     # short-range expanding ring (first slash)
OVERCLOCK_RING_EXPAND_SPEED = 9.0  # units/sec expansion
OVERCLOCK_CRESCENT_SPEED = 17.0
OVERCLOCK_CRESCENT_MAX_DIST = 4.0   # units of travel before despawn
OVERCLOCK_DELAY_BETWEEN = 0.4      # seconds between slashes

# Dynamic camera per art: position offset and camera angle for A1-A3 states.
# Format: {'offset': Vec3(x,y,z), 'pitch': deg, 'yaw_offset': deg}
# These are offsets/overrides applied instead of the normal follow-cam.
# Tunable placeholder values -- adjust in-game feel.
DYNAMIC_CAMERA_KEY = 'y'
ART_CAM_POSES = {
    ArtType.CENTIPEDE: {'back': -6.0, 'height': 3, 'side': -1.0, 'fov': 80},
    ArtType.KAGURA:    {'back': -6.5, 'height': 3, 'side': -1.0, 'fov': 85},
    ArtType.HARMONIC:  {'back': -6, 'height': 3, 'side': -1, 'fov': 80},
    ArtType.OVERCLOCK: {'back': -6.0, 'height': 3, 'side': -1.0, 'fov': 75},
}
ART_CAM_BLEND_SPEED = 1.0    # lerp speed when blending back to normal cam after A3
# During the blend-back the camera keeps its aim fully on the player until the
# blend-back progress (1 -> 0) drops below this, then eases to the normal midpoint
# framing over the remaining tail. Higher = start easing to normal sooner; lower =
# hold the player-lock longer. Keeps the player from clipping the frame edge.
ART_CAM_AIM_TAIL = 0.4


# ----------------------------------------------------------------------------- #
#  Colors / visuals
# ----------------------------------------------------------------------------- #
PLAYER_COLOR = color.rgb32(70, 140, 220)
ENEMY_COLOR = color.rgb32(210, 80, 70)
SWORD_COLOR = color.rgb32(220, 220, 230)
SWORD_GLOW_PARRY = color.rgb32(255, 230, 120)

# Ghost sword alphas (4 ghosts, index 0 = most opaque).
GHOST_SWORD_ALPHAS = (0.8, 0.6, 0.4, 0.2)

# Ring sprite thickness (visual band width, world units) per art.
CENTIPEDE_RING_THICKNESS = 2
KAGURA_RING_THICKNESS = 0.5
OVERCLOCK_RING_THICKNESS = 0.8


# ----------------------------------------------------------------------------- #
#  Controls (player) -- documented for UI prompts
# ----------------------------------------------------------------------------- #
CONTROLS_TEXT = (
    "WASD move  |  SPACE jump  |  J light  |  R heavy  |  T charge  |  "
    "J/R in air = aerial  |  I feint  |  Q/SHIFT dodge  |  F block/parry  |  "
    "1/2 arts (ground/air)  |  Y dyn-cam  |  G difficulty  |  K/C battlefield  |  H fog  |  "
    "BACKSPACE restart  |  ESC quit"
)


# ----------------------------------------------------------------------------- #
#  AI tuning
# ----------------------------------------------------------------------------- #
# The AI reads your swings and commits a defense the moment a hit is imminent
# (stage-1 ACTIVE, which precedes the hitbox-live ACTIVE2), so its parries/dodges
# actually connect. The skill values below are the chance it *chooses* to defend
# a given swing -- lower them to make the AI more beatable, raise to punish full
# offense harder. It also punishes: ripostes after a parry, counters after a
# successive dodge, and uses heavies to break a turtling guard.
# Per-swing defense odds (parry+block for lights ~0.75; dodge+parry for heavies
# ~0.75). They intentionally leave a ~25% gap so offense still lands sometimes.
AI_AGGRESSION = 0.60         # 0..1 pressure to press an attack when in range
AI_PARRY_SKILL = 0.55        # 0..1 chance AI parries a telegraphed light swing
AI_DODGE_SKILL = 0.50        # 0..1 chance AI dodges (favoured vs heavies)
AI_BLOCK_SKILL = 0.20        # 0..1 chance AI blocks a light as a fallback
# Hold INSIDE light range (2.3) so the AI is actually threatening -- at the old
# 2.2 it circled just outside its own reach and never committed.
AI_PREFERRED_RANGE = 1.9     # AI tries to hold around this distance
# Anti-turtle mix: how the AI answers a held guard. A reflexive heavy is readable
# and gets perfect-dodged into a free punish, so the response is randomized across
# TWO guard-breakers (heavy and the cheaper charge -- mixing them stops the player
# pattern-matching a single guard-break timing) plus a fast chip light (hard to
# pre-dodge, drains guard stamina + chips HP) and an occasional feint (skip the
# swing so a pre-loaded dodge whiffs). The charge is fired stationary (no lunge)
# since the turtle is already in reach. CHARGE + HEAVY + LIGHT must stay below 1.0
# -- the remainder is the feint chance.
AI_TURTLE_CHARGE_CHANCE = 0.12  # chance to guard-break with a (stationary) charge vs a blocker
AI_TURTLE_HEAVY_CHANCE = 0.08  # chance to commit the guard-break heavy vs a blocker
AI_TURTLE_LIGHT_CHANCE = 0.70   # chance to chip with a light vs a blocker
# Basic close-combat mix: chance the AI picks a (stationary, non-lunging) charge
# as an in-range attack against an open opponent, instead of a light/heavy. The
# charge is a slow, telegraphed, guard-breaking strike for light damage -- a
# third mix-up option alongside light and heavy. Keep low enough that the slow
# windup doesn't make the AI a free parry target. (The anti-turtle response to a
# held guard has its own mix -- see AI_TURTLE_*.)
AI_CHARGE_CHANCE = 0.18         # chance to mix a stationary charge into in-range offense
# Chase gap-close: in a pure footrace (opponent backing off to regen, both at the
# same MOVE_SPEED so a straight chase never closes), the AI spends a dodge to
# lunge into range. Only when stamina is at/above this floor, so it never digs
# into the reserves it needs to actually fight. One lunge drops it below the
# floor, so it won't chain dodges to exhaustion.
AI_GAPCLOSE_STAMINA = 60.0      # min stamina before the AI dodges to close distance
# How close to the arena wall (radius ARENA_RADIUS) the AI is considered "cornered".
# Within this margin, a retreat (into the wall OR simply away from the player) is
# replaced by a circle AROUND the opponent instead of the AI pinning itself. Wider
# than a hair so the AI commits to circling BEFORE it is flush against the wall.
AI_WALL_MARGIN = 3.5
# Chase charge: when the opponent is actively RETREATING (a real chase, not a
# standstill), the AI answers with a lunging CHARGE that catches the kiter and
# forces the engagement. This is a probabilistic-over-TIME commit (per-frame
# chance = rate * intensity * dt), NOT a fire-the-instant-you're-in-range gate --
# so the charge lands at a varying moment in the pursuit and the player can't
# pre-load a parry on a fixed "now in range -> charge incoming" tell. Higher =
# commits the charge sooner/more often during a chase. Expected delay before
# committing at intensity 1.0 is roughly 1/AI_CHASE_CHARGE_RATE seconds.
AI_CHASE_CHARGE_RATE = 3.0      # per-second commit rate for the chase charge
# Aerial jump-in: per-second chance the AI leaps in with a plunge as an offensive
# mix-up when at medium range. Kept low so it's an occasional change-of-angle, not
# a reflex (and it's a committed, punishable approach). Scaled by intensity and the
# difficulty's aggression_mult.
AI_JUMP_IN_RATE = 0.5

# ----- AI arts (offense + reaction) ------------------------------------------ #
# The per-difficulty rates live in DIFFICULTY_PROFILES (art_use_rate /
# art_react_skill). These are difficulty-independent shaping constants.
AI_ART_GLOBAL_COOLDOWN = 4.0    # min seconds between the AI's own art casts
AI_ART_STAMINA_BUFFER = 12.0    # keep this much stamina ABOVE an art's cost
AI_ART_PARRY_FRACTION = 0.5     # fraction of art reactions that parry (rest dodge)
# How early (seconds before the projectile is estimated to connect) the AI commits
# its art reaction, so the dodge i-frames / parry window are LIVE when it arrives.
#
# These are set to each defense's COVERAGE DURATION (minus a small safety margin),
# NOT a tiny fixed lead. The reaction-timing model can't track a projectile in
# flight, so its estimated impact_eta plateaus at the full travel time (dist/speed)
# once the projectile spawns. A lead SMALLER than that plateau is unreachable -- the
# trigger never fires and the AI simply never reacts beyond point-blank (this was a
# latent bug: slow arts like OVERCLOCK, speed 10, were only reactable within ~1.8u).
# Firing when impact is within the i-frame / parry window instead keeps the defense
# active at arrival across realistic distances (dodge reaches farther than parry
# because its i-frames last far longer than the short parry window).
AI_ART_REACT_MARGIN = 0.05     # safety margin so the defense is still live at impact
AI_ART_DODGE_LEAD = DODGE_IFRAMES - AI_ART_REACT_MARGIN                     # ~0.31
AI_ART_PARRY_LEAD = PARRY_P1_DURATION + PARRY_WINDOW - AI_ART_REACT_MARGIN  # ~0.18
# Dodge lead against a TRACKED in-flight projectile (real eta counts down, so we can
# commit late). Kept short so a single dodge's i-frames (DODGE_IFRAMES) bracket a
# two-part art whose second hit lands soon after the first (e.g. HARMONIC's paired
# crescents, HARMONIC_DELAY_BETWEEN apart): fire at ~0.14 -> i-frames cover the first
# now and the second ~0.2s later, both inside the 0.36s window.
AI_ART_TRACK_DODGE_LEAD = 0.14
# Defensive art: when an incoming swing is imminent and a reaction wasn't already
# committed, the AI may instead burn an art for its immediate full i-frame window.
AI_ART_PANIC_CHANCE = 0.5       # scaled by art_use_rate + intensity

# ----- AI advanced art behaviour (gated by profile 'art_advanced', HIGH only) - #
# These shape a more REALISTIC art response and smarter art usage. They apply only
# where DIFFICULTY_PROFILES[...]['art_advanced'] is True. Balanced tuning: clearly
# present, but roughly difficulty-neutral overall (realism can even make the AI a
# touch MORE beatable -- it no longer reacts to everything or parries flawlessly).
#
# Parry rhythm: the AI's own recent melee parries accrue a decaying tally. When the
# fight has been a parry-exchange, a tempo-changing art is more likely met with a
# parry (the rhythm carries over) than a dodge. REF = tally at which the bonus maxes.
AI_PARRY_RHYTHM_REF = 3.0
AI_PARRY_RHYTHM_DECAY = 0.5      # multiplicative per second (half-life ~1s)
AI_ART_RHYTHM_PARRY_BONUS = 0.4  # added to parry-preference at full rhythm
# Guard breaks under pressure: a committed art PARRY is NOT a guaranteed clean
# defence (applies to BOTH difficulties). Under pressure it may MISTIME (fires
# off-window so it has lapsed by impact -> the art lands), or -- when pressured
# enough -- collapse into a PANIC BLOCK (blocking an art is a long stagger, i.e. a
# real guard-break). Both chances scale with pressure (low stamina / stamina
# deficit), are reduced by the difficulty's art_react_skill, and are further scaled
# by the profile's art_guard_break_mult (MEDIUM cracks more than HIGH).
AI_ART_PARRY_MISTIME_BASE = 0.10
AI_ART_PARRY_MISTIME_PRESSURE = 0.35
AI_ART_PANIC_BLOCK_CHANCE = 0.18
# The AI only panic-blocks an art once its pressure exceeds this floor.
AI_ART_PANIC_MIN_PRESSURE = 0.5

# ----- AI smart art usage (gated by profile 'art_advanced', HIGH only) -------- #
# Prefer a two-strike art (OVERCLOCK: ring + crescent) to punish a LONG stagger
# (guard-break) instead of a single heavy -- only when the stagger will outlast the
# art's startup. Rolled probabilistically so it isn't a deterministic tell.
AI_ART_PUNISH_CHANCE = 0.5
# Minimum remaining stagger on the opponent before an art-punish is worth starting
# (must clear OVERCLOCK's startup to first strike, ~0.5s, with margin).
AI_ART_PUNISH_MIN_STAGGER = 0.7
# Against a held guard (turtle), chance to answer with an art (an art vs a block is
# a guard-break) as an extra option beyond the charge/heavy guard-break mix.
AI_ART_ANTITURTLE_CHANCE = 0.22
# While chasing a RECEDING opponent, per-second chance to open with a reaching
# ranged ground art (OVERCLOCK closes distance / CENTIPEDE ring) rather than always
# the chase-charge. Scaled by intensity + aggression_mult like the chase charge.
AI_ART_PURSUIT_RATE = 1.2

# ----- AI airborne arts ------------------------------------------------------ #
# The AI leaps and casts an airborne art (KAGURA up close, HARMONIC at range) as
# an occasional offensive change-up -- it commits to a hang-time float, so it is
# kept rarer than a ground art. Scaled by intensity, art_use_rate, aggression_mult.
AI_AIR_ART_RATE = 0.4          # per-second base chance to commit an aerial art
AI_KAGURA_RANGE = 3.0          # opponent within this -> KAGURA (AoE); else HARMONIC
AI_AIR_ART_APEX_VY = 2.5       # cast once the rising speed drops below this (near apex)


# ----------------------------------------------------------------------------- #
#  Difficulty
# ----------------------------------------------------------------------------- #
# Two selectable difficulties (toggle G in-game). MEDIUM is the established feel
# -- the attention read and feints exist (so spam is no longer free), but the AI
# presses conservatively and leaves obvious openings. HIGH leverages the read in
# full: it pressures harder, feints more to bait reactions, and adapts faster.
# Each profile is a bundle of scalars consumed by Fighter.ai_think:
#   aggression_mult : multiplies the base attack-pressure roll
#   attention_mult  : how strongly the player-read biases responses (0 = ignore)
#   feint_rate      : base chance an in-range offensive swing is thrown as a feint
#   defense_cap     : ceiling on per-swing parry/dodge probability
#   adapt_speed     : multiplies how fast the attention tallies accrue (faster read)
DIFFICULTY_PROFILES = {
    Difficulty.MEDIUM: {
        'aggression_mult': 1.0,
        'attention_mult': 0.6,
        'feint_rate': 0.1,
        'defense_cap': 0.85,
        'adapt_speed': 1.0,
        'art_use_rate': 0.5,
        'art_react_skill': 0.55,
        # Advanced art SMARTS (distance-scaled reactions, tempo parrying, two-strike
        # punishes, anti-turtle & pursuit arts) are HIGH-only; MEDIUM keeps the flat-
        # skill art behaviour. (Guard-breaks-under-pressure apply to BOTH -- see
        # art_guard_break_mult -- so MEDIUM's art parries crack more readily.)
        'art_advanced': False,
        # How readily art parries fail under pressure (mistime / panic-block). >1
        # cracks more; MEDIUM is the weaker AI, so it gets guard-broken by arts more
        # often than HIGH.
        'art_guard_break_mult': 1.8,
    },
    Difficulty.HIGH: {
        'aggression_mult': 1.35,
        'attention_mult': 1.0,
        'feint_rate': 0.32,
        'defense_cap': 0.95,
        'adapt_speed': 1.5,
        'art_use_rate': 1.1,
        'art_react_skill': 0.9,
        'art_advanced': True,
        'art_guard_break_mult': 1.0,
    },
}
DEFAULT_DIFFICULTY = Difficulty.MEDIUM


# ----------------------------------------------------------------------------- #
#  Attention / read model (AI)
# ----------------------------------------------------------------------------- #
# The AI keeps a decaying tally of the opponent's recent actions and biases its
# responses toward what counters the dominant pattern -- the user's "attention
# meter." It is purely internal (no HUD). Feints are what keep these reads from
# being a free wall: a planned counter can whiff on a faked attack, and the model
# itself learns to be wary of a feint-happy opponent.
#
# Every tally decays multiplicatively each frame: tally *= ATTENTION_DECAY ** dt.
# At 0.55 the half-life is ~1.2s, so "recent" means the last couple of seconds.
ATTENTION_DECAY = 0.55
ATTENTION_MAX = 6.0             # clamp so one pattern can't accumulate unbounded
# How much one observation adds to its tally (scaled by the difficulty adapt_speed).
ATTENTION_GAIN_LIGHT = 1.0
ATTENTION_GAIN_HEAVY = 1.2
ATTENTION_GAIN_CHARGE = 1.4
ATTENTION_GAIN_DODGE = 0.9
ATTENTION_GAIN_RETREAT = 1.2    # per SECOND while the opponent is actively receding
ATTENTION_GAIN_PARRY_WHIFF = 1.3
ATTENTION_GAIN_FEINT = 1.6
# Extra preferred-range the AI adds when its "spacing" read is maxed (a light- or
# guard-break-spamming opponent gets fought from farther out).
ATTENTION_SPACING_BONUS = 1.1
# When the AI deliberately does NOT defend an incoming swing -- it refuses to be a
# predictable parry-chain, and it cashes a buffered riposte/dodge-counter instead
# of burying it under another parry -- it arms this window to punish the attacker's
# recovery with a (varied) counter-attack. This is what makes spam actually cost
# something: a mix of defenses PLUS break-pattern counters, never a deterministic wall.
AI_COUNTER_WINDOW = 0.6
