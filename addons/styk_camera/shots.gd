## Where a film's camera is on any frame, from its shots (StykCam builds them). A pure function of the frame number:
## any frame renders alone and the same every time. StykKit uses it, and so does StykFilm in a plain Godot scene.
##
## Shots: {start, end (exclusive), ease, from: {pos, look, lens, roll}, to, focus, fstop, dof}, plus
##   keys: [{f, pos, look, lens, roll}, ...]  a curved move through several views instead of from/to (a one-er, a crane)
##   orbit: [x, y, z]  the camera circles this point from from.pos to to.pos (turns: whole extra circles)
##   swing: true       the aim turns between from and to instead of sliding (pans, tilts, whip pans)
##   vertigo: true | [x, y, z]  the lens follows the distance to look (or that point): a dolly zoom
##   shake: {amp (m), rot (deg), freq (Hz), seed}  smooth camera noise from the frame number (handheld, steadicam)
##   hide: [names or paths]  set nodes left out of the shot (a point of view's own head)
## Positions are in set coordinates: metres, z up, the front toward -y (Blender's). `space` maps them into the Godot
## world; the default turns z-up into Godot's y-up, so set (x, y, z) is Godot (x, z, -y). Lenses are mm on a 36 mm
## wide sensor (horizontal fit).
class_name StykShots
extends RefCounted

## set coordinates (z up) -> Godot world (y up)
const Z_UP := Transform3D(Basis(Vector3(1, 0, 0), Vector3(0, 0, -1), Vector3(0, 1, 0)), Vector3.ZERO)


static func v3(a: Array) -> Vector3:
	return Vector3(a[0], a[1], a[2])


static func ease_k(kind: String, u: float) -> float:
	match kind:
		"in": return u * u
		"out": return 1.0 - (1.0 - u) * (1.0 - u)
		"smooth": return 0.4 * u + 0.6 * u * u * (3.0 - 2.0 * u)
		"in_out": return u * u * (3.0 - 2.0 * u)
		"whip":   # still, a fast swing through the middle, still again
			var v := clampf((u - 0.3) / 0.4, 0.0, 1.0)
			return v * v * v * (v * (6.0 * v - 15.0) + 10.0)
		"crash": return 1.0 - pow(1.0 - u, 5.0)
	return u


## the shot that holds frame f (the last one past the end)
static func shot_at(shots: Array, f: int) -> Dictionary:
	for s in shots:
		if f >= s.start and f < s.end:
			return s
	return shots[-1]


## how far the shot's move is at frame f, eased (0..1)
static func shot_e(s: Dictionary, f: int) -> float:
	if s.has("u"):   # resolved by the studio's author step: the move's progress on each frame
		var us: Array = s.u
		return us[clampi(f - s.start, 0, us.size() - 1)]
	var u := clampf(float(f - s.start) / maxf(s.end - s.start - 1, 1), 0.0, 1.0)
	return ease_k(s.get("ease", "smooth"), u)


## a scalar, or [from, to] mixed by u
static func rng(v, u: float) -> float:
	return lerpf(v[0], v[1], u) if v is Array else float(v)


## the shot's own move at frame f: {pos, look, lens, roll} in set coordinates. A shot from a StykStudio scene.json
## carries `view`, its camera already placed on every frame ([x, y, z, look x, y, z, lens, roll?]); without a roll in
## the view, the shot's roll rides along by u.
static func view(s: Dictionary, f: int) -> Dictionary:
	var e := shot_e(s, f)
	if s.has("view"):
		var vs: Array = s.view
		var v: Array = vs[clampi(f - s.start, 0, vs.size() - 1)]
		# an eighth number is that frame's roll (a rig's dutch key), else the shot's roll rides along by u
		var rl: float = float(v[7]) if v.size() > 7 else rng(s.get("roll", 0.0), e)
		return {pos = Vector3(v[0], v[1], v[2]), look = Vector3(v[3], v[4], v[5]), lens = float(v[6]), roll = rl}
	if s.has("keys"):
		return _keys_view(s.keys, s.start + e * (s.end - 1 - s.start))
	var a: Dictionary = s.from
	var b: Dictionary = s.get("to", a)
	var pa := v3(a.pos)
	var pb := v3(b.get("pos", a.pos))
	var la := v3(a.look)
	var lb := v3(b.get("look", a.look))
	var pos := pa.lerp(pb, e)
	if s.has("orbit"):
		var c := v3(s.orbit)
		var ra := pa - c
		var rb := pb - c
		var a0 := atan2(ra.y, ra.x)
		var turn := wrapf(atan2(rb.y, rb.x) - a0, -PI, PI) + TAU * float(s.get("turns", 0))
		var r := lerpf(Vector2(ra.x, ra.y).length(), Vector2(rb.x, rb.y).length(), e)
		var ang := a0 + turn * e
		pos = c + Vector3(cos(ang) * r, sin(ang) * r, lerpf(ra.z, rb.z, e))
	var look := la.lerp(lb, e)
	if s.get("swing", false):
		var da := la - pa
		var db := lb - pb
		look = pos + da.normalized().slerp(db.normalized(), e) * lerpf(da.length(), db.length(), e)
	var roll: float = a.get("roll", 0.0)
	return {pos = pos, look = look, lens = lerpf(a.lens, b.get("lens", a.lens), e), roll = lerpf(roll, b.get("roll", roll), e)}


## Catmull-Rom through the keys at virtual frame t
static func _keys_view(keys: Array, t: float) -> Dictionary:
	var i := 0
	while i < keys.size() - 2 and t >= keys[i + 1].f:
		i += 1
	var n := keys.size()
	var k0: Dictionary = keys[maxi(i - 1, 0)]
	var k1: Dictionary = keys[i]
	var k2: Dictionary = keys[mini(i + 1, n - 1)]
	var k3: Dictionary = keys[mini(i + 2, n - 1)]
	var w := clampf((t - k1.f) / maxf(k2.f - k1.f, 1e-4), 0.0, 1.0)
	var C := func(ch: String) -> Vector3:
		return v3(k1[ch]).cubic_interpolate(v3(k2[ch]), v3(k0[ch]), v3(k3[ch]), w)
	var c := func(ch: String, d: float) -> float:
		return cubic_interpolate(k1.get(ch, d), k2.get(ch, d), k0.get(ch, d), k3.get(ch, d), w)
	var l0: float = keys[0].lens
	return {pos = C.call("pos"), look = C.call("look"), lens = c.call("lens", l0), roll = c.call("roll", 0.0)}


## a camera at pos aiming at look (set coordinates, placed by space), the frame's top toward z; straight down its top
## is toward +y
static func aim(space: Transform3D, pos: Vector3, look: Vector3) -> Transform3D:
	var up := space.basis * Vector3(0, 0, 1)
	if absf((space * look - space * pos).normalized().dot(up)) > 0.999:
		up = space.basis * Vector3(0, 1, 0)
	return Transform3D(Basis(), space * pos).looking_at(space * look, up)


static var _noise: FastNoiseLite


## smooth noise of frame f: a small turn and sideways / up-down drift in the camera's own axes
static func shake(sh: Dictionary, f: int, fps: float) -> Transform3D:
	if _noise == null:
		_noise = FastNoiseLite.new()
		_noise.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
		_noise.frequency = 1.0
	var t := f / fps * float(sh.get("freq", 1.0))
	var sd := float(sh.get("seed", 0)) * 13.7
	var n := func(ch: int) -> float:
		return _noise.get_noise_2d(t, sd + ch * 31.3)
	var amp: float = sh.get("amp", 0.0)
	var rot := deg_to_rad(sh.get("rot", 0.0))
	return Transform3D(Basis.from_euler(Vector3(n.call(0) * rot, n.call(1) * rot, n.call(2) * rot * 0.5)),
		Vector3(n.call(3) * amp, n.call(4) * amp, 0.0))


## Shake cues {f, amp, frames}: the camera's answer to an impact. The picture jolts by `amp` frame widths on frame f and
## settles within `frames`: it swings every 4 frames under an envelope that only falls, so each swing is smaller than
## the last and nothing grows back.
static func jolt(cues: Array, f: int) -> Vector2:
	var sum := Vector2.ZERO
	for c: Dictionary in cues:
		var n := int(c.get("frames", 12))
		var i := f - int(c.f)
		if i < 0 or i >= n:
			continue
		var fall := pow(1.0 - float(i) / n, 2.0)
		var a := TAU * i / 4.0
		var dir := Vector2.from_angle(deg_to_rad(float(c.get("angle", 70.0))))   # mostly up and down, as a blow lands
		sum += (dir * cos(a) + dir.orthogonal() * 0.4 * sin(a)) * float(c.get("amp", 0.006)) * fall
	return sum


## The camera of frame f in shot s: {xform (Godot world), fov (horizontal degrees), pos (set), dist (m to what it
## looks at), lens: {lens, fstop, focus} or null without depth of field}. track_xf / track_focus: a tracking camera's
## own placement this frame (StykTrack), which replaces the shot's move.
static func frame(s: Dictionary, f: int, fps: float, cues: Array, space: Transform3D, track_xf = null,
		track_focus = null) -> Dictionary:
	var v := view(s, f)
	var look: Vector3 = v.look
	var xf := aim(space, v.pos, look)
	if track_xf != null:
		xf = track_xf
		if track_focus != null:
			look = track_focus
	var cam_pos := space.affine_inverse() * xf.origin
	var lens_mm: float = v.lens
	if s.has("vertigo"):
		var sub := v3(s.vertigo) if s.vertigo is Array else look
		var a: Dictionary = s.keys[0] if s.has("keys") else s.from
		lens_mm = a.lens * (cam_pos - sub).length() / maxf((v3(a.pos) - sub).length(), 1e-4)
	if v.roll != 0.0:
		xf.basis = xf.basis * Basis(Vector3(0, 0, 1), deg_to_rad(v.roll))
	if s.has("shake"):
		xf = xf * shake(s.shake, f, fps)
		cam_pos = space.affine_inverse() * xf.origin
	var dist := maxf(0.1, (look - cam_pos).length())
	var j := jolt(cues, f)
	if j != Vector2.ZERO:   # turned, not moved: the whole picture shifts as one, near and far alike
		var per := 36.0 / lens_mm   # radians to a frame width (small angles)
		xf.basis = xf.basis * Basis.from_euler(Vector3(j.y * per, j.x * per, 0.0))
	var focus := look
	if s.has("focus"):
		var fo = s.focus
		if fo is Array:
			focus = v3(fo)
		else:
			var fa := v3(fo.get("from", fo.get("to")))
			focus = fa.lerp(v3(fo.get("to", fo.from)), shot_e(s, f))
	var lens = null if s.get("dof", true) == false else {lens = lens_mm, fstop = rng(s.get("fstop", 3.2), shot_e(s, f)),
		focus = maxf(0.1, (focus - cam_pos).length())}
	# Blender: horizontal sensor fit, 36 mm sensor
	return {xform = xf, fov = 2.0 * rad_to_deg(atan(18.0 / lens_mm)), pos = cam_pos, dist = dist, lens = lens}
