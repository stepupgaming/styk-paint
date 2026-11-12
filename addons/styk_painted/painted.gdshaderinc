// Painted sets (StykPainted, addons/styk_painted/paint.py): an image model repainted a place's set from a few cameras,
// and each painting is projected back onto the set from its camera (styk_toon's paint pass). Two modes:
// - plate (paint_plate): the painting itself, untouched. The final pass shows it in place of the set, so the set looks
//   exactly as painted, with its own shapes, light and lines; figures, their outlines and text stay ours, on top.
// - ratio: the painting over our own bare render of the view, per channel. The final pass multiplies the finished
//   picture by it: our set's shapes and light, the painting's colour, tone and material.
// Surfaces marked keep (text, prints, pictures: kit.mat's keep) are never painted.
// A pixel's place in the world comes from the data pass (inverse depth) and the camera; each painting camera sees it
// when its own depth there agrees (paint_depth). Of the cameras that see it, the one facing it most squarely and closest
// wins, with soft hand-overs, so a surface keeps one painting whichever camera films it: no flicker, no smear of two.

uniform int paint_n = 0;
uniform bool paint_plate = false;
uniform sampler2DArray paint_ratio : filter_linear_mipmap;   // plate: the painting (display colour), a: set or not
                                                             // ratio: rgb painting / our render, a: how much it is trusted
uniform sampler2DArray paint_depth : filter_nearest;         // r: inverse view depth, as the data pass
uniform sampler2DArray paint_light : filter_linear_mipmap;   // plate: the light alone as drawn from that camera
uniform bool paint_shade = false;                            // plate: paint_light is there
uniform mat4 paint_w2v[16];    // world -> each painting camera's view
uniform vec2 paint_tan[16];    // tan of half its field of view, across and up
uniform vec3 paint_eye[16];
uniform mat3 cam_basis = mat3(1.0);   // this frame's camera
uniform vec3 cam_origin = vec3(0.0);
uniform vec2 cam_tan = vec2(1.0);

// The paint at world point p with world normal n. rgb: the ratio (1 where unpainted), or in plate mode the painting;
// a: how much of the spot it covers. bare_light: in plate mode, the light (luma) the painting cameras saw on the spot,
// with no figures there
vec4 painted_at(vec3 p, vec3 n, out float bare_light) {
	bare_light = 0.0;
	if (paint_n == 0)
		return vec4(1.0, 1.0, 1.0, 0.0);
	vec3 acc = vec3(0.0);
	float wsum = 0.0;
	float cover = 0.0;
	for (int i = 0; i < 16; i++) {
		if (i >= paint_n) break;
		vec4 v = paint_w2v[i] * vec4(p, 1.0);
		float zi = -v.z;
		if (zi <= 0.05) continue;
		vec2 q = vec2(0.5 + 0.5 * v.x / (zi * paint_tan[i].x), 0.5 - 0.5 * v.y / (zi * paint_tan[i].y));
		if (q.x <= 0.0 || q.x >= 1.0 || q.y <= 0.0 || q.y >= 1.0) continue;
		float di = texture(paint_depth, vec3(q, float(i))).r;
		if (di <= 0.0 || abs(1.0 / di - zi) > 0.02 * zi + 0.02) continue;   // something else is in front from there
		vec4 r = texture(paint_ratio, vec3(q, float(i)));
		float face = max(dot(n, normalize(paint_eye[i] - p)), 0.0);
		vec2 e = min(q, 1.0 - q);
		float edge = smoothstep(0.0, 0.06, min(e.x, e.y));   // another view takes over towards this one's frame edge
		// a plate shows on every set surface its camera sees, however slanted (from its camera the set is the
		// painting); slanted views only lose to squarer ones
		float sees = smoothstep(0.0, 0.004, min(e.x, e.y)) * (paint_plate ? 1.0 : smoothstep(0.15, 0.45, face));
		float w = r.a * edge * sees * max(face, 0.02) / (zi * paint_tan[i].x);   // trusted, squarer and finer wins
		w = w * w * w * w;
		acc += w * r.rgb;
		if (paint_plate && paint_shade)   // a little softened, as the film's own light is read (paint.gdshader)
			bare_light += w * textureLod(paint_light, vec3(q, float(i)), 1.0).r;
		wsum += w;
		// trust only picks between views: paint carried in from around a spot is still the whole paint there
		if (r.a > 0.0) cover = max(cover, sees);
	}
	if (wsum <= 0.0)
		return vec4(1.0, 1.0, 1.0, 0.0);
	bare_light /= wsum;
	if (paint_plate)
		return vec4(acc / wsum, cover);
	return vec4(mix(vec3(1.0), acc / wsum, cover), cover);
}

// painted_at() for a pixel of the frame: its place from the data pass's inverse depth and this frame's camera, its
// normal from the nrm pass (Blender's frame)
vec4 painted_detail(vec2 uv, float inv_z, vec3 n_blender, out float bare_light) {
	bare_light = 0.0;
	if (paint_n == 0 || inv_z <= 0.0)
		return vec4(1.0, 1.0, 1.0, 0.0);
	float z = 1.0 / inv_z;
	vec3 p = cam_origin + cam_basis * vec3((uv.x * 2.0 - 1.0) * cam_tan.x * z, (1.0 - uv.y * 2.0) * cam_tan.y * z, -z);
	return painted_at(p, normalize(vec3(n_blender.x, n_blender.z, -n_blender.y)), bare_light);
}
