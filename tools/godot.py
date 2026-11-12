"""Run Godot (4.7 or newer) on this project.

  python tools/godot.py <godot args...>

GODOT names the Godot executable (a console build on Windows, so its output shows); without it, `godot` on the PATH.
Godot reads nothing from the console, so its input is closed: a script that fails then ends instead of waiting at the
debugger. addons/styk_painted/paint.py draws its views and frames through run().
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(args, **kw):
    """Godot's exit code. kw goes to subprocess.call (stdout, stderr)."""
    exe = os.environ.get("GODOT") or shutil.which("godot") or shutil.which("godot4")
    if not exe:
        sys.exit("no Godot: set GODOT to the Godot 4.7 executable")
    return subprocess.call([exe, "--path", ROOT, *args], cwd=ROOT, stdin=subprocess.DEVNULL, **kw)


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
