## A film in a plain Godot scene: put this (or a script that extends it) on the scene's root. It poses the scene for any
## frame: every AnimationPlayer under it is seeked to the frame's time, and the camera is placed by the shots (StykShots,
## built with StykCam), so any frame renders alone and the same every time. addons/styk_painted/capture.gd draws it.
##
##   extends StykFilm
##   func build() -> void:                       # called once the scene is in the tree
##       var guy := StykCam.figure(space_node(), $Guy, Vector3(0, 0, 1))
##       shots = [StykCam.push_in("a", 0, 90, guy, "ms", "cu"), StykCam.static_shot("b", 90, 150, StykCam.frame(guy, "full"))]
##
## Without shots the camera stays as the scene placed it (animate it with an AnimationPlayer instead). Nodes in the group
## "figure" are left out of the views the painter paints from and are never painted; nodes in "paint_keep" (text,
## signs, pictures) are never painted either. place_at() names the place a frame is in, when one film moves between
## places that are painted apart.
class_name StykFilm
extends Node3D

@export var fps := 30.0
@export var length := 150                       ## frames
@export var size := Vector2i(1080, 1920)        ## the film's frame in pixels
@export var camera: Camera3D                    ## the film's camera (the first Camera3D under the root if left empty)
@export var place_name := "set"                 ## the painted place every frame is in (override place_at for more)

var shots: Array = []      ## StykShots shot dicts, in set coordinates (StykShots documents every field)
var cues: Array = []       ## shake cues {f, amp, frames}: the camera's jolt from an impact
var space := StykShots.Z_UP   ## set coordinates -> this scene's world
var lens = null            ## this frame's {lens, fstop, focus}, or null
var _space_node: Node3D
var _hidden: Array[Node3D] = []
var _playing := false
var _t := 0.0


## Run as the main scene (F5 / F6), the film plays in real time with its shots, to watch it before drawing it. A script
## that extends StykFilm and has its own _ready() calls super() first.
func _ready() -> void:
	if get_tree().current_scene == self:
		start()
		_playing = true


func _process(delta: float) -> void:
	if _playing:
		_t = fmod(_t + delta * fps, float(length))
		place(int(_t))


## make the shots; the scene is in the tree and posed at frame 0
func build() -> void:
	pass


## the place frame f is in
func place_at(_f: int) -> String:
	return place_name


## the figures, left out of the views to paint and never painted
func figures() -> Array[Node]:
	return get_tree().get_nodes_in_group("figure")


## a node placed at set coordinates (z up), for StykCam.figure and the like
func space_node() -> Node3D:
	if _space_node == null:
		_space_node = Node3D.new()
		_space_node.name = "set_space"
		add_child(_space_node)
		_space_node.global_transform = space
	return _space_node


func start() -> void:
	if camera == null:
		camera = find_children("*", "Camera3D", true, false).front() as Camera3D
	assert(camera != null, "StykFilm: no Camera3D in the film")
	camera.current = true
	camera.keep_aspect = Camera3D.KEEP_WIDTH   # fov is across, as the shots' lenses are
	pose(0)
	build()


## everything but the camera at frame f: every AnimationPlayer at the frame's time
func pose(f: int) -> void:
	for ap: AnimationPlayer in find_children("*", "AnimationPlayer", true, false):
		var anim := ap.assigned_animation if ap.assigned_animation != "" else ap.autoplay
		if anim == "":
			continue
		ap.callback_mode_process = AnimationMixer.ANIMATION_CALLBACK_MODE_PROCESS_MANUAL
		if ap.assigned_animation != anim:
			ap.play(anim)
		ap.seek(f / fps, true)


## the whole film at frame f: posed, the camera placed, the shot's hidden nodes hidden
func place(f: int) -> void:
	pose(f)
	if shots.is_empty():
		return
	var s := StykShots.shot_at(shots, f)
	var c := StykShots.frame(s, f, fps, cues, space)
	camera.global_transform = c.xform
	camera.fov = c.fov
	lens = c.lens
	var now: Array[Node3D] = []
	for nm in s.get("hide", []):
		# a path from the film's root, or from the set space (StykCam.figure's node), or a name anywhere under it
		var n: Node3D = null
		if "/" in nm:
			n = get_node_or_null(nm) as Node3D
			n = n if n != null else space_node().get_node_or_null(nm) as Node3D
		else:
			n = find_child(nm, true, false) as Node3D
		if n != null:
			now.append(n)
	for n in _hidden:
		if is_instance_valid(n) and not n in now:
			n.visible = true
	for n in now:
		n.visible = false
	_hidden = now


## the shots' frames, for the painter: {total, shots: [{start, end}]}
func info() -> Dictionary:
	var out := []
	for s in shots:
		out.append({start = int(s.start), end = int(s.end)})
	if out.is_empty():
		out.append({start = 0, end = length})
	return {total = length, shots = out, fps = fps, size = [size.x, size.y]}
