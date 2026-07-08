"""
art_sprites.py -- Projectile sprite system for Arts.

Builds two sprite shapes procedurally (no textures needed):
  RingSprite   -- an expanding white circle/ring (used by CENTIPEDE and KAGURA)
  CrescentSprite -- a travelling half-ring slash (used by HARMONIC and OVERCLOCK)

Both carry a single-hit hitbox that fires once on contact and never repeats.
ArtProjectileManager owns all active sprites, updates them per frame, resolves
hits against the designated target, and cleans up completed sprites.

Hit resolution rules (from Prompt.txt):
  - target.invulnerable (dodge i-frames) + perfect dodge of an art: reset dodge cooldown immediately
  - target.parry_active: art-user NOT staggered; parrier knocked back ART_PARRY_KNOCKBACK units
  - target.is_blocking: target staggers (long)
  - clean hit: deal damage, follow normal hit rules (cancel windup/active attacks)
"""

import math
import random

from ursina import Entity, Mesh, Vec3, color as ucolor, destroy, scene

import constants
from constants import (
    ArtType,
    ART_PARRY_KNOCKBACK,
    BLOCK_HEAVY_STAGGER_TIME,
    CENTIPEDE_RING_EXPAND_SPEED,
    CENTIPEDE_RING_MAX_RADIUS,
    CENTIPEDE_RING_ORIGIN_RADIUS,
    CENTIPEDE_RING_HEIGHT,
    HARMONIC_CRESCENT_SPEED,
    HARMONIC_CRESCENT_HEIGHT,
    HARMONIC_TARGET_HEIGHT,
    HARMONIC_DELAY_BETWEEN,
    KAGURA_RING_COUNT,
    KAGURA_RING_EXPAND_SPEED,
    KAGURA_RING_MAX_RADIUS,
    KAGURA_RING_ORIGIN_RADIUS,
    KAGURA_RING_HEIGHT_BASE,
    KAGURA_RING_SPREAD,
    OVERCLOCK_CRESCENT_MAX_DIST,
    OVERCLOCK_CRESCENT_SPEED,
    OVERCLOCK_TORSO_HEIGHT,
    OVERCLOCK_RING_MAX_RADIUS,
    OVERCLOCK_RING_EXPAND_SPEED,
    OVERCLOCK_DELAY_BETWEEN,
    CENTIPEDE_RING_THICKNESS,
    KAGURA_RING_THICKNESS,
    OVERCLOCK_RING_THICKNESS,
    State,
    ATTACKS,
    AttackType,
)

ART_SPRITE_COLOR = ucolor.rgba32(240, 250, 255, 200)
RING_SEGMENTS = 32        # polygon approximation of a circle
CRESCENT_SEGMENTS = 8    # half-circle for crescent


# --------------------------------------------------------------------------- #
#  Geometry helpers
# --------------------------------------------------------------------------- #

def _ring_mesh(radius, thickness=0.5, segments=RING_SEGMENTS, y=0.0):
    """Flat horizontal ring at height y. Returns (verts, tris) for a Mesh."""
    r_out = radius + thickness * 0.5
    r_in = max(0.0, radius - thickness * 0.5)
    verts = []
    tris = []
    for i in range(segments):
        a0 = (i / segments) * math.tau
        a1 = ((i + 1) / segments) * math.tau
        i0_out = len(verts)
        verts.append(Vec3(math.cos(a0) * r_out, y, math.sin(a0) * r_out))
        i0_in = len(verts)
        verts.append(Vec3(math.cos(a0) * r_in, y, math.sin(a0) * r_in))
        i1_out = len(verts)
        verts.append(Vec3(math.cos(a1) * r_out, y, math.sin(a1) * r_out))
        i1_in = len(verts)
        verts.append(Vec3(math.cos(a1) * r_in, y, math.sin(a1) * r_in))
        tris += [(i0_out, i0_in, i1_out), (i0_in, i1_in, i1_out)]
    return verts, tris


def _crescent_mesh(radius=2, thickness=0.5, segments=CRESCENT_SEGMENTS):
    """Flat horizontal half-ring crescent (front-facing +z arc). Returns (verts, tris)."""
    r_out = radius + thickness * 0.25
    r_in = max(0.0, radius - thickness * 0.25)
    verts = []
    tris = []
    # Half ring: 0 to pi (front semicircle)
    for i in range(segments):
        a0 = (i / segments) * math.pi
        a1 = ((i + 1) / segments) * math.pi
        i0_out = len(verts)
        verts.append(Vec3(math.cos(a0) * r_out, 0, math.sin(a0) * r_out))
        i0_in = len(verts)
        verts.append(Vec3(math.cos(a0) * r_in, 0, math.sin(a0) * r_in))
        i1_out = len(verts)
        verts.append(Vec3(math.cos(a1) * r_out, 0, math.sin(a1) * r_out))
        i1_in = len(verts)
        verts.append(Vec3(math.cos(a1) * r_in, 0, math.sin(a1) * r_in))
        tris += [(i0_out, i0_in, i1_out), (i0_in, i1_in, i1_out)]
    return verts, tris


def _make_entity(verts, tris, position):
    return Entity(
        parent=scene,
        model=Mesh(vertices=verts, triangles=tris, mode='triangle'),
        color=ART_SPRITE_COLOR,
        position=position,
        unlit=True,
        double_sided=True,
    )


# --------------------------------------------------------------------------- #
#  Sprite classes
# --------------------------------------------------------------------------- #

class RingSprite:
    """An expanding horizontal ring that holds one hit-once hitbox as it grows.

    Used for CENTIPEDE (one ring, hitbox expands with ring edge) and
    KAGURA (multiple rings, but the hitbox is a sphere managed by a KaguraHitbox).
    """

    def __init__(self, origin, max_radius, expand_speed, height,
                 art_type, art_user, hitbox=True, tilt=None, start_radius=None,
                 thickness=None):
        self.origin = Vec3(origin.x, 0.0, origin.z)
        self.max_radius = max_radius
        self.expand_speed = expand_speed
        self.height = height
        self.art_type = art_type
        self.art_user = art_user
        self.has_hitbox = hitbox   # False for KAGURA visual rings (hitbox is separate)
        # Optional (pitch, yaw, roll) tilt in degrees. When set, the ring is
        # centred at (origin.x, height, origin.z) and rotated, rather than lying
        # flat on the ground. Used by KAGURA so its rings fan out at angles.
        self.tilt = tilt
        if thickness is not None:
            self.thickness = thickness
        elif art_type == ArtType.CENTIPEDE:
            self.thickness = CENTIPEDE_RING_THICKNESS
        elif art_type == ArtType.KAGURA:
            self.thickness = KAGURA_RING_THICKNESS
        else:
            self.thickness = OVERCLOCK_RING_THICKNESS
        if start_radius is not None:
            self.radius = start_radius
        else:
            self.radius = CENTIPEDE_RING_ORIGIN_RADIUS if art_type == ArtType.CENTIPEDE else KAGURA_RING_ORIGIN_RADIUS
        self.hit_fired = False
        self.dead = False
        self._entity = None
        self._rebuild_mesh()

    def _rebuild_mesh(self):
        if self._entity is not None:
            destroy(self._entity)
        if self.tilt is not None:
            verts, tris = _ring_mesh(self.radius, thickness=self.thickness, y=0.0)
            pos = Vec3(self.origin.x, self.height, self.origin.z)
            self._entity = _make_entity(verts, tris, pos)
            self._entity.rotation = self.tilt
        else:
            verts, tris = _ring_mesh(self.radius, thickness=self.thickness, y=self.height)
            pos = Vec3(self.origin.x, 0.0, self.origin.z)
            self._entity = _make_entity(verts, tris, pos)

    def update(self, dt, target):
        if self.dead:
            return
        self.radius += self.expand_speed * dt
        # Rebuild the mesh at new radius.
        self._rebuild_mesh()
        if self.radius >= self.max_radius:
            self.dead = True
            return
        if self.has_hitbox and not self.hit_fired and target is not None:
            self._check_hit(target)

    def _check_hit(self, target):
        if target.state == State.DEAD:
            return
        dx = target.position.x - self.origin.x
        dz = target.position.z - self.origin.z
        dist = math.hypot(dx, dz)
        contact_zone = self.thickness + 0.5   # 0.5 = approx fighter radius
        if abs(dist - self.radius) <= contact_zone:
            self.hit_fired = True
            _resolve_art_hit(self.art_user, target, self.art_type, 1)

    def clear(self):
        self.dead = True
        if self._entity is not None:
            destroy(self._entity)
            self._entity = None


class KaguraHitbox:
    """A standalone expanding sphere hitbox for KAGURA (one single hit, no visual).
    KAGURA uses multiple RingSprites for visuals, but the hitbox is this object.
    """

    def __init__(self, origin, art_user):
        self.origin = Vec3(origin.x, origin.y, origin.z)
        self.radius = KAGURA_RING_ORIGIN_RADIUS
        self.hit_fired = False
        self.dead = False
        self.art_user = art_user

    def update(self, dt, target):
        if self.dead:
            return
        self.radius += KAGURA_RING_EXPAND_SPEED * dt
        if self.radius >= KAGURA_RING_MAX_RADIUS:
            self.dead = True
            return
        if not self.hit_fired and target is not None:
            self._check_hit(target)

    def _check_hit(self, target):
        if target.state == State.DEAD:
            return
        dx = target.position.x - self.origin.x
        dy = (target.position.y + 1.0) - self.origin.y  # target chest height vs sphere center
        dz = target.position.z - self.origin.z
        dist = math.sqrt(dx*dx + dy*dy + dz*dz)
        if abs(dist - self.radius) <= KAGURA_RING_THICKNESS + 0.7:
            self.hit_fired = True
            _resolve_art_hit(self.art_user, target, ArtType.KAGURA, 1)

    def clear(self):
        self.dead = True


class CrescentSprite:
    """A crescent slash that travels in a fixed direction. One hit-once hitbox.

    Used for HARMONIC (travels full stage) and OVERCLOCK (short range).
    """

    def __init__(self, origin, direction, speed, max_dist, height,
                 art_type, art_user, tilt=0.0, descent_slope=0.0):
        self.origin = Vec3(origin.x, 0.0, origin.z)
        self.direction = Vec3(direction.x, 0.0, direction.z)
        d = math.hypot(self.direction.x, self.direction.z)
        if d > 1e-6:
            self.direction = Vec3(self.direction.x / d, 0.0, self.direction.z / d)
        self.speed = speed
        self.max_dist = max_dist
        self.height = height
        self.art_type = art_type
        self.art_user = art_user
        self.tilt = tilt   # roll (degrees) about the travel axis: 0=flat, 90=vertical
        # Vertical drop per unit of horizontal travel. >0 makes the crescent
        # descend diagonally as it flies (HARMONIC). 0 = level horizontal flight.
        self.descent_slope = descent_slope
        self.dist_travelled = 0.0
        self.hit_fired = False
        self.dead = False
        # Build entity at starting position.
        pos = Vec3(self.origin.x, self.height, self.origin.z)
        verts, tris = _crescent_mesh()
        self._entity = _make_entity(verts, tris, pos)
        # Orient entity so the crescent faces the travel direction (yaw), then
        # roll it about that travel axis by `tilt` so it can stand diagonal/vertical.
        yaw = math.degrees(math.atan2(self.direction.x, self.direction.z))
        self._entity.rotation = Vec3(0.0, yaw, self.tilt)

    def update(self, dt, target):
        if self.dead:
            return
        step = self.speed * dt
        self.dist_travelled += step
        pos = self._entity.position
        self._entity.position = Vec3(
            pos.x + self.direction.x * step,
            pos.y - self.descent_slope * step,
            pos.z + self.direction.z * step,
        )
        if self.dist_travelled >= self.max_dist:
            self.dead = True
            return
        if not self.hit_fired and target is not None:
            self._check_hit(target)

    def _check_hit(self, target):
        if target.state == State.DEAD:
            return
        cp = self._entity.position
        dx = target.position.x - cp.x
        dz = target.position.z - cp.z
        dist = math.hypot(dx, dz)
        # Simple proximity check: crescent radius ~0.8, fighter radius ~0.5
        if dist <= 1.4:
            self.hit_fired = True
            _resolve_art_hit(self.art_user, target, self.art_type, 1)

    def clear(self):
        self.dead = True
        if self._entity is not None:
            destroy(self._entity)
            self._entity = None


# --------------------------------------------------------------------------- #
#  Hit resolution
# --------------------------------------------------------------------------- #

def _resolve_art_hit(art_user, target, art_type, hit_count):
    """Resolve a single art projectile hit against target.

    Rules:
    - Dodge i-frames: DODGED; a perfect dodge (invulnerable) flashes the dodger
      blue (same cue as dodging any attack) and resets their dodge cooldown.
    - Parry active: art-user NOT staggered, parrier knocked back ART_PARRY_KNOCKBACK.
    - Blocking: target gets BLOCK_HEAVY_STAGGER_TIME stagger (same as guard-break).
    - Clean hit: damage, may cancel target's windup/active attack.
    """
    if target.state == State.DEAD:
        return

    heavy_attack = ATTACKS[AttackType.HEAVY]
    damage = heavy_attack.damage

    # Direction from art_user to target.
    dx = target.position.x - art_user.position.x
    dz = target.position.z - art_user.position.z
    mag = math.hypot(dx, dz)
    if mag > 1e-6:
        direction = Vec3(dx / mag, 0.0, dz / mag)
    else:
        direction = art_user.forward

    if target.invulnerable:
        # Perfect dodge of an art: blue flash (identical to perfect-dodging any
        # other attack) + reset the dodge cooldown so the next hit can be dodged too.
        target.on_art_dodge_success()
        return

    if target.parry_active:
        # Art parried: parrier gets knocked back, art-user is NOT staggered.
        knockback = direction * ART_PARRY_KNOCKBACK
        target.body.apply_impulse(Vec3(knockback.x, 0.0, knockback.z))
        target.on_parry_success(None)  # gives riposte window + sparks
        # art_user deliberately NOT called on_staggered() -- per spec.
        return

    if target.is_blocking:
        # Blocking an art: stagger (long) the blocker -- same as a guard-break.
        target.take_damage(damage * constants.BLOCK_DAMAGE_MULT + damage * constants.BLOCK_CHIP,
                           direction * 3.0, BLOCK_HEAVY_STAGGER_TIME)
        # Flag the guard-break event so main runs the camera shake + freeze-frame
        # orbit (the "revolution"), just like a heavy crashing through a block.
        art_user.guard_break_event = True
        return

    # Clean hit: deal damage. Cancel pre-commit attacks (WINDUP/ACTIVE) per rules.
    target.take_damage(damage, direction * heavy_attack.knockback, 0.0)


# --------------------------------------------------------------------------- #
#  Manager
# --------------------------------------------------------------------------- #

class ArtProjectileManager:
    """Owns and updates all active art projectile sprites."""

    def __init__(self):
        self._sprites = []     # list of RingSprite or CrescentSprite
        self._hitboxes = []    # list of KaguraHitbox
        self._pending = []     # [(delay_remaining, constructor_callback)]

    def clear(self):
        for s in self._sprites:
            s.clear()
        self._sprites = []
        for h in self._hitboxes:
            h.clear()
        self._hitboxes = []
        self._pending = []

    def spawn_centipede(self, art_user):
        """Single expanding ring starting from user position."""
        origin = Vec3(art_user.position.x, 0.0, art_user.position.z)
        ring = RingSprite(
            origin=origin,
            max_radius=CENTIPEDE_RING_MAX_RADIUS,
            expand_speed=CENTIPEDE_RING_EXPAND_SPEED,
            height=CENTIPEDE_RING_HEIGHT,
            art_type=ArtType.CENTIPEDE,
            art_user=art_user,
            hitbox=True,
        )
        self._sprites.append(ring)

    def spawn_kagura(self, art_user):
        """Multiple visual rings + one sphere hitbox expanding from user."""
        origin = Vec3(art_user.position.x, 0.0, art_user.position.z)
        head_y = art_user.position.y + 1.9 + 1.0   # 1 unit above head
        # All rings share the same centre point; each gets a random tilt
        # (yaw + pitch) so they fan out in different orientations.
        for i in range(KAGURA_RING_COUNT):
            tilt = Vec3(
                random.uniform(0, 360),   # pitch
                random.uniform(0, 360),   # yaw
                random.uniform(0, 360),   # roll
            )
            # Each ring starts at a noticeably different size (visual only).
            start_radius = random.uniform(KAGURA_RING_ORIGIN_RADIUS,
                                          KAGURA_RING_MAX_RADIUS * 0.6)
            # Visual only (no hitbox).
            ring = RingSprite(
                origin=origin,
                max_radius=KAGURA_RING_MAX_RADIUS,
                expand_speed=KAGURA_RING_EXPAND_SPEED,
                height=head_y,
                art_type=ArtType.KAGURA,
                art_user=art_user,
                hitbox=False,
                tilt=tilt,
                start_radius=start_radius,
            )
            self._sprites.append(ring)
        # Standalone sphere hitbox.
        sphere = KaguraHitbox(
            origin=Vec3(origin.x, head_y, origin.z),
            art_user=art_user,
        )
        self._hitboxes.append(sphere)

    def spawn_harmonic(self, art_user, target_pos):
        """Two crescent slashes fired diagonally down toward target_pos.
        First fires immediately, second fires HARMONIC_DELAY_BETWEEN seconds later.
        """
        head_y = art_user.position.y + 1.9 + 1.0   # 1 unit above head
        origin = Vec3(art_user.position.x, 0.0, art_user.position.z)
        dx = target_pos.x - origin.x
        dz = target_pos.z - origin.z
        mag = math.hypot(dx, dz)
        if mag < 1e-4:
            dx, dz = art_user.forward.x, art_user.forward.z
            mag = 1.0
        direction = Vec3(dx / mag, 0.0, dz / mag)

        # Descend diagonally so the crescent reaches the opponent's body height
        # right at the opponent's position. The drop is spread over the whole
        # horizontal distance, so a farther target yields a gentler descent.
        target_y = target_pos.y + HARMONIC_TARGET_HEIGHT
        descent_slope = max(0.0, (HARMONIC_CRESCENT_HEIGHT - target_y) / mag)

        # Two slightly diagonal variants (slight spread left/right).
        spread_angle = 0.18   # radians
        for i, sign in enumerate((-1, 1)):
            angle = sign * spread_angle
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            d = Vec3(direction.x * cos_a - direction.z * sin_a, 0.0,
                     direction.x * sin_a + direction.z * cos_a)
            delay = i * HARMONIC_DELAY_BETWEEN

            tilt = sign * 45.0   # one crescent at +45, the other at -45

            def make_crescent(d=d, delay=delay, tilt=tilt):
                crescent = CrescentSprite(
                    origin=Vec3(origin.x, 0.0, origin.z),
                    direction=d,
                    speed=HARMONIC_CRESCENT_SPEED,
                    max_dist=mag + 2.0,   # travel past target
                    height=HARMONIC_CRESCENT_HEIGHT,
                    art_type=ArtType.HARMONIC,
                    art_user=art_user,
                    tilt=tilt,
                    descent_slope=descent_slope,
                )
                self._sprites.append(crescent)

            if delay <= 0.0:
                make_crescent()
            else:
                self._pending.append([delay, make_crescent])

    def spawn_overclock(self, art_user):
        """First slash: a stationary vertical expanding ring. Second slash: a
        vertical crescent fired forward shortly after."""
        origin = Vec3(art_user.position.x, 0.0, art_user.position.z)
        direction = Vec3(art_user.forward.x, 0.0, art_user.forward.z)
        yaw = math.degrees(math.atan2(direction.x, direction.z))
        torso_y = art_user.position.y + OVERCLOCK_TORSO_HEIGHT   # torso altitude

        # First slash: a vertical ring that expands in place (does not travel).
        ring = RingSprite(
            origin=origin,
            max_radius=OVERCLOCK_RING_MAX_RADIUS,
            expand_speed=OVERCLOCK_RING_EXPAND_SPEED,
            height=torso_y,
            art_type=ArtType.OVERCLOCK,
            art_user=art_user,
            hitbox=True,
            tilt=Vec3(0, yaw, 90.0),   # stand the ring vertical, facing forward
        )
        self._sprites.append(ring)

        # Second slash: a vertical crescent fired forward after a short delay.
        def make_crescent(d=direction):
            crescent = CrescentSprite(
                origin=Vec3(origin.x, 0.0, origin.z),
                direction=d,
                speed=OVERCLOCK_CRESCENT_SPEED,
                max_dist=OVERCLOCK_CRESCENT_MAX_DIST,
                height=torso_y,
                art_type=ArtType.OVERCLOCK,
                art_user=art_user,
                tilt=90.0,   # vertical crescent
            )
            self._sprites.append(crescent)

        self._pending.append([OVERCLOCK_DELAY_BETWEEN, make_crescent])

    def update(self, dt, target):
        """Update all sprites and pending spawns. target is the opponent."""
        # Tick pending spawns.
        still_pending = []
        for entry in self._pending:
            entry[0] -= dt
            if entry[0] <= 0.0:
                entry[1]()  # fire the spawn callback
            else:
                still_pending.append(entry)
        self._pending = still_pending

        # Update ring/crescent sprites.
        alive = []
        for sprite in self._sprites:
            sprite.update(dt, target)
            if not sprite.dead:
                alive.append(sprite)
            else:
                sprite.clear()
        self._sprites = alive

        # Update sphere hitboxes.
        alive_h = []
        for hb in self._hitboxes:
            hb.update(dt, target)
            if not hb.dead:
                alive_h.append(hb)
        self._hitboxes = alive_h
