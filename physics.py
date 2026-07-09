"""
physics.py -- pure kinematic physics engine for Riposte.

Capsule bodies stood on a flat ground inside a circular arena.
Collisions resolved on the xz-plane as circles (correct for ground combat).
"""

from ursina import Vec3
import constants


def _length_xz(v: Vec3) -> float:
    return (v.x * v.x + v.z * v.z) ** 0.5


class PhysicsBody:
    def __init__(self, position=None, velocity=None,
                 radius=constants.FIGHTER_RADIUS,
                 height=constants.FIGHTER_HEIGHT,
                 mass=constants.FIGHTER_MASS,
                 is_static=False):
        self.position = Vec3(*position) if position is not None else Vec3(0, 0, 0)
        self.velocity = Vec3(*velocity) if velocity is not None else Vec3(0, 0, 0)
        self.radius = float(radius)
        self.height = float(height)
        self.mass = float(mass)
        self.on_ground = False
        self.is_static = bool(is_static)
        # When False, this body ignores world gravity for the substep (used to
        # make a fighter float mid-air while casting an airborne art). The caller
        # is responsible for managing velocity while gravity is suspended.
        self.gravity_enabled = True
        # Feet height at the START of the current substep (before integration), used
        # by one-way platform support to detect the frame the body crosses a top.
        self._prev_feet = self.position.y

    def apply_impulse(self, impulse: Vec3) -> None:
        if self.is_static or self.mass <= 0.0:
            return
        inv_m = 1.0 / self.mass
        self.velocity = Vec3(
            self.velocity.x + impulse.x * inv_m,
            self.velocity.y + impulse.y * inv_m,
            self.velocity.z + impulse.z * inv_m,
        )


class PhysicsWorld:
    def __init__(self, gravity: Vec3 = None):
        self.gravity = Vec3(*gravity) if gravity is not None else Vec3(*constants.GRAVITY)
        self.bodies: list[PhysicsBody] = []
        # One-way support platforms: each is (cx, top_y, cz, hx, hz) -- a rectangular
        # ledge whose TOP surface is at top_y, footprint [cx-hx,cx+hx] x [cz-hz,cz+hz].
        # A body lands on top when it falls onto the footprint, and passes freely
        # through from below or from the sides (classic platformer one-way ledges),
        # so there is no wall-clipping / stuck-under-ledge jank.
        self.platforms: list[tuple] = []
        self._accumulator = 0.0

    def add_body(self, body: PhysicsBody) -> PhysicsBody:
        self.bodies.append(body)
        return body

    def add_platform(self, cx, top_y, cz, hx, hz):
        self.platforms.append((float(cx), float(top_y), float(cz), float(hx), float(hz)))

    def clear_platforms(self):
        self.platforms = []

    def step(self, dt: float) -> None:
        if dt <= 0.0:
            return
        self._accumulator += dt
        substeps = 0
        fixed = constants.FIXED_DT
        while self._accumulator >= fixed and substeps < constants.MAX_SUBSTEPS:
            self._substep(fixed)
            self._accumulator -= fixed
            substeps += 1
        # If we hit the cap, drop the remaining accumulator to avoid spiral-of-death.
        if substeps >= constants.MAX_SUBSTEPS:
            self._accumulator = 0.0

    def _substep(self, dt: float) -> None:
        # Remember each body's feet height before integration so one-way platforms
        # can tell the body crossed a ledge top this substep (robust at any speed:
        # the crossing always happens within a single substep).
        for b in self.bodies:
            if not b.is_static:
                b._prev_feet = b.position.y

        for b in self.bodies:
            if b.is_static:
                continue
            # Semi-implicit Euler: integrate velocity first, then position.
            # A body with gravity suspended (floating art cast) skips the pull.
            if b.gravity_enabled:
                b.velocity = Vec3(
                    b.velocity.x + self.gravity.x * dt,
                    b.velocity.y + self.gravity.y * dt,
                    b.velocity.z + self.gravity.z * dt,
                )
            # Horizontal damping, frame-rate-independent: v_h *= (1 - damp*dt) clamped >= 0.
            damp = constants.GROUND_FRICTION if b.on_ground else constants.AIR_DAMPING
            factor = 1.0 - damp * dt
            if factor < 0.0:
                factor = 0.0
            b.velocity = Vec3(b.velocity.x * factor, b.velocity.y, b.velocity.z * factor)
            # Integrate position.
            b.position = Vec3(
                b.position.x + b.velocity.x * dt,
                b.position.y + b.velocity.y * dt,
                b.position.z + b.velocity.z * dt,
            )

        # Resolve ground collision.
        for b in self.bodies:
            if b.is_static:
                continue
            if b.position.y <= constants.GROUND_Y:
                b.position = Vec3(b.position.x, constants.GROUND_Y, b.position.z)
                if b.velocity.y < 0.0:
                    # Optionally bounce; RESTITUTION == 0 for this duel.
                    b.velocity = Vec3(b.velocity.x,
                                      -b.velocity.y * constants.RESTITUTION,
                                      b.velocity.z)
                    if abs(b.velocity.y) < 1e-4:
                        b.velocity = Vec3(b.velocity.x, 0.0, b.velocity.z)
                b.on_ground = True
            else:
                b.on_ground = False

        # Resolve circular arena wall (xz plane).
        for b in self.bodies:
            if b.is_static:
                continue
            max_dist = constants.ARENA_RADIUS - b.radius
            if max_dist < 0.0:
                max_dist = 0.0
            dist = (b.position.x * b.position.x + b.position.z * b.position.z) ** 0.5
            if dist > max_dist:
                if dist > 1e-8:
                    nx = b.position.x / dist
                    nz = b.position.z / dist
                else:
                    nx, nz = 1.0, 0.0
                b.position = Vec3(nx * max_dist, b.position.y, nz * max_dist)
                # Cancel outward velocity component (with restitution).
                v_out = b.velocity.x * nx + b.velocity.z * nz
                if v_out > 0.0:
                    bounce = (1.0 + constants.RESTITUTION) * v_out
                    b.velocity = Vec3(
                        b.velocity.x - bounce * nx,
                        b.velocity.y,
                        b.velocity.z - bounce * nz,
                    )

        # Resolve one-way platform support. Runs AFTER the wall pass so the footprint
        # test uses the wall-clamped position (a body shoved by the wall off a ledge's
        # footprint is then correctly left unsupported). A body lands on a ledge top
        # only when falling/resting (velocity.y <= 0), it crossed that top from above
        # this substep (_prev_feet >= top), and its centre is over the footprint.
        # Rising bodies pass straight through from below; walking off the edge drops.
        if self.platforms:
            for b in self.bodies:
                if b.is_static or b.velocity.y > 0.0:
                    continue
                for (cx, top, cz, hx, hz) in self.platforms:
                    if not ((cx - hx) <= b.position.x <= (cx + hx)
                            and (cz - hz) <= b.position.z <= (cz + hz)):
                        continue
                    if b._prev_feet >= top - 1e-3 and b.position.y <= top + 1e-3:
                        b.position = Vec3(b.position.x, top, b.position.z)
                        if b.velocity.y < 0.0:
                            b.velocity = Vec3(b.velocity.x, 0.0, b.velocity.z)
                        b.on_ground = True
                        break   # supported by one ledge; no need to test the rest

        # Resolve body-vs-body collisions (xz-plane circle push-out, mass-weighted).
        n = len(self.bodies)
        for i in range(n):
            a = self.bodies[i]
            for j in range(i + 1, n):
                b = self.bodies[j]
                if a.is_static and b.is_static:
                    continue
                # Skip pairs whose capsules don't overlap vertically -- otherwise a
                # body standing on a ledge and one on the floor below it would shove
                # each other horizontally (a "ghost collision" between two heights).
                # Feet sit at position.y; each capsule spans [feet, feet+height].
                if (a.position.y >= b.position.y + b.height
                        or b.position.y >= a.position.y + a.height):
                    continue
                dx = b.position.x - a.position.x
                dz = b.position.z - a.position.z
                min_dist = a.radius + b.radius
                dist_sq = dx * dx + dz * dz
                if dist_sq >= min_dist * min_dist:
                    continue
                dist = dist_sq ** 0.5
                if dist > 1e-8:
                    nx = dx / dist
                    nz = dz / dist
                else:
                    nx, nz = 1.0, 0.0
                    dist = 0.0
                overlap = (min_dist - dist) * constants.BODY_PUSH_STIFFNESS

                # Mass-weighted split. Static bodies don't move; the other takes full push.
                if a.is_static:
                    a_share, b_share = 0.0, 1.0
                elif b.is_static:
                    a_share, b_share = 1.0, 0.0
                else:
                    total = a.mass + b.mass
                    # Lighter body moves more: share inversely proportional to its mass.
                    a_share = b.mass / total
                    b_share = a.mass / total

                if not a.is_static:
                    a.position = Vec3(
                        a.position.x - nx * overlap * a_share,
                        a.position.y,
                        a.position.z - nz * overlap * a_share,
                    )
                if not b.is_static:
                    b.position = Vec3(
                        b.position.x + nx * overlap * b_share,
                        b.position.y,
                        b.position.z + nz * overlap * b_share,
                    )

                # Resolve relative velocity along the contact normal (xz only).
                rvx = b.velocity.x - a.velocity.x
                rvz = b.velocity.z - a.velocity.z
                v_rel = rvx * nx + rvz * nz
                if v_rel < 0.0:  # approaching
                    inv_a = 0.0 if a.is_static else 1.0 / a.mass
                    inv_b = 0.0 if b.is_static else 1.0 / b.mass
                    inv_sum = inv_a + inv_b
                    if inv_sum > 0.0:
                        jmag = -(1.0 + constants.RESTITUTION) * v_rel / inv_sum
                        jx = jmag * nx
                        jz = jmag * nz
                        if not a.is_static:
                            a.velocity = Vec3(
                                a.velocity.x - jx * inv_a,
                                a.velocity.y,
                                a.velocity.z - jz * inv_a,
                            )
                        if not b.is_static:
                            b.velocity = Vec3(
                                b.velocity.x + jx * inv_b,
                                b.velocity.y,
                                b.velocity.z + jz * inv_b,
                            )


if __name__ == "__main__":
    # ---- Test 1: gravity + ground settle. ----
    world = PhysicsWorld()
    body = world.add_body(PhysicsBody(position=Vec3(0, 5, 0)))
    t = 0.0
    while t < 1.5:
        world.step(1.0 / 60.0)
        t += 1.0 / 60.0
    assert abs(body.position.y - constants.GROUND_Y) < 1e-4, \
        f"Body should settle at GROUND_Y, got y={body.position.y}"
    assert body.on_ground, "Body should be on_ground after settling"
    assert abs(body.velocity.y) < 1e-3, f"y-velocity should be ~0, got {body.velocity.y}"
    print(f"PASS test 1: gravity + ground settle (y={body.position.y:.6f}, on_ground={body.on_ground})")

    # ---- Test 2: body-vs-body push-out. ----
    world = PhysicsWorld()
    r = constants.FIGHTER_RADIUS
    a = world.add_body(PhysicsBody(position=Vec3(0, 0, 0)))
    b = world.add_body(PhysicsBody(position=Vec3(0.01, 0, 0)))  # nearly coincident
    for _ in range(240):  # ~2 seconds
        world.step(1.0 / 60.0)
    dx = b.position.x - a.position.x
    dz = b.position.z - a.position.z
    dist = (dx * dx + dz * dz) ** 0.5
    min_dist = 2.0 * r
    assert dist >= min_dist - 1e-3, \
        f"Bodies should separate to >= 2*radius={min_dist}, got dist={dist}"
    print(f"PASS test 2: body-vs-body push-out (dist={dist:.4f}, min={min_dist:.4f})")

    # ---- Test 3: arena wall containment. ----
    world = PhysicsWorld()
    body = world.add_body(PhysicsBody(position=Vec3(0, 0, 0)))
    # Shove hard outward along +x.
    body.apply_impulse(Vec3(500.0, 0.0, 0.0))
    for _ in range(300):  # 5 seconds
        world.step(1.0 / 60.0)
    dist = (body.position.x ** 2 + body.position.z ** 2) ** 0.5
    max_allowed = constants.ARENA_RADIUS - body.radius + 1e-3
    assert dist <= max_allowed, \
        f"Body escaped arena: dist={dist}, max_allowed={max_allowed}"
    print(f"PASS test 3: arena wall containment (dist={dist:.4f}, max={max_allowed:.4f})")

    # ---- Test 4: one-way platform -- land on top when falling onto the footprint. ----
    world = PhysicsWorld()
    world.add_platform(0.0, 2.0, 0.0, 1.5, 1.5)
    body = world.add_body(PhysicsBody(position=Vec3(0, 5, 0)))   # dropped above the ledge
    for _ in range(180):
        world.step(1.0 / 60.0)
    assert abs(body.position.y - 2.0) < 1e-3, \
        f"Body should rest on the platform top (y=2.0), got y={body.position.y}"
    assert body.on_ground, "Body should be on_ground while resting on the platform"
    print(f"PASS test 4: platform landing (y={body.position.y:.4f}, on_ground={body.on_ground})")

    # ---- Test 5: one-way -- a rising body passes up THROUGH the ledge from below. ----
    world = PhysicsWorld()
    world.add_platform(0.0, 2.0, 0.0, 1.5, 1.5)
    body = world.add_body(PhysicsBody(position=Vec3(0, 0, 0)))
    body.on_ground = True
    body.apply_impulse(Vec3(0, 9.0, 0))   # hop up; apex ~1.8 (below the 2.0 ledge top)
    rose_through = False
    for _ in range(120):
        world.step(1.0 / 60.0)
        if 0.1 < body.position.y < 1.99 and body.velocity.y > 0:
            rose_through = True   # was climbing freely below the ledge, not snapped
    assert abs(body.position.y - 0.0) < 1e-3, \
        f"Body should fall back to the ground, got y={body.position.y}"
    assert rose_through, "Body should rise freely below the ledge (one-way), not stick to it"
    print(f"PASS test 5: one-way pass-through (settled y={body.position.y:.4f})")

    # ---- Test 6: walking off the footprint edge drops the body to the ground. ----
    world = PhysicsWorld()
    world.add_platform(0.0, 2.0, 0.0, 1.5, 1.5)
    body = world.add_body(PhysicsBody(position=Vec3(0, 2.0, 0)))
    body.on_ground = True
    body.position = Vec3(3.0, 2.0, 0)   # teleport off the footprint (x=3 > hx=1.5)
    for _ in range(180):
        world.step(1.0 / 60.0)
    assert abs(body.position.y - 0.0) < 1e-3, \
        f"Body off the footprint should fall to the ground, got y={body.position.y}"
    print(f"PASS test 6: walk-off-edge drop (y={body.position.y:.4f})")

    # ---- Test 7: bodies at different heights do NOT shove each other (no ghost
    #              collision between a ledge fighter and one on the floor below). ----
    world = PhysicsWorld()
    world.add_platform(0.0, 2.0, 0.0, 1.5, 1.5)
    top_body = world.add_body(PhysicsBody(position=Vec3(0, 2.0, 0)))    # on the ledge
    top_body.on_ground = True
    low_body = world.add_body(PhysicsBody(position=Vec3(0.2, 0, 0)))    # on the floor, ~same xz
    for _ in range(120):
        world.step(1.0 / 60.0)
    # The floor body must not have been pushed away horizontally by the ledge body.
    low_r = (low_body.position.x ** 2 + low_body.position.z ** 2) ** 0.5
    assert low_r < 0.6, f"Floor body was ghost-pushed by the ledge body: r={low_r}"
    assert abs(top_body.position.y - 2.0) < 1e-3, "Ledge body should stay on the ledge"
    print(f"PASS test 7: no cross-height ghost collision (floor r={low_r:.4f})")

    print("ALL PHYSICS SELF-TESTS PASSED")
