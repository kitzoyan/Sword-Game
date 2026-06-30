"""
battlefields.py -- selectable, fully-themed battlefields for Riposte.

The duel is always fought on the same circular arena (the physics wall lives at
constants.ARENA_RADIUS and never moves). What this module changes is everything
*cosmetic*: a fleshed-out themed floor (NOT a flat grid -- each theme paints its
ground with ponds, mosaics, lava seams, frozen lakes, rune circles, mossy glades)
and a creatively-arranged, asymmetric scene of scenery around the combat circle
(no repeated ring-of-pillars -- each theme has its own grove / colonnade / crystal
field / forge / forest), plus its own sky, lighting and ambient particle field.

Design / interface
------------------
A `Theme` is a data bundle (colours, sky, lighting numbers) + ONE `build(bf)`
callable that composes the whole scene through `Battlefield` helpers. Every spawned
entity is classified either "lit" (stone / wood / ground that should respond to the
L lighting toggle) or "glow" (lava / crystals / lanterns / runes -- always unlit so
they read as emissive regardless of the sun).

`Battlefield(theme)` lays down a solid (un-gridded) ground plane, runs theme.build,
raises the sky, and owns an `AmbientField`. It is one root entity, so a theme swap
is a single destroy() + rebuild. It exposes:
    .lit_parts            -- entities main.py feeds the sun/ambient uniforms to
    set_lit(on, shader)   -- bind/unbind LIT_SHADER on the lit parts
    update(dt)            -- tick the ambient particle field
    destroy()             -- tear the whole theme down

Composition rules the builders follow:
  * The combat disc (r < ~11) stays readable: only FLAT floor decals or very low
    (< ~0.7) centrepieces go inside it. All tall scenery sits at r >= ~11.5.
  * Scenery is placed in asymmetric CLUSTERS (groves, colonnades, formations),
    never an even ring, so each battlefield has a composed, hand-placed feel.

This module must NOT import main (main imports this); it depends only on ursina +
constants, like fighter.py.
"""

import math
import random

from ursina import Entity, Sky, Vec3, color, destroy

from constants import ARENA_RADIUS, GROUND_Y

R = ARENA_RADIUS


# ----------------------------------------------------------------------------- #
#  Ambient particle field (petals / snow / embers / motes / fireflies)
# ----------------------------------------------------------------------------- #
# One lightweight, continuously-recycling particle system shared by every theme.
# Unlike the combat SparkSystem (one-shot bursts) it keeps a fixed pool alive and
# respawns each particle when it drifts out of bounds, so the field never empties
# and never grows. All particles are unlit and parented to a single root.
#
#   'petals' -- flutter slowly down, swaying + tumbling
#   'snow'   -- drift straight down, gentle sway
#   'embers' -- rise, flicker, fade as they climb
#   'motes'  -- hang in the air on slow random drift, pulsing (firefly = + flicker)
_FIELD_TOP = 11.0
_FIELD_DEFAULTS = {
    'count': 80, 'size': 0.12, 'speed': (0.5, 1.0), 'sway': 0.6,
    'spread': 1.5, 'alpha': 1.0, 'flicker': 0.0,
}


class AmbientField:
    def __init__(self, spec):
        s = dict(_FIELD_DEFAULTS)
        s.update(spec)
        self.style = s['style']
        self.col = s['color']
        self.size = s['size']
        self.speed_lo, self.speed_hi = s['speed']
        self.sway = s['sway']
        self.alpha = s['alpha']
        self.flicker = s['flicker']
        self.radius = R * s['spread']
        self.t = 0.0
        self.root = Entity()
        self._p = []
        model = 'quad' if self.style in ('petals', 'snow') else 'cube'
        for _ in range(int(s['count'])):
            e = Entity(parent=self.root, model=model, color=self.col,
                       unlit=True, double_sided=True, scale=self.size)
            p = {'e': e}
            self._spawn(p, initial=True)
            self._p.append(p)

    def _spawn(self, p, initial=False):
        ang = random.uniform(0, math.tau)
        r = math.sqrt(random.random()) * self.radius
        p['x0'] = math.cos(ang) * r
        p['z0'] = math.sin(ang) * r
        p['phase'] = random.uniform(0, math.tau)
        p['spin'] = random.uniform(40, 200)
        p['speed'] = random.uniform(self.speed_lo, self.speed_hi)
        if self.style == 'embers':
            p['y'] = random.uniform(0.0, _FIELD_TOP) if initial else random.uniform(0.0, 0.6)
        elif self.style == 'motes':
            p['y'] = random.uniform(1.0, 6.5)
            p['vx'] = random.uniform(-0.3, 0.3)
            p['vy'] = random.uniform(-0.15, 0.15)
            p['vz'] = random.uniform(-0.3, 0.3)
        else:
            p['y'] = random.uniform(0.0, _FIELD_TOP) if initial else _FIELD_TOP + random.uniform(0, 2)

    def update(self, dt):
        self.t += dt
        t = self.t
        for p in self._p:
            e = p['e']
            if self.style == 'embers':
                p['y'] += p['speed'] * dt
                frac = p['y'] / _FIELD_TOP
                e.x = p['x0'] + math.sin(t * 1.3 + p['phase']) * self.sway * 0.5
                e.z = p['z0'] + math.cos(t * 1.1 + p['phase']) * self.sway * 0.5
                e.y = GROUND_Y + p['y']
                flick = 0.55 + 0.45 * math.sin(t * 9.0 + p['phase'])
                e.alpha = max(0.0, self.alpha * flick * (1.0 - frac))
                if p['y'] >= _FIELD_TOP:
                    self._spawn(p)
            elif self.style == 'motes':
                p['x0'] += p['vx'] * dt
                p['y'] += p['vy'] * dt
                p['z0'] += p['vz'] * dt
                e.x, e.y, e.z = p['x0'], GROUND_Y + p['y'], p['z0']
                pulse = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(t * 2.0 + p['phase']))
                if self.flicker:
                    pulse *= (1.0 - self.flicker) + self.flicker * (0.5 + 0.5 * math.sin(t * 11.0 + p['phase'] * 3))
                e.alpha = self.alpha * pulse
                if (p['x0'] ** 2 + p['z0'] ** 2) > self.radius ** 2 or p['y'] < 0.5 or p['y'] > 7.0:
                    self._spawn(p)
            else:  # petals / snow
                p['y'] -= p['speed'] * dt
                e.x = p['x0'] + math.sin(t * 0.8 + p['phase']) * self.sway
                e.z = p['z0'] + math.cos(t * 0.6 + p['phase']) * self.sway * 0.6
                e.y = GROUND_Y + p['y']
                e.alpha = self.alpha
                if self.style == 'petals':
                    e.rotation = (t * p['spin'] * 0.3, t * p['spin'], 0)
                if p['y'] <= GROUND_Y - 0.3:
                    self._spawn(p)

    def destroy(self):
        destroy(self.root)
        self._p = []


# ----------------------------------------------------------------------------- #
#  Theme + Battlefield
# ----------------------------------------------------------------------------- #
class Theme:
    def __init__(self, name, *, banner_color, ground_color, build, ambient=None,
                 ground_scale=R * 4.0, sky_texture='sky_default', sky_color=None,
                 sun_azimuth=45.0, sun_elevation=48.0, sun_intensity=0.9,
                 ambient_light=0.4, window_bg=color.rgb32(18, 20, 28)):
        self.name = name
        self.banner_color = banner_color
        self.ground_color = ground_color
        self.build = build
        self.ambient = ambient
        self.ground_scale = ground_scale
        self.sky_texture = sky_texture
        self.sky_color = sky_color
        self.sun_azimuth = sun_azimuth
        self.sun_elevation = sun_elevation
        self.sun_intensity = sun_intensity
        self.ambient_light = ambient_light
        self.window_bg = window_bg


class Battlefield:
    """Builds one theme's scene under a single root and owns its ambient field."""

    def __init__(self, theme):
        self.theme = theme
        self.root = Entity()
        self.lit_parts = []
        self.unlit_parts = []

        # Solid ground plane (no grid texture). Themes paint detail on top of it.
        self.lit(model='plane', scale=theme.ground_scale, color=theme.ground_color,
                 position=(0, GROUND_Y, 0))

        theme.build(self)

        self.sky = Sky(texture=theme.sky_texture)
        if theme.sky_color is not None:
            self.sky.color = theme.sky_color

        self.ambient = AmbientField(theme.ambient) if theme.ambient else None

    def lit(self, parent=None, **kw):
        e = Entity(parent=parent or self.root, unlit=True, **kw)
        self.lit_parts.append(e)
        return e

    def glow(self, parent=None, **kw):
        e = Entity(parent=parent or self.root, unlit=True, **kw)
        self.unlit_parts.append(e)
        return e

    def node(self, **kw):
        return Entity(parent=self.root, **kw)

    def set_lit(self, on, lit_shader):
        for e in self.lit_parts:
            e.shader = lit_shader if on else None
            e.unlit = not on

    def update(self, dt):
        if self.ambient is not None:
            self.ambient.update(dt)

    def destroy(self):
        if self.ambient is not None:
            self.ambient.destroy()
            self.ambient = None
        if self.sky is not None:
            # Ursina's Sky appends itself to a class-level Sky.instances list and
            # never removes itself on destroy, so drop it ourselves -- otherwise
            # every theme cycle leaks a dead Sky reference (and would crash the
            # shadow-bounds pass if directional shadows were ever enabled).
            try:
                Sky.instances.remove(self.sky)
            except ValueError:
                pass
            destroy(self.sky)
            self.sky = None
        destroy(self.root)
        self.lit_parts = []
        self.unlit_parts = []


# ----------------------------------------------------------------------------- #
#  Composition helpers (shared by all theme builders)
# ----------------------------------------------------------------------------- #
def _t(bf, model, col, pos, scale, rot=(0, 0, 0), glow=False, parent=None):
    """Spawn one themed entity, lit or glowing."""
    fn = bf.glow if glow else bf.lit
    return fn(parent=parent, model=model, color=col, position=pos, scale=scale, rotation=rot)


def _disc(bf, col, radius, y=0.05, glow=False, center=(0.0, 0.0), stagger=True):
    """A flat filled circle lying on the ground (pond / emblem / platform / patch).

    Two anti-z-fighting measures: discs are double_sided (never back-face culled at
    a grazing third-person angle), and independent decals are auto-staggered onto
    a handful of micro-height bands so two overlapping patches are rarely coplanar
    (which is what makes ground decals flicker). Ordered stacks (see _concentric)
    pass stagger=False and manage their own height so the layer order is exact."""
    if stagger:
        bf._decal_i = getattr(bf, '_decal_i', 0) + 1
        y = y + (bf._decal_i % 6) * 0.009
    fn = bf.glow if glow else bf.lit
    return fn(model='circle', color=col,
              position=(center[0], GROUND_Y + y, center[1]),
              scale=(radius * 2, radius * 2, radius * 2),
              rotation=(90, 0, 0), double_sided=True)


def _concentric(bf, bands, center=(0.0, 0.0), y0=0.05, dy=0.03, glow=False):
    """Stacked filled discs (largest first) -> concentric coloured bands. Uses an
    explicit, generous per-band height step so the rings never z-fight."""
    for k, (radius, col) in enumerate(bands):
        _disc(bf, col, radius, y0 + dy * k, glow, center, stagger=False)


def _ring_studs(bf, radius, count, col, y=0.04, size=0.18, glow=False, tall=0.05,
                center=(0.0, 0.0)):
    """A flat ring traced out of small studs (etched circle / boundary line)."""
    for i in range(count):
        a = (i / count) * math.tau
        _t(bf, 'cube', col,
           (center[0] + math.cos(a) * radius, GROUND_Y + y, center[1] + math.sin(a) * radius),
           (size, tall, size), (0, math.degrees(a), 0), glow)


def _radials(bf, count, r0, r1, col, y=0.04, width=0.12, glow=False, jitter=0.0,
             tall=0.03, center=(0.0, 0.0)):
    """Radial bars from r0..r1 (rune spokes / cracks / raked lines)."""
    for i in range(count):
        a = (i / count) * math.tau + random.uniform(-jitter, jitter)
        rm = (r0 + r1) * 0.5
        _t(bf, 'cube', col,
           (center[0] + math.cos(a) * rm, GROUND_Y + y, center[1] + math.sin(a) * rm),
           (r1 - r0, tall, width), (0, math.degrees(a), 0), glow)


def _crack(bf, x0, z0, x1, z1, col, width=0.14, y=0.035, glow=False, segs=1):
    """A (possibly jagged) flat crack/seam line between two ground points."""
    px, pz = x0, z0
    for s in range(segs):
        f = (s + 1) / segs
        nx = x0 + (x1 - x0) * f + (random.uniform(-0.6, 0.6) if s < segs - 1 else 0)
        nz = z0 + (z1 - z0) * f + (random.uniform(-0.6, 0.6) if s < segs - 1 else 0)
        mx, mz = (px + nx) * 0.5, (pz + nz) * 0.5
        length = math.dist((px, pz), (nx, nz))
        ang = math.atan2(nz - pz, nx - px)
        _t(bf, 'cube', col, (mx, GROUND_Y + y, mz), (length, 0.03, width),
           (0, math.degrees(ang), 0), glow)
        px, pz = nx, nz


def _scatter(n, r0, r1, fn):
    for i in range(n):
        a = random.uniform(0, math.tau)
        r = random.uniform(r0, r1)
        fn(math.cos(a) * r, math.sin(a) * r, a, i)


def _cluster(n, ang_c, ang_spread, r0, r1, fn):
    for i in range(n):
        a = ang_c + random.uniform(-ang_spread, ang_spread)
        r = random.uniform(r0, r1)
        fn(math.cos(a) * r, math.sin(a) * r, a, i)


def _box(bf, col, x, z, w, h, d, y0=None, rot=(0, 0, 0), glow=False):
    """A cube placed by its FOOTPRINT (base on the ground unless y0 given)."""
    y = (GROUND_Y + h * 0.5) if y0 is None else y0
    return _t(bf, 'cube', col, (x, y, z), (w, h, d), rot, glow)


def _crystal(bf, col, x, z, h, w, y0=None, tilt=10.0, glow=True):
    y = (GROUND_Y + h * 0.5) if y0 is None else y0
    return _t(bf, 'diamond', col, (x, y, z), (w, h, w),
              (random.uniform(-tilt, tilt), random.uniform(0, 360),
               random.uniform(-tilt, tilt)), glow)


def _blob(bf, col, x, z, s, y=None, glow=False, model='sphere'):
    """A squashed sphere boulder/canopy sitting on the ground. `s` is the world
    diameter (the 'sphere' primitive is radius 0.5, so scale == diameter)."""
    y = (GROUND_Y + s * 0.4) if y is None else y
    return _t(bf, model, col, (x, y, z), (s, s * 0.8, s), (0, random.uniform(0, 360), 0), glow)


# =========================================================================== #
#  THEME: Japanese Garden
# =========================================================================== #
_SAKURA = color.rgb32(244, 178, 206)
_SAKURA_DEEP = color.rgb32(232, 150, 188)
_MOSS = color.rgb32(54, 84, 52)
_GRAVEL = color.rgb32(214, 206, 178)
_GRAVEL_2 = color.rgb32(198, 188, 158)
_WATER = color.rgb32(58, 120, 158)
_WATER_HI = color.rgb32(96, 156, 188)
_STONE = color.rgb32(150, 150, 142)
_WOOD = color.rgb32(120, 78, 54)
_TORII_RED = color.rgb32(196, 54, 38)
_TORII_DARK = color.rgb32(40, 30, 28)
_LANTERN = color.rgb32(255, 214, 140)
_TRUNK = color.rgb32(96, 68, 52)
_BAMBOO = color.rgb32(120, 168, 96)


def _build_japanese(bf):
    # -- Floor: raked zen gravel (concentric rings) in the duelling circle. --
    _concentric(bf, [(6.0, _GRAVEL), (5.0, _GRAVEL_2), (4.0, _GRAVEL),
                     (3.0, _GRAVEL_2), (2.0, _GRAVEL)])
    _ring_studs(bf, 5.5, 60, _MOSS, y=0.19, size=0.1, tall=0.04)
    _ring_studs(bf, 3.5, 44, _MOSS, y=0.2, size=0.1, tall=0.04)
    # Three "islands" of stones in the gravel (karesansui rock arrangement).
    for cx, cz in ((1.6, 1.2), (-2.2, 0.4), (0.3, -2.4)):
        for _ in range(random.randint(2, 3)):
            _blob(bf, _STONE, cx + random.uniform(-0.5, 0.5), cz + random.uniform(-0.5, 0.5),
                  random.uniform(0.4, 0.8))
    # Moss patches spread over the grass beyond the gravel.
    _scatter(16, 6.5, 11.0, lambda x, z, a, i: _disc(bf, _MOSS, random.uniform(0.6, 1.5),
                                                     y=0.03, center=(x, z)))

    # -- Koi pond off the +x side, reaching beyond the boundary. --
    pond = (12.5, 0.0)
    _disc(bf, _WATER, 3.4, y=0.06, center=pond, stagger=False)
    _disc(bf, _WATER, 2.4, y=0.07, center=(pond[0] - 2.0, 1.6), stagger=False)
    _disc(bf, _WATER_HI, 1.3, y=0.1, center=pond, stagger=False)
    for _ in range(5):  # lily pads + a koi
        _disc(bf, _MOSS, random.uniform(0.25, 0.45), y=0.14,
              center=(pond[0] + random.uniform(-2, 1.5), random.uniform(-2, 2)))
    _box(bf, color.rgb32(232, 132, 60), pond[0] - 1.0, 0.6, 0.4, 0.12, 0.25, y0=GROUND_Y + 0.08)
    # Stepping-stone path curving from the pond toward the centre.
    for k in range(7):
        f = k / 6
        ang = -0.2 + f * 1.1
        rr = 10.5 - f * 4.5
        _disc(bf, _STONE, 0.55, y=0.05, center=(math.cos(ang) * rr, math.sin(ang) * rr))
    # Wooden bridge over the near edge of the pond.
    bridge = bf.node(position=(9.6, GROUND_Y, 0.6), rotation=(0, 25, 0))
    for px in (-0.9, 0.0, 0.9):
        _t(bf, 'cube', _WOOD, (px, 0.35, 0), (0.7, 0.1, 1.6), parent=bridge)
    for sx in (-1.3, 1.3):
        _t(bf, 'cube', _TORII_RED, (sx, 0.45, 0.7), (0.12, 0.5, 0.12), parent=bridge)
        _t(bf, 'cube', _TORII_RED, (sx, 0.45, -0.7), (0.12, 0.5, 0.12), parent=bridge)

    # -- One grand torii gate as a focal entrance on the -x side. --
    gate = bf.node(position=(-R - 0.5, GROUND_Y, 0), rotation=(0, 90, 0))
    for sx in (-1.1, 1.1):
        _t(bf, 'cube', _TORII_RED, (sx, 2.0, 0), (0.3, 4.0, 0.3), parent=gate)
    _t(bf, 'cube', _TORII_DARK, (0, 4.05, 0), (3.3, 0.4, 0.5), parent=gate)
    _t(bf, 'cube', _TORII_DARK, (0, 4.5, 0), (3.9, 0.28, 0.55), parent=gate)
    _t(bf, 'cube', _TORII_RED, (0, 3.1, 0), (2.7, 0.28, 0.32), parent=gate)

    # -- Cherry grove clustered on the back-left, two lone trees elsewhere. --
    def cherry(x, z, a, i):
        h = random.uniform(2.2, 3.4)
        _box(bf, _TRUNK, x, z, 0.45, h, 0.45)
        for _ in range(4):
            _blob(bf, random.choice((_SAKURA, _SAKURA_DEEP)),
                  x + random.uniform(-1.1, 1.1), z + random.uniform(-1.1, 1.1),
                  random.uniform(1.8, 2.8), y=h + random.uniform(-0.4, 0.9))
    _cluster(5, 2.4, 0.5, 13.5, 17.0, cherry)
    cherry(math.cos(5.5) * 14, math.sin(5.5) * 14, 0, 0)
    cherry(math.cos(0.6) * 15, math.sin(0.6) * 15, 0, 0)

    # -- Stone lanterns lining the path, set out at the boundary so they frame
    # the duel rather than clutter the combat circle. --
    for ang, rr in ((-0.1, 12.5), (0.5, 11.7), (4.2, 12.6), (3.4, 11.8)):
        x, z = math.cos(ang) * rr, math.sin(ang) * rr
        _box(bf, _STONE, x, z, 0.5, 0.35, 0.5)
        _box(bf, _STONE, x, z, 0.16, 0.6, 0.16, y0=GROUND_Y + 0.6)
        _t(bf, 'cube', _LANTERN, (x, GROUND_Y + 1.05, z), (0.34, 0.34, 0.34), glow=True)
        _box(bf, _STONE, x, z, 0.52, 0.12, 0.52, y0=GROUND_Y + 1.3)
        _crystal(bf, _STONE, x, z + 0.0, 0.32, 0.42, y0=GROUND_Y + 1.5, tilt=0, glow=False)

    # -- Bamboo clump on the +z side. --
    def bamboo(x, z, a, i):
        h = random.uniform(3.5, 5.5)
        _box(bf, _BAMBOO, x, z, 0.16, h, 0.16,
             rot=(random.uniform(-4, 4), 0, random.uniform(-4, 4)))
    _cluster(10, 1.4, 0.35, 12.5, 14.5, bamboo)


_JAPANESE = Theme(
    'Japanese Garden', banner_color=_SAKURA,
    ground_color=color.rgb32(78, 106, 74), build=_build_japanese,
    ambient={'style': 'petals', 'color': _SAKURA, 'count': 70, 'size': 0.13,
             'speed': (0.5, 1.1), 'sway': 0.8, 'spread': 1.5, 'alpha': 0.95},
    sky_texture='sky_default', sky_color=color.rgb32(214, 232, 246),
    sun_azimuth=60.0, sun_elevation=55.0, sun_intensity=0.85, ambient_light=0.5,
    window_bg=color.rgb32(40, 48, 44),
)


# =========================================================================== #
#  THEME: Ancient Ruins  (overgrown Greco-Roman temple)
# =========================================================================== #
_MARBLE_A = color.rgb32(206, 192, 162)
_MARBLE_B = color.rgb32(182, 168, 138)
_MARBLE_C = color.rgb32(160, 146, 118)
_DIRT = color.rgb32(96, 84, 64)
_SANDSTONE = color.rgb32(208, 190, 152)
_SANDSTONE_DK = color.rgb32(168, 150, 116)
_BRONZE = color.rgb32(168, 132, 70)
_RUIN_GRASS = color.rgb32(86, 116, 64)


def _build_ruins(bf):
    # -- Floor: a broken marble mosaic (jittered tiles, some missing -> dirt). --
    step = 2.0
    n = int(R / step)
    for gx in range(-n, n + 1):
        for gz in range(-n, n + 1):
            x, z = gx * step, gz * step
            if x * x + z * z > (R - 0.5) ** 2:
                continue
            if random.random() < 0.16:       # missing tile -> exposed dirt
                _disc(bf, _DIRT, step * 0.5, y=0.04, center=(x, z))
                if random.random() < 0.4:
                    _disc(bf, _RUIN_GRASS, random.uniform(0.2, 0.4), y=0.12, center=(x, z))
                continue
            shade = random.choice((_MARBLE_A, _MARBLE_A, _MARBLE_B, _MARBLE_C))
            _t(bf, 'cube', shade, (x, GROUND_Y + 0.03, z),
               (step * 0.92, 0.05, step * 0.92), (0, random.uniform(-3, 3), 0))
    # Central laurel emblem (ordered disc stack + studs/spokes on higher tiers).
    _concentric(bf, [(2.6, _BRONZE), (2.1, _MARBLE_A)])
    _ring_studs(bf, 2.35, 40, _BRONZE, y=0.12, size=0.12, tall=0.06)
    _radials(bf, 16, 0.0, 2.0, _BRONZE, y=0.14, width=0.1)
    # A few long cracks across the floor.
    for _ in range(4):
        a = random.uniform(0, math.tau)
        _crack(bf, math.cos(a) * 1.5, math.sin(a) * 1.5,
               math.cos(a) * 9.5, math.sin(a) * 9.5, _DIRT, width=0.12, y=0.16, segs=4)

    # -- A colonnade down each of the +z / -z sides (rows, not a ring). --
    def colonnade(side_z, intact):
        for k in range(7):
            x = -9 + k * 3.0
            z = side_z + random.uniform(-0.4, 0.4)
            if not intact and random.random() < 0.5:
                # toppled column lying beyond the boundary
                _box(bf, _SANDSTONE, x, z + math.copysign(2, side_z), 0.6, 0.6,
                     random.uniform(2.0, 3.2), y0=GROUND_Y + 0.3,
                     rot=(0, random.uniform(0, 40), 90))
                continue
            h = random.choice((1.4, 2.6, 3.4, 3.8)) if intact else random.choice((0.7, 1.5, 2.4))
            _box(bf, _SANDSTONE, x, z, 0.6, h, 0.6)
            _box(bf, _SANDSTONE_DK, x, z, 0.74, 0.18, 0.74, y0=GROUND_Y + h - 0.05,
                 rot=(random.uniform(-5, 5), random.uniform(0, 40), random.uniform(-5, 5)))
            if random.random() < 0.4:   # vines
                _box(bf, _RUIN_GRASS, x + 0.32, z, 0.08, h * 0.6, 0.2, y0=GROUND_Y + h * 0.4)
    colonnade(R + 1.5, intact=True)
    colonnade(-R - 1.5, intact=False)
    # Standing entablature linking two columns on the intact side.
    _box(bf, _SANDSTONE_DK, -3.0, R + 1.5, 6.4, 0.7, 0.9, y0=GROUND_Y + 4.0)

    # -- Grand broken archway on the -x side. --
    arch = bf.node(position=(-R - 2.0, GROUND_Y, 0))
    for sx in (-1.4, 1.4):
        _t(bf, 'cube', _SANDSTONE, (sx, 2.0, 0), (0.8, 4.0, 0.8), parent=arch)
    _t(bf, 'cube', _SANDSTONE_DK, (-0.6, 4.2, 0), (1.8, 0.7, 0.9), parent=arch)
    _t(bf, 'cube', _SANDSTONE, (1.2, 3.4, 0), (0.7, 0.7, 0.8), parent=arch)  # broken span

    # -- A toppled statue + rubble on the +x corner. --
    _box(bf, _MARBLE_B, R + 2.5, 3.0, 0.8, 0.8, 3.2, y0=GROUND_Y + 0.4,
         rot=(0, 30, 90))
    _blob(bf, _MARBLE_A, R + 1.4, 4.4, 1.0)   # fallen head
    _scatter(18, R - 1.0, R + 6.0, lambda x, z, a, i:
             _blob(bf, random.choice((_MARBLE_C, _SANDSTONE_DK, _RUIN_GRASS)),
                   x, z, random.uniform(0.3, 0.8)))


_RUINS = Theme(
    'Ancient Ruins', banner_color=_SANDSTONE,
    ground_color=color.rgb32(150, 134, 104), build=_build_ruins,
    ambient={'style': 'motes', 'color': color.rgb32(230, 214, 170), 'count': 46,
             'size': 0.06, 'speed': (0.05, 0.18), 'spread': 1.4, 'alpha': 0.5},
    sky_texture='sky_default', sky_color=color.rgb32(226, 208, 168),
    sun_azimuth=80.0, sun_elevation=42.0, sun_intensity=0.95, ambient_light=0.45,
    window_bg=color.rgb32(60, 52, 38),
)


# =========================================================================== #
#  THEME: Frozen Tundra
# =========================================================================== #
_ICE = color.rgb32(186, 214, 238)
_ICE_DEEP = color.rgb32(140, 180, 218)
_LAKE = color.rgb32(168, 200, 228)
_LAKE_DK = color.rgb32(132, 170, 206)
_SNOW = color.rgb32(238, 246, 252)
_SNOW_SH = color.rgb32(206, 222, 236)
_PINE = color.rgb32(46, 78, 62)
_PINE_DK = color.rgb32(36, 64, 50)
_ROCK = color.rgb32(96, 102, 110)


def _build_tundra(bf):
    # -- Floor: a frozen lake (the duelling surface) with cracks. --
    _concentric(bf, [(8.0, _LAKE), (5.5, _LAKE_DK), (3.0, _LAKE)])
    for _ in range(7):  # branching cracks across the ice (above the lake bands)
        a = random.uniform(0, math.tau)
        _crack(bf, math.cos(a) * 0.5, math.sin(a) * 0.5,
               math.cos(a) * 7.5, math.sin(a) * 7.5, _SNOW, width=0.08, y=0.2, segs=5)
    # Snow drifts + exposed rock ringing the lake (kept low + off the duel surface).
    _scatter(20, 8.5, 12.5, lambda x, z, a, i:
             _blob(bf, random.choice((_SNOW, _SNOW, _SNOW_SH)), x, z, random.uniform(0.8, 1.5)))
    _scatter(8, 6.5, 11.0, lambda x, z, a, i: _disc(bf, _ROCK, random.uniform(0.5, 1.1),
                                                    y=0.22, center=(x, z)))

    # -- Pine forest: a dense grove on the back, sparser stands at the sides. --
    def pine(x, z, a, i):
        h = random.uniform(1.0, 1.6)
        _box(bf, color.rgb32(74, 58, 46), x, z, 0.3, h, 0.3)
        tiers = random.randint(3, 4)
        for tier in range(tiers):
            y = h + tier * 0.9
            s = 2.2 - tier * 0.5
            _t(bf, 'diamond', random.choice((_PINE, _PINE_DK)), (x, y, z),
               (s, 1.5, s), (0, 0, 0))
        _blob(bf, _SNOW, x, z, 0.7, y=h + tiers * 0.9 - 0.1)  # snow cap
    _cluster(9, 3.6, 0.7, 12.5, 17.0, pine)   # dense back grove
    _cluster(4, 0.4, 0.4, 12.5, 15.0, pine)
    _cluster(3, 5.6, 0.3, 12.5, 14.5, pine)

    # -- Iceberg / ice-shard formations in two clusters. --
    def shard(x, z, a, i):
        h = random.uniform(1.6, 3.4)
        _crystal(bf, random.choice((_ICE, _ICE_DEEP)), x, z, h, random.uniform(0.6, 1.1),
                 tilt=8, glow=False)
    _cluster(6, 1.7, 0.4, 12.0, 15.0, shard)
    _cluster(4, 4.7, 0.3, 12.0, 14.0, shard)

    # -- A frozen waterfall / ice wall on the -x side. --
    wall = bf.node(position=(-R - 1.0, GROUND_Y, 0))
    for k in range(5):
        _t(bf, 'cube', _ICE if k % 2 else _ICE_DEEP,
           (random.uniform(-2, 2), 1.5 + k * 0.9, random.uniform(-0.4, 0.4)),
           (random.uniform(0.8, 1.6), random.uniform(2.5, 4.0), 0.7),
           (0, random.uniform(-10, 10), 0), parent=wall)
    # Snow-capped boulders dotted around.
    _scatter(6, 11.0, 13.5, lambda x, z, a, i: (
        _blob(bf, _ROCK, x, z, random.uniform(0.8, 1.4)),
        _blob(bf, _SNOW, x, z, random.uniform(0.6, 1.0), y=0.9)))


_TUNDRA = Theme(
    'Frozen Tundra', banner_color=_ICE,
    ground_color=color.rgb32(224, 234, 242), build=_build_tundra,
    ambient={'style': 'snow', 'color': _SNOW, 'count': 120, 'size': 0.08,
             'speed': (0.4, 0.9), 'sway': 0.4, 'spread': 1.6, 'alpha': 0.95},
    sky_texture='sky_default', sky_color=color.rgb32(204, 222, 236),
    sun_azimuth=120.0, sun_elevation=28.0, sun_intensity=0.75, ambient_light=0.6,
    window_bg=color.rgb32(150, 170, 188),
)


# =========================================================================== #
#  THEME: Molten Caldera
#  A dark volcanic-rock island ringed by a lava moat, under a great erupting
#  volcano on the horizon. The floor is broken by organic, branching lava veins
#  (deliberately NOT a tiled grid).
# =========================================================================== #
_BASALT = color.rgb32(42, 35, 39)
_BASALT2 = color.rgb32(31, 26, 31)
_CHAR = color.rgb32(22, 18, 22)
_RIDGE = color.rgb32(35, 29, 33)
_CRUST = color.rgb32(54, 33, 31)
_LAVA = color.rgb32(255, 92, 22)
_LAVA_HOT = color.rgb32(255, 160, 56)
_LAVA_CORE = color.rgb32(255, 226, 140)
_SMOKE = color.rgb32(66, 58, 60)


def _build_volcanic(bf):
    # -- The duelling island: dark rock sitting in a glowing lava moat. The moat
    #    discs go down first (largest, lowest) and the rock island covers their
    #    centre, leaving a ring of lava around the combat circle. --
    _disc(bf, _LAVA, 16.5, y=0.02, glow=True, stagger=False)        # outer molten moat
    _disc(bf, _LAVA_HOT, 13.8, y=0.05, glow=True, stagger=False)    # hotter inner moat
    _disc(bf, _BASALT, 12.2, y=0.09, stagger=False)                 # the rock island
    _disc(bf, _BASALT2, 8.5, y=0.12, stagger=False)                 # tonal variation
    _disc(bf, _CHAR, 4.5, y=0.15, stagger=False)                    # scorched centre
    _ring_studs(bf, 12.2, 84, _CRUST, y=0.16, size=0.55, tall=0.28)  # crumbled shoreline

    # -- Molten veins branching out across the island floor (organic, no grid). --
    for k in range(6):
        a = (k / 6) * math.tau + random.uniform(-0.2, 0.2)
        hx, hz = math.cos(a) * random.uniform(1.0, 3.0), math.sin(a) * random.uniform(1.0, 3.0)
        for _ in range(random.randint(2, 3)):
            ea = a + random.uniform(-0.5, 0.5)
            er = random.uniform(8.0, 11.5)
            _crack(bf, hx, hz, math.cos(ea) * er, math.sin(ea) * er,
                   random.choice((_LAVA, _LAVA_HOT)), width=random.uniform(0.12, 0.3),
                   y=0.2, glow=True, segs=5)
    # Glowing pooled hotspots dotted over the floor.
    _scatter(7, 2.0, 10.0, lambda x, z, a, i: (
        _disc(bf, _LAVA, random.uniform(0.5, 1.1), y=0.17, glow=True, center=(x, z)),
        _disc(bf, _LAVA_CORE, random.uniform(0.2, 0.45), y=0.24, glow=True, center=(x, z))))

    # -- The great erupting volcano on the far -z horizon. --
    vol = bf.node(position=(0, GROUND_Y, -30.0))
    _t(bf, 'diamond', _RIDGE, (0, 13.0, 0), (24, 28, 24), parent=vol)      # main cone
    _t(bf, 'diamond', _BASALT2, (-3, 11.0, 2), (15, 24, 15), parent=vol)   # secondary peak
    # Truncated crater near the apex (the cone is ~5 wide at this height).
    _t(bf, 'circle', _LAVA, (0, 23.4, 0), (5.4, 5.4, 5.4), (90, 0, 0), glow=True, parent=vol)
    _t(bf, 'circle', _LAVA_CORE, (0, 23.7, 0), (2.6, 2.6, 2.6), (90, 0, 0), glow=True, parent=vol)
    # Lava streaks running down the near face of the cone.
    for sx in (-3.0, -1.0, 1.4, 3.2):
        _t(bf, 'cube', random.choice((_LAVA, _LAVA_HOT)),
           (sx, random.uniform(11, 15), 7.0),
           (random.uniform(0.4, 0.9), random.uniform(8, 14), 0.5),
           (random.uniform(-8, 8), 0, 0), glow=True, parent=vol)
    # Billowing smoke column above the crater.
    for k in range(6):
        _t(bf, 'sphere', _SMOKE,
           (random.uniform(-2.5, 2.5), 25 + k * 2.4, random.uniform(-2.5, 2.5)),
           (random.uniform(3.0, 5.5),) * 3, parent=vol)

    # -- Jagged caldera ridges between the moat and the volcano (irregular,
    #    with a clear gap toward the volcano so it stays framed). --
    def ridge(x, z, a, i):
        h = random.uniform(3.0, 7.0)
        _crystal(bf, _RIDGE, x, z, h, random.uniform(1.0, 2.4), tilt=10, glow=False)
        if random.random() < 0.45:
            _t(bf, 'cube', _LAVA, (x, GROUND_Y + 0.1, z), (1.3, 0.1, 1.3), glow=True)
    _cluster(6, 0.6, 0.6, 16.5, 21.0, ridge)
    _cluster(5, 2.3, 0.6, 16.5, 21.0, ridge)
    _cluster(4, 3.5, 0.5, 16.5, 20.0, ridge)
    _cluster(4, 5.9, 0.5, 16.5, 20.0, ridge)

    # -- A few obsidian shards rising straight out of the lava moat. --
    def shard(x, z, a, i):
        _crystal(bf, _CHAR, x, z, random.uniform(1.6, 3.0), random.uniform(0.4, 0.8),
                 tilt=14, glow=False)
    _cluster(3, 1.2, 0.4, 12.6, 13.6, shard)
    _cluster(3, 4.2, 0.4, 12.6, 13.6, shard)


_VOLCANIC = Theme(
    'Molten Caldera', banner_color=_LAVA_HOT,
    ground_color=color.rgb32(24, 20, 23), build=_build_volcanic, ground_scale=R * 6.0,
    ambient={'style': 'embers', 'color': _LAVA_HOT, 'count': 100, 'size': 0.07,
             'speed': (0.8, 1.9), 'sway': 0.5, 'spread': 1.5, 'alpha': 1.0},
    sky_texture='sky_default', sky_color=color.rgb32(68, 24, 20),
    sun_azimuth=20.0, sun_elevation=16.0, sun_intensity=0.7, ambient_light=0.4,
    window_bg=color.rgb32(28, 11, 9),
)


# =========================================================================== #
#  THEME: Astral Void  (a rune-etched platform floating in space)
# =========================================================================== #
_VOID = color.rgb32(10, 7, 22)
_PLATFORM = color.rgb32(44, 36, 64)
_PLATFORM_DK = color.rgb32(32, 26, 50)
_RUNE_A = color.rgb32(190, 110, 255)
_RUNE_B = color.rgb32(110, 220, 255)
_STAR = color.rgb32(230, 232, 255)


def _build_astral(bf):
    # -- The floating platform: dark stone disc with a raised rim. --
    _concentric(bf, [(R - 0.3, _PLATFORM), (R - 2.5, _PLATFORM_DK)])
    _ring_studs(bf, R - 0.6, 72, _PLATFORM_DK, y=0.12, size=0.5, tall=0.3)
    # Glowing rune circles + radial glyphs etched into the floor, each on its own
    # micro-height tier so overlapping spokes/rings never z-fight.
    _ring_studs(bf, 7.0, 64, _RUNE_A, y=0.16, size=0.16, tall=0.05, glow=True)
    _radials(bf, 12, 4.5, 7.0, _RUNE_A, y=0.18, width=0.1, glow=True)
    _ring_studs(bf, 4.5, 48, _RUNE_B, y=0.20, size=0.16, tall=0.05, glow=True)
    _radials(bf, 8, 0.0, 4.0, _RUNE_B, y=0.22, width=0.08, glow=True)
    _disc(bf, _RUNE_B, 1.2, y=0.24, glow=True, stagger=False)
    _disc(bf, _VOID, 0.7, y=0.26, stagger=False)
    # Void-light cracks creeping out from the centre (above every rune tier).
    for _ in range(5):
        a = random.uniform(0, math.tau)
        _crack(bf, math.cos(a) * 1.0, math.sin(a) * 1.0,
               math.cos(a) * 9.0, math.sin(a) * 9.0, _RUNE_A, width=0.08, y=0.3,
               glow=True, segs=4)

    # -- Crystal monoliths rising from the platform edge, in clusters. --
    def monolith(x, z, a, i):
        h = random.uniform(3.0, 6.0)
        col = _RUNE_A if i % 2 else _RUNE_B
        _crystal(bf, _PLATFORM_DK, x, z, 1.0, 1.2, tilt=0, glow=False)
        _crystal(bf, col, x, z, h, random.uniform(0.5, 0.9), y0=GROUND_Y + h * 0.5 + 0.4, tilt=6)
    _cluster(4, 2.0, 0.4, R - 0.4, R + 0.4, monolith)
    _cluster(3, 4.6, 0.3, R - 0.4, R + 0.4, monolith)
    _cluster(3, 0.0, 0.3, R - 0.4, R + 0.4, monolith)

    # -- Floating crystal islands suspended in the void around the platform. --
    def island(x, z, a, i):
        y = random.uniform(1.5, 6.0)
        _crystal(bf, _PLATFORM, x, z, random.uniform(1.0, 2.2), random.uniform(1.2, 2.4),
                 y0=GROUND_Y + y, tilt=20, glow=False)
        col = random.choice((_RUNE_A, _RUNE_B))
        for _ in range(random.randint(2, 3)):
            _crystal(bf, col, x + random.uniform(-1, 1), z + random.uniform(-1, 1),
                     random.uniform(0.8, 1.8), random.uniform(0.3, 0.6),
                     y0=GROUND_Y + y + random.uniform(0.6, 1.6))
    _scatter(10, R + 3.0, R + 10.0, island)
    # A starfield of tiny glints high above.
    _scatter(60, 2.0, R + 14.0, lambda x, z, a, i:
             _t(bf, 'cube', _STAR, (x, GROUND_Y + random.uniform(6, 16), z),
                (0.12, 0.12, 0.12), glow=True))
    # A standing portal ring on the -x side.
    portal = bf.node(position=(-R - 1.0, GROUND_Y + 3.0, 0), rotation=(0, 90, 0))
    for i in range(20):
        a = (i / 20) * math.tau
        _t(bf, 'cube', _RUNE_A, (math.cos(a) * 2.6, math.sin(a) * 2.6, 0),
           (0.3, 0.3, 0.3), (0, 0, math.degrees(a)), glow=True, parent=portal)


_ASTRAL = Theme(
    'Astral Void', banner_color=_RUNE_A,
    ground_color=_VOID, build=_build_astral, ground_scale=R * 6.0,
    ambient={'style': 'motes', 'color': _RUNE_B, 'count': 80, 'size': 0.09,
             'speed': (0.1, 0.3), 'spread': 1.8, 'alpha': 0.9},
    sky_texture=None, sky_color=color.rgb32(8, 5, 18),
    sun_azimuth=200.0, sun_elevation=62.0, sun_intensity=0.5, ambient_light=0.55,
    window_bg=color.rgb32(8, 5, 18),
)


# =========================================================================== #
#  THEME: Enchanted Forest  (a glowing mushroom glade ringed by giant trees)
# =========================================================================== #
_GLADE = color.rgb32(52, 78, 46)
_GLADE_2 = color.rgb32(64, 92, 54)
_GLADE_3 = color.rgb32(42, 64, 40)
_BARK = color.rgb32(74, 56, 42)
_BARK_DK = color.rgb32(58, 44, 34)
_CANOPY = color.rgb32(46, 84, 52)
_CANOPY_DK = color.rgb32(34, 68, 42)
_MUSH_CAP = color.rgb32(206, 70, 72)
_MUSH_GLOW = color.rgb32(150, 230, 255)
_MUSH_GLOW2 = color.rgb32(190, 150, 255)
_STEM = color.rgb32(226, 220, 206)
_BROOK = color.rgb32(70, 130, 150)


def _build_forest(bf):
    # -- Floor: an uneven mossy glade (overlapping patches, no grid). --
    _scatter(40, 0.0, 11.5, lambda x, z, a, i:
             _disc(bf, random.choice((_GLADE, _GLADE_2, _GLADE_3)),
                   random.uniform(0.8, 2.2), y=0.04, center=(x, z)))
    # Bioluminescent patches scattered through the moss (above the moss tier).
    _scatter(14, 1.5, 10.5, lambda x, z, a, i:
             _disc(bf, random.choice((_MUSH_GLOW, _MUSH_GLOW2)),
                   random.uniform(0.3, 0.6), y=0.16, glow=True, center=(x, z)))
    # A fairy ring of mushrooms around the duelling circle (low, at r ~ 5).
    for i in range(14):
        a = (i / 14) * math.tau
        x, z = math.cos(a) * 5.0, math.sin(a) * 5.0
        _box(bf, _STEM, x, z, 0.12, random.uniform(0.3, 0.5), 0.12)
        _t(bf, 'sphere', _MUSH_CAP, (x, GROUND_Y + 0.5, z), (0.45, 0.3, 0.45))
    # A small brook arcing across the +x edge with mossy stones.
    for k in range(8):
        a = -0.6 + k * 0.18
        rr = 9.0 + math.sin(k) * 0.6
        _disc(bf, _BROOK, random.uniform(0.7, 1.1), y=0.03, center=(math.cos(a) * rr, math.sin(a) * rr))
    _scatter(6, 8.0, 10.5, lambda x, z, a, i: _blob(bf, color.rgb32(110, 110, 102), x, z,
                                                    random.uniform(0.4, 0.8)))
    # A fallen mossy log on the -z side.
    _box(bf, _BARK_DK, math.cos(4.5) * 9, math.sin(4.5) * 9, 0.7, 0.7, 4.0,
         y0=GROUND_Y + 0.35, rot=(0, 40, 90))

    # -- Giant ancient trees ringing the glade, with gaps (a clearing). --
    def giant_tree(x, z, a, i):
        th = random.uniform(5.0, 8.0)
        _box(bf, random.choice((_BARK, _BARK_DK)), x, z, random.uniform(1.0, 1.6), th,
             random.uniform(1.0, 1.6))
        # Buttress roots creeping toward the glade.
        for _ in range(3):
            ra = a + math.pi + random.uniform(-0.6, 0.6)
            _box(bf, _BARK_DK, x + math.cos(ra) * 1.2, z + math.sin(ra) * 1.2,
                 0.3, 0.3, 1.6, y0=GROUND_Y + 0.15, rot=(0, math.degrees(ra), 0))
        # Layered canopy.
        for _ in range(5):
            _blob(bf, random.choice((_CANOPY, _CANOPY_DK)),
                  x + random.uniform(-2.0, 2.0), z + random.uniform(-2.0, 2.0),
                  random.uniform(3.0, 4.5), y=th + random.uniform(-0.5, 2.5))
    _cluster(4, 2.3, 0.7, 13.5, 16.0, giant_tree)   # dense stand
    _cluster(3, 4.9, 0.6, 13.5, 16.0, giant_tree)
    giant_tree(math.cos(0.3) * 14, math.sin(0.3) * 14, 0.3, 0)
    giant_tree(math.cos(5.7) * 15, math.sin(5.7) * 15, 5.7, 1)

    # -- Clusters of giant glowing mushrooms (the fantasy centrepiece scenery). --
    def big_mushroom(x, z, a, i):
        h = random.uniform(2.5, 4.5)
        glow_col = random.choice((_MUSH_GLOW, _MUSH_GLOW2))
        _box(bf, _STEM, x, z, random.uniform(0.4, 0.7), h, random.uniform(0.4, 0.7))
        _t(bf, 'sphere', glow_col, (x, GROUND_Y + h + 0.3, z),
           (random.uniform(1.6, 2.6), random.uniform(1.0, 1.5), random.uniform(1.6, 2.6)),
           glow=True)
    _cluster(5, 1.2, 0.5, 12.0, 15.0, big_mushroom)
    _cluster(3, 3.9, 0.4, 12.0, 14.5, big_mushroom)
    # Hanging glow orbs near the trees.
    _scatter(10, 11.0, 15.0, lambda x, z, a, i:
             _t(bf, 'sphere', random.choice((_MUSH_GLOW, _MUSH_GLOW2)),
                (x, GROUND_Y + random.uniform(3.0, 6.0), z), (0.3, 0.3, 0.3), glow=True))


_FOREST = Theme(
    'Enchanted Forest', banner_color=_MUSH_GLOW,
    ground_color=color.rgb32(46, 68, 42), build=_build_forest,
    ambient={'style': 'motes', 'color': color.rgb32(200, 240, 150), 'count': 70,
             'size': 0.08, 'speed': (0.1, 0.3), 'spread': 1.5, 'alpha': 0.9, 'flicker': 0.7},
    sky_texture='sky_default', sky_color=color.rgb32(86, 120, 120),
    sun_azimuth=140.0, sun_elevation=38.0, sun_intensity=0.7, ambient_light=0.5,
    window_bg=color.rgb32(24, 36, 30),
)


# ----------------------------------------------------------------------------- #
#  Theme registry (cycle order; index 0 is the default at boot)
# ----------------------------------------------------------------------------- #
THEMES = [_JAPANESE, _RUINS, _TUNDRA, _VOLCANIC, _ASTRAL, _FOREST]


def theme_count():
    return len(THEMES)


def get_theme(i):
    return THEMES[i % len(THEMES)]
