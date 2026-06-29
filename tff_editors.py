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

import tff_engine
import tff_sound
import tff_dialogs
globals().update({_k: _v for _k, _v in vars(tff_engine).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_sound).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_dialogs).items()
                  if not _k.startswith("__")})


def _slugify_id(text):
    """Lowercase, alphanumerics + underscore only. Empty if no valid chars."""
    cleaned = re.sub(r"[^a-z0-9_]+", "_", (text or "").strip().lower())
    return re.sub(r"_+", "_", cleaned).strip("_")


# NOTE: the previous _meters_to_text / _meters_from_text and
# _recipe_to_text / _recipe_from_text helpers (which round-tripped
# editor data through pipe-separated text) were removed when the
# room-type editor switched to per-row labeled controls. The JSON
# files on disk are unchanged — meters are still a list of dicts and
# build_recipe is still {item_name: count} — only the editor UI is
# different.


def _species_in_use_count(state, species_id):
    """How many creatures of `species_id` exist anywhere in the save."""
    n = 0
    for room in state.get("rooms", []):
        for c in room.get("creatures", []):
            if c.get("species") == species_id:
                n += 1
    for c in state.get("village", []):
        if c.get("species") == species_id:
            n += 1
    # Pre-rolled babies inside expecting (gestating) records also
    # count — their species is locked at conception, and they'd be
    # purged if the species is deleted before they're born.
    for rec in state.get("expecting", []):
        if rec.get("species") == species_id:
            n += len(rec.get("babies") or [])
    return n


def _room_type_in_use_count(state, type_id):
    return sum(1 for r in state.get("rooms", []) if r.get("type") == type_id)


def _purge_species_from_state(state, species_id):
    """Strip every creature of `species_id` out of rooms, village, and
    pending expecting records. Used by the species-delete flow to clean
    up referencing data before the JSON is removed from disk.
    """
    # Collect ids and pair_ids of every creature about to be removed so
    # we can scrub stale references (pair_progress, last_breed_per_pair)
    # in one pass — same shape as _purge_room_type_from_state. Without
    # this, deleting a species leaves orphaned formation timers and
    # cooldowns referencing dead creatures until the natural prune in
    # progress_pairing eventually catches them.
    purged_ids = set()
    purged_pair_ids = set()
    for room in state.get("rooms", []):
        for c in room.get("creatures", []):
            if c.get("species") == species_id:
                purged_ids.add(c["id"])
                if c.get("pair_id"):
                    purged_pair_ids.add(c["pair_id"])
    for c in state.get("village", []):
        if c.get("species") == species_id:
            purged_ids.add(c["id"])
            if c.get("pair_id"):
                purged_pair_ids.add(c["pair_id"])

    for room in state.get("rooms", []):
        room["creatures"] = [
            c for c in room.get("creatures", [])
            if c.get("species") != species_id
        ]
    state["village"] = [
        c for c in state.get("village", [])
        if c.get("species") != species_id
    ]
    # Drop expecting (gestating) records of the deleted species —
    # pre-rolled babies inside them belong to the deleted species,
    # and letting them mature into placed creatures would produce
    # phantom-species residents.
    state["expecting"] = [
        rec for rec in state.get("expecting", [])
        if rec.get("species") != species_id
    ]
    # Strip pair-formation progress for any purged creature.
    progress = state.get("pair_progress", {})
    for key in list(progress.keys()):
        if any(cid in key.split("+") for cid in purged_ids):
            del progress[key]
    # Strip per-pair breed cooldowns for any pair that's now broken.
    cooldowns = state.get("last_breed_per_pair", {})
    for pid in purged_pair_ids:
        cooldowns.pop(pid, None)


def _purge_room_type_from_state(state, type_id):
    """Remove every room of `type_id`. Compatible residents move to the
    village; any expecting records attached to a removed room go with
    it (the babies will be placed somewhere else at birth via
    find_room_for_species's normal fallback chain).
    """
    rooms = state.get("rooms", [])
    surviving_rooms = []
    removed_room_ids = set()
    displaced_ids = set()
    now = time.time()
    for room in rooms:
        if room.get("type") == type_id:
            removed_room_ids.add(room["id"])
            for creature in room.get("creatures", []):
                displaced_ids.add(creature["id"])
                # Pairs survive room-type purges too — same model as
                # `move_creature_to_*`. The displaced creature keeps its
                # pair_id; if their partner was in a different room (one
                # not being purged), the pair persists across the move
                # and reunites if both end up in the same room later.
                creature["moved_to_village_at"] = now
                state.setdefault("village", []).append(creature)
        else:
            surviving_rooms.append(room)
    state["rooms"] = surviving_rooms
    # Reroute any expecting records pointing at a removed room — the
    # mother already moved to village above, so the babies would have
    # nowhere to land at birth. Reassign room_id to None; process_expecting
    # handles missing/invalid room_id by walking find_room_for_species
    # over the survivors (and falling through to the village if no
    # compatible room has space).
    for rec in state.get("expecting", []):
        if rec.get("room_id") in removed_room_ids:
            rec["room_id"] = None
    # Strip stale pair-formation progress for any displaced creature.
    # Without this, progress entries linger until the next progress_pairing
    # natural prune. Cheap, and keeps pair_progress reflecting only live
    # candidates.
    progress = state.get("pair_progress", {})
    for key in list(progress.keys()):
        if any(cid in key.split("+") for cid in displaced_ids):
            del progress[key]


class DisabilityListEditor(wx.Panel):
    """Row-based editor for a species' disabilities pool.

    Each disability is one row: a description text field plus two
    checkboxes for the optional behavioural flags ("Stays in village" /
    "Produces as elder", both defaulting checked to match the
    no-flag-by-default behaviour of disabilities.txt). Replaces the
    older single multi-line text widget where users had to type the
    pipe-format flags by hand — that violated the cogacc principle of
    "no pipe-formats in user-facing UI."

    Storage format on disk is unchanged. `GetValue()` serializes back
    to "description | ok | no_produce" lines so disabilities.txt is
    still hand-editable for modders who prefer that workflow, and
    older saves load without migration.
    """

    def __init__(self, parent, raw_lines):
        super().__init__(parent)
        self.rows = []  # list of {"panel", "desc", "stays", "produces"}

        outer = wx.BoxSizer(wx.VERTICAL)

        self._rows_panel = wx.Panel(self)
        self._rows_sizer = wx.BoxSizer(wx.VERTICAL)
        self._rows_panel.SetSizer(self._rows_sizer)
        outer.Add(self._rows_panel, 0, wx.EXPAND | wx.ALL, 2)

        add_btn = wx.Button(self, label="&Add a disability")
        add_btn.SetName("Add a new disability row")
        add_btn.Bind(wx.EVT_BUTTON, self._on_add)
        outer.Add(add_btn, 0, wx.ALL, 4)

        self.SetSizer(outer)

        for raw in raw_lines or []:
            desc, flags = parse_disability_entry(raw)
            if desc:
                self._add_row(desc, flags)

    def _on_add(self, evt):
        # New empty row, with focus on its description so the user can
        # immediately start typing without hunting for the field.
        row = self._add_row("", set())
        row["desc"].SetFocus()

    def _add_row(self, description, flags):
        row_panel = wx.Panel(self._rows_panel)
        row_sizer = wx.BoxSizer(wx.HORIZONTAL)

        desc_ctrl = wx.TextCtrl(row_panel, value=description)
        desc_ctrl.SetName("Disability description")
        row_sizer.Add(desc_ctrl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        # Both default-checked maps to "no flag in storage" — same default
        # behaviour as the absent-flag case in disabilities.txt.
        stays_chk = wx.CheckBox(row_panel, label="Stays in village")
        stays_chk.SetValue("ok" not in flags)
        stays_chk.SetName(
            "Stays in village (untick if this creature can auto-emigrate to the wild)"
        )
        row_sizer.Add(stays_chk, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        produces_chk = wx.CheckBox(row_panel, label="Produces as elder")
        produces_chk.SetValue("no_produce" not in flags)
        produces_chk.SetName(
            "Produces items as an elder (untick if this disability blocks elder production)"
        )
        row_sizer.Add(produces_chk, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        remove_btn = wx.Button(row_panel, label="Remove")
        remove_btn.SetName("Remove this disability row")
        row_sizer.Add(remove_btn, 0, wx.ALIGN_CENTER_VERTICAL)

        row_panel.SetSizer(row_sizer)

        row_data = {
            "panel": row_panel,
            "desc": desc_ctrl,
            "stays": stays_chk,
            "produces": produces_chk,
        }
        remove_btn.Bind(
            wx.EVT_BUTTON, lambda evt, rd=row_data: self._remove_row(rd)
        )

        self.rows.append(row_data)
        self._rows_sizer.Add(row_panel, 0, wx.EXPAND | wx.BOTTOM, 4)
        self._relayout()
        return row_data

    def _remove_row(self, row_data):
        if row_data not in self.rows:
            return
        self.rows.remove(row_data)
        row_data["panel"].Destroy()
        self._relayout()

    def _relayout(self):
        self._rows_panel.Layout()
        self.Layout()
        # The species editor sits inside a wx.ScrolledWindow; walking up
        # so the scrollable virtual size recomputes after we add/remove.
        ancestor = self.GetParent()
        while ancestor is not None and not isinstance(ancestor, wx.ScrolledWindow):
            ancestor = ancestor.GetParent()
        if ancestor is not None:
            ancestor.FitInside()

    def GetValue(self):
        """Serialize rows to a newline-joined string in the
        `description | flag | flag` format. Empty descriptions are
        skipped. Mirrors wx.TextCtrl's GetValue so the species editor's
        save loop can treat this widget identically to the old text
        field."""
        lines = []
        for row in self.rows:
            desc = row["desc"].GetValue().strip()
            if not desc:
                continue
            tail = []
            if not row["stays"].GetValue():
                tail.append("ok")
            if not row["produces"].GetValue():
                tail.append("no_produce")
            lines.append(desc + ("".join(f" | {f}" for f in tail) if tail else ""))
        return "\n".join(lines)


class SpeciesEditorDialog(wx.Dialog):
    """Add or edit a species, including its four text pools.

    Save writes assets/types/species/<id>.json plus
    names_female.txt / names_male.txt / descriptions.txt / pet_responses.txt
    in assets/text/species/<text_directory>/. Caller is responsible for
    calling load_types() + load_text_assets() afterward.
    """

    def __init__(self, parent, species_id=None):
        title = "Edit species" if species_id else "Add new species"
        super().__init__(parent, title=title, size=(760, 760))
        self.original_id = species_id
        self.controls = {}
        self.seed_pair_cb = None     # set in _build for new species only
        self._saved_id = None        # set in on_save once the spec writes
        self._build()

    def _build(self):
        # Outer layout: ScrolledWindow with all the content groups, plus a
        # fixed Save/Cancel button row at the bottom that stays visible no
        # matter how far the user has scrolled. wxDialog itself doesn't
        # scroll; the inner ScrolledWindow does.
        outer = wx.BoxSizer(wx.VERTICAL)
        scroll = wx.ScrolledWindow(self, style=wx.VSCROLL)
        scroll.SetScrollRate(0, 16)
        sizer = wx.BoxSizer(wx.VERTICAL)
        scroll.SetSizer(sizer)

        if self.original_id:
            data = SPECIES_DATA.get(self.original_id) or {}
            spec = dict(data.get("spec") or {})
            # Pre-redesign specs stored ages in game-days. Normalise to
            # the new *_seconds keys before populating the form so the
            # add_duration helpers below see the unified field names. Drops
            # the legacy keys after migration so a Save round-trips clean.
            for sec_key, day_key in [
                ("elder_age_seconds",         "elder_age_days"),
                ("starter_age_min_seconds",   "starter_age_min"),
                ("starter_age_max_seconds",   "starter_age_max"),
            ]:
                if sec_key not in spec and day_key in spec:
                    spec[sec_key] = int(_legacy_days_to_seconds(spec[day_key] or 0))
                spec.pop(day_key, None)
            # Pre-merge specs split "becomes elder" from "retires from
            # breeding". After the merge both happen at elder_age. If a
            # legacy spec only had max_breeding_age_seconds, promote it
            # so the editor's elder field shows the right number; either
            # way drop the now-unused max_breeding keys on save.
            if "elder_age_seconds" not in spec:
                if "max_breeding_age_seconds" in spec:
                    spec["elder_age_seconds"] = spec["max_breeding_age_seconds"]
                elif "max_breeding_age_days" in spec:
                    spec["elder_age_seconds"] = int(
                        _legacy_days_to_seconds(spec["max_breeding_age_days"] or 0)
                    )
            spec.pop("max_breeding_age_seconds", None)
            spec.pop("max_breeding_age_days", None)
            # basket_label / basket_label_plural were renamed to
            # litter_label / litter_label_plural to match what the
            # field actually describes (the species' word for a group
            # of newborns — "litter", "clutch", "spawn"). Promote the
            # legacy keys on dialog-open so the form shows the value;
            # save writes only the new keys so the round-trip cleans up.
            if "litter_label" not in spec and "basket_label" in spec:
                spec["litter_label"] = spec["basket_label"]
            if "litter_label_plural" not in spec and "basket_label_plural" in spec:
                spec["litter_label_plural"] = spec["basket_label_plural"]
            spec.pop("basket_label", None)
            spec.pop("basket_label_plural", None)
            text_pools = {
                "name_pool_f": list(data.get("name_pool_f") or []),
                "name_pool_m": list(data.get("name_pool_m") or []),
                "descriptions": list(data.get("descriptions") or []),
                "pet_responses": list(data.get("pet_responses") or []),
                "disabilities": list(data.get("disabilities") or []),
                "colors": list(data.get("colors") or []),
            }
        else:
            # Defaults for a brand-new species. All times are in real
            # seconds — modder edits the friendly text inputs in the form
            # ("5 minutes", "2 hours", etc.) so they don't have to think
            # in raw seconds.
            spec = {
                "starter_age_min_seconds": 1800,    # 30 min
                "starter_age_max_seconds": 7200,    # 2 hours
                "starter_pairs": 1,
                "breeding_age_seconds": 14400,      # 4 hours
                "elder_age_seconds": 216000,        # 60 hours — a single
                # 'they're old now' threshold; gates both elder
                # production and retirement-from-breeding (these were
                # two separate stages before the merge).
                "twin_chance": 0.0,
                # Disability is opt-in per species. Default 0.0 so a
                # brand-new species spec ships disabled-disabilities;
                # modder bumps the chance and writes entries in the
                # Disabilities pool below before the mechanic fires.
                "disability_chance": 0.0,
                "litter_label": "litter",
                "litter_label_plural": "litters",
                "care_action_label": "Pet",
                "sex_label_female": "female",
                "sex_label_male": "male",
                "sex_short_female": "F",
                "sex_short_male": "M",
                # Pre-tick the first available room type so a brand-new
                # species defaults to *somewhere* its creatures can live.
                # Empty compatible_room_types is a save-time error (see
                # on_save validation); this default just makes the most
                # common case — "indoor species" — work without the user
                # having to remember to tick a box.
                "compatible_room_types": (
                    [next(iter(ROOM_TYPES.keys()))] if ROOM_TYPES else []
                ),
            }
            text_pools = {
                "name_pool_f": [],
                "name_pool_m": [],
                "descriptions": [],
                "pet_responses": [],
                "disabilities": [],
                "colors": [],
            }

        # ---- Helper builders ---------------------------------------------
        # Each grid passed in is the FlexGridSizer of the StaticBox they
        # belong to. `parent` defaults to the outer scroll window for
        # default-visible fields; pass the CollapsiblePane's pane window
        # for fields that should live inside the Advanced section.

        def add_text(grid, key, label, default="", parent=None):
            parent = parent or scroll
            lbl = wx.StaticText(parent, label=label + ":")
            ctrl = wx.TextCtrl(parent, value=str(spec.get(key, default)))
            ctrl.SetName(label)
            grid.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)
            self.controls[key] = ctrl

        def add_int(grid, key, label, default=0, parent=None):
            # Plain TextCtrl + parse, not wx.SpinCtrl. Same NVDA-flood
            # rationale as in SettingsDialog: holding the SpinCtrl arrow
            # spins the value rapidly and queues an NVDA announcement per
            # step. Typing the value directly = single announcement.
            parent = parent or scroll
            lbl = wx.StaticText(parent, label=label + ":")
            ctrl = wx.TextCtrl(parent, value=str(int(spec.get(key, default))))
            ctrl.SetName(label)
            grid.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)
            self.controls[key] = ctrl

        def add_duration(grid, key, label, default=0, hint="e.g. 5 minutes", parent=None):
            # Plain-language duration field — accepts '5 minutes', '1 hour',
            # '1h 30m', etc. Stored as int seconds in the JSON. Empty = 0
            # (immediately mature, etc., depending on the field).
            parent = parent or scroll
            lbl = wx.StaticText(parent, label=label + ":")
            initial_int = int(spec.get(key, default))
            initial_text = format_duration_human(initial_int) if initial_int > 0 else ""
            ctrl = wx.TextCtrl(parent, value=initial_text)
            ctrl.SetName(label)
            if not initial_text and hint:
                ctrl.SetHint(hint)
            grid.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)
            self.controls[key] = ctrl

        def make_group(parent_sizer, title, parent=None):
            parent = parent or scroll
            box = wx.StaticBox(parent, label=title)
            box_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
            grid = wx.FlexGridSizer(rows=0, cols=2, hgap=12, vgap=6)
            grid.AddGrowableCol(1, 1)
            box_sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 6)
            parent_sizer.Add(box_sizer, 0, wx.ALL | wx.EXPAND, 8)
            return grid, box_sizer

        # ---- Group 1: Identity -------------------------------------------
        grid, _ = make_group(sizer, "Identity")
        # Species ID is the first field NVDA reads when the dialog opens.
        # Spelling out "Species ID" prevents confusion with any other ID
        # fields the user might encounter while editing.
        add_text(grid, "id", "Species ID (lowercase, no spaces)")
        if self.original_id:
            self.controls["id"].Disable()
        add_text(grid, "name", "Display name, singular")
        add_text(grid, "name_plural", "Display name, plural")
        # text_directory has moved to the Advanced section below — it
        # auto-derives from the species ID for everyone except modders
        # who deliberately want to share a text folder between species.

        # ---- Group 2: Vocabulary -----------------------------------------
        grid, _ = make_group(sizer, "Vocabulary")
        add_text(grid, "sex_label_female",
                 "Word for a female, long form (e.g. 'female')")
        add_text(grid, "sex_label_male",
                 "Word for a male, long form (e.g. 'male')")
        add_text(grid, "care_action_label",
                 "Care action button label (e.g. 'Pet', 'Feed', 'Visit')")
        add_text(grid, "litter_label",
                 "Word for one group of babies (e.g. 'litter', 'clutch')")
        # sex_short_female / sex_short_male / litter_label_plural have
        # moved to the Advanced section. The shorts default to F / M
        # and the plural to the singular + 's' — both are fine for
        # almost every species and modders rarely need to override.

        # Markov toggle — when on, new creature names are generated in
        # the style of this species' name pools using a small Markov
        # chain trained on those pools. When off (default for modder
        # species), names are drawn directly from the pool. Toggle is
        # safe to flip at any time; existing creatures keep the names
        # they already have.
        markov_lbl = wx.StaticText(scroll, label="Generate new names:")
        self.markov_chk = wx.CheckBox(
            scroll,
            label="Generate new names in this species' style "
                  "(off = pick from pool as written)",
        )
        self.markov_chk.SetName("Generate new names")
        self.markov_chk.SetValue(
            (spec.get("name_generation") or "pool").lower() == "markov"
        )
        grid.Add(markov_lbl, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.markov_chk, 1, wx.EXPAND)
        markov_help = wx.StaticText(
            scroll,
            label=(
                "When on, the game uses a small character-Markov chain "
                "trained on this species' name pools to invent new names "
                "in the same style — useful for fast-breeding species "
                "that quickly run through a hand-written list. Needs at "
                "least 4-5 names per sex in the pool to produce decent "
                "output; turn off and pick from the pool directly if you "
                "have very few names."
            ),
        )
        markov_help.Wrap(520)
        markov_help.SetForegroundColour(wx.Colour(90, 90, 90))
        grid.Add((0, 0))  # spacer cell to align help text under the checkbox
        grid.Add(markov_help, 1, wx.EXPAND | wx.BOTTOM, 4)

        # ---- Group 3: Life stages ----------------------------------------
        # The two duration inputs accept plain language ('5 minutes',
        # '2 hours', '3 days') or a raw number of seconds. Starter ages,
        # starter pairs, twin chance, disability chance, and per-species
        # litter overrides all live in the Advanced section below — they
        # rarely need touching for a working species.
        grid, _ = make_group(sizer, "Life stages")
        add_duration(grid, "breeding_age_seconds",
                     "Time before a baby is mature (real time, "
                     "e.g. '5 minutes' or '1 hour')")
        add_duration(grid, "elder_age_seconds",
                     "Age at which a creature becomes an elder "
                     "and retires from breeding "
                     "(real time, e.g. '3 days' or '60 hours')")

        # ---- Group 5: Compatible room types ------------------------------
        # wx.CheckListBox with item-text prefixes ("checked: " / "not
        # checked: ") so NVDA announces state alongside the room-type
        # name as the user arrows through. Toggling rewrites the prefix.
        compat_static = wx.StaticBox(scroll, label="Compatible room types")
        compat_sizer = wx.StaticBoxSizer(compat_static, wx.VERTICAL)
        compat_intro = wx.StaticText(
            scroll,
            label=("Tick every room type this species can live in. "
                   "If none are ticked, you won't be able to put this "
                   "species into any room. Use arrow keys to move "
                   "between room types, Space to toggle the highlighted "
                   "one."),
        )
        compat_intro.Wrap(640)
        compat_intro.SetForegroundColour(wx.Colour(90, 90, 90))
        compat_sizer.Add(compat_intro, 0, wx.ALL, 4)
        self._room_type_ids = list(ROOM_TYPES.keys())
        room_type_items = [
            (rid, f"{ROOM_TYPES[rid].get('name', rid)} ({rid})")
            for rid in self._room_type_ids
        ]
        self.compat_box = make_state_announcing_checklist(
            scroll,
            "Compatible room types",
            room_type_items,
            checked_ids=spec.get("compatible_room_types") or [],
        )
        compat_sizer.Add(self.compat_box, 0, wx.ALL | wx.EXPAND, 4)
        sizer.Add(compat_sizer, 0, wx.ALL | wx.EXPAND, 8)

        # ---- Advanced section --------------------------------------------
        # Collapsed by default. Holds rarely-touched knobs: text-folder
        # name, sex short forms (auto F/M), litter plural (auto +s),
        # starter age/pairs, twin/disability chances, litter overrides.
        # Hiding them keeps the default view at a manageable handful of
        # fields rather than 17, so a first-time modder isn't drowning.
        adv_pane = wx.CollapsiblePane(
            scroll,
            label="Advanced settings (most modders don't need these)",
            style=wx.CP_DEFAULT_STYLE | wx.CP_NO_TLW_RESIZE,
        )
        adv_pane.SetName("Advanced settings")
        adv_window = adv_pane.GetPane()
        adv_sizer = wx.BoxSizer(wx.VERTICAL)
        adv_window.SetSizer(adv_sizer)

        def _on_advanced_toggle(_evt):
            scroll.FitInside()
            scroll.Layout()
            sizer.Layout()
            self.Layout()
        adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, _on_advanced_toggle)

        # Identity (advanced)
        adv_grid, _ = make_group(
            adv_sizer, "Identity (advanced)", parent=adv_window,
        )
        add_text(adv_grid, "text_directory",
                 "Text folder name (defaults to species ID)",
                 parent=adv_window)
        if self.original_id:
            self.controls["text_directory"].Disable()

        # Vocabulary (advanced)
        adv_grid, _ = make_group(
            adv_sizer, "Vocabulary (advanced)", parent=adv_window,
        )
        add_text(adv_grid, "sex_short_female",
                 "Short letter for female (defaults to 'F')",
                 parent=adv_window)
        add_text(adv_grid, "sex_short_male",
                 "Short letter for male (defaults to 'M')",
                 parent=adv_window)
        add_text(adv_grid, "litter_label_plural",
                 "Plural of the litter label "
                 "(defaults to singular + 's')",
                 parent=adv_window)

        # Life stages (advanced) — starter age range, starter pairs, twin
        # and disability chances. Each falls back to a sensible default
        # if left blank, so the basic view doesn't need to expose them.
        adv_grid, life_adv_box_sizer = make_group(
            adv_sizer, "Life stages (advanced)", parent=adv_window,
        )
        add_duration(adv_grid, "starter_age_min_seconds",
                     "Starter age, minimum "
                     "(e.g. '30 minutes' or '2 hours')",
                     parent=adv_window)
        add_duration(adv_grid, "starter_age_max_seconds",
                     "Starter age, maximum "
                     "(e.g. '2 hours' or '1 day')",
                     parent=adv_window)
        add_int(adv_grid, "starter_pairs",
                "Starter pairs per built room",
                parent=adv_window)
        add_duration(adv_grid, "gestation_seconds",
                     "Gestation — how long between conception and the "
                     "babies being born (leave blank or 0 for instant — "
                     "babies are born right away when a pair "
                     "successfully breeds)",
                     parent=adv_window)
        add_duration(adv_grid, "mother_dependency_seconds",
                     "Mother-dependency — how long after birth babies "
                     "stay tethered to their mother (during this time, "
                     "you can't move a baby without their mother, and "
                     "moving the mother brings all her dependent "
                     "babies with her — the destination must have "
                     "room for everyone). Leave blank or 0 for "
                     "precocial species whose babies are independent "
                     "right away (chickens, fish, snails)",
                     parent=adv_window)
        add_duration(adv_grid, "breed_cooldown_seconds",
                     "Pair rest between litters — how long after a "
                     "successful breeding before this species' pairs "
                     "can try again. Different species have different "
                     "biological cadences (a cat between litters is "
                     "not a chicken between clutches). Leave blank to "
                     "use the global default in Settings; set 0 for "
                     "no cooldown.",
                     parent=adv_window)

        twin_lbl = wx.StaticText(adv_window, label="Twin chance (0.0 to 1.0):")
        twin_ctrl = wx.TextCtrl(
            adv_window, value=f"{float(spec.get('twin_chance', 0.0)):.2f}",
        )
        twin_ctrl.SetName("Twin chance (0.0 to 1.0)")
        adv_grid.Add(twin_lbl, 0, wx.ALIGN_CENTER_VERTICAL)
        adv_grid.Add(twin_ctrl, 1, wx.EXPAND)
        self.controls["twin_chance"] = twin_ctrl

        # Disability chance — see the docstring on maybe_disability for
        # the design intent. Disability is a representational feature
        # only; it never gates breeding, pairing, affection, or care.
        # The chance only matters once the Disabilities pool below has
        # entries — empty pool = no disability descriptions to assign.
        dis_lbl = wx.StaticText(
            adv_window, label="Disability chance (0.0 to 1.0):",
        )
        dis_ctrl = wx.TextCtrl(
            adv_window, value=f"{float(spec.get('disability_chance', 0.0)):.2f}",
        )
        dis_ctrl.SetName("Disability chance (0.0 to 1.0)")
        adv_grid.Add(dis_lbl, 0, wx.ALIGN_CENTER_VERTICAL)
        adv_grid.Add(dis_ctrl, 1, wx.EXPAND)
        self.controls["disability_chance"] = dis_ctrl

        chance_help = wx.StaticText(
            adv_window,
            label=(
                "About the two chances above:\n"
                "• Twin chance: how often a newborn comes with a fraternal "
                "twin (a second baby in the same birth). 0 means never; "
                "0.10 means roughly 1 in 10 births is twins.\n"
                "• Disability chance: how often a newborn is born with one "
                "of the descriptions from the Disabilities pool below. 0 "
                "means never; 0.05 means roughly 1 in 20 births. Disability "
                "in this game is a respectful, factual description — it "
                "doesn't change how the creature lives, breeds, or is cared "
                "for. Set this to 0 if you don't want to use the mechanic.\n"
                "• When the parents are closely related (full siblings, "
                "half-siblings, or one is the other's parent), the "
                "Disability chance is multiplied by the Inbreeding "
                "disability multiplier in Settings (default 3 times). This "
                "models real-world inbreeding — same disability pool, just "
                "drawn from more often."
            ),
        )
        chance_help.Wrap(640)
        chance_help.SetForegroundColour(wx.Colour(90, 90, 90))
        life_adv_box_sizer.Add(chance_help, 0, wx.ALL, 8)

        # Litter size override (advanced) — empty = inherit Settings.
        litter_grid, litter_box_sizer = make_group(
            adv_sizer, "Litter size override", parent=adv_window,
        )

        def add_optional_int(grid, key, label, parent):
            lbl = wx.StaticText(parent, label=label + ":")
            raw = spec.get(key)
            initial = "" if raw in (None, "") else str(int(raw))
            ctrl = wx.TextCtrl(parent, value=initial)
            ctrl.SetName(label)
            ctrl.SetHint("blank = use Settings default")
            grid.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)
            self.controls[key] = ctrl

        add_optional_int(
            litter_grid, "min_babies",
            "Smallest litter size (leave blank to use Settings default)",
            adv_window,
        )
        add_optional_int(
            litter_grid, "max_babies",
            "Biggest litter size (leave blank to use Settings default)",
            adv_window,
        )
        litter_help = wx.StaticText(
            adv_window,
            label=(
                "How many babies arrive in one successful breeding. "
                "Leave both blank to inherit the Smallest / Biggest "
                "litter size from File → Settings (the global default "
                "applies to every species that doesn't override). Set a "
                "value here to give this species its own range — e.g. "
                "rabbits at 4-8, hedgehogs at 3-5, fish at 1-1 if you "
                "only want a single fry per spawn. Smallest must be at "
                "least 1; if Smallest is bigger than Biggest, the game "
                "swaps them automatically."
            ),
        )
        litter_help.Wrap(640)
        litter_help.SetForegroundColour(wx.Colour(90, 90, 90))
        litter_box_sizer.Add(litter_help, 0, wx.ALL, 8)

        sizer.Add(adv_pane, 0, wx.ALL | wx.EXPAND, 8)

        # ---- Groups 5–8: Text pools --------------------------------------
        # Each pool stays as a multi-line textarea — these can hold dozens
        # of entries (50+ names is normal) and a per-row UI would make NVDA
        # navigation tedious. The intro text is the cognitive-accessibility
        # win: it makes clear the player edits these IN this dialog, not
        # by hopping out to text files.

        def add_pool(title, intro_text, value, name_hint):
            box = wx.StaticBox(scroll, label=title)
            box_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
            intro = wx.StaticText(scroll, label=intro_text)
            intro.Wrap(640)
            intro.SetForegroundColour(wx.Colour(90, 90, 90))
            box_sizer.Add(intro, 0, wx.ALL, 4)
            ctrl = wx.TextCtrl(
                scroll, style=wx.TE_MULTILINE,
                value="\n".join(value), size=(-1, 90),
            )
            ctrl.SetName(name_hint)
            box_sizer.Add(ctrl, 1, wx.ALL | wx.EXPAND, 4)
            sizer.Add(box_sizer, 0, wx.ALL | wx.EXPAND, 8)
            return ctrl

        pool_intro_common = (
            "Each line is one entry. Type or paste your list here — "
            "you don't need to open the text files manually. "
            "Saving overwrites the matching file."
        )
        self.names_f_text = add_pool(
            "Female names",
            pool_intro_common,
            text_pools["name_pool_f"],
            "Female names, one per line",
        )
        self.names_m_text = add_pool(
            "Male names",
            pool_intro_common,
            text_pools["name_pool_m"],
            "Male names, one per line",
        )
        self.desc_text = add_pool(
            "Descriptions",
            pool_intro_common,
            text_pools["descriptions"],
            "Descriptions, one per line",
        )
        self.pet_text = add_pool(
            "Pet / care responses",
            (pool_intro_common + " Use {name} where you want the "
             "creature's name to appear in the response."),
            text_pools["pet_responses"],
            "Pet or care responses, one per line",
        )
        # Disabilities — row-based editor (one disability per row, with
        # checkboxes for the two flags) instead of the older pipe-format
        # text field. See DisabilityListEditor for the widget; storage on
        # disk is unchanged.
        dis_box = wx.StaticBox(scroll, label="Disabilities")
        dis_box_sizer = wx.StaticBoxSizer(dis_box, wx.VERTICAL)
        dis_intro = wx.StaticText(
            scroll,
            label=(
                "Physical or sensory differences a creature of this species "
                "can be born with. Add one row per disability — write "
                "factual, neutral language like 'Born blind.', 'Three "
                "legs.', or 'Doesn't see well in dim light.' Disability "
                "is a respectful representation feature, not a debuff: "
                "disabled creatures pair, breed, age, and are loved like "
                "any other.\n"
                "\n"
                "Each row has two tickboxes that flip a single behaviour. "
                "Both start ticked, which matches the default "
                "behaviour:\n"
                "• Stays in village — when ticked, the creature stays "
                "in the village rather than auto-emigrating to the wild "
                "(the village is for those who need a home that knows "
                "them). Untick if a creature with this specific "
                "disability can live in the wild.\n"
                "• Produces as elder — when ticked, the creature "
                "produces items as an elder normally. Untick if this "
                "specific disability blocks elder production.\n"
                "\n"
                "Set the Disability chance above to 0 to turn the "
                "mechanic off entirely; an empty list also keeps it "
                "dormant even at a non-zero chance."
            ),
        )
        dis_intro.Wrap(640)
        dis_intro.SetForegroundColour(wx.Colour(90, 90, 90))
        dis_box_sizer.Add(dis_intro, 0, wx.ALL, 4)

        self.disabilities_text = DisabilityListEditor(
            scroll, text_pools["disabilities"]
        )
        dis_box_sizer.Add(self.disabilities_text, 0, wx.ALL | wx.EXPAND, 4)

        sizer.Add(dis_box_sizer, 0, wx.ALL | wx.EXPAND, 8)

        # Colors pool — one phrase per line, used at birth to roll each
        # creature's two colors. Babies inherit one color from each
        # parent (with a small Settings-controlled mutation chance for a
        # fresh pool roll). Descriptions can reference {color} /
        # {color2} placeholders for inline prose; an empty pool here
        # means "no colors for this species" and the detail panel just
        # hides the Colour line.
        self.colors_text = add_pool(
            "Colors",
            (pool_intro_common + " Each creature is born with two of "
             "these — one inherited from each parent (with a small "
             "chance of a fresh roll, set in Settings → Color "
             "mutation chance). Descriptions can reference {color} "
             "and {color2} to mention them inline; the detail panel "
             "always shows them on a 'Colour:' line if the pool isn't "
             "empty."),
            text_pools["colors"],
            "Colors, one phrase per line",
        )

        # ---- Group 9 (new species only): seed a starter pair -------------
        # Without this, a brand-new species exists as a spec on disk but
        # has no living creatures anywhere — the player can't actually
        # play with it. Default the checkbox on; let modders untick it
        # if they're authoring a spec they don't want to populate yet.
        if self.original_id is None:
            # Pull the renameable place-name from the parent panel's
            # frame so this intro and the checkbox label match the
            # player's current village name.
            seed_place_name = (
                getattr(self.GetParent(), "frame", None).state.get(
                    "village_name", "Village",
                )
                if hasattr(self.GetParent(), "frame") else "Village"
            )
            seed_static = wx.StaticBox(scroll, label="Make playable right away")
            seed_box_sizer = wx.StaticBoxSizer(seed_static, wx.VERTICAL)
            seed_intro = wx.StaticText(
                scroll,
                label=(f"Drop one female and one male of this species into "
                       f"{seed_place_name} as soon as you save, so you "
                       "can adopt them right away. Untick to save the "
                       "species spec without seeding any creatures yet."),
            )
            seed_intro.Wrap(640)
            seed_intro.SetForegroundColour(wx.Colour(90, 90, 90))
            seed_box_sizer.Add(seed_intro, 0, wx.ALL, 4)
            self.seed_pair_cb = wx.CheckBox(
                scroll,
                label=f"Seed a starter pair in {seed_place_name}",
            )
            self.seed_pair_cb.SetValue(True)
            self.seed_pair_cb.SetName("Seed a starter pair in the village")
            seed_box_sizer.Add(self.seed_pair_cb, 0, wx.ALL, 4)
            sizer.Add(seed_box_sizer, 0, wx.ALL | wx.EXPAND, 8)

        # ---- Wire up ScrolledWindow + button row -------------------------
        scroll.FitInside()
        outer.Add(scroll, 1, wx.EXPAND)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        save_btn = wx.Button(self, label="Save")
        save_btn.Bind(wx.EVT_BUTTON, self.on_save)
        save_btn.SetDefault()
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "Cancel")
        btns.AddStretchSpacer()
        btns.Add(save_btn, 0, wx.RIGHT, 8)
        btns.Add(cancel_btn, 0)
        outer.Add(btns, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(outer)

    def on_save(self, evt):
        if self.original_id:
            sid = self.original_id
        else:
            sid = _slugify_id(self.controls["id"].GetValue())
            if not sid:
                wx.MessageBox(
                    "Please give the species an ID (lowercase letters, "
                    "numbers, and underscores).",
                    "Add species", wx.OK | wx.ICON_WARNING, self,
                )
                return
            if sid in SPECIES_DATA:
                wx.MessageBox(
                    f"A species with ID '{sid}' already exists.",
                    "Add species", wx.OK | wx.ICON_WARNING, self,
                )
                return

        name = self.controls["name"].GetValue().strip()
        if not name:
            wx.MessageBox(
                "Please give the species a display name.",
                "Save species", wx.OK | wx.ICON_WARNING, self,
            )
            return

        try:
            twin = float(self.controls["twin_chance"].GetValue().strip() or "0")
        except ValueError:
            wx.MessageBox(
                "Twin chance must be a number between 0 and 1 (e.g. 0.10).",
                "Save species", wx.OK | wx.ICON_WARNING, self,
            )
            return
        twin = max(0.0, min(1.0, twin))

        try:
            disability = float(
                self.controls["disability_chance"].GetValue().strip() or "0"
            )
        except ValueError:
            wx.MessageBox(
                "Disability chance must be a number between 0 and 1 "
                "(e.g. 0.05).",
                "Save species", wx.OK | wx.ICON_WARNING, self,
            )
            return
        disability = max(0.0, min(1.0, disability))

        # Helper for the remaining integer fields (now plain TextCtrls).
        # Empty input is treated as 0 since these fields all have
        # meaningful zero behaviour. Bad input pops up a friendly error
        # pointing at the field by its label.
        int_field_labels = {
            "starter_pairs": "Starter pairs per built room",
        }

        def parse_int_field(key):
            raw = self.controls[key].GetValue().strip()
            if not raw:
                return 0
            try:
                return max(0, int(raw))
            except ValueError:
                wx.MessageBox(
                    f"'{raw}' isn't a whole number for "
                    f"'{int_field_labels[key]}'. Try something like 1.",
                    "Save species", wx.OK | wx.ICON_WARNING, self,
                )
                self.controls[key].SetFocus()
                return None

        ints = {}
        for key in int_field_labels:
            value = parse_int_field(key)
            if value is None:
                return
            ints[key] = value

        # Plain-language duration parsing for every life-stage field. Each
        # accepts '5 minutes', '2 hours', '1 day', etc. Empty → 0 (which
        # for the threshold fields means "no threshold ever fires" —
        # documented behaviour, not a bug).
        duration_field_labels = {
            "starter_age_min_seconds": "Starter age, minimum",
            "starter_age_max_seconds": "Starter age, maximum",
            "breeding_age_seconds":    "Time before a baby is mature",
            "elder_age_seconds":       "Age at which a creature becomes an elder and retires from breeding",
            "gestation_seconds":       "Gestation",
            "mother_dependency_seconds": "Mother-dependency",
            "breed_cooldown_seconds":  "Pair rest between litters",
        }
        durations = {}
        blank_durations = set()
        for key, label in duration_field_labels.items():
            raw = self.controls[key].GetValue().strip()
            if not raw:
                durations[key] = 0
                blank_durations.add(key)
                continue
            try:
                durations[key] = parse_duration(raw)
            except ValueError as e:
                wx.MessageBox(
                    f"{label}: {e}",
                    "Save species", wx.OK | wx.ICON_WARNING, self,
                )
                self.controls[key].SetFocus()
                return

        # Sanitize text_directory through the same slug rule as the
        # species id. Raw input here gets joined into filesystem paths
        # for read AND for delete (`_purge_species_from_state` /
        # SpeciesDialog.on_delete `unlink`s + `rmdir`s the resolved
        # directory) — a stray '..' or backslash could write or
        # remove files outside user_data/text/species/. Defaulting to
        # `sid` when blank stays the same; user-supplied values now
        # get cleaned to lowercase + alphanumerics + underscore.
        text_dir_raw = self.controls["text_directory"].GetValue().strip()
        text_dir = _slugify_id(text_dir_raw) if text_dir_raw else sid
        if text_dir_raw and not text_dir:
            wx.MessageBox(
                "Text folder name needs at least one letter, number, or "
                "underscore. Leave it blank to use the species ID, or "
                "type something like 'dragon' or 'sea_creature'.",
                "Save species", wx.OK | wx.ICON_WARNING, self,
            )
            self.controls["text_directory"].SetFocus()
            return

        compatible = checklist_get_checked_ids(self.compat_box)
        # A species with no compatible room types is broken — its
        # creatures can never leave the village (no room can accept
        # them). Refuse to save in that state rather than silently
        # shipping a stranded species. The detail panel in the Species
        # dialog tries to soften this with "you'll need to add one"
        # copy, but that's a help-text band-aid; the real fix is to
        # not let it happen in the first place.
        if not compatible:
            wx.MessageBox(
                "This species needs at least one compatible room type "
                "— otherwise creatures of this species will be stuck "
                "in the village forever, unable to move into any "
                "room. Tick at least one room type in the 'Compatible "
                "room types' section before saving. If none of the "
                "existing room types fit, use Mods → Manage room "
                "types to add a new one first.",
                "Save species", wx.OK | wx.ICON_WARNING, self,
            )
            self.compat_box.SetFocus()
            return

        # Validate the name pools are non-empty BEFORE writing anything.
        # An empty pool means new creatures of this species fall back to
        # a generic placeholder name ("Newcomer" etc.) — not the end of
        # the world, but if the player creates a species and immediately
        # seeds a starter pair (the default), the seeded babies inherit
        # those placeholders permanently. The player tends to fill in
        # the pools later and find the original pair is stuck with the
        # wrong names. Refusing here forces the order: pools first,
        # then save, then seed.
        def _pool_lines(text_widget):
            return [
                ln.strip() for ln in text_widget.GetValue().splitlines()
                if ln.strip()
            ]
        names_f = _pool_lines(self.names_f_text)
        names_m = _pool_lines(self.names_m_text)
        if not names_f or not names_m:
            missing = []
            if not names_f:
                missing.append("Female names")
            if not names_m:
                missing.append("Male names")
            wx.MessageBox(
                f"Please add at least one name to: {', '.join(missing)}. "
                "Empty name pools mean new creatures of this species "
                "would be named with a generic placeholder ('Newcomer', "
                "'Visitor', etc.) instead of something species-specific. "
                "If you save a brand-new species with empty pools and "
                "the starter-pair option is on, that pair is stamped "
                "with the placeholder names permanently — filling the "
                "pools in afterwards doesn't rename existing creatures.",
                "Save species", wx.OK | wx.ICON_WARNING, self,
            )
            if not names_f:
                self.names_f_text.SetFocus()
            else:
                self.names_m_text.SetFocus()
            return

        # Per-species litter overrides — empty field = inherit Settings.
        # Parse to int (≥ 1) when present; bail on garbage with a clear
        # message that names the offending field.
        litter_overrides = {}
        for key, label in (
            ("min_babies", "Smallest litter size"),
            ("max_babies", "Biggest litter size"),
        ):
            raw = self.controls[key].GetValue().strip()
            if not raw:
                continue
            try:
                value = int(raw)
            except ValueError:
                wx.MessageBox(
                    f"'{raw}' isn't a whole number for '{label}'. "
                    "Leave the field blank to use the Settings default, "
                    "or type a number like 1.",
                    "Save species", wx.OK | wx.ICON_WARNING, self,
                )
                self.controls[key].SetFocus()
                return
            if value < 1:
                wx.MessageBox(
                    f"'{label}' must be at least 1 (or leave it blank "
                    "to use the Settings default).",
                    "Save species", wx.OK | wx.ICON_WARNING, self,
                )
                self.controls[key].SetFocus()
                return
            litter_overrides[key] = value
        if (
            "min_babies" in litter_overrides
            and "max_babies" in litter_overrides
            and litter_overrides["min_babies"] > litter_overrides["max_babies"]
        ):
            wx.MessageBox(
                "Smallest litter size can't be bigger than biggest "
                "litter size.",
                "Save species", wx.OK | wx.ICON_WARNING, self,
            )
            self.controls["min_babies"].SetFocus()
            return

        spec = {
            "id": sid,
            "name": name,
            "name_plural": self.controls["name_plural"].GetValue().strip() or (name + "s"),
            "sex_label_female": self.controls["sex_label_female"].GetValue().strip() or "female",
            "sex_label_male": self.controls["sex_label_male"].GetValue().strip() or "male",
            "sex_short_female": self.controls["sex_short_female"].GetValue().strip() or "F",
            "sex_short_male": self.controls["sex_short_male"].GetValue().strip() or "M",
            "compatible_room_types": compatible,
            "starter_age_min_seconds": durations["starter_age_min_seconds"],
            "starter_age_max_seconds": durations["starter_age_max_seconds"],
            "starter_pairs": ints["starter_pairs"],
            "text_directory": text_dir,
            "care_action_label": self.controls["care_action_label"].GetValue().strip() or "Pet",
            "litter_label": self.controls["litter_label"].GetValue().strip() or "litter",
            "litter_label_plural": self.controls["litter_label_plural"].GetValue().strip() or (
                (self.controls["litter_label"].GetValue().strip() or "litter") + "s"
            ),
            "name_generation": "markov" if self.markov_chk.GetValue() else "pool",
            "breeding_age_seconds": durations["breeding_age_seconds"],
            "elder_age_seconds": durations["elder_age_seconds"],
            "gestation_seconds": durations["gestation_seconds"],
            "mother_dependency_seconds": durations["mother_dependency_seconds"],
            "twin_chance": twin,
            "disability_chance": disability,
        }
        # "Pair rest between litters" follows the same blank-means-inherit
        # rule its help text promises: only persist the key when the field
        # was actually filled in. A blank field leaves the key absent so
        # species_breed_cooldown_seconds() falls back to the global
        # Settings default; a typed 0 is kept and means "no cooldown".
        # Writing it unconditionally (the old behaviour) pinned every
        # re-saved species to 0 and silently stripped shipped cooldowns.
        if "breed_cooldown_seconds" not in blank_durations:
            spec["breed_cooldown_seconds"] = durations["breed_cooldown_seconds"]
        # Only persist litter overrides that were actually entered;
        # absent keys mean "inherit Settings" downstream.
        spec.update(litter_overrides)
        if spec["starter_age_min_seconds"] > spec["starter_age_max_seconds"]:
            wx.MessageBox(
                "Starter age, minimum can't be greater than starter age, maximum.",
                "Save species", wx.OK | wx.ICON_WARNING, self,
            )
            self.controls["starter_age_min_seconds"].SetFocus()
            return

        SPECIES_DIR.mkdir(parents=True, exist_ok=True)
        json_path = SPECIES_DIR / f"{sid}.json"
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(spec, f, indent=2)
        except OSError as e:
            wx.MessageBox(
                f"Couldn't write {json_path}: {e}",
                "Save species", wx.OK | wx.ICON_ERROR, self,
            )
            return

        species_text_dir = TEXT_DIR / "species" / text_dir
        species_text_dir.mkdir(parents=True, exist_ok=True)
        text_files = [
            ("names_female.txt", self.names_f_text.GetValue()),
            ("names_male.txt", self.names_m_text.GetValue()),
            ("descriptions.txt", self.desc_text.GetValue()),
            ("pet_responses.txt", self.pet_text.GetValue()),
            ("disabilities.txt", self.disabilities_text.GetValue()),
            ("colors.txt", self.colors_text.GetValue()),
        ]
        for fname, content in text_files:
            path = species_text_dir / fname
            header = _resolve_text_file_header(fname, spec)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    for header_line in header.splitlines():
                        f.write(f"# {header_line}\n")
                    f.write("\n")
                    for line in content.splitlines():
                        stripped = line.strip()
                        if stripped:
                            f.write(stripped + "\n")
            except OSError as e:
                wx.MessageBox(
                    f"Couldn't write {path}: {e}",
                    "Save species", wx.OK | wx.ICON_ERROR, self,
                )
                return

        # Stash for callers (SpeciesDialog.on_create) that need to know
        # which species was just written so they can seed a starter pair
        # into the village afterwards.
        self._saved_id = sid
        self.EndModal(wx.ID_OK)

    def saved_species_id(self):
        """Return the species id written to disk by the last successful
        save, or None if the dialog was cancelled / hasn't saved yet.
        """
        return self._saved_id

    def should_seed_starter_pair(self):
        """True if the dialog was for a brand-new species AND the user
        left the seed-pair checkbox ticked. False for edits to existing
        species (no seeding ever) or if the user explicitly unticked it.
        """
        return (
            self.original_id is None
            and self.seed_pair_cb is not None
            and self.seed_pair_cb.GetValue()
        )


# Helpers for building a wx.CheckListBox whose items announce their
# checked state in their visible text. NVDA reads wx.CheckListBox items
# as plain list entries on Windows — it doesn't pick up the checkbox
# state — so we bake "checked: " or "not checked: " into the item text
# itself. Toggling an item updates the prefix immediately so the next
# arrow-key landing on it (or the toggle re-announce) reads correctly.

class MeterPanel(wx.Panel):
    """One meter's worth of labeled fields, grouped in a StaticBox.

    Used in the room-type editor as a replacement for the previous pipe-
    separated text format. Each field has its own label and TextCtrl, so
    NVDA reads each as a discrete "Meter group, Internal name edit, Food"
    — no need for the user to remember field order or syntax.

    Cognitive accessibility:
    - All fields labeled in plain language (no `key`, `verb_present`, etc.)
    - Sensible defaults pre-fill the boilerplate fields ("Refill",
      "Refilled", "empty in", "empty", "full") so a new meter only needs
      its internal name + display label.
    - decay_seconds is marked optional and accepts plain-language durations.
    - 'Remove this meter' button at the bottom of the group.
    """

    # (key, label, default, placeholder). Order = display order = NVDA tab order.
    # Labels carry an example in parentheses so the user sees what the field
    # expects on first focus — no second-guessing about whether a phrase or
    # single word is wanted, no need to read separate help text.
    FIELDS = [
        ("key",          "Internal name (lowercase, no spaces)",                 "",          "e.g. food"),
        ("label",        "Display label, what players hear",                     "",          "e.g. Food"),
        ("verb_present", "Refill button label",                                  "Refill",    "e.g. Refill"),
        ("verb_past",    "Past-tense word for the refill log",                   "Refilled",  "e.g. Refilled"),
        ("low_word",     "Status phrase when partly empty (e.g. 'empty in')",    "empty in",  "e.g. empty in"),
        ("empty_word",   "Status phrase when fully empty (e.g. 'empty')",        "empty",     "e.g. empty"),
        ("full_word",    "Status phrase when full (e.g. 'full')",                "full",      "e.g. full"),
    ]

    def __init__(self, parent, on_remove, meter=None):
        super().__init__(parent)
        self.on_remove = on_remove
        meter = meter or {}
        self.fields = {}

        # Box label is dynamic: derived from the meter's display label
        # (preferred), then internal name, else "Meter (new)". Updates
        # live via EVT_TEXT below so NVDA reads "Food meter group" and
        # not "Meter group" three times in a row when there are 3 meters.
        self._box = wx.StaticBox(self, label=self._box_label_for(meter))
        box_sizer = wx.StaticBoxSizer(self._box, wx.VERTICAL)

        grid = wx.FlexGridSizer(rows=0, cols=2, hgap=10, vgap=4)
        grid.AddGrowableCol(1, 1)

        for key, label_text, default, placeholder in self.FIELDS:
            initial = str(meter.get(key, default))
            lbl = wx.StaticText(self, label=label_text + ":")
            ctrl = wx.TextCtrl(self, value=initial)
            ctrl.SetName(label_text)
            if placeholder and not initial:
                ctrl.SetHint(placeholder)
            grid.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)
            self.fields[key] = ctrl

        # decay_seconds is special: stored as int seconds in JSON, shown
        # as human-readable in the editor, marked optional in the label.
        # The label spells out an example unit so the user knows what to
        # type without hunting for help text — they may type '1 hour',
        # '30 minutes', '1h 30m', etc. (parsed by parse_duration()).
        decay_label = "How long full-to-empty, e.g. '1 hour' or '30 minutes' (optional)"
        decay_lbl = wx.StaticText(self, label=decay_label + ":")
        decay_val = meter.get("decay_seconds", "")
        if isinstance(decay_val, (int, float)) and decay_val > 0:
            initial_decay = format_duration_human(int(decay_val))
        else:
            initial_decay = ""
        decay_ctrl = wx.TextCtrl(self, value=initial_decay)
        decay_ctrl.SetName(decay_label)
        if not initial_decay:
            decay_ctrl.SetHint("e.g. 1 hour — blank uses the default from Settings")
        grid.Add(decay_lbl, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(decay_ctrl, 1, wx.EXPAND)
        self.fields["decay_seconds"] = decay_ctrl

        # Live-update the StaticBox label as the user types in the display
        # label or internal name fields. NVDA re-announces the group label
        # next time the user enters/leaves the group, so the live update
        # ensures distinct identification ("Food meter", "Water meter")
        # without forcing a save+reopen cycle.
        self.fields["label"].Bind(wx.EVT_TEXT, lambda evt: self._refresh_box_label())
        self.fields["key"].Bind(wx.EVT_TEXT, lambda evt: self._refresh_box_label())

        box_sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 6)

        remove_btn = wx.Button(self, label="Remove this meter")
        remove_btn.Bind(wx.EVT_BUTTON, lambda evt: self.on_remove(self))
        box_sizer.Add(remove_btn, 0, wx.LEFT | wx.BOTTOM, 6)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(box_sizer, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(outer)

    @staticmethod
    def _box_label_for(meter_or_form):
        """Pick the most informative label for the StaticBox header.

        Accepts either a meter dict (from JSON) or a dict-like with
        'label' and 'key' string fields (from live form values). Trims
        and falls back through display label → internal name → "Meter
        (new)".
        """
        label = (meter_or_form.get("label") or "").strip()
        key = (meter_or_form.get("key") or "").strip()
        if label:
            return f"{label} meter"
        if key:
            return f"'{key}' meter"
        return "Meter (new)"

    def _refresh_box_label(self):
        current = {
            "label": self.fields["label"].GetValue(),
            "key": self.fields["key"].GetValue(),
        }
        self._box.SetLabel(self._box_label_for(current))
        # SetLabel may resize the box header; re-layout the panel so the
        # new title fits without graphical artefacts on sighted setups.
        self.Layout()

    def get_data(self):
        """Return the meter dict for JSON, or raise ValueError on bad input.

        Required: 'key' must be non-empty (raises with a friendly message
        that names the meter by its display label so the user knows which
        one). decay_seconds, if non-blank, is parsed via parse_duration();
        if blank, the key is omitted entirely so the runtime falls back to
        the global default.
        """
        result = {}
        for key, ctrl in self.fields.items():
            val = ctrl.GetValue().strip()
            if key == "decay_seconds":
                if val:
                    # parse_duration may raise ValueError; let it propagate
                    # to the caller so they show a per-meter error message.
                    result[key] = parse_duration(val)
                # blank → omit so it falls back to the global default
            else:
                result[key] = val
        if not result.get("key"):
            label = result.get("label") or "(unnamed)"
            raise ValueError(
                f"The meter '{label}' is missing its internal name. "
                "Type a lowercase identifier like 'food' or 'water', "
                "or click 'Remove this meter' if you don't want it."
            )
        return result


class IngredientPanel(wx.Panel):
    """One build-recipe ingredient: an item picker + a count + a Remove button.

    The item picker is a wx.ComboBox seeded with everything from
    ITEMS_COMMON + ITEMS_UNCOMMON (sorted, deduped). Editable, so the
    user can type a brand-new item if they want — but the dropdown
    means the common case (picking from existing items) is one click,
    no text-file hopping.

    Cognitive accessibility:
    - Visible labels next to each control + SetName for NVDA.
    - One ingredient = one compact row. Tab order is item, count, remove.
    """

    def __init__(self, parent, on_remove, item_name="", count=1):
        super().__init__(parent)
        self.on_remove = on_remove

        # Pull a fresh list every panel — the user may have opened the
        # text-files-managing UI and added items between dialogs. Sorted
        # for predictable NVDA reading order.
        choices = sorted(set(list(ITEMS_COMMON) + list(ITEMS_UNCOMMON)))

        row = wx.BoxSizer(wx.HORIZONTAL)

        item_lbl = wx.StaticText(self, label="Item:")
        row.Add(item_lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)

        self.item_ctrl = wx.ComboBox(
            self, choices=choices, value=str(item_name),
            style=wx.CB_DROPDOWN,
        )
        self.item_ctrl.SetName("Item")
        if not item_name:
            self.item_ctrl.SetHint("pick from list, or type a new name")
        row.Add(self.item_ctrl, 1, wx.EXPAND | wx.RIGHT, 8)

        count_lbl = wx.StaticText(self, label="Count:")
        row.Add(count_lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)

        self.count_ctrl = wx.SpinCtrl(self, min=1, max=999, initial=int(count or 1))
        self.count_ctrl.SetName("Count")
        row.Add(self.count_ctrl, 0, wx.RIGHT, 8)

        remove_btn = wx.Button(self, label="Remove this ingredient")
        remove_btn.Bind(wx.EVT_BUTTON, lambda evt: self.on_remove(self))
        row.Add(remove_btn, 0)

        self.SetSizer(row)

    def get_data(self):
        """Return (item_name, count). Raises ValueError if item name is blank."""
        name = self.item_ctrl.GetValue().strip()
        count = int(self.count_ctrl.GetValue())
        if not name:
            raise ValueError(
                "An ingredient row is missing its item. Pick one from the "
                "list, type a new item name, or click 'Remove this "
                "ingredient' if you don't want it."
            )
        if count < 1:
            count = 1
        return name, count


class RoomTypeEditorDialog(wx.Dialog):
    """Add or edit a room type. Save writes
    assets/types/room_types/<id>.json. Caller is responsible for calling
    load_types() afterward.
    """

    def __init__(self, parent, type_id=None):
        title = "Edit room type" if type_id else "Add new room type"
        super().__init__(parent, title=title, size=(720, 920))
        self.original_id = type_id
        self.controls = {}
        self._build()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        if self.original_id:
            spec = dict(ROOM_TYPES.get(self.original_id) or {})
        else:
            spec = {
                "default_slots": 4,
                "meters": [
                    {"key": "food", "label": "Food",
                     "verb_present": "Refill", "verb_past": "Refilled",
                     "low_word": "empty in", "empty_word": "empty",
                     "full_word": "full"},
                    {"key": "water", "label": "Water",
                     "verb_present": "Refill", "verb_past": "Refilled",
                     "low_word": "empty in", "empty_word": "empty",
                     "full_word": "full"},
                ],
                "build_recipe": {"stick": 4},
            }
        # Keep the originally-loaded spec so on_save can preserve fields this
        # editor doesn't expose (e.g. treasure_cost) instead of dropping them
        # by rebuilding the dict from scratch.
        self._loaded_spec = dict(spec)

        grid = wx.FlexGridSizer(rows=0, cols=2, hgap=12, vgap=6)
        grid.AddGrowableCol(1, 1)

        def add_text(key, label, default=""):
            lbl = wx.StaticText(self, label=label + ":")
            ctrl = wx.TextCtrl(self, value=str(spec.get(key, default)))
            ctrl.SetName(label)
            grid.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)
            self.controls[key] = ctrl

        # Room-type ID is the first field NVDA reads when the dialog opens.
        # Spelling out "Room type ID" (rather than just "ID") removes the
        # ambiguity with the per-meter internal name field below.
        add_text("id", "Room type ID (lowercase, no spaces)")
        if self.original_id:
            self.controls["id"].Disable()
        add_text("name", "Display name")
        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)

        sizer.Add(wx.StaticText(self, label="Description:"),
                  0, wx.LEFT | wx.RIGHT, 10)
        self.desc_ctrl = wx.TextCtrl(
            self, style=wx.TE_MULTILINE,
            value=str(spec.get("description", "")), size=(-1, 50),
        )
        self.desc_ctrl.SetName("Description")
        sizer.Add(self.desc_ctrl, 0, wx.ALL | wx.EXPAND, 6)

        slots_row = wx.BoxSizer(wx.HORIZONTAL)
        slots_row.Add(wx.StaticText(self, label="Default slots:"),
                      0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.slots_ctrl = wx.SpinCtrl(
            self, min=1, max=100, initial=int(spec.get("default_slots", 4)),
        )
        self.slots_ctrl.SetName("Default slots")
        slots_row.Add(self.slots_ctrl, 0)
        sizer.Add(slots_row, 0, wx.ALL, 10)

        # Compatible species — wrapped in a StaticBox so NVDA reads
        # "Compatible species group" on entry. The relationship is
        # one-sided on disk (species own `compatible_room_types`,
        # room types don't carry the reverse list any more), but
        # this checklist is editable as a *second view* on the same
        # data: ticks here propagate to species JSONs at save time.
        # Either editor lets you change the same relationship; the
        # species file is the only place it actually lives.
        compat_box_widget = wx.StaticBox(self, label="Compatible species")
        compat_box_sizer = wx.StaticBoxSizer(compat_box_widget, wx.VERTICAL)
        compat_intro = wx.StaticText(
            self,
            label=("Tick every species that's allowed to live in this "
                   "room type. Saving this dialog updates each ticked "
                   "species' 'Compatible room types' to include this "
                   "room type, and removes it from any unticked species. "
                   "Use arrow keys to move between species, Space to "
                   "toggle the highlighted one."),
        )
        compat_intro.Wrap(640)
        compat_intro.SetForegroundColour(wx.Colour(90, 90, 90))
        compat_box_sizer.Add(compat_intro, 0, wx.ALL, 4)

        self._species_ids = list(SPECIES_DATA.keys())
        species_items = [
            (s, f"{SPECIES_DATA[s]['spec'].get('name', s)} ({s})")
            for s in self._species_ids
        ]
        # Initial check state is the species-derived compat list, NOT
        # whatever the legacy `compatible_species` field on the room-
        # type JSON happens to say. That makes this view truthful even
        # for room types whose JSONs predate the single-source-of-truth
        # change.
        if self.original_id:
            initial_checked = room_type_compatible_species(self.original_id)
        else:
            initial_checked = []
        self.compat_box = make_state_announcing_checklist(
            self,
            "Compatible species",
            species_items,
            checked_ids=initial_checked,
        )
        compat_box_sizer.Add(self.compat_box, 1, wx.ALL | wx.EXPAND, 4)
        sizer.Add(compat_box_sizer, 0, wx.ALL | wx.EXPAND, 8)

        # ---- Meters group ------------------------------------------------
        # Wrap the whole meters section in a StaticBox so NVDA reads the
        # intro text as part of the "Meters group" — not as ambient dialog
        # text that gets confused with the dialog caption.
        meters_box = wx.StaticBox(self, label="Meters")
        meters_box_sizer = wx.StaticBoxSizer(meters_box, wx.VERTICAL)

        meters_intro = wx.StaticText(
            self,
            label=("Each meter is one care stat the player tops up "
                   "(food, water, etc.). New meters start with sensible "
                   "defaults — 'Refill', 'Refilled', 'empty in', 'empty', "
                   "'full' — that you can change in any field below. The "
                   "'How long full-to-empty' field is optional and accepts "
                   "plain language like '1 hour' or '30 minutes'; leave it "
                   "blank to use the default from Settings."),
        )
        meters_intro.Wrap(640)
        meters_intro.SetForegroundColour(wx.Colour(90, 90, 90))
        meters_box_sizer.Add(meters_intro, 0, wx.ALL, 6)

        # Scrolled container so the dialog stays a sane size when there are
        # many meters. NVDA navigates the contents like any other panel —
        # the scroll is for sighted users; tab order goes through every
        # meter in sequence regardless of what's visible.
        self.meters_scroll = wx.ScrolledWindow(self, style=wx.VSCROLL | wx.BORDER_SIMPLE)
        self.meters_scroll.SetScrollRate(0, 16)
        self.meters_scroll.SetMinSize((-1, 260))
        self.meters_scroll_sizer = wx.BoxSizer(wx.VERTICAL)
        self.meters_scroll.SetSizer(self.meters_scroll_sizer)
        meters_box_sizer.Add(self.meters_scroll, 1, wx.EXPAND | wx.ALL, 4)

        self.meter_panels = []
        for meter in spec.get("meters", []):
            self._add_meter_panel(meter)

        add_meter_btn = wx.Button(self, label="Add meter")
        add_meter_btn.Bind(wx.EVT_BUTTON, self.on_add_meter)
        meters_box_sizer.Add(add_meter_btn, 0, wx.ALL, 6)

        sizer.Add(meters_box_sizer, 1, wx.EXPAND | wx.ALL, 8)

        # ---- Build recipe group ------------------------------------------
        recipe_box = wx.StaticBox(self, label="Build recipe")
        recipe_box_sizer = wx.StaticBoxSizer(recipe_box, wx.VERTICAL)

        recipe_intro = wx.StaticText(
            self,
            label=("Items the player must spend to build one of these "
                   "rooms. Each row is one ingredient — pick an item from "
                   "the dropdown (or type a new one) and set how many. "
                   "Use 'Add ingredient' for more rows."),
        )
        recipe_intro.Wrap(640)
        recipe_intro.SetForegroundColour(wx.Colour(90, 90, 90))
        recipe_box_sizer.Add(recipe_intro, 0, wx.ALL, 6)

        self.ingredients_scroll = wx.ScrolledWindow(self, style=wx.VSCROLL | wx.BORDER_SIMPLE)
        self.ingredients_scroll.SetScrollRate(0, 16)
        self.ingredients_scroll.SetMinSize((-1, 120))
        self.ingredients_scroll_sizer = wx.BoxSizer(wx.VERTICAL)
        self.ingredients_scroll.SetSizer(self.ingredients_scroll_sizer)
        recipe_box_sizer.Add(self.ingredients_scroll, 0, wx.EXPAND | wx.ALL, 4)

        self.ingredient_panels = []
        for item_name, count in (spec.get("build_recipe") or {}).items():
            self._add_ingredient_panel(item_name, count)

        add_ing_btn = wx.Button(self, label="Add ingredient")
        add_ing_btn.Bind(wx.EVT_BUTTON, self.on_add_ingredient)
        recipe_box_sizer.Add(add_ing_btn, 0, wx.ALL, 6)

        sizer.Add(recipe_box_sizer, 0, wx.EXPAND | wx.ALL, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        save_btn = wx.Button(self, label="Save")
        save_btn.Bind(wx.EVT_BUTTON, self.on_save)
        save_btn.SetDefault()
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "Cancel")
        btns.AddStretchSpacer()
        btns.Add(save_btn, 0, wx.RIGHT, 8)
        btns.Add(cancel_btn, 0)
        sizer.Add(btns, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

    def _add_meter_panel(self, meter_data=None):
        """Append a new meter group to the scrolled list. Returns the panel
        so callers (e.g. on_add_meter) can move focus to it.
        """
        panel = MeterPanel(self.meters_scroll, self._remove_meter_panel, meter=meter_data)
        self.meter_panels.append(panel)
        self.meters_scroll_sizer.Add(panel, 0, wx.EXPAND | wx.ALL, 4)
        self.meters_scroll.Layout()
        self.meters_scroll.FitInside()
        return panel

    def _remove_meter_panel(self, panel):
        if panel not in self.meter_panels:
            return
        self.meter_panels.remove(panel)
        self.meters_scroll_sizer.Detach(panel)
        panel.Destroy()
        self.meters_scroll.Layout()
        self.meters_scroll.FitInside()

    def on_add_meter(self, evt):
        panel = self._add_meter_panel(meter_data=None)
        # Move focus to the new meter's first field so NVDA announces it
        # immediately — otherwise focus stays on the Add button.
        first_field = panel.fields.get("key")
        if first_field:
            first_field.SetFocus()

    def _add_ingredient_panel(self, item_name="", count=1):
        panel = IngredientPanel(
            self.ingredients_scroll, self._remove_ingredient_panel,
            item_name=item_name, count=count,
        )
        self.ingredient_panels.append(panel)
        self.ingredients_scroll_sizer.Add(panel, 0, wx.EXPAND | wx.ALL, 4)
        self.ingredients_scroll.Layout()
        self.ingredients_scroll.FitInside()
        return panel

    def _remove_ingredient_panel(self, panel):
        if panel not in self.ingredient_panels:
            return
        self.ingredient_panels.remove(panel)
        self.ingredients_scroll_sizer.Detach(panel)
        panel.Destroy()
        self.ingredients_scroll.Layout()
        self.ingredients_scroll.FitInside()

    def on_add_ingredient(self, evt):
        panel = self._add_ingredient_panel()
        # Land focus on the new ingredient's item picker so NVDA announces
        # the dropdown right away.
        panel.item_ctrl.SetFocus()

    def on_save(self, evt):
        if self.original_id:
            tid = self.original_id
        else:
            tid = _slugify_id(self.controls["id"].GetValue())
            if not tid:
                wx.MessageBox(
                    "Please give the room type an ID.",
                    "Add room type", wx.OK | wx.ICON_WARNING, self,
                )
                return
            if tid in ROOM_TYPES:
                wx.MessageBox(
                    f"A room type with ID '{tid}' already exists.",
                    "Add room type", wx.OK | wx.ICON_WARNING, self,
                )
                return

        display_name = self.controls["name"].GetValue().strip()
        if not display_name:
            wx.MessageBox(
                "Please give the room type a display name.",
                "Save room type", wx.OK | wx.ICON_WARNING, self,
            )
            return

        if not self.meter_panels:
            wx.MessageBox(
                "Please add at least one meter (click 'Add meter').",
                "Save room type", wx.OK | wx.ICON_WARNING, self,
            )
            return

        meters = []
        seen_keys = set()
        for i, panel in enumerate(self.meter_panels, start=1):
            try:
                data = panel.get_data()
            except ValueError as e:
                wx.MessageBox(
                    f"Meter {i}: {e}",
                    "Save room type", wx.OK | wx.ICON_WARNING, self,
                )
                # Move focus to the offending meter's first field so the
                # user can fix it without hunting.
                first_field = panel.fields.get("key")
                if first_field:
                    first_field.SetFocus()
                return
            key = data["key"]
            if key in seen_keys:
                wx.MessageBox(
                    f"Two meters share the internal name '{key}'. "
                    "Each meter needs a unique internal name "
                    "(like 'food' and 'water').",
                    "Save room type", wx.OK | wx.ICON_WARNING, self,
                )
                first_field = panel.fields.get("key")
                if first_field:
                    first_field.SetFocus()
                return
            seen_keys.add(key)
            meters.append(data)

        if not self.ingredient_panels:
            wx.MessageBox(
                "Please add at least one ingredient (click 'Add ingredient').",
                "Save room type", wx.OK | wx.ICON_WARNING, self,
            )
            return
        recipe = {}
        for i, panel in enumerate(self.ingredient_panels, start=1):
            try:
                ingredient_name, count = panel.get_data()
            except ValueError as e:
                wx.MessageBox(
                    f"Ingredient {i}: {e}",
                    "Save room type", wx.OK | wx.ICON_WARNING, self,
                )
                panel.item_ctrl.SetFocus()
                return
            if ingredient_name in recipe:
                wx.MessageBox(
                    f"Two ingredients use the item '{ingredient_name}'. "
                    "Combine them into one row, or use a different item.",
                    "Save room type", wx.OK | wx.ICON_WARNING, self,
                )
                panel.item_ctrl.SetFocus()
                return
            recipe[ingredient_name] = count

        # Pre-validate the propagation BEFORE writing the room-type JSON
        # so a refusal here doesn't leave the room type half-saved.
        # The species editor refuses to save a species with empty
        # `compatible_room_types`; this editor must enforce the same
        # invariant from the other side, otherwise unticking the only
        # species that listed this room type would silently leave that
        # species stranded in the village forever.
        checked_species = set(checklist_get_checked_ids(self.compat_box))
        would_strand = []
        for sid, data in SPECIES_DATA.items():
            current_compat = list(
                (data.get("spec") or {}).get("compatible_room_types") or []
            )
            should_be_in = sid in checked_species
            is_in = tid in current_compat
            # Only the unchecked-but-currently-in case can strand —
            # removing tid from this species's compat list. If tid is
            # the only entry, the species ends up empty.
            if not should_be_in and is_in and current_compat == [tid]:
                spec_name = (data.get("spec") or {}).get("name", sid)
                would_strand.append(spec_name)
        if would_strand:
            wx.MessageBox(
                "Can't untick: " + ", ".join(would_strand) + ". "
                "This room type is the only one each of those species "
                "lists as compatible — unticking would leave them with "
                "nowhere to live (creatures stuck in the village "
                "forever). Either keep them ticked here, or first edit "
                "the species (File → Species → Edit) to add another "
                "compatible room type.",
                "Save room type", wx.OK | wx.ICON_WARNING, self,
            )
            self.compat_box.SetFocus()
            return

        # Start from the loaded spec so fields this editor doesn't expose
        # (notably treasure_cost — the Glade ships treasure_cost: 1) survive
        # the edit, then overwrite the keys the editor owns. Rebuilding from
        # {} used to silently drop treasure_cost, making a treasure-gated
        # room type free to build after any routine edit.
        spec = dict(self._loaded_spec)
        spec.update({
            "id": tid,
            "name": display_name,
            "description": self.desc_ctrl.GetValue().strip(),
            "meters": meters,
            "build_recipe": recipe,
            "default_slots": int(self.slots_ctrl.GetValue()),
        })
        # The legacy `compatible_species` field is intentionally NOT persisted
        # — species' `compatible_room_types` is the single source of truth (the
        # compat checklist above propagates to the species JSONs). Drop any
        # stale copy carried in from the loaded spec.
        spec.pop("compatible_species", None)

        ROOM_TYPES_DIR.mkdir(parents=True, exist_ok=True)
        path = ROOM_TYPES_DIR / f"{tid}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(spec, f, indent=2)
        except OSError as e:
            wx.MessageBox(
                f"Couldn't write {path}: {e}",
                "Save room type", wx.OK | wx.ICON_ERROR, self,
            )
            return

        # Propagate the Compatible species checklist to each species'
        # `compatible_room_types`. The room-type editor is a "second
        # view" on the same relationship that the species editor edits;
        # both directions write to the same place (species JSONs). For
        # each species: if checked here, ensure tid is in its compat
        # list; if not checked, ensure tid is NOT in its compat list.
        # No-op when the species' list already matches.
        for sid, data in list(SPECIES_DATA.items()):
            species_spec_dict = dict(data.get("spec") or {})
            current_compat = list(
                species_spec_dict.get("compatible_room_types") or []
            )
            should_be_in = sid in checked_species
            is_in = tid in current_compat
            if should_be_in and not is_in:
                current_compat.append(tid)
            elif not should_be_in and is_in:
                current_compat = [c for c in current_compat if c != tid]
            else:
                continue
            species_spec_dict["compatible_room_types"] = current_compat
            species_path = SPECIES_DIR / f"{sid}.json"
            try:
                with open(species_path, "w", encoding="utf-8") as f:
                    json.dump(species_spec_dict, f, indent=2)
            except OSError as e:
                wx.MessageBox(
                    f"Couldn't update {species_path}: {e}",
                    "Save room type", wx.OK | wx.ICON_ERROR, self,
                )
                return
        # Reload so the in-memory SPECIES_DATA reflects the new compat
        # lists. The caller (ManageRoomTypesDialog) also reloads, but
        # doing it here keeps the in-memory view consistent if anything
        # downstream reads SPECIES_DATA before that.
        load_types()
        load_text_assets()

        self.EndModal(wx.ID_OK)


class SpeciesDialog(wx.Dialog):
    """Single Species dialog — replaces the old WelcomeDialog plus the
    Mods → Manage species and Mods → Add or remove extra species items.

    One door for everything the player or modder might do with species:
    pick a starter pair from the loaded library, design a new species
    from scratch, edit an existing one, or delete one. Browser-style:
    the dialog stays open after each action so the player can keep
    curating without re-opening it.

    Auto-opens at first launch when the save is fresh (see
    `state_is_fresh`) so a brand-new player has a clear first step.
    Re-openable any time from File → Species.

    Anti-completionism design: no 'install all', no counts, no progress
    bar, single-species detail-on-select. The user has an OCD-flavored
    pull toward 'must take them all home'; the picker should feel like
    a library of invitations, not a checklist.
    """

    def __init__(self, parent_frame):
        super().__init__(
            parent_frame,
            title="Species",
            size=(680, 600),
        )
        self.frame = parent_frame
        # Escape closes via the Cancel id (the "Close" button below
        # uses ID_CANCEL so Escape and the button do the same thing).
        self.SetEscapeId(wx.ID_CANCEL)
        self._build()
        self._refresh_list()
        self._update_detail()

    def _build(self):
        outer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.TextCtrl(
            self,
            style=(
                wx.TE_READONLY | wx.TE_MULTILINE
                | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE
            ),
            value=(
                "Pick a species to bring into your park, or design a "
                "new one. You'll start with a small pair of babies in "
                "your village; adopt them into a room when you're "
                "ready, and watch them grow up. You can also edit or "
                "delete any species from here.\n\n"
                "Open this screen any time from File → Species — feel "
                "free to start with just one species and add more later."
            ),
            size=(-1, 96),
        )
        intro.SetName("Species help")
        outer.Add(intro, 0, wx.ALL | wx.EXPAND, 10)

        # ---- Species list (top half) -----------------------------------
        list_lbl = wx.StaticText(self, label="Available species:")
        outer.Add(list_lbl, 0, wx.LEFT | wx.RIGHT, 10)
        self.list_box = wx.ListBox(self, choices=[])
        self.list_box.SetName("Available species")
        self.list_box.Bind(wx.EVT_LISTBOX, self._on_select)
        # Double-click on a species fires Bring Home, mirroring how
        # most pickers behave — extra affordance for mouse users.
        self.list_box.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.on_bring_home(e))
        outer.Add(self.list_box, 1, wx.ALL | wx.EXPAND, 10)

        # ---- Detail panel (lower half) ---------------------------------
        detail_lbl = wx.StaticText(self, label="About the selected species:")
        outer.Add(detail_lbl, 0, wx.LEFT | wx.RIGHT, 10)
        self.detail = wx.TextCtrl(
            self,
            style=(
                wx.TE_READONLY | wx.TE_MULTILINE
                | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE
            ),
            size=(-1, 132),
        )
        self.detail.SetName("Selected species details")
        outer.Add(self.detail, 0, wx.ALL | wx.EXPAND, 10)

        # ---- Action buttons --------------------------------------------
        # Two rows so 'things you do TO the selected species' read as
        # a group, separately from 'things you do regardless'. Five
        # actions total, but visually chunked = below the cogacc
        # ≤5-things-per-screen rule of thumb.
        per_species_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.bring_btn = wx.Button(self, label="&Bring them home")
        self.bring_btn.Bind(wx.EVT_BUTTON, self.on_bring_home)
        self.bring_btn.SetDefault()
        self.edit_btn = wx.Button(self, label="&Edit selected…")
        self.edit_btn.Bind(wx.EVT_BUTTON, self.on_edit)
        self.delete_btn = wx.Button(self, label="&Delete selected…")
        self.delete_btn.Bind(wx.EVT_BUTTON, self.on_delete)
        per_species_btns.Add(self.bring_btn, 0, wx.RIGHT, 8)
        per_species_btns.Add(self.edit_btn, 0, wx.RIGHT, 8)
        per_species_btns.Add(self.delete_btn, 0, wx.RIGHT, 8)
        outer.Add(per_species_btns, 0, wx.ALL | wx.EXPAND, 10)

        standalone_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.create_btn = wx.Button(self, label="&Create a new species…")
        self.create_btn.Bind(wx.EVT_BUTTON, self.on_create)
        close_btn = wx.Button(self, wx.ID_CANCEL, "Cl&ose")
        standalone_btns.Add(self.create_btn, 0, wx.RIGHT, 8)
        standalone_btns.AddStretchSpacer()
        standalone_btns.Add(close_btn, 0)
        outer.Add(standalone_btns, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.SetSizer(outer)
        # Land focus on the list so arrow keys + NVDA read species
        # immediately, rather than starting on the help intro.
        self.list_box.SetFocus()

    def _refresh_list(self, select_id=None):
        """Rebuild the species list from current SPECIES_DATA. Sorted by
        display name so the alphabetical scan matches what the player
        reads in the picker. If `select_id` is given and present, that
        species is selected after the rebuild — used to keep focus on
        a species the player just edited or just created.
        """
        self.list_box.Clear()
        items = sorted(
            SPECIES_DATA.items(),
            key=lambda kv: (kv[1].get("spec", {}).get("name", kv[0]) or kv[0]).lower(),
        )
        index_to_select = 0
        for i, (sid, data) in enumerate(items):
            spec = data.get("spec", {})
            display_name = spec.get("name", sid)
            self.list_box.Append(display_name, sid)
            if select_id and sid == select_id:
                index_to_select = i
        if self.list_box.GetCount() > 0:
            self.list_box.SetSelection(index_to_select)

    def _selected_id(self):
        sel = self.list_box.GetSelection()
        if sel < 0:
            return None
        return self.list_box.GetClientData(sel)

    def _on_select(self, _evt):
        self._update_detail()

    def _update_detail(self):
        """Render the detail panel for the currently selected species
        and update the per-species button labels + enable states.
        """
        sid = self._selected_id()
        if not sid:
            self.detail.SetValue(
                "No species available yet. Click 'Create a new species…' "
                "to design one from scratch."
            )
            self.bring_btn.SetLabel("&Bring them home")
            self.bring_btn.Disable()
            self.edit_btn.SetLabel("&Edit selected…")
            self.edit_btn.Disable()
            self.delete_btn.SetLabel("&Delete selected…")
            self.delete_btn.Disable()
            return
        self.bring_btn.Enable()
        self.edit_btn.Enable()
        self.delete_btn.Enable()
        data = SPECIES_DATA.get(sid, {})
        spec = data.get("spec", {})
        name = spec.get("name", sid)
        plural = spec.get("name_plural", name + "s")
        self.bring_btn.SetLabel(f"&Bring {plural.lower()} home")
        self.edit_btn.SetLabel(f"&Edit {name.lower()}…")
        self.delete_btn.SetLabel(f"&Delete {name.lower()}…")

        # Room types: render as "indoor rooms" / "outdoor or aviary rooms".
        compat_ids = spec.get("compatible_room_types") or []
        compat_names = []
        for rid in compat_ids:
            rt = ROOM_TYPES.get(rid, {})
            compat_names.append(rt.get("name", rid).lower())
        if not compat_names:
            rooms_line = (
                "no room type yet — you'll need to add one before they "
                "can move out of the village"
            )
        elif len(compat_names) == 1:
            rooms_line = f"{compat_names[0]} rooms"
        else:
            rooms_line = " or ".join(compat_names) + " rooms"

        # Litter range: best effort using species_litter_size_range.
        try:
            min_b, max_b = species_litter_size_range(spec)
        except Exception:
            min_b, max_b = 1, 1
        litter_word = _spec_litter_label(spec)
        if min_b == max_b:
            litter_line = f"a {litter_word} of {min_b}"
        else:
            litter_line = f"a {litter_word} of {min_b}-{max_b}"

        # A taste of the writing — first description and pet response so
        # the player gets the *feel* of the species, not just stats.
        descs = data.get("descriptions") or []
        pets = data.get("pet_responses") or []
        sample_desc = descs[0] if descs else "(no descriptions written yet)"
        sample_pet_template = pets[0] if pets else None
        if sample_pet_template:
            sample_pet = sample_pet_template.replace("{name}", name)
        else:
            sample_pet = "(no pet responses written yet)"

        care = spec.get("care_action_label", "Pet")
        in_use = _species_in_use_count(self.frame.state, sid)
        if in_use == 0:
            in_use_line = "Currently in your park: none yet."
        elif in_use == 1:
            in_use_line = "Currently in your park: 1 creature."
        else:
            in_use_line = f"Currently in your park: {in_use} creatures."

        lines = [
            f"{name} ({plural})",
            "",
            f"They live in: {rooms_line}",
            f"They arrive in: {litter_line}",
            f"Care action: {care}",
            in_use_line,
            "",
            f"Sample description: {sample_desc}",
            f"Sample {care.lower()} response: {sample_pet}",
        ]
        self.detail.SetValue("\n".join(lines))

    # ---- Action handlers -------------------------------------------------
    # All four actions keep the dialog open so the player can keep
    # curating. The list refreshes (and selection re-anchors where it
    # makes sense) after each successful action.

    def on_bring_home(self, _evt):
        sid = self._selected_id()
        if not sid:
            return
        if not seed_village_pair(self.frame.state, sid):
            wx.MessageBox(
                "Couldn't add a starter pair — the species spec may be "
                "missing or invalid. Try Edit selected to check.",
                "Species", wx.OK | wx.ICON_WARNING, self,
            )
            return
        save_state(self.frame.state)
        self._announce_brought_home(sid)
        self.frame.save_and_refresh()
        # Re-render the detail so the "Currently in your park" line
        # reflects the new pair without requiring a re-select.
        self._update_detail()

    def on_edit(self, _evt):
        sid = self._selected_id()
        if not sid:
            return
        with SpeciesEditorDialog(self, species_id=sid) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
        load_types()
        load_text_assets()
        self._refresh_list(select_id=sid)
        self._update_detail()
        self.frame.announce_event("species_saved")
        # The edit might have changed display values used elsewhere
        # (litter label, room compatibility, etc.), so refresh the
        # game panels too.
        self.frame.save_and_refresh()

    def on_create(self, _evt):
        with SpeciesEditorDialog(self) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            sid = dlg.saved_species_id()
            should_seed = dlg.should_seed_starter_pair()
        load_types()
        load_text_assets()
        if sid and should_seed and seed_village_pair(self.frame.state, sid):
            save_state(self.frame.state)
            self._announce_brought_home(sid)
        elif sid:
            self.frame.announce_event("species_added")
        self._refresh_list(select_id=sid)
        self._update_detail()
        self.frame.save_and_refresh()

    def on_delete(self, _evt):
        sid = self._selected_id()
        if not sid:
            return
        spec = (SPECIES_DATA.get(sid) or {}).get("spec") or {}
        # Defense in depth: sanitize text_directory before joining it
        # into a filesystem path. The species editor sanitizes on save,
        # so any spec written through the UI is safe — but a hand-
        # edited JSON could still contain '..' or backslashes. Slugging
        # the value here means the delete operates on a constrained
        # path even when the spec on disk is malformed.
        raw_text_dir = spec.get("text_directory") or sid
        text_dir_name = _slugify_id(raw_text_dir) or sid
        species_text_dir = TEXT_DIR / "species" / text_dir_name
        # text_directory is a deliberately-shareable field. If any OTHER
        # loaded species resolves to the same text folder, deleting it would
        # strip that survivor's name / description / colour pools — so in
        # that case only the JSON is removed, never the shared folder.
        text_dir_shared = any(
            other_sid != sid
            and (_slugify_id((other.get("spec") or {}).get("text_directory")
                             or other_sid) or other_sid) == text_dir_name
            for other_sid, other in SPECIES_DATA.items()
        )
        in_use = _species_in_use_count(self.frame.state, sid)

        place_name = self.frame.state.get("village_name", "Village").lower()
        msg_lines = [f"Delete species '{spec.get('name', sid)}'?", ""]
        if in_use > 0:
            msg_lines.append(
                f"{in_use} creature(s) of this species will also be "
                f"removed from your save (rooms, {place_name}, and "
                f"any pending births)."
            )
            msg_lines.append("")
        msg_lines.append("This will remove:")
        msg_lines.append(f"  - user_data/types/species/{sid}.json")
        if not text_dir_shared:
            msg_lines.append(
                f"  - user_data/text/species/{text_dir_name}/ "
                f"(and all text files inside)"
            )
        else:
            msg_lines.append(
                f"  (the text folder user_data/text/species/{text_dir_name}/ "
                f"is shared with another species, so it will be kept)"
            )
        msg_lines.append(
            "  (the shipped factory copy in assets/ is not affected)"
        )
        msg_lines.append("")
        msg_lines.append("Other species and rooms will not be touched.")

        confirm = wx.MessageBox(
            "\n".join(msg_lines), "Delete species",
            wx.YES_NO | wx.ICON_QUESTION, self,
        )
        if confirm != wx.YES:
            return

        if in_use > 0:
            _purge_species_from_state(self.frame.state, sid)
            save_state(self.frame.state)

        try:
            json_path = SPECIES_DIR / f"{sid}.json"
            if json_path.exists():
                json_path.unlink()
            if not text_dir_shared and species_text_dir.exists():
                for child in species_text_dir.iterdir():
                    if child.is_file():
                        child.unlink()
                species_text_dir.rmdir()
        except OSError as e:
            wx.MessageBox(
                f"Couldn't fully delete: {e}",
                "Delete species", wx.OK | wx.ICON_ERROR, self,
            )
        load_types()
        load_text_assets()
        self._refresh_list()
        self._update_detail()
        if in_use > 0:
            self.frame.save_and_refresh()
            self.frame.announce_event(
                "species_deleted_with_purge",
                name=spec.get("name", sid),
                n=in_use,
            )
        else:
            self.frame.announce_event("species_deleted")

    def _announce_brought_home(self, sid):
        spec = SPECIES_DATA.get(sid, {}).get("spec", {})
        plural = spec.get("name_plural") or spec.get("name", sid)
        # Reuse the existing "species added" announcement vocabulary
        # so the message style matches what the rest of the game uses.
        self.frame.announce_event(
            "species_added_with_seed", plural=plural,
        )


class ManageAnnouncementsDialog(wx.Dialog):
    """Edit any of the game's announcement templates one event at a time.

    A modder-friendly alternative to opening assets/text/announcements.txt
    in a text editor. The dialog edits a working copy in memory; on
    Save it merges into ANNOUNCEMENTS (live, takes effect immediately
    for future announcements) and rewrites the file with header + docs.
    Cancel discards the working copy.

    Cogacc-friendly layout: pick one event from a dropdown, see the
    placeholders it accepts, see the shipped default for reference,
    edit the template in a single text box. Per-event reset and
    "reset everything" are explicit buttons; nothing is destructive
    without confirmation.
    """

    def __init__(self, parent_frame):
        super().__init__(
            parent_frame, title="Manage announcements", size=(720, 640),
        )
        self.frame = parent_frame
        # Working copy: starts as a snapshot of the live ANNOUNCEMENTS
        # dict. Edits land here until the user clicks Save.
        self._working = dict(ANNOUNCEMENTS)
        # Current event being edited; None until the user picks one.
        self._current_event_id = None
        self.SetEscapeId(wx.ID_CANCEL)
        self._build()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=(
                "Pick an event below to see and edit the line the game "
                "speaks for it. Use {placeholder} for runtime values — "
                "the placeholders each event understands are listed once "
                "you pick it. Save commits your changes (and rewrites "
                "assets/text/announcements.txt). Cancel discards them."
            ),
            size=(-1, 72),
        )
        intro.SetName("Manage announcements help")
        sizer.Add(intro, 0, wx.ALL | wx.EXPAND, 10)

        picker_box = wx.StaticBox(self, label="Event")
        picker_sizer = wx.StaticBoxSizer(picker_box, wx.VERTICAL)
        # Sorted alphabetically — a flat list with ~70 items is more
        # navigable for NVDA than nested categories. Keys are the
        # canonical ordering from DEFAULT_ANNOUNCEMENTS plus any extras
        # the modder added.
        self._event_ids = sorted(
            set(DEFAULT_ANNOUNCEMENTS.keys()) | set(self._working.keys())
        )
        self.event_choice = wx.Choice(self, choices=self._event_ids)
        self.event_choice.SetName("Event to edit")
        self.event_choice.Bind(wx.EVT_CHOICE, self._on_event_change)
        picker_sizer.Add(self.event_choice, 0, wx.ALL | wx.EXPAND, 4)
        sizer.Add(picker_sizer, 0, wx.ALL | wx.EXPAND, 8)

        details_box = wx.StaticBox(self, label="Selected event")
        details_sizer = wx.StaticBoxSizer(details_box, wx.VERTICAL)

        self.placeholders_label = wx.StaticText(
            self, label="Placeholders: (pick an event above)",
        )
        self.placeholders_label.SetForegroundColour(wx.Colour(90, 90, 90))
        details_sizer.Add(self.placeholders_label, 0, wx.ALL, 4)

        details_sizer.Add(
            wx.StaticText(self, label="Shipped default:"),
            0, wx.LEFT | wx.TOP, 4,
        )
        self.default_text = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 60),
        )
        self.default_text.SetName("Shipped default for this event")
        details_sizer.Add(self.default_text, 0, wx.ALL | wx.EXPAND, 4)

        details_sizer.Add(
            wx.StaticText(self, label="Your template:"),
            0, wx.LEFT | wx.TOP, 4,
        )
        self.template_text = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE,
            value="",
            size=(-1, 80),
        )
        self.template_text.SetName(
            "Your template for this event — use {placeholder} for runtime values"
        )
        # Auto-commit edits to the working copy on every keystroke so
        # changing the dropdown never silently loses an unsaved edit.
        # NVDA-quiet: TextCtrl events don't trigger announcements.
        self.template_text.Bind(wx.EVT_TEXT, self._on_template_text)
        details_sizer.Add(self.template_text, 1, wx.ALL | wx.EXPAND, 4)

        per_event_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.reset_event_btn = wx.Button(self, label="Reset this event to shipped default")
        self.reset_event_btn.Bind(wx.EVT_BUTTON, self._on_reset_event)
        self.reset_event_btn.Disable()
        per_event_btns.Add(self.reset_event_btn, 0)
        details_sizer.Add(per_event_btns, 0, wx.ALL, 4)

        sizer.Add(details_sizer, 1, wx.ALL | wx.EXPAND, 8)

        bottom = wx.BoxSizer(wx.HORIZONTAL)
        reset_all_btn = wx.Button(
            self, label="Reset ALL events to shipped defaults…",
        )
        reset_all_btn.Bind(wx.EVT_BUTTON, self._on_reset_all)
        save_btn = wx.Button(self, label="Save")
        save_btn.SetDefault()
        save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "Cancel")
        bottom.Add(reset_all_btn, 0, wx.RIGHT, 8)
        bottom.AddStretchSpacer()
        bottom.Add(save_btn, 0, wx.RIGHT, 8)
        bottom.Add(cancel_btn, 0)
        sizer.Add(bottom, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

        if self._event_ids:
            self.event_choice.SetSelection(0)
            self._show_event(self._event_ids[0])

    def _on_event_change(self, evt):
        idx = self.event_choice.GetSelection()
        if idx < 0:
            return
        self._show_event(self._event_ids[idx])

    def _show_event(self, event_id):
        """Switch the detail panel to the named event. Pulls placeholder
        docs, default text, and the working-copy template into the
        widgets. Idempotent — calling repeatedly is fine.
        """
        self._current_event_id = event_id
        placeholders = _ANNOUNCEMENT_DOCS.get(event_id, "(unknown event)")
        self.placeholders_label.SetLabel(f"Placeholders: {placeholders}")
        self.default_text.ChangeValue(
            DEFAULT_ANNOUNCEMENTS.get(event_id, "(no shipped default)")
        )
        # ChangeValue avoids firing wxEVT_TEXT, so we don't trigger our
        # own _on_template_text handler when programmatically loading
        # the working-copy value.
        self.template_text.ChangeValue(self._working.get(event_id, ""))
        self.reset_event_btn.Enable(event_id in DEFAULT_ANNOUNCEMENTS)

    def _on_template_text(self, evt):
        if self._current_event_id is None:
            return
        self._working[self._current_event_id] = self.template_text.GetValue()

    def _on_reset_event(self, evt):
        if self._current_event_id is None:
            return
        default = DEFAULT_ANNOUNCEMENTS.get(self._current_event_id)
        if default is None:
            return
        self._working[self._current_event_id] = default
        self.template_text.ChangeValue(default)

    def _on_reset_all(self, evt):
        with wx.MessageDialog(
            self,
            "Reset every event to its shipped default? This wipes all "
            "your customisations in this dialog (you'll still need to "
            "click Save to write them to disk).",
            "Reset all events?",
            wx.YES_NO | wx.ICON_QUESTION,
        ) as confirm:
            if confirm.ShowModal() != wx.ID_YES:
                return
        self._working = dict(DEFAULT_ANNOUNCEMENTS)
        if self._current_event_id is not None:
            self._show_event(self._current_event_id)

    def _on_save(self, evt):
        # Merge working copy into the live ANNOUNCEMENTS dict so the
        # next announce_event() call sees the new templates without a
        # restart, then rewrite the file so the changes persist.
        # Update the shared ANNOUNCEMENTS dict in place (clear + update)
        # so the engine half, which reads this same dict object, sees
        # the edits without a rebind that would desync the two modules.
        ANNOUNCEMENTS.clear()
        ANNOUNCEMENTS.update(self._working)
        path = TEXT_DIR / "announcements.txt"
        try:
            TEXT_DIR.mkdir(parents=True, exist_ok=True)
            _write_announcements_file(path, ANNOUNCEMENTS)
        except OSError as e:
            wx.MessageBox(
                f"Couldn't write {path}: {e}",
                "Save announcements", wx.OK | wx.ICON_ERROR, self,
            )
            return
        self.frame.announce_event("announcements_saved")
        self.EndModal(wx.ID_OK)


class ManageRoomTypesDialog(wx.Dialog):
    """List of room types with Add / Edit / Delete. Reloads ROOM_TYPES
    after each successful operation.
    """

    def __init__(self, parent_frame):
        super().__init__(parent_frame, title="Manage room types", size=(560, 480))
        self.frame = parent_frame
        # wxDialog only auto-binds Escape to wx.ID_CANCEL by default, but
        # this dialog uses a wx.ID_CLOSE button. Tell wx that Escape should
        # fire that ID so the dialog can be closed without a mouse.
        self.SetEscapeId(wx.ID_CLOSE)
        self._build()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        info = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=("Add new room types, edit existing ones, or remove unused "
                   "types. Changes write to user_data/types/room_types/ as "
                   "JSON. The shipped factory copies in assets/ are never "
                   "modified — copy one back into user_data/ to revert. "
                   "Restart Time for Family if existing tabs don't refresh."),
            size=(-1, 60),
        )
        info.SetName("Manage room types help")
        sizer.Add(info, 0, wx.ALL | wx.EXPAND, 10)

        self.list_box = wx.ListBox(self, choices=[])
        self.list_box.SetName("Room types list")
        sizer.Add(self.list_box, 1, wx.ALL | wx.EXPAND, 10)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        add_btn = wx.Button(self, label="Add new…")
        add_btn.Bind(wx.EVT_BUTTON, self.on_add)
        edit_btn = wx.Button(self, label="Edit selected…")
        edit_btn.Bind(wx.EVT_BUTTON, self.on_edit)
        del_btn = wx.Button(self, label="Delete selected")
        del_btn.Bind(wx.EVT_BUTTON, self.on_delete)
        close_btn = wx.Button(self, wx.ID_CLOSE, "Close")
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        close_btn.SetDefault()
        btns.Add(add_btn, 0, wx.RIGHT, 8)
        btns.Add(edit_btn, 0, wx.RIGHT, 8)
        btns.Add(del_btn, 0, wx.RIGHT, 8)
        btns.AddStretchSpacer()
        btns.Add(close_btn, 0)
        sizer.Add(btns, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)
        self._refresh_list()

    def _refresh_list(self):
        self.list_box.Clear()
        for tid, spec in ROOM_TYPES.items():
            display_name = spec.get("name", tid)
            self.list_box.Append(f"{display_name}  ({tid})", tid)
        if self.list_box.GetCount() > 0:
            self.list_box.SetSelection(0)

    def _selected_id(self):
        sel = self.list_box.GetSelection()
        if sel < 0:
            return None
        return self.list_box.GetClientData(sel)

    def on_add(self, evt):
        with RoomTypeEditorDialog(self) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                load_types()
                self._refresh_list()
                self.frame.announce_event("room_type_added")

    def on_edit(self, evt):
        tid = self._selected_id()
        if not tid:
            return
        with RoomTypeEditorDialog(self, type_id=tid) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                load_types()
                self._refresh_list()
                self.frame.announce_event("room_type_saved")

    def on_delete(self, evt):
        tid = self._selected_id()
        if not tid:
            return
        spec = ROOM_TYPES.get(tid) or {}

        # Refuse to delete a room type that is the ONLY home some species
        # has — otherwise that species' compatible_room_types would point at
        # a deleted type and its creatures could never move out of the
        # village. Mirrors the species editor (won't save an empty compat
        # list) and the room-type editor (won't untick the last species).
        # `also_listed` collects species that list this type alongside other
        # homes — safe to delete, but we strip the dead id from them below so
        # no species JSON is left referencing a room type that's gone. The
        # strand assessment is a pure engine helper (tested headlessly, and
        # reused by any non-UI driver).
        would_strand, also_listed = room_type_delete_impact(tid)
        if would_strand:
            wx.MessageBox(
                "Can't delete '" + spec.get("name", tid) + "'. It's the only "
                "room type these species can live in: "
                + ", ".join(sorted(would_strand)) + ". Deleting it would "
                "leave their creatures stuck in the village forever. First "
                "edit each of those species (File → Species → Edit) "
                "to add another compatible room type, then delete this one.",
                "Delete room type", wx.OK | wx.ICON_WARNING, self,
            )
            return

        in_use = _room_type_in_use_count(self.frame.state, tid)
        residents = sum(
            len(r.get("creatures", []))
            for r in self.frame.state.get("rooms", [])
            if r.get("type") == tid
        )

        place_name = self.frame.state.get("village_name", "Village")
        msg_lines = [f"Delete room type '{spec.get('name', tid)}'?", ""]
        if in_use > 0:
            msg_lines.append(
                f"{in_use} room(s) of this type will be removed from your save."
            )
            if residents > 0:
                msg_lines.append(
                    f"Their {residents} resident(s) will be moved to {place_name}."
                )
            msg_lines.append("")
        msg_lines.append(
            f"This will remove user_data/types/room_types/{tid}.json."
        )
        msg_lines.append(
            "(The shipped factory copy in assets/ is not affected.)"
        )

        confirm = wx.MessageBox(
            "\n".join(msg_lines), "Delete room type",
            wx.YES_NO | wx.ICON_QUESTION, self,
        )
        if confirm != wx.YES:
            return

        if in_use > 0:
            _purge_room_type_from_state(self.frame.state, tid)
            save_state(self.frame.state)

        try:
            path = ROOM_TYPES_DIR / f"{tid}.json"
            if path.exists():
                path.unlink()
        except OSError as e:
            wx.MessageBox(
                f"Couldn't delete: {e}",
                "Delete room type", wx.OK | wx.ICON_ERROR, self,
            )
        # Strip the now-deleted type from any species that listed it
        # alongside other homes (would_strand above guarantees none of these
        # ends up empty), so no species JSON is left pointing at a room type
        # that no longer exists. load_types() below re-reads the cleaned files.
        for sid in also_listed:
            data = SPECIES_DATA.get(sid) or {}
            species_spec_dict = dict(data.get("spec") or {})
            species_spec_dict["compatible_room_types"] = [
                c for c in (species_spec_dict.get("compatible_room_types") or [])
                if c != tid
            ]
            species_path = SPECIES_DIR / f"{sid}.json"
            try:
                with open(species_path, "w", encoding="utf-8") as f:
                    json.dump(species_spec_dict, f, indent=2)
            except OSError as e:
                wx.MessageBox(
                    f"Couldn't update {species_path}: {e}",
                    "Delete room type", wx.OK | wx.ICON_ERROR, self,
                )
        load_types()
        self._refresh_list()
        if in_use > 0:
            # Room tabs and other room-derived UI need a full rebuild because
            # rooms have been removed from state. Tell the user.
            wx.MessageBox(
                f"Room type deleted. {in_use} room tab(s) were removed from "
                f"your save. Restart Time for Family to refresh the tab bar.",
                "Room type deleted", wx.OK | wx.ICON_INFORMATION, self,
            )
            self.frame.save_and_refresh()
            self.frame.announce_event(
                "room_type_deleted_with_purge", n=in_use,
            )
        else:
            self.frame.announce_event("room_type_deleted")
