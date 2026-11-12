## Named shots and camera moves, so a film's camera is written as "a medium close-up from a low angle, pushing in"
## instead of hand-placed points. Each builder returns a shot dict for StykShots (shots.gd documents
## every field), in set coordinates (Blender: metres, z up, the front toward -y). The names follow film usage
## (vvsvs.pro/cinematique, Camera Work).
##
## A subject is {feet, head, head_r, facing}: where it stands, its head's centre and radius, and the way it faces.
## StykCam.figure() reads one off a figure's root node at the current frame (adding top, the highest point of its head
## and hat, and node, its path for hide); StykCam.thing() makes one for an object. Sizes fit exactly as seen from the
## camera, so a size reads the same from any angle; the frame is measured at the face, not the head's centre.
##
## Shot sizes, widest first: els (extreme long), ls (long, the whole body with room), full, cowboy (mid-thigh up),
## ms (medium, waist up), mcu (medium close-up, chest up), cu (close-up, head and shoulders), choker (chin to brow),
## ecu (extreme close-up, the eyes). Angles: eye (the camera at the subject's eyes), high, low, worm (from the floor),
## overhead (straight down, a bird's-eye view), or {elev = degrees}. Side: degrees around the subject from its front
## (0 head-on, 35 three-quarter, 90 profile, 180 from behind); positive goes to the subject's left.
##
## Views ({pos, look, lens, focus}) are the pieces; moves join two of them:
##   static_shot, push_in, pull_out, zoom, crash_zoom, dolly (truck / track sideways), pan, tilt, whip_pan, crane,
##   arc, orbit_360, vertigo, rack_focus, keys (a one-er through several views), follow (Phantom Camera tracking);
##   handheld() and steadicam() add camera noise to any shot, dutch() rolls one. Other views: group (two-shot and up),
##   ots (over the shoulder), pov (through a figure's eyes, its own head hidden), insert, establishing. A shot's hide
##   (names or paths in the set) leaves things out of it, such as a lamp over an overhead shot.
## tools/camera_moves.gd renders a sheet of all of them; its --check 1 proves each frame is the same however a render
## reaches it.
class_name StykCam
extends RefCounted

## the film frame: width / height (portrait 1080 x 1920)
static var aspect := 1080.0 / 1920.0

const SIZES := ["els", "ls", "full", "cowboy", "ms", "mcu", "cu", "choker", "ecu"]
## a lens for each size (mm, 36 mm wide sensor): wide for wide shots, longer as the camera comes in
const LENS := {els = 18.0, ls = 24.0, full = 24.0, cowboy = 28.0, ms = 32.0, mcu = 40.0, cu = 50.0, choker = 60.0, ecu = 75.0}
const ANGLES := {eye = 0.0, high = 30.0, low = -20.0, worm = -55.0, overhead = 89.0, bird = 89.0}


# ------------------------------------------------------------------------------------------------------ subjects
## A figure as it stands at the frame last drawn. set_root: the node whose transform places set coordinates in the
## world (StykKit's root; StykFilm.space for a plain Godot scene). The head is the figure's child named "head" (else the
## top of what is drawn, its radius a tenth of the height), the feet its "*_shoe" children (else the lowest point
## drawn). front: the figure's own forward axis (StykFigure's -y; a glTF character's is usually +z).
static func figure(set_root: Node3D, fig: Node3D, front := Vector3(0, -1, 0)) -> Dictionary:
	var inv := set_root.global_transform.affine_inverse()
	var hn := fig.get_node_or_null("head") as Node3D
	var low := INF
	for c in fig.get_children():
		if String(c.name).ends_with("_shoe"):
			low = minf(low, (inv * (c as Node3D).global_position).z)
	var top := _top(hn if hn != null else fig, inv)
	if low == INF:
		low = _bottom(fig, inv)
	var head: Vector3
	var r: float
	if hn != null:
		head = inv * hn.global_position
		r = hn.basis.get_scale().x
	else:
		var at := inv * fig.global_position
		r = (top - low) * 0.1
		head = Vector3(at.x, at.y, top - r)
	var face := inv.basis * (fig.global_basis * front)
	face.z = 0.0
	return {feet = Vector3(head.x, head.y, low), head = head, head_r = r, top = top,
		node = String(set_root.get_path_to(fig)),
		facing = face.normalized() if face.length() > 1e-4 else Vector3(0, -1, 0)}


## the lowest point of what is drawn under a node, set coordinates
static func _bottom(n: Node, inv: Transform3D) -> float:
	var low := INF
	if n is VisualInstance3D:
		var b: AABB = inv * (n.global_transform * (n as VisualInstance3D).get_aabb())
		low = b.position.z
	for c in n.get_children():
		low = minf(low, _bottom(c, inv))
	return low


## the highest point of what is drawn under a node (a head with its hat or hair), set coordinates
static func _top(n: Node, inv: Transform3D) -> float:
	var top := -INF
	if n is VisualInstance3D:
		var b: AABB = inv * (n.global_transform * (n as VisualInstance3D).get_aabb())
		top = b.end.z
	for c in n.get_children():
		top = maxf(top, _top(c, inv))
	return top


## an object: a point and its size (m), facing -y unless told
static func thing(at: Vector3, size: float, facing := Vector3(0, -1, 0)) -> Dictionary:
	return {feet = at - Vector3(0, 0, size / 2.0), head = at, head_r = size / 2.0, facing = facing}


## the part of the subject a size frames: [bottom, top] heights
static func _extent(sub: Dictionary, size: String) -> Vector2:
	var hz: float = sub.head.z
	var r: float = sub.head_r
	var foot: float = sub.feet.z
	var top: float = sub.get("top", hz + r)
	var h := top - foot
	match size:
		"els": return Vector2(foot - 1.6 * h, top + 2.4 * h)
		"ls": return Vector2(foot - 0.15 * h, top + 0.2 * h)
		"full": return Vector2(foot - 0.06 * h, top + 0.08 * h)
		"cowboy": return Vector2(foot + 0.36 * h, top + 0.06 * h)
		"ms": return Vector2(foot + 0.5 * h, top + 0.06 * h)
		"mcu": return Vector2(hz - 3.0 * r, top + 0.3 * r)
		"cu": return Vector2(hz - 2.0 * r, top + 0.25 * r)
		"choker": return Vector2(hz - 1.15 * r, hz + 0.75 * r)
		"ecu": return Vector2(hz - 0.5 * r, hz + 0.4 * r)
	assert(false, "StykCam: no shot size '%s'" % size)
	return Vector2()


static func vfov(lens: float) -> float:
	return 2.0 * atan(18.0 / aspect / lens)


## the lens that shows height h (m) at distance d
static func lens_for(h: float, d: float) -> float:
	return 18.0 / aspect / (h / 2.0 / d)


static func _is_eye(angle) -> bool:
	return typeof(angle) == TYPE_STRING and angle == "eye"


static func _elev(angle) -> float:
	if angle is float or angle is int:
		return float(angle)
	if angle is Dictionary:
		return angle.get("elev", 0.0)
	return ANGLES[angle]


## the way from the subject toward the camera: its facing turned by side, raised by elev
static func _dir(sub: Dictionary, side: float, elev: float) -> Vector3:
	var f: Vector3 = sub.facing
	var flat := f.rotated(Vector3(0, 0, 1), deg_to_rad(side))
	var e := deg_to_rad(elev)
	return (flat * cos(e) + Vector3(0, 0, sin(e))).normalized()


# --------------------------------------------------------------------------------------------------------- views
## A view of one subject. opts: angle ("eye"), side (0), lens (by size), fstop, height_bias (shifts the frame up,
## in frame heights, for lead room above), focus (the head by default)
static func frame(sub: Dictionary, size := "ms", opts := {}) -> Dictionary:
	var ex := _extent(sub, size)
	var h := ex.y - ex.x
	var mid := (ex.x + ex.y) / 2.0 + h * float(opts.get("height_bias", 0.0))
	var bot := mid - h / 2.0
	var top := mid + h / 2.0
	var lens: float = opts.get("lens", LENS[size])
	var half := vfov(lens) / 2.0
	var angle = opts.get("angle", "eye")
	var eye := _is_eye(angle)
	var e := 0.0 if eye else _elev(angle)
	var side: float = opts.get("side", 0.0)
	var r: float = sub.head_r
	var axis := Vector3(sub.head.x, sub.head.y, 0.0)
	var pos: Vector3
	var look: Vector3
	if absf(e) > 60.0:
		# from overhead the subject is seen end-on: fit its height as a flat plane, standing off past its top
		look = axis + Vector3(0, 0, mid)
		pos = look + _dir(sub, side, e) * (h / 2.0 / tan(half) + absf(sin(deg_to_rad(e))) * h / 2.0 + r)
	else:
		# the camera stands on a line from the subject (level at its eyes, or raised by the angle); find the distance
		# at which [bot, top] spans the frame's height exactly as seen from there, measured at the face (a head's
		# radius nearer than the axis), and aim at the middle of that span. Exact for a camera that tips down.
		var flat := _dir(sub, side, 0.0)
		var dir := _dir(sub, side, e)
		var floor_z: float = sub.feet.z + 0.03   # a worm's-eye camera sits on the floor
		var cam := func(d: float) -> Vector3:
			var p: Vector3 = axis + flat * d + Vector3(0, 0, sub.head.z) if eye else axis + Vector3(0, 0, mid) + dir * d
			p.z = maxf(p.z, floor_z)
			return p
		var hz: float = sub.head.z
		# the angles up to top and down to bot seen from p. Top: the face plane, or the head as a ball (with what it
		# wears) whose outline reaches past its top point when seen from below. Bot: the face plane inside the head,
		# else the body, about half a head's radius in front of the axis.
		var span := func(p: Vector3) -> Vector2:
			var x := Vector2(p.x - axis.x, p.y - axis.y).length()
			var ball := atan2(hz - p.z, x) + asin(clampf((top - hz) / maxf(Vector2(x, hz - p.z).length(), 1e-3), 0.0, 1.0))
			var a_top := maxf(atan2(top - p.z, maxf(x - r, 1e-3)), ball)
			return Vector2(a_top, atan2(bot - p.z, maxf(x - (r if bot >= hz - r else 0.5 * r), 1e-3)))
		var lo := 0.0
		var hi := 500.0
		for i in 60:
			var m := (lo + hi) / 2.0
			var a: Vector2 = span.call(cam.call(m))
			if a.x - a.y > 2.0 * half:
				lo = m
			else:
				hi = m
		pos = cam.call(hi)
		var a: Vector2 = span.call(pos)
		var x := Vector2(pos.x - axis.x, pos.y - axis.y).length()
		look = Vector3(axis.x, axis.y, pos.z + x * tan((a.x + a.y) / 2.0))
	return {pos = _a(pos), look = _a(look), lens = lens, focus = _a(opts.get("focus", sub.head))}


## a view that shows every subject: two-shot, three-shot, a group. opts as frame(); size is the tallest one's
static func group(subs: Array, size := "ms", opts := {}) -> Dictionary:
	var lo := INF
	var hi := -INF
	var mid := Vector3()
	var facing := Vector3()
	for s in subs:
		var ex := _extent(s, size)
		lo = minf(lo, ex.x)
		hi = maxf(hi, ex.y)
		mid += s.head
		facing += s.facing
	mid /= subs.size()
	facing = facing.normalized() if facing.length() > 1e-3 else Vector3(0, -1, 0)
	var flat := facing.rotated(Vector3(0, 0, 1), deg_to_rad(opts.get("side", 0.0)))
	var across := Vector3(0, 0, 1).cross(flat).normalized()
	var w := 0.0
	for s in subs:
		w = maxf(w, absf((s.head - mid).dot(across)) + s.head_r * 2.5)
	# tall enough for the heights, and wide enough for the spread in a portrait frame
	var h := maxf(hi - lo, 2.0 * w / aspect)
	var mz := (lo + hi) / 2.0
	var box := {feet = Vector3(mid.x, mid.y, mz - h / 2.0), head = Vector3(mid.x, mid.y, mz + h / 2.0), facing = facing}
	var o := opts.duplicate()
	o.focus = o.get("focus", mid)
	return _box_view(box, h, o, size)


static func _box_view(box: Dictionary, h: float, opts: Dictionary, size: String) -> Dictionary:
	var lens: float = opts.get("lens", LENS[size])
	var d := h / 2.0 / tan(vfov(lens) / 2.0)
	var target := Vector3(box.head.x, box.head.y, (box.feet.z + box.head.z) / 2.0)
	var pos := target + _dir(box, opts.get("side", 0.0), _elev(opts.get("angle", "eye"))) * d
	pos.z = maxf(pos.z, box.feet.z + 0.03)
	return {pos = _a(pos), look = _a(target), lens = lens, focus = _a(opts.focus)}


## over the shoulder: past `over`'s shoulder onto `at`, framed at size (mcu)
static func ots(over: Dictionary, at: Dictionary, size := "mcu", opts := {}) -> Dictionary:
	var to_at: Vector3 = at.head - over.head
	to_at.z = 0.0
	to_at = to_at.normalized()
	var side_v := Vector3(0, 0, 1).cross(to_at) * (1.0 if opts.get("shoulder", "right") == "right" else -1.0)
	var r: float = over.head_r
	var pos: Vector3 = over.head - to_at * 3.0 * r + side_v * 1.6 * r + Vector3(0, 0, 0.3 * r)
	var ex := _extent(at, size)
	var target := Vector3(at.head.x, at.head.y, (ex.x + ex.y) / 2.0)
	var lens: float = opts.get("lens", lens_for(ex.y - ex.x, pos.distance_to(target)))
	if not opts.has("lens"):
		# never so long that the shoulder leaves the frame: keep the near side of over's head inside the width
		var to_head: Vector3 = over.head - pos
		var off := (target - pos).angle_to(to_head) - asin(clampf(r / to_head.length(), 0.0, 1.0)) * 0.6
		lens = minf(lens, 18.0 / tan(maxf(off, 0.1)))
	return {pos = _a(pos), look = _a(target), lens = lens, focus = _a(at.head)}


## point of view: from `of`'s eyes toward a point. The shot hides of's head (and what it wears) so it cannot block
## the lens; add more names to the shot's hide for a near hand or a hat brim that should not show.
static func pov(of: Dictionary, at: Vector3, lens := 24.0) -> Dictionary:
	var to: Vector3 = at - of.head
	to.z = 0.0
	var pos: Vector3 = of.head + to.normalized() * of.head_r * 0.8 + Vector3(0, 0, 0.1 * of.head_r)
	var v := {pos = _a(pos), look = _a(at), lens = lens, focus = _a(at)}
	if of.has("node"):
		v.hide = [of.node + "/head"]
	return v


## an insert: a close look at a thing (thing()), from angle (high)
static func insert(sub: Dictionary, opts := {}) -> Dictionary:
	var o := opts.duplicate()
	o.angle = o.get("angle", "high")
	return frame(sub, "cu", o)


## establishing / master / aerial: every point in view from a side and height. opts: angle (high), side, lens (18)
static func establishing(points: Array, opts := {}) -> Dictionary:
	var b := AABB(points[0], Vector3.ZERO)
	for p in points:
		b = b.expand(p)
	var c := b.get_center()
	var box := {feet = Vector3(c.x, c.y, b.position.z), head = Vector3(c.x, c.y, b.end.z), head_r = 0.0,
		facing = opts.get("facing", Vector3(0, -1, 0))}
	var w := Vector2(b.size.x, b.size.y).length()
	var h := maxf(b.size.z, w / aspect) * float(opts.get("room", 1.15))
	var o := opts.duplicate()
	o.angle = o.get("angle", "high")
	o.lens = o.get("lens", 18.0)
	o.focus = c
	return _box_view(box, h, o, "els")


# ---------------------------------------------------------------------------------------------------------- shots
static func _a(v: Vector3) -> Array:
	return [v.x, v.y, v.z]


static func _shot(name: String, start: int, end: int, a: Dictionary, b, ease: String, opts: Dictionary) -> Dictionary:
	var s := {name = name, start = start, end = end, ease = ease, from = {pos = a.pos, look = a.look, lens = a.lens}}
	if b != null:
		s.to = {pos = b.pos, look = b.look, lens = b.lens}
		if a.has("roll") or b.has("roll"):
			s.from.roll = a.get("roll", 0.0)
			s.to.roll = b.get("roll", 0.0)
		if a.has("focus") and b.has("focus") and a.focus != b.focus:
			s.focus = {from = a.focus, to = b.focus}
		elif a.has("focus"):
			s.focus = a.focus
	else:
		if a.has("roll"):
			s.from.roll = a.roll
		if a.has("focus"):
			s.focus = a.focus
	var hide := []
	for v in [a, b]:
		if v is Dictionary and v.has("hide"):
			hide.append_array(v.hide)
	if not hide.is_empty():
		s.hide = hide
	for key in opts:
		s[key] = opts[key]
	return s


## a static shot: the camera does not move
static func static_shot(name: String, start: int, end: int, v: Dictionary, opts := {}) -> Dictionary:
	return _shot(name, start, end, v, null, "linear", opts)


## a move between two views, as given
static func move(name: String, start: int, end: int, a: Dictionary, b: Dictionary, ease := "smooth", opts := {}) -> Dictionary:
	return _shot(name, start, end, a, b, ease, opts)


## push in: the camera travels toward the subject, from one size to a closer one on the same lens
static func push_in(name: String, start: int, end: int, sub: Dictionary, from_size: String, to_size: String, opts := {}) -> Dictionary:
	var o := opts.duplicate()
	o.lens = o.get("lens", LENS[from_size])
	return _shot(name, start, end, frame(sub, from_size, o), frame(sub, to_size, o), opts.get("ease", "out"), _rest(opts))


## pull out: push_in backwards
static func pull_out(name: String, start: int, end: int, sub: Dictionary, from_size: String, to_size: String, opts := {}) -> Dictionary:
	var o := opts.duplicate()
	o.lens = o.get("lens", LENS[to_size])
	return _shot(name, start, end, frame(sub, from_size, o), frame(sub, to_size, o), opts.get("ease", "in_out"), _rest(opts))


## zoom: the camera stays, the lens changes to show the new size
static func zoom(name: String, start: int, end: int, sub: Dictionary, from_size: String, to_size: String, opts := {}) -> Dictionary:
	var a := frame(sub, from_size, opts)
	var b := frame(sub, to_size, opts)
	var pos := StykShots.v3(a.pos)
	var ex := _extent(sub, to_size)
	var o := b.duplicate()
	o.pos = a.pos
	o.lens = lens_for(ex.y - ex.x, pos.distance_to(StykShots.v3(b.look)))
	return _shot(name, start, end, a, o, opts.get("ease", "in_out"), _rest(opts))


## crash zoom: a zoom in a few frames that lands hard
static func crash_zoom(name: String, start: int, end: int, sub: Dictionary, from_size: String, to_size: String, opts := {}) -> Dictionary:
	var o := opts.duplicate()
	o.ease = "crash"
	return zoom(name, start, end, sub, from_size, to_size, o)


## dolly / truck / tracking: the camera slides sideways (right, in metres; negative goes left), aim along with it,
## up by rise, in toward the aim by push
static func dolly(name: String, start: int, end: int, v: Dictionary, right: float, opts := {}) -> Dictionary:
	var pos := StykShots.v3(v.pos)
	var look := StykShots.v3(v.look)
	var fwd := look - pos
	fwd.z = 0.0
	fwd = fwd.normalized()
	var r := fwd.cross(Vector3(0, 0, 1)).normalized()
	var d := r * right + Vector3(0, 0, opts.get("rise", 0.0)) + fwd * float(opts.get("push", 0.0))
	var b := v.duplicate()
	b.pos = _a(pos + d)
	b.look = _a(look + d)
	return _shot(name, start, end, v, b, opts.get("ease", "in_out"), _rest(opts))


## pan: the camera turns on the spot (degrees, positive to the right)
static func pan(name: String, start: int, end: int, v: Dictionary, deg: float, opts := {}) -> Dictionary:
	var pos := StykShots.v3(v.pos)
	var b := v.duplicate()
	b.look = _a(pos + (StykShots.v3(v.look) - pos).rotated(Vector3(0, 0, 1), -deg_to_rad(deg)))
	var o := _rest(opts)
	o.swing = true
	return _shot(name, start, end, v, b, opts.get("ease", "in_out"), o)


## tilt: the camera tips up (positive) or down on the spot
static func tilt(name: String, start: int, end: int, v: Dictionary, deg: float, opts := {}) -> Dictionary:
	var pos := StykShots.v3(v.pos)
	var dir := StykShots.v3(v.look) - pos
	var axis := dir.cross(Vector3(0, 0, 1)).normalized()
	var b := v.duplicate()
	b.look = _a(pos + dir.rotated(axis, deg_to_rad(deg)))
	var o := _rest(opts)
	o.swing = true
	return _shot(name, start, end, v, b, opts.get("ease", "in_out"), o)


## whip pan: a fast swing from one view's aim to another's, the camera staying at the first; nearly still at both ends
static func whip_pan(name: String, start: int, end: int, a: Dictionary, b: Dictionary, opts := {}) -> Dictionary:
	var to := b.duplicate()
	to.pos = a.pos
	var o := _rest(opts)
	o.swing = true
	return _shot(name, start, end, a, to, "whip", o)


## crane / jib: the camera rises (or sinks) by rise metres; the aim rises by look_rise (0: it keeps its subject)
static func crane(name: String, start: int, end: int, v: Dictionary, rise: float, opts := {}) -> Dictionary:
	var b := v.duplicate()
	b.pos = _a(StykShots.v3(v.pos) + Vector3(0, 0, rise))
	b.look = _a(StykShots.v3(v.look) + Vector3(0, 0, opts.get("look_rise", 0.0)))
	return _shot(name, start, end, v, b, opts.get("ease", "in_out"), _rest(opts))


## arc: the camera circles its aim point by deg (positive anticlockwise seen from above)
static func arc(name: String, start: int, end: int, v: Dictionary, deg: float, opts := {}) -> Dictionary:
	var c := StykShots.v3(v.look)
	var b := v.duplicate()
	b.pos = _a(c + (StykShots.v3(v.pos) - c).rotated(Vector3(0, 0, 1), deg_to_rad(fmod(deg, 360.0))))
	var o := _rest(opts)
	o.orbit = v.look
	o.turns = int(deg / 360.0)
	return _shot(name, start, end, v, b, opts.get("ease", "in_out"), o)


## 360-degree shot: one whole circle round the aim point
static func orbit_360(name: String, start: int, end: int, v: Dictionary, opts := {}) -> Dictionary:
	var o := opts.duplicate()
	o.ease = o.get("ease", "linear")
	return arc(name, start, end, v, 360.0 * (1.0 if opts.get("dir", 1) > 0 else -1.0), o)


## vertigo (dolly zoom): the camera travels from one distance to another while the lens keeps the subject the same
## size, so the background swells (in) or falls away (out). to_lens sets how far it travels.
static func vertigo(name: String, start: int, end: int, sub: Dictionary, size: String, to_lens: float, opts := {}) -> Dictionary:
	var a := frame(sub, size, opts)
	var o := opts.duplicate()
	o.lens = to_lens
	var b := frame(sub, size, o)
	var r := _rest(opts)
	r.vertigo = a.look
	return _shot(name, start, end, a, b, opts.get("ease", "in_out"), r)


## rack focus: the camera holds while the focus moves from one point to another on a shallow stop
static func rack_focus(name: String, start: int, end: int, v: Dictionary, from_pt: Vector3, to_pt: Vector3, opts := {}) -> Dictionary:
	var s := _shot(name, start, end, v, v, opts.get("ease", "in_out"), _rest(opts))
	s.focus = {from = _a(from_pt), to = _a(to_pt)}
	s.fstop = opts.get("fstop", 1.4)
	return s


## a curved move through several views in one shot (a one-er): views [[frame, view], ...] with absolute frames
static func keys(name: String, views: Array, opts := {}) -> Dictionary:
	var ks := []
	for fv in views:
		var v: Dictionary = fv[1]
		ks.append({f = fv[0], pos = v.pos, look = v.look, lens = v.lens, roll = v.get("roll", 0.0)})
	var s := {name = name, start = views[0][0], end = views[-1][0] + 1, ease = opts.get("ease", "linear"), keys = ks}
	if views[0][1].has("focus"):
		s.focus = views[0][1].focus
	for key in _rest(opts):
		s[key] = opts[key]
	return s


## a tracking shot through Phantom Camera: the camera follows (and / or aims at) set nodes by name with damping.
## v is where it starts; track as StykTrack documents (follow, offset, damping, look, look_damping, ...).
## With follow, offset defaults to where v sits from the target's starting point (target_at).
static func follow(name: String, start: int, end: int, v: Dictionary, track: Dictionary, opts := {}) -> Dictionary:
	var s := _shot(name, start, end, v, opts.get("to", null), opts.get("ease", "linear"), _rest(opts))
	s.erase("focus")
	s.track = track
	return s


## handheld: small quick wobble; steadicam: a slow float
static func handheld(s: Dictionary, strength := 1.0, seed := 1) -> Dictionary:
	s.shake = {amp = 0.006 * strength, rot = 0.6 * strength, freq = 1.4, seed = seed}
	return s


static func steadicam(s: Dictionary, strength := 1.0, seed := 1) -> Dictionary:
	s.shake = {amp = 0.012 * strength, rot = 0.2 * strength, freq = 0.3, seed = seed}
	return s


## dutch angle: roll the whole shot by deg
static func dutch(s: Dictionary, deg := 12.0) -> Dictionary:
	if s.has("keys"):
		for kk in s.keys:
			kk.roll = deg
	else:
		s.from.roll = deg
		if s.has("to"):
			s.to.roll = deg
	return s


## the options a move takes that belong on the shot itself
static func _rest(opts: Dictionary) -> Dictionary:
	var out := {}
	for key in ["hide", "fstop", "dof", "shake"]:
		if opts.has(key):
			out[key] = opts[key]
	return out
