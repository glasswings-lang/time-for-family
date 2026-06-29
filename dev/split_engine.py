"""One-off: split time_for_family.pyw into tff_engine.py (brain) + a new
time_for_family.pyw (screen) that imports the engine.

Boundaries are found by the existing `# ===== ... =====` section markers so
this is robust to line-number drift. Run from project root:

    python dev/split_engine.py

Writes tff_engine.py and rewrites time_for_family.pyw. A backup must already
exist (it does — see tff_backups). Re-runnable: it reads the CURRENT
time_for_family.pyw, so only run it once on the un-split file.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "time_for_family.pyw"

STDLIB_IMPORTS = """\
import ctypes
import json
import math
import os
import random
import re
import shutil
import struct
import sys
import time
import uuid
import wave
from datetime import date
from pathlib import Path
"""

ENGINE_HEADER = '''\
"""
Time for Family -- game engine (the "brain").

Pure game logic: world state, creatures, breeding, aging, rooms, time,
save/load, content loading, and the text / name / announcement helpers.
No wxPython, no sound, no screen -- this module runs headless and is
imported both by the windowed game (time_for_family.pyw) and by any
headless driver (e.g. an AI player).

Split out of the original single-file time_for_family.pyw (June 2026).
The shared mutable shelves (SETTINGS, SPECIES_DATA, ROOM_TYPES,
ANNOUNCEMENTS, the item / name pools) are refilled IN PLACE by the
loaders so the importing module always sees live data -- never rebind
them with `=`; mutate with .clear()/.update()/[:]= instead.
"""
'''

UI_HEADER = '''\
"""
Time for Family -- a cozy creature-park sim (windowed app).

The wxPython presentation layer: windows, panels, dialogs, sound, and
NVDA announcements. All game rules live in tff_engine.py, imported
below. Launch this file to play.

Save lives in state.json next to this file. Sounds are auto-generated
tones in assets/sounds/ on first run; replace any with a real .wav of
the same filename to swap.
"""
'''

UI_IMPORT_GLUE = '''\
import wx
import wx.adv

import tff_engine
# Pull every engine name -- including the single-underscore helpers that a
# plain `from tff_engine import *` would skip -- into this module's globals,
# so the unqualified engine calls throughout the UI keep resolving. The
# shelves come across as references to the SAME objects the engine mutates,
# so a reload in the engine is visible here with no rebinding.
globals().update({_k: _v for _k, _v in vars(tff_engine).items()
                  if not _k.startswith("__")})
'''


def find_marker(lines, prefix):
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            return i
    raise SystemExit(f"marker not found: {prefix!r}")


def main():
    text = SRC.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # Locate the import block end and the section boundaries.
    i_wxadv = next(i for i, l in enumerate(lines) if l.strip() == "import wx.adv")
    i_sound = find_marker(lines, "# ===== Sound generation")
    i_datamodel = find_marker(lines, "# ===== Data model")
    i_ui = find_marker(lines, "# ===== UI =====")

    if not (i_wxadv < i_sound < i_datamodel < i_ui):
        raise SystemExit("markers out of expected order; aborting")

    engine_top = lines[i_wxadv + 1:i_sound]      # constants, globals, loaders
    presentation = lines[i_sound:i_datamodel]    # sound + NVDA  -> UI
    engine_model = lines[i_datamodel:i_ui]       # data model + actions + build
    ui_body = lines[i_ui:]                        # UI classes + main()

    engine_text = (
        ENGINE_HEADER
        + "\n"
        + STDLIB_IMPORTS
        + "\n"
        + "".join(engine_top)
        + "".join(engine_model)
    )
    ui_text = (
        UI_HEADER
        + "\n"
        + STDLIB_IMPORTS
        + "\n"
        + UI_IMPORT_GLUE
        + "\n"
        + "".join(presentation)
        + "".join(ui_body)
    )

    (ROOT / "tff_engine.py").write_text(engine_text, encoding="utf-8")
    SRC.write_text(ui_text, encoding="utf-8")

    print("Split complete.")
    print(f"  imports end at line   {i_wxadv + 1}")
    print(f"  sound/NVDA block      lines {i_sound + 1}..{i_datamodel}")
    print(f"  engine top region     {len(engine_top)} lines")
    print(f"  engine model region   {len(engine_model)} lines")
    print(f"  presentation -> UI    {len(presentation)} lines")
    print(f"  UI body               {len(ui_body)} lines")
    print(f"  tff_engine.py         {engine_text.count(chr(10))} lines")
    print(f"  time_for_family.pyw   {ui_text.count(chr(10))} lines")


if __name__ == "__main__":
    main()
