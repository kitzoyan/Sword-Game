"""Probe actual framebuffer pixel colours to tell whether the white render is
real or just a broken offscreen capture."""
from ursina import Ursina, window, camera, Vec3
from panda3d.core import PNMImage
import builtins
import main
import constants as c

app = Ursina(title="riposte-probe", borderless=False, development_mode=False)
window.color = c.WINDOW_BG
main.app = app
main.build_environment()
main.build_hud()
main.spawn_fighters()
p, e = main.player, main.enemy
p.is_player = True
e.is_player = True

DT = 1.0 / 60.0
for _ in range(70):
    p.update_fighter(DT, e); e.update_fighter(DT, p)
    main.world.step(DT); main.update_camera(DT)

base = builtins.base
tm = app.taskMgr
win = app.win
for _ in range(10):
    tm.step()

pnm = PNMImage()
got = win.getScreenshot(pnm)
print("getScreenshot ok:", got)
if got:
    W, H = pnm.getXSize(), pnm.getYSize()
    print("size:", W, H)

    def sample(name, fx, fy):
        x = int(fx * W); y = int(fy * H)
        r = pnm.getRedVal(x, y); g = pnm.getGreenVal(x, y); b = pnm.getBlueVal(x, y)
        print(f"  {name:14s} @({fx:.2f},{fy:.2f}) -> RGB({r},{g},{b})")

    # Player (left of centre), enemy (right), ground, sky.
    sample("player torso", 0.42, 0.60)
    sample("enemy torso", 0.58, 0.60)
    sample("ground", 0.18, 0.55)
    sample("sky", 0.50, 0.12)
    sample("centre gap", 0.50, 0.55)

try:
    app.userExit()
except Exception:
    pass
