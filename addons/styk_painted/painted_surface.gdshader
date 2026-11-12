// A place's paintings on a plain Godot scene's set (capture.gd puts this on every set surface as its material_overlay):
// the painting itself (plate mode) projected from its cameras, on top of the surface's own colour. It is darkened where
// the film's own light falls short of what the painting camera saw there (a figure stands in it): the figures' shadows.
// Show it with Godot's Linear tonemapping, so the painting reaches the screen as it was painted.
shader_type spatial;
render_mode unshaded, blend_mix, depth_draw_never, cull_back, shadows_disabled, fog_disabled;

#include "res://addons/styk_painted/painted.gdshaderinc"

uniform sampler2D screen : hint_screen_texture, filter_linear;
uniform float shade_from = 0.9;   // light this share of the painting camera's or more is no shadow
uniform float shade = 0.85;       // how much of the shadow a plate takes

varying vec3 world_pos;
varying vec3 world_nrm;

float lum(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

vec3 to_linear(vec3 c) {
	return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(vec3(0.04045), c));
}

void vertex() {
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	world_nrm = (MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz;
}

void fragment() {
	float bare;
	vec4 r = painted_at(world_pos, normalize(world_nrm), bare);
	if (r.a <= 0.0)
		discard;
	vec3 c = to_linear(r.rgb);
	if (paint_shade && bare > 0.0) {
		// the screen holds the set as the film lights it, linear, as the light pass saw it from the painting camera
		float k = clamp(lum(texture(screen, SCREEN_UV).rgb) / (bare * shade_from), 0.0, 1.0);
		c *= mix(1.0, k, shade);
	}
	ALBEDO = c;
	ALPHA = r.a;
}
