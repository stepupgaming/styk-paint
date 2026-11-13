## Draw a film made in a plain Godot scene (its root a StykFilm): its frames, or the views tools/paint.py paints from.
##   godot --path . -s res://addons/styk_painted/capture.gd -- --film res://my_film.tscn --out DIR
##       [--frames 0-29,45] [--size WxH] [--paint PAINT_DIR]
##       [--views [--overscan F] [--pull M]]    [--info FILE]
## Frames are written to DIR/f%05d.png. --paint: the paintings paint.py made (DIR/<place>/paint.json) shown on the set
## (plate mode): every surface but the figures (group "figure") and kept things (group "paint_keep") takes the
## painting projected from its cameras (painted_surface.gdshader, as its material_overlay), darkened where the film's
## own light falls short of what the painting camera saw (the figures' shadows).
## --views: the frames as bare views of their places for paint.py: no figures, --overscan times as wide, --pull metres
## further back. Beside each frame's PNG go its depth and kind (f%05d.depth.exr: r inverse view depth, 0 the sky; g 0.1
## on kept things), its light (f%05d.light.exr: the set as lit, linear), its ids (f%05d.ids.exr: r the number of the
## named piece of the set each pixel shows, g 2 where one is; StykPainted.name_of, the nodes' own names here) and its
## camera (DIR/views.json, with the names).
## --info: the film's length and shots as JSON, for paint.py's choice of frames to judge.
extends SceneTree

const DATA := preload("res://addons/styk_painted/capture_data.gdshader")
const SURFACE := preload("res://addons/styk_painted/painted_surface.gdshader")

var film: StykFilm
var vp: SubViewport       ## the film as it shows
var dvp: SubViewport      ## the light and data passes (linear, float), the same world
var dcam: Camera3D
var w := 0
var h := 0
var painted := {}         ## place -> its paint (StykPainted.load_dir)
var overlay: ShaderMaterial


func _initialize() -> void:
	_run.call_deferred()   # once the root is in the tree, so the film's nodes have world positions


func _arg(name: String, def: String) -> String:
	var a := OS.get_cmdline_user_args()
	var i := a.find("--" + name)
	return a[i + 1] if i >= 0 and i + 1 < a.size() else def


func _flag(name: String) -> bool:
	return OS.get_cmdline_user_args().has("--" + name)


func _list(spec: String, total: int) -> Array[int]:
	var fr: Array[int] = []
	for part in spec.split(",", false):
		var ab := part.split("-")
		for x in range(int(ab[0]), int(ab[ab.size() - 1]) + 1):
			if x >= 0 and x < total:
				fr.append(x)
	return fr


func _run() -> void:
	var path := _arg("film", "")
	var ps := load(path) as PackedScene
	if ps == null:
		push_error("capture: no film scene at '%s'" % path)
		quit(2)
		return
	var node := ps.instantiate()
	if not node is StykFilm:
		push_error("capture: the root of %s is not a StykFilm" % path)
		quit(2)
		return
	film = node
	var sz := _arg("size", "%dx%d" % [film.size.x, film.size.y]).split("x")
	w = int(sz[0])
	h = int(sz[1])
	vp = _viewport(false)
	vp.world_3d = World3D.new()
	root.add_child(vp)
	vp.add_child(film)
	film.start()
	var info := _arg("info", "")
	if info != "":
		var fi := FileAccess.open(info, FileAccess.WRITE)
		fi.store_string(JSON.stringify(film.info(), "	"))
		fi.close()
		quit(0)
		return
	dvp = _viewport(true)
	dvp.world_3d = vp.world_3d
	dvp.render_target_update_mode = SubViewport.UPDATE_DISABLED
	dcam = Camera3D.new()
	dvp.add_child(dcam)
	root.add_child(dvp)
	dcam.current = true
	var paint_dir := _arg("paint", "")
	if paint_dir != "":
		painted = StykPainted.load_dir(paint_dir)
		overlay = ShaderMaterial.new()
		overlay.shader = SURFACE
		_lay(film)
	var out := _arg("out", "")
	DirAccess.make_dir_recursive_absolute(out)
	var todo := _list(_arg("frames", "0-%d" % (film.length - 1)), film.length)
	if _flag("views"):
		await _views(out, todo)
	else:
		for f in todo:
			film.place(f)
			_paint(f)
			var img := await _draw(vp)
			img.convert(Image.FORMAT_RGB8)
			img.save_png(out.path_join("f%05d.png" % f))
		print("wrote %d frames (%dx%d) to %s" % [todo.size(), w, h, out])
	quit(0)


func _viewport(hdr: bool) -> SubViewport:
	var v := SubViewport.new()
	v.size = Vector2i(w, h)
	v.use_hdr_2d = hdr
	v.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	if hdr:
		v.msaa_3d = Viewport.MSAA_DISABLED
		v.screen_space_aa = Viewport.SCREEN_SPACE_AA_DISABLED
		v.use_taa = false
		v.use_debanding = false
	return v


## n or one of its parents is in the group
func _in(n: Node, group: String) -> bool:
	while n != null:
		if n.is_in_group(group):
			return true
		n = n.get_parent()
	return false


## the paint on every set surface: not on the figures, not on kept things
func _lay(n: Node) -> void:
	for gi: GeometryInstance3D in n.find_children("*", "GeometryInstance3D", true, false):
		if not _in(gi, "figure") and not _in(gi, "paint_keep"):
			gi.material_overlay = overlay


## the paint of frame f's place, or none (paint_n 0 draws nothing)
func _paint(f: int, on := true) -> void:
	if overlay == null:
		return
	var p: Dictionary = painted.get(film.place_at(f), {}) if on else {}
	overlay.set_shader_parameter("paint_n", p.get("n", 0))
	overlay.set_shader_parameter("paint_plate", true)
	overlay.set_shader_parameter("paint_shade", p.get("light") != null)
	if p.get("n", 0) == 0:
		return
	if not p.get("plate", false):
		push_warning("capture: %s was painted in ratio mode; a plain Godot scene shows plates only" % film.place_at(f))
	if p.get("light") != null:
		overlay.set_shader_parameter("paint_light", p.light)
	overlay.set_shader_parameter("paint_ratio", p.ratio)
	overlay.set_shader_parameter("paint_depth", p.depth)
	overlay.set_shader_parameter("paint_w2v", p.w2v)
	overlay.set_shader_parameter("paint_tan", p.tan)
	overlay.set_shader_parameter("paint_eye", p.eye)


func _draw(v: SubViewport) -> Image:
	await process_frame
	RenderingServer.force_draw(false)
	return v.get_texture().get_image()


## the pass camera where the film's is; env: the light pass's (the film's own, linear) or the data pass's (black)
func _pass_camera(env: Environment) -> void:
	var c := film.camera
	dcam.global_transform = c.global_transform
	dcam.keep_aspect = c.keep_aspect
	dcam.fov = c.fov
	dcam.near = c.near
	dcam.far = c.far
	dcam.cull_mask = c.cull_mask
	dcam.environment = env


func _views(out: String, todo: Array[int]) -> void:
	var cams := []
	var over := float(_arg("overscan", "1"))
	var pull := float(_arg("pull", "0"))
	var base: Environment = film.camera.environment if film.camera.environment != null else vp.world_3d.environment
	var lit := base.duplicate() as Environment if base != null else Environment.new()
	lit.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	lit.tonemap_exposure = 1.0
	lit.glow_enabled = false
	lit.adjustment_enabled = false
	var black := Environment.new()
	black.background_mode = Environment.BG_COLOR
	black.background_color = Color.BLACK
	black.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	black.ambient_light_source = Environment.AMBIENT_SOURCE_DISABLED
	black.reflected_light_source = Environment.REFLECTION_SOURCE_DISABLED
	var data := ShaderMaterial.new()
	data.shader = DATA
	var keep := ShaderMaterial.new()
	keep.shader = DATA
	keep.set_shader_parameter("keep", true)
	# the set's named pieces (by StykPainted.name_of, node names too), drawn by number as each view's ids pass
	var ids := StykPainted.number(film, func(gi: GeometryInstance3D) -> bool: return not _in(gi, "figure"), true)
	var id_mats := {}
	for m: Array in ids.marks:
		if not id_mats.has(m[1]):
			id_mats[m[1]] = ShaderMaterial.new()
			id_mats[m[1]].shader = DATA
			id_mats[m[1]].set_shader_parameter("pid", float(m[1]))
	for f in todo:
		film.place(f)
		var cam := film.camera
		# wider than the shot by --overscan, so a painting also covers the set just outside its frame
		var fov := rad_to_deg(2.0 * atan(tan(deg_to_rad(cam.fov) * 0.5) * over))
		var xf := cam.global_transform
		xf.origin += xf.basis.z.normalized() * pull   # back along the line of sight (the camera looks down -z)
		cam.global_transform = xf
		cam.fov = fov
		var shown := {}
		for fig in film.figures():
			if fig is Node3D:
				shown[fig] = fig.visible
				fig.visible = false
		_paint(f)
		vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
		var img := await _draw(vp)
		img.convert(Image.FORMAT_RGB8)
		img.save_png(out.path_join("f%05d.png" % f))
		# the light and data passes: no paint, the set as lit and its depth
		_paint(f, false)
		vp.render_target_update_mode = SubViewport.UPDATE_DISABLED
		dvp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
		_pass_camera(lit)
		var li := await _draw(dvp)
		li.convert(Image.FORMAT_RGBAF)
		li.save_exr(out.path_join("f%05d.light.exr" % f))
		var was := {}
		for gi: GeometryInstance3D in film.find_children("*", "GeometryInstance3D", true, false):
			was[gi] = gi.material_override
			gi.material_override = keep if _in(gi, "paint_keep") else data
		_pass_camera(black)
		var d := await _draw(dvp)
		d.convert(Image.FORMAT_RGBAF)
		d.save_exr(out.path_join("f%05d.depth.exr" % f))
		for gi in was:   # the ids pass: r the number of the named piece each pixel shows, g 2 where one is
			gi.material_override = data
		for m: Array in ids.marks:
			(m[0] as GeometryInstance3D).material_override = id_mats[m[1]]
		var di := await _draw(dvp)
		di.convert(Image.FORMAT_RGBAF)
		di.save_exr(out.path_join("f%05d.ids.exr" % f))
		for gi in was:
			gi.material_override = was[gi]
		dvp.render_target_update_mode = SubViewport.UPDATE_DISABLED
		for fig in shown:
			fig.visible = shown[fig]
		var c := StykPainted.camera_json(xf, fov, film.place_at(f))
		c.merge({frame = f, image = "f%05d.png" % f, depth = "f%05d.depth.exr" % f, light = "f%05d.light.exr" % f,
			ids = "f%05d.ids.exr" % f, names = ids.names, w = w, h = h})
		cams.append(c)
	var fa := FileAccess.open(out.path_join("views.json"), FileAccess.WRITE)
	fa.store_string(JSON.stringify({views = cams}, "	"))
	fa.close()
	print("wrote %d views (%dx%d) to %s" % [cams.size(), w, h, out])
