"""One-off: split the screen (time_for_family.pyw) into readable pieces:

    tff_sound.py    -- sound generation + NVDA speech (leaf)
    tff_dialogs.py  -- every pop-up dialog + its sub-widgets + small UI helpers
    tff_panels.py   -- the four main panels (Room / Village / Park / Stats)
    time_for_family.pyw -- MainFrame + main(), importing the rest

Classes are interleaved in the source, so we parse the file into top-level
blocks (each class / def / module-assignment with its leading comments) and
route each block by name. Dependency direction is one-way and acyclic
(verified): MainFrame -> panels -> dialogs -> sub-widgets, everything ->
sound/engine. Run once, from project root:

    python dev/split_screen.py
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "time_for_family.pyw"

SOUND_NAMES = {
    "SOUND_RECIPES", "synthesize", "write_wav", "ensure_sounds",
    "_live_sounds", "SOUND_MUTED", "play_sound", "set_muted", "is_muted",
    "_nvda_lib", "_nvda_attempted", "_try_load_nvda", "nvda_available",
    "nvda_speak",
}
PANEL_NAMES = {"RoomPanel", "VillagePanel", "StatsPanel", "ParkPanel"}
PYW_NAMES = {"PAUSED", "AUTO_BREEDING", "MainFrame", "main", "__main__"}
# DIALOGS = every other top-level block.

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

import wx
import wx.adv
"""

SOUND_HEADER = '''\
"""
Time for Family -- sound generation + NVDA speech (the presentation's voice).

Synthesises the chime .wav files on first run, plays them, and pushes
announcements to the NVDA screen reader via its controller DLL. Imported by
the UI modules. The mute flag lives here with its only reader (play_sound);
the UI toggles it through set_muted()/is_muted().
"""

import ctypes
import math
import struct
import wave
from pathlib import Path

import wx
import wx.adv

from tff_engine import PROJECT_DIR, SOUNDS_DIR
'''

DIALOGS_HEADER = '''\
"""
Time for Family -- the pop-up dialogs and their sub-widgets.

Every wx.Dialog the game opens (build a room, edit a room, the species and
room-type editors, settings, help, the announcements editor, etc.) plus the
small shared widgets and helpers they use. Depends only on the engine and
sound -- no panel or MainFrame reference -- so the import graph stays acyclic.
"""

''' + STDLIB_IMPORTS + '''
import tff_engine
import tff_sound
# Pull engine + sound names (including _underscore helpers a star-import
# skips) into this module so the unqualified calls keep resolving. Shelves
# come across as references to the SAME objects -- never rebind them.
globals().update({_k: _v for _k, _v in vars(tff_engine).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_sound).items()
                  if not _k.startswith("__")})
'''

PANELS_HEADER = '''\
"""
Time for Family -- the four main panels (Room, Village, Park, Stats).

The pages inside MainFrame's notebook. Each opens dialogs from tff_dialogs;
none reference each other or MainFrame (MainFrame builds them). Depends on
engine, sound, and dialogs.
"""

''' + STDLIB_IMPORTS + '''
import tff_engine
import tff_sound
import tff_dialogs
globals().update({_k: _v for _k, _v in vars(tff_engine).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_sound).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_dialogs).items()
                  if not _k.startswith("__")})
'''

PYW_HEADER = '''\
"""
Time for Family -- a cozy creature-park sim (windowed app; launch this file).

The top-level window (MainFrame) and main(). The rest of the presentation
lives in tff_panels (the notebook pages), tff_dialogs (pop-ups), and
tff_sound (chimes + NVDA); all the game rules are in tff_engine. PAUSED,
AUTO_BREEDING, and AMBIENT_ENABLED are MainFrame-only runtime toggles and
live here with the frame that owns them.

Save lives in state.json next to this file.
"""

''' + STDLIB_IMPORTS + '''
import tff_engine
import tff_sound
import tff_dialogs
import tff_panels
globals().update({_k: _v for _k, _v in vars(tff_engine).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_sound).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_dialogs).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_panels).items()
                  if not _k.startswith("__")})
'''

SOUND_EXTRA = ""  # set_muted/is_muted already in the source, routed to sound.


def block_name(line):
    s = line.rstrip("\n")
    if s.startswith("class "):
        return s[6:].split("(")[0].split(":")[0].strip()
    if s.startswith("def "):
        return s[4:].split("(")[0].strip()
    if s.startswith("if __name__"):
        return "__main__"
    m = re.match(r"^([A-Za-z_]\w*)\s*=(?!=)", s)
    if m:
        return m.group(1)
    return None


def main():
    lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)

    glue_idx = next(i for i, l in enumerate(lines)
                    if "if not _k.startswith" in l)
    body = lines[glue_idx + 1:]

    # Parse body into (name, text) blocks. Leading comment/blank lines attach
    # to the block that follows them.
    blocks = []
    i, n = 0, len(body)
    while i < n:
        lead = []
        while i < n and block_name(body[i]) is None:
            lead.append(body[i])
            i += 1
        if i >= n:
            if blocks:
                blocks[-1] = (blocks[-1][0], blocks[-1][1] + lead)
            break
        name = block_name(body[i])
        chunk = lead + [body[i]]
        i += 1
        while i < n and block_name(body[i]) is None:
            chunk.append(body[i])
            i += 1
        blocks.append((name, "".join(chunk)))

    routed = {"sound": [], "dialogs": [], "panels": [], "pyw": []}
    for name, text in blocks:
        if name in SOUND_NAMES:
            routed["sound"].append(text)
        elif name in PANEL_NAMES:
            routed["panels"].append(text)
        elif name in PYW_NAMES:
            routed["pyw"].append(text)
        else:
            routed["dialogs"].append(text)

    def join(parts):
        return "".join(parts).strip("\n") + "\n"

    (ROOT / "tff_sound.py").write_text(
        SOUND_HEADER + "\n\n" + join(routed["sound"]), encoding="utf-8")
    (ROOT / "tff_dialogs.py").write_text(
        DIALOGS_HEADER + "\n\n" + join(routed["dialogs"]), encoding="utf-8")
    (ROOT / "tff_panels.py").write_text(
        PANELS_HEADER + "\n\n" + join(routed["panels"]), encoding="utf-8")
    SRC.write_text(
        PYW_HEADER + "\n\n" + join(routed["pyw"]), encoding="utf-8")

    for key in routed:
        names = [bn for bn, _ in blocks
                 if (bn in SOUND_NAMES and key == "sound")
                 or (bn in PANEL_NAMES and key == "panels")
                 or (bn in PYW_NAMES and key == "pyw")
                 or (key == "dialogs" and bn not in SOUND_NAMES
                     and bn not in PANEL_NAMES and bn not in PYW_NAMES)]
        print(f"{key:8} <- {len(routed[key])} blocks: {names}")


if __name__ == "__main__":
    main()
