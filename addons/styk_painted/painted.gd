## A place's paint, as tools/paint.py writes it: DIR/<place>/paint.json lists the painting cameras, each with its
## plate (the painting itself, a: set), its ratio (rgb: the painting over our render, a: trust) and its depth image
## (r: inverse view depth, as the data pass), and the light alone as drawn from it (the light pass's luma), by which
## plates are darkened where the film's own light falls short of it (a figure's shadow). paint.json's mode picks which
## the film uses: plate (the set shows the painting) or ratio (our set takes the painting's colour, tone and material). StykToon.set_paint hands one place's
## paint to the compositor (painted.gdshaderinc).
##
##   var painted := StykPainted.load_dir("C:/.../paint")   # {place name: {n, plate, ratio, depth, light, w2v, tan, eye}}
##   toon.set_paint(painted.get(place, {}))
class_name StykPainted
extends RefCounted

const MAX_VIEWS := 16


## every place painted under dir, by name; a place whose files do not load is left out with an error
static func load_dir(dir: String) -> Dictionary:
	var out := {}
	var da := DirAccess.open(dir)
	if da == null:
		push_error("no painted detail at " + dir)
		return out
	for place in da.get_directories():
		var p := load_place(dir.path_join(place))
		if p.get("n", 0) > 0:
			out[place] = p
	return out


static func load_place(dir: String) -> Dictionary:
	var text := FileAccess.get_file_as_string(dir.path_join("paint.json"))
	if text.is_empty():
		return {}
	var pj: Dictionary = JSON.parse_string(text)
	var views: Array = pj.get("views", [])
	var plate: bool = pj.get("mode", "ratio") == "plate"
	if views.size() > MAX_VIEWS:
		push_warning("%s: %d painting cameras, the first %d are used" % [dir, views.size(), MAX_VIEWS])
		views = views.slice(0, MAX_VIEWS)
	var ratios: Array[Image] = []
	var depths: Array[Image] = []
	var lights: Array[Image] = []   # plate mode: each view's bare light, for the figures' shadows on the plate
	var w2v := []
	var tans := []
	var eyes := []
	for v: Dictionary in views:
		var r := Image.load_from_file(dir.path_join(v.plate if plate else v.ratio))
		var d := Image.load_from_file(dir.path_join(v.depth))
		if r == null or d == null:
			push_error("%s: cannot read the paint or depth of view %s" % [dir, v.get("frame", "?")])
			return {}
		r.convert(Image.FORMAT_RGBA8 if plate else Image.FORMAT_RGBAH)
		r.generate_mipmaps()
		d.convert(Image.FORMAT_RF)
		ratios.append(r)
		depths.append(d)
		var li: Image = Image.load_from_file(dir.path_join(v.light)) if plate and v.has("light") else null
		if li != null:
			li.convert(Image.FORMAT_RH)
			li.generate_mipmaps()
			lights.append(li)
		var b: Array = v.basis
		var xf := Transform3D(Basis(Vector3(b[0], b[1], b[2]), Vector3(b[3], b[4], b[5]), Vector3(b[6], b[7], b[8])),
			Vector3(v.origin[0], v.origin[1], v.origin[2]))
		w2v.append(Projection(xf.affine_inverse()))
		var t := tan(deg_to_rad(float(v.hfov)) * 0.5)
		tans.append(Vector2(t, t * float(d.get_height()) / d.get_width()))
		eyes.append(xf.origin)
	if ratios.is_empty():
		return {}
	var ra := Texture2DArray.new()
	var de := Texture2DArray.new()
	if ra.create_from_images(ratios) != OK or de.create_from_images(depths) != OK:
		push_error("%s: the painting cameras' images must all be one size" % dir)
		return {}
	var la: Texture2DArray = null
	if lights.size() == ratios.size():   # every view's, or the plates go without the figures' shadows
		la = Texture2DArray.new()
		if la.create_from_images(lights) != OK:
			la = null
	return {n = ratios.size(), plate = plate, ratio = ra, depth = de, light = la, w2v = w2v, tan = tans, eye = eyes}


## what tools/paint.py needs of a camera to paint from it: its transform and horizontal field of view
static func camera_json(xf: Transform3D, hfov: float, env: String) -> Dictionary:
	var b := xf.basis
	return {basis = [b.x.x, b.x.y, b.x.z, b.y.x, b.y.y, b.y.z, b.z.x, b.z.y, b.z.z],
		origin = [xf.origin.x, xf.origin.y, xf.origin.z], hfov = hfov, env = env}


## The name paint.py gives a piece of the set in a view's contents: its own or a parent's "paint_name" meta (a built
## set names its walls, floor, ground and scatter), else a dress prop's name (its root is dress_<prop>_<i>_<j>) or a
## film prop's (sign_, screen_, piece_), else, with by_node_name, the first node up from it that was named by hand (a
## plain Godot film's own names), else "".
static func name_of(n: Node, stop: Node = null, by_node_name := false) -> String:
	var dress := RegEx.create_from_string("^dress_(.+)_[0-9]+_[0-9]+$")
	var prop := RegEx.create_from_string("^(sign|screen|piece)_(.+)$")
	var made := RegEx.create_from_string("^(@|MeshInstance3D|MultiMeshInstance3D|Node3D|StaticBody3D|Sprite3D|Label3D)")
	var hand := ""
	var x := n
	while x != null and x != stop:
		if x.has_meta("paint_name"):
			return str(x.get_meta("paint_name"))
		var m := dress.search(x.name)
		if m:
			return m.get_string(1)
		m = prop.search(x.name)   # a film's own props (StykProps): a sign, a screen, a piece
		if m:
			return {sign = "a sign", screen = "a TV screen"}.get(m.get_string(1), m.get_string(2))
		if by_node_name and hand == "" and made.search(x.name) == null:
			hand = str(x.name).to_snake_case()
		x = x.get_parent()
	return hand


## Every GeometryInstance3D under root that `pick` takes, numbered by its name_of (the same name, the same number, from
## 1): {names = {"1": name, ...}, marks = [[instance, number], ...]}. Views draw these numbers as their ids pass, for
## paint.py to tell H3 what each view shows.
static func number(root: Node, pick: Callable, by_node_name := false) -> Dictionary:
	var ids := {}
	var names := {}
	var marks := []
	for gi: GeometryInstance3D in root.find_children("*", "GeometryInstance3D", true, false):
		if not pick.call(gi):
			continue
		var nm := name_of(gi, root, by_node_name)
		if nm == "":
			continue
		if not ids.has(nm):
			ids[nm] = ids.size() + 1
			names[str(ids[nm])] = nm
		marks.append([gi, ids[nm]])
	return {names = names, marks = marks}
