"""Render the real scene to a PNG so we can eyeball colours + camera framing."""
import os
from ursina import Ursina, window, Vec3, camera
from panda3d.core import Filename
import main
import constants as c

app = Ursina(title="riposte-shot", borderless=False, development_mode=False)
window.color = c.WINDOW_BG

main.app = app
main.build_environment()
main.build_hud()
main.spawn_fighters()
p, e = main.player, main.enemy
# Freeze AI so the staged poses hold.
p.is_player = True
e.is_player = True

DT = 1.0 / 60.0

# 1) Let everything settle: fighters face off, camera converges.
for _ in range(70):
    p.update_fighter(DT, e)
    e.update_fighter(DT, p)
    main.world.step(DT)
    main.update_camera(DT)

# 2) Stage readable states: player heavy windup (orange telegraph) vs enemy block (blue).
p.start_attack(c.AttackType.HEAVY)
e.start_block()
for _ in range(4):
    p.update_fighter(DT, e)
    e.update_fighter(DT, p)
    main.world.step(DT)
    main.update_camera(DT)
main.update_hud(DT)
main.update_action_cues(DT)

# 3) Render several frames, then screenshot.
tm = getattr(app, 'taskMgr', None) or globals().get('base').taskMgr
for _ in range(8):
    tm.step()

out = os.path.abspath('shot.png')
win = getattr(app, 'win', None) or globals().get('base').win
ok = win.saveScreenshot(Filename.from_os_specific(out))
print(f"player state = {p.state.name}, enemy state = {e.state.name}")
print(f"camera pos = {camera.world_position}, rot = {camera.rotation}")
print(f"screenshot saved = {ok} -> {out}")

try:
    app.userExit()
except Exception:
    pass
