"""Riposte - 3D Sword Duel : app shell, arena, camera, HUD, game loop."""

from ursina import (
    Ursina, Entity, Mesh, Text, Sky, Shader,
    DirectionalLight, AmbientLight,
    application, camera, color, time, window, held_keys, scene,
    Vec3, Vec2, lerp,
)
# NOTE: Ursina's stock lit shaders don't work for us here. lit_with_shadows_shader
# fails to link on this GL driver ("Fail to build reflection info"); and
# basic_lighting_shader IGNORES the light entirely -- it fakes shading from the
# surface normal alone, so sun direction has no effect. LIT_SHADER below is a
# minimal Lambert shader driven by our own sun_dir / sun_strength / ambient
# uniforms (pushed onto the scene root, which propagate to all lit geometry).
LIT_SHADER = Shader(language=Shader.GLSL, name='riposte_lit', vertex='''
#version 140
uniform mat4 p3d_ModelViewProjectionMatrix;
uniform mat4 p3d_ModelMatrix;
in vec4 p3d_Vertex;
in vec3 p3d_Normal;
in vec2 p3d_MultiTexCoord0;
out vec3 world_normal;
out vec2 texcoord;
// Ursina applies texture_scale/offset (e.g. the ground grid's tiling) as shader
// inputs, NOT baked UVs -- so we must replicate the stock shader's UV transform
// here or tiled textures collapse to a single stretched tile under this shader.
uniform vec2 texture_scale;
uniform vec2 texture_offset;
void main() {
    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;
    world_normal = normalize(mat3(p3d_ModelMatrix) * p3d_Normal);
    texcoord = (p3d_MultiTexCoord0 * texture_scale) + texture_offset;
}
''', fragment='''
#version 140
uniform sampler2D p3d_Texture0;
uniform vec4 p3d_ColorScale;
uniform vec3 sun_dir;       // direction the sunlight travels (points down-ish)
uniform float sun_strength; // diffuse contribution of the sun
uniform float ambient;      // flat fill so shadowed faces keep their hue
in vec3 world_normal;
in vec2 texcoord;
out vec4 fragColor;
void main() {
    vec3 N = normalize(world_normal);
    float diff = max(dot(N, -normalize(sun_dir)), 0.0);
    float shade = ambient + sun_strength * diff;
    vec4 base = texture(p3d_Texture0, texcoord) * p3d_ColorScale;
    fragColor = vec4(base.rgb * shade, base.a);
}
''', default_input={
    'sun_dir': Vec3(0.5, -0.7, 0.5),
    'sun_strength': 0.9,
    'ambient': 0.4,
    # Fallbacks when an entity hasn't set its own (1,1)/(0,0) = no tiling).
    'texture_scale': Vec2(1, 1),
    'texture_offset': Vec2(0, 0),
})
import math
import random

import physics
import fighter
import battlefields
import pose_editor as pose_editor_mod
from constants import (
    GAME_TITLE, WINDOW_BG, FULLSCREEN,
    GROUND_Y, ARENA_RADIUS, GROUND_COLOR, ARENA_RING_COLOR, SKY_COLOR,
    PLAYER_COLOR, ENEMY_COLOR,
    MAX_HP, MAX_STAMINA, CONTROLS_TEXT,
    State, AttackType, ArtType, Difficulty, DEFAULT_DIFFICULTY,
    DYNAMIC_CAMERA_KEY, ART_CAM_POSES, ART_CAM_BLEND_SPEED, ART_CAM_AIM_TAIL,
    ART_COOLDOWN_START, ART_COOLDOWN_MIN,
)


# --------------------------------------------------------------------------- #
#  Global state (filled in by build_world / reset on restart)
# --------------------------------------------------------------------------- #
app = None
world = None
player = None
enemy = None
game_over = False
parry_flash_t = 0.0
action_flash_t = 0.0
prev_player_state = None
prev_enemy_state = None

# Camera shake (guard-break impact). shake_t counts down; the jitter is applied
# to camera ROTATION (not position) at the end of update_camera, so it doesn't
# feed back into the position-follow lerp and self-corrects to zero each frame.
shake_t = 0.0
SHAKE_DURATION = 0.22    # seconds the shake lasts
SHAKE_MAGNITUDE = 2.2    # peak yaw/pitch jitter in degrees
SHAKE_ROLL_MULT = 1.6    # extra roll punch (roll sells impact the most)

# Environment / HUD handles (so restart can leave them alone)
# `battlefield` is the active cosmetic theme (ground + boundary markers + props +
# sky + ambient particles), owned by battlefields.Battlefield. It persists across
# duel restarts; only a theme cycle (C) tears it down and rebuilds. The legacy
# `ground`/`arena_ring`/`sky` globals are gone -- those entities now live inside
# the battlefield root.
battlefield = None
current_theme_index = 0
dir_light = None
amb_light = None
# Volumetric fog layer (independent, toggled with H). Persists across theme swaps;
# re-tinted on a theme cycle. Owned by battlefields.FogSystem.
fog_system = None

# ---- FX prototypes: scene lighting + bloom, toggled with L / B ---- #
lighting_on = False
bloom_on = False

# Single-pass bloom post-process: extract pixels brighter than `threshold`,
# blur them with a small Gaussian kernel, and add the glow back on top. Cheap
# enough for this tiny scene; the radius is a (2*R+1)^2 tap grid.
BLOOM_SHADER = Shader(language=Shader.GLSL, name='bloom', fragment='''
#version 430
uniform sampler2D tex;
in vec2 uv;
uniform float threshold;
uniform float intensity;
uniform vec2 bloom_step;
out vec4 color;

vec3 extract_bright(vec3 c) {
    float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
    float k = max(l - threshold, 0.0);
    return l > 1e-4 ? c * (k / l) : vec3(0.0);
}

void main() {
    vec3 base = texture(tex, uv).rgb;
    vec3 bloom = vec3(0.0);
    float total = 0.0;
    const int R = 4;
    for (int x = -R; x <= R; x++) {
        for (int y = -R; y <= R; y++) {
            float w = exp(-float(x*x + y*y) / 8.0);
            vec2 o = vec2(float(x), float(y)) * bloom_step;
            bloom += extract_bright(texture(tex, uv + o).rgb) * w;
            total += w;
        }
    }
    bloom /= total;
    color = vec4(base + bloom * intensity, 1.0);
}
''', default_input={
    'threshold': 0.55,   # luminance above which a pixel starts to glow
    'intensity': 2.0,    # how strongly the glow is added back
    'bloom_step': Vec2(0.004, 0.004),   # uv spacing between blur taps (spread)
})

# ---- Live FX tuning ---- #
# Scalar sources for the bloom + lighting knobs. The on-screen FX panel (toggle
# O) lets you drive these in-game: Up/Down select a row, Left/Right adjust it,
# 0 resets all. Sun direction is exposed as azimuth (around the horizon) +
# elevation (height) rather than a raw vector, which is far easier to dial in.
FX_DEFAULTS = {
    'BLOOM_THRESHOLD': 0.55,
    'BLOOM_INTENSITY': 2.0,
    'BLOOM_SPREAD': 0.004,
    'SUN_AZIMUTH': 45.0,
    'SUN_ELEVATION': 48.0,
    'SUN_INTENSITY': 0.9,
    'AMBIENT_BRIGHTNESS': 0.4,
}
BLOOM_THRESHOLD = FX_DEFAULTS['BLOOM_THRESHOLD']
BLOOM_INTENSITY = FX_DEFAULTS['BLOOM_INTENSITY']
BLOOM_SPREAD = FX_DEFAULTS['BLOOM_SPREAD']
SUN_AZIMUTH = FX_DEFAULTS['SUN_AZIMUTH']        # degrees around the horizon
SUN_ELEVATION = FX_DEFAULTS['SUN_ELEVATION']    # degrees above the horizon
SUN_INTENSITY = FX_DEFAULTS['SUN_INTENSITY']    # diffuse strength of the sun
AMBIENT_BRIGHTNESS = FX_DEFAULTS['AMBIENT_BRIGHTNESS']

# Panel rows: which global each tunes, its range, and hold-adjust rate (units/sec).
FX_PARAMS = [
    {'g': 'BLOOM_THRESHOLD',  'label': 'Bloom threshold', 'lo': 0.0, 'hi': 1.5,  'rate': 0.4,  'apply': 'bloom'},
    {'g': 'BLOOM_INTENSITY',  'label': 'Bloom intensity', 'lo': 0.0, 'hi': 6.0,  'rate': 2.0,  'apply': 'bloom'},
    {'g': 'BLOOM_SPREAD',     'label': 'Bloom spread',    'lo': 0.0, 'hi': 0.02, 'rate': 0.012, 'apply': 'bloom'},
    {'g': 'SUN_AZIMUTH',      'label': 'Sun azimuth',     'lo': 0.0, 'hi': 360.0, 'rate': 70.0, 'apply': 'sun', 'wrap': True},
    {'g': 'SUN_ELEVATION',    'label': 'Sun elevation',   'lo': 3.0, 'hi': 89.0, 'rate': 45.0, 'apply': 'sun'},
    {'g': 'SUN_INTENSITY',    'label': 'Sun intensity',   'lo': 0.0, 'hi': 2.0,  'rate': 0.8,  'apply': 'sun'},
    {'g': 'AMBIENT_BRIGHTNESS', 'label': 'Ambient fill',  'lo': 0.0, 'hi': 1.5,  'rate': 0.6,  'apply': 'sun'},
]
fx_panel = None
fx_selected = 0
fx_panel_visible = True

hud_root = None
player_hp_bg = None
player_hp_fill = None
player_stam_bg = None
player_stam_fill = None
enemy_hp_bg = None
enemy_hp_fill = None
enemy_stam_bg = None
enemy_stam_fill = None
controls_label = None
status_label = None
parry_label = None
action_label = None
feint_label = None
difficulty_label = None

# Arts HUD: one diamond per art key (2) per fighter, horizontal row beside HP bars.
# Each diamond has a background quad and a fill quad (vertical fill = cooldown left).
# Lists indexed to match fighter.ART_HUD_SLOTS (one representative art per key).
player_art_diamonds = []   # list of (bg, fill) Entity pairs
enemy_art_diamonds = []

# Selectable AI difficulty (toggle G). Preserved across restarts and applied to
# each freshly spawned enemy. prev_feint_ready tracks the player's feint-cooldown
# edge so we can flash "FEINT READY" the moment it recharges.
current_difficulty = DEFAULT_DIFFICULTY
prev_feint_ready = True

# Camera tuning
CAM_HEIGHT = 4.2
CAM_BACK = 6.0          # base distance behind the action...
CAM_BACK_PER_SEP = 0.7  # ...plus this * fighter separation, so both stay framed
CAM_SIDE = 2.2          # over-the-shoulder offset so the player never fully hides the enemy
CAM_LERP = 6.0          # higher = snappier follow
LOOK_HEIGHT = 1.1       # look at chest height

# Dev mode: freeze the enemy and orbit the camera around the player (toggle 'p').
# The player keeps its own facing while the camera spins -- handy for inspecting
# attack animations from every angle.
dev_freeze = False
dev_orbit_angle = 0.0
dev_status_label = None
DEV_TOGGLE_KEY = 'p'

# Pose editor (developer animation helper). Gated behind dev mode: press P, then O
# to enter/exit. See pose_editor.py. Bound to the player each spawn.
pose_editor = pose_editor_mod.PoseEditor()
POSE_EDITOR_KEY = 'o'
# On-screen controls guide for the pose editor; shown whenever dev mode is on.
dev_help_label = None

# Dynamic camera: activated by Y toggle. During ATTACK_ART A1-A3 (sub-frames 0-2)
# of any fighter, the camera moves to a per-art fixed pose. After A3, it blends
# back to the normal follow-cam. art_cam_blend_t tracks the blend-back (1=full art
# cam, 0=returned to normal). art_cam_saved_pos/rot hold the last art-cam frame
# for use during the blend.
dynamic_camera_on = False
art_cam_blend_t = 0.0          # 1.0 when in art cam pose, fades to 0 on blend-back
art_cam_saved_pos = Vec3(0, 0, 0)
art_cam_saved_rot = Vec3(0, 0, 0)
DEV_ORBIT_RADIUS = 5.0   # camera distance from the player while orbiting
DEV_ORBIT_HEIGHT = 2.5   # camera height above the player's feet
DEV_ORBIT_SPEED = 20.0   # degrees/sec the camera sweeps around the player

# Game-over cinematic: once someone wins/loses, the simulation freezes and the
# camera orbits the midpoint between the fighters (dev-mode style) so the fallen
# fighter stays framed. Slower sweep than dev mode for a cinematic feel.
gameover_orbit_angle = 0.0
GAMEOVER_ORBIT_RADIUS = 7.0    # camera distance from the fighters' midpoint
GAMEOVER_ORBIT_HEIGHT = 3.5    # camera height above the midpoint
GAMEOVER_ORBIT_SPEED = 18.0    # degrees/sec the camera sweeps around the scene

# Stagger freeze-frame cinematic: triggered ONLY by a guard-break (a heavy
# crashing through a block), NOT by a normal parry stagger. Time freezes for both
# fighters for STAGGER_FREEZE_DURATION while the camera whips around their
# midpoint (same orbit mechanism as the dev/game-over cameras) for a hit-stop
# beat, then play resumes exactly where it left off. Parry staggers are frequent
# and short, so they're deliberately excluded; the guard-break is the heavy,
# punishing moment worth the dramatic pause.
stagger_cinematic_t = 0.0
stagger_orbit_angle = 0.0
STAGGER_FREEZE_DURATION = 1.0   # seconds time stays frozen
STAGGER_ORBIT_RADIUS = 6.0      # camera distance from the midpoint
STAGGER_ORBIT_HEIGHT = 3.0      # camera height above the midpoint
STAGGER_ORBIT_SPEED = 360.0     # degrees/sec -> one full spin over a 1s freeze

# HUD bar geometry (in UI space, screen is roughly [-0.5..0.5] wide on aspect 1)
BAR_W = 0.28

# Art diamond geometry. ART_DIAMOND_BG_SIDE is the scale passed to the background
# quad (rotated 45°). When a unit quad is rotated 45°, its corners land at
# (0, ±side/2·√2) and (±side/2·√2, 0), so the visual half-diagonal is:
#   ART_DIAMOND_HALF = ART_DIAMOND_BG_SIDE * 0.5 * sqrt(2)
# The fill mesh is built in that same coordinate space (corners at (0,±s),(±s,0)).
ART_DIAMOND_BG_SIDE = 0.045          # rotated-quad scale
ART_DIAMOND_HALF = ART_DIAMOND_BG_SIDE * 0.5 * math.sqrt(2)  # ~0.0424
ART_DIAMOND_GAP = 0.03             # gap between adjacent diamonds
ART_DIAMOND_PITCH = ART_DIAMOND_BG_SIDE + ART_DIAMOND_GAP
BAR_H = 0.02
BAR_PAD = 0.03
# Player bars sit centred at the bottom of the screen (near the action) and are
# much wider than the enemy's top-corner bars so the player can track them.
PLAYER_BAR_W = 0.7


# --------------------------------------------------------------------------- #
#  Environment & HUD construction
# --------------------------------------------------------------------------- #
def build_environment():
    """Build the initial battlefield theme + the (cosmetic) light entities once."""
    global dir_light, amb_light, fog_system
    # Lighting prototype: LIT_SHADER (a Lambert shader) replaces the flat unlit
    # look when L is toggled on. The sun/ambient uniforms are pushed per lit
    # entity (see push_light_uniforms) so azimuth/elevation/intensity/ambient
    # actually shape the scene. The DirectionalLight/AmbientLight entities are
    # cosmetic-only here (Ursina's stock lit shaders don't suit this driver --
    # see the LIT_SHADER comment).
    dir_light = DirectionalLight(shadows=False)
    amb_light = AmbientLight()
    apply_theme(current_theme_index)   # builds the battlefield + seeds light uniforms
    # Volumetric fog layer (toggled with H). Independent of the per-theme
    # atmosphere; re-tinted to the active theme via apply_theme.
    theme = battlefields.get_theme(current_theme_index)
    fog_system = battlefields.FogSystem(battlefields.fog_color_for(theme))


def apply_theme(index):
    """Tear down the current battlefield and build theme `index`. Adopts the
    theme's lighting defaults into the live FX globals (so a later L toggle shows
    the themed sun), retints the window, and re-applies the current lighting/bloom
    state to the fresh geometry. Safe to call before the HUD exists (boot path)."""
    global battlefield, current_theme_index
    global SUN_AZIMUTH, SUN_ELEVATION, SUN_INTENSITY, AMBIENT_BRIGHTNESS
    current_theme_index = index % battlefields.theme_count()
    theme = battlefields.get_theme(current_theme_index)

    # Adopt the theme's lighting numbers into the live FX globals.
    SUN_AZIMUTH = theme.sun_azimuth
    SUN_ELEVATION = theme.sun_elevation
    SUN_INTENSITY = theme.sun_intensity
    AMBIENT_BRIGHTNESS = theme.ambient_light

    if battlefield is not None:
        battlefield.destroy()
    battlefield = battlefields.Battlefield(theme)
    window.color = theme.window_bg

    if lighting_on:
        battlefield.set_lit(True, LIT_SHADER)
    # AFTER binding -- shader binding re-applies default_input, so push last.
    push_light_uniforms()
    _sync_platforms()   # add/remove the platformer ledges to match the new theme
    # Re-tint the (independent) fog layer to the new theme.
    if fog_system is not None:
        fog_system.set_color(battlefields.fog_color_for(theme))
    refresh_fx_panel()
    flash_action('BATTLEFIELD: ' + theme.name, theme.banner_color)


def cycle_battlefield():
    """Advance to the next battlefield theme (C/K)."""
    apply_theme(current_theme_index + 1)


def cycle_fog():
    """Cycle the volumetric fog layer off/dense/ground/rolling (H)."""
    if fog_system is None:
        return
    mode = fog_system.cycle()
    flash_action('FOG: ' + mode.upper(), color.rgb32(200, 220, 235))


def apply_lighting(on):
    """Flip world geometry between the lit shader and the flat unlit look.
    Effects always stay unlit (handled inside Fighter.set_lit / effect systems)."""
    global lighting_on
    lighting_on = on
    if battlefield is not None:
        battlefield.set_lit(on, LIT_SHADER)
    for f in (player, enemy):
        if f is not None:
            f.set_lit(on, LIT_SHADER)
    # AFTER binding -- shader binding re-applies default_input, so push last.
    push_light_uniforms()


def set_bloom(on):
    """Enable/disable the bloom post-process on the camera."""
    global bloom_on
    bloom_on = on
    if on:
        camera.shader = BLOOM_SHADER
        apply_bloom_inputs()
    elif getattr(camera, 'filter_manager', None) is not None:
        # Only tear down when a filter pipeline actually exists. Ursina's
        # camera.shader=None setter calls filter_manager.cleanup() unguarded,
        # so disabling bloom before it was ever enabled would crash.
        camera.shader = None


def apply_bloom_inputs():
    """Push the live bloom scalars onto the camera shader.

    Guarded by bloom_on: when bloom is off, Ursina has removed the camera's
    filter_quad NodePath but left the attribute pointing at the dead node, so
    camera.set_shader_input would assert (!is_empty) and hard-crash the app.
    Values are kept in the globals and re-applied by set_bloom on re-enable."""
    if not bloom_on:
        return
    camera.set_shader_input('threshold', BLOOM_THRESHOLD)
    camera.set_shader_input('intensity', BLOOM_INTENSITY)
    camera.set_shader_input('bloom_step', Vec2(BLOOM_SPREAD, BLOOM_SPREAD))


def _sun_dir_vec():
    """The direction the sunlight travels, from azimuth + elevation (degrees)."""
    az = math.radians(SUN_AZIMUTH)
    el = math.radians(SUN_ELEVATION)
    horiz = math.cos(el)
    return Vec3(horiz * math.cos(az), -math.sin(el), horiz * math.sin(az))


def _lit_entities():
    """Every entity that carries LIT_SHADER: battlefield geometry + both fighters'
    parts. (Effects and emissive theme props stay unlit and are excluded.)"""
    ents = []
    if battlefield is not None:
        ents.extend(battlefield.lit_parts)
    for f in (player, enemy):
        if f is not None:
            ents.extend(getattr(f, 'lit_parts', []))
    return ents


def push_light_uniforms():
    """Feed the sun/ambient uniforms to every lit entity. They MUST be set
    per-entity, not on the scene root: Ursina re-applies the shader's
    default_input on each entity when the shader is bound, and that per-node
    value shadows any inherited scene-level input. Call AFTER binding LIT_SHADER.
    Harmless while geometry is unlit -- the inputs are just ignored until L."""
    d = _sun_dir_vec()
    for e in _lit_entities():
        e.set_shader_input('sun_dir', d)
        e.set_shader_input('sun_strength', SUN_INTENSITY)
        e.set_shader_input('ambient', AMBIENT_BRIGHTNESS)
    if dir_light is not None:   # keep the (cosmetic) light entity aimed too
        dir_light.look_at(d)


# update_sun / update_ambient both just re-push the shared light uniforms.
update_sun = push_light_uniforms
update_ambient = push_light_uniforms


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _apply_fx(kind):
    if kind == 'bloom':
        apply_bloom_inputs()
    elif kind == 'sun':
        update_sun()
    elif kind == 'ambient':
        update_ambient()


def adjust_fx(idx, delta):
    """Nudge the FX_PARAMS[idx] global by delta (clamped/wrapped) and apply it."""
    p = FX_PARAMS[idx]
    v = globals()[p['g']] + delta
    v = v % 360.0 if p.get('wrap') else _clamp(v, p['lo'], p['hi'])
    globals()[p['g']] = v
    _apply_fx(p['apply'])


def reset_fx():
    """Restore every FX scalar to its default and re-apply."""
    for k, v in FX_DEFAULTS.items():
        globals()[k] = v
    apply_bloom_inputs()
    update_sun()
    update_ambient()
    refresh_fx_panel()
    flash_action('FX RESET', color.rgb32(200, 235, 255))


def refresh_fx_panel():
    """Rebuild the FX panel text with the selected row highlighted."""
    if fx_panel is None:
        return
    lines = [
        '<azure>FX TUNING</azure>  (O hide)',
        'Up/Down pick  Left/Right adjust  0 reset',
    ]
    for i, p in enumerate(FX_PARAMS):
        v = globals()[p['g']]
        row = f'{p["label"]:<15}{v:8.3f}'
        lines.append(f'<yellow>> {row}</yellow>' if i == fx_selected else f'  {row}')
    lines.append(
        f'L lighting:{"ON" if lighting_on else "off"}   '
        f'B bloom:{"ON" if bloom_on else "off"}'
    )
    fx_panel.text = '\n'.join(lines)


def update_fx_tuning(dt):
    """Continuous Left/Right adjustment of the selected row while the panel shows."""
    if not fx_panel_visible:
        return
    p = FX_PARAMS[fx_selected]
    d = 0.0
    if held_keys['right arrow']:
        d += p['rate'] * dt
    if held_keys['left arrow']:
        d -= p['rate'] * dt
    if d != 0.0:
        adjust_fx(fx_selected, d)
    refresh_fx_panel()


def build_hud():
    """Create the HUD overlay (parented to camera.ui)."""
    global hud_root
    global player_hp_bg, player_hp_fill, player_stam_bg, player_stam_fill
    global enemy_hp_bg, enemy_hp_fill, enemy_stam_bg, enemy_stam_fill
    global controls_label, status_label, parry_label, action_label
    global dev_status_label, fx_panel, feint_label, difficulty_label
    global player_art_diamonds, enemy_art_diamonds, dev_help_label

    hud_root = Entity(parent=camera.ui)

    # Layout: player bars centred along the bottom (close to the action),
    # enemy bars in the top-right corner.
    # camera.ui spans roughly x in [-0.5*aspect..+0.5*aspect], y in [-0.5..0.5].
    right_x = 0.86 - BAR_W * 0.5 - BAR_PAD
    top_y = 0.5 - BAR_PAD - BAR_H * 0.5
    # Player: centred horizontally, stacked just above the controls line.
    player_x = 0.0
    player_hp_y = -0.39
    player_stam_y = player_hp_y - (BAR_H + 0.012)

    def make_bar(x, y, hp_color, w=BAR_W):
        bg = Entity(
            parent=hud_root, model='quad',
            color=color.rgba32(0, 0, 0, 180),
            scale=(w + 0.006, BAR_H + 0.006),
            position=(x, y, 0),
        )
        # Anchor fill to its left edge so we can scale-x from full to empty.
        fill_anchor = Entity(
            parent=hud_root,
            position=(x - w * 0.5, y, -0.01),
        )
        fill = Entity(
            parent=fill_anchor, model='quad',
            color=hp_color,
            scale=(w, BAR_H),
            origin=(-0.5, 0),  # left-aligned scaling
            position=(0, 0, 0),
        )
        return bg, fill

    player_hp_bg, player_hp_fill = make_bar(
        player_x, player_hp_y, color.rgb32(80, 200, 110), w=PLAYER_BAR_W
    )
    player_stam_bg, player_stam_fill = make_bar(
        player_x, player_stam_y, color.rgb32(220, 200, 80), w=PLAYER_BAR_W
    )
    enemy_hp_bg, enemy_hp_fill = make_bar(right_x, top_y, color.rgb32(220, 90, 80))
    enemy_stam_bg, enemy_stam_fill = make_bar(
        right_x, top_y - (BAR_H + 0.012), color.rgb32(220, 200, 80)
    )

    # Labels for each side.
    Text(parent=hud_root, text='PLAYER',
         position=(player_x - PLAYER_BAR_W * 0.5, player_hp_y + 0.03),
         origin=(-0.5, 0), scale=0.9, color=color.white)
    Text(parent=hud_root, text='ENEMY', position=(right_x + BAR_W * 0.5, top_y + 0.03),
         origin=(0.5, 0), scale=0.9, color=color.white)

    controls_label = Text(
        parent=hud_root, text=CONTROLS_TEXT,
        origin=(0, 0), position=(0, -0.47), scale=0.7,
        color=color.rgba32(230, 230, 230, 220),
    )

    status_label = Text(
        parent=hud_root, text='',
        origin=(0, 0), position=(0, 0.05), scale=3.4,
        color=color.rgb32(255, 230, 120),
    )
    status_label.enabled = False

    parry_label = Text(
        parent=hud_root, text='PARRY!',
        origin=(0, 0), position=(0, -0.12), scale=2.4,
        color=color.rgb32(255, 230, 120),
    )
    parry_label.enabled = False

    # Transient action cue (DODGE / BLOCK / HEAVY / STAGGER!) above the controls.
    action_label = Text(
        parent=hud_root, text='',
        origin=(0, 0), position=(0, -0.36), scale=1.6,
        color=color.white,
    )
    action_label.enabled = False

    # Feint availability indicator: green "FEINT READY" when off cooldown, dim
    # "FEINT --" while recharging. Sits just right of the player bars.
    feint_label = Text(
        parent=hud_root, text='FEINT READY',
        origin=(-0.5, 0), position=(PLAYER_BAR_W * 0.5 + 0.04, player_stam_y),
        scale=0.85, color=color.rgb32(120, 235, 140),
    )

    # Difficulty indicator (player-facing; toggle G). Under the enemy bars.
    difficulty_label = Text(
        parent=hud_root, text='DIFFICULTY: ' + current_difficulty.name,
        origin=(0.5, 0),
        position=(right_x + BAR_W * 0.5, top_y - 2 * (BAR_H + 0.012) - 0.005),
        scale=0.85, color=color.rgb32(255, 200, 120),
    )

    # Dev-mode indicator (enemy frozen + orbit camera). Shown only while active.
    dev_status_label = Text(
        parent=hud_root, text='DEV: ENEMY FROZEN - camera orbit (P)',
        origin=(0, 0), position=(0, 0.42), scale=1.0,
        color=color.rgb32(120, 240, 160),
    )
    dev_status_label.enabled = False

    # Live FX tuning panel (top-left, below the player bars). Self-documents its
    # controls; toggle with O. Driven by refresh_fx_panel().
    fx_panel = Text(
        parent=hud_root, text='',
        origin=(-0.5, 0.5), position=(-0.86, 0.30), scale=0.7,
        color=color.rgba32(220, 235, 255, 230),
    )
    fx_panel.enabled = fx_panel_visible
    refresh_fx_panel()

    # Pose-editor controls guide (right side). Shown whenever dev mode is on so the
    # keybinds are always visible while inspecting/animating. Text sourced from
    # pose_editor so it can't drift from the actual controls.
    dev_help_label = Text(
        parent=hud_root, text=pose_editor_mod.CONTROLS_GUIDE,
        origin=(0.5, 0.5), position=(0.87, 0.18), scale=0.7,
        color=color.rgba32(160, 240, 190, 230),
    )
    dev_help_label.enabled = False

    # Arts diamonds: one per art key (2), horizontal row. Each diamond has a dark
    # rotated-quad background and a custom-mesh fill rebuilt each frame with the
    # correct partially-filled-diamond geometry (vertical fill = cooldown left).
    NUM_ARTS = len(fighter.ART_HUD_SLOTS)

    def make_diamonds(cx, cy, fill_color):
        """Build NUM_ARTS diamond (bg, fill, fill_mesh) triples centred at (cx, cy)."""
        diamonds = []
        total_w = NUM_ARTS * ART_DIAMOND_PITCH - ART_DIAMOND_GAP
        start_x = cx - total_w * 0.5 + ART_DIAMOND_BG_SIDE * 0.5
        for i in range(NUM_ARTS):
            dx = start_x + i * ART_DIAMOND_PITCH
            bg = Entity(
                parent=hud_root, model='quad',
                color=color.rgba32(0, 0, 0, 180),
                scale=(ART_DIAMOND_BG_SIDE, ART_DIAMOND_BG_SIDE),
                position=(dx, cy, 0),
                rotation_z=45,
            )
            fill_mesh = Mesh(vertices=[], triangles=[], mode='triangle')
            fill = Entity(
                parent=hud_root,
                model=fill_mesh,
                color=fill_color,
                position=(dx, cy, -0.005),
            )
            diamonds.append((bg, fill, fill_mesh))
        return diamonds

    # Place the diamonds in a horizontal row to the LEFT of the player health bar.
    _art_group_w = NUM_ARTS * ART_DIAMOND_PITCH - ART_DIAMOND_GAP
    player_art_cx = (player_x - PLAYER_BAR_W * 0.5) - 0.018 - _art_group_w * 0.5
    player_art_diamonds = make_diamonds(
        player_art_cx, player_hp_y, color.rgb32(150, 200, 255)
    )

    enemy_art_y = top_y - 2 * (BAR_H + 0.012) - 0.034
    enemy_art_diamonds = make_diamonds(
        right_x, enemy_art_y, color.rgb32(255, 100, 100)
    )


# --------------------------------------------------------------------------- #
#  Spawning & resetting
# --------------------------------------------------------------------------- #
def spawn_fighters():
    global world, player, enemy
    world = physics.PhysicsWorld()
    player = fighter.Fighter(
        world, Vec3(-3, GROUND_Y, 0),
        team=0, color=PLAYER_COLOR, is_player=True,
    )
    enemy = fighter.Fighter(
        world, Vec3(3, GROUND_Y, 0),
        team=1, color=ENEMY_COLOR, is_player=False,
    )
    enemy.difficulty = current_difficulty
    # New fighters spawn unlit; re-apply lighting if the prototype is on.
    if lighting_on:
        player.set_lit(True, LIT_SHADER)
        enemy.set_lit(True, LIT_SHADER)
        push_light_uniforms()
    # Bind the pose-editor dev tool to the fresh player.
    if pose_editor is not None:
        pose_editor.bind(player)
    _sync_platforms()


def _sync_platforms():
    """Load the active theme's collidable ledges into the physics world (only the
    platformer map has any; every other theme clears them). Called whenever the
    world is (re)built or the theme is switched, so physics always matches the
    visible slabs built from the same battlefields.PLATFORMS list."""
    if world is None:
        return
    world.clear_platforms()
    if battlefields.get_theme(current_theme_index).name == battlefields.PLATFORMER_THEME:
        for (cx, top, cz, hx, hz) in battlefields.PLATFORMS:
            world.add_platform(cx, top, cz, hx, hz)


def destroy_fighters():
    """Best-effort teardown of fighters and their physics bodies."""
    global world, player, enemy
    for f in (player, enemy):
        if f is None:
            continue
        # Try to remove the body from the world.
        body = getattr(f, 'body', None)
        if body is not None and world is not None:
            try:
                world.bodies.remove(body)
            except (ValueError, AttributeError):
                pass
        # Tear down the sword trail: its segments are parented to the scene, not
        # the fighter, so destroy(f) won't reach them -- they'd orphan otherwise.
        trail = getattr(f, 'sword_trail', None)
        if trail is not None:
            trail.clear()
        sparks = getattr(f, 'sparks', None)
        if sparks is not None:
            sparks.clear()
        glints = getattr(f, 'glints', None)
        if glints is not None:
            glints.clear()
        # Tear down the Combat Arts projectile manager (its sprites are scene-parented).
        art_manager = getattr(f, 'art_manager', None)
        if art_manager is not None:
            art_manager.clear()
        # Destroy the visual entity (and let Ursina clean children).
        try:
            from ursina import destroy
            destroy(f)
        except Exception:
            pass
    player = None
    enemy = None
    world = None


def restart():
    """Reset the duel: kill old fighters/world, spawn fresh, clear banner."""
    global game_over, parry_flash_t, action_flash_t, shake_t
    global prev_player_state, prev_enemy_state, gameover_orbit_angle
    global stagger_cinematic_t, stagger_orbit_angle, prev_feint_ready
    global art_cam_blend_t
    destroy_fighters()
    spawn_fighters()
    art_cam_blend_t = 0.0
    prev_feint_ready = True
    game_over = False
    parry_flash_t = 0.0
    action_flash_t = 0.0
    shake_t = 0.0
    gameover_orbit_angle = 0.0
    stagger_cinematic_t = 0.0
    stagger_orbit_angle = 0.0
    prev_player_state = None
    prev_enemy_state = None
    status_label.enabled = False
    parry_label.enabled = False
    if action_label is not None:
        action_label.enabled = False
    # Preserve dev freeze across restarts: re-pin the new enemy if still active.
    if dev_status_label is not None:
        dev_status_label.enabled = dev_freeze
    if dev_help_label is not None:
        dev_help_label.enabled = dev_freeze
    if dev_freeze and enemy is not None and getattr(enemy, 'body', None) is not None:
        enemy.body.is_static = True


# --------------------------------------------------------------------------- #
#  Camera (third-person lock-on)
# --------------------------------------------------------------------------- #
def trigger_shake(scale=1.0):
    """Kick off a camera shake (restarts the timer). scale<1 gives a lighter punch
    (e.g. an aerial-slam landing) than a full guard-break."""
    global shake_t
    shake_t = SHAKE_DURATION * scale


def _apply_camera_shake(dt):
    """Add a decaying random rotation jitter on top of the aimed camera rotation.
    Applied last so it overrides nothing important: rotation is recomputed fresh
    every frame, so there's no drift to undo."""
    global shake_t
    if shake_t <= 0.0:
        return
    shake_t = max(0.0, shake_t - dt)
    mag = SHAKE_MAGNITUDE * (shake_t / SHAKE_DURATION)   # decays to 0 with time
    r = camera.rotation
    camera.rotation = Vec3(
        r.x + random.uniform(-mag, mag),
        r.y + random.uniform(-mag, mag),
        r.z + random.uniform(-mag, mag) * SHAKE_ROLL_MULT,
    )


def _compute_normal_cam(p, e):
    """Return (desired_pos, aim_pos) for the standard follow camera. Frames the
    midpoint and uses max(p.y, e.y) so an airborne fighter stays in shot."""
    to_enemy = Vec3(e.x - p.x, 0, e.z - p.z)
    dist = math.sqrt(to_enemy.x * to_enemy.x + to_enemy.z * to_enemy.z)
    if dist < 1e-4:
        forward = Vec3(0, 0, 1)
    else:
        forward = Vec3(to_enemy.x / dist, 0, to_enemy.z / dist)
    midp = Vec3((p.x + e.x) * 0.5, 0, (p.z + e.z) * 0.5)
    back = CAM_BACK + CAM_BACK_PER_SEP * dist
    right = Vec3(forward.z, 0, -forward.x)
    desired = Vec3(
        midp.x - forward.x * back + right.x * CAM_SIDE,
        max(p.y, e.y) + CAM_HEIGHT,
        midp.z - forward.z * back + right.z * CAM_SIDE,
    )
    mid = Vec3((p.x + e.x) * 0.5, max(p.y, e.y) + LOOK_HEIGHT, (p.z + e.z) * 0.5)
    return desired, mid


def _compute_art_cam(art_fighter, pose):
    """Return (desired_pos, aim_pos) for an art's dynamic camera pose.

    Pose fields: back, height, side, fov. Camera sits behind the art_fighter
    (relative to their facing direction) and looks at them."""
    p = Vec3(art_fighter.position)
    fwd = getattr(art_fighter, 'forward', Vec3(0, 0, 1))
    right = Vec3(fwd.z, 0, -fwd.x)
    desired = Vec3(
        p.x - fwd.x * pose['back'] + right.x * pose['side'],
        p.y + pose['height'],
        p.z - fwd.z * pose['back'] + right.z * pose['side'],
    )
    aim = Vec3(p.x, p.y + LOOK_HEIGHT, p.z)
    return desired, aim


def _set_camera_toward(pos, aim_pos):
    """Point the camera at aim_pos from its current world_position (roll forced 0).
    Using camera.look_at() let Panda3D introduce roll/tilt; this removes it."""
    cam = camera.world_position
    dx = aim_pos.x - cam.x
    dz = aim_pos.z - cam.z
    dy = cam.y - aim_pos.y
    dist_h = math.sqrt(dx * dx + dz * dz)
    yaw = math.degrees(math.atan2(dx, dz))
    pitch = math.degrees(math.atan2(dy, dist_h))
    camera.rotation = Vec3(pitch, yaw, 0)


def _active_art_fighter():
    """Return the PLAYER if they are in ATTACK_ART at sub-frame 0-2 (A1-A3), else
    None. Dynamic camera is player-only -- the AI's arts never trigger it."""
    f = player
    if (f is not None
            and f.state == State.ATTACK_ART
            and getattr(f, '_art_sub_frame', 3) < 3
            and f.current_art is not None):
        return f
    return None


def update_camera(dt):
    """Position camera: normal follow-cam, or art dynamic cam when toggled (Y)."""
    global art_cam_blend_t, art_cam_saved_pos, art_cam_saved_rot
    if player is None or enemy is None:
        return

    p = Vec3(player.position)
    e = Vec3(enemy.position)
    normal_desired, normal_aim = _compute_normal_cam(p, e)

    art_f = _active_art_fighter() if dynamic_camera_on else None

    if art_f is not None:
        # Dynamic art camera: snap to per-art pose for A1-A3.
        pose = ART_CAM_POSES[art_f.current_art]
        art_desired, art_aim = _compute_art_cam(art_f, pose)
        camera.fov = pose.get('fov', 75)
        camera.world_position = art_desired
        _set_camera_toward(art_desired, art_aim)
        art_cam_blend_t = 1.0
        art_cam_saved_pos = Vec3(art_desired)
        art_cam_saved_rot = Vec3(camera.rotation)
        _apply_camera_shake(dt)
        return

    if art_cam_blend_t > 0.0:
        # Blend back from art-cam position to normal follow-cam.
        art_cam_blend_t = max(0.0, art_cam_blend_t - ART_CAM_BLEND_SPEED * dt)
        k_blend = min(1.0, CAM_LERP * dt)
        blend_pos = Vec3(
            lerp(normal_desired.x, art_cam_saved_pos.x, art_cam_blend_t),
            lerp(normal_desired.y, art_cam_saved_pos.y, art_cam_blend_t),
            lerp(normal_desired.z, art_cam_saved_pos.z, art_cam_blend_t),
        )
        cur = camera.world_position
        camera.world_position = Vec3(
            lerp(cur.x, blend_pos.x, k_blend),
            lerp(cur.y, blend_pos.y, k_blend),
            lerp(cur.z, blend_pos.z, k_blend),
        )
        camera.fov = lerp(75, ART_CAM_POSES.get(
            getattr(player, 'current_art', ArtType.CENTIPEDE) or ArtType.CENTIPEDE,
            {}
        ).get('fov', 75), art_cam_blend_t)
        # Aim stays LOCKED ON THE PLAYER for the bulk of the return, easing to the
        # normal (midpoint) framing only in the final stretch. The old code snapped
        # the aim to the fighters' midpoint immediately, which -- with the camera
        # still near the art pose behind the player -- swung the player out of frame.
        # A plain linear blend shifts the aim too early (while the camera is still
        # close), so the player can still clip the edge. Holding full player-aim
        # until the camera has mostly pulled back (blend_t below ART_CAM_AIM_TAIL),
        # where the player/midpoint angle is small, keeps them centred the whole way.
        aim_w = min(1.0, art_cam_blend_t / ART_CAM_AIM_TAIL)
        char_aim = Vec3(p.x, p.y + LOOK_HEIGHT, p.z)
        blended_aim = Vec3(
            lerp(normal_aim.x, char_aim.x, aim_w),
            lerp(normal_aim.y, char_aim.y, aim_w),
            lerp(normal_aim.z, char_aim.z, aim_w),
        )
        _set_camera_toward(camera.world_position, blended_aim)
        _apply_camera_shake(dt)
        return

    # Standard follow-cam.
    camera.fov = 75
    k = min(1.0, CAM_LERP * dt)
    cur = camera.world_position
    camera.world_position = Vec3(
        lerp(cur.x, normal_desired.x, k),
        lerp(cur.y, normal_desired.y, k),
        lerp(cur.z, normal_desired.z, k),
    )
    _set_camera_toward(camera.world_position, normal_aim)
    _apply_camera_shake(dt)


def update_dev_camera(dt):
    """Dev mode: orbit the camera around the player while looking at them. The
    player's own facing is untouched, so the camera spins independently."""
    global dev_orbit_angle
    if player is None:
        return
    dev_orbit_angle = (dev_orbit_angle + DEV_ORBIT_SPEED * dt) % 360.0

    p = Vec3(player.position)
    rad = math.radians(dev_orbit_angle)
    cam = Vec3(
        p.x + math.sin(rad) * DEV_ORBIT_RADIUS,
        p.y + DEV_ORBIT_HEIGHT,
        p.z + math.cos(rad) * DEV_ORBIT_RADIUS,
    )
    camera.world_position = cam

    # Aim at the player's chest, roll forced to 0 (same convention as update_camera).
    dx = p.x - cam.x
    dz = p.z - cam.z
    dy = cam.y - (p.y + LOOK_HEIGHT)
    dist_h = math.sqrt(dx * dx + dz * dz)
    yaw = math.degrees(math.atan2(dx, dz))
    pitch = math.degrees(math.atan2(dy, dist_h))
    camera.rotation = Vec3(pitch, yaw, 0)


def _begin_gameover_orbit():
    """Seed the orbit angle from the current camera bearing around the fighters'
    midpoint, so the game-over pan starts where the follow-cam left off (no snap)."""
    global gameover_orbit_angle
    if player is None or enemy is None:
        gameover_orbit_angle = 0.0
        return
    mx = (player.position.x + enemy.position.x) * 0.5
    mz = (player.position.z + enemy.position.z) * 0.5
    cam = camera.world_position
    gameover_orbit_angle = math.degrees(math.atan2(cam.x - mx, cam.z - mz))


def update_gameover_camera(dt):
    """Game-over cinematic: orbit the camera around the midpoint between the two
    fighters while looking at them (roll forced to 0, same convention as the other
    cameras). The fighters are frozen, so this just sweeps the static scene."""
    global gameover_orbit_angle
    if player is None or enemy is None:
        return
    gameover_orbit_angle = (gameover_orbit_angle + GAMEOVER_ORBIT_SPEED * dt) % 360.0

    mx = (player.position.x + enemy.position.x) * 0.5
    my = max(player.position.y, enemy.position.y)
    mz = (player.position.z + enemy.position.z) * 0.5
    rad = math.radians(gameover_orbit_angle)
    cam = Vec3(
        mx + math.sin(rad) * GAMEOVER_ORBIT_RADIUS,
        my + GAMEOVER_ORBIT_HEIGHT,
        mz + math.cos(rad) * GAMEOVER_ORBIT_RADIUS,
    )
    camera.world_position = cam

    dx = mx - cam.x
    dz = mz - cam.z
    dy = cam.y - (my + LOOK_HEIGHT)
    dist_h = math.sqrt(dx * dx + dz * dz)
    yaw = math.degrees(math.atan2(dx, dz))
    pitch = math.degrees(math.atan2(dy, dist_h))
    camera.rotation = Vec3(pitch, yaw, 0)


def begin_stagger_cinematic():
    """Freeze time and seed the orbit angle from the live camera bearing around the
    fighters' midpoint, so the spin starts from the current view without a snap."""
    global stagger_cinematic_t, stagger_orbit_angle
    stagger_cinematic_t = STAGGER_FREEZE_DURATION
    # Refresh both fighters' poses NOW so the staggered fighter shows its yellow
    # tint/tilt before time freezes. The defender enters STAGGERED inside its
    # attacker's update_fighter; if that attacker is the enemy (player guard-
    # broken), the player already ran its own visual update earlier this frame, so
    # its stagger pose wouldn't render until after the freeze. Re-posing here keeps
    # the start of the spin symmetric regardless of who broke whose guard.
    for f in (player, enemy):
        if f is not None:
            f._update_sword_visual()
            f._update_body_visual()
    if player is None or enemy is None:
        stagger_orbit_angle = 0.0
        return
    mx = (player.position.x + enemy.position.x) * 0.5
    mz = (player.position.z + enemy.position.z) * 0.5
    cam = camera.world_position
    stagger_orbit_angle = math.degrees(math.atan2(cam.x - mx, cam.z - mz))


def update_stagger_camera(dt):
    """Freeze-frame cinematic: orbit the camera around the fighters' midpoint while
    looking at them (roll forced to 0, same convention as the other cameras). Time
    is frozen elsewhere, so this sweeps the held staggered pose."""
    global stagger_orbit_angle
    if player is None or enemy is None:
        return
    stagger_orbit_angle = (stagger_orbit_angle + STAGGER_ORBIT_SPEED * dt) % 360.0

    mx = (player.position.x + enemy.position.x) * 0.5
    my = max(player.position.y, enemy.position.y)
    mz = (player.position.z + enemy.position.z) * 0.5
    rad = math.radians(stagger_orbit_angle)
    cam = Vec3(
        mx + math.sin(rad) * STAGGER_ORBIT_RADIUS,
        my + STAGGER_ORBIT_HEIGHT,
        mz + math.cos(rad) * STAGGER_ORBIT_RADIUS,
    )
    camera.world_position = cam

    dx = mx - cam.x
    dz = mz - cam.z
    dy = cam.y - (my + LOOK_HEIGHT)
    dist_h = math.sqrt(dx * dx + dz * dz)
    yaw = math.degrees(math.atan2(dx, dz))
    pitch = math.degrees(math.atan2(dy, dist_h))
    camera.rotation = Vec3(pitch, yaw, 0)
    # Carry the guard-break impact shake into the start of the freeze (decays over
    # its own ~0.22s on top of the orbit) so the hit still lands with a punch.
    _apply_camera_shake(dt)


# --------------------------------------------------------------------------- #
#  HUD updates
# --------------------------------------------------------------------------- #
def flash_action(text, col):
    """Show a brief centre cue for a notable event."""
    global action_flash_t
    if action_label is None:
        return
    action_label.text = text
    action_label.color = col
    action_flash_t = 0.7


def update_action_cues(dt):
    """Watch state transitions and surface readable cues for the player."""
    global action_flash_t, prev_player_state, prev_enemy_state

    if player is not None:
        ps = player.state
        if ps != prev_player_state:
            if ps == State.DODGING:
                flash_action('DODGE', color.rgb32(120, 220, 255))
            elif ps == State.BLOCKING:
                flash_action('BLOCK', color.rgb32(120, 180, 255))
            elif ps == State.JUMPING:
                flash_action('JUMP', color.rgb32(150, 220, 180))
            elif (ps == State.ATTACK_WINDUP and player.current_attack is not None
                  and player.current_attack.atype == AttackType.HEAVY):
                flash_action('HEAVY!', color.rgb32(255, 140, 60))
            elif (ps == State.ATTACK_WINDUP and player.current_attack is not None
                  and player.current_attack.atype == AttackType.AERIAL):
                flash_action('AERIAL!', color.rgb32(255, 180, 90))
        prev_player_state = ps

    if enemy is not None:
        es = enemy.state
        if es != prev_enemy_state:
            # Enemy staggering means the player parried it or broke its block with
            # a heavy -> good feedback (clean hits no longer stagger).
            if es == State.STAGGERED:
                flash_action('STAGGER!', color.rgb32(255, 230, 120))
        prev_enemy_state = es

    if action_flash_t > 0.0:
        action_flash_t -= dt
        action_label.enabled = action_flash_t > 0.0
    else:
        action_label.enabled = False


def _diamond_fill_mesh(s, frac):
    """Build (vertices, triangles) for a diamond filled to fraction frac [0,1].

    The diamond has corners at (0,+s)=top, (+s,0)=right, (0,-s)=bottom,
    (-s,0)=left, matching a unit quad scaled to (s√2 × s√2) and rotated 45°.
    Fills from the bottom corner upward. All triangles are CCW."""
    frac = max(0.0, min(1.0, frac))
    if frac < 0.001:
        return [], []

    y_top = s * (2.0 * frac - 1.0)   # horizontal fill line, -s (empty) → +s (full)

    if frac <= 0.5:
        # Shape: triangle with apex at bottom corner, base at fill line.
        x_w = 2.0 * s * frac   # half-width at y_top
        verts = [Vec3(0, -s, 0), Vec3(x_w, y_top, 0), Vec3(-x_w, y_top, 0)]
        tris = [0, 1, 2]
    else:
        # Lower half (full triangle) + upper trapezoid up to fill line.
        x_w = 2.0 * s * (1.0 - frac)   # half-width at y_top (shrinks toward 0 at top)
        verts = [
            Vec3(0, -s, 0),        # 0  bottom corner
            Vec3(s, 0, 0),         # 1  right corner
            Vec3(-s, 0, 0),        # 2  left corner
            Vec3(x_w, y_top, 0),   # 3  upper-right fill edge
            Vec3(-x_w, y_top, 0),  # 4  upper-left fill edge
        ]
        tris = [0, 1, 2,   # lower triangle (CCW)
                2, 1, 3,   # upper trapezoid tri 1
                2, 3, 4]   # upper trapezoid tri 2
    return verts, tris


def _update_art_diamonds(fighter_obj, diamonds):
    """Rebuild the fill mesh geometry for each art diamond each frame."""
    if fighter_obj is None or not diamonds:
        return
    cooldowns = getattr(fighter_obj, 'art_cooldowns', {})
    cd_base = getattr(fighter_obj, 'art_cooldown_base', ART_COOLDOWN_START)
    max_cd = max(cd_base, ART_COOLDOWN_MIN, 0.01)
    for i, art_type in enumerate(fighter.ART_HUD_SLOTS):
        if i >= len(diamonds):
            break
        _bg, _fill, fill_mesh = diamonds[i]
        cd = cooldowns.get(art_type, 0.0)
        frac = max(0.0, min(1.0, 1.0 - cd / max_cd))
        verts, tris = _diamond_fill_mesh(ART_DIAMOND_HALF, frac)
        fill_mesh.vertices = verts
        fill_mesh.triangles = tris
        fill_mesh.generate()


def update_hud(dt):
    global parry_flash_t, prev_feint_ready

    # Feint availability indicator (+ a one-shot flash the moment it recharges).
    if player is not None and feint_label is not None:
        ready = player.feint_cooldown <= 0.0 and player.state != State.DEAD
        feint_label.text = 'FEINT READY' if ready else 'FEINT  --'
        feint_label.color = (color.rgb32(120, 235, 140) if ready
                             else color.rgba32(150, 150, 150, 160))
        if ready and not prev_feint_ready:
            flash_action('FEINT READY', color.rgb32(120, 235, 140))
        prev_feint_ready = ready

    if player is not None:
        hp_frac = max(0.0, min(1.0, player.hp / MAX_HP))
        st_frac = max(0.0, min(1.0, player.stamina / MAX_STAMINA))
        player_hp_fill.scale_x = PLAYER_BAR_W * hp_frac
        player_stam_fill.scale_x = PLAYER_BAR_W * st_frac

    if enemy is not None:
        hp_frac = max(0.0, min(1.0, enemy.hp / MAX_HP))
        st_frac = max(0.0, min(1.0, enemy.stamina / MAX_STAMINA))
        enemy_hp_fill.scale_x = BAR_W * hp_frac
        enemy_stam_fill.scale_x = BAR_W * st_frac

    # Detect a successful parry on either side to flash "PARRY!".
    if player is not None and getattr(player, 'riposte_ready', False):
        parry_flash_t = 0.6
    if enemy is not None and getattr(enemy, 'riposte_ready', False):
        parry_flash_t = 0.6

    if parry_flash_t > 0.0:
        parry_flash_t -= dt
        parry_label.enabled = parry_flash_t > 0.0
    else:
        parry_label.enabled = False

    # Art cooldown diamonds (fill drains as each art's cooldown runs).
    _update_art_diamonds(player, player_art_diamonds)
    _update_art_diamonds(enemy, enemy_art_diamonds)


def show_game_over(victory):
    status_label.text = 'VICTORY' if victory else 'DEFEAT'
    status_label.color = color.rgb32(120, 240, 140) if victory else color.rgb32(240, 90, 90)
    status_label.enabled = True


def set_difficulty(diff):
    """Apply a difficulty to the live enemy + persist it for future spawns."""
    global current_difficulty
    current_difficulty = diff
    if enemy is not None:
        enemy.difficulty = diff
    if difficulty_label is not None:
        difficulty_label.text = 'DIFFICULTY: ' + diff.name
    flash_action('DIFFICULTY: ' + diff.name, color.rgb32(255, 200, 120))


def cycle_difficulty():
    set_difficulty(Difficulty.HIGH if current_difficulty == Difficulty.MEDIUM
                   else Difficulty.MEDIUM)


def apply_occluder_fade(dt):
    """Make scenery see-through whenever it would block the view of either fighter
    (or sits right in front of the camera), so there are never opaque obstructions
    between the player and the action. Targets a chest-height point on each fighter."""
    if battlefield is None or player is None or enemy is None:
        return
    p = player.position
    e = enemy.position
    targets = ((p.x, p.y + 1.0, p.z), (e.x, e.y + 1.0, e.z))
    battlefield.fade_occluders(camera.world_position, targets, dt)


# --------------------------------------------------------------------------- #
#  Main loop / input hooks (called by Ursina automatically)
# --------------------------------------------------------------------------- #
def update():
    global game_over, stagger_cinematic_t
    dt = time.dt
    if dt <= 0:
        return

    update_fx_tuning(dt)   # live FX adjustment works in any state

    # Ambient theme particles drift on regardless of game state (during the duel,
    # the freeze-frame cinematic, and the game-over orbit) so the world stays alive.
    if battlefield is not None:
        battlefield.update(dt)
    # Fog drifts/rolls in every state too (purely cosmetic).
    if fog_system is not None:
        fog_system.update(dt)

    if world is None or player is None or enemy is None:
        return

    if pose_editor is not None and pose_editor.active:
        # Pose editor owns the player: skip ALL sim/visual updates so the frozen
        # pose holds, and let the editor re-apply (and let you nudge) the limbs.
        # Camera still orbits so you can inspect the pose from any angle.
        pose_editor.update(dt)
        update_dev_camera(dt)
        update_hud(dt)
        update_action_cues(dt)
        return

    if dev_freeze:
        # Dev mode: the enemy is frozen (skipped entirely). Only the player
        # updates and the camera orbits them. Win/lose checks are suspended.
        if not game_over:
            player.update_fighter(dt, enemy)
            player.art_manager.update(dt, enemy)
            world.step(dt)
            # Consume one-shot impact flags here too, so they don't linger and fire
            # a spurious shake/cinematic the moment dev mode is toggled back off.
            player.land_shake_event = False
            player.guard_break_event = False
            enemy.land_shake_event = False
            enemy.guard_break_event = False
        update_dev_camera(dt)
        apply_occluder_fade(dt)
        update_hud(dt)
        update_action_cues(dt)
        return

    # Stagger freeze-frame cinematic in progress: time is frozen for BOTH fighters
    # (no update_fighter / world.step), so the staggered pose is held while the
    # camera spins. Just tick the timer and orbit until it elapses, then play
    # resumes from the next frame exactly where it paused.
    if stagger_cinematic_t > 0.0:
        stagger_cinematic_t = max(0.0, stagger_cinematic_t - dt)
        update_stagger_camera(dt)
        apply_occluder_fade(dt)
        update_hud(dt)
        update_action_cues(dt)
        return

    if not game_over:
        # Order: fighters write velocity/impulses, THEN physics integrates.
        # Avoids 1-frame input lag (cost: visuals show pre-step position,
        # imperceptible vs. mushy controls in a parry-timing game).
        player.update_fighter(dt, enemy)
        enemy.update_fighter(dt, player)
        # Tick art projectile managers: each fighter's manager targets the opponent.
        player.art_manager.update(dt, enemy)
        enemy.art_manager.update(dt, player)
        world.step(dt)

        # Aerial-slam landing: a lighter impact shake (consumed one-shot). Done
        # before the guard-break check so a guard-break's full shake wins if both
        # fire on the same frame.
        if player.land_shake_event or enemy.land_shake_event:
            player.land_shake_event = False
            enemy.land_shake_event = False
            trigger_shake(0.6)

        # Guard-break (a heavy crashing through a block) is the ONLY stagger that
        # triggers the freeze-frame cinematic -- a normal parry stagger does NOT.
        # The attacker's guard_break_event flags it (the blocker is left staggered);
        # consume the one-shot flag here. Gated on both fighters still alive so a
        # lethal guard-break goes straight to the game-over orbit instead.
        if player.guard_break_event or enemy.guard_break_event:
            player.guard_break_event = False
            enemy.guard_break_event = False
            trigger_shake()
            if player.hp > 0 and enemy.hp > 0:
                begin_stagger_cinematic()
                update_stagger_camera(dt)
                apply_occluder_fade(dt)
                update_hud(dt)
                update_action_cues(dt)
                return

    if game_over:
        update_gameover_camera(dt)
    else:
        update_camera(dt)
    apply_occluder_fade(dt)
    update_hud(dt)
    update_action_cues(dt)

    if not game_over:
        if enemy.hp <= 0:
            game_over = True
            show_game_over(victory=True)
            _begin_gameover_orbit()
        elif player.hp <= 0:
            game_over = True
            show_game_over(victory=False)
            _begin_gameover_orbit()


def input(key):
    global fx_selected, fx_panel_visible, dev_freeze
    if key == 'escape':
        application.quit()
        return
    # Pose editor (dev tool) routing. While active it captures the keyboard so its
    # arrow/limb controls don't leak into combat or the FX panel. Escape above is
    # the only key that still passes through.
    if pose_editor is not None and pose_editor.active:
        if key == POSE_EDITOR_KEY:
            pose_editor.deactivate()
            return
        pose_editor.handle_key(key)
        return   # swallow everything else while editing
    if key == 'backspace':
        restart()
        return
    if key == 'g':
        cycle_difficulty()
        return
    if key == 'c' or key == 'k':
        cycle_battlefield()
        return
    if key == 'h':
        cycle_fog()
        return
    if key == 'l':
        apply_lighting(not lighting_on)
        flash_action('LIGHTING ' + ('ON' if lighting_on else 'OFF'),
                     color.rgb32(255, 230, 150))
        refresh_fx_panel()
        return
    if key == 'b':
        set_bloom(not bloom_on)
        flash_action('BLOOM ' + ('ON' if bloom_on else 'OFF'),
                     color.rgb32(150, 220, 255))
        refresh_fx_panel()
        return
    if key == 'o':
        # In dev mode, O enters the pose editor; otherwise it toggles the FX panel.
        if dev_freeze and pose_editor is not None:
            pose_editor.activate()
            return
        fx_panel_visible = not fx_panel_visible
        if fx_panel is not None:
            fx_panel.enabled = fx_panel_visible
        return
    if key == 'up arrow':
        fx_selected = (fx_selected - 1) % len(FX_PARAMS)
        refresh_fx_panel()
        return
    if key == 'down arrow':
        fx_selected = (fx_selected + 1) % len(FX_PARAMS)
        refresh_fx_panel()
        return
    if key == '0':
        reset_fx()
        return
    if key == DEV_TOGGLE_KEY:
        dev_freeze = not dev_freeze
        if dev_status_label is not None:
            dev_status_label.enabled = dev_freeze
        if dev_help_label is not None:
            dev_help_label.enabled = dev_freeze
        # Pin the enemy body so it can't drift or be bumped while frozen.
        if enemy is not None and getattr(enemy, 'body', None) is not None:
            # Don't freeze the enemy mid-jump -- a static body never updates gravity/
            # on_ground, so drop it to the ground and reset to idle first.
            if dev_freeze and not enemy.body.on_ground:
                enemy.body.position = Vec3(enemy.body.position.x, GROUND_Y,
                                          enemy.body.position.z)
                enemy.body.velocity = Vec3(0, 0, 0)
                enemy.body.on_ground = True
                enemy._airborne = False
                enemy._enter_idle()
            enemy.body.is_static = dev_freeze
        return
    if key == DYNAMIC_CAMERA_KEY:
        global dynamic_camera_on
        dynamic_camera_on = not dynamic_camera_on
        flash_action('DYN CAM ' + ('ON' if dynamic_camera_on else 'OFF'),
                     color.rgb32(200, 240, 200))
        return
    # Forward to player if fighter exposes an input hook (option B compatibility).
    if player is not None:
        player_input = getattr(player, 'input', None)
        if callable(player_input):
            try:
                player_input(key)
            except TypeError:
                pass


# --------------------------------------------------------------------------- #
#  Boot
# --------------------------------------------------------------------------- #
def main():
    global app
    app = Ursina(
        title=GAME_TITLE,
        borderless=True,
        fullscreen=FULLSCREEN,
        vsync=True,
        development_mode=False,
    )
    window.title = GAME_TITLE
    window.color = WINDOW_BG
    window.fps_counter.enabled = False
    window.exit_button.visible = False

    # Disable the default editor camera / mouse-look if present.
    try:
        from ursina.prefabs.editor_camera import EditorCamera  # noqa: F401
    except Exception:
        pass
    camera.orthographic = False
    camera.fov = 75

    build_environment()
    build_hud()
    spawn_fighters()

    app.run()


if __name__ == '__main__':
    main()
