# Styk Paint

Film cameras and an AI set painter for Godot 4.7.

Build a set out of plain boxes and flat colours. Write the camera as named shots ("push in from a full shot to a medium
shot"). The painter then repaints the set with **MiniMax-H3-Image**, so it looks like a painted animated film, and
projects those paintings back onto the 3D set. Every frame of the film shows the same paint from any angle, with your
characters, their shadows and your text drawn on top.

It has two parts:

- **`addons/styk_camera`**: the camera system.
  - `StykCam` builds shots by name: sizes, angles and around twenty moves.
  - `StykShots` places the camera for any frame.
  - `StykFilm` turns a plain Godot scene into a film you can render frame by frame.
- **`addons/styk_painted`**: the painter.
  - `paint.py` picks the views to paint, paints them through ComfyUI and checks the results.
  - `capture.gd` renders the views and the final frames.
  - The shaders project the paint onto the set.

## What you need

- **Godot 4.7** with the Forward+ renderer.
- **Python 3.10 or newer**, then `pip install -r requirements.txt` (numpy and OpenCV).
- **ComfyUI** with the **MiniMax-H3-Image** image edit model. One view takes about a minute on a 16 GB card.
- **ffmpeg**, optional, to turn the frames into a video.

## Set up

1. **Point the scripts at Godot.** Set `GODOT` to the Godot 4.7 executable (on Windows, the `_console.exe` build), or
   put `godot` on your PATH.
2. **Register the classes.** Open the project once in the Godot editor, or run:

   ```bash
   python tools/godot.py --headless --import
   ```

3. **Give the painter a ComfyUI workflow.**
   - In ComfyUI, open the MiniMax H3 image edit template.
   - Type `{prompt}` as the whole prompt text.
   - Turn its prompt expansion off. Otherwise a language model rewrites the painter's careful prompt.
   - Export it with **Export (API)** to `addons/styk_painted/h3_edit_api.json`. To keep it somewhere else, set
     `COMFY_WORKFLOW` to its path.

   For each view, the painter fills in the prompt, the input picture, the seed and the megapixel size.
4. **Point the painter at ComfyUI.** Start ComfyUI and set `COMFY_URL`, for example to `http://127.0.0.1:8188`.

## Try the demo

`demo/room.tscn` is a small room with a figure, filmed in three shots: a wide shot, a push-in and an arc.

1. Render it unpainted, to see the shots:

   ```bash
   python tools/godot.py -s res://addons/styk_painted/capture.gd -- --film res://demo/room.tscn --out out/plain
   ```

2. Paint its set:

   ```bash
   python addons/styk_painted/paint.py --scene res://demo/room.tscn --out out/paint --frames auto --note "a cosy study with a wooden table, a bookshelf, a rug and a cabinet"
   ```

3. Render it with the paint:

   ```bash
   python tools/godot.py -s res://addons/styk_painted/capture.gd -- --film res://demo/room.tscn --out out/painted --paint out/paint
   ```

4. Make a video:

   ```bash
   ffmpeg -framerate 30 -i out/painted/f%05d.png -c:v libx264 -pix_fmt yuv420p -crf 16 room.mp4
   ```

## Make your own film

Build a normal Godot scene, then:

- **Root script.** Give the root node a script that `extends StykFilm`. In its `build()`, fill `shots` (see
  `demo/room.gd`). The root's exported properties set the frame rate, the length in frames and the frame size.
- **Camera.** Put one `Camera3D` in the scene. With shots, `StykFilm` places it on every frame. Without shots, it stays
  where you put it, or wherever an `AnimationPlayer` moves it.
- **Motion.** Animate everything else with `AnimationPlayer`s. Each frame seeks every player to that frame's time, so
  any frame renders on its own and comes out the same every time.
- **Characters.** Put them in the group **`figure`**. Figures never get painted and are left out of the views the
  painter works from. `StykCam.figure()` finds the head (a child named `head`, whose scale is its radius) and the feet.
- **Names.** Name the set's nodes by what they are (`Table`, `Bookshelf`, `Rug`). The painter tells H3 what each view
  shows from those names, so the note only needs to say the place and its light. A `paint_name` meta on a node
  overrides its name; unnamed nodes (`MeshInstance3D`, `@...`) are skipped.
- **Things to keep.** Put signs, text, posters and pictures in the group **`paint_keep`**. They keep their own look.
- **Lighting.** Use **Linear** tonemapping on the environment, so the paint reaches the screen as it was painted.
- **More than one place.** If the film moves between places that should be painted separately, override `place_at(f)`
  to return each frame's place name. Then pass one note per place, for example
  `--note "kitchen=a 1950s kitchen;yard=a back yard at dusk"`. Give each place its own light ("at night, lit by one
  lamp"): H3 lets light words leak, so a night place says it is night and a day place says nothing about night.

### Shots

All positions are in set coordinates: metres, z up, the front toward -y. `StykFilm.space_node()` gives you that frame.

- **Subjects.** A subject is either `StykCam.figure(space_node(), $Hero, front)` or `StykCam.thing(point, size)`.
- **Sizes**, widest first: `els`, `ls`, `full`, `cowboy`, `ms`, `mcu`, `cu`, `choker`, `ecu`.
- **Angles:** `eye`, `high`, `low`, `worm`, `overhead`, or `{elev = degrees}`. The `side` option turns the camera around
  the subject: 0 is head-on, 35 is three-quarter, 90 is profile.
- **Views:** `frame`, `group`, `ots`, `pov`, `insert`, `establishing`.
- **Moves:** `static_shot`, `push_in`, `pull_out`, `zoom`, `crash_zoom`, `dolly`, `pan`, `tilt`, `whip_pan`, `crane`,
  `arc`, `orbit_360`, `vertigo`, `rack_focus` and `keys`, a curved one-take move through several views.
- **Modifiers:** `handheld()`, `steadicam()` and `dutch()` wrap any shot.

Each move documents its own options in `addons/styk_camera/cam.gd`. `addons/styk_camera/shots.gd` documents every field
of a shot.

## What the painter does

`--frames auto` runs the whole job:

1. **Choose views.** It samples frames from every shot. It renders each place's set as bare views, wider than the shots
   (`--overscan`), with no figures. Then it picks the views that best cover what the film shows. A view counts only
   when it sees a surface squarely enough not to smear it.
2. **Paint, one view after another.** H3 repaints each view as a material change: the same objects with painted
   detail. From the second view of a place on, H3 starts from the view with the earlier paint already projected onto
   it, so it continues the same paint instead of starting fresh.
3. **Check every painting.** A painting whose shapes don't match the view is redrawn with a new seed, then dropped. A
   close-up H3 can't read is drawn again from further back, and only its middle is kept. That middle is enlarged, and
   H3 adds the fine detail a close painting would have (`--sharpen`).
4. **Hide the joins.** A later painting only fills in what the earlier ones lack, fades out towards its frame edge,
   and has its colour matched to the paint already there. The coverage report measures any visible join that is left.
5. **Write the place.** It writes `DIR/<place>/paint.json` with every painting, depth map and camera. Then it reports
   how much of each sampled frame is unpainted, smeared, or shows a visible join between paintings. Frames over the
   limits are marked, and their pictures go to `DIR/bare/`. If any frame still shows bare set, `paint.py` exits with
   code 3, so a build script can stop before rendering.

Paintings are cached, so running it again only paints what changed. Other modes:

- `--draft` paints every view small (1 megapixel, about a quarter of the time) so you can try notes, styles and
  seeds quickly. When one looks right, `--final` with the same `--out` paints each view again at full size from its
  draft. It keeps the draft's picture and colours and adds the full-size detail. Changing `--note`, `--style` or
  `--seed` and running `--draft` again only repaints what changed.
- `--sharpen dlss` sharpens close-ups with NVIDIA DLSS 5 instead of H3. Set `DLSS_URL` to a ComfyUI that has the
  `RH_DLSS5Enhance` node (from RH-RunningHub/ComfyUI-RH-DLSS5, installed separately). It is quicker but adds much
  less detail than H3, the default.
- `--coverage` reports on an existing paint folder.
- `--frames 0,40,90` paints chosen frames instead of planning.
- `--style` changes the look H3 is asked for.
- `--seed` changes the seed.

Run `python addons/styk_painted/paint.py --help` for every option.

When the film is rendered with `--paint`, the set shows the paintings. Where your lights cast a shadow that the
painting camera didn't see, the paint darkens, so characters still cast shadows on the painted set. H3 tends to grey
colour, so a painting is shown at least as colourful as the set it replaces (`plate_colour` on
`painted_surface.gdshader`; 0 shows it as painted).

## Limits

- **Plate mode only.** A plain Godot scene shows the paintings as painted. The painter's `ratio` mode keeps your own
  shading and takes only the painting's colour and material. It needs a separate compositor that isn't included.
- **16 painting cameras per place.**
- **Figures aren't painted.** Only the set is.
- **No tracking shots.** Shots that follow a moving node need a tracking camera that isn't included. Use `keys` or an
  animated camera instead.

## Credits

- Painting by MiniMax-H3-Image, run through ComfyUI.
- The idea of projecting image-model paintings back onto a 3D set from their cameras is also used by StableGen for
  Blender.
- Everything in this repository is our own code, under the MIT licence (see `LICENSE`).
