"""One-off: split tff_dialogs.py into the everyday dialogs (kept in
tff_dialogs.py) and the modding editors (moved to tff_editors.py).

The editors (species / room-type / announcements authoring) are the heavy,
rarely-played-through dialogs the user wants isolated. Dependency stays
acyclic: tff_editors -> tff_dialogs (only for the shared checklist helpers)
-> engine/sound. Run once, from project root:

    python dev/split_editors.py

Then add `import tff_editors` + glue to time_for_family.pyw (done separately).
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIALOGS = ROOT / "tff_dialogs.py"

EDITOR_NAMES = {
    "_slugify_id", "_species_in_use_count", "_room_type_in_use_count",
    "_purge_species_from_state", "_purge_room_type_from_state",
    "DisabilityListEditor", "SpeciesEditorDialog", "MeterPanel",
    "IngredientPanel", "RoomTypeEditorDialog", "SpeciesDialog",
    "ManageAnnouncementsDialog", "ManageRoomTypesDialog",
}

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

EDITORS_HEADER = '''\
"""
Time for Family -- the modding editors (content authoring).

The heavy, rarely-exercised dialogs for authoring content: the species
editor + curator (SpeciesEditorDialog, SpeciesDialog), the room-type editor
+ manager (RoomTypeEditorDialog, ManageRoomTypesDialog), and the
announcements editor (ManageAnnouncementsDialog) -- plus their sub-widgets
(DisabilityListEditor, MeterPanel, IngredientPanel) and the pure state
helpers they use to count/purge content on delete (_slugify_id,
_species_in_use_count, _room_type_in_use_count, _purge_species_from_state,
_purge_room_type_from_state). Depends on engine, sound, and tff_dialogs (for
the shared checklist helpers). MainFrame opens the top-level ones.
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


def block_name(line):
    s = line.rstrip("\n")
    if s.startswith("class "):
        return s[6:].split("(")[0].split(":")[0].strip()
    if s.startswith("def "):
        return s[4:].split("(")[0].strip()
    m = re.match(r"^([A-Za-z_]\w*)\s*=(?!=)", s)
    if m:
        return m.group(1)
    return None


def main():
    lines = DIALOGS.read_text(encoding="utf-8").splitlines(keepends=True)

    # Header ends after the LAST glue line (engine then sound update blocks).
    last_glue = max(i for i, l in enumerate(lines)
                    if "if not _k.startswith" in l)
    header = "".join(lines[:last_glue + 1])
    body = lines[last_glue + 1:]

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

    editors, kept = [], []
    for name, text in blocks:
        (editors if name in EDITOR_NAMES else kept).append((name, text))

    def join(parts):
        return "".join(t for _, t in parts).strip("\n") + "\n"

    (ROOT / "tff_editors.py").write_text(
        EDITORS_HEADER + "\n\n" + join(editors), encoding="utf-8")
    DIALOGS.write_text(header + "\n" + join(kept), encoding="utf-8")

    print("editors:", [n for n, _ in editors])
    print("kept in dialogs:", [n for n, _ in kept])


if __name__ == "__main__":
    main()
