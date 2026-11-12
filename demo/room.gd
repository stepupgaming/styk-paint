## A small room and one figure, filmed in three shots: the example film for the painter. Draw it with
##   python tools/godot.py -s res://addons/styk_painted/capture.gd -- --film res://demo/room.tscn --out out/frames
## and paint it with
##   python addons/styk_painted/paint.py --scene res://demo/room.tscn --out out/paint --frames auto --note "a cosy study"
extends StykFilm


func build() -> void:
	# the figure walks in during the wide shot and stands from frame 36: frame it where it stands, then go back to 0.
	# Its front is +z (Godot), toward the open side of the room.
	pose(40)
	var guy := StykCam.figure(space_node(), $Guy, Vector3(0, 0, 1))
	pose(0)
	# set coordinates (metres, z up, the open front toward -y): the room's corners
	var room := [Vector3(-3, -1, 0), Vector3(3, -1, 0), Vector3(-3, 3, 0), Vector3(3, 3, 2.6)]
	shots = [
		StykCam.static_shot("wide", 0, 40, StykCam.establishing(room)),
		# a portrait frame is narrow: longer lenses than the size's default keep a close figure from bulging
		StykCam.push_in("push", 40, 90, guy, "full", "ms", {lens = 35.0}),
		StykCam.arc("arc", 90, 150, StykCam.frame(guy, "ms", {side = 25.0, lens = 40.0}), 40.0),
	]
