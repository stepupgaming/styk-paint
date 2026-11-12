"""Repaint a film's sets with MiniMax-H3-Image and project the paintings back onto the set (StykPainted).

  python addons/styk_painted/paint.py --scene SCENE --out DIR --frames auto|0,30,100,272 [--size 864x1536]
      [--note "wire=a 1900s telegraph office;street=a house's porch at night"] [--mode plate|ratio] [--seed 42]
      [--colour 1.0] [--tone 0.5] [--style "..."] [--vae FILE] [--views-only | --keep-views] [--at-once]
  python addons/styk_painted/paint.py --scene SCENE --out DIR --coverage [--every 10]   # what is bare or smeared
  python addons/styk_painted/paint.py --out DIR --retone [--mode ratio --tone 1 --colour 1]   # read again, no painting

SCENE is a plain Godot film scene (res://....tscn whose root is a StykFilm: capture.gd draws it) or a StykStudio
scene.json (tools/film.gd draws it). H3 runs on a ComfyUI server (COMFY_URL, and COMFY_WORKFLOW: ComfyUI's MiniMax H3
image edit template exported in API format with {prompt} in its prompt box) or, without COMFY_URL, through the Model
Stack (STACK).

1. Views. capture.gd / film.gd --views draws the frames as bare views of their places (no figures, everything sharp), --overscan
   times as wide as the shot so the paint reaches past its frame, with each view's depth, kind and keep mark and its
   camera, into DIR/views.
2. Paint, one view after another. Each view goes to H3 image edit as a material
   change: the same picture, every surface repainted in a polished painted film look. --note says what each place is
   (one note, or place=note pairs split by ;). From the second view of a place on, H3 is given the view with the
   earlier paintings already on the set (--views --paint), so it carries on the same paint instead of starting
   afresh, and the views agree (--at-once paints each bare view on its own). A painting that drew another picture (its
   shapes disagree with the view's, or with its guide's for a carried view) is repainted with the next seed, and
   dropped after three. Paintings are cached by view, prompt, seed, VAE and size in DIR/painted, so a re-run only
   paints what changed.
3. What the film shows (--mode, written to paint.json; --retone switches it without painting):
   - plate (the default): the painting itself, untouched, at PLATE size, with alpha on the set surfaces it may paint.
     The film shows it in place of the set's own colour, light and lines, projected from its camera, so the set looks
     exactly as painted; figures, their outlines and text stay Godot's, on top.
   - ratio: the painting warped back onto our view where its edges drifted (optical flow) and kept as its ratio over
     our bare view in linear light, per channel, so our set keeps its own shapes and light and takes the painting's
     colour, tone and material. --colour sets how much of its colour (1 all, 0 only its light, dark and material on
     our hues) and --tone how much of its broad light and dark. Where the painting cannot be read (ink, object edges,
     added objects, badly drifted spots) the paint around the spot is taken, and glowing lights keep their own light.
   Never painted: surfaces marked keep (text, prints, pictures: kit.mat's keep, so text is always Godot's), figures and
   cards, and the sky.
4. Write DIR/<place>/paint.json with each view's plate (PNG), ratio (EXR, rgb: ratio, a: trust), bare light (EXR, the
   light pass's luma: in plate mode the film darkens the plate where its own light falls short of it, which is where a
   figure's shadow falls), depth and camera.
   capture.gd / film.gd --paint DIR draws the film with it (a plain Godot scene shows plate mode only).
5. --coverage draws every --every-th frame and each shot's ends as bare views and reports how much of each frame's
   picture is set no painting covers (bare) and set the best painting shows at under SOFT painting pixels per film
   pixel (smeared: seen edge-on or from much further away). A frame over COVER or SOFTMAX needs a view of its own.
   It also reports seams: where the film hands over from one painting to another (as the paint pass weighs them) with
   a colour step SEAM_DE or more between them, longer than SEAMLEN of the frame's width; such a frame shows a patch.
   Paintings are kept from making patches in the first place: a view's plate only weighs in where the earlier views
   are too soft for it (NEED), every plate hands over towards its frame edge (FEATHER), and a carried painting's broad
   colour is moved onto the earlier paint's (MATCH_BLUR).
6. --frames auto picks the views itself: the judged frames are drawn as the film shows them and as --overscan wide
   candidates, and the candidate that newly covers the most of the failing frames' set, unsmeared, is taken, again
   and again, until no judged frame fails, none adds MINGAIN of a failing frame, or a place has MAXVIEWS views. Those
   are painted (the first, usually the widest, first), the plan is made again with the views that survived the shape
   check (up to ROUNDS times), and the coverage is reported. A dropped view (H3 could not read its close framing) is
   drawn again from PULLS further back and painted at PULL_MP; only the middle the close view sees is kept, as a
   narrower camera: enlarged to the plate's width and painted again by H3 with the detail a close painting would have
   (zoom(), refine()).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
import cv2  # noqa: E402
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import godot  # noqa: E402

# H3 runs either through a ComfyUI server (COMFY_URL, with COMFY_WORKFLOW: ComfyUI's MiniMax H3 image edit template
# exported in API format, its prompt box holding {prompt}) or through the Model Stack (STACK, else godot.STACK).
COMFY_URL = os.environ.get("COMFY_URL", "")
COMFY_WORKFLOW = os.environ.get("COMFY_WORKFLOW", os.path.join(HERE, "h3_edit_api.json"))
STACK = os.environ.get("STACK") or getattr(godot, "STACK", None)
LMAX = np.log(64.0)   # the ratio is kept within 1/64 .. 64
CMAX = np.log(64.0)   # and its colour (each channel over its light) within 1/64 .. 64: H3 greys a saturated teal, whose
                      # red is near nothing, by twenty times its red
INKMAX = np.log(4.0)  # our ink lines take the light around them within 1/4 .. 4, and none of its colour
TBLUR = 24.0          # px: the scale of the painting's broad light and dark, which --tone scales (its finer detail stays)
PLATE = (1536, 2720)  # the painting as it is shown (--mode plate): sharper than the 1080x1920 film, mipmapped
OVERSCAN = 1.2  # each view is painted this much wider than its shot: the set just outside the frame, which the camera
                # moves onto, is painted too (at 1/1.2 of the resolution)
COVER = 0.03   # --coverage: a frame with more of its picture than this on set no painting covers needs a view
SOFT = 0.5     # painting pixels per film pixel, across the surface's most stretched way: below it a painting is shown
               # more than twice its size there (a surface it saw edge-on or from far), which reads as smeared
SOFTMAX = 0.10  # --coverage: a frame with more of its picture than this shown smeared needs a view as well
FILM_W = 1080  # the film's width in pixels
PLAN_STEP = 4  # --frames auto judges frames on every 4th pixel
MAXVIEWS = 16  # views per place the loader uses (painted.gd MAX_VIEWS)
ROUNDS = 4     # --frames auto: plan and paint again this many times at most, after views are dropped
PULLS = (1.0, 2.0, 3.0)  # m: a dropped view is offered again from this far back along its line of sight
PULL_MP = 8.0  # megapixels a pulled-back view is painted at (H3 ~80 s, against ~52 s at its default 4): only the middle
               # the close shot sees is kept (zoom()), so it starts from as many pixels as H3 can give
REFINE = 2.5   # zoom(): that middle is enlarged at most this much to the plate's width (a tighter middle is widened)
REFINE_AGREE = 0.8  # refine(): H3's sharper redrawing must keep the enlarged middle's shapes this well (good ones
                    # measure 0.8-0.9, one that moved a paper or an edge 0.7, a new room 0.0-0.2), else the next seed,
                    # else the enlargement is kept
REFINE_PROMPT = ("Make this exact picture sharper and more detailed, as if painted at a higher resolution: the same "
                 "picture, every object, edge, colour, shadow and light exactly where it is, nothing added, moved or "
                 "removed. Where it is soft, paint the fine detail each surface would have up close: its grain, fibres, "
                 "pores, cracks, chips, stains and wear. Keep the same polished painted film look.")
                 # no place names: told what the place is, H3 paints the whole room again
NEED = (0.4, 0.75)  # a later view's paint is kept where the earlier views give its spot under 0.4 of its own painting
                    # pixels and gives way to theirs over 0.75 (blending between), so a painting only adds what the
                    # earlier ones lack: where two meet, the set shows one paint, not a patch of each
NEEDMIN = 0.03      # its weight where the earlier paint is kept (above 0: it still covers the spot, as a fallback)
FEATHER = 0.1       # share of a plate's width and height over which it hands over to the others towards its frame edge
MATCH_BLUR = 0.03   # share of a view's width: a carried painting's broad colour and light are moved onto the earlier
MATCH_MAX = np.log(1.5)  # paint's at this scale where that paint shows on its guide, at most 1.5 times either way
DETAIL_BLUR = 12.0  # px at the plate's width: refine() keeps the sharper redrawing's detail finer than this, on the
                    # enlarged painting's colour and light (the redrawing darkens and shifts the colour)
SEAM_BLUR = 6.0     # --coverage: px on a half-size plate: the paintings' colours are compared this smoothed (not grain)
SEAM_DE = 4.0       # a colour step this large (CIELAB) from one sample to the next, where one painting hands over to
                    # another, is a seam
SEAMLEN = 0.15      # a frame whose seams are longer than this share of its width shows a patch: it fails
MINGAIN = 0.01  # --frames auto: a view is painted only if it newly covers this share of some failing frame's picture
ALIKE = (0.3, 15.0)  # m, degrees: a candidate this close to a dropped view's camera, and looking the same way, would
                     # be dropped too (H3 cannot read that framing), so it is not offered
CBLUR = 2.5           # px: the colour is taken a little smoothed, so an edge a pixel off does not fringe red or blue
EPS = 0.01            # linear light added to both sides of the ratio, so near-black does not explode it
AGREE = 0.3           # a painting whose shapes agree less than this with our view drew another picture: repainted
FLIP = 60.0           # degrees: a surface whose hue the painting turns further than this got a new object (a corkboard on
                      # a bare wall); H3 greys and darkens surfaces, but keeps their hue
TRIES = 3             # seeds tried per view before it is dropped (its surfaces take the other views' paint)

# Asked as a material change on a flat-coloured frame, H3 keeps every outline within a few pixels; asked to "repaint"
# the frame, it redraws shapes (a cabinet, a sign's edge) and the projection cannot follow.
PROMPT = """Subject definition: <Picture 1> is a frame from a 3D cartoon film: {note}, drawn with flat colours and black outlines, with no people.
Preserved: the camera, framing, perspective and every object's outline, position and size are fully preserved; every sign, poster, paper and all lettering are fully preserved exactly as they are; every surface keeps its base color; lamps and glowing panels stay lit.
Changed: every surface gains {style}.{carry} Same soft lighting, black outlines stay, no people, no new objects, no extra text."""

STYLE = ("rich hand-painted material detail in a polished 3D animated film style: visible wood grain, knots and worn edges "
         "on wood, faint plaster texture and soft grime on walls, scuffs and dents on metal, paper and fabric texture, fine "
         "seams and wear on tiles and floors")

CARRY = (" Surfaces that already look painted keep exactly the paint they have; the plain flat surfaces are painted to "
         "match them.")


def tscn(scene):
    """a plain Godot film scene (StykFilm, drawn by capture.gd), not a StykStudio scene.json (tools/film.gd)"""
    return scene.endswith(".tscn") or scene.endswith(".scn")


def views(scene, vdir, frames, size, paint_dir=None, overscan=1.0, pull=0.0):
    run = ["-s", "res://addons/styk_painted/capture.gd", "--", "--film", scene] if tscn(scene) else         ["-s", "res://tools/film.gd", "--", "--scene", scene]
    args = ["--fixed-fps", "30", "--disable-vsync", *run, "--out", vdir, "--frames", frames, "--size", size,
            "--views", "--overscan", "%g" % overscan, "--pull", "%g" % pull]
    if paint_dir:
        args += ["--paint", paint_dir]
    code = godot.run(args)
    if code != 0:
        sys.exit("drawing the views failed (%d)" % code)
    return vdir


def paint(view_png, prompt, seed, vae, cache, mp=None):
    """the H3 painting of one view, from the cache when the view, prompt, seed, VAE and size (mp megapixels, else H3's
    default) are unchanged"""
    h = hashlib.sha1()
    h.update(open(view_png, "rb").read())
    h.update(prompt.encode())
    h.update(str(seed).encode())
    if vae:
        h.update(vae.encode())
    if mp:
        h.update(("mp%g" % mp).encode())
    dest = os.path.join(cache, h.hexdigest()[:16] + ".png")
    if os.path.exists(dest):
        return dest
    if COMFY_URL:
        comfy(view_png, prompt, seed, vae, mp, dest)
        return dest
    if not STACK:
        sys.exit("no painter: set COMFY_URL (and COMFY_WORKFLOW) to a ComfyUI server with MiniMax-H3-Image")
    tmp = os.path.join(cache, "run-" + h.hexdigest()[:16])
    os.makedirs(tmp, exist_ok=True)
    env = dict(os.environ, GPU_RUN_NEED_GB=os.environ.get("GPU_RUN_NEED_GB", "10"))
    lock = [sys.executable, godot.GPU_RUN] if getattr(godot, "GPU_RUN", None) else []   # a GPU lock, where there is one
    cmd = [*lock, sys.executable, "run.py", "run", "h3-image-edit", "prompt=" + prompt,
           "image=" + os.path.abspath(view_png), "seed=%d" % seed, "--out", tmp, "--json"]
    if vae:
        cmd.insert(-3, "vae=" + vae)
    if mp:
        cmd.insert(-3, "megapixels=%g" % mp)
    r = subprocess.run(cmd, cwd=STACK, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    pngs = sorted(f for f in os.listdir(tmp) if f.lower().endswith(".png"))
    if r.returncode != 0 or not pngs:
        sys.stderr.write(r.stdout[-3000:] + r.stderr[-3000:])
        sys.exit("H3 did not paint %s" % view_png)
    shutil.move(os.path.join(tmp, pngs[-1]), dest)
    shutil.rmtree(tmp, ignore_errors=True)
    return dest


def comfy(view_png, prompt, seed, vae, mp, dest):
    """paint through a ComfyUI server: COMFY_WORKFLOW (the H3 image edit template in API format) with {prompt} in its
    prompt box, the view as its LoadImage, seed on every sampler, mp on its megapixel resize and vae on its VAELoader"""
    import time
    import urllib.parse
    import urllib.request
    import uuid
    url = COMFY_URL.rstrip("/")
    if not os.path.exists(COMFY_WORKFLOW):
        sys.exit("no ComfyUI workflow at %s (COMFY_WORKFLOW): export the MiniMax H3 image edit template in API format"
                 % COMFY_WORKFLOW)
    wf = json.load(open(COMFY_WORKFLOW, encoding="utf-8"))
    name = "styk-%s.png" % uuid.uuid4().hex[:12]
    b = uuid.uuid4().hex
    head = '--%s\r\nContent-Disposition: form-data; name="image"; filename="%s"\r\nContent-Type: image/png\r\n\r\n'
    body = (head % (b, name)).encode() + open(view_png, "rb").read() + ("\r\n--%s--\r\n" % b).encode()
    req = urllib.request.Request(url + "/upload/image", body, {"Content-Type": "multipart/form-data; boundary=" + b})
    name = json.load(urllib.request.urlopen(req, timeout=120))["name"]
    said = False
    for node in wf.values():
        ins = node.get("inputs", {})
        for k, v in list(ins.items()):
            if isinstance(v, str) and "{prompt}" in v:
                ins[k] = v.replace("{prompt}", prompt)
                said = True
        if node.get("class_type") == "LoadImage":
            ins["image"] = name
        for k in ("seed", "noise_seed"):
            if k in ins and not isinstance(ins[k], list):
                ins[k] = seed
        if mp and "resize_type.megapixels" in ins and not isinstance(ins["resize_type.megapixels"], list):
            ins["resize_type.megapixels"] = mp
        if vae and node.get("class_type") == "VAELoader":
            ins["vae_name"] = vae
    if not said:
        sys.exit("%s has no {prompt} in its prompt box" % COMFY_WORKFLOW)
    req = urllib.request.Request(url + "/prompt", json.dumps({"prompt": wf, "client_id": uuid.uuid4().hex}).encode(),
                                 {"Content-Type": "application/json"})
    pid = json.load(urllib.request.urlopen(req, timeout=120))["prompt_id"]
    for _ in range(3600):
        hist = json.load(urllib.request.urlopen("%s/history/%s" % (url, pid), timeout=60))
        if pid in hist:
            break
        time.sleep(1.0)
    else:
        sys.exit("ComfyUI did not finish painting %s" % view_png)
    for out in hist[pid].get("outputs", {}).values():
        for im in out.get("images", []):
            if im.get("type") == "output":
                q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                            "type": "output"})
                with open(dest, "wb") as f:
                    f.write(urllib.request.urlopen("%s/view?%s" % (url, q), timeout=120).read())
                return
    sys.exit("ComfyUI painted nothing for %s: %s" % (view_png, json.dumps(hist[pid].get("status", {}))[:500]))


def lin(img8):
    x = img8.astype(np.float32) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def luma(x):   # BGR
    return 0.0722 * x[..., 0] + 0.7152 * x[..., 1] + 0.2126 * x[..., 2]


def fill(v, w):
    """v (HxWxC) where w (HxW, 0..1) trusts it, and elsewhere the trusted values around it, nearest first"""
    est = None
    for s in (64, 32, 16, 8, 4, 2):   # coarse to fine: each finer scale takes over where it has enough trusted pixels
        d = cv2.GaussianBlur(w, (0, 0), s)
        n = cv2.GaussianBlur(v * w[..., None], (0, 0), s).reshape(v.shape) / np.maximum(d, 1e-6)[..., None]
        a = np.clip(d / 0.3, 0.0, 1.0)[..., None]
        est = n if est is None else est * (1.0 - a) + n * a
    return v * w[..., None] + est * (1.0 - w[..., None])


def regions(depth):
    """(paintable set HxW bool, keep HxW bool, inverse depth) from a view's data pass"""
    invz = depth[..., 2]   # cv2 reads the data pass as BGRA: r (inverse depth) is index 2, g (kind / 4) index 1
    k4 = depth[..., 1] * 4.0
    kind = np.round(k4)
    keep = (k4 - kind) > 0.2          # text, prints, pictures (data.gdshader's keep)
    return (kind == 0) & (invz > 0) & ~keep, keep, invz


def agreement(view, painting, depth):
    """how well the painting keeps our view's shapes, over the paintable set: the correlation of their brightness
    gradients, 1 the same shapes, about 0 another picture. Good paintings measure 0.45-0.6, ones that redrew the frame
    (a close-up turned into a wide shot of the room) 0.0-0.1."""
    H, W = view.shape[:2]
    pt = cv2.resize(painting, (W, H), interpolation=cv2.INTER_AREA)
    m = cv2.erode(regions(depth)[0].astype(np.float32), np.ones((9, 9), np.uint8))
    if m.sum() < 1:
        return 1.0

    def grad(img):
        g = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 3)
        return cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
    ax, ay = grad(view)
    bx, by = grad(pt)
    num = ((ax * bx + ay * by) * m).sum()
    den = np.sqrt(((ax * ax + ay * ay) * m).sum() * ((bx * bx + by * by) * m).sum())
    return float(num / max(den, 1e-6))


def repaint(view, guide, painting, depth, colour, tone):
    """(ratio HxWx3, trust HxW, aligned painting, stats): the painting over our bare view. guide is what H3 was given
    (the bare view, or the view with the earlier paintings on it), the same picture as view, used to align."""
    H, W = view.shape[:2]
    pt = cv2.resize(painting, (W, H), interpolation=cv2.INTER_AREA)
    g0 = cv2.cvtColor(guide, cv2.COLOR_BGR2GRAY)
    g1 = cv2.cvtColor(pt, cv2.COLOR_BGR2GRAY)
    # where each of our pixels went in the painting; smoothed, since the texture the painting added is not motion
    flow = cv2.calcOpticalFlowFarneback(g0, g1, None, 0.5, 5, 31, 5, 7, 1.5, cv2.OPTFLOW_FARNEBACK_GAUSSIAN)
    flow = cv2.GaussianBlur(flow, (0, 0), 6)
    gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    aligned = cv2.remap(pt, gx + flow[..., 0], gy + flow[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

    lv = lin(view)
    la = lin(aligned)
    region, keep, invz = regions(depth)

    # where the painting can be read directly; everywhere else in the region takes the paint around it
    lum = luma(lv)
    ok = region & (lum > 0.004) & (lum < 0.97)          # our ink, and glowing lights
    e = cv2.Canny(cv2.cvtColor(view, cv2.COLOR_BGR2GRAY), 40, 110)
    ok &= ~(cv2.dilate(e, np.ones((5, 5), np.uint8)) > 0)   # object edges: the painting draws them a little elsewhere
    zl = cv2.Laplacian(invz, cv2.CV_32F, ksize=3)
    ok &= ~(cv2.dilate((np.abs(zl) > 0.02 * np.maximum(invz, 1e-3) + 0.002).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0)
    mag = np.hypot(flow[..., 0], flow[..., 1])
    ok &= mag < 10.0
    # objects the painter added: the hue of what it was given turned around (both coloured enough to have a hue)
    lg, lp = [cv2.cvtColor(cv2.GaussianBlur(x.astype(np.float32) / 255.0, (0, 0), 8), cv2.COLOR_BGR2LAB)
              for x in (guide, aligned)]
    dh = np.abs((np.degrees(np.arctan2(lp[..., 2], lp[..., 1]) - np.arctan2(lg[..., 2], lg[..., 1])) + 180) % 360 - 180)
    added = region & (np.hypot(lg[..., 1], lg[..., 2]) > 12) & (np.hypot(lp[..., 1], lp[..., 2]) > 8) & (dh > FLIP)
    added = cv2.dilate(added.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    ok &= ~added
    sure = cv2.erode(ok.astype(np.float32), np.ones((3, 3), np.uint8))
    sure = cv2.GaussianBlur(sure, (0, 0), 1.5) * ok

    # the painting's own colour where it is read, and where it is not, the painting's colour around the spot (not the
    # ratio around it: over a colour boundary a ratio carried across turns into a red or blue halo); then its ratio over
    # ours, light and dark at full detail, colour (each channel over the light) a little smoothed, so an edge a pixel
    # off does not fringe
    lp = fill(np.log(la + EPS), sure)
    lr = lp - np.log(lv + EPS)
    ll = np.log(luma(np.exp(lp) - EPS) + EPS) - np.log(lum + EPS)
    ch = np.clip(lr - ll[..., None], -CMAX, CMAX)
    ll = np.clip(ll, -LMAX, LMAX)
    ch = cv2.GaussianBlur(ch, (0, 0), CBLUR)
    # our ink keeps its colour and takes only a bounded share of the light around it
    ink = lum <= 0.004
    ll[ink] = np.clip(ll[ink], -INKMAX, INKMAX)
    ch[ink] = 0.0
    # the painting's broad light and dark (H3 darkens and greys whole surfaces) scaled by tone; its material and shading
    # finer than TBLUR always kept
    w = region.astype(np.float32)
    broad = cv2.GaussianBlur(ll * w, (0, 0), TBLUR) / np.maximum(cv2.GaussianBlur(w, (0, 0), TBLUR), 1e-3)
    ll = ll - (1.0 - tone) * broad
    lr = np.clip(ll[..., None] + colour * ch, -LMAX, LMAX)
    # glowing lights keep their own light: the paint around a lamp is no guide to the lamp
    glow = cv2.dilate((lum >= 0.97).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    lr[glow] = 0.0
    ratio = np.exp(lr) * region[..., None]
    ratio[~region] = 1.0
    # a view's own reading of the painting beats paint carried over from around it, where another view has the real one
    trust = region * (0.3 + 0.7 * sure)
    trust[added & region] = 0.1       # added objects: the paint around them, below any other view's reading
    stats = {"read": float(ok[region].mean()) if region.any() else 0.0,
             "flow_p90": float(np.percentile(mag[ok], 90)) if ok.any() else 0.0,
             "keep": float(keep.mean()), "added": float(added[region].mean()) if region.any() else 0.0}
    return ratio.astype(np.float32), trust.astype(np.float32), aligned, stats


def bare_light(vdir, v, pdir, name):
    """write the view's light alone (the light pass's luma, linear) as name.light.exr: in plate mode the film darkens
    the plate where its own light falls short of this, which is where a figure's shadow falls. False for views drawn
    before film.gd wrote the light."""
    if not v.get("light") or not os.path.exists(os.path.join(vdir, v["light"])):
        return False
    light = cv2.imread(os.path.join(vdir, v["light"]), cv2.IMREAD_UNCHANGED)
    cv2.imwrite(os.path.join(pdir, name + ".light.exr"), luma(light[..., :3]).astype(np.float32))
    return True


def plate(painting, depth, want=None):
    """the painting itself, as the film shows it in --mode plate: untouched (nothing warped onto our meshes), at PLATE
    size, alpha on the set surfaces it may paint (not keep, not the sky): how much it weighs against the other views
    there (the paint pass), `want` (need()) handing over towards its frame edge over FEATHER, at least 1/255"""
    region = regions(depth)[0]
    H, W = region.shape
    a = np.ones((H, W), np.float32) if want is None else want.astype(np.float32)
    ys, xs = np.mgrid[0:H, 0:W]
    e = np.minimum(np.minimum(xs + 0.5, W - xs - 0.5) / W, np.minimum(ys + 0.5, H - ys - 0.5) / H)
    t = np.clip(e / FEATHER, 0.0, 1.0)
    a = np.maximum(a * t * t * (3 - 2 * t), NEEDMIN)
    A = cv2.resize(a, PLATE, interpolation=cv2.INTER_LINEAR)
    on = cv2.resize(region.astype(np.uint8), PLATE, interpolation=cv2.INTER_NEAREST) > 0
    alpha = np.where(on, np.maximum(np.round(A * 255.0), 1.0), 0.0).astype(np.uint8)
    return np.dstack([cv2.resize(painting, PLATE, interpolation=cv2.INTER_AREA), alpha])


def crop(img, k):
    """the middle k of img (k of its width and of its height)"""
    h, w = img.shape[:2]
    x0, y0 = (w - round(w * k)) // 2, (h - round(h * k)) // 2
    return img[y0:h - y0, x0:w - x0]


def footprint(depth, pull):
    """how much of the middle of a view drawn `pull` m further back the close view it stands in for sees (a share of
    its width and height): a surface z m from the close camera is z + pull from this one, so it fills z / (z + pull),
    taken at the median distance of the set in that middle"""
    st, keep, invz = regions(depth)
    k = 1.0
    for _ in range(6):
        iz = crop(invz, k)[crop(st | keep, k)]
        iz = iz[iz > 0]
        if not iz.size:
            return 1.0
        zm = float(np.median(1.0 / iz))
        k = max(zm - pull, 0.05) / zm
    return k


def refine(pdir, name, pt, k, depth, a, cache):
    """the middle k of painting pt at the plate's width: enlarged, and then painted again by H3 at that size with the
    detail a painting from this close would have (StableGen's refine pass: the same picture, sharper). A redrawing
    whose shapes moved (agreement() with the enlargement under REFINE_AGREE) is tried with the next seed, and after
    TRIES the plain enlargement is kept. Returns the painting's path (the enlargement is NAME.crop.png)."""
    c = crop(pt, k)
    up = c.shape[1] < PLATE[0]
    big = cv2.resize(c, (PLATE[0], round(PLATE[0] * c.shape[0] / c.shape[1])),
                     interpolation=cv2.INTER_LANCZOS4 if up else cv2.INTER_AREA)
    src = os.path.join(pdir, name + ".crop.png")
    cv2.imwrite(src, big)
    if not up:
        return src
    small = cv2.resize(big, (depth.shape[1], depth.shape[0]), interpolation=cv2.INTER_AREA)
    for t in range(TRIES):
        out = paint(src, REFINE_PROMPT, a.seed + t, a.vae, cache)
        agree = agreement(small, cv2.imread(out), depth)
        if agree >= REFINE_AGREE:
            print("%s: its middle (%.2f) enlarged %.1f times and redrawn sharper, shapes agree %.2f" % (
                name, k, PLATE[0] / c.shape[1], agree), flush=True)
            kept = os.path.join(pdir, name + ".refined.png")
            cv2.imwrite(kept, detail(cv2.imread(out), big))
            return kept
        print("%s: seed %d's sharper redrawing moved things (shapes agree %.2f), %s" % (
            name, a.seed + t, agree, "trying the next seed" if t + 1 < TRIES else "kept enlarged"), flush=True)
    return src


def detail(ref, base):
    """ref's detail finer than DETAIL_BLUR on base's broader colour and light (base resized to ref)"""
    r = ref.astype(np.float32)
    b = cv2.resize(base, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    lo = lambda x: cv2.GaussianBlur(x, (0, 0), DETAIL_BLUR)
    return np.clip(lo(b) + r - lo(r), 0, 255).astype(np.uint8)


def need(cams, v, depth, step=2):
    """(how much a new view's paint is wanted on each of its pixels (HxW, NEEDMIN..1): 1 where the earlier views'
    paintings (cams, from cams_of()) give its spot under NEED[0] of its own painting pixels, NEEDMIN over NEED[1];
    where any of them shows (HxW 0..1, however soft))"""
    H, W = depth.shape[:2]
    if not cams:
        return np.ones((H, W), np.float32), np.zeros((H, W), np.float32)
    P, z, _ = points(v, depth, step)
    best = np.zeros(z.shape)
    for cam in cams:
        best = np.maximum(best, sharpness(P, z, step, cam))   # their painting pixels per pixel of this view
    ratio = best / (PLATE[0] / W)
    n = np.where(z > 0, np.clip((NEED[1] - ratio) / (NEED[1] - NEED[0]), 0.0, 1.0), 1.0).astype(np.float32)
    n = cv2.GaussianBlur(cv2.resize(n, (W, H), interpolation=cv2.INTER_LINEAR), (0, 0), 0.03 * W)
    seen = cv2.resize((best > 0).astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)
    return np.maximum(n, NEEDMIN), seen


def match(pt, guide, w):
    """pt with its broad colour and light moved onto guide's (the earlier paint, as the film shows it from this view)
    where w (HxW at guide's size, 0..1) says the earlier paint is there; the move fades out away from it"""
    H, W = w.shape
    if w.mean() < 0.01:
        return pt
    s = MATCH_BLUR * W
    lp = cv2.GaussianBlur(lin(cv2.resize(pt, (W, H), interpolation=cv2.INTER_AREA)), (0, 0), s)
    lg = cv2.GaussianBlur(lin(guide), (0, 0), s)
    d = (np.log(lg + EPS) - np.log(lp + EPS)) * w[..., None]
    S = 4.0 * s
    ws = cv2.GaussianBlur(w, (0, 0), S)
    corr = cv2.GaussianBlur(d, (0, 0), S) / np.maximum(ws, 1e-4)[..., None] * np.clip(ws / 0.2, 0.0, 1.0)[..., None]
    corr = cv2.resize(np.clip(corr, -MATCH_MAX, MATCH_MAX), (pt.shape[1], pt.shape[0]), interpolation=cv2.INTER_LINEAR)
    x = np.clip(lin(pt) * np.exp(corr), 0.0, 1.0)
    x = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return np.clip(x * 255.0 + 0.5, 0, 255).astype(np.uint8)


def zoom(a, pdir, name, v, depth, guide, pt, cache):
    """a pulled-back view kept as the close view it stands in for: the middle of its painting that the close view sees
    (footprint(), at most REFINE times enlarged), made sharp by refine(), with its bare view, depth and light drawn
    again by film.gd as that narrower camera, so the depth test is as sharp as the film's own (into pdir/NAME.zoom).
    Returns (that view, the folder it was drawn into, its guide (the middle of `guide`), the painting's path, its
    paint.json fields)."""
    k = max(footprint(depth, v["pull"]), PLATE[0] / (pt.shape[1] * REFINE))
    k = min(k, 1.0)
    zd = os.path.join(pdir, name + ".zoom")
    shutil.rmtree(zd, ignore_errors=True)
    views(a.scene, zd, str(v["frame"]), a.size, overscan=a.overscan * k, pull=v["pull"])
    zv = json.load(open(os.path.join(zd, "views.json"), encoding="utf-8"))["views"][0]
    zdepth = cv2.imread(os.path.join(zd, zv["depth"]), cv2.IMREAD_UNCHANGED)
    zguide = cv2.resize(crop(guide, k), (zdepth.shape[1], zdepth.shape[0]), interpolation=cv2.INTER_LINEAR)
    return zv, zd, zguide, refine(pdir, name, pt, k, zdepth, a, cache), {"zoom": round(k, 4)}


def keep(pdir, name, vdir, v, bare, guide, pt, depth, a, want=None):
    """write a painted view's ratio, plate (weighed by want, need(), kept as NAME.need.png), light and depth into
    pdir; returns (whether its light was written, repaint stats, the aligned painting)"""
    ratio, trust, aligned, st = repaint(bare, guide, pt, depth, a.colour, a.tone)
    wp = os.path.join(pdir, name + ".need.png")
    if want is not None:
        cv2.imwrite(wp, np.round(want * 255.0).astype(np.uint8))
    elif os.path.exists(wp):
        want = cv2.imread(wp, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
    lit = bare_light(vdir, v, pdir, name)
    shutil.copyfile(os.path.join(vdir, v["depth"]), os.path.join(pdir, name + ".depth.exr"))
    cv2.imwrite(os.path.join(pdir, name + ".ratio.exr"), np.dstack([ratio, trust]))
    cv2.imwrite(os.path.join(pdir, name + ".plate.png"), plate(pt, depth, want))
    return lit, st, aligned


def write_place(out, env, note, a, entries):
    with open(os.path.join(out, env, "paint.json"), "w", encoding="utf-8") as f:
        json.dump({"note": note, "mode": a.mode, "seed": a.seed, "colour": a.colour, "tone": a.tone, "style": a.style,
                   "vae": a.vae, "views": entries}, f, indent=1)


def retone(out, a):
    """read every place's paintings in out again at a.colour and a.tone, without painting anything"""
    vdir = os.path.join(out, "views")
    byname = {vname(v): v for v in json.load(open(os.path.join(vdir, "views.json"), encoding="utf-8"))["views"]}
    for env in sorted(os.listdir(out)):
        pj = os.path.join(out, env, "paint.json")
        if not os.path.isfile(pj):
            continue
        pdir = os.path.dirname(pj)
        place = json.load(open(pj, encoding="utf-8"))
        for e in place["views"]:
            v = byname[vname(e)]
            name = vname(e)
            guide = os.path.join(pdir, name + ".guide.png")
            guide = cv2.imread(guide if os.path.exists(guide) else os.path.join(vdir, v["image"]))
            vd = vdir
            if e.get("zoom"):   # kept as a narrower camera (zoom()), drawn into its own folder
                vd = os.path.join(pdir, name + ".zoom")
                v = json.load(open(os.path.join(vd, "views.json"), encoding="utf-8"))["views"][0]
                guide = crop(guide, e["zoom"])
            bare = cv2.imread(os.path.join(vd, v["image"]))
            guide = cv2.resize(guide, (bare.shape[1], bare.shape[0]), interpolation=cv2.INTER_LINEAR)
            depth = cv2.imread(os.path.join(vd, v["depth"]), cv2.IMREAD_UNCHANGED)
            pt = cv2.imread(os.path.join(pdir, e["painting"]))
            lit, _, _ = keep(pdir, name, vd, v, bare, guide, pt, depth, a)
            e["hfov"] = v["hfov"]
            e["plate"] = name + ".plate.png"
            if lit:
                e["light"] = name + ".light.exr"
        a.seed, a.style, a.vae = place["seed"], place["style"], place.get("vae", "")
        write_place(out, env, place["note"], a, place["views"])
        print("%s: %d views read again, %s mode, colour %.2f, tone %.2f" % (env, len(place["views"]), a.mode, a.colour,
                                                                               a.tone))


def vname(v):
    """a view's name, which its files start with: f00129 for frame 129 as the film frames it, f00129b2 for frame 129
    drawn from 2 m further back"""
    return v.get("name") or "f%05d" % v["frame"]


def looks(v):
    """a view's camera position and the way it looks"""
    b = v["basis"]
    return np.array(v["origin"]), -np.array(b[6:9]) / np.linalg.norm(b[6:9])


def film_info(scene):
    """a StykFilm scene's length and shots, as capture.gd --info writes them"""
    fd, path = __import__("tempfile").mkstemp(suffix=".json")
    os.close(fd)
    code = godot.run(["-s", "res://addons/styk_painted/capture.gd", "--", "--film", scene, "--info", path])
    if code != 0:
        sys.exit("capture.gd --info failed (%d)" % code)
    info = json.load(open(path, encoding="utf-8"))
    os.remove(path)
    return info


def sample(scene, every):
    """the frames --coverage and --frames auto judge: every `every`-th frame and each shot's first and last"""
    sc = film_info(scene) if tscn(scene) else json.load(open(scene, encoding="utf-8"))
    want = set(range(0, sc["total"], every))
    for sh in sc["shots"]:
        want |= {sh["start"], sh["end"] - 1}
    return ",".join(str(f) for f in sorted(want))


def points(v, d, step=1):
    """a bare view's set pixels as world points, on every step-th pixel: (P hxwx3, z hxw with 0 off the set, film
    pixels per step)"""
    st, _, invz = regions(d)
    H, W = st.shape
    ys, xs = np.mgrid[0:H:step, 0:W:step]
    z = np.where(st[ys, xs], 1.0 / np.maximum(invz[ys, xs], 1e-6), 0.0)
    B, o, tx, ty = camera(v, (H, W))
    cam = np.stack([((xs + 0.5) / W * 2 - 1) * tx * z, (1 - (ys + 0.5) / H * 2) * ty * z, -z], -1)
    return cam @ B.T + o, z, step * FILM_W / W


def painter(v, d, on=None):
    """a painting camera for sharpness(): its camera, inverse depth and the set it paints (`on`, else the view's own
    paintable set, as a painting of it would have)"""
    invz = d[..., 2]
    return camera(v, invz.shape), invz, regions(d)[0] if on is None else on


def sharpness(P, z, film_step, cam):
    """how many painting pixels each film pixel gets from one painting camera, the way the paint pass looks it up
    (inside its frame, the same depth there, on its set; 0 where it does not see the point). It is the fewest across
    the surface's most stretched way, so a surface the painting saw edge-on, or from much further away, scores low:
    it shows smeared."""
    (B, o, tx, ty), di, seti = cam
    c = (P - o) @ B
    zi = -c[..., 2]
    zs = np.maximum(zi, 1e-6)
    qx = 0.5 + 0.5 * c[..., 0] / (zs * tx)
    qy = 0.5 - 0.5 * c[..., 1] / (zs * ty)
    ok = (z > 0) & (zi > 0.05) & (qx > 0) & (qx < 1) & (qy > 0) & (qy < 1)
    h, w = di.shape
    px = np.clip((qx * w).astype(int), 0, w - 1)
    py = np.clip((qy * h).astype(int), 0, h - 1)
    dz = di[py, px]
    ok &= (dz > 0) & (np.abs(1.0 / np.maximum(dz, 1e-6) - zi) <= 0.02 * zi + 0.02) & seti[py, px]
    X, Y = qx * PLATE[0], qy * PLATE[1]

    def nxt(A, axis):   # the step to the next pixel along axis, where both are seen on one surface
        a0, a1 = (A[:, :-1], A[:, 1:]) if axis else (A[:-1], A[1:])
        z0, z1 = (z[:, :-1], z[:, 1:]) if axis else (z[:-1], z[1:])
        o0, o1 = (ok[:, :-1], ok[:, 1:]) if axis else (ok[:-1], ok[1:])
        dd = np.where(o0 & o1 & (np.abs(z1 - z0) <= 0.02 * z0 + 0.01), a1 - a0, np.nan)
        return np.pad(dd, [(0, 0), (0, 1)] if axis else [(0, 1), (0, 0)], mode="edge")

    a, c2, b, d2 = nxt(X, 1), nxt(Y, 1), nxt(X, 0), nxt(Y, 0)
    T = a * a + b * b + c2 * c2 + d2 * d2
    D = a * d2 - b * c2
    smin = np.sqrt(np.maximum(T - np.sqrt(np.maximum(T * T - 4 * D * D, 0)), 0) / 2) / film_step
    return np.where(ok, np.where(np.isnan(smin), np.inf, smin), 0.0)   # no neighbour on its surface: trusted


def cams_of(pdir, entries, names=None, colour=False):
    """the painting cameras of paint.json entries in pdir (only the views in `names`, when given); with colour, each
    also with its plate as (half-size colour, smoothed by SEAM_BLUR, as CIELAB; its alpha 0..1, the paint pass's
    weight), for seams()"""
    cams = []
    for e in entries:
        if names is not None and vname(e) not in names:
            continue
        d = cv2.imread(os.path.join(pdir, e["depth"]), cv2.IMREAD_UNCHANGED)
        full = cv2.imread(os.path.join(pdir, e.get("plate") or e["ratio"]), cv2.IMREAD_UNCHANGED)
        pl = cv2.resize(full, (d.shape[1], d.shape[0]), interpolation=cv2.INTER_NEAREST)
        cam = painter(e, d, pl[..., 3] > 0)
        if colour and full.dtype == np.uint8 and full.shape[2] == 4:
            half = cv2.resize(full, (full.shape[1] // 2, full.shape[0] // 2), interpolation=cv2.INTER_AREA)
            lab = cv2.cvtColor(cv2.GaussianBlur(half[..., :3], (0, 0), SEAM_BLUR).astype(np.float32) / 255.0,
                               cv2.COLOR_BGR2LAB)
            cam = cam + ((lab, half[..., 3].astype(np.float32) / 255.0, np.array(looks(e)[0])),)
        cams.append(cam)
    return cams


def painters(out, env, names=None, colour=False):
    """the painting cameras of a place in out (only the views in `names`, when given)"""
    pj = os.path.join(out, env, "paint.json")
    if not os.path.exists(pj):
        return []
    return cams_of(os.path.join(out, env), json.load(open(pj, encoding="utf-8"))["views"], names, colour)


def seams(P, z, step, cams, view=None):
    """where the film hands over from one painting to another with a visible colour step, on a bare view's samples
    (points()): the paint pass's weights (painted.gdshaderinc, plate mode) of each painting that sees a sample, and
    the step there: the sum over paintings of how much its share changes to the next sample times how far its colour
    (smoothed) is from the blend. A hand-over along one of the view's drawn lines (object edges, outlines: `view`, the
    bare view) or a break in depth hides under the line, and is not one. Returns the samples where that is SEAM_DE or
    more (HxW bool)."""
    if len(cams) < 2 or any(len(c) < 4 for c in cams):
        return np.zeros(z.shape, bool)
    dx = np.pad(P[:, 1:] - P[:, :-1], [(0, 0), (0, 1), (0, 0)], mode="edge")
    dy = np.pad(P[1:] - P[:-1], [(0, 1), (0, 0), (0, 0)], mode="edge")
    nrm = np.cross(dx, dy)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=-1, keepdims=True), 1e-9)
    ws, labs = [], []
    for (B, o, tx, ty), di, seti, (lab, alpha, eye) in cams:
        c = (P - o) @ B
        zi = -c[..., 2]
        zs = np.maximum(zi, 1e-6)
        qx = 0.5 + 0.5 * c[..., 0] / (zs * tx)
        qy = 0.5 - 0.5 * c[..., 1] / (zs * ty)
        ok = (z > 0) & (zi > 0.05) & (qx > 0) & (qx < 1) & (qy > 0) & (qy < 1)
        h, w = di.shape
        dz = di[np.clip((qy * h).astype(int), 0, h - 1), np.clip((qx * w).astype(int), 0, w - 1)]
        ok &= (dz > 0) & (np.abs(1.0 / np.maximum(dz, 1e-6) - zi) <= 0.02 * zi + 0.02)
        lh, lw = alpha.shape
        px, py = np.clip((qx * lw).astype(int), 0, lw - 1), np.clip((qy * lh).astype(int), 0, lh - 1)
        e = np.minimum(np.minimum(qx, 1 - qx), np.minimum(qy, 1 - qy))
        t = np.clip(e / 0.06, 0, 1)
        sees = np.clip(e / 0.004, 0, 1)
        to = eye - P
        face = np.abs((nrm * to).sum(-1)) / np.maximum(np.linalg.norm(to, axis=-1), 1e-9)
        wt = alpha[py, px] * t * t * (3 - 2 * t) * sees * np.maximum(face, 0.02) / (zs * tx)
        ws.append(np.where(ok, wt, 0.0) ** 4)
        labs.append(lab[py, px])
    W = np.stack(ws)
    tot = W.sum(0)
    share = W / np.maximum(tot, 1e-30)
    bar = (share[..., None] * np.stack(labs)).sum(0)
    on = tot > 0
    flat = on & (np.pad(np.abs(np.diff(z, axis=1)), [(0, 0), (0, 1)]) <= 0.02 * z + 0.01) & \
        (np.pad(np.abs(np.diff(z, axis=0)), [(0, 1), (0, 0)]) <= 0.02 * z + 0.01)
    S = np.zeros(z.shape)
    for k in range(len(ws)):
        g = np.pad(np.abs(np.diff(share[k], axis=1)), [(0, 0), (0, 1)]) + \
            np.pad(np.abs(np.diff(share[k], axis=0)), [(0, 1), (0, 0)])
        S += g * np.linalg.norm(labs[k] - bar, axis=-1)
    hidden = ~flat
    if view is not None:
        e = cv2.Canny(cv2.cvtColor(view, cv2.COLOR_BGR2GRAY), 40, 110) > 0
        e |= cv2.cvtColor(view, cv2.COLOR_BGR2GRAY) < 40   # ink
        hidden |= cv2.resize(e.astype(np.uint8), z.shape[::-1], interpolation=cv2.INTER_AREA) > 0
    hidden = cv2.dilate(hidden.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    return on & ~hidden & (S >= SEAM_DE)


def failing(bare, soft):
    return bare > COVER or soft > SOFTMAX


def coverage(out, scene, every, size):
    """how well the paintings in out cover the film: every `every`-th frame (and each shot's first and last) drawn as a
    bare view, each set pixel looked up in the painting cameras of its place as the paint pass does. Prints each
    frame's share of the picture on bare set (no painting sees it) and shown smeared (the best painting gives it fewer
    than SOFT painting pixels per film pixel: seen edge-on or from far), and returns the frames over COVER or SOFTMAX;
    those need a view of their own. Each is written to DIR/bare/ with its bare set red and its smeared set yellow."""
    shutil.rmtree(os.path.join(out, "bare"), ignore_errors=True)
    vdir = views(scene, os.path.join(out, "coverage"), sample(scene, every), size)
    vs = json.load(open(os.path.join(vdir, "views.json"), encoding="utf-8"))["views"]
    cams = {}
    rows = []
    for v in vs:
        if v["env"] not in cams:
            cams[v["env"]] = painters(out, v["env"], colour=True)
        d = cv2.imread(os.path.join(vdir, v["depth"]), cv2.IMREAD_UNCHANGED)
        P, z, fs = points(v, d)
        best = np.zeros(z.shape)
        for cam in cams[v["env"]]:
            best = np.maximum(best, sharpness(P, z, fs, cam[:3]))
        on = z > 0
        bare, soft = on & (best == 0), on & (best > 0) & (best < SOFT)
        b, sf = float(bare.mean()), float(soft.mean())
        sm = seams(*points(v, d, 2)[:2], 2, cams[v["env"]], cv2.imread(os.path.join(vdir, v["image"])))
        sl = float(sm.sum()) * 2 / z.shape[1]   # its length, in frame widths
        if failing(b, sf) or sl > SEAMLEN:   # where, for choosing the frame that sees it, or seeing the patch
            img = cv2.imread(os.path.join(vdir, v["image"]))
            img = cv2.resize(img, z.shape[::-1]) if img.shape[:2] != z.shape else img
            sm = cv2.dilate(cv2.resize(sm.astype(np.uint8), z.shape[::-1], interpolation=cv2.INTER_NEAREST),
                            np.ones((3, 3), np.uint8)) > 0
            for m, col in ((bare, (0, 0, 255)), (soft, (0, 220, 255)), (sm, (255, 0, 255))):
                img[m] = (img[m] * 0.3 + np.array(col) * 0.7).astype(np.uint8)
            os.makedirs(os.path.join(out, "bare"), exist_ok=True)
            cv2.imwrite(os.path.join(out, "bare", "f%05d.jpg" % v["frame"]), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        rows.append((v["frame"], v["env"], b, sf, sl))
    print("coverage of %s (frame, place, share of the picture on bare set, shown smeared, seams in frame widths):" % out)
    for f, env, b, sf, sl in rows:
        print("  %5d %-8s %5.1f%% %5.1f%% %5.2f%s%s" % (f, env, b * 100, sf * 100, sl,
                                                    "  <- needs a view" if failing(b, sf) else "",
                                                    "  <- shows a patch" if sl > SEAMLEN else ""))
    shutil.rmtree(vdir, ignore_errors=True)
    bad = [r for r in rows if failing(r[2], r[3])]
    print("%d of %d frames need a view, %d show a patch" % (len(bad), len(rows), sum(r[4] > SEAMLEN for r in rows)),
          flush=True)
    return bad


def plan(out, scene, every, size, overscan, have, skip):
    """--frames auto: the views to paint next, in order, as (frame, pull). The judged frames (sample()) are drawn as the
    film shows them and, as candidates, as views --overscan wide. A candidate is scored by how much of the failing
    frames' set it would newly cover, unsmeared, given the paintings already made (`have`, view names); the best is
    taken, and again, until no judged frame fails, none gains MINGAIN of some frame, or a place has MAXVIEWS views.
    `skip` holds each dropped view's name and camera: candidates framed like it are not offered, and it is drawn again
    from the nearest of PULLS further back not yet dropped, where H3 sees what the surface is. The first pick of a place
    covers the most, so it is usually its widest view: the one the others carry the paint on from."""
    frames = sample(scene, every)
    film = views(scene, os.path.join(out, "plan", "film"), frames, size)
    fv = json.load(open(os.path.join(film, "views.json"), encoding="utf-8"))["views"]
    cand = views(scene, os.path.join(out, "plan", "cand"), frames, size, overscan=overscan)
    cv = json.load(open(os.path.join(cand, "views.json"), encoding="utf-8"))["views"]

    def alike(v):
        o, l = looks(v)
        for w in skip.values():
            wo, wl = looks(w)
            if np.linalg.norm(o - wo) < ALIKE[0] and np.degrees(np.arccos(np.clip(l @ wl, -1, 1))) < ALIKE[1]:
                return True
        return False
    picks = []
    for env in dict.fromkeys(v["env"] for v in fv):
        made = painters(out, env, set(have))
        fr = []
        for v in (v for v in fv if v["env"] == env):
            P, z, fs = points(v, cv2.imread(os.path.join(film, v["depth"]), cv2.IMREAD_UNCHANGED), PLAN_STEP)
            best = np.zeros(z.shape)
            for cam in made:
                best = np.maximum(best, sharpness(P, z, fs, cam))
            fr.append({"frame": v["frame"], "P": P, "z": z, "fs": fs, "on": z > 0, "seen": best > 0,
                       "good": best >= SOFT, "n": z.size})
        # a dropped close view gets its nearest pull-back not yet tried, unscored: it is painted at PULL_MP and kept
        # zoomed in on the close view's middle and sharpened (zoom()), which is all the close shot gets
        mine = []
        for n, w in skip.items():
            if w["env"] == env and not w.get("pull") and not any(h.startswith(n + "b") for h in have):
                pl = next((pl for pl in PULLS if n + "b%g" % pl not in skip), None)
                if pl is not None:
                    mine.append((w["frame"], pl))
        cs = []
        for v in cv:
            if v["env"] != env or vname(v) in have or vname(v) in skip or alike(v):
                continue
            cam = painter(v, cv2.imread(os.path.join(cand, v["depth"]), cv2.IMREAD_UNCHANGED))
            sees = [sharpness(g["P"], g["z"], g["fs"], cam) for g in fr]
            cs.append(((v["frame"], 0.0), [x > 0 for x in sees], [x >= SOFT for x in sees]))

        def fails(f):
            return failing((f["on"] & ~f["seen"]).sum() / f["n"], (f["on"] & f["seen"] & ~f["good"]).sum() / f["n"])

        def gains(c):   # the share of each failing frame's picture it newly covers, unsmeared
            return [((c[1][k] & f["on"] & ~f["seen"]).sum() + (c[2][k] & f["on"] & ~f["good"]).sum()) / f["n"]
                    for k, f in enumerate(fr) if fails(f)]

        n = len(made) + len(mine)
        while n < MAXVIEWS and cs and any(fails(f) for f in fr):
            c = max(cs, key=lambda c: sum(gains(c)))
            if max(gains(c)) < MINGAIN:   # a little on many frames it cannot make pass is not worth a painting
                break
            for k, f in enumerate(fr):
                f["seen"] |= c[1][k]
                f["good"] |= c[2][k]
            cs.remove(c)
            mine.append(c[0])
            n += 1
        left = sum(fails(f) for f in fr)
        print("plan %s: %d views made, paint %s next; %d of %d judged frames would still need a view" % (
            env, len(made), [f if not pl else "%d from %gm back" % (f, pl) for f, pl in mine] or "none", left,
            len(fr)), flush=True)
        picks += mine
    shutil.rmtree(os.path.join(out, "plan"), ignore_errors=True)
    return picks


def paint_views(a, out, vdir, vs, places):
    """paint the bare views vs (drawn into vdir) one after another, onto the places' paintings so far (places: env ->
    paint.json entries). Returns (places, the names of the views painted, the views dropped)."""
    said = notes(a.note)
    note = lambda env: said.get(env, said.get("", "a room or street set"))
    cache = os.path.join(out, "painted")
    os.makedirs(cache, exist_ok=True)
    made, dropped = [], []
    for v in vs:
        png = os.path.join(vdir, v["image"])
        env = v["env"]
        pdir = os.path.join(out, env)
        os.makedirs(pdir, exist_ok=True)
        name = vname(v)
        guide = png
        carry = ""
        if places.get(env) and not a.at_once:
            # this view with the earlier paintings already on the set, so H3 carries on the same paint
            write_place(out, env, note(env), a, places[env])
            pre = views(a.scene, os.path.join(out, "pre"), str(v["frame"]), a.size, out, a.overscan,
                        v.get("pull", 0.0))
            guide = os.path.join(pre, "f%05d.png" % v["frame"])
            kept = os.path.join(pdir, name + ".guide.png")
            shutil.copyfile(guide, kept)
            guide = kept
            carry = CARRY
        prompt = PROMPT.format(note=note(env), style=a.style, carry=carry)
        depth = cv2.imread(os.path.join(vdir, v["depth"]), cv2.IMREAD_UNCHANGED)
        bare = cv2.imread(png)
        for t in range(TRIES):
            painted = paint(guide, prompt, a.seed + t, a.vae, cache, PULL_MP if v.get("pull") else None)
            agree = agreement(bare, cv2.imread(painted), depth)
            if carry:   # a close view of a flat surface has next to no shapes of its own to keep; the paint carried
                # onto its guide does, and a painting that drew another picture keeps neither
                agree = max(agree, agreement(cv2.imread(guide), cv2.imread(painted), depth))
            if agree >= AGREE:
                break
            print("%s %s: seed %d drew another picture (shapes agree %.2f), %s" % (
                env, name, a.seed + t, agree, "trying the next seed" if t + 1 < TRIES else "view dropped"), flush=True)
        else:
            for ext in (".ratio.exr", ".plate.png", ".light.exr", ".depth.exr", ".aligned.jpg", ".guide.png",
                        ".crop.png", ".refined.png", ".matched.png", ".need.png"):   # nothing of an earlier run's
                if os.path.exists(os.path.join(pdir, name + ext)):
                    os.remove(os.path.join(pdir, name + ext))
            shutil.rmtree(os.path.join(pdir, name + ".zoom"), ignore_errors=True)
            dropped.append(v)
            continue
        kv, kd, g, shown, extra = v, vdir, cv2.imread(guide), painted, {}
        if v.get("pull"):   # kept as the close view it stands in for, sharpened
            kv, kd, g, shown, extra = zoom(a, pdir, name, v, depth, g, cv2.imread(painted), cache)
            bare = cv2.imread(os.path.join(kd, kv["image"]))
            depth = cv2.imread(os.path.join(kd, kv["depth"]), cv2.IMREAD_UNCHANGED)
            extra["painted"] = os.path.relpath(painted, pdir)
        # only where the earlier paintings are too soft for this view does its own paint weigh in, and its broad
        # colour is the earlier paint's where that shows on its guide: where they meet, one paint
        want, seen = need(cams_of(pdir, places.get(env, [])), kv, depth)
        pt = cv2.imread(shown)
        if carry:   # the earlier paint's broad colour, however soft it shows
            on = regions(depth)[0].astype(np.float32)
            pt = match(pt, cv2.resize(g, (on.shape[1], on.shape[0]), interpolation=cv2.INTER_LINEAR), seen * on)
            shown = os.path.join(pdir, name + ".matched.png")
            cv2.imwrite(shown, pt)
        lit, st, aligned = keep(pdir, name, kd, kv, bare, g, pt, depth, a, want)
        cv2.imwrite(os.path.join(pdir, name + ".aligned.jpg"), aligned, [cv2.IMWRITE_JPEG_QUALITY, 90])
        entry = {k: v[k] for k in ("frame", "name", "pull") if k in v} | {k: kv[k] for k in ("basis", "origin", "hfov")}
        entry |= {"ratio": name + ".ratio.exr", "plate": name + ".plate.png", "depth": name + ".depth.exr",
                  "painting": os.path.relpath(shown, pdir)} | extra
        if lit:
            entry["light"] = name + ".light.exr"
        places.setdefault(env, []).append(entry)
        made.append(name)
        print("%s %s: shapes agree %.2f, %.0f%% read directly, %.0f%% added objects left out, drift p90 %.1f px, "
              "%.1f%% kept as drawn" % (env, name, agree, st["read"] * 100, st["added"] * 100, st["flow_p90"],
                                        st["keep"] * 100), flush=True)
    return places, made, dropped


def draw(a, out, picks):
    """draw the bare views picked ((frame, pull) each) into DIR/views, named by vname(), next to the views drawn
    before (views.json merged); returns them in the order picked"""
    vdir = os.path.join(out, "views")
    os.makedirs(vdir, exist_ok=True)
    vs = []
    for pl in dict.fromkeys(p for _, p in picks):
        fs = [f for f, p in picks if p == pl]
        new = views(a.scene, os.path.join(out, "views-new"), ",".join(str(f) for f in fs), a.size,
                    overscan=a.overscan, pull=pl)
        for v in json.load(open(os.path.join(new, "views.json"), encoding="utf-8"))["views"]:
            v["name"] = "f%05d" % v["frame"] + ("b%g" % pl if pl else "")
            v["pull"] = pl
            for k in ("image", "depth", "light"):
                if v.get(k) and os.path.exists(os.path.join(new, v[k])):
                    to = v["name"] + v[k][len("f%05d" % v["frame"]):]
                    os.replace(os.path.join(new, v[k]), os.path.join(vdir, to))
                    v[k] = to
            vs.append(v)
        shutil.rmtree(new, ignore_errors=True)
    vj = os.path.join(vdir, "views.json")
    old = json.load(open(vj, encoding="utf-8"))["views"] if os.path.exists(vj) else []
    names = [v["name"] for v in vs]
    with open(vj, "w", encoding="utf-8") as f:
        json.dump({"views": [v for v in old if vname(v) not in names] + vs}, f, indent=1)
    order = ["f%05d" % f + ("b%g" % p if p else "") for f, p in picks]
    return vdir, sorted(vs, key=lambda v: order.index(v["name"]))


def auto(a, out):
    """--frames auto: plan, paint what the plan picks, and plan again with what was really painted (a dropped view's
    surfaces are judged anew, and it is drawn again from further back), up to ROUNDS times; then the coverage report."""
    for name in os.listdir(out) if os.path.isdir(out) else []:   # an earlier run's views and places, not its cache
        if name != "painted" and os.path.isdir(os.path.join(out, name)):
            shutil.rmtree(os.path.join(out, name))
    places, have, skip = {}, [], {}
    said = notes(a.note)
    for r in range(ROUNDS):
        picks = plan(out, a.scene, a.every, a.size, a.overscan, have, skip)
        if not picks:
            break
        vdir, vs = draw(a, out, picks)
        places, made, dropped = paint_views(a, out, vdir, vs, places)
        for env, entries in places.items():   # the next plan judges what is painted now
            write_place(out, env, said.get(env, said.get("", "a room or street set")), a, entries)
        have += made
        skip |= {vname(v): v for v in dropped}
        print("round %d: painted %s, dropped %s" % (r + 1, made or "none", [vname(v) for v in dropped] or "none"),
              flush=True)
    shutil.rmtree(os.path.join(out, "pre"), ignore_errors=True)
    print("painted %s into %s from views %s" % (", ".join(places), out, have), flush=True)
    coverage(out, a.scene, a.every, a.size)


def camera(v, shape):
    """(basis 3x3 with the camera's axes as columns, origin, tan across, tan up) of a views.json / paint.json camera"""
    b = v["basis"]
    t = np.tan(np.radians(v["hfov"]) * 0.5)
    return np.array(b, np.float64).reshape(3, 3).T, np.array(v["origin"], np.float64), t, t * shape[0] / shape[1]


def notes(text):
    """--note as {place: note}: "place=note;place=note", or one note for every place (key "")"""
    if not re.match(r"^\s*\w+\s*=", text):
        return {"": text.strip()}
    out = {}
    for part in text.split(";"):
        if part.strip():
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scene", help="the film: a StykStudio scene.json, or a Godot scene (res://....tscn) whose root is "
                                     "a StykFilm")
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", help="the frames to paint from, as film.gd --frames (the first is painted first), or "
                                     "auto: chosen from what the film shows")
    ap.add_argument("--size", default="864x1536")
    ap.add_argument("--note", default="a room or street set",
                    help='what the place is, for the painter: one note for every place, or "wire=...;street=..." '
                         'with one per place (a place not named takes the default)')
    ap.add_argument("--style", default=STYLE, help="the look every surface is repainted in")
    ap.add_argument("--mode", choices=("plate", "ratio"), default="plate",
                    help="plate: the set shows each painting as it is; ratio: our set keeps its shapes and light and takes "
                         "the painting's colour, tone and material (--colour, --tone)")
    ap.add_argument("--colour", type=float, default=1.0, help="how much of the painting's colour the set takes, 0 to 1")
    ap.add_argument("--tone", type=float, default=0.5,
                    help="how much of the painting's broad light and dark the set takes, 0 to 1 (its material always)")
    ap.add_argument("--overscan", type=float, default=OVERSCAN,
                    help="paint each view this many times wider than the shot, to cover the set around its frame")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--vae", default="", help="an H3-Image VAE file other than the template's")
    ap.add_argument("--at-once", action="store_true", help="paint every bare view on its own instead of one after another")
    ap.add_argument("--views-only", action="store_true")
    ap.add_argument("--coverage", action="store_true",
                    help="report how much of the film's set the paintings in DIR cover, frame by frame, and paint nothing")
    ap.add_argument("--every", type=int, default=10, help="--coverage and --frames auto: judge every this many frames")
    ap.add_argument("--keep-views", action="store_true", help="reuse DIR/views as drawn last time")
    ap.add_argument("--retone", action="store_true",
                    help="read the paintings already in DIR again at --colour and --tone, painting nothing")
    a = ap.parse_args()

    out = os.path.abspath(a.out)
    if a.retone:
        return retone(out, a)
    if a.coverage:
        if not a.scene:
            ap.error("--coverage needs --scene")
        coverage(out, a.scene, a.every, a.size)
        return
    if not a.scene or not a.frames:
        ap.error("--scene and --frames are needed to paint")
    if a.frames == "auto":
        return auto(a, out)
    vdir = os.path.join(out, "views")
    if not a.keep_views:
        views(a.scene, vdir, a.frames, a.size, overscan=a.overscan)
    if a.views_only:
        return
    vs = json.load(open(os.path.join(vdir, "views.json"), encoding="utf-8"))["views"]
    if a.keep_views and all(f.strip().isdigit() for f in a.frames.split(",")):
        want = [int(f) for f in a.frames.split(",")]
        vs = sorted((v for v in vs if v["frame"] in want), key=lambda v: want.index(v["frame"]))
    places, _, _ = paint_views(a, out, vdir, vs, {})
    said = notes(a.note)
    note = lambda env: said.get(env, said.get("", "a room or street set"))
    for env, entries in places.items():
        write_place(out, env, note(env), a, entries)
    shutil.rmtree(os.path.join(out, "pre"), ignore_errors=True)
    print("painted %s into %s" % (", ".join(places), out))


if __name__ == "__main__":
    main()
