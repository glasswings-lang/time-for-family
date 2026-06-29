"""
Time for Family -- the pop-up dialogs and their sub-widgets.

Every wx.Dialog the game opens (build a room, edit a room, the species and
room-type editors, settings, help, the announcements editor, etc.) plus the
small shared widgets and helpers they use. Depends only on the engine and
sound -- no panel or MainFrame reference -- so the import graph stays acyclic.
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
# Pull engine + sound names (including _underscore helpers a star-import
# skips) into this module so the unqualified calls keep resolving. Shelves
# come across as references to the SAME objects -- never rebind them.
globals().update({_k: _v for _k, _v in vars(tff_engine).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_sound).items()
                  if not _k.startswith("__")})

class AddVillagerDialog(wx.Dialog):
    """Spawn one or more villagers of any loaded species.

    Pick species, how many, and how the sexes are assigned (random,
    all female, all male, or alternating). Used to introduce newly-added
    species without needing to reset the park.
    """

    def __init__(self, parent):
        super().__init__(parent, title="Add a villager", size=(440, 360))
        self.species_id = None
        self.count = 0
        self.sexes = []
        self._build()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        # Parent is the VillagePanel; pull the player-renameable name.
        place_name = (
            getattr(self.GetParent(), "frame", None).state.get(
                "village_name", "Village",
            )
            if hasattr(self.GetParent(), "frame") else "Village"
        )
        intro = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=(f"Spawn fresh villagers of any species. They'll arrive "
                   f"in {place_name} and can be brought home from there."),
            size=(-1, 56),
        )
        intro.SetName("Add villager intro")
        sizer.Add(intro, 0, wx.ALL | wx.EXPAND, 10)

        species_row = wx.BoxSizer(wx.HORIZONTAL)
        species_row.Add(
            wx.StaticText(self, label="Species:"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.species_choice = wx.Choice(self, choices=[])
        self.species_choice.SetName("Species")
        self._species_ids = list(SPECIES_DATA.keys())
        for sid in self._species_ids:
            spec = SPECIES_DATA[sid].get("spec", {})
            self.species_choice.Append(spec.get("name", sid), sid)
        if self._species_ids:
            self.species_choice.SetSelection(0)
        species_row.Add(self.species_choice, 1)
        sizer.Add(species_row, 0, wx.ALL | wx.EXPAND, 10)

        count_row = wx.BoxSizer(wx.HORIZONTAL)
        count_row.Add(
            wx.StaticText(self, label="How many:"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.count_ctrl = wx.SpinCtrl(self, min=1, max=20, initial=2)
        self.count_ctrl.SetName("How many")
        count_row.Add(self.count_ctrl, 0)
        sizer.Add(count_row, 0, wx.ALL | wx.EXPAND, 10)

        self.sex_radio = wx.RadioBox(
            self,
            label="Sex assignment",
            choices=["Random", "All female", "All male", "Alternating (F, M, F, M…)"],
            style=wx.RA_SPECIFY_ROWS,
        )
        self.sex_radio.SetName("Sex assignment")
        sizer.Add(self.sex_radio, 0, wx.ALL | wx.EXPAND, 10)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(self, wx.ID_OK, "Add")
        ok_btn.Bind(wx.EVT_BUTTON, self.on_ok)
        ok_btn.SetDefault()
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "Cancel")
        btns.AddStretchSpacer()
        btns.Add(ok_btn, 0, wx.RIGHT, 8)
        btns.Add(cancel_btn, 0)
        sizer.Add(btns, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

    def on_ok(self, evt):
        sel = self.species_choice.GetSelection()
        if sel < 0:
            wx.MessageBox("Pick a species first.",
                          "Add a villager", wx.OK | wx.ICON_WARNING, self)
            return
        self.species_id = self.species_choice.GetClientData(sel)
        self.count = int(self.count_ctrl.GetValue())
        mode = self.sex_radio.GetSelection()
        if mode == 1:
            self.sexes = ["F"] * self.count
        elif mode == 2:
            self.sexes = ["M"] * self.count
        elif mode == 3:
            self.sexes = ["F" if i % 2 == 0 else "M" for i in range(self.count)]
        else:
            self.sexes = [random.choice(["F", "M"]) for _ in range(self.count)]
        self.EndModal(wx.ID_OK)


class EditRoomDialog(wx.Dialog):
    """Edit an existing room's name and/or type. Creature relocation on
    type change is handled by the caller (RoomPanel.on_edit_room).
    """

    def __init__(self, parent, room):
        super().__init__(parent, title="Edit room", size=(560, 480))
        self.room = room
        self.type_ids = list(ROOM_TYPES.keys())
        # The dialog's parent is the RoomPanel, which carries .frame.state.
        # Pluck the renameable village name once for use in intro text.
        self._place_name = (
            getattr(parent, "frame", None).state.get("village_name", "Village")
            if hasattr(parent, "frame") else "Village"
        )
        self._build()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=(
                "Rename this room or change its type. Changing the type "
                "rebuilds the meters; any creatures whose species can't "
                f"live in the new type will be moved to {self._place_name}."
            ),
            size=(-1, 60),
        )
        intro.SetName("Edit room info")
        sizer.Add(intro, 0, wx.ALL | wx.EXPAND, 10)

        name_row = wx.BoxSizer(wx.HORIZONTAL)
        name_row.Add(
            wx.StaticText(self, label="Room name:"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.name_field = wx.TextCtrl(self, value=self.room["name"])
        self.name_field.SetName("Room name")
        name_row.Add(self.name_field, 1)
        sizer.Add(name_row, 0, wx.ALL | wx.EXPAND, 10)

        if self.type_ids:
            choices = []
            for tid in self.type_ids:
                spec = ROOM_TYPES[tid]
                species = room_type_compatible_species(tid)
                species_part = (
                    "for " + ", ".join(species) if species else "no creatures yet"
                )
                meters = ", ".join(m["label"] for m in spec.get("meters", []))
                choices.append(f"{spec['name']} ({species_part}; meters: {meters})")
            self.type_radio = wx.RadioBox(
                self,
                label="Room type",
                choices=choices,
                style=wx.RA_SPECIFY_ROWS,
            )
            self.type_radio.SetName("Room type")
            current = self.room.get("type", "indoor")
            if current in self.type_ids:
                self.type_radio.SetSelection(self.type_ids.index(current))
            self.type_radio.Bind(wx.EVT_RADIOBOX, self._on_type_change)
            sizer.Add(self.type_radio, 0, wx.ALL | wx.EXPAND, 10)
        else:
            self.type_radio = None

        # Per-instance species restriction. Pre-populated with the room's
        # current allowed_species. If the user picks a different room type,
        # we re-seed with that type's full compat list (since the previous
        # narrowing was relative to the OLD type).
        allowed_static = wx.StaticBox(self, label="Allowed species in this room")
        allowed_box_sizer = wx.StaticBoxSizer(allowed_static, wx.VERTICAL)
        allowed_intro = wx.StaticText(
            self,
            label=("Which species can live in this room. Uncheck any you "
                   "want to keep out — creatures of unchecked species "
                   f"will be moved to {self._place_name} when you save. "
                   "Use arrow keys to move, Space to toggle."),
        )
        allowed_intro.Wrap(520)
        allowed_intro.SetForegroundColour(wx.Colour(90, 90, 90))
        allowed_box_sizer.Add(allowed_intro, 0, wx.ALL, 4)
        self.allowed_box = make_state_announcing_checklist(
            self, "Allowed species", items=[], checked_ids=[],
            size=(-1, 110),
        )
        allowed_box_sizer.Add(self.allowed_box, 1, wx.ALL | wx.EXPAND, 4)
        sizer.Add(allowed_box_sizer, 0, wx.ALL | wx.EXPAND, 10)
        self._refresh_allowed_box()

        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(self, wx.ID_OK, "Save")
        ok_btn.SetDefault()
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "Cancel")
        btns.AddStretchSpacer()
        btns.Add(ok_btn, 0, wx.RIGHT, 8)
        btns.Add(cancel_btn, 0)
        sizer.Add(btns, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)
        self.name_field.SetFocus()
        self.name_field.SelectAll()

    def get_name(self):
        return self.name_field.GetValue().strip()

    def selected_type_id(self):
        if self.type_radio is None or not self.type_ids:
            return None
        return self.type_ids[self.type_radio.GetSelection()]

    def selected_allowed_species(self):
        return checklist_get_checked_ids(self.allowed_box)

    def _on_type_change(self, evt):
        self._refresh_allowed_box()

    def _refresh_allowed_box(self):
        """Repopulate the allowed-species checklist for the currently
        selected room type. If the type matches the room's current type,
        seed with the room's current allowed_species (preserving the
        user's narrowing). If the type changed, seed with the new
        type's full compat list (since the old narrowing doesn't apply
        to a different type).
        """
        tid = self.selected_type_id() or self.room.get("type")
        type_compat = room_type_compatible_species(tid)
        items = [
            (sid, f"{SPECIES_DATA.get(sid, {}).get('spec', {}).get('name', sid)} ({sid})")
            for sid in type_compat
        ]
        if tid == self.room.get("type"):
            current = self.room.get("allowed_species") or []
            checked = [s for s in current if s in type_compat] or list(type_compat)
        else:
            checked = list(type_compat)
        repopulate_state_announcing_checklist(self.allowed_box, items, checked)


class ExpandSlotDialog(wx.Dialog):
    """Add a slot to an existing room — three payment options:
    common items (recipe-style), spend a treasure, or spend an object.

    Each Spend button performs the action and closes the dialog. Cancel
    closes without changes.
    """

    def __init__(self, parent_panel, room):
        super().__init__(parent_panel, title=f"Add a slot to {room['name']}", size=(600, 540))
        self.parent_panel = parent_panel
        self.room = room
        self._build()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        state = self.parent_panel.frame.state
        used = len(self.room["creatures"])
        cap = self.room["slot_count"]
        cost = int(SETTINGS.get("slot_expansion_common_cost", 5))
        commons = sum(state.get("inventory", {}).get("common", {}).values())
        # Stacked dicts: {name: {count, description}}. Sort by name so
        # the picker reads predictably to NVDA. Each unique kind is one
        # row; the count tells the player how many they have left.
        treasures_dict = state.get("inventory", {}).get("treasures", {})
        objects_dict = state.get("inventory", {}).get("objects", {})
        treasures_total = total_collectible_count(treasures_dict)
        objects_total = total_collectible_count(objects_dict)
        treasure_choices = [
            (name, f"{name} × {entry.get('count', 0)}")
            for name, entry in sorted(treasures_dict.items())
            if int(entry.get("count", 0)) > 0
        ]
        object_choices = [
            (name, f"{name} × {entry.get('count', 0)}")
            for name, entry in sorted(objects_dict.items())
            if int(entry.get("count", 0)) > 0
        ]

        intro = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=(
                f"{self.room['name']}: {used} of {cap} slots used. "
                "Pick how to add one more slot."
            ),
            size=(-1, 48),
        )
        intro.SetName("Slot expansion info")
        sizer.Add(intro, 0, wx.ALL | wx.EXPAND, 10)

        # Option 1: common items
        common_box = wx.StaticBox(self, label="Common items")
        common_sizer = wx.StaticBoxSizer(common_box, wx.VERTICAL)
        common_status = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=f"Spend {cost} common items. You have {commons}.",
            size=(-1, 28),
        )
        common_status.SetName("Common items option")
        common_sizer.Add(common_status, 0, wx.ALL | wx.EXPAND, 4)
        self.common_btn = wx.Button(self, label=f"Spend {cost} common items")
        self.common_btn.Enable(commons >= cost)
        self.common_btn.Bind(wx.EVT_BUTTON, self.on_spend_commons)
        common_sizer.Add(self.common_btn, 0, wx.ALL, 4)
        sizer.Add(common_sizer, 0, wx.ALL | wx.EXPAND, 8)

        # Option 2: treasure
        treasure_box = wx.StaticBox(self, label="Treasure")
        treasure_sizer = wx.StaticBoxSizer(treasure_box, wx.VERTICAL)
        treasure_status = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=(
                f"Spend one treasure. You have {treasures_total}."
                if treasures_total else
                "Spend one treasure. You haven't found any yet."
            ),
            size=(-1, 28),
        )
        treasure_status.SetName("Treasure option")
        treasure_sizer.Add(treasure_status, 0, wx.ALL | wx.EXPAND, 4)
        self.treasure_choice = wx.Choice(
            self, choices=[label for _, label in treasure_choices],
        )
        self.treasure_choice.SetName("Treasure to spend")
        # Stash the parallel name list so on_spend_treasure can look up
        # the actual key from the chosen index.
        self._treasure_names = [name for name, _ in treasure_choices]
        if treasure_choices:
            self.treasure_choice.SetSelection(0)
        else:
            self.treasure_choice.Disable()
        treasure_sizer.Add(self.treasure_choice, 0, wx.ALL | wx.EXPAND, 4)
        self.treasure_btn = wx.Button(self, label="Spend selected treasure")
        self.treasure_btn.Enable(bool(treasure_choices))
        self.treasure_btn.Bind(wx.EVT_BUTTON, self.on_spend_treasure)
        treasure_sizer.Add(self.treasure_btn, 0, wx.ALL, 4)
        sizer.Add(treasure_sizer, 0, wx.ALL | wx.EXPAND, 8)

        # Option 3: object
        object_box = wx.StaticBox(self, label="Object")
        object_sizer = wx.StaticBoxSizer(object_box, wx.VERTICAL)
        object_status = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=(
                f"Spend one object. You have {objects_total}."
                if objects_total else
                "Spend one object. You haven't found any yet."
            ),
            size=(-1, 28),
        )
        object_status.SetName("Object option")
        object_sizer.Add(object_status, 0, wx.ALL | wx.EXPAND, 4)
        self.object_choice = wx.Choice(
            self, choices=[label for _, label in object_choices],
        )
        self.object_choice.SetName("Object to spend")
        self._object_names = [name for name, _ in object_choices]
        if object_choices:
            self.object_choice.SetSelection(0)
        else:
            self.object_choice.Disable()
        object_sizer.Add(self.object_choice, 0, wx.ALL | wx.EXPAND, 4)
        self.object_btn = wx.Button(self, label="Spend selected object")
        self.object_btn.Enable(bool(object_choices))
        self.object_btn.Bind(wx.EVT_BUTTON, self.on_spend_object)
        object_sizer.Add(self.object_btn, 0, wx.ALL, 4)
        sizer.Add(object_sizer, 0, wx.ALL | wx.EXPAND, 8)

        cancel = wx.Button(self, wx.ID_CANCEL, "Cancel")
        sizer.Add(cancel, 0, wx.ALL | wx.ALIGN_RIGHT, 10)

        self.SetSizer(sizer)

    def _finish_event(self, event_id, **kwargs):
        play_sound("breed_success")
        self.parent_panel.frame.announce_event(event_id, **kwargs)
        save_state(self.parent_panel.frame.state)
        self.parent_panel.frame.rebuild_room_tab(self.room["id"])
        self.EndModal(wx.ID_OK)

    def on_spend_commons(self, evt):
        cost = int(SETTINGS.get("slot_expansion_common_cost", 5))
        state = self.parent_panel.frame.state
        taken = deduct_recipe(state, {"_any_common": cost})
        if taken is None:
            wx.MessageBox(
                "Not enough common items.",
                "Slot expansion failed", wx.OK | wx.ICON_WARNING, self,
            )
            return
        new_total = expand_room_slots(state, self.room["id"], 1)
        used_str = ", ".join(f"{n} {pluralize(name, n)}" for name, n in taken.items())
        self._finish_event(
            "slot_added_commons",
            room_name=self.room["name"], total=new_total, used=used_str,
        )

    def on_spend_treasure(self, evt):
        sel = self.treasure_choice.GetSelection()
        if sel < 0 or sel >= len(self._treasure_names):
            return
        name = self._treasure_names[sel]
        consumed = consume_treasure(self.parent_panel.frame.state, name)
        if consumed is None:
            return
        new_total = expand_room_slots(self.parent_panel.frame.state, self.room["id"], 1)
        self._finish_event(
            "slot_added_treasure",
            room_name=self.room["name"], total=new_total,
            treasure_name=consumed.get("name", "a treasure"),
        )

    def on_spend_object(self, evt):
        sel = self.object_choice.GetSelection()
        if sel < 0 or sel >= len(self._object_names):
            return
        name = self._object_names[sel]
        consumed = consume_object(self.parent_panel.frame.state, name)
        if consumed is None:
            return
        new_total = expand_room_slots(self.parent_panel.frame.state, self.room["id"], 1)
        self._finish_event(
            "slot_added_object",
            room_name=self.room["name"], total=new_total,
            object_name=consumed.get("name", "an object"),
        )


class HelpDialog(wx.Dialog):
    """How-to-play dialog. Shown the very first time the app runs (per save)
    and on demand from Help → How to play. Pure read-only; uses focusable
    multi-line text so NVDA reads the whole thing on tab.
    """

    # {village_name} is the player's renameable name for the village
    # (default "Village"). Substituted at dialog construction so the
    # help text always matches what the user has actually called it.
    HELP_TEMPLATE = (
        "Welcome to Time for Family — a cozy life sim about caring for "
        "creatures, growing your family, and building a small world.\n"
        "\n"
        "Getting around:\n"
        "Near the top of the window there's a 'Go to' combo box. It "
        "lists every room you have, plus {village_name}, Park, and "
        "Stats. Pick one to switch the view. With the dropdown open you "
        "can also type the first few letters of a name to jump straight "
        "to it — typing 'ind' lands on Indoor Room 1, for example. "
        "From anywhere in the app, Ctrl+G (Tools → Go to room or "
        "section) puts focus back on the picker.\n"
        "\n"
        "About time:\n"
        "Everything in the game uses real (wall-clock) time. The Age "
        "column on each creature shows how long they've been alive in "
        "real terms (e.g. '3 hours' or '2 days, 4 hours'). The species "
        "editor's life-stage fields are the same — time before a baby "
        "is mature, age at which a creature becomes an elder (which "
        "is also when they retire from breeding) — all real time. "
        "If you want the whole lifecycle to move faster or slower at "
        "once without editing each species, change Settings → "
        "'Lifecycle pace': 1.0 is normal, 0.5 is twice as fast across "
        "the board, 2.0 is twice as slow.\n"
        "\n"
        "Getting started:\n"
        "Time for Family begins with an empty park — no rooms, no "
        "{village_name}, no creatures yet. The first time you launch "
        "(or after File → Reset park), the Species dialog opens and "
        "asks who lives here. Pick a species from the list and click "
        "'Bring them home', or design a brand-new one with 'Create a "
        "new species…'. A small pair of babies arrives in "
        "{village_name}. You can come back to that picker any time "
        "from File → Species — there's no rush, and no obligation to "
        "add every species. Add another whenever you'd like, or use "
        "the same dialog to edit or delete species you've installed. "
        "After your starter pair is in {village_name}, head to the "
        "Park to build a room they can live in (each room type has a "
        "recipe — collect items by digging in the Park first), then "
        "visit {village_name} to adopt them in. (Tip: you can rename "
        "{village_name} from its section via the 'Rename this place…' "
        "button — Sanctuary, Home, Refuge, whatever fits.)\n"
        "\n"
        "The basic loop:\n"
        "1. Each room has care meters — food, water, and one third meter "
        "(litter for cats indoor, shelter for outdoor, water quality "
        "for aquatic, nest material for the aviary). Click the matching "
        "Refill / Clean / Tidy / Refresh button to top them up — or "
        "click 'Refill all care' to top up every meter at once. The "
        "meters slowly drop in real time, so check on them regularly.\n"
        "2. Spend time with the creatures. Select one and click 'Pet "
        "selected', or click 'Pet everyone here' to give every creature "
        "in the room a moment of attention in one go.\n"
        "3. Cats and other creatures pair up over time when an unpaired "
        "male and female of the same species share a room. Pair "
        "formation continues even while the game is closed, so you "
        "don't need to leave it open. Once paired, click 'Try to breed' "
        "to attempt a litter — or turn on Tools → Turn on "
        "auto-breeding (Ctrl+B) to have eligible pairs breed on their "
        "own at a slow background rhythm. Success rate, room "
        "cleanliness threshold, and pair cooldown are all in Settings.\n"
        "4. Successful breeding gives the mother an 'expecting' "
        "period (only when the species has a gestation set — many "
        "species are configured to give birth instantly). When the "
        "babies arrive, they're placed directly into mother's room "
        "(or another compatible room, or {village_name}, if mother's "
        "room is full). They stay tethered to mother for her "
        "species' mother-dependency period — during that time, "
        "moving the mother brings all her babies along, and you "
        "can't move a baby away from her. After that, the babies "
        "are independent; they keep growing for their species' "
        "breeding age before they can pair or breed themselves. "
        "Their description box shows when. If you want fewer "
        "creatures in your direct care, use the normal Move action "
        "to send any to {village_name} once they're independent.\n"
        "5. {village_name} holds creatures waiting to be adopted, plus "
        "any who have left your rooms. Use the 'Go to' picker to visit "
        "it, filter by species, and bring anyone home if there's a "
        "free slot in a compatible room.\n"
        "6. The Park lets you dig for items — sticks, stones, ribbons, "
        "feathers, plus rare objects and treasures. Items are used to "
        "build new rooms (each room type has its own recipe) and to "
        "add slots to existing rooms.\n"
        "\n"
        "Useful menus:\n"
        " • File → Species: the one-stop species dialog. Pick a "
        "starter pair to bring home, design a brand-new species, or "
        "edit / delete an existing one. Browser-style — stays open "
        "after each action so you can keep curating without "
        "re-opening it. There's no 'add all' button by design; pick "
        "whoever appeals to you, and come back later for more if you "
        "want. The basic editor view stays focused on the fields most "
        "species need; rarely-touched knobs (sex shorts, starter age "
        "range, twin and disability chances, litter overrides) hide "
        "inside an 'Advanced settings' section that you expand only "
        "when you need it.\n"
        " • File → Settings: tune almost everything (decay rate, "
        "breeding chance, dig outcomes, lifecycle pace, the wild "
        "emigration chance, etc.).\n"
        " • File → Reset park: wipes your save and starts fresh with "
        "an empty park (your current save is backed up first to "
        "state.json.backup). Different from closing and reopening the "
        "game, which keeps your save intact. The Species dialog fires "
        "automatically right after a reset.\n"
        " • Tools → Go to room or section (Ctrl+G): jump focus back "
        "to the 'Go to' picker from anywhere.\n"
        " • Tools → Pause time: freeze decay, aging, and pair "
        "formation while you step away.\n"
        " • Tools → Turn on auto-breeding (Ctrl+B): the slow "
        "background option — eligible pairs in rooms breed on their "
        "own once per auto-breeding interval, and village creatures "
        "produce occasional offscreen births. With this off, breeding "
        "only happens when you click 'Try to breed' on a room.\n"
        " • Tools → Mute sounds: silence the chimes without losing "
        "NVDA announcements.\n"
        " • Help → How to play: opens this dialog any time.\n"
        "\n"
        "Where your edits live:\n"
        "Time for Family ships factory copies of every species, room "
        "type, name pool, and announcement template in the assets/ "
        "folder next to this game. The first time you launch, those "
        "are copied into a sibling user_data/ folder and the game "
        "reads and writes only there from then on. assets/ stays "
        "untouched as a 'factory reset' reference: if you ever want "
        "to revert a species to its shipped default, copy the file "
        "back from assets/types/species/ into "
        "user_data/types/species/. Same idea for room types and text "
        "pools. Anything you edit through the in-game tools (File → "
        "Species, Mods → Manage room types, Mods → Manage "
        "announcements) goes to user_data/, never to assets/. Your "
        "save still lives in state.json next to the game.\n"
        "\n"
        "Colours:\n"
        "Each creature is born with two colours — one inherited from "
        "each parent. The detail panel shows the colours on a "
        "'Colour:' line if the species has a colours pool defined. "
        "Babies usually inherit; once in a while (Settings → Color "
        "mutation chance, default 5% per colour) a baby rolls a "
        "fresh colour from the species' pool, like a recessive "
        "ancestor showing through. Edit a species' colour list "
        "through File → Species → Edit → the Colours pool, or "
        "directly in user_data/text/species/<species>/colors.txt. "
        "Set the mutation chance to 0 in Settings if you want strict "
        "inheritance.\n"
        "\n"
        "Modding: drop new species or room types into "
        "user_data/types/ as JSON files (see docs/MODDING.md for full "
        "instructions). Text pools (names, descriptions, pet "
        "responses, colours) are simple line-per-entry text files in "
        "user_data/text/. Edit, save, restart.\n"
        "\n"
        "Have fun."
    )

    def __init__(self, parent):
        super().__init__(parent, title="How to play", size=(700, 600))
        sizer = wx.BoxSizer(wx.VERTICAL)
        place_name = (
            getattr(parent, "state", {}).get("village_name", "Village")
            if hasattr(parent, "state") else "Village"
        )
        body = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.BORDER_SIMPLE,
            value=self.HELP_TEMPLATE.format(village_name=place_name),
        )
        body.SetName("How to play")
        sizer.Add(body, 1, wx.ALL | wx.EXPAND, 10)
        close = wx.Button(self, wx.ID_OK, "Close")
        close.SetDefault()
        sizer.Add(close, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.SetSizer(sizer)
        body.SetInsertionPoint(0)


class BuildRoomDialog(wx.Dialog):
    def __init__(self, parent_panel):
        super().__init__(parent_panel, title="Build a new room", size=(560, 480))
        self.parent_panel = parent_panel
        self.type_ids = list(ROOM_TYPES.keys())
        self._build()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=(
                "Pick a room type. Each type has its own recipe of items "
                "needed to build it."
            ),
            size=(-1, 48),
        )
        intro.SetName("Build room info")
        sizer.Add(intro, 0, wx.ALL | wx.EXPAND, 10)

        if self.type_ids:
            state = self.parent_panel.frame.state
            choices = []
            for tid in self.type_ids:
                spec = ROOM_TYPES[tid]
                species = room_type_compatible_species(tid)
                species_part = (
                    "for " + ", ".join(species)
                    if species
                    else "no creatures yet"
                )
                recipe = get_room_recipe(spec)
                missing = recipe_shortfall(state, recipe, type_spec=spec)
                status = format_shortfall(missing)
                choices.append(
                    f"{spec['name']} — needs: {format_recipe(recipe, type_spec=spec)} "
                    f"({status}; {species_part})"
                )
            self.type_radio = wx.RadioBox(
                self,
                label="Room type",
                choices=choices,
                style=wx.RA_SPECIFY_ROWS,
            )
            self.type_radio.SetName("Room type")
            self.type_radio.Bind(wx.EVT_RADIOBOX, self.on_type_change)
            sizer.Add(self.type_radio, 0, wx.ALL | wx.EXPAND, 10)
        else:
            self.type_radio = None

        self.type_detail = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 60),
        )
        self.type_detail.SetName("Selected room type details")
        sizer.Add(self.type_detail, 0, wx.ALL | wx.EXPAND, 10)

        species_row = wx.BoxSizer(wx.HORIZONTAL)
        species_row.Add(
            wx.StaticText(self, label="Starter species:"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.species_choice = wx.Choice(self, choices=[])
        self.species_choice.SetName("Starter species")
        species_row.Add(self.species_choice, 1)
        sizer.Add(species_row, 0, wx.ALL | wx.EXPAND, 10)

        # Treasure picker — only visible/enabled when the selected room
        # type has a non-zero treasure_cost (currently the Glade). The
        # row is shown/hidden by _update_treasure_choice when the type
        # changes; cog-acc-wise, hiding > permanently empty so the
        # picker doesn't add tab clutter for indoor/outdoor/aquatic/
        # aviary builds.
        self.treasure_row = wx.BoxSizer(wx.HORIZONTAL)
        self.treasure_label = wx.StaticText(
            self, label="Treasure to spend:",
        )
        self.treasure_row.Add(
            self.treasure_label, 0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.treasure_choice = wx.Choice(self, choices=[])
        self.treasure_choice.SetName("Treasure to spend")
        self.treasure_row.Add(self.treasure_choice, 1)
        self.treasure_row_sizer_item = sizer.Add(
            self.treasure_row, 0, wx.ALL | wx.EXPAND, 10,
        )

        self.starter_checkbox = wx.CheckBox(
            self,
            label="Add starter pairs to the new room",
        )
        self.starter_checkbox.SetValue(True)
        self.starter_checkbox.SetName("Add starter pairs")
        sizer.Add(self.starter_checkbox, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Per-instance species restriction. The room type sets which
        # species CAN live there; this list lets the player narrow that
        # to "only these species, in this specific room." All compatible
        # species start checked, so the default behaviour is unchanged.
        allowed_static = wx.StaticBox(self, label="Allowed species in this room")
        allowed_box_sizer = wx.StaticBoxSizer(allowed_static, wx.VERTICAL)
        allowed_intro = wx.StaticText(
            self,
            label=("Which species can live in this room. All compatible "
                   "species are allowed by default — uncheck any you want "
                   "to keep out. You can change this later by editing "
                   "the room. Use arrow keys to move, Space to toggle."),
        )
        allowed_intro.Wrap(520)
        allowed_intro.SetForegroundColour(wx.Colour(90, 90, 90))
        allowed_box_sizer.Add(allowed_intro, 0, wx.ALL, 4)
        self.allowed_box = make_state_announcing_checklist(
            self, "Allowed species", items=[], checked_ids=[],
            size=(-1, 110),
        )
        allowed_box_sizer.Add(self.allowed_box, 1, wx.ALL | wx.EXPAND, 4)
        sizer.Add(allowed_box_sizer, 0, wx.ALL | wx.EXPAND, 10)

        name_row = wx.BoxSizer(wx.HORIZONTAL)
        name_row.Add(
            wx.StaticText(self, label="Room name:"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.name_field = wx.TextCtrl(self, value="")
        self.name_field.SetName("Room name")
        self.name_field.SetHint("e.g. Sunroom, Garden, Cozy Corner")
        name_row.Add(self.name_field, 1)
        sizer.Add(name_row, 0, wx.ALL | wx.EXPAND, 10)

        # Cancel comes first in the tab order so Build is the LAST thing
        # the player tab-lands on — they were accidentally Space-/Enter-
        # activating Build expecting a navigation step. Build keeps
        # SetDefault so Enter still confirms when the player wants it to;
        # the fix is purely about tab ordering, not Enter behaviour.
        btns = wx.BoxSizer(wx.HORIZONTAL)
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "Cancel")
        ok_btn = wx.Button(self, wx.ID_OK, "Build")
        ok_btn.SetDefault()
        btns.AddStretchSpacer()
        btns.Add(cancel_btn, 0, wx.RIGHT, 8)
        btns.Add(ok_btn, 0)
        sizer.Add(btns, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)
        self._update_type_detail()
        self._update_species_choice()
        self._update_allowed_species()
        self._update_treasure_choice()
        # Initial focus on the type chooser — the first decision the
        # player makes — rather than the name field. NVDA reads "Room
        # type, radio box, [first option], 1 of N" instead of "Room
        # name, edit, blank," giving the player the lay of the land
        # before they have to type anything.
        if self.type_radio is not None:
            self.type_radio.SetFocus()
        else:
            self.name_field.SetFocus()

    def selected_type_id(self):
        if self.type_radio is None or not self.type_ids:
            return None
        return self.type_ids[self.type_radio.GetSelection()]

    def selected_species_id(self):
        sel = self.species_choice.GetSelection()
        if sel < 0:
            return None
        return self.species_choice.GetClientData(sel)

    def add_starter_pairs(self):
        return self.starter_checkbox.GetValue()

    def _update_type_detail(self):
        tid = self.selected_type_id()
        if not tid:
            self.type_detail.ChangeValue("(no room types loaded)")
            return
        spec = ROOM_TYPES[tid]
        meter_words = ", ".join(m["label"] for m in spec.get("meters", []))
        self.type_detail.ChangeValue(
            f"{spec.get('description', '')}\nMeters: {meter_words}."
        )

    def _update_species_choice(self):
        self.species_choice.Clear()
        tid = self.selected_type_id()
        if not tid:
            self.species_choice.Disable()
            return
        species_ids = room_type_compatible_species(tid)
        if not species_ids:
            self.species_choice.Append("(no creatures available yet)", None)
            self.species_choice.SetSelection(0)
            self.species_choice.Disable()
            return
        for sid in species_ids:
            spec = SPECIES_DATA.get(sid, {}).get("spec", {})
            self.species_choice.Append(spec.get("name", sid), sid)
        self.species_choice.SetSelection(0)
        self.species_choice.Enable(len(species_ids) > 1)

    def _update_allowed_species(self):
        """Repopulate the allowed-species checklist for the currently
        selected room type. All species start checked (the type's full
        compat list); the player narrows by unchecking.
        """
        tid = self.selected_type_id()
        if not tid:
            repopulate_state_announcing_checklist(self.allowed_box, [], [])
            return
        species_ids = room_type_compatible_species(tid)
        items = [
            (sid, f"{SPECIES_DATA.get(sid, {}).get('spec', {}).get('name', sid)} ({sid})")
            for sid in species_ids
        ]
        repopulate_state_announcing_checklist(
            self.allowed_box, items, checked_ids=species_ids,
        )

    def selected_allowed_species(self):
        return checklist_get_checked_ids(self.allowed_box)

    def _update_treasure_choice(self):
        """Show or hide the treasure picker based on the selected type's
        treasure_cost. When shown, the picker is filled with the player's
        current treasures (with counts); first one is pre-selected so a
        sighted player sees a default choice and an NVDA user hears one
        when they tab to the field. When the player has none, the picker
        is shown but disabled with a single explanatory item.
        """
        tid = self.selected_type_id()
        cost = get_treasure_cost(ROOM_TYPES.get(tid)) if tid else 0
        if cost <= 0:
            self.treasure_label.Hide()
            self.treasure_choice.Hide()
            self.Layout()
            return
        # Treasure required — populate from inventory.
        self.treasure_label.Show()
        self.treasure_choice.Show()
        self.treasure_choice.Clear()
        treasures = list_treasures(self.parent_panel.frame.state)
        if not treasures:
            self.treasure_choice.Append("(no treasures yet — keep digging)", None)
            self.treasure_choice.SetSelection(0)
            self.treasure_choice.Disable()
        else:
            for name, count, _desc in treasures:
                label = f"{name} (x{count})" if count > 1 else name
                self.treasure_choice.Append(label, name)
            self.treasure_choice.SetSelection(0)
            self.treasure_choice.Enable()
        self.Layout()

    def selected_treasure_name(self):
        """Return the chosen treasure name, or None if no choice / not
        applicable for the current room type.
        """
        if not self.treasure_choice.IsShown():
            return None
        sel = self.treasure_choice.GetSelection()
        if sel < 0:
            return None
        return self.treasure_choice.GetClientData(sel)

    def on_type_change(self, evt):
        self._update_type_detail()
        self._update_species_choice()
        self._update_allowed_species()
        self._update_treasure_choice()

    def get_room_name(self):
        return self.name_field.GetValue().strip()


class RenameDialog(wx.Dialog):
    """Prompt for a new creature name, with a checkbox to also append the
    name to the user's names file for that species + sex.
    """

    def __init__(self, parent, current_name, sex, species_id="cat"):
        super().__init__(parent, title="Rename", size=(460, 240))
        self.current_name = current_name
        self.sex = sex
        self.species_id = species_id
        self._build()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        spec = SPECIES_DATA.get(self.species_id, {}).get("spec", {})
        sex_word = (
            spec.get("sex_label_female", "female")
            if self.sex == "F"
            else spec.get("sex_label_male", "male")
        )
        species_name = spec.get("name", "creature").lower()

        prompt = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=f"Rename {self.current_name}, the {sex_word} {species_name}.",
            size=(-1, 28),
        )
        prompt.SetName("Rename prompt")
        sizer.Add(prompt, 0, wx.ALL | wx.EXPAND, 10)

        name_row = wx.BoxSizer(wx.HORIZONTAL)
        name_row.Add(
            wx.StaticText(self, label="New name:"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.name_field = wx.TextCtrl(self, value=self.current_name)
        self.name_field.SetName("New name")
        name_row.Add(self.name_field, 1)
        sizer.Add(name_row, 0, wx.ALL | wx.EXPAND, 10)

        list_label = f"{sex_word} {species_name}"
        self.save_checkbox = wx.CheckBox(
            self,
            label=f"Also add this name to my {list_label} names list",
        )
        self.save_checkbox.SetName(f"Save to {list_label} names list")
        sizer.Add(self.save_checkbox, 0, wx.ALL, 10)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(self, wx.ID_OK, "OK")
        ok_btn.SetDefault()
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "Cancel")
        btns.AddStretchSpacer()
        btns.Add(ok_btn, 0, wx.RIGHT, 8)
        btns.Add(cancel_btn, 0)
        sizer.Add(btns, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)
        self.name_field.SetFocus()
        self.name_field.SelectAll()

    def get_result(self):
        name = self.name_field.GetValue().strip()
        if not name:
            return None
        return (name, self.save_checkbox.GetValue())


def prompt_rename(parent, current_name, sex, species_id="cat"):
    """Show RenameDialog. Returns (new_name, save_to_file_bool) or None."""
    with RenameDialog(parent, current_name, sex, species_id) as dlg:
        if dlg.ShowModal() != wx.ID_OK:
            return None
        return dlg.get_result()



# ===== Modding: species + room type editors =====

# Meter field names live on MeterPanel.FIELDS (see RoomTypeEditorDialog).
# The JSON schema is documented in docs/MODDING.md.


def _checklist_label(checked, base_text):
    return ("checked: " if checked else "not checked: ") + base_text


def make_state_announcing_checklist(parent, accessible_name, items,
                                    checked_ids=None, size=(-1, 130)):
    """Build a wx.CheckListBox whose item strings carry "checked: " /
    "not checked: " prefixes so NVDA announces state along with the item.

    `items`: list of (item_id, display_text) tuples. `item_id` is the
    stable key written to JSON; `display_text` is what the user reads
    (without the state prefix — that's added here).
    `checked_ids`: iterable of ids that should start checked.

    Returns the CheckListBox. Use checklist_get_checked_ids(box) to
    pull the current selection back out as a list of item_ids.
    Use repopulate_state_announcing_checklist(box, items, checked_ids)
    to swap out the choice list (for dialogs where the available items
    depend on another control like a room-type selector).
    """
    box = wx.CheckListBox(parent, choices=[], size=size)
    box.SetName(accessible_name)
    repopulate_state_announcing_checklist(box, items, checked_ids)

    def on_toggle(evt):
        idx = evt.GetSelection()
        if 0 <= idx < len(box._item_ids):
            checked = box.IsChecked(idx)
            box.SetString(idx, _checklist_label(checked, box._raw_labels[idx]))
        evt.Skip()

    box.Bind(wx.EVT_CHECKLISTBOX, on_toggle)
    return box


def repopulate_state_announcing_checklist(box, items, checked_ids=None):
    """Replace the entire contents of a CheckListBox built by
    make_state_announcing_checklist(). Use when the upstream choice list
    changes — e.g., the user picks a different room type and the set of
    species available to allow changes accordingly.
    """
    already = set(checked_ids or [])
    item_ids = [i for i, _ in items]
    raw_labels = [d for _, d in items]
    initial_strings = [
        _checklist_label(item_ids[i] in already, raw_labels[i])
        for i in range(len(items))
    ]
    box.Set(initial_strings)
    for i, item_id in enumerate(item_ids):
        if item_id in already:
            box.Check(i)
    # Stash the parallel arrays on the box so the toggle handler and the
    # save-time getter can do their work without the caller passing
    # state around.
    box._item_ids = item_ids
    box._raw_labels = raw_labels


def checklist_get_checked_ids(box):
    """Return the list of item_ids that are currently checked, in
    original-list order."""
    return [
        box._item_ids[i]
        for i in range(len(box._item_ids))
        if box.IsChecked(i)
    ]


class SettingsDialog(wx.Dialog):
    """Adjust every gameplay-tunable setting from one place.

    Save commits to SETTINGS (live) and state["settings"] (persisted) and
    triggers a save_state. Reset writes the defaults into the form fields
    but does NOT save until the user clicks Save.
    """

    INT_FIELDS = [
        ("full_decay_seconds", "Default decay time for meters (seconds, used when a room type doesn't set its own)"),
        ("affection_decay_seconds", "How long affection lasts before it drops to zero (seconds)"),
        ("pair_formation_seconds", "How long two creatures of the same species spend together to become a pair (seconds)"),
        # breed_cooldown_seconds dropped from runtime UI — it's now a
        # per-species field in the species editor's Advanced section.
        # Still in DEFAULT_SETTINGS as the fallback for species that
        # don't set their own (and for old saves' migration).
        ("min_babies", "Smallest litter size (default — species can override)"),
        ("max_babies", "Biggest litter size (default — species can override)"),
        ("digs_per_day", "How many times you can dig in the park each calendar day"),
        ("slot_expansion_common_cost", "Common items needed to add one slot to a room"),
        ("auto_breed_interval_seconds", "Auto-breeding interval (when on)"),
        ("elder_production_seconds", "How often each elder produces one item from their room's build recipe (seconds)"),
        ("ambient_interval_seconds", "How often an ambient observation can fire, at most (seconds)"),
        ("ambient_quiet_seconds", "How long since the last announcement before ambient kicks in (seconds)"),
    ]
    # Fields whose values are durations in seconds — for plain-language
    # parsing and helper text. Any field listed here can also be entered as
    # '1 hour' / '30 min' etc., not just raw seconds.
    DURATION_INT_KEYS = {
        "full_decay_seconds", "affection_decay_seconds",
        "pair_formation_seconds",
        "auto_breed_interval_seconds",
        "elder_production_seconds",
        "ambient_interval_seconds", "ambient_quiet_seconds",
    }
    FLOAT_FIELDS = [
        ("lifecycle_pace",
         "Lifecycle pace — one knob for all life stages (1.0 = default; "
         "0.5 = babies grow up and creatures age toward elder twice as "
         "fast; 2.0 = twice as slow). Affects every species' breeding "
         "age and elder age proportionally."),
        ("breed_success_chance", "Chance a breeding attempt works"),
        ("breed_min_care", "How clean and fed the room must be before its creatures will breed (every meter must be at or above this level)"),
        ("inbreeding_disability_mult",
         "Disability chance multiplier when parents are closely related "
         "— siblings, half-siblings, or parent and child (1 = no effect, "
         "3 = default)"),
        ("color_mutation_chance",
         "Per-color chance a baby rolls a fresh colour from the species' "
         "pool instead of inheriting from a parent. 0 = strict (always "
         "one colour from each parent). 0.05 default = ~5% per slot, so "
         "occasionally a kitten shows a recessive ancestor colour. "
         "1 = always fresh roll, parents irrelevant."),
        ("wild_emigration_chance",
         "Chance per check that a healthy elder (past their species' "
         "elder age — the same milestone at which they retire from "
         "breeding) leaves for the wild. 0 turns the mechanic off; "
         "0.05 default = ~5%/check. Disabled creatures never emigrate "
         "— they retire to the village permanently instead."),
        ("low_meter_threshold", "Warn when any room's care meter (food, water, litter, shelter, water quality, nest, cover, etc.) drops below this"),
        ("dig_chance_nothing",  "Chance a dig finds nothing"),
        ("dig_chance_common",   "Chance a dig finds a common item (sticks, stones, etc.)"),
        ("dig_chance_uncommon", "Chance a dig finds an uncommon item (ribbons, feathers, etc.)"),
        ("dig_chance_object",   "Chance a dig finds an object (a toy, a small basket, etc.)"),
        ("dig_chance_treasure", "Chance a dig finds a real treasure"),
    ]

    # Tab structure for the dialog. Each entry is (tab_id, tab_label,
    # [field_keys_in_display_order]). Field keys reference INT_FIELDS or
    # FLOAT_FIELDS — the helpers look up the label/type metadata there,
    # so categorising a field is just a matter of adding it to a tab's
    # list. New settings: pick the right tab, drop the key in.
    #
    # The tab labels use && (escaped & for wx mnemonic syntax) where a
    # literal & is needed.
    TABS = [
        ("lifecycle", "Time && lifecycle", [
            "lifecycle_pace",
            "pair_formation_seconds",
            # breed_cooldown_seconds moved to the species editor's
            # Advanced section — it's per-species now (a cat between
            # litters is biologically different from a chicken between
            # clutches). The key still exists in DEFAULT_SETTINGS as
            # the fallback for any species that doesn't set its own,
            # but it's not editable from this dialog anymore.
            "elder_production_seconds",
            "color_mutation_chance",
            "wild_emigration_chance",
        ]),
        ("breeding", "Breeding", [
            "breed_success_chance",
            "breed_min_care",
            "min_babies",
            "max_babies",
            "auto_breed_interval_seconds",
            "inbreeding_disability_mult",
        ]),
        ("meters", "Care meters", [
            "full_decay_seconds",
            "affection_decay_seconds",
            "low_meter_threshold",
        ]),
        ("park", "Park", [
            "digs_per_day",
            "slot_expansion_common_cost",
            "dig_chance_nothing",
            "dig_chance_common",
            "dig_chance_uncommon",
            "dig_chance_object",
            "dig_chance_treasure",
        ]),
        ("ambient", "Ambient", [
            "ambient_interval_seconds",
            "ambient_quiet_seconds",
        ]),
    ]

    def __init__(self, parent_frame):
        super().__init__(parent_frame, title="Settings", size=(680, 700))
        self.frame = parent_frame
        self.controls = {}
        self.helpers = {}  # key → wx.StaticText shown under duration fields
        self._build()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        intro = wx.StaticText(
            self,
            label=(
                "Tweak how things work. All time fields accept plain "
                "language: '1 hour', '30 minutes', '1h 30m', or just a "
                "number of seconds. Chances go from 0 (never) to 1 "
                "(always). Click Save to keep your changes."
            ),
        )
        intro.Wrap(640)
        intro.SetForegroundColour(wx.Colour(90, 90, 90))
        sizer.Add(intro, 0, wx.ALL | wx.EXPAND, 10)

        # Build label lookups so the per-tab loop can dispatch by type
        # without re-iterating INT_FIELDS / FLOAT_FIELDS each time.
        int_labels = {key: label for key, label in self.INT_FIELDS}
        float_keys = {key for key, _ in self.FLOAT_FIELDS}
        float_labels = {key: label for key, label in self.FLOAT_FIELDS}

        notebook = wx.Notebook(self)
        notebook.SetName("Settings categories")
        for tab_id, tab_label, field_keys in self.TABS:
            page = wx.Panel(notebook)
            page_sizer = wx.BoxSizer(wx.VERTICAL)
            grid = wx.FlexGridSizer(rows=0, cols=2, hgap=12, vgap=8)
            grid.AddGrowableCol(1, 1)
            for key in field_keys:
                if key in float_keys:
                    self._add_float(grid, key, float_labels[key], parent=page)
                else:
                    self._add_int(grid, key, int_labels[key], parent=page)
            page_sizer.Add(grid, 1, wx.ALL | wx.EXPAND, 10)
            page.SetSizer(page_sizer)
            notebook.AddPage(page, tab_label)
        sizer.Add(notebook, 1, wx.ALL | wx.EXPAND, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        save_btn = wx.Button(self, label="Save")
        save_btn.Bind(wx.EVT_BUTTON, self.on_save)
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "Cancel")
        reset_btn = wx.Button(self, label="Reset to defaults")
        reset_btn.Bind(wx.EVT_BUTTON, self.on_reset)
        btns.Add(save_btn, 0, wx.RIGHT, 8)
        btns.Add(cancel_btn, 0, wx.RIGHT, 8)
        btns.AddStretchSpacer()
        btns.Add(reset_btn, 0)
        sizer.Add(btns, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)
        save_btn.SetDefault()

    def _add_int(self, grid, key, label_text, parent=None):
        # Plain TextCtrl + parsing instead of wx.SpinCtrl. Holding a SpinCtrl
        # arrow key spins the value rapidly and queues an NVDA announcement
        # per intermediate value — for fields with large defaults like
        # seconds_per_game_day (86400) that floods NVDA's speech queue and
        # makes the screen reader appear to hang. Typing the value directly
        # is one announcement on tab-out.
        # `parent` defaults to the dialog itself for backward-compat, but
        # the tabbed _build now passes the notebook page so child controls
        # belong to the correct page (otherwise wx layouts and tab order
        # break).
        parent = parent or self
        label = wx.StaticText(parent, label=label_text + ":")
        is_duration = key in self.DURATION_INT_KEYS
        if is_duration:
            initial = format_duration_human(int(SETTINGS[key]))
        else:
            initial = str(int(SETTINGS[key]))
        ctrl = wx.TextCtrl(parent, value=initial)
        ctrl.SetName(label_text)

        inner = wx.BoxSizer(wx.VERTICAL)
        inner.Add(ctrl, 0, wx.EXPAND)
        if is_duration:
            helper = wx.StaticText(parent, label=self._helper_text(key, int(SETTINGS[key])))
            helper.SetForegroundColour(wx.Colour(90, 90, 90))
            inner.Add(helper, 0, wx.TOP, 2)
            self.helpers[key] = helper
            # Update the helper as the user types so they see what their
            # value means without having to save first. EVT_TEXT fires on
            # every keystroke; StaticText.SetLabel doesn't generate NVDA
            # announcements, so this stays quiet for screen-reader users.
            ctrl.Bind(wx.EVT_TEXT, lambda evt, k=key: self._refresh_helper(k))

        grid.Add(label, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(inner, 1, wx.EXPAND)
        self.controls[key] = ctrl

    def _helper_text(self, key, seconds):
        """Plain-language description shown under a duration field."""
        if seconds <= 0:
            return "(currently: 0 seconds)"
        human = format_duration_human(seconds)
        if key == "full_decay_seconds":
            return f"(= {human}; default for any meter without its own rate)"
        if key == "affection_decay_seconds":
            return f"(= {human} from full to empty without petting)"
        if key == "auto_breed_interval_seconds":
            return f"(= every {human} when auto-breeding is on)"
        if key == "pair_formation_seconds":
            return f"(= {human} together before becoming a pair)"
        if key == "elder_production_seconds":
            return f"(= {human} between each elder's productions)"
        return f"(= {human})"

    def _refresh_helper(self, key):
        helper = self.helpers.get(key)
        if helper is None:
            return
        raw = self.controls[key].GetValue().strip()
        try:
            seconds = parse_duration(raw)
            helper.SetLabel(self._helper_text(key, seconds))
        except ValueError:
            helper.SetLabel("(unrecognized — try '1 hour' or '30 minutes')")

    def _add_float(self, grid, key, label_text, parent=None):
        # wx.SpinCtrlDouble's inner text control on Windows doesn't expose the
        # parent's accessible name to NVDA reliably, so floats use a plain
        # wx.TextCtrl with parsing on save instead. Tab order is preserved
        # and the label-via-SetName works the same as the integer fields.
        # See _add_int for why `parent` is parameterised.
        parent = parent or self
        label = wx.StaticText(parent, label=label_text + ":")
        ctrl = wx.TextCtrl(parent, value=f"{float(SETTINGS[key]):.2f}")
        ctrl.SetName(label_text)
        grid.Add(label, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(ctrl, 1, wx.EXPAND)
        self.controls[key] = ctrl

    # Float fields that aren't probabilities — they live outside [0, 1]
    # and the universal 0-1 clamp would silently break them. Listed
    # explicitly so a new chance/probability added to FLOAT_FIELDS picks
    # up the [0, 1] clamp by default.
    FLOAT_NON_PROBABILITY_KEYS = {
        "lifecycle_pace",            # default 1.0; can be < 1 (faster) or > 1 (slower)
        "inbreeding_disability_mult", # default 3.0; multiplier, can be > 1
    }

    def on_save(self, evt):
        float_keys = {key for key, _ in self.FLOAT_FIELDS}
        new = {}
        for key, ctrl in self.controls.items():
            raw = ctrl.GetValue().strip()
            if key in float_keys:
                try:
                    parsed = float(raw)
                except ValueError:
                    wx.MessageBox(
                        f"'{raw}' isn't a number for '{key}'. "
                        "Try something like 0.5.",
                        "Settings",
                        wx.OK | wx.ICON_WARNING,
                        self,
                    )
                    return
                if key in self.FLOAT_NON_PROBABILITY_KEYS:
                    # lifecycle_pace and inbreeding_disability_mult are
                    # multipliers, not probabilities. Clamp to a small
                    # positive floor so divisions don't blow up; no
                    # upper bound (player can pick whatever pace /
                    # multiplier they want). Without this branch the
                    # universal 0-1 clamp below silently capped them
                    # — saving Settings turned every "twice as slow"
                    # lifecycle_pace into 1.0 and disabled inbreeding
                    # disability scaling.
                    new[key] = max(0.01, parsed)
                else:
                    new[key] = max(0.0, min(1.0, parsed))
            elif key in self.DURATION_INT_KEYS:
                try:
                    parsed = parse_duration(raw)
                except ValueError as e:
                    wx.MessageBox(
                        str(e),
                        "Settings",
                        wx.OK | wx.ICON_WARNING,
                        self,
                    )
                    return
                # Clamp to >=1 so divisions in the tick loops never see 0.
                new[key] = max(1, parsed)
            else:
                try:
                    parsed = int(raw)
                except ValueError:
                    wx.MessageBox(
                        f"'{raw}' isn't a whole number for '{key}'. "
                        "Try something like 60.",
                        "Settings",
                        wx.OK | wx.ICON_WARNING,
                        self,
                    )
                    return
                new[key] = max(0, parsed)
        if new["min_babies"] > new["max_babies"]:
            wx.MessageBox(
                "Minimum babies per litter can't be more than the maximum.",
                "Settings",
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return
        apply_settings(self.frame.state, new)
        save_state(self.frame.state)
        self.frame.announce_event("settings_saved")
        self.EndModal(wx.ID_OK)

    def on_reset(self, evt):
        with wx.MessageDialog(
            self,
            "Reset all settings to their defaults? You'll still need to click Save to keep the change.",
            "Reset to defaults?",
            wx.YES_NO | wx.ICON_QUESTION,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_YES:
                return
        float_keys = {key for key, _ in self.FLOAT_FIELDS}
        for key, value in DEFAULT_SETTINGS.items():
            if key not in self.controls:
                continue
            if key in float_keys:
                self.controls[key].SetValue(f"{float(value):.2f}")
            elif key in self.DURATION_INT_KEYS:
                self.controls[key].SetValue(format_duration_human(int(value)))
            else:
                self.controls[key].SetValue(str(int(value)))
        # Update helper texts to match the freshly reset values.
        for key in self.helpers:
            self._refresh_helper(key)
