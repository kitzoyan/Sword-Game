"""Pose editor / animation helper (developer tool).

A non-destructive, in-engine way to dial in limb poses without recompiling.
It NEVER writes to your source and NEVER changes tuning constants -- it only
nudges the live limb transforms so you can SEE a pose, then prints the numbers
for you to copy-paste into fighter.py yourself.

How it works
------------
The editor binds to the PLAYER fighter (the one dev mode inspects). When active
it takes over rendering of that fighter: it freezes a chosen animation STATE,
re-applies the captured pose every frame, and lets you push individual limbs
around with the keyboard. The fighter's own per-frame pose code is skipped while
editing (main.py stops calling player.update_fighter), so your manual tweaks
persist. Toggle the editor off and the normal animation code resumes untouched.

Poses are reproduced by driving the fighter's REAL visual code (_update_sword_
visual / _update_body_visual) for a chosen state, then capturing the resulting
transforms -- so the editor always matches the live game, with no duplicated
pose tables to keep in sync.

Activation
----------
Turn on dev mode first (P), then press O to enter/exit the pose editor.

Keys (while the editor is active)
---------------------------------
  ,  /  .     previous / next STATE  (IDLE, LIGHT, HEAVY, CHARGE, arts,
                                       PARRYING p1/p2/p3, BLOCK, DODGE, ...)
  ;  /  '     previous / next sub-frame of that state (attack stage / art A1-A6 /
                                       light swing direction)
  [  /  ]     previous / next LIMB   (arm_r, arm_l, head, torso, leg_l, leg_r,
                                       sword, root)
  Left/Right  select axis            (X -> Y -> Z)
  Up / Down   increase / decrease the selected value (hold to repeat)
  M           toggle MOVE (position) <-> ROTATE
  -  /  =     coarser / finer step size
  0           reset the current limb to its frozen (source) value
  \\           reset the WHOLE pose back to the source values for this state
  P           print all limb values for the current pose (copy-paste ready)
  O           exit the editor
"""

from ursina import Vec3, held_keys

from constants import State, AttackType, ArtType, ATTACKS


# Canonical controls guide -- shown on screen while dev mode is on (main.py) and
# printed to the console when the editor activates. Single source so they match.
CONTROLS_GUIDE = (
    "DEV MODE  -  press O for POSE EDITOR\n"
    "(freezes the player so you can pose limbs)\n"
    "\n"
    "POSE EDITOR\n"
    " , / .    prev / next STATE\n"
    " ; / '    prev / next sub-frame\n"
    " [ / ]    prev / next LIMB\n"
    " left/right   select axis (X/Y/Z)\n"
    " up/down  adjust value (hold to repeat)\n"
    " M        move (position) / rotate\n"
    " - / =    step size\n"
    " 0        reset limb     \\  reset pose\n"
    " P        print values (console)\n"
    " O        exit editor"
)


# Each editable limb: (display name, fighter entity attr, source rotation var,
# source position var, has_position). The source var names match the local
# variables in fighter._update_sword_visual so printed lines paste straight in.
LIMBS = [
    ("arm_r", "arm_r_pivot", "ra_rot", "ra_pos", True),
    ("arm_l", "arm_l_pivot", "la_rot", "la_pos", True),
    ("head",  "head_pivot",  "h_rot",  "h_pos",  True),
    ("torso", "torso_pivot", "b_rot",  "b_pos",  True),
    ("leg_l", "leg_l_pivot", "ll_rot", "ll_pos", True),
    ("leg_r", "leg_r_pivot", "rl_rot", "rl_pos", True),
    ("sword", "sword", "self.sword.rotation", "self.sword.position", True),
    ("root",  "model_root", "root_rot", None, False),
]

# Step multipliers cycled with - / = ; applied to the base pos/rot increments.
STEP_LEVELS = [0.25, 0.5, 1.0, 2.0, 4.0]
POS_BASE = 0.05      # world units per step at multiplier 1.0
ROT_BASE = 5.0       # degrees per step at multiplier 1.0
REPEAT_INTERVAL = 0.05   # seconds between repeats while an arrow is held


class _Category:
    """One selectable animation state, with one or more sub-frames."""
    def __init__(self, name, subs):
        self.name = name
        self.subs = subs   # list of (sublabel, setup_fn(fighter))


def _reset_anim_vars(f):
    """Clear the attributes the visual code branches on, so each spec starts from
    a clean slate (no leftover attack/art bleeding into the next pose)."""
    f.current_attack = None
    f.current_art = None
    f._art_sub_frame = 0
    f._dodge_spin = False
    f._dodge_spin_t = 0.0


_STAGES = [
    ("windup", State.ATTACK_WINDUP),
    ("active", State.ATTACK_ACTIVE),
    ("active2", State.ATTACK_ACTIVE2),
    ("recovery", State.ATTACK_RECOVERY),
]


def _attack_subs(atype):
    subs = []
    for label, st in _STAGES:
        def fn(f, st=st, atype=atype):
            _reset_anim_vars(f)
            f.current_attack = ATTACKS[atype]
            f.state = st
        subs.append((label, fn))
    return subs


def _light_subs():
    subs = []
    # _light_swing_left False -> s=+1 (left->right); True -> s=-1 (right->left).
    for dlabel, left in (("LR", False), ("RL", True)):
        for label, st in _STAGES:
            def fn(f, st=st, left=left):
                _reset_anim_vars(f)
                f.current_attack = ATTACKS[AttackType.LIGHT]
                f._light_swing_left = left
                f.state = st
            subs.append((dlabel + "/" + label, fn))
    return subs


def _art_subs(art_type):
    subs = []
    for sf in range(6):
        def fn(f, sf=sf, art_type=art_type):
            _reset_anim_vars(f)
            f.current_art = art_type
            f._art_sub_frame = sf
            f.state = State.ATTACK_ART
        subs.append(("A%d" % (sf + 1), fn))
    return subs


def _simple_subs(state, **attrs):
    def fn(f, state=state, attrs=attrs):
        _reset_anim_vars(f)
        for k, v in attrs.items():
            setattr(f, k, v)
        f.state = state
    return [("", fn)]


def _build_categories():
    return [
        _Category("IDLE", _simple_subs(State.IDLE)),
        _Category("LIGHT", _light_subs()),
        _Category("HEAVY", _attack_subs(AttackType.HEAVY)),
        _Category("CHARGE", _attack_subs(AttackType.CHARGE)),
        _Category("CENTIPEDE", _art_subs(ArtType.CENTIPEDE)),
        _Category("KAGURA", _art_subs(ArtType.KAGURA)),
        _Category("HARMONIC", _art_subs(ArtType.HARMONIC)),
        _Category("OVERCLOCK", _art_subs(ArtType.OVERCLOCK)),
        _Category("PARRYING(p1)", _simple_subs(State.PARRYING)),
        _Category("PARRYING2(p2)", _simple_subs(State.PARRYING2)),
        _Category("PARRYING3(p3)", _simple_subs(State.PARRYING3)),
        _Category("BLOCKING", _simple_subs(State.BLOCKING)),
        _Category("DODGE(spin)", _simple_subs(State.DODGING, _dodge_spin=True)),
        _Category("DODGE(dir)", _simple_subs(State.DODGING, _dodge_spin=False)),
        _Category("STAGGERED", _simple_subs(State.STAGGERED)),
        _Category("DEAD", _simple_subs(State.DEAD)),
    ]


def _bump(vec, axis, delta):
    x, y, z = vec.x, vec.y, vec.z
    if axis == 0:
        x += delta
    elif axis == 1:
        y += delta
    else:
        z += delta
    return Vec3(x, y, z)


def _fmt(v):
    return "Vec3(%s, %s, %s)" % (_n(v.x), _n(v.y), _n(v.z))


def _n(x):
    # Trim to 3 decimals and drop trailing zeros for tidy paste-back.
    return ("%.3f" % x).rstrip("0").rstrip(".") or "0"


class PoseEditor:
    """Bind to a fighter; toggle active to freeze + edit its pose."""

    def __init__(self, fighter=None):
        self.fighter = fighter
        self.active = False
        self.cats = _build_categories()
        self.cat_i = 0
        self.sub_i = 0
        self.limb_i = 0
        self.mode = "rot"          # "rot" or "pos"
        self.axis = 0              # 0=X 1=Y 2=Z
        self.step_i = 2            # index into STEP_LEVELS
        self.pose = {}             # name -> [pos Vec3|None, rot Vec3]
        self.orig = {}             # frozen source values for reset
        self._repeat = 0.0

    # -- binding / lifecycle ------------------------------------------------ #
    def bind(self, fighter):
        """Point at a (possibly newly spawned) fighter; drop any active session."""
        self.fighter = fighter
        self.active = False

    def activate(self):
        if self.fighter is None:
            return
        self.active = True
        self._try_match_current_state()
        self._apply_spec(capture=True)
        self._print_help()
        self._print_status()

    def deactivate(self):
        # Nothing to restore: normal update_fighter resumes next frame and
        # recomputes the visuals from the real animation code.
        self.active = False
        print("[pose-editor] off (live animation resumed)")

    def toggle(self):
        if self.active:
            self.deactivate()
        else:
            self.activate()

    # -- spec / capture ----------------------------------------------------- #
    def _cur_cat(self):
        return self.cats[self.cat_i % len(self.cats)]

    def _cur_sub(self):
        cat = self._cur_cat()
        return cat.subs[self.sub_i % len(cat.subs)]

    def _try_match_current_state(self):
        """Best-effort: start the picker on the category matching the fighter's
        current state, so toggling on near a pose lands you close to it."""
        st = getattr(self.fighter, "state", None)
        name_by_state = {
            State.IDLE: "IDLE", State.MOVING: "IDLE",
            State.PARRYING: "PARRYING(p1)", State.PARRYING2: "PARRYING2(p2)",
            State.PARRYING3: "PARRYING3(p3)", State.BLOCKING: "BLOCKING",
            State.STAGGERED: "STAGGERED", State.DEAD: "DEAD",
        }
        target = name_by_state.get(st)
        if target:
            for i, c in enumerate(self.cats):
                if c.name == target:
                    self.cat_i = i
                    self.sub_i = 0
                    return

    def _entity(self, attr):
        return getattr(self.fighter, attr)

    def _apply_spec(self, capture):
        """Configure the fighter for the selected state, run the REAL pose code,
        then (optionally) capture the result as the editable working pose."""
        cat = self._cur_cat()
        self.sub_i %= len(cat.subs)
        _sublabel, fn = cat.subs[self.sub_i]
        fn(self.fighter)
        self.fighter._update_sword_visual()
        self.fighter._update_body_visual()
        if capture:
            self._capture()

    def _capture(self):
        self.pose = {}
        self.orig = {}
        for name, attr, _rn, _pn, haspos in LIMBS:
            e = self._entity(attr)
            rot = Vec3(e.rotation.x, e.rotation.y, e.rotation.z)
            pos = Vec3(e.position.x, e.position.y, e.position.z) if haspos else None
            self.pose[name] = [pos, rot]
            self.orig[name] = [Vec3(pos) if pos is not None else None, Vec3(rot)]

    def _apply_pose(self):
        for name, attr, _rn, _pn, haspos in LIMBS:
            e = self._entity(attr)
            pos, rot = self.pose[name]
            e.rotation = Vec3(rot)
            if haspos and pos is not None:
                e.position = Vec3(pos)

    # -- editing ------------------------------------------------------------ #
    def _delta(self):
        base = POS_BASE if self.mode == "pos" else ROT_BASE
        return base * STEP_LEVELS[self.step_i]

    def _cur_limb_haspos(self):
        return LIMBS[self.limb_i][4]

    def _nudge(self, sign):
        name = LIMBS[self.limb_i][0]
        mode = self.mode
        if mode == "pos" and not self._cur_limb_haspos():
            mode = "rot"
        delta = sign * self._delta()
        pos, rot = self.pose[name]
        if mode == "pos":
            self.pose[name][0] = _bump(pos, self.axis, delta)
        else:
            self.pose[name][1] = _bump(rot, self.axis, delta)

    # -- per-frame ---------------------------------------------------------- #
    def update(self, dt):
        if not self.active or self.fighter is None:
            return
        self._apply_pose()
        up = held_keys['up arrow']
        dn = held_keys['down arrow']
        if up or dn:
            self._repeat -= dt
            if self._repeat <= 0.0:
                self._nudge(1 if up else -1)
                self._repeat = REPEAT_INTERVAL
        else:
            self._repeat = 0.0

    # -- input -------------------------------------------------------------- #
    def handle_key(self, key):
        """Return True if the key was consumed by the editor."""
        if not self.active:
            return False

        # Arrows: value nudge is continuous (see update); axis select is discrete.
        if key in ('up arrow', 'down arrow'):
            return True   # consumed; the actual nudge happens in update()
        if key == 'left arrow':
            self.axis = (self.axis - 1) % 3
            self._print_status()
            return True
        if key == 'right arrow':
            self.axis = (self.axis + 1) % 3
            self._print_status()
            return True

        # Limb selection.
        if key == '[':
            self.limb_i = (self.limb_i - 1) % len(LIMBS)
            self._fix_mode()
            self._print_status()
            return True
        if key == ']':
            self.limb_i = (self.limb_i + 1) % len(LIMBS)
            self._fix_mode()
            self._print_status()
            return True

        # Move / rotate toggle.
        if key == 'm':
            self.mode = "pos" if self.mode == "rot" else "rot"
            self._fix_mode()
            self._print_status()
            return True

        # Step size.
        if key == '-':
            self.step_i = max(0, self.step_i - 1)
            self._print_status()
            return True
        if key == '=':
            self.step_i = min(len(STEP_LEVELS) - 1, self.step_i + 1)
            self._print_status()
            return True

        # State picker.
        if key == '.':
            self.cat_i = (self.cat_i + 1) % len(self.cats)
            self.sub_i = 0
            self._apply_spec(capture=True)
            self._print_status()
            return True
        if key == ',':
            self.cat_i = (self.cat_i - 1) % len(self.cats)
            self.sub_i = 0
            self._apply_spec(capture=True)
            self._print_status()
            return True
        if key == "'":
            self.sub_i = (self.sub_i + 1) % len(self._cur_cat().subs)
            self._apply_spec(capture=True)
            self._print_status()
            return True
        if key == ';':
            self.sub_i = (self.sub_i - 1) % len(self._cur_cat().subs)
            self._apply_spec(capture=True)
            self._print_status()
            return True

        # Resets.
        if key == '0':
            name = LIMBS[self.limb_i][0]
            o_pos, o_rot = self.orig[name]
            self.pose[name] = [Vec3(o_pos) if o_pos is not None else None,
                               Vec3(o_rot)]
            print("[pose-editor] reset limb '%s' to source value" % name)
            return True
        if key == '\\':
            self._apply_spec(capture=True)
            print("[pose-editor] reset whole pose to source")
            return True

        # Output.
        if key == 'p':
            self._print_pose()
            return True

        return False

    def _fix_mode(self):
        if self.mode == "pos" and not self._cur_limb_haspos():
            self.mode = "rot"

    # -- console output ----------------------------------------------------- #
    def _label(self):
        cat = self._cur_cat()
        sub = self._cur_sub()[0]
        return cat.name + (" " + sub if sub else "")

    def _print_status(self):
        name = LIMBS[self.limb_i][0]
        axis = "XYZ"[self.axis]
        print("[pose-editor] state=%s | limb=%s | %s %s | step=%g"
              % (self._label(), name, self.mode.upper(), axis, self._delta()))

    def _print_pose(self):
        bp = getattr(self.fighter, "sword_base_pos", None)
        bpy = bp.y if bp is not None else 0.0
        print()
        print("# --- POSE: %s ---" % self._label())
        print("# raw local transforms (sword_base_pos.y = %s). If the target line"
              % _n(bpy))
        print("# uses 'bp.y + k', subtract %s from the printed Y." % _n(bpy))
        for name, _attr, rn, pn, haspos in LIMBS:
            pos, rot = self.pose[name]
            print("%s = %s" % (rn, _fmt(rot)))
            if haspos and pn is not None and pos is not None:
                print("%s = %s" % (pn, _fmt(pos)))
        print()

    def _print_help(self):
        print()
        print("=" * 60)
        print(" POSE EDITOR ON  (player frozen)")
        print(CONTROLS_GUIDE)
        print("=" * 60)
