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

from ursina import Entity, Sky, Vec3, color, destroy, scene, camera

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
#   'rain'   -- thin streaks falling fast on a slight wind-driven slant
#   'mist'   -- broad faint ground-hugging fog sheets drifting slowly (low alpha)
# A theme may stack several fields (e.g. rain + mist) -- see Battlefield.
_FIELD_TOP = 11.0
_FIELD_DEFAULTS = {
    'count': 80, 'size': 0.12, 'speed': (0.5, 1.0), 'sway': 0.6,
    'spread': 1.5, 'alpha': 1.0, 'flicker': 0.0, 'wind': 1.4,
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
        self.wind = s['wind']
        self.radius = R * s['spread']
        self.t = 0.0
        self.root = Entity()
        self._p = []
        for _ in range(int(s['count'])):
            e = self._make_entity()
            p = {'e': e}
            self._spawn(p, initial=True)
            self._p.append(p)

    def _make_entity(self):
        if self.style == 'rain':   # a thin vertical streak, slanted by the wind
            return Entity(parent=self.root, model='cube', color=self.col, unlit=True,
                          scale=(0.035, self.size, 0.035), rotation=(0, 0, 11))
        if self.style == 'mist':   # a soft camera-facing ellipse (a fog puff);
            # many of these overlap and stack into a believable volumetric haze
            # instead of the old flat squares. Wider than tall + billboarded.
            asp = random.uniform(0.45, 0.7)
            return Entity(parent=self.root, model='circle', color=self.col, unlit=True,
                          double_sided=True, scale=(self.size, self.size * asp, 1))
        model = 'quad' if self.style in ('petals', 'snow') else 'cube'
        return Entity(parent=self.root, model=model, color=self.col, unlit=True,
                      double_sided=True, scale=self.size)

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
        elif self.style == 'mist':
            p['y'] = random.uniform(0.5, 2.6)
            p['vx'] = random.uniform(-0.25, 0.25)
            p['vz'] = random.uniform(-0.25, 0.25)
        else:  # petals / snow / rain -- fall from above
            p['y'] = random.uniform(0.0, _FIELD_TOP) if initial else _FIELD_TOP + random.uniform(0, 3)

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
            elif self.style == 'rain':
                p['y'] -= p['speed'] * dt
                p['x0'] += self.wind * dt          # wind-driven slant drift
                e.x, e.y, e.z = p['x0'], GROUND_Y + p['y'], p['z0']
                e.alpha = self.alpha
                if p['y'] <= GROUND_Y - 0.3:
                    self._spawn(p)
            elif self.style == 'mist':
                p['x0'] += p['vx'] * dt
                p['z0'] += p['vz'] * dt
                e.x = p['x0']
                e.z = p['z0']
                e.y = GROUND_Y + p['y'] + math.sin(t * 0.4 + p['phase']) * 0.25
                # Billboard about the vertical axis ONLY -- a full look_at would let
                # the wider-than-tall ellipse roll/tumble as it drifts; yaw-only keeps
                # the puff upright and horizontal while still facing the viewer.
                e.rotation = (0, math.degrees(math.atan2(
                    camera.world_position.x - p['x0'], camera.world_position.z - p['z0'])), 0)
                e.alpha = self.alpha * (0.6 + 0.4 * math.sin(t * 0.5 + p['phase']))
                if (p['x0'] ** 2 + p['z0'] ** 2) > self.radius ** 2:
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
#  Volumetric fog (independent, player-toggleable layer)  [from Battle-Fields]
# ----------------------------------------------------------------------------- #
# A cosmetic cloud-fog layer that sits ON TOP of the per-theme atmosphere. It is
# toggled independently (H cycles off/dense/ground/rolling) and re-tinted to the
# active battlefield via set_color(). Self-contained; owns its own entities.
_FOG_MODES = ('off', 'dense', 'ground', 'rolling')

# Per-mode shape: cloud-centre height range, drift speed, horizontal/vertical
# cluster spread, per-puff size range, and per-puff alpha range. 'ground'/'rolling'
# use a small vertical spread so the cluster flattens into a low bank.
_FOG_SPEC = {
    'dense':   {'cy': (1.0, 8.0), 'speed': (0.2, 0.6), 'spread': (3.0, 1.3),
                'size': (3.0, 5.5), 'alpha': (0.05, 0.10)},
    'ground':  {'cy': (0.7, 2.2), 'speed': (0.1, 0.35), 'spread': (3.6, 0.55),
                'size': (3.0, 5.0), 'alpha': (0.07, 0.12)},
    'rolling': {'cy': (0.4, 1.3), 'speed': (1.3, 2.5), 'spread': (4.2, 0.45),
                'size': (3.2, 5.5), 'alpha': (0.07, 0.12)},
}


class FogSystem:
    def __init__(self, fog_color, clouds=16, puffs_per_cloud=6):
        self.base_color = fog_color
        self.mode = 'off'
        self.radius = R * 1.25
        self.t = 0.0
        # Wind direction for 'rolling' (unit vector in the xz plane).
        wx, wz = 1.0, 0.3
        wmag = math.hypot(wx, wz)
        self.wind = Vec3(wx / wmag, 0.0, wz / wmag)
        # Axis perpendicular to the wind (the 'across' axis for rolling spawns).
        self.perp = Vec3(-self.wind.z, 0.0, self.wind.x)
        self.root = Entity(enabled=False)
        self._clouds = []
        for _ in range(int(clouds)):
            puffs = []
            for _ in range(int(puffs_per_cloud)):
                e = Entity(parent=self.root, model='quad', texture='circle',
                           color=fog_color, unlit=True, double_sided=True)
                puffs.append({'e': e, 'ox': 0.0, 'oy': 0.0, 'oz': 0.0, 'a': 0.1,
                              'sway': random.uniform(0.2, 0.6),
                              'ph': random.uniform(0, math.tau)})
            # 'along'/'across' are wind-aligned coords used by the rolling mode.
            self._clouds.append({'cx': 0.0, 'cy': 0.0, 'cz': 0.0, 'along': 0.0,
                                 'across': 0.0, 'phase': 0.0, 'speed': 0.0,
                                 'puffs': puffs})

    # -- spawning / placement ------------------------------------------------- #
    def _place_cloud(self, c, mode, initial=False):
        spec = _FOG_SPEC[mode]
        if mode == 'rolling':
            # Wind-aligned coords: 'along' runs with the wind, 'across' spans the
            # width of the field. Spawn across the FULL band so fog covers the
            # whole map; on a wrap (initial=False) re-enter at the upwind edge with
            # a fresh across-offset, so coverage stays even instead of bunching.
            c['across'] = random.uniform(-self.radius, self.radius)
            c['along'] = (random.uniform(-self.radius, self.radius)
                          if initial else -self.radius)
            c['cx'] = self.wind.x * c['along'] + self.perp.x * c['across']
            c['cz'] = self.wind.z * c['along'] + self.perp.z * c['across']
        else:
            ang = random.uniform(0, math.tau)
            r = math.sqrt(random.random()) * self.radius
            c['cx'] = math.cos(ang) * r
            c['cz'] = math.sin(ang) * r
        c['cy'] = GROUND_Y + random.uniform(*spec['cy'])
        c['speed'] = random.uniform(*spec['speed'])
        c['phase'] = random.uniform(0, math.tau)
        spread_h, spread_v = spec['spread']
        for q in c['puffs']:
            q['ox'] = random.uniform(-spread_h, spread_h)
            q['oz'] = random.uniform(-spread_h, spread_h)
            q['oy'] = random.uniform(-spread_v, spread_v)
            s = random.uniform(*spec['size'])
            q['a'] = random.uniform(*spec['alpha'])
            q['ph'] = random.uniform(0, math.tau)
            e = q['e']
            e.scale = (s, s * random.uniform(0.7, 0.95), 1.0)
            e.alpha = q['a']

    # -- public API ----------------------------------------------------------- #
    def set_mode(self, mode):
        self.mode = mode if mode in _FOG_MODES else 'off'
        on = self.mode != 'off'
        self.root.enabled = on
        if on:
            for c in self._clouds:
                self._place_cloud(c, self.mode, initial=True)
        return self.mode

    def cycle(self):
        i = (_FOG_MODES.index(self.mode) + 1) % len(_FOG_MODES)
        return self.set_mode(_FOG_MODES[i])

    def set_color(self, fog_color):
        """Re-tint the fog to the active battlefield (called on a theme swap)."""
        self.base_color = fog_color
        for c in self._clouds:
            for q in c['puffs']:
                q['e'].color = fog_color
                q['e'].alpha = q['a']

    def update(self, dt):
        if self.mode == 'off':
            return
        self.t += dt
        t = self.t
        cam = camera.world_position
        for c in self._clouds:
            # Cloud-centre motion: rolling translates along the wind (wrapping at
            # the arena edge); all modes add a slow organic drift/bob.
            if self.mode == 'rolling':
                c['along'] += c['speed'] * dt
                if c['along'] > self.radius:   # past the downwind edge -> wrap back
                    self._place_cloud(c, 'rolling', initial=False)
                c['cx'] = self.wind.x * c['along'] + self.perp.x * c['across']
                c['cz'] = self.wind.z * c['along'] + self.perp.z * c['across']
                cy = c['cy'] + math.sin(t * 0.5 + c['phase']) * 0.08
                bx = c['cx'] + math.sin(t * 0.3 + c['phase']) * 0.3
                bz = c['cz']
            elif self.mode == 'dense':
                cy = c['cy'] + math.sin(t * 0.30 + c['phase']) * 0.4
                bx = c['cx'] + math.sin(t * 0.20 + c['phase']) * 1.0
                bz = c['cz'] + math.cos(t * 0.17 + c['phase']) * 1.0
            else:  # ground
                cy = c['cy'] + math.sin(t * 0.35 + c['phase']) * 0.12
                bx = c['cx'] + math.sin(t * 0.15 + c['phase']) * 0.6
                bz = c['cz'] + math.cos(t * 0.13 + c['phase']) * 0.6
            for q in c['puffs']:
                e = q['e']
                e.x = bx + q['ox'] + math.sin(t * q['sway'] + q['ph']) * 0.25
                e.y = cy + q['oy']
                e.z = bz + q['oz'] + math.cos(t * q['sway'] + q['ph']) * 0.25
                # Billboard toward the camera (double-sided + radially-symmetric
                # texture, so the facing sign and any roll are irrelevant).
                e.look_at(cam)

    def destroy(self):
        destroy(self.root)
        self._clouds = []


# ----------------------------------------------------------------------------- #
#  Theme + Battlefield
# ----------------------------------------------------------------------------- #
class Theme:
    def __init__(self, name, *, banner_color, ground_color, build, ambient=None,
                 ground_radius=R * 6.0, horizon_color=None,
                 sky_texture='sky_default', sky_color=None,
                 sun_azimuth=45.0, sun_elevation=48.0, sun_intensity=0.9,
                 ambient_light=0.4, window_bg=color.rgb32(18, 20, 28)):
        self.name = name
        self.banner_color = banner_color
        self.ground_color = ground_color
        self.build = build
        self.ambient = ambient
        self.ground_radius = ground_radius
        # Optional darker/lighter outer ground tone for a graded horizon (defaults
        # to the ground colour). Gives the distant plane some depth near the sky.
        self.horizon_color = horizon_color or ground_color
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

        # Round ground: a big graded disc (no square plane edges) so the world
        # reads as an open field running to the horizon rather than a flat tile.
        # A wider, darker/lighter outer disc sits just beneath to soften the far
        # rim where it meets the sky.
        rr = theme.ground_radius
        # Keep refs to the two base ground discs so the occluder-fade pass can skip
        # them (they're huge and flat -- never a vision blocker, must never fade).
        self._ground_discs = [
            self.lit(model='circle', color=theme.horizon_color,
                     position=(0, GROUND_Y - 0.04, 0),
                     scale=(rr * 2.4, rr * 2.4, rr * 2.4), rotation=(90, 0, 0),
                     double_sided=True),
            self.lit(model='circle', color=theme.ground_color,
                     position=(0, GROUND_Y - 0.02, 0),
                     scale=(rr * 2, rr * 2, rr * 2), rotation=(90, 0, 0),
                     double_sided=True),
        ]

        theme.build(self)

        self.sky = Sky(texture=theme.sky_texture)
        if theme.sky_color is not None:
            self.sky.color = theme.sky_color

        # Ambient + atmosphere. A theme's `ambient` is the primary particle field;
        # _ATMOSPHERE may stack extra layers (e.g. rain + ground mist) and set a
        # distance fog. Fog is global scene state, so it is (re)set here on every
        # build and cleared in destroy(); linear fog (start, end) keeps the near
        # combat area crisp while the far backdrop melts into the sky.
        atmo = _ATMOSPHERE.get(theme.name, {})
        specs = []
        if theme.ambient:
            specs += theme.ambient if isinstance(theme.ambient, (list, tuple)) else [theme.ambient]
        specs += atmo.get('add', [])
        self.ambient = [AmbientField(s) for s in specs]

        fog = atmo.get('fog')
        if fog is not None:
            scene.fog_color, scene.fog_density = fog
            # Panda3D fog would also flatten the sky dome and swallow the distant
            # sun/moon/stars. Exempt the sky + every emissive element (glow parts)
            # so the backdrop stays vivid; only the LIT mid/far geometry fades into
            # the haze, which is exactly the depth cue we want.
            for e in [self.sky] + self.unlit_parts:
                try:
                    e.set_fog_off()
                except Exception:
                    pass
        else:
            scene.fog_density = 0

        self._build_occluders()

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

    # -- See-through occluders ------------------------------------------------- #
    # Any solid scenery that comes between the camera and a fighter (or right in
    # front of the camera) is faded to a ghost so it never blocks the view of the
    # duel. Static positions are precomputed once; the per-frame pass is cheap
    # arithmetic over the pruned candidate list.
    FADE_ALPHA = 0.22        # how see-through a blocker becomes
    FADE_NEAR = 3.6          # fade anything this close to the camera (xz)
    FADE_MARGIN = 0.7        # extra reach around the sightline / object
    FADE_LERP = 11.0         # alpha easing speed (per second)

    def _build_occluders(self):
        """Snapshot the candidate blockers: tall-ish scenery within the arena
        region (the only things that can sit between the camera and the fighters).
        Skips the ground discs, flat floor decals, and far background props.

        Uses each entity's rotation-aware world-space bounding box (Panda3D tight
        bounds) so tilted props (a leaning mast, kelp, tilted crystals) report their
        true footprint/height and still fade -- falling back to scale if needed."""
        self.occluders = []
        skip = {id(g) for g in self._ground_discs}
        for e in self.lit_parts + self.unlit_parts:
            if id(e) in skip:
                continue
            try:
                mn, mx = e.get_tight_bounds(scene)
                cx, cz = 0.5 * (mn.x + mx.x), 0.5 * (mn.z + mx.z)
                base, top = mn.y, mx.y
                rad = 0.5 * max(mx.x - mn.x, mx.z - mn.z)
            except Exception:
                wp, ws = e.world_position, e.world_scale
                cx, cz = wp.x, wp.z
                base, top = wp.y - abs(ws.y) * 0.5, wp.y + abs(ws.y) * 0.5
                rad = 0.5 * max(abs(ws.x), abs(ws.z))
            if top < 0.6:                       # flat / floor decals never block
                continue
            if (cx * cx + cz * cz) ** 0.5 - rad > 18.0:
                continue                        # whole prop is far background (near edge too)
            self.occluders.append([e, cx, cz, base, top, rad, 1.0])

    def fade_occluders(self, cam, targets, dt):
        """Fade blockers between `cam` (a Vec3) and any of `targets` (a list of
        (x, y, z) fighter points), plus anything hugging the camera, and ease the
        rest back to opaque."""
        if not self.occluders:
            return
        k = min(1.0, Battlefield.FADE_LERP * dt)
        near2 = Battlefield.FADE_NEAR ** 2
        margin = Battlefield.FADE_MARGIN
        fade_a = Battlefield.FADE_ALPHA
        cx, cy, cz = cam.x, cam.y, cam.z
        for rec in self.occluders:
            e, ox, oz, oby, oty, orad, a = rec
            blocked = False
            # Right in front of the camera (and tall enough to be in view).
            dxc, dzc = ox - cx, oz - cz
            if dxc * dxc + dzc * dzc < near2 and oby - 1.0 <= cy <= oty + 1.0:
                blocked = True
            else:
                rr = (orad + margin) ** 2
                for (tx, ty, tz) in targets:
                    sx, sz = tx - cx, tz - cz
                    seg2 = sx * sx + sz * sz
                    if seg2 < 1e-6:
                        continue
                    t = (dxc * sx + dzc * sz) / seg2      # projection along cam->target (xz)
                    if t <= 0.05 or t >= 0.98:            # behind cam or past the fighter
                        continue
                    ddx = ox - (cx + sx * t)
                    ddz = oz - (cz + sz * t)
                    if ddx * ddx + ddz * ddz > rr:
                        continue
                    ly = cy + (ty - cy) * t               # sightline height at the blocker
                    if oby - margin <= ly <= oty + margin:
                        blocked = True
                        break
            target_a = fade_a if blocked else 1.0
            if a != target_a:
                a += (target_a - a) * k
                if abs(a - target_a) < 0.01:
                    a = target_a
                rec[6] = a
                e.alpha = a

    def update(self, dt):
        for field in self.ambient:
            field.update(dt)

    def destroy(self):
        for field in self.ambient:
            field.destroy()
        self.ambient = []
        scene.fog_density = 0   # clear the global fog so the next theme starts clean
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


# ----------------------------------------------------------------------------- #
#  Background / horizon / sky helpers
#  These build the distant skyline + celestial features that give each theme its
#  individual backdrop. Everything here sits far out (r >= ~22) or high in the
#  sky, well clear of the readable combat disc (r < ~11.5).
# ----------------------------------------------------------------------------- #
def _sky_pos(ang, elev, dist):
    """World point on a sky shell at azimuth `ang` (rad), elevation `elev` (deg)."""
    e = math.radians(elev)
    return (math.cos(ang) * math.cos(e) * dist,
            GROUND_Y + math.sin(e) * dist,
            math.sin(ang) * math.cos(e) * dist)


def _celestial(bf, ang, elev, size, col, dist=60.0, model='sphere'):
    """A sun / moon / planet hanging in the sky."""
    return _t(bf, model, col, _sky_pos(ang, elev, dist), (size, size, size), glow=True)


def _ringed_planet(bf, ang, elev, size, body, ring_col, dist=62.0):
    """A gas-giant: glowing sphere with a tilted ring disc behind it."""
    p = _sky_pos(ang, elev, dist)
    _t(bf, 'circle', ring_col, p, (size * 2.6, size * 2.6, size * 2.6), (72, 0, 18), glow=True)
    _t(bf, 'sphere', body, p, (size, size, size), glow=True)


def _glow_halo(bf, ang, elev, size, col, dist=59.0):
    """A soft sun with a larger faint halo behind it (col should carry alpha)."""
    p = _sky_pos(ang, elev, dist)
    _t(bf, 'circle', col, p, (size * 2.2,) * 3, (0, 0, 0), glow=True)
    _t(bf, 'circle', col, p, (size * 1.4,) * 3, (0, 0, 0), glow=True)


def _mountains(bf, ang_c, spread, count, r_lo, r_hi, col, h_lo, h_hi,
               snow_col=None):
    """A range of pyramidal peaks along the horizon arc.

    A 'diamond' is a full octahedron (bipyramid). To read as a MOUNTAIN we centre
    it AT ground level and give it double the wanted height, so its widest slice
    sits on the ground and only the upper pyramid shows -- the lower pyramid is
    buried under the ground disc. (Centring at h/2 -- the old bug -- left the whole
    floating gem visible.)"""
    for i in range(count):
        a = ang_c + random.uniform(-spread, spread)
        r = random.uniform(r_lo, r_hi)
        x, z = math.cos(a) * r, math.sin(a) * r
        h = random.uniform(h_lo, h_hi)            # visible peak height above ground
        w = h * random.uniform(1.5, 2.4)          # base width at the ground
        rot = random.uniform(0, 360)
        _t(bf, 'diamond', col, (x, GROUND_Y, z), (w, h * 2.0, w), (0, rot, 0))
        if snow_col is not None:
            # A smaller white pyramid capping the upper ~third; its base is buried
            # inside the rock peak so only the snowy cap shows.
            _t(bf, 'diamond', snow_col, (x, GROUND_Y + h * 0.62, z),
               (w * 0.5, h * 0.76, w * 0.5), (0, rot, 0))


def _skyline(bf, ang_c, spread, count, r, col_list, h_lo, h_hi, w_lo=1.4, w_hi=3.4):
    """Distant towers/buildings (a city or temple skyline)."""
    for i in range(count):
        a = ang_c + random.uniform(-spread, spread)
        rr = r * random.uniform(0.92, 1.08)
        x, z = math.cos(a) * rr, math.sin(a) * rr
        h = random.uniform(h_lo, h_hi)
        _t(bf, 'cube', random.choice(col_list), (x, GROUND_Y + h * 0.5, z),
           (random.uniform(w_lo, w_hi), h, random.uniform(w_lo, w_hi)),
           (0, math.degrees(a) + random.uniform(-10, 10), 0))


def _cloud(bf, x, y, z, s, col):
    """A puffy cloud (a clump of glowing white-ish spheres)."""
    for _ in range(random.randint(3, 5)):
        _t(bf, 'sphere', col,
           (x + random.uniform(-s, s), y + random.uniform(-s * 0.3, s * 0.3),
            z + random.uniform(-s, s)),
           (random.uniform(s * 0.9, s * 1.5),) * 3, glow=True)


def _cloud_ring(bf, n, r, y, s, col):
    for i in range(n):
        a = random.uniform(0, math.tau)
        rr = r * random.uniform(0.8, 1.25)
        _cloud(bf, math.cos(a) * rr, y + random.uniform(-2, 3), math.sin(a) * rr, s, col)


def _aurora(bf, ang_c, spread, bands, col_list, y=15.0, height=14.0, dist=40.0):
    """Shimmering vertical light curtains along an arc of the sky."""
    for i in range(bands):
        a = ang_c + random.uniform(-spread, spread)
        rr = dist * random.uniform(0.85, 1.15)
        x, z = math.cos(a) * rr, math.sin(a) * rr
        _t(bf, 'cube', random.choice(col_list),
           (x, GROUND_Y + y + random.uniform(-2, 4), z),
           (random.uniform(0.5, 1.2), height * random.uniform(0.7, 1.3), 0.4),
           (0, math.degrees(a), random.uniform(-14, 14)), glow=True)


def _arc(bf, count, radius, col, center, thick=0.5, a0=15.0, a1=165.0,
         tilt=0.0, plane='xy'):
    """Studs along an arc of a circle in a VERTICAL plane (for rainbows / arches).
    center is (x, y, z); plane 'xy' arcs in the x-y plane facing +z."""
    cx, cy, cz = center
    for i in range(count):
        f = i / max(1, count - 1)
        ad = math.radians(a0 + (a1 - a0) * f)
        ox = math.cos(ad) * radius
        oy = math.sin(ad) * radius
        if plane == 'xy':
            pos = (cx + ox, cy + oy, cz)
        else:  # 'zy'
            pos = (cx, cy + oy, cz + ox)
        _t(bf, 'cube', col, pos, (thick, thick, thick), (0, 0, 0), glow=True)


def _floating_island(bf, x, z, y, top_r, col_top, col_rock, waterfall_col=None):
    """A chunk of floating land: flat top disc + an inverted rock cone beneath,
    optionally trailing a waterfall ribbon."""
    _t(bf, 'circle', col_top, (x, y, z), (top_r * 2,) * 3, (90, 0, 0), glow=True)
    _t(bf, 'diamond', col_rock, (x, y - top_r * 1.1, z),
       (top_r * 1.7, top_r * 2.4, top_r * 1.7), (180, 0, 0), glow=True)
    if waterfall_col is not None:
        _t(bf, 'cube', waterfall_col, (x + top_r * 0.4, y - top_r * 1.2, z),
           (0.5, top_r * 2.4, 0.5), (0, 0, 0), glow=True)


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

    # -- Backdrop: distant rolling hills, snow-capped Mt. Fuji, a soft sun. --
    _mountains(bf, 1.6, 1.1, 7, 30, 44, color.rgb32(110, 132, 138), 6, 11)
    # Mt. Fuji: a diamond centred at the ground (scale_y = 2x height) so only the
    # upper pyramid shows, with a buried-base snow cap -- same convention as _mountains.
    fx, fz = math.cos(1.9) * 46, math.sin(1.9) * 46
    _t(bf, 'diamond', color.rgb32(126, 140, 156), (fx, GROUND_Y, fz), (30, 36, 30), (0, 0, 0))
    _t(bf, 'diamond', color.rgb32(236, 244, 250), (fx, GROUND_Y + 11.2, fz), (15, 13.7, 15), (0, 0, 0))
    _glow_halo(bf, 5.4, 30, 5.5, color.rgba32(255, 240, 210, 150))


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

    # -- Backdrop: olive hills, a distant acropolis skyline + warm sun. --
    _mountains(bf, 3.4, 1.3, 8, 28, 42, color.rgb32(126, 134, 96), 5, 9)
    _skyline(bf, 2.0, 0.4, 6, 34, [_SANDSTONE, _SANDSTONE_DK], 5, 9, w_lo=1.2, w_hi=2.0)
    _t(bf, 'cube', _SANDSTONE_DK, (math.cos(2.0) * 34, GROUND_Y + 9, math.sin(2.0) * 34),
       (9, 0.8, 4), (0, math.degrees(2.0), 0))                   # temple roofline
    _glow_halo(bf, 1.1, 26, 5.0, color.rgba32(255, 236, 196, 150))


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

    # -- Backdrop: a great snow-mountain range + shimmering aurora + pale sun. --
    _mountains(bf, 3.6, 1.6, 10, 26, 44, _ROCK, 8, 17, snow_col=_SNOW)
    _aurora(bf, 1.4, 1.4, 16, [color.rgba32(120, 240, 180, 130),
                               color.rgba32(140, 180, 255, 120),
                               color.rgba32(200, 150, 255, 110)], y=16, height=16, dist=38)
    _celestial(bf, 5.6, 24, 4.0, color.rgb32(235, 244, 252), dist=58)


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

    # -- The great erupting volcano on the far -z horizon. The cones are diamonds
    #    centred at ground level (scale_y = 2x height) so only the upper pyramid
    #    shows -- the lower half is buried, giving a real mountain, not a gem. --
    vol = bf.node(position=(0, GROUND_Y, -30.0))
    _t(bf, 'diamond', _RIDGE, (0, 0, 0), (24, 32, 24), parent=vol)         # main cone (apex y=16)
    _t(bf, 'diamond', _BASALT2, (-3, 0, 2), (15, 24, 15), parent=vol)      # secondary peak (apex y=12)
    # Truncated crater just below the apex (cone is ~3.7 wide there).
    _t(bf, 'circle', _LAVA, (0, 13.5, 0), (4.0, 4.0, 4.0), (90, 0, 0), glow=True, parent=vol)
    _t(bf, 'circle', _LAVA_CORE, (0, 13.8, 0), (2.0, 2.0, 2.0), (90, 0, 0), glow=True, parent=vol)
    # Lava streaks on the broad lower face of the cone (kept low so they hug the
    # slope and never float off the narrow upper apex; z follows the face profile).
    for sx in (-3.0, -1.0, 1.4, 3.2):
        yc = random.uniform(3, 7)
        zc = 12.0 * (1 - yc / 16.0) - 0.6     # just inside the near face at this height
        _t(bf, 'cube', random.choice((_LAVA, _LAVA_HOT)),
           (sx, yc, zc), (random.uniform(0.4, 0.9), random.uniform(3, 5), 0.5),
           (random.uniform(-8, 8), 0, 0), glow=True, parent=vol)
    # Billowing smoke column above the crater.
    for k in range(6):
        _t(bf, 'sphere', _SMOKE,
           (random.uniform(-2.5, 2.5), 17 + k * 2.4, random.uniform(-2.5, 2.5)),
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

    # -- Backdrop: a smaller distant volcano + dark ash ridges + a dull blood sun. --
    smol = bf.node(position=(math.cos(2.4) * 34, GROUND_Y, math.sin(2.4) * 34))
    _t(bf, 'diamond', _RIDGE, (0, 0, 0), (16, 20, 16), parent=smol)   # apex y=10
    _t(bf, 'circle', _LAVA, (0, 8.4, 0), (2.8,) * 3, (90, 0, 0), glow=True, parent=smol)
    _mountains(bf, 4.2, 1.3, 7, 26, 40, _BASALT2, 5, 10)
    _celestial(bf, 0.7, 20, 5.0, color.rgb32(210, 70, 40), dist=56)


_VOLCANIC = Theme(
    'Molten Caldera', banner_color=_LAVA_HOT,
    ground_color=color.rgb32(24, 20, 23), build=_build_volcanic, ground_radius=R * 6.0,
    horizon_color=color.rgb32(46, 16, 14),
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

    # -- Backdrop: a huge ringed gas-giant + a smaller moon hanging in the void. --
    _ringed_planet(bf, 1.7, 34, 11.0, color.rgb32(150, 90, 210), color.rgb32(120, 200, 255))
    _celestial(bf, 4.3, 22, 3.5, color.rgb32(190, 190, 220), dist=56)


_ASTRAL = Theme(
    'Astral Void', banner_color=_RUNE_A,
    ground_color=_VOID, build=_build_astral, ground_radius=R * 6.0, horizon_color=_VOID,
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

    # -- Backdrop: misty blue forested hills receding behind the giant trees. --
    _mountains(bf, 3.5, 1.7, 9, 30, 44, color.rgb32(70, 104, 96), 6, 12)
    _mountains(bf, 0.6, 1.2, 5, 32, 44, color.rgb32(86, 120, 112), 5, 9)
    _glow_halo(bf, 2.4, 30, 4.5, color.rgba32(210, 240, 200, 120))


_FOREST = Theme(
    'Enchanted Forest', banner_color=_MUSH_GLOW,
    ground_color=color.rgb32(46, 68, 42), build=_build_forest,
    ambient={'style': 'motes', 'color': color.rgb32(200, 240, 150), 'count': 70,
             'size': 0.08, 'speed': (0.1, 0.3), 'spread': 1.5, 'alpha': 0.9, 'flicker': 0.7},
    sky_texture='sky_default', sky_color=color.rgb32(86, 120, 120),
    sun_azimuth=140.0, sun_elevation=38.0, sun_intensity=0.7, ambient_light=0.5,
    window_bg=color.rgb32(24, 36, 30),
)


# =========================================================================== #
#  THEME: Desert Dunes
# =========================================================================== #
_SAND = color.rgb32(218, 190, 138)
_SAND_DK = color.rgb32(196, 166, 110)
_SAND_SH = color.rgb32(172, 142, 92)
_OASIS = color.rgb32(64, 138, 150)
_OASIS_HI = color.rgb32(110, 176, 180)
_PALM_T = color.rgb32(122, 92, 56)
_PALM = color.rgb32(92, 142, 78)
_PYRAMID = color.rgb32(200, 172, 122)


def _build_desert(bf):
    # Floor: rippled sand, an oasis pool, scattered dry stones.
    _disc(bf, _SAND_DK, 9.0, y=0.04, stagger=False)
    _disc(bf, _SAND, 5.5, y=0.07, stagger=False)
    for _ in range(10):     # wind ripples (long gentle sand bars)
        a = random.uniform(0, math.tau)
        _crack(bf, math.cos(a) * 1.5, math.sin(a) * 1.5,
               math.cos(a) * 10.5, math.sin(a) * 10.5, _SAND_SH, width=0.5, y=0.12, segs=4)
    _scatter(10, 4.0, 11.0, lambda x, z, a, i: _disc(bf, _SAND_SH, random.uniform(0.4, 1.0),
                                                     y=0.1, center=(x, z)))
    oasis = (12.0, 1.0)     # oasis off the +x edge
    _disc(bf, _OASIS, 3.0, y=0.06, center=oasis, stagger=False)
    _disc(bf, _OASIS_HI, 1.6, y=0.09, center=oasis, stagger=False)
    _scatter(8, 0.0, 2.6, lambda x, z, a, i: _box(bf, _PALM, oasis[0] + x, oasis[1] + z,
                                                  0.1, random.uniform(0.6, 1.2), 0.1))

    # Palm trees by the oasis + rolling dunes ringing the arena.
    def palm(x, z, a, i):
        h = random.uniform(3.0, 4.5)
        _box(bf, _PALM_T, x, z, 0.3, h, 0.3, rot=(random.uniform(-6, 6), 0, random.uniform(-6, 6)))
        for f in range(7):  # drooping fronds
            fa = (f / 7) * math.tau
            _box(bf, _PALM, x + math.cos(fa) * 1.2, z + math.sin(fa) * 1.2, 2.2, 0.12, 0.4,
                 y0=GROUND_Y + h - 0.2, rot=(0, math.degrees(fa), -28))
    _cluster(5, 0.1, 0.4, 12.5, 14.5, palm)
    # Rolling dunes ringing the arena: wide, low, partially-buried sand ridges
    # (squashed + elongated spheres sunk into the ground) -- NOT the round balls
    # the old _blob produced.
    _scatter(14, 12.0, 22.0, lambda x, z, a, i: _t(
        bf, 'sphere', random.choice((_SAND, _SAND_DK)),
        (x, GROUND_Y - 1.4, z),
        (random.uniform(6, 12), random.uniform(4, 7), random.uniform(10, 18)),
        (0, random.uniform(0, 360), 0)))
    # A half-buried obelisk.
    _box(bf, _PYRAMID, math.cos(3.5) * 13, math.sin(3.5) * 13, 0.9, 5.0, 0.9,
         rot=(0, 20, 12))

    # Backdrop: great pyramids (diamonds centred at ground => only the top pyramid
    # shows, the lower half buried), distant mesas, a huge low desert sun.
    for ang, rr, s in ((2.1, 38, 16), (2.5, 44, 22), (1.7, 42, 13)):
        _t(bf, 'diamond', _PYRAMID,
           (math.cos(ang) * rr, GROUND_Y, math.sin(ang) * rr),
           (s * 1.4, s * 2.0, s * 1.4), (0, 45, 0))
    _mountains(bf, 4.4, 1.4, 6, 30, 42, _SAND_SH, 4, 8)
    _celestial(bf, 0.6, 14, 8.0, color.rgb32(255, 210, 130), dist=56)


_DESERT = Theme(
    'Desert Dunes', banner_color=_SAND,
    ground_color=color.rgb32(214, 184, 132), horizon_color=color.rgb32(228, 196, 142),
    build=_build_desert,
    ambient={'style': 'motes', 'color': color.rgba32(226, 200, 150, 150), 'count': 50,
             'size': 0.06, 'speed': (0.15, 0.35), 'spread': 1.6, 'alpha': 0.5},
    sky_texture='sky_sunset', sky_color=color.rgb32(245, 206, 150),
    sun_azimuth=35.0, sun_elevation=18.0, sun_intensity=0.95, ambient_light=0.55,
    window_bg=color.rgb32(60, 44, 30),
)


# =========================================================================== #
#  THEME: Sky Citadel  (floating isles among the clouds)
# =========================================================================== #
_SKY_STONE = color.rgb32(182, 188, 202)
_SKY_STONE_DK = color.rgb32(150, 156, 172)
_SKY_GRASS = color.rgb32(112, 172, 122)
_CLOUD = color.rgba32(248, 250, 255, 235)
_FALL = color.rgb32(150, 200, 235)
_GOLDR = color.rgb32(228, 196, 110)


def _build_citadel(bf):
    # Floor: the stone top of a floating island, gold-inlaid, grassy at the rim.
    _concentric(bf, [(11.5, _SKY_STONE_DK), (9.5, _SKY_STONE), (3.0, _SKY_STONE_DK)])
    _ring_studs(bf, 9.6, 56, _GOLDR, y=0.16, size=0.16, tall=0.06, glow=True)
    _radials(bf, 12, 3.0, 9.0, _GOLDR, y=0.18, width=0.1, glow=True)
    _disc(bf, _GOLDR, 1.6, y=0.2, glow=True, stagger=False)
    _ring_studs(bf, 11.4, 40, _SKY_GRASS, y=0.14, size=0.5, tall=0.2)   # mossy rim
    # Low golden balustrade posts around the rim.
    for i in range(16):
        a = (i / 16) * math.tau
        _box(bf, _SKY_STONE, math.cos(a) * 11.7, math.sin(a) * 11.7, 0.3, 0.9, 0.3)
        _t(bf, 'sphere', _GOLDR, (math.cos(a) * 11.7, GROUND_Y + 1.0, math.sin(a) * 11.7),
           (0.32,) * 3, glow=True)

    # Background: floating islands w/ waterfalls + a grand castle + clouds + rainbow.
    for ang, rr, yy, tr in ((1.2, 22, 5, 3.0), (2.6, 26, 9, 4.0), (4.0, 24, 3, 2.4),
                            (5.3, 28, 11, 3.4), (0.2, 30, 7, 3.0)):
        x, z = math.cos(ang) * rr, math.sin(ang) * rr
        _floating_island(bf, x, z, GROUND_Y + yy, tr, _SKY_GRASS, _SKY_STONE_DK, _FALL)
        if random.random() < 0.6:   # a tiny tree or spire on top
            _box(bf, _PALM_T if random.random() < 0.5 else _SKY_STONE,
                 x, z, 0.4, 2.0, 0.4, y0=GROUND_Y + yy)
    # Grand floating castle on the -z horizon.
    castle = bf.node(position=(math.cos(3.5) * 40, GROUND_Y + 8, math.sin(3.5) * 40))
    for cx, cw, ch in ((-4, 3, 10), (0, 5, 14), (4, 3, 11)):
        _t(bf, 'cube', _SKY_STONE, (cx, ch * 0.5, 0), (cw, ch, cw), parent=castle)
        _t(bf, 'diamond', _GOLDR, (cx, ch + 1.2, 0), (cw * 0.9, 2.6, cw * 0.9), parent=castle, glow=True)
    _floating_island(bf, math.cos(3.5) * 40, GROUND_Y + 1, math.sin(3.5) * 40, 7, _SKY_STONE_DK, _SKY_STONE_DK)
    _cloud_ring(bf, 14, 26, 6, 3.0, _CLOUD)
    _cloud_ring(bf, 8, 40, 12, 5.0, _CLOUD)
    for k, c in enumerate((color.rgb32(255, 110, 110), color.rgb32(255, 180, 90),
                           color.rgb32(255, 240, 120), color.rgb32(130, 220, 130),
                           color.rgb32(120, 190, 255), color.rgb32(170, 130, 235))):
        _arc(bf, 26, 30 + k * 1.3, c, (0, GROUND_Y + 2, 34), thick=1.2, plane='zy')
    _celestial(bf, 0.9, 40, 6.0, color.rgb32(255, 250, 225), dist=58)


_CITADEL = Theme(
    'Sky Citadel', banner_color=_GOLDR,
    ground_color=color.rgb32(176, 182, 196), horizon_color=color.rgb32(150, 156, 172),
    build=_build_citadel,
    ambient={'style': 'motes', 'color': _CLOUD, 'count': 40, 'size': 0.1,
             'speed': (0.1, 0.25), 'spread': 1.7, 'alpha': 0.6},
    sky_texture='sky_default', sky_color=color.rgb32(150, 200, 245),
    sun_azimuth=55.0, sun_elevation=52.0, sun_intensity=0.9, ambient_light=0.6,
    window_bg=color.rgb32(70, 110, 150),
)


# =========================================================================== #
#  THEME: Crystal Cavern  (an underground hollow lit by glowing crystals)
# =========================================================================== #
_CAVE = color.rgb32(58, 54, 66)
_CAVE_DK = color.rgb32(40, 36, 48)
_CRYS_A = color.rgb32(120, 220, 255)
_CRYS_B = color.rgb32(205, 120, 255)
_CRYS_C = color.rgb32(120, 255, 185)
_UWATER = color.rgb32(58, 116, 146)


def _build_cavern(bf):
    # Floor: dark cave rock, an underground lake, glowing mineral veins.
    _disc(bf, _CAVE, 11.0, y=0.04, stagger=False)
    _scatter(9, 2.0, 10.0, lambda x, z, a, i: _disc(bf, _CAVE_DK, random.uniform(0.8, 2.0),
                                                    y=0.07, center=(x, z)))
    lake = (11.5, 1.5)
    _disc(bf, _UWATER, 3.2, y=0.06, center=lake, stagger=False)
    _disc(bf, _CRYS_A, 1.4, y=0.09, glow=True, center=lake, stagger=False)
    for _ in range(6):      # glowing veins
        a = random.uniform(0, math.tau)
        _crack(bf, math.cos(a) * 1.0, math.sin(a) * 1.0,
               math.cos(a) * 10.0, math.sin(a) * 10.0,
               random.choice((_CRYS_A, _CRYS_B, _CRYS_C)), width=0.1, y=0.12, glow=True, segs=5)
    # Small glowing crystal clusters dotted just outside the combat circle, plus
    # low glowing crystal nubs (kept short) scattered across the floor itself.
    _scatter(10, 11.6, 15.0, lambda x, z, a, i: _crystal(
        bf, random.choice((_CRYS_A, _CRYS_B, _CRYS_C)), x, z, random.uniform(0.8, 1.6),
        random.uniform(0.25, 0.5), tilt=20))
    _scatter(10, 3.0, 10.5, lambda x, z, a, i: _crystal(
        bf, random.choice((_CRYS_A, _CRYS_B, _CRYS_C)), x, z, random.uniform(0.25, 0.5),
        random.uniform(0.18, 0.3), tilt=24))

    # Stalagmites + big crystal clusters at the rim; stalactites + crystal ceiling above.
    def formation(x, z, a, i):
        _crystal(bf, _CAVE_DK, x, z, random.uniform(2.5, 5.0), random.uniform(1.0, 2.0),
                 tilt=6, glow=False)
        for _ in range(random.randint(2, 4)):
            _crystal(bf, random.choice((_CRYS_A, _CRYS_B, _CRYS_C)),
                     x + random.uniform(-1.2, 1.2), z + random.uniform(-1.2, 1.2),
                     random.uniform(1.5, 3.5), random.uniform(0.3, 0.7), tilt=18)
    _cluster(5, 2.3, 0.6, 13, 17, formation)
    _cluster(4, 5.0, 0.5, 13, 16, formation)
    _cluster(4, 0.3, 0.5, 13, 16, formation)
    # Enclosing cave walls.
    _scatter(22, 17, 24, lambda x, z, a, i: _crystal(bf, _CAVE_DK, x, z,
             random.uniform(6, 14), random.uniform(2, 5), tilt=8, glow=False))
    # Stalactites + a glowing crystal ceiling cluster (downward crystals up high).
    _scatter(16, 4, 16, lambda x, z, a, i: _t(bf, 'diamond', _CAVE_DK,
             (x, GROUND_Y + random.uniform(13, 18), z),
             (random.uniform(0.6, 1.4), random.uniform(2, 5), random.uniform(0.6, 1.4)), (180, 0, 0)))
    _scatter(12, 2, 12, lambda x, z, a, i: _t(bf, 'diamond',
             random.choice((_CRYS_A, _CRYS_B, _CRYS_C)),
             (x, GROUND_Y + random.uniform(12, 16), z),
             (random.uniform(0.4, 0.9), random.uniform(1.5, 3), random.uniform(0.4, 0.9)),
             (180, 0, 0), glow=True))


_CAVERN = Theme(
    'Crystal Cavern', banner_color=_CRYS_A,
    ground_color=color.rgb32(52, 48, 60), horizon_color=color.rgb32(34, 30, 42),
    build=_build_cavern,
    ambient={'style': 'motes', 'color': _CRYS_A, 'count': 70, 'size': 0.08,
             'speed': (0.08, 0.2), 'spread': 1.4, 'alpha': 0.85, 'flicker': 0.4},
    sky_texture=None, sky_color=color.rgb32(14, 13, 22),
    sun_azimuth=90.0, sun_elevation=70.0, sun_intensity=0.35, ambient_light=0.5,
    window_bg=color.rgb32(10, 9, 16),
)


# =========================================================================== #
#  THEME: Moonlit Necropolis  (a gothic graveyard under a full moon)
# =========================================================================== #
_GRAVE = color.rgb32(70, 78, 64)
_GRAVE_DK = color.rgb32(52, 58, 48)
_TOMB = color.rgb32(150, 152, 158)
_TOMB_DK = color.rgb32(112, 114, 122)
_DEAD_TREE = color.rgb32(72, 64, 56)
_MOONC = color.rgb32(238, 242, 226)


def _build_necropolis(bf):
    # Floor: dark turf, a stone path, patchy grass and bare earth.
    _scatter(26, 0.0, 11.0, lambda x, z, a, i: _disc(bf, random.choice((_GRAVE, _GRAVE_DK)),
             random.uniform(1.0, 2.4), y=0.04, center=(x, z)))
    for k in range(8):      # a worn flagstone path
        rr = 10.5 - k * 1.3
        _disc(bf, _TOMB_DK, 0.7, y=0.1, center=(math.cos(0.4) * rr, math.sin(0.4) * rr))
    _disc(bf, _TOMB_DK, 2.2, y=0.08, stagger=False)   # central cracked slab
    _disc(bf, _GRAVE_DK, 1.7, y=0.11, stagger=False)

    # Tombstones (low, leaning) scattered; bare dead trees; iron fence at the rim.
    _scatter(18, 5.0, 11.0, lambda x, z, a, i: (
        _box(bf, random.choice((_TOMB, _TOMB_DK)), x, z, 0.6, random.uniform(0.5, 0.9), 0.18,
             rot=(random.uniform(-10, 10), random.uniform(0, 360), random.uniform(-8, 8)))))

    def dead_tree(x, z, a, i):
        h = random.uniform(3.0, 5.0)
        _box(bf, _DEAD_TREE, x, z, 0.4, h, 0.4)
        for _ in range(5):
            ba = random.uniform(0, math.tau)
            _box(bf, _DEAD_TREE, x + math.cos(ba) * 0.8, z + math.sin(ba) * 0.8,
                 0.15, 1.8, 0.15, y0=GROUND_Y + h * 0.7,
                 rot=(random.uniform(20, 50), math.degrees(ba), 0))
    _cluster(4, 2.6, 0.6, 12.5, 15.5, dead_tree)
    _cluster(3, 5.2, 0.5, 12.5, 15.0, dead_tree)
    for i in range(40):     # spiked iron fence
        a = (i / 40) * math.tau
        _box(bf, color.rgb32(40, 42, 48), math.cos(a) * 12.0, math.sin(a) * 12.0,
             0.1, 1.2, 0.1)

    # A ruined gothic cathedral on the -x side.
    cath = bf.node(position=(-R - 3.0, GROUND_Y, 0))
    _t(bf, 'cube', _TOMB_DK, (0, 4, 0), (6, 8, 4), parent=cath)
    for sx in (-2.4, 2.4):
        _t(bf, 'cube', _TOMB_DK, (sx, 6, 1.5), (1.4, 12, 1.4), parent=cath)     # towers
        _t(bf, 'diamond', _TOMB, (sx, 13, 1.5), (1.8, 3.5, 1.8), parent=cath)   # spires
    _t(bf, 'cube', color.rgb32(120, 150, 200), (0, 4, 2.05), (1.4, 3.0, 0.2), glow=True, parent=cath)

    # Backdrop: a huge full moon, distant spires, bare-tree hills, stars.
    _celestial(bf, 1.4, 26, 11.0, _MOONC, dist=58)
    _t(bf, 'sphere', color.rgb32(210, 214, 200), _sky_pos(1.4, 26, 57.6), (9.4,) * 3, glow=True)
    _mountains(bf, 3.6, 1.6, 9, 26, 40, color.rgb32(38, 42, 50), 5, 11)
    _skyline(bf, 2.5, 0.5, 5, 32, [_TOMB_DK, color.rgb32(60, 62, 70)], 8, 14, w_lo=1.0, w_hi=1.8)
    _scatter(70, 6, 60, lambda x, z, a, i: _t(bf, 'cube', color.rgb32(220, 222, 210),
             (x, GROUND_Y + random.uniform(10, 30), z), (0.12,) * 3, glow=True))


_NECROPOLIS = Theme(
    'Moonlit Necropolis', banner_color=color.rgb32(180, 200, 220),
    ground_color=color.rgb32(60, 68, 56), horizon_color=color.rgb32(40, 46, 42),
    build=_build_necropolis,
    ambient={'style': 'motes', 'color': color.rgba32(200, 220, 220, 120), 'count': 40,
             'size': 0.1, 'speed': (0.06, 0.16), 'spread': 1.5, 'alpha': 0.4},
    sky_texture=None, sky_color=color.rgb32(28, 28, 52),
    sun_azimuth=80.0, sun_elevation=40.0, sun_intensity=0.45, ambient_light=0.5,
    window_bg=color.rgb32(16, 16, 32),
)


# =========================================================================== #
#  THEME: Coral Reef  (a sunlit seabed arena underwater)
# =========================================================================== #
_SEABED = color.rgb32(202, 188, 152)
_SEABED_DK = color.rgb32(178, 164, 128)
_CORAL_A = color.rgb32(255, 132, 120)
_CORAL_B = color.rgb32(255, 184, 92)
_CORAL_C = color.rgb32(190, 124, 224)
_CORAL_D = color.rgb32(120, 214, 200)
_KELP = color.rgb32(92, 152, 92)
_RAYC = color.rgba32(180, 230, 240, 90)


def _build_reef(bf):
    # Floor: tan seabed with ripples, shells/starfish, a sandy mound.
    _disc(bf, _SEABED_DK, 9.5, y=0.04, stagger=False)
    _disc(bf, _SEABED, 5.5, y=0.07, stagger=False)
    for _ in range(9):
        a = random.uniform(0, math.tau)
        _crack(bf, math.cos(a) * 1.5, math.sin(a) * 1.5,
               math.cos(a) * 10.0, math.sin(a) * 10.0, _SEABED_DK, width=0.4, y=0.1, segs=4)
    _scatter(14, 2.0, 10.5, lambda x, z, a, i: _disc(
        bf, random.choice((_CORAL_A, _CORAL_B, _CORAL_C, _CORAL_D)),
        random.uniform(0.2, 0.45), y=0.12, center=(x, z)))

    # Coral formations + kelp clustered at the rim.
    def coral(x, z, a, i):
        col = random.choice((_CORAL_A, _CORAL_B, _CORAL_C, _CORAL_D))
        base = random.uniform(0.6, 1.4)
        _blob(bf, col, x, z, base * 1.6)
        for _ in range(random.randint(3, 5)):   # branching arms
            ba = random.uniform(0, math.tau)
            _box(bf, col, x + math.cos(ba) * 0.6, z + math.sin(ba) * 0.6,
                 0.3, random.uniform(1.0, 2.2), 0.3, y0=GROUND_Y + 0.4,
                 rot=(random.uniform(-25, 25), 0, random.uniform(-25, 25)))
    _cluster(6, 2.2, 0.6, 12.0, 15.5, coral)
    _cluster(5, 4.8, 0.6, 12.0, 15.0, coral)
    _cluster(4, 0.3, 0.5, 12.0, 14.5, coral)

    def kelp(x, z, a, i):
        for _ in range(random.randint(2, 4)):
            _box(bf, _KELP, x + random.uniform(-0.6, 0.6), z + random.uniform(-0.6, 0.6),
                 0.18, random.uniform(4, 7), 0.18, rot=(random.uniform(-12, 12), 0,
                 random.uniform(-12, 12)))
    _cluster(5, 1.3, 0.5, 12.5, 16.0, kelp)
    _cluster(4, 5.6, 0.5, 12.5, 15.0, kelp)

    # A sunken ship on the -z side.
    ship = bf.node(position=(math.cos(3.4) * 16, GROUND_Y, math.sin(3.4) * 16),
                   rotation=(0, 40, 14))
    _t(bf, 'cube', color.rgb32(96, 72, 50), (0, 1.5, 0), (8, 3, 2.6), parent=ship)
    _t(bf, 'cube', color.rgb32(110, 84, 58), (0, 3.2, 0), (5, 1.2, 2.2), parent=ship)
    _t(bf, 'cube', color.rgb32(80, 60, 42), (0, 6, 0), (0.4, 7, 0.4), parent=ship)   # mast
    _t(bf, 'cube', _GOLDR, (3, 0.5, 1.6), (1.0, 0.7, 0.7), glow=True, parent=ship)    # treasure

    # Background: reef walls, fish schools, godrays from the surface.
    _mountains(bf, 4.2, 1.6, 8, 24, 38, color.rgb32(96, 150, 150), 6, 13)
    for _ in range(7):       # schools of fish
        ca = random.uniform(0, math.tau)
        cr = random.uniform(16, 26)
        cy = random.uniform(5, 12)
        col = random.choice((_CORAL_B, _CORAL_D, color.rgb32(255, 230, 120)))
        for _ in range(8):
            _t(bf, 'cube', col, (math.cos(ca) * cr + random.uniform(-2, 2),
               GROUND_Y + cy + random.uniform(-1.5, 1.5), math.sin(ca) * cr + random.uniform(-2, 2)),
               (0.4, 0.25, 0.18), glow=True)
    _scatter(10, 12, 22, lambda x, z, a, i: _t(bf, 'cube', _RAYC,
             (x, GROUND_Y + 12, z), (1.6, 24, 1.6), (random.uniform(-6, 6), 0, random.uniform(-6, 6)),
             glow=True))
    _celestial(bf, 1.4, 60, 7.0, color.rgba32(210, 240, 245, 160), dist=40)


_REEF = Theme(
    'Coral Reef', banner_color=_CORAL_D,
    ground_color=color.rgb32(196, 182, 148), horizon_color=color.rgb32(120, 168, 168),
    build=_build_reef,
    ambient={'style': 'embers', 'color': color.rgba32(220, 245, 250, 180), 'count': 70,
             'size': 0.06, 'speed': (0.5, 1.2), 'sway': 0.4, 'spread': 1.5, 'alpha': 0.7},
    sky_texture=None, sky_color=color.rgb32(40, 120, 142),
    sun_azimuth=70.0, sun_elevation=66.0, sun_intensity=0.7, ambient_light=0.6,
    window_bg=color.rgb32(20, 70, 86),
)


# =========================================================================== #
#  THEME: Autumn Vale
# =========================================================================== #
_AGRASS = color.rgb32(104, 114, 64)
_AGRASS_DK = color.rgb32(86, 96, 52)
_LEAF_R = color.rgb32(198, 78, 50)
_LEAF_O = color.rgb32(226, 138, 54)
_LEAF_Y = color.rgb32(232, 192, 82)
_ATRUNK = color.rgb32(98, 70, 48)
_MILL = color.rgb32(134, 94, 60)
_PUMP = color.rgb32(232, 134, 48)


def _build_autumn(bf):
    # Floor: grass thick with fallen leaves, a dirt path, a pumpkin patch.
    _scatter(20, 0.0, 11.0, lambda x, z, a, i: _disc(bf, random.choice((_AGRASS, _AGRASS_DK)),
             random.uniform(1.2, 2.6), y=0.03, center=(x, z)))
    _scatter(40, 0.0, 11.2, lambda x, z, a, i: _disc(
        bf, random.choice((_LEAF_R, _LEAF_O, _LEAF_Y)), random.uniform(0.18, 0.4),
        y=0.12, center=(x, z)))
    for k in range(8):
        rr = 10.5 - k * 1.2
        _disc(bf, color.rgb32(120, 96, 64), 0.8, y=0.08,
              center=(math.cos(4.0) * rr, math.sin(4.0) * rr))
    for _ in range(6):      # pumpkins
        a = random.uniform(1.0, 2.0)
        rr = random.uniform(7, 10)
        _blob(bf, _PUMP, math.cos(a) * rr, math.sin(a) * rr, random.uniform(0.5, 0.9))

    # Autumn trees ringing the vale + a windmill + a fence.
    def atree(x, z, a, i):
        h = random.uniform(3.0, 4.5)
        _box(bf, _ATRUNK, x, z, 0.5, h, 0.5)
        for _ in range(5):
            _blob(bf, random.choice((_LEAF_R, _LEAF_O, _LEAF_Y)),
                  x + random.uniform(-1.4, 1.4), z + random.uniform(-1.4, 1.4),
                  random.uniform(2.2, 3.4), y=h + random.uniform(-0.4, 1.2))
    _cluster(5, 2.3, 0.7, 13, 17, atree)
    _cluster(4, 5.0, 0.6, 13, 16, atree)
    atree(math.cos(0.4) * 14, math.sin(0.4) * 14, 0, 0)
    # Windmill on the +x side.
    mill = bf.node(position=(math.cos(0.0) * 15, GROUND_Y, math.sin(0.0) * 15))
    _t(bf, 'cube', _MILL, (0, 3, 0), (3.4, 6, 3.4), parent=mill)
    _t(bf, 'diamond', color.rgb32(150, 70, 50), (0, 6.6, 0), (3.6, 2.4, 3.6), parent=mill)
    hub = bf.node(position=(math.cos(0.0) * 15 - 1.9, GROUND_Y + 4.5, math.sin(0.0) * 15))
    for b in range(4):
        _t(bf, 'cube', _MILL, (0, 0, 0), (0.4, 5.0, 0.6), (b * 90, 0, 0), parent=hub)

    # Backdrop: rolling autumn hills, distant forest, a warm low sun.
    _mountains(bf, 3.4, 1.7, 9, 28, 42, color.rgb32(150, 116, 64), 5, 10)
    _mountains(bf, 1.0, 1.2, 6, 30, 42, color.rgb32(126, 96, 58), 4, 8)
    _celestial(bf, 5.4, 16, 6.5, color.rgb32(255, 196, 120), dist=56)


_AUTUMN = Theme(
    'Autumn Vale', banner_color=_LEAF_O,
    ground_color=color.rgb32(98, 108, 60), horizon_color=color.rgb32(120, 100, 58),
    build=_build_autumn,
    ambient={'style': 'petals', 'color': _LEAF_O, 'count': 80, 'size': 0.13,
             'speed': (0.5, 1.1), 'sway': 1.0, 'spread': 1.5, 'alpha': 0.95},
    sky_texture='sky_default', sky_color=color.rgb32(238, 196, 142),
    sun_azimuth=130.0, sun_elevation=24.0, sun_intensity=0.85, ambient_light=0.5,
    window_bg=color.rgb32(56, 42, 28),
)


# =========================================================================== #
#  THEME: Celestial Sanctum  (a marble temple upon the clouds)
# =========================================================================== #
_MARB = color.rgb32(214, 214, 224)
_MARB_DK = color.rgb32(184, 186, 200)
_GOLD2 = color.rgb32(234, 202, 120)
_HCLOUD = color.rgba32(250, 250, 255, 235)


def _build_sanctum(bf):
    # Floor: white-and-gold marble with a radiant sun emblem; clouds at the rim.
    _concentric(bf, [(11.0, _MARB_DK), (8.5, _MARB), (3.2, _MARB_DK)])
    _ring_studs(bf, 8.6, 60, _GOLD2, y=0.16, size=0.16, tall=0.06, glow=True)
    _radials(bf, 16, 3.2, 8.2, _GOLD2, y=0.18, width=0.12, glow=True)
    _disc(bf, _GOLD2, 1.8, y=0.2, glow=True, stagger=False)
    _ring_studs(bf, 11.3, 36, _HCLOUD, y=0.1, size=0.7, tall=0.3, glow=True)

    # Golden colonnade + braziers framing the temple (asymmetric arc, at the rim).
    def pillar(x, z, a, i):
        _box(bf, _MARB, x, z, 0.7, 5.0, 0.7)
        _box(bf, _GOLD2, x, z, 0.9, 0.5, 0.9, y0=GROUND_Y + 5.0)
        _box(bf, _MARB_DK, x, z, 0.95, 0.3, 0.95, y0=GROUND_Y + 5.5)
    _cluster(7, 3.3, 1.1, 11.6, 12.4, pillar)
    for ang in (0.4, 5.5):   # braziers
        x, z = math.cos(ang) * 11.5, math.sin(ang) * 11.5
        _box(bf, _GOLD2, x, z, 0.5, 1.4, 0.5)
        _t(bf, 'sphere', color.rgb32(255, 220, 150), (x, GROUND_Y + 1.7, z), (0.7,) * 3, glow=True)
    # An altar arch on the -x side.
    altar = bf.node(position=(-R - 2.0, GROUND_Y, 0))
    for sx in (-2, 2):
        _t(bf, 'cube', _MARB, (sx, 3, 0), (0.9, 6, 0.9), parent=altar)
    _t(bf, 'cube', _GOLD2, (0, 6.3, 0), (5, 0.8, 1.2), glow=True, parent=altar)

    # Background: towering clouds, a radiant sun w/ godrays, a rainbow bridge.
    _cloud_ring(bf, 18, 24, 5, 4.0, _HCLOUD)
    _cloud_ring(bf, 10, 40, 12, 6.0, _HCLOUD)
    _glow_halo(bf, 1.6, 42, 9.0, color.rgba32(255, 250, 220, 150))
    _scatter(12, 13, 22, lambda x, z, a, i: _t(bf, 'cube', color.rgba32(255, 250, 220, 90),
             (x, GROUND_Y + 14, z), (1.4, 26, 1.4), (random.uniform(-5, 5), 0, random.uniform(-5, 5)),
             glow=True))
    for k, c in enumerate((color.rgb32(255, 140, 140), color.rgb32(255, 200, 120),
                           color.rgb32(255, 245, 150), color.rgb32(150, 230, 150),
                           color.rgb32(150, 200, 255))):
        _arc(bf, 24, 28 + k * 1.2, c, (0, GROUND_Y, -34), thick=1.1, plane='xy')


_SANCTUM = Theme(
    'Celestial Sanctum', banner_color=_GOLD2,
    ground_color=color.rgb32(206, 208, 218), horizon_color=color.rgb32(220, 226, 238),
    build=_build_sanctum,
    ambient={'style': 'motes', 'color': color.rgba32(255, 250, 220, 200), 'count': 60,
             'size': 0.07, 'speed': (0.08, 0.22), 'spread': 1.6, 'alpha': 0.7, 'flicker': 0.3},
    sky_texture='sky_default', sky_color=color.rgb32(184, 216, 250),
    sun_azimuth=80.0, sun_elevation=58.0, sun_intensity=1.0, ambient_light=0.65,
    window_bg=color.rgb32(150, 180, 210),
)


# =========================================================================== #
#  THEME: Highland Cliffs  (a clifftop stone circle in the mountains)
# =========================================================================== #
_HGRASS = color.rgb32(90, 120, 68)
_HGRASS_DK = color.rgb32(74, 102, 58)
_HROCK = color.rgb32(122, 122, 114)
_HEATH = color.rgb32(150, 112, 162)
_HFALL = color.rgb32(150, 200, 230)


def _build_highland(bf):
    # Floor: highland turf with rocky outcrops, heather, a worn path.
    _scatter(22, 0.0, 11.0, lambda x, z, a, i: _disc(bf, random.choice((_HGRASS, _HGRASS_DK)),
             random.uniform(1.2, 2.6), y=0.03, center=(x, z)))
    _scatter(10, 2.0, 10.5, lambda x, z, a, i: _disc(bf, _HEATH, random.uniform(0.4, 0.9),
             y=0.1, center=(x, z)))
    _scatter(8, 1.0, 9.0, lambda x, z, a, i: _disc(bf, _HROCK, random.uniform(0.5, 1.1),
             y=0.08, center=(x, z)))

    # An ancient standing-stone circle ringing the arena (the henge frames play).
    for i in range(11):
        a = (i / 11) * math.tau
        x, z = math.cos(a) * 11.6, math.sin(a) * 11.6
        _box(bf, _HROCK, x, z, 1.0, random.uniform(3.0, 4.0), 0.6,
             rot=(random.uniform(-4, 4), math.degrees(a), random.uniform(-4, 4)))
        if i % 3 == 0:   # occasional lintel
            _box(bf, _HROCK, x, z, 1.4, 0.6, 0.8, y0=GROUND_Y + 4.0)
    # A lone twisted tree + scattered boulders.
    _box(bf, _ATRUNK, math.cos(1.2) * 13, math.sin(1.2) * 13, 0.6, 3.5, 0.6, rot=(0, 0, 10))
    _blob(bf, _HGRASS_DK, math.cos(1.2) * 13, math.sin(1.2) * 13, 3.0, y=GROUND_Y + 3.8)
    _scatter(8, 12.5, 16, lambda x, z, a, i: _blob(bf, _HROCK, x, z, random.uniform(1.0, 2.2)))

    # Backdrop: a great snow-capped range, waterfalls, a distant loch, bright sun.
    _mountains(bf, 3.6, 1.8, 11, 26, 44, _HROCK, 9, 18, snow_col=color.rgb32(238, 244, 248))
    for ang in (3.0, 3.8, 4.5):     # waterfalls down the cliffs
        _t(bf, 'cube', _HFALL, (math.cos(ang) * 28, GROUND_Y + 7, math.sin(ang) * 28),
           (1.0, 14, 0.5), glow=True)
    _disc(bf, color.rgb32(96, 140, 168), 8.0, y=0.0, center=(math.cos(0.6) * 30, math.sin(0.6) * 30))
    _celestial(bf, 0.4, 30, 5.0, color.rgb32(255, 248, 224), dist=58)


_HIGHLAND = Theme(
    'Highland Cliffs', banner_color=_HEATH,
    ground_color=color.rgb32(84, 114, 64), horizon_color=color.rgb32(96, 124, 78),
    build=_build_highland,
    ambient={'style': 'motes', 'color': color.rgba32(225, 235, 235, 110), 'count': 36,
             'size': 0.09, 'speed': (0.1, 0.25), 'spread': 1.6, 'alpha': 0.4},
    sky_texture='sky_default', sky_color=color.rgb32(150, 186, 224),
    sun_azimuth=25.0, sun_elevation=42.0, sun_intensity=0.9, ambient_light=0.55,
    window_bg=color.rgb32(44, 60, 70),
)


# =========================================================================== #
#  THEME: Alien Exoworld
# =========================================================================== #
_XGROUND = color.rgb32(126, 100, 140)
_XGROUND_DK = color.rgb32(104, 80, 118)
_XROCK = color.rgb32(88, 74, 98)
_XFLORA_A = color.rgb32(120, 255, 200)
_XFLORA_B = color.rgb32(255, 120, 200)
_XFLORA_C = color.rgb32(150, 200, 255)


def _build_exoworld(bf):
    # Floor: violet alien soil with glowing patches + strange ring formations.
    _disc(bf, _XGROUND_DK, 10.0, y=0.04, stagger=False)
    _ring_studs(bf, 6.5, 40, _XFLORA_A, y=0.12, size=0.14, tall=0.05, glow=True)
    _ring_studs(bf, 4.0, 28, _XFLORA_B, y=0.14, size=0.14, tall=0.05, glow=True)
    _scatter(14, 1.5, 10.5, lambda x, z, a, i: _disc(
        bf, random.choice((_XFLORA_A, _XFLORA_B, _XFLORA_C)), random.uniform(0.3, 0.7),
        y=0.16, glow=True, center=(x, z)))

    # Alien spires + glowing stalk-plants + floating rocks.
    def spire(x, z, a, i):
        h = random.uniform(3.0, 6.0)
        _crystal(bf, _XROCK, x, z, h, random.uniform(0.6, 1.3), tilt=14, glow=False)
        _t(bf, 'sphere', random.choice((_XFLORA_A, _XFLORA_B, _XFLORA_C)),
           (x, GROUND_Y + h, z), (0.8,) * 3, glow=True)
    _cluster(5, 2.2, 0.6, 12.5, 16, spire)
    _cluster(4, 5.0, 0.5, 12.5, 15, spire)

    def stalk(x, z, a, i):
        h = random.uniform(2.0, 4.0)
        _box(bf, _XROCK, x, z, 0.18, h, 0.18, rot=(random.uniform(-8, 8), 0, random.uniform(-8, 8)))
        _t(bf, 'sphere', random.choice((_XFLORA_A, _XFLORA_B)), (x, GROUND_Y + h + 0.2, z),
           (random.uniform(0.6, 1.1),) * 3, glow=True)
    _cluster(8, 1.3, 0.6, 12.0, 15.0, stalk)
    _cluster(6, 3.9, 0.5, 12.0, 14.5, stalk)
    _scatter(8, 12, 18, lambda x, z, a, i: _crystal(bf, _XROCK, x, z, random.uniform(0.8, 1.6),
             random.uniform(0.8, 1.6), y0=GROUND_Y + random.uniform(3, 7), tilt=30, glow=False))

    # Backdrop: twin suns, a huge ringed planet, alien mountains.
    _celestial(bf, 1.0, 28, 6.0, color.rgb32(255, 170, 110), dist=58)
    _celestial(bf, 1.5, 22, 3.6, color.rgb32(255, 130, 180), dist=58)
    _ringed_planet(bf, 4.2, 30, 10.0, color.rgb32(120, 200, 180), color.rgb32(200, 160, 255))
    _mountains(bf, 3.4, 1.6, 8, 26, 40, _XGROUND, 6, 12)


_EXOWORLD = Theme(
    'Alien Exoworld', banner_color=_XFLORA_A,
    ground_color=color.rgb32(120, 96, 134), horizon_color=color.rgb32(100, 78, 116),
    build=_build_exoworld,
    ambient={'style': 'motes', 'color': _XFLORA_B, 'count': 70, 'size': 0.08,
             'speed': (0.1, 0.28), 'spread': 1.5, 'alpha': 0.85, 'flicker': 0.5},
    sky_texture='sky_default', sky_color=color.rgb32(70, 56, 108),
    sun_azimuth=60.0, sun_elevation=40.0, sun_intensity=0.7, ambient_light=0.5,
    window_bg=color.rgb32(30, 22, 48),
)


# =========================================================================== #
#  THEME: Golden Savanna
# =========================================================================== #
_SGRASS = color.rgb32(178, 152, 92)
_SGRASS_DK = color.rgb32(154, 128, 74)
_ACACIA = color.rgb32(94, 116, 68)
_STRUNK = color.rgb32(112, 86, 56)
_WATERHOLE = color.rgb32(98, 142, 150)
_SROCK = color.rgb32(142, 122, 92)


def _build_savanna(bf):
    # Floor: dry golden grass, a watering hole, cracked earth, scattered rocks.
    _scatter(18, 0.0, 11.0, lambda x, z, a, i: _disc(bf, random.choice((_SGRASS, _SGRASS_DK)),
             random.uniform(1.4, 3.0), y=0.03, center=(x, z)))
    hole = (12.0, 1.0)
    _disc(bf, color.rgb32(120, 100, 70), 3.2, y=0.05, center=hole, stagger=False)
    _disc(bf, _WATERHOLE, 2.2, y=0.08, center=hole, stagger=False)
    for _ in range(6):      # cracked dry earth
        a = random.uniform(0, math.tau)
        _crack(bf, math.cos(a) * 2, math.sin(a) * 2, math.cos(a) * 9, math.sin(a) * 9,
               color.rgb32(140, 118, 80), width=0.12, y=0.1, segs=4)
    _scatter(6, 6, 10.5, lambda x, z, a, i: _blob(bf, _SROCK, x, z, random.uniform(0.4, 0.85)))

    # Iconic flat-topped acacia trees + kopje boulders + termite mounds.
    def acacia(x, z, a, i):
        h = random.uniform(3.5, 5.0)
        _box(bf, _STRUNK, x, z, 0.4, h, 0.4)
        _t(bf, 'sphere', _ACACIA, (x, GROUND_Y + h + 0.4, z),
           (random.uniform(4.0, 6.0), 1.0, random.uniform(4.0, 6.0)))   # umbrella canopy
    _cluster(4, 2.4, 0.8, 13, 17, acacia)
    acacia(math.cos(0.5) * 14, math.sin(0.5) * 14, 0, 0)
    acacia(math.cos(5.4) * 15, math.sin(5.4) * 15, 0, 0)
    _scatter(3, 13, 16, lambda x, z, a, i: [_blob(bf, _SROCK, x + dx, z + dz, random.uniform(1.2, 2.4))
             for dx, dz in ((0, 0), (1.2, 0.6), (-0.8, 1.0))])
    _scatter(5, 11.6, 15, lambda x, z, a, i: _crystal(bf, color.rgb32(126, 100, 70), x, z,
             random.uniform(1.2, 2.0), random.uniform(0.6, 1.0), tilt=0, glow=False))

    # Backdrop: a vast flat horizon, a huge orange sun, herds, distant acacias.
    _celestial(bf, 0.5, 9, 11.0, color.rgb32(255, 168, 80), dist=54)
    _mountains(bf, 3.6, 1.9, 6, 32, 44, color.rgb32(150, 120, 78), 3, 6)
    for ang in (2.7, 2.9, 3.1, 3.3, 4.0, 4.3):   # distant acacia silhouettes
        x, z = math.cos(ang) * 30, math.sin(ang) * 30
        _box(bf, color.rgb32(80, 70, 56), x, z, 0.5, 5, 0.5)
        _t(bf, 'sphere', color.rgb32(80, 70, 56), (x, GROUND_Y + 5.4, z), (5, 0.9, 5))
    for ang in (2.5, 2.6, 2.75, 4.5, 4.6):       # herd silhouettes
        x, z = math.cos(ang) * 24, math.sin(ang) * 24
        _box(bf, color.rgb32(70, 58, 48), x, z, 1.4, 1.0, 0.6)
        _box(bf, color.rgb32(70, 58, 48), x + 0.8, z, 0.5, 0.6, 0.5)


_SAVANNA = Theme(
    'Golden Savanna', banner_color=color.rgb32(255, 180, 90),
    ground_color=color.rgb32(174, 148, 90), horizon_color=color.rgb32(196, 158, 96),
    build=_build_savanna,
    ambient={'style': 'motes', 'color': color.rgba32(235, 200, 140, 150), 'count': 44,
             'size': 0.06, 'speed': (0.12, 0.3), 'spread': 1.6, 'alpha': 0.5},
    sky_texture='sky_sunset', sky_color=color.rgb32(246, 184, 112),
    sun_azimuth=30.0, sun_elevation=12.0, sun_intensity=0.95, ambient_light=0.55,
    window_bg=color.rgb32(70, 46, 28),
)


# =========================================================================== #
#  THEME: Jungle Ruins  (an overgrown Aztec temple in the rainforest)
# =========================================================================== #
_JSTONE = color.rgb32(118, 126, 100)
_JSTONE_DK = color.rgb32(94, 102, 80)
_JMOSS = color.rgb32(70, 106, 58)
_JUNGLE = color.rgb32(48, 94, 52)
_JUNGLE_DK = color.rgb32(36, 78, 44)
_JWATER = color.rgb32(70, 152, 140)
_JGOLD = color.rgb32(220, 190, 110)


def _build_jungle(bf):
    # Floor: mossy stone slabs (broken), a sun-disk carving, a cenote pool.
    step = 2.2
    n = int(R / step)
    for gx in range(-n, n + 1):
        for gz in range(-n, n + 1):
            x, z = gx * step, gz * step
            if x * x + z * z > (R - 0.5) ** 2:
                continue
            if random.random() < 0.22:
                _disc(bf, _JMOSS, step * 0.5, y=0.04, center=(x, z))
                continue
            _t(bf, 'cube', random.choice((_JSTONE, _JSTONE, _JSTONE_DK)),
               (x, GROUND_Y + 0.03, z), (step * 0.9, 0.05, step * 0.9), (0, random.uniform(-4, 4), 0))
    _concentric(bf, [(2.8, _JGOLD), (2.2, _JSTONE_DK)])
    _radials(bf, 16, 0.0, 2.4, _JGOLD, y=0.14, width=0.12, glow=True)
    cenote = (12.0, 2.0)
    _disc(bf, _JSTONE_DK, 3.2, y=0.05, center=cenote, stagger=False)
    _disc(bf, _JWATER, 2.4, y=0.08, center=cenote, stagger=False)

    # A step-pyramid temple on the -z side.
    pyr = bf.node(position=(math.cos(3.6) * 17, GROUND_Y, math.sin(3.6) * 17))
    for k in range(5):
        s = 10 - k * 1.8
        _t(bf, 'cube', _JSTONE if k % 2 else _JSTONE_DK, (0, k * 1.6 + 0.8, 0),
           (s, 1.6, s), parent=pyr)
    _t(bf, 'cube', _JSTONE, (0, 9, 0), (3, 2.2, 3), parent=pyr)          # shrine
    _t(bf, 'cube', _JGOLD, (0, 9, 1.6), (1.4, 1.4, 0.3), glow=True, parent=pyr)

    # Dense jungle trees + hanging vines + totems.
    def jungle_tree(x, z, a, i):
        h = random.uniform(5, 8)
        _box(bf, color.rgb32(80, 64, 46), x, z, random.uniform(0.7, 1.1), h, random.uniform(0.7, 1.1))
        for _ in range(5):
            _blob(bf, random.choice((_JUNGLE, _JUNGLE_DK)),
                  x + random.uniform(-1.8, 1.8), z + random.uniform(-1.8, 1.8),
                  random.uniform(3.0, 4.6), y=h + random.uniform(-0.5, 2.0))
        for _ in range(3):  # hanging vines
            _box(bf, _JMOSS, x + random.uniform(-2, 2), z + random.uniform(-2, 2),
                 0.1, random.uniform(2, 4), 0.1, y0=GROUND_Y + h - 1)
    _cluster(4, 2.2, 0.7, 13, 16, jungle_tree)
    _cluster(4, 5.0, 0.6, 13, 16, jungle_tree)
    _cluster(3, 0.6, 0.5, 13, 15, jungle_tree)
    for ang in (1.0, 4.6):  # carved totems at the rim
        x, z = math.cos(ang) * 12, math.sin(ang) * 12
        for t in range(3):
            _box(bf, _JSTONE_DK if t % 2 else _JSTONE, x, z, 0.8, 0.8, 0.8, y0=GROUND_Y + 0.4 + t * 0.8)

    # Backdrop: jungle-clad hills, a waterfall, a humid sun.
    _mountains(bf, 3.4, 1.9, 10, 26, 42, color.rgb32(56, 100, 64), 7, 14)
    _t(bf, 'cube', _JWATER, (math.cos(2.6) * 28, GROUND_Y + 8, math.sin(2.6) * 28),
       (2.0, 16, 0.6), glow=True)
    _glow_halo(bf, 1.2, 34, 5.0, color.rgba32(240, 245, 200, 120))


_JUNGLE_RUINS = Theme(
    'Jungle Ruins', banner_color=_JGOLD,
    ground_color=color.rgb32(96, 106, 78), horizon_color=color.rgb32(64, 104, 70),
    build=_build_jungle,
    ambient={'style': 'motes', 'color': color.rgba32(200, 240, 150, 180), 'count': 60,
             'size': 0.07, 'speed': (0.1, 0.3), 'spread': 1.4, 'alpha': 0.8, 'flicker': 0.6},
    sky_texture='sky_default', sky_color=color.rgb32(126, 174, 152),
    sun_azimuth=110.0, sun_elevation=46.0, sun_intensity=0.8, ambient_light=0.55,
    window_bg=color.rgb32(30, 50, 38),
)


# =========================================================================== #
#  THEME: Northern Fjord  (a Viking coast under the aurora)
# =========================================================================== #
_FROCK = color.rgb32(98, 106, 110)
_FROCK_DK = color.rgb32(74, 82, 88)
_FSNOW = color.rgb32(232, 240, 246)
_FGRASS = color.rgb32(86, 110, 76)
_FWATER = color.rgb32(58, 108, 138)
_FWOOD = color.rgb32(110, 80, 52)


def _build_fjord(bf):
    # Floor: rocky coast, snow and grass patches, the fjord lapping one edge.
    _scatter(18, 0.0, 11.0, lambda x, z, a, i: _disc(bf, random.choice((_FROCK, _FROCK_DK, _FGRASS)),
             random.uniform(1.2, 2.6), y=0.03, center=(x, z)))
    _scatter(10, 1.0, 10.5, lambda x, z, a, i: _disc(bf, _FSNOW, random.uniform(0.5, 1.2),
             y=0.1, center=(x, z)))
    _disc(bf, _FWATER, 6.0, y=0.05, center=(13.5, 0.0), stagger=False)   # fjord edge

    # A beached longship + a longhall + runestones + a bonfire.
    ship = bf.node(position=(math.cos(0.1) * 13, GROUND_Y, math.sin(0.1) * 13),
                   rotation=(0, 80, 0))
    _t(bf, 'cube', _FWOOD, (0, 0.8, 0), (8, 1.6, 2.2), parent=ship)
    _t(bf, 'cube', color.rgb32(90, 64, 42), (4.2, 1.4, 0), (1.2, 2.2, 0.6),
       (0, 0, 30), parent=ship)   # dragon prow
    _t(bf, 'cube', _FWOOD, (0, 4, 0), (0.4, 7, 0.4), parent=ship)         # mast
    for sx in (-3, -1.5, 0, 1.5, 3):  # shields along the hull
        _t(bf, 'cube', random.choice((color.rgb32(180, 60, 50), color.rgb32(200, 180, 90))),
           (sx, 1.6, 1.15), (0.7, 0.7, 0.1), parent=ship)
    hall = bf.node(position=(-R - 2.5, GROUND_Y, 0))
    _t(bf, 'cube', _FWOOD, (0, 2, 0), (8, 4, 4), parent=hall)
    _t(bf, 'diamond', color.rgb32(90, 66, 44), (0, 5, 0), (9, 4, 5), parent=hall)  # A-frame roof
    for ang in (1.6, 4.7):  # runestones
        _box(bf, _FROCK, math.cos(ang) * 11.5, math.sin(ang) * 11.5, 0.9, 2.6, 0.4,
             rot=(0, math.degrees(ang), random.uniform(-4, 4)))
    _t(bf, 'sphere', color.rgb32(255, 150, 70), (math.cos(2.5) * 8, GROUND_Y + 0.25, math.sin(2.5) * 8),
       (0.6, 0.5, 0.6), glow=True)   # low campfire (kept short so it never hides a fighter)

    # Backdrop: towering fjord cliffs, waterfalls, the aurora, a low moon.
    _mountains(bf, 2.0, 1.0, 6, 22, 34, _FROCK_DK, 12, 22, snow_col=_FSNOW)
    _mountains(bf, 4.4, 1.0, 6, 22, 34, _FROCK_DK, 12, 22, snow_col=_FSNOW)
    for ang in (1.9, 4.6):
        _t(bf, 'cube', _FWATER, (math.cos(ang) * 24, GROUND_Y + 8, math.sin(ang) * 24),
           (1.2, 18, 0.5), glow=True)
    _aurora(bf, 3.2, 1.6, 18, [color.rgba32(120, 245, 170, 130), color.rgba32(120, 190, 255, 120),
                               color.rgba32(190, 140, 250, 110)], y=17, height=18, dist=36)
    _celestial(bf, 0.7, 22, 4.5, color.rgb32(232, 238, 244), dist=56)


_FJORD = Theme(
    'Northern Fjord', banner_color=color.rgb32(150, 220, 200),
    ground_color=color.rgb32(96, 106, 104), horizon_color=color.rgb32(70, 84, 90),
    build=_build_fjord,
    ambient={'style': 'snow', 'color': _FSNOW, 'count': 90, 'size': 0.07,
             'speed': (0.3, 0.7), 'sway': 0.4, 'spread': 1.6, 'alpha': 0.85},
    sky_texture=None, sky_color=color.rgb32(30, 44, 74),
    sun_azimuth=40.0, sun_elevation=30.0, sun_intensity=0.55, ambient_light=0.5,
    window_bg=color.rgb32(18, 28, 48),
)


# =========================================================================== #
#  THEME: Clockwork City  (a steampunk plaza of brass and steam)
# =========================================================================== #
_BRASS = color.rgb32(188, 148, 86)
_BRASS_DK = color.rgb32(152, 114, 62)
_IRONP = color.rgb32(98, 94, 98)
_IRONP_DK = color.rgb32(72, 70, 76)
_COPPER = color.rgb32(192, 112, 72)
_CGLOW = color.rgb32(255, 196, 120)


def _build_clockwork(bf):
    # Floor: riveted metal/cobble plates (jittered), an embedded great gear, pipes.
    step = 2.3
    n = int(R / step)
    for gx in range(-n, n + 1):
        for gz in range(-n, n + 1):
            x = gx * step + random.uniform(-0.2, 0.2)
            z = gz * step + random.uniform(-0.2, 0.2)
            if x * x + z * z > (R - 0.5) ** 2:
                continue
            _t(bf, 'cube', random.choice((_IRONP, _IRONP_DK, _BRASS_DK)),
               (x, GROUND_Y + 0.04, z), (step * 0.84, 0.08, step * 0.84),
               (0, random.uniform(-4, 4), 0))
    # Big embedded gear at centre (body disc + teeth + hub + spokes).
    _disc(bf, _BRASS_DK, 4.0, y=0.1, stagger=False)
    _disc(bf, _BRASS, 3.4, y=0.13, stagger=False)
    _ring_studs(bf, 4.1, 28, _BRASS, y=0.12, size=0.5, tall=0.16)
    _radials(bf, 8, 0.6, 3.2, _BRASS_DK, y=0.16, width=0.3)
    _disc(bf, _IRONP_DK, 0.9, y=0.18, stagger=False)
    for ang in (0.5, 3.0):  # glowing floor vents
        _disc(bf, _CGLOW, 0.6, y=0.12, glow=True, center=(math.cos(ang) * 8, math.sin(ang) * 8))

    # Standing gears, boilers, lamp posts, a clock tower.
    def gear(x, z, a, i):
        rad = random.uniform(2.0, 3.5)
        _t(bf, 'circle', _BRASS, (x, GROUND_Y + rad + 0.5, z), (rad * 2,) * 3, (0, 90, 0))
        for t in range(14):
            ta = (t / 14) * math.tau
            _t(bf, 'cube', _BRASS_DK, (x, GROUND_Y + rad + 0.5 + math.sin(ta) * rad,
               z + math.cos(ta) * rad), (0.3, 0.5, 0.3), (0, 90, math.degrees(ta)))
    _cluster(3, 2.4, 0.5, 13, 16, gear)
    _cluster(2, 5.2, 0.4, 13, 15, gear)
    for ang in (1.2, 3.9, 5.6):  # boilers
        x, z = math.cos(ang) * 13, math.sin(ang) * 13
        _box(bf, _IRONP, x, z, 1.8, 3.0, 1.8)
        _t(bf, 'cube', _COPPER, (x, GROUND_Y + 3.4, z), (0.8, 1.2, 0.8))
    for i in range(10):  # lamp posts at the rim
        a = (i / 10) * math.tau
        x, z = math.cos(a) * 11.8, math.sin(a) * 11.8
        _box(bf, _IRONP_DK, x, z, 0.18, 2.6, 0.18)
        _t(bf, 'sphere', _CGLOW, (x, GROUND_Y + 2.8, z), (0.4,) * 3, glow=True)
    tower = bf.node(position=(-R - 3.0, GROUND_Y, 0), rotation=(0, 90, 0))
    _t(bf, 'cube', _IRONP_DK, (0, 6, 0), (4, 12, 4), parent=tower)
    _t(bf, 'cube', _BRASS, (0, 11.5, 0), (3, 1.0, 3), parent=tower)
    _t(bf, 'circle', _CGLOW, (0, 9, 2.1), (3,) * 3, glow=True, parent=tower)   # clock face
    _t(bf, 'cube', _IRONP_DK, (0, 9, 2.2), (0.15, 1.2, 0.1), parent=tower)
    _t(bf, 'cube', _IRONP_DK, (0.5, 9, 2.2), (1.0, 0.15, 0.1), parent=tower)

    # Backdrop: a smoggy city skyline w/ chimneys + drifting airships + brass sun.
    _skyline(bf, 2.6, 1.8, 16, 30, [_IRONP, _IRONP_DK, _BRASS_DK, _COPPER], 8, 20, w_lo=2, w_hi=4)
    for ang in (2.0, 3.2, 4.4):  # airships
        x, z = math.cos(ang) * 32, math.sin(ang) * 32
        yy = random.uniform(14, 20)
        _t(bf, 'diamond', _COPPER, (x, GROUND_Y + yy, z), (8, 4, 4), (0, math.degrees(ang), 0))
        _t(bf, 'cube', _IRONP_DK, (x, GROUND_Y + yy - 2.5, z), (2.5, 1.0, 1.2))
    _celestial(bf, 0.8, 24, 6.0, color.rgb32(240, 200, 130), dist=56)


_CLOCKWORK = Theme(
    'Clockwork City', banner_color=_BRASS,
    ground_color=color.rgb32(96, 92, 90), horizon_color=color.rgb32(120, 100, 76),
    build=_build_clockwork,
    ambient={'style': 'embers', 'color': _CGLOW, 'count': 70, 'size': 0.06,
             'speed': (0.6, 1.4), 'sway': 0.4, 'spread': 1.4, 'alpha': 0.7},
    sky_texture='sky_default', sky_color=color.rgb32(200, 166, 124),
    sun_azimuth=45.0, sun_elevation=30.0, sun_intensity=0.8, ambient_light=0.5,
    window_bg=color.rgb32(56, 46, 34),
)


# =========================================================================== #
#  THEME: Starlit Festival  (a night flower-meadow strung with lanterns)
# =========================================================================== #
_MGRASS = color.rgb32(66, 80, 74)
_MGRASS_DK = color.rgb32(52, 66, 62)
_FLOW_A = color.rgb32(255, 180, 200)
_FLOW_B = color.rgb32(180, 200, 255)
_FLOW_C = color.rgb32(255, 230, 160)
_MLANT = color.rgb32(255, 188, 120)
_MWATER = color.rgb32(64, 92, 124)
_MMOON = color.rgb32(244, 242, 224)


def _build_festival(bf):
    # Floor: night meadow with glowing flowers, a lit path, a moonlit pond.
    _scatter(20, 0.0, 11.0, lambda x, z, a, i: _disc(bf, random.choice((_MGRASS, _MGRASS_DK)),
             random.uniform(1.2, 2.6), y=0.03, center=(x, z)))
    _scatter(30, 1.5, 10.8, lambda x, z, a, i: _disc(
        bf, random.choice((_FLOW_A, _FLOW_B, _FLOW_C)), random.uniform(0.18, 0.4),
        y=0.14, glow=True, center=(x, z)))
    for k in range(8):      # a glowing stepping-stone path
        rr = 10.5 - k * 1.2
        _disc(bf, _MLANT, 0.35, y=0.12, glow=True,
              center=(math.cos(3.8) * rr, math.sin(3.8) * rr))
    pond = (12.0, 1.5)
    _disc(bf, _MWATER, 3.0, y=0.05, center=pond, stagger=False)
    _disc(bf, color.rgba32(244, 242, 224, 160), 1.0, y=0.08, glow=True, center=pond, stagger=False)

    # Lantern poles at the rim, a wishing tree, a festival gate, flower bushes.
    for i in range(14):
        a = (i / 14) * math.tau
        x, z = math.cos(a) * 11.7, math.sin(a) * 11.7
        _box(bf, color.rgb32(80, 60, 44), x, z, 0.14, 2.4, 0.14)
        _t(bf, 'sphere', _MLANT, (x, GROUND_Y + 2.6, z), (0.4,) * 3, glow=True)
    wish = bf.node(position=(math.cos(2.4) * 14, GROUND_Y, math.sin(2.4) * 14))
    _t(bf, 'cube', color.rgb32(86, 64, 46), (0, 2.5, 0), (0.9, 5, 0.9), parent=wish)
    for _ in range(5):
        _t(bf, 'sphere', _MGRASS_DK, (random.uniform(-2, 2), 5 + random.uniform(-0.5, 1.5),
           random.uniform(-2, 2)), (random.uniform(2.5, 3.5),) * 3, parent=wish)
    for _ in range(8):
        _t(bf, 'sphere', random.choice((_MLANT, _FLOW_A, _FLOW_C)),
           (random.uniform(-2.4, 2.4), random.uniform(3.5, 6), random.uniform(-2.4, 2.4)),
           (0.3,) * 3, glow=True, parent=wish)
    gate = bf.node(position=(math.cos(0.2) * 12.5, GROUND_Y, math.sin(0.2) * 12.5),
                   rotation=(0, 100, 0))
    for sx in (-2, 2):
        _t(bf, 'cube', color.rgb32(150, 60, 60), (sx, 1.8, 0), (0.3, 3.6, 0.3), parent=gate)
    _t(bf, 'cube', color.rgb32(150, 60, 60), (0, 3.7, 0), (4.4, 0.3, 0.4), parent=gate)
    for lx in (-1.4, -0.5, 0.5, 1.4):
        _t(bf, 'sphere', _MLANT, (lx, 3.2, 0), (0.35,) * 3, glow=True, parent=gate)

    # Backdrop: a huge moon, dense stars, shooting stars, hills, rising lanterns.
    _celestial(bf, 1.6, 30, 10.0, _MMOON, dist=58)
    _scatter(90, 6, 60, lambda x, z, a, i: _t(bf, 'cube',
             random.choice((color.rgb32(230, 232, 220), color.rgb32(200, 210, 255))),
             (x, GROUND_Y + random.uniform(8, 32), z), (0.12,) * 3, glow=True))
    for ang in (2.2, 4.5):  # shooting-star streaks
        _t(bf, 'cube', color.rgb32(235, 240, 255),
           (math.cos(ang) * 34, GROUND_Y + 22, math.sin(ang) * 34), (5, 0.18, 0.18),
           (0, math.degrees(ang), 35), glow=True)
    _mountains(bf, 3.7, 1.8, 8, 28, 42, color.rgb32(40, 48, 56), 5, 10)
    _scatter(14, 10, 22, lambda x, z, a, i: _t(bf, 'sphere', _MLANT,
             (x, GROUND_Y + random.uniform(5, 14), z), (0.5,) * 3, glow=True))  # sky lanterns


_FESTIVAL = Theme(
    'Starlit Festival', banner_color=_MLANT,
    ground_color=color.rgb32(60, 74, 70), horizon_color=color.rgb32(44, 56, 56),
    build=_build_festival,
    ambient={'style': 'motes', 'color': color.rgb32(255, 210, 140), 'count': 70,
             'size': 0.08, 'speed': (0.08, 0.22), 'spread': 1.5, 'alpha': 0.9, 'flicker': 0.8},
    sky_texture=None, sky_color=color.rgb32(20, 24, 46),
    sun_azimuth=100.0, sun_elevation=45.0, sun_intensity=0.45, ambient_light=0.5,
    window_bg=color.rgb32(14, 16, 34),
)


# =========================================================================== #
#  THEME: Skyward Bastion  (the one PLATFORMER map)
#  Real, collidable one-way ledges floating over a high-altitude arena. The
#  SAME layout list drives both the physics ledges (see main._sync_platforms ->
#  physics.add_platform) and the visual slabs here, so they can never disagree.
#  Each entry: (cx, top_y, cz, hx, hz) -- top surface height + footprint half-size.
#  Tiers are spaced ~2 units of height apart so each is reachable in one jump
#  (apex ~2.75); the centre/front stays open for ground combat.
# =========================================================================== #
PLATFORMER_THEME = 'Skyward Bastion'
PLATFORMS = [
    (-6.5, 2.0,  1.0, 2.0, 2.2),   # L1  (low, reachable from the ground)
    ( 6.5, 2.0,  1.0, 2.0, 2.2),   # R1
    ( 0.0, 2.2, -6.0, 2.8, 1.6),   # back ledge (wide)
    (-3.0, 4.1, -1.5, 1.7, 1.7),   # L2  (reach via a running jump off L1)
    ( 3.0, 4.1, -1.5, 1.7, 1.7),   # R2
    ( 0.0, 6.0, -3.5, 2.2, 1.8),   # summit (reach from L2 / R2)
]

_BASTION_STONE = color.rgb32(168, 170, 182)
_BASTION_DK = color.rgb32(118, 120, 138)
_BASTION_FLOOR = color.rgb32(150, 152, 166)
_BASTION_RUNE = color.rgb32(120, 212, 255)
_BASTION_GOLD = color.rgb32(228, 200, 120)
_BASTION_CLOUD = color.rgba32(248, 250, 255, 235)


def _build_bastion(bf):
    # -- Arena floor: a great floating stone disc with a rune compass. --
    _concentric(bf, [(11.5, _BASTION_DK), (9.5, _BASTION_FLOOR), (3.2, _BASTION_DK)])
    _ring_studs(bf, 9.6, 60, _BASTION_RUNE, y=0.14, size=0.14, tall=0.05, glow=True)
    _radials(bf, 8, 0.0, 9.0, _BASTION_RUNE, y=0.16, width=0.1, glow=True)
    _disc(bf, _BASTION_GOLD, 1.6, y=0.2, glow=True, stagger=False)
    _ring_studs(bf, 11.5, 48, _BASTION_DK, y=0.16, size=0.45, tall=0.32)   # battlement rim

    # -- The collidable ledges, drawn straight from the shared PLATFORMS list so
    #    the visible slab top sits exactly at the physics support height. --
    for (cx, top, cz, hx, hz) in PLATFORMS:
        th = 0.5
        _t(bf, 'cube', _BASTION_STONE, (cx, top - th * 0.5, cz), (hx * 2, th, hz * 2))
        _t(bf, 'cube', _BASTION_DK, (cx, top - th - 0.2, cz),
           (hx * 1.7, 0.45, hz * 1.7))                                     # thicker core
        _t(bf, 'diamond', _BASTION_DK, (cx, top - th - 0.5 - hz * 0.7, cz),
           (hx * 1.5, hz * 1.8, hz * 1.5), (180, 0, 0))                    # floating-rock underside
        # Glowing rune rim around the standable top so the ledge edge reads clearly.
        _t(bf, 'cube', _BASTION_RUNE, (cx, top + 0.05, cz + hz), (hx * 2, 0.07, 0.14), glow=True)
        _t(bf, 'cube', _BASTION_RUNE, (cx, top + 0.05, cz - hz), (hx * 2, 0.07, 0.14), glow=True)
        _t(bf, 'cube', _BASTION_RUNE, (cx + hx, top + 0.05, cz), (0.14, 0.07, hz * 2), glow=True)
        _t(bf, 'cube', _BASTION_RUNE, (cx - hx, top + 0.05, cz), (0.14, 0.07, hz * 2), glow=True)
        # A small beacon crystal on a corner -- a landmark per ledge.
        _t(bf, 'diamond', _BASTION_RUNE, (cx + hx * 0.6, top + 0.6, cz - hz * 0.6),
           (0.4, 1.1, 0.4), glow=True)

    # A banner pair flanking the summit so the top of the climb feels earned.
    for sx in (-1.6, 1.6):
        _t(bf, 'cube', _BASTION_DK, (sx, 6.0 + 1.4, -3.5), (0.18, 2.8, 0.18))
        _t(bf, 'cube', _BASTION_GOLD, (sx, 6.0 + 2.2, -3.5), (0.9, 1.4, 0.1), glow=True)

    # -- Backdrop: floating isles, cloud banks, distant spires, a bright sun. --
    for ang, rr, yy, tr in ((1.1, 24, 6, 3.2), (2.4, 28, 11, 4.0), (3.7, 26, 4, 2.6),
                            (5.0, 30, 13, 3.4), (0.1, 32, 8, 3.0)):
        x, z = math.cos(ang) * rr, math.sin(ang) * rr
        _floating_island(bf, x, z, GROUND_Y + yy, tr, _BASTION_FLOOR, _BASTION_DK, _FALL)
    castle = bf.node(position=(math.cos(3.5) * 42, GROUND_Y + 10, math.sin(3.5) * 42))
    for cx2, cw, ch in ((-5, 3, 12), (0, 4, 16), (5, 3, 13)):
        _t(bf, 'cube', _BASTION_STONE, (cx2, ch * 0.5, 0), (cw, ch, cw), parent=castle)
        _t(bf, 'diamond', _BASTION_GOLD, (cx2, ch + 1.4, 0), (cw, 3.0, cw), parent=castle, glow=True)
    _cloud_ring(bf, 16, 26, 4, 3.2, _BASTION_CLOUD)
    _cloud_ring(bf, 9, 42, 12, 5.0, _BASTION_CLOUD)
    _celestial(bf, 0.8, 44, 6.0, color.rgb32(255, 250, 228), dist=58)


_BASTION = Theme(
    'Skyward Bastion', banner_color=_BASTION_RUNE,
    ground_color=color.rgb32(150, 152, 166), horizon_color=color.rgb32(178, 198, 224),
    build=_build_bastion,
    ambient={'style': 'motes', 'color': _BASTION_CLOUD, 'count': 36, 'size': 0.1,
             'speed': (0.1, 0.25), 'spread': 1.7, 'alpha': 0.5},
    sky_texture='sky_default', sky_color=color.rgb32(158, 200, 242),
    sun_azimuth=50.0, sun_elevation=54.0, sun_intensity=0.9, ambient_light=0.6,
    window_bg=color.rgb32(72, 110, 150),
)


# ----------------------------------------------------------------------------- #
#  Atmosphere: per-theme distance fog + extra particle layers (rain / mist).
#  Fog (linear start,end) fades the busy backdrop into the sky for depth while
#  the near combat area stays crisp -- it is the main "mood" lever and keeps the
#  layout legible. A couple of themes layer rain or ground mist on top of their
#  primary ambient for a stronger sense of place. Tuned to never hide a fighter:
#  fog 'start' stays >= ~14 and rain/mist run at low alpha.
# ----------------------------------------------------------------------------- #
def _rain(count=150, col=color.rgba32(190, 205, 225, 110), speed=(9.0, 15.0),
          size=0.55, wind=1.8, spread=1.5, alpha=0.5):
    return {'style': 'rain', 'color': col, 'count': count, 'size': size,
            'speed': speed, 'wind': wind, 'spread': spread, 'alpha': alpha}


def _mist(count=26, col=color.rgba32(220, 226, 232, 90), spread=1.7, alpha=0.22,
          size=5.0):
    return {'style': 'mist', 'color': col, 'count': count, 'size': size,
            'spread': spread, 'alpha': alpha}


_ATMOSPHERE = {
    'Japanese Garden':   {'fog': (color.rgb32(214, 232, 246), (28, 66))},
    'Ancient Ruins':     {'fog': (color.rgb32(226, 208, 168), (26, 64))},
    'Enchanted Forest':  {'fog': (color.rgb32(86, 120, 120), (16, 46)),
                          'add': [_mist(col=color.rgba32(150, 200, 180, 70), alpha=0.2)]},
    'Autumn Vale':       {'fog': (color.rgb32(238, 196, 142), (24, 60))},
    'Highland Cliffs':   {'fog': (color.rgb32(150, 170, 190), (15, 42)),
                          'add': [_rain(count=150, col=color.rgba32(190, 205, 225, 110), wind=2.2),
                                  _mist(count=20, col=color.rgba32(190, 200, 210, 70), alpha=0.18)]},
    'Golden Savanna':    {'fog': (color.rgb32(246, 200, 150), (26, 60))},
    'Desert Dunes':      {'fog': (color.rgb32(238, 206, 156), (24, 58))},
    'Frozen Tundra':     {'fog': (color.rgb32(214, 228, 240), (18, 48))},
    'Northern Fjord':    {'fog': (color.rgb32(120, 140, 165), (15, 40)),
                          'add': [_mist(col=color.rgba32(190, 205, 215, 80), alpha=0.22)]},
    'Molten Caldera':    {'fog': (color.rgb32(58, 22, 18), (18, 50))},
    'Jungle Ruins':      {'fog': (color.rgb32(120, 160, 140), (15, 42)),
                          'add': [_rain(count=120, col=color.rgba32(175, 205, 200, 95),
                                        speed=(8, 13), wind=1.0),
                                  _mist(col=color.rgba32(150, 190, 160, 70), alpha=0.2)]},
    'Coral Reef':        {'fog': (color.rgb32(40, 120, 140), (18, 44))},
    'Sky Citadel':       {'fog': (color.rgb32(170, 205, 240), (24, 60))},
    'Celestial Sanctum': {'fog': (color.rgb32(210, 228, 248), (22, 58))},
    'Moonlit Necropolis': {'fog': (color.rgb32(60, 64, 92), (22, 50)),
                           'add': [_mist(count=34, col=color.rgba32(178, 188, 200, 95),
                                         alpha=0.16, size=4.0)]},
    'Crystal Cavern':    {'fog': (color.rgb32(16, 15, 26), (18, 42)),
                          'add': [_mist(count=16, col=color.rgba32(120, 160, 200, 60), alpha=0.16)]},
    'Alien Exoworld':    {'fog': (color.rgb32(70, 56, 108), (18, 48))},
    'Clockwork City':    {'fog': (color.rgb32(150, 128, 100), (16, 44))},
    'Astral Void':       {'fog': None},   # space stays clear so the stars read
    'Starlit Festival':  {'fog': (color.rgb32(24, 28, 52), (20, 54)),
                          'add': [_mist(count=18, col=color.rgba32(120, 140, 180, 60), alpha=0.16)]},
    'Skyward Bastion':   {'fog': (color.rgb32(178, 198, 224), (28, 66))},
}


# ----------------------------------------------------------------------------- #
#  Volumetric fog tint (per theme) -- the colour of the player-toggleable cloud
#  layer (H). Distinct from the distance fog in _ATMOSPHERE: these are picked to
#  read as drifting haze/cloud puffs at low alpha, so dark themes use a visible
#  mid-tone rather than their near-black distance-fog colour. Each tint echoes
#  its battlefield's signature scenery. fog_color_for() falls back to a neutral
#  grey for any theme not listed here.
# ----------------------------------------------------------------------------- #
DEFAULT_FOG_COLOR = color.rgb32(200, 200, 205)

_FOG_COLOR = {
    'Japanese Garden':    color.rgb32(236, 224, 230),   # soft warm blossom-mist
    'Ancient Ruins':      color.rgb32(216, 200, 168),   # dusty sandstone haze
    'Enchanted Forest':   color.rgb32(150, 178, 150),   # pale luminous forest mist
    'Autumn Vale':        color.rgb32(228, 192, 150),   # warm amber leaf-haze
    'Highland Cliffs':    color.rgb32(188, 200, 212),   # cool grey-blue rain mist
    'Golden Savanna':     color.rgb32(236, 200, 150),   # dusty golden heat haze
    'Desert Dunes':       color.rgb32(234, 206, 158),   # pale wind-blown sand
    'Frozen Tundra':      color.rgb32(212, 228, 240),   # cold blue-white blizzard haze
    'Northern Fjord':     color.rgb32(170, 188, 200),   # cold pale sea-mist
    'Molten Caldera':     color.rgb32(92, 56, 48),      # warm dark volcanic smoke
    'Jungle Ruins':       color.rgb32(150, 186, 158),   # humid green canopy mist
    'Coral Reef':         color.rgb32(120, 186, 196),   # aqua underwater haze
    'Sky Citadel':        color.rgb32(206, 224, 244),   # bright airy sky-cloud
    'Celestial Sanctum':  color.rgb32(236, 236, 226),   # soft radiant gold-white
    'Moonlit Necropolis': color.rgb32(176, 188, 200),   # ghostly pale moonlit mist
    'Crystal Cavern':     color.rgb32(120, 150, 190),   # faint crystal-glow haze
    'Alien Exoworld':     color.rgb32(150, 124, 180),   # eerie alien violet
    'Clockwork City':     color.rgb32(190, 168, 138),   # industrial brass smog
    'Astral Void':        color.rgb32(96, 70, 150),     # arcane violet nebula haze
    'Starlit Festival':   color.rgb32(120, 130, 170),   # soft lantern-lit night blue
    'Skyward Bastion':    color.rgb32(200, 218, 240),   # airy high-altitude sky-blue
}


def fog_color_for(theme):
    """Volumetric-fog tint for a theme (matches its scenery; grey fallback)."""
    name = theme if isinstance(theme, str) else getattr(theme, 'name', None)
    return _FOG_COLOR.get(name, DEFAULT_FOG_COLOR)


# ----------------------------------------------------------------------------- #
#  Theme registry (cycle order; index 0 is the default at boot)
# ----------------------------------------------------------------------------- #
THEMES = [
    _JAPANESE, _RUINS, _FOREST, _AUTUMN, _HIGHLAND, _SAVANNA,
    _DESERT, _TUNDRA, _FJORD, _VOLCANIC, _JUNGLE_RUINS, _REEF,
    _CITADEL, _SANCTUM, _NECROPOLIS, _CAVERN, _EXOWORLD, _CLOCKWORK,
    _ASTRAL, _FESTIVAL, _BASTION,
]


def theme_count():
    return len(THEMES)


def get_theme(i):
    return THEMES[i % len(THEMES)]
