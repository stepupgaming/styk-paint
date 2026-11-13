// capture.gd's data pass, as styk_toon's data pass writes it for tools/paint.py: r = inverse view depth (0 is the sky:
// the pass clears to black), g = 0.1 on kept surfaces (text, signs, pictures: group "paint_keep") that are never painted.
shader_type spatial;
render_mode unshaded, cull_disabled, shadows_disabled, fog_disabled;

uniform bool keep = false;
uniform float pid = 0.0;   // above 0: r = pid, g = 2, which named piece of the set this is (paint.py's view contents)

void fragment() {
	ALBEDO = pid > 0.0 ? vec3(pid, 2.0, 0.0) : vec3(1.0 / max(-VERTEX.z, 0.05), keep ? 0.1 : 0.0, 0.0);
}
