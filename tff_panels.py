"""
Time for Family -- the four main panels (Room, Village, Park, Stats).

The pages inside MainFrame's notebook. Each opens dialogs from tff_dialogs;
none reference each other or MainFrame (MainFrame builds them). Depends on
engine, sound, and dialogs.
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


class RoomPanel(wx.Panel):
    def __init__(self, parent, frame, room_id):
        super().__init__(parent)
        self.frame = frame
        self.room_id = room_id
        self._cat_ids = []
        self._build()
        self.refresh()

    def get_room(self):
        return find_room(self.frame.state, self.room_id)

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        room = self.get_room()

        # Title row is just the room name now — Edit room moved into the
        # "Room options" popup menu below to cut the per-room button count.
        # Held on self so refresh() can re-sync after a rename without
        # having to rebuild the whole panel.
        self.title_ctrl = wx.StaticText(self, label=room["name"])
        font = self.title_ctrl.GetFont()
        font.PointSize += 4
        self.title_ctrl.SetFont(font.Bold())
        sizer.Add(self.title_ctrl, 0, wx.ALL, 8)

        meters_box = wx.StaticBox(self, label="Care")
        meters_sizer = wx.StaticBoxSizer(meters_box, wx.VERTICAL)
        self.meter_gauges = {}
        self.meter_status_ctrls = {}
        room = self.get_room()
        type_spec = ROOM_TYPES.get(room.get("type", "indoor"), {})
        meter_specs = type_spec.get("meters") or [
            {"key": "food",   "label": "Food",   "verb_present": "Refill", "full_word": "full"},
            {"key": "water",  "label": "Water",  "verb_present": "Refill", "full_word": "full"},
            {"key": "litter", "label": "Litter", "verb_present": "Clean",  "full_word": "fresh"},
        ]
        # Batch refill comes FIRST inside the Care box so it's the first
        # focusable widget on the panel — one Tab from the room picker
        # lands here regardless of how many meters the room has. (Modder
        # room types can define 6+ meters; without this, reaching the
        # batch button via Tab would mean walking past every meter
        # status / individual refill in turn.)
        self.refill_all_btn = wx.Button(self, label="Refill all care")
        self.refill_all_btn.Bind(wx.EVT_BUTTON, self.on_refill_all)
        self.refill_all_btn.SetToolTip(
            "Top up every care meter in this room at once. Same effect as "
            "clicking each meter's button in turn."
        )
        meters_sizer.Add(self.refill_all_btn, 0, wx.ALL, 4)
        for meter_spec in meter_specs:
            meter_key = meter_spec["key"]
            meter_label = meter_spec.get("label", meter_key.replace("_", " ").title())
            verb = meter_spec.get("verb_present", "Refill")
            full_word = meter_spec.get("full_word", "full")
            row = wx.BoxSizer(wx.HORIZONTAL)
            gauge = wx.Gauge(self, range=100, size=(120, 20))
            row.Add(gauge, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
            status = wx.TextCtrl(
                self,
                style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
                value=f"{meter_label}: 100% — {full_word}",
                size=(-1, 28),
            )
            status.SetName(f"{meter_label} status")
            row.Add(status, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
            btn = wx.Button(self, label=f"{verb} {meter_label}")
            btn.Bind(wx.EVT_BUTTON, lambda evt, m=meter_key: self.on_refill(m))
            row.Add(btn, 0, wx.ALIGN_CENTER_VERTICAL)
            meters_sizer.Add(row, 0, wx.ALL | wx.EXPAND, 4)
            self.meter_gauges[meter_key] = gauge
            self.meter_status_ctrls[meter_key] = status
        sizer.Add(meters_sizer, 0, wx.ALL | wx.EXPAND, 8)

        species_label, species_label_plural = self._species_labels()
        care_label = self._care_action_label()

        self.cats_box = wx.StaticBox(self, label=species_label_plural)
        cats_sizer = wx.StaticBoxSizer(self.cats_box, wx.VERTICAL)
        self.cats_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.cats_list.SetName(f"{species_label_plural} in this room")
        for col, (label, w) in enumerate([
            ("Name", 120), ("Species", 90), ("Sex", 50), ("Pair", 60),
            ("Affection", 100), ("Age", 130),
        ]):
            self.cats_list.AppendColumn(label, width=w)
        self.cats_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_cat_selected)
        self.cats_list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.on_cat_selected)
        cats_sizer.Add(self.cats_list, 1, wx.ALL | wx.EXPAND, 4)

        self.cat_detail = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=f"Select a {species_label} to see their description.",
            size=(-1, 56),
        )
        self.cat_detail.SetName(f"Selected {species_label} description")
        cats_sizer.Add(self.cat_detail, 0, wx.ALL | wx.EXPAND, 4)

        # Batch pet — gives affection to every creature in the room in one
        # click. Sits on its own row above the per-creature actions so the
        # "act on the room as a whole" affordance is visually separate
        # from "act on the selected one."
        batch_actions = wx.BoxSizer(wx.HORIZONTAL)
        self.pet_all_btn = wx.Button(self, label="Pet everyone here")
        self.pet_all_btn.Bind(wx.EVT_BUTTON, self.on_pet_all)
        self.pet_all_btn.SetToolTip(
            "Give a moment of attention to every creature in this room at "
            "once. Each gets the same affection bump as a single pet."
        )
        batch_actions.Add(self.pet_all_btn, 0)
        cats_sizer.Add(batch_actions, 0, wx.ALL, 4)

        actions = wx.BoxSizer(wx.HORIZONTAL)
        self.pet_btn = wx.Button(self, label=f"{care_label} selected {species_label}")
        self.pet_btn.Bind(wx.EVT_BUTTON, self.on_pet)
        self.pet_btn.SetToolTip("Pet (or feed) the selected creature; raises affection.")
        self.rename_btn = wx.Button(self, label=f"Rename selected {species_label}")
        self.rename_btn.Bind(wx.EVT_BUTTON, self.on_rename)
        self.rename_btn.SetToolTip("Give the selected creature a new name; you can also add the name to your saved names list.")
        breed_btn = wx.Button(self, label="Try to breed")
        breed_btn.Bind(wx.EVT_BUTTON, self.on_breed)
        breed_btn.SetToolTip("Try to breed an eligible mature pair in this room. Pairs need both halves mature, the room cared-for, and not on cooldown.")
        self.move_btn = wx.Button(self, label=f"Move selected {species_label}…")
        self.move_btn.Bind(wx.EVT_BUTTON, self.on_move_creature)
        place_name = self.frame.state.get("village_name", "Village")
        self.move_btn.SetToolTip(
            f"Move the selected creature to another compatible room or {place_name}."
        )
        # "Room options" collapses the rarely-used standalone buttons
        # (Edit room, Add a slot) behind one popup menu so the per-room
        # button count stays low.
        self.options_btn = wx.Button(self, label="Room options ▾")
        self.options_btn.Bind(wx.EVT_BUTTON, self.on_room_options)
        self.options_btn.SetToolTip(
            "Edit this room or add a slot."
        )
        actions.Add(self.pet_btn, 0, wx.RIGHT, 8)
        actions.Add(self.rename_btn, 0, wx.RIGHT, 8)
        actions.Add(self.move_btn, 0, wx.RIGHT, 8)
        actions.Add(breed_btn, 0, wx.RIGHT, 8)
        actions.Add(self.options_btn, 0)
        cats_sizer.Add(actions, 0, wx.ALL, 4)

        sizer.Add(cats_sizer, 1, wx.ALL | wx.EXPAND, 8)

        self.slot_text = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 28),
        )
        self.slot_text.SetName("Slots")
        sizer.Add(self.slot_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 8)

        self.SetSizer(sizer)

    def refresh_meters(self):
        room = self.get_room()
        last_refilled = room.get("meter_last_refilled", {})
        now = time.time()
        type_spec = ROOM_TYPES.get(room.get("type", "indoor"), {})
        meter_lookup = {m["key"]: m for m in type_spec.get("meters", [])}
        for meter_key, value in room["meters"].items():
            pct = int(round(value * 100))
            if meter_key in self.meter_gauges:
                self.meter_gauges[meter_key].SetValue(max(0, min(100, pct)))
            spec = meter_lookup.get(meter_key, {})
            # Per-meter decay rate (room-type JSON override) falls back to
            # the global full_decay_seconds when the meter has no override.
            decay_secs = meter_decay_seconds_for(spec)
            text = self._meter_status_text(meter_key, spec, value, last_refilled.get(meter_key), decay_secs, now)
            if meter_key in self.meter_status_ctrls:
                self.meter_status_ctrls[meter_key].ChangeValue(text)

    @staticmethod
    def _meter_status_text(meter_key, meter_spec, value, last_refilled_at, decay_secs, now):
        label = meter_spec.get("label", meter_key.replace("_", " ").title())
        low_word = meter_spec.get("low_word", "empty in")
        empty_word = meter_spec.get("empty_word", "empty")
        full_word = meter_spec.get("full_word", "full")
        past_verb = meter_spec.get("verb_past", "Refilled")

        pct = int(round(value * 100))
        if value <= 0.0:
            state_text = empty_word
        elif value >= 1.0:
            state_text = full_word
        else:
            state_text = f"{low_word} {format_duration(int(value * decay_secs))}"

        if last_refilled_at:
            ago = max(0, int(now - last_refilled_at))
            history_text = f"{past_verb} just now" if ago < 1 else f"{past_verb} {format_duration(ago)} ago"
        else:
            history_text = ""

        parts = [f"{label}: {pct}%", state_text]
        if history_text:
            parts.append(history_text)
        return " — ".join(parts)

    def on_room_options(self, evt):
        """Pop up the Room options menu beneath the button — Edit room
        and Add a slot.
        """
        menu = wx.Menu()
        edit_item = menu.Append(wx.ID_ANY, "&Edit room…")
        slot_item = menu.Append(wx.ID_ANY, "&Add a slot…")
        self.Bind(wx.EVT_MENU, self.on_edit_room, edit_item)
        self.Bind(wx.EVT_MENU, self.on_expand_room, slot_item)
        # Pop up directly below the button so focus / NVDA stay anchored.
        self.PopupMenu(menu, self.options_btn.GetPosition() + (0, self.options_btn.GetSize().height))
        menu.Destroy()

    def refresh_cats(self):
        room = self.get_room()
        # The room's species label can drift over the room's life — cats out,
        # hamsters in — so re-derive it from current contents and update the
        # box title and the list's accessible name. wx.StaticBox.SetLabel
        # works at runtime; ListCtrl.SetName too.
        _, plural = self._species_labels()
        self.cats_box.SetLabel(plural)
        self.cats_list.SetName(f"{plural} in this room")
        prev_sel_id = self.selected_cat_id()
        self.cats_list.DeleteAllItems()
        self._cat_ids = []
        new_sel_row = -1
        for i, cat in enumerate(room["creatures"]):
            row = self.cats_list.InsertItem(i, cat["name"])
            sid = cat.get("species", "cat")
            spec = SPECIES_DATA.get(sid, {}).get("spec", {})
            self.cats_list.SetItem(row, 1, spec.get("name", sid))
            self.cats_list.SetItem(row, 2, cat["sex"])
            self.cats_list.SetItem(row, 3, cat.get("pair_id") or "—")
            self.cats_list.SetItem(row, 4, f"{int(cat['affection'] * 100)}%")
            # Coarse format on the list (sub-minute = "newborn") so
            # the per-tick value refresh doesn't flip the cell every
            # second — that NVDA-flooded the row.
            self.cats_list.SetItem(row, 5, format_age_for_list(cat_age_seconds(cat)))
            self._cat_ids.append(cat["id"])
            if cat["id"] == prev_sel_id:
                new_sel_row = i
        if new_sel_row >= 0:
            self.cats_list.Select(new_sel_row)
            self.cats_list.Focus(new_sel_row)
        self.slot_text.ChangeValue(f"Slots: {len(room['creatures'])}/{room['slot_count']}")
        self.refresh_cat_detail()

    def refresh_ages(self):
        """Tick-driven: update the Age cells in the list when their
        bucket-formatted value has changed. format_age_for_list buckets
        to minute / hour / day boundaries so this rewrites a cell at
        most once per minute (usually much less), which keeps NVDA from
        being interrupted on every tick while still letting the visible
        age advance. Full refresh_cats stays for structural changes
        (creature added / removed / renamed / pair formed).
        """
        room = self.get_room()
        if not room:
            return
        by_id = {c["id"]: c for c in room["creatures"]}
        for row, cat_id in enumerate(self._cat_ids):
            cat = by_id.get(cat_id)
            if cat is None:
                continue
            new_age = format_age_for_list(cat_age_seconds(cat))
            if self.cats_list.GetItemText(row, 5) != new_age:
                self.cats_list.SetItem(row, 5, new_age)

    def refresh_cat_detail(self, announce=True):
        """Update the description box text.

        announce=True: uses SetValue, which fires wxEVT_TEXT so NVDA re-reads
        the box on focus. Use on selection change.
        announce=False: uses ChangeValue, silent — keeps the visible text
        current (e.g., live-updating "ready to breed in X" countdown) without
        making NVDA chatter.
        """
        cat = self.selected_cat()
        species_label, _ = self._species_labels()
        if cat is None:
            text = f"Select a {species_label} to see their description."
        else:
            text = f"{cat['name']}: {cat_full_description(cat)}"
            text += _status_line_for(cat)
        if self.cat_detail.GetValue() != text:
            if announce:
                self.cat_detail.SetValue(text)
            else:
                self.cat_detail.ChangeValue(text)

    def _room_species_id(self):
        """Best-guess single species id for the room. Used when we need to
        pick *one* species (e.g., for the default care action verb when no
        creature is selected). For labeling, prefer _species_labels() which
        is reality-aware about multi-species rooms.
        """
        room = self.get_room()
        creatures = room.get("creatures") or []
        if creatures:
            return creatures[0].get("species", "cat")
        allowed = room.get("allowed_species") or []
        if allowed:
            return allowed[0]
        compatible = room_type_compatible_species(room.get("type", "indoor"))
        return compatible[0] if compatible else "cat"

    def _species_labels(self):
        """Return (singular, plural) labels for the room.

        Reality-based: if the room actually contains a single species, label
        it that way; multi-species → "creature" / "Residents". Empty rooms
        fall back to the room's allowed_species list (single → species name,
        multi → "Residents").
        """
        room = self.get_room()
        creatures = room.get("creatures") or []
        species_present = {c.get("species") for c in creatures if c.get("species")}
        if len(species_present) > 1:
            return "creature", "Residents"
        if len(species_present) == 1:
            sid = next(iter(species_present))
            spec = SPECIES_DATA.get(sid, {}).get("spec", {})
            singular = spec.get("name", "creature").lower()
            return singular, spec.get("name_plural", singular + "s")
        allowed = room.get("allowed_species") or []
        if len(allowed) == 1:
            spec = SPECIES_DATA.get(allowed[0], {}).get("spec", {})
            singular = spec.get("name", "creature").lower()
            return singular, spec.get("name_plural", singular + "s")
        return "creature", "Residents"

    def _care_action_label(self):
        # If the room has a single species (present or allowed), use its
        # care action verb; otherwise default to "Pet".
        room = self.get_room()
        creatures = room.get("creatures") or []
        species_present = {c.get("species") for c in creatures if c.get("species")}
        if len(species_present) == 1:
            sid = next(iter(species_present))
        else:
            allowed = room.get("allowed_species") or []
            sid = allowed[0] if len(allowed) == 1 else None
        if not sid:
            return "Pet"
        spec = SPECIES_DATA.get(sid, {}).get("spec", {})
        return spec.get("care_action_label", "Pet")

    def refresh(self):
        # Pick up name changes from the room edit dialog. The title
        # StaticText was set once at build time; SetLabel here keeps it
        # in sync without a full panel rebuild.
        room = self.get_room()
        # The room behind this panel can disappear from state (a room-type
        # delete purges its rooms) while the panel is still in the book.
        # Bail quietly — the user is already told to restart to clear the
        # orphaned tabs; refreshing a roomless panel would crash.
        if room is None:
            return
        if self.title_ctrl.GetLabel() != room["name"]:
            self.title_ctrl.SetLabel(room["name"])
        self.refresh_meters()
        self.refresh_cats()
        self.refresh_action_labels()

    def selected_cat_id(self):
        sel = self.cats_list.GetFirstSelected()
        if sel < 0 or sel >= len(self._cat_ids):
            return None
        return self._cat_ids[sel]

    def selected_cat(self):
        cat_id = self.selected_cat_id()
        if not cat_id:
            return None
        return next(
            (c for c in self.get_room()["creatures"] if c["id"] == cat_id),
            None,
        )

    def on_cat_selected(self, evt):
        self.refresh_cat_detail()
        self.refresh_action_labels()
        evt.Skip()

    def refresh_action_labels(self):
        """Update Pet, Rename, and Move-to-village button labels based on the
        selected creature (or the room's primary species if nothing is selected).
        """
        cat = self.selected_cat()
        if cat is not None:
            species_id = cat.get("species", "cat")
            spec = SPECIES_DATA.get(species_id, {}).get("spec", {})
            verb = spec.get("care_action_label", "Pet")
            label = spec.get("name", "creature").lower()
        else:
            label, _ = self._species_labels()
            verb = self._care_action_label()
        self.pet_btn.SetLabel(f"{verb} selected {label}")
        self.rename_btn.SetLabel(f"Rename selected {label}")
        self.move_btn.SetLabel(f"Move selected {label}…")
        # The "everyone" button keys off the room's primary species (not
        # the per-creature selection) since the action applies to the
        # whole room. Mixed-species rooms fall back to "Pet."
        self.pet_all_btn.SetLabel(f"{self._care_action_label()} everyone here")

    def on_move_creature(self, evt):
        cat = self.selected_cat()
        if cat is None:
            self.frame.announce_event("select_creature")
            return
        species_id = cat.get("species", "cat")
        species_label = (
            SPECIES_DATA.get(species_id, {}).get("spec", {}).get("name", species_id).lower()
        )

        # Build destination choices: every other room that allows this
        # species and has a free slot, plus "the village".
        destinations = []  # list of (label, ("room", room_id) | ("village", None))
        for r in self.frame.state["rooms"]:
            if r["id"] == self.room_id:
                continue
            if species_id not in (r.get("allowed_species") or []):
                continue
            free = r["slot_count"] - len(r["creatures"])
            if free <= 0:
                continue
            destinations.append((
                f"{r['name']} ({free} of {r['slot_count']} slots free)",
                ("room", r["id"]),
            ))
        place_name = self.frame.state.get("village_name", "Village")
        destinations.append((place_name, ("village", None)))

        labels = [d[0] for d in destinations]
        with wx.SingleChoiceDialog(
            self,
            f"Where should {cat['name']} the {species_label} go?",
            f"Move {cat['name']}",
            labels,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            idx = dlg.GetSelection()
        kind, target_id = destinations[idx][1]

        if kind == "room":
            moved, reason = move_creature_to_room(
                self.frame.state, self.room_id, target_id, cat["id"]
            )
            if moved is None:
                self._show_move_refusal(cat, reason)
                return
            dest_name = next(
                (r["name"] for r in self.frame.state["rooms"] if r["id"] == target_id),
                target_id,
            )
            play_sound("arrival")
            self.frame.announce_event(
                "creature_moved", name=moved["name"], room_name=dest_name,
            )
        else:
            moved, reason = move_creature_to_village(
                self.frame.state, self.room_id, cat["id"]
            )
            if moved is None:
                self._show_move_refusal(cat, reason)
                return
            play_sound("arrival")
            self.frame.announce_event(
                "creature_to_village", name=moved["name"],
            )
        self.frame.save_and_refresh()

    def _show_move_refusal(self, cat, reason):
        """Translate the move_*-functions' reason codes into a friendly
        message. is_dependent gets the most-helpful redirect ("move
        the mother instead") since that's the new behavior players are
        most likely to bump into.
        """
        if reason == "is_dependent":
            mother_id = cat.get("dependent_on")
            mother = find_creature_by_id(self.frame.state, mother_id) if mother_id else None
            mother_name = mother["name"] if mother else "their mother"
            wx.MessageBox(
                f"{cat['name']} is still dependent on {mother_name} and "
                f"can't be moved alone yet. Move {mother_name} instead — "
                f"{cat['name']} (and any other babies) will come along.",
                "Still dependent", wx.OK | wx.ICON_INFORMATION, self,
            )
        elif reason == "dest_full":
            wx.MessageBox(
                "That room doesn't have enough free slots — if "
                f"{cat['name']} has dependent babies, they need to "
                "move together.",
                "Move failed", wx.OK | wx.ICON_WARNING, self,
            )
        else:
            wx.MessageBox(
                "That room couldn't take them — it may have just filled up.",
                "Move failed", wx.OK | wx.ICON_WARNING, self,
            )

    def on_expand_room(self, evt):
        room = self.get_room()
        with ExpandSlotDialog(self, room) as dlg:
            dlg.ShowModal()

    def on_refill(self, meter):
        refill_meter(self.frame.state, self.room_id, meter)
        play_sound("care")
        verb = "Cleaned" if meter == "litter" else "Refilled"
        self.frame.announce_event("meter_refilled", verb=verb, meter=meter)
        self.frame.save_and_refresh()

    def on_refill_all(self, evt):
        # Top up every meter the room has, regardless of which species /
        # room type defined them. One sound, one announcement — the per-
        # meter sequence would flood NVDA with three near-identical lines.
        room = self.get_room()
        for meter_key in list(room["meters"].keys()):
            refill_meter(self.frame.state, self.room_id, meter_key)
        play_sound("care")
        self.frame.announce_event("meters_all_refilled", room_name=room["name"])
        self.frame.save_and_refresh()

    def on_pet_all(self, evt):
        # Pet every creature in the room in one click. Empty room → friendly
        # explanation rather than a silent no-op (keeps the user oriented).
        room = self.get_room()
        creatures = list(room.get("creatures") or [])
        if not creatures:
            self.frame.announce_event("pet_everyone_empty", room_name=room["name"])
            return
        for cat in creatures:
            pet_cat(self.frame.state, self.room_id, cat["id"])
        play_sound("pet")
        _, plural = self._species_labels()
        self.frame.announce_event(
            "pet_everyone_done",
            n=len(creatures), plural=plural, room_name=room["name"],
        )
        self.frame.save_and_refresh()

    def on_pet(self, evt):
        cat_id = self.selected_cat_id()
        species_label, _ = self._species_labels()
        if not cat_id:
            self.frame.announce_event("select_species", species_label=species_label)
            return
        cat = pet_cat(self.frame.state, self.room_id, cat_id)
        if cat:
            play_sound("pet")
            response = random_pet_response(cat.get("species", "cat"), cat["name"])
            affection_pct = int(cat["affection"] * 100)
            if response:
                self.frame.announce_event(
                    "pet_with_response",
                    response=response, affection_pct=affection_pct,
                )
            else:
                self.frame.announce_event(
                    "pet_no_response",
                    care=self._care_action_label(),
                    name=cat["name"],
                    affection_pct=affection_pct,
                )
            self.frame.save_and_refresh()

    def on_rename(self, evt):
        cat = self.selected_cat()
        if cat is None:
            self.frame.announce_event("select_creature")
            return
        species_id = cat.get("species", "cat")
        result = prompt_rename(self, cat["name"], cat["sex"], species_id)
        if result is None:
            return
        new_name, save_to_file = result
        old_name = rename_creature(self.frame.state, cat["id"], new_name)
        play_sound("care")
        if save_to_file and append_name_to_file(new_name, cat["sex"], species_id):
            spec = SPECIES_DATA.get(species_id, {}).get("spec", {})
            sex_word = (
                spec.get("sex_label_female", "female")
                if cat["sex"] == "F"
                else spec.get("sex_label_male", "male")
            )
            self.frame.announce_event(
                "creature_renamed_saved",
                old_name=old_name, new_name=new_name,
                sex_word=sex_word,
                species_word=spec.get("name", "creature").lower(),
            )
        else:
            self.frame.announce_event(
                "creature_renamed", old_name=old_name, new_name=new_name,
            )
        self.frame.save_and_refresh()

    def on_breed(self, evt):
        status, payload = attempt_breed(self.frame.state, self.room_id)
        # `payload` is an expecting record on "conceived", and None
        # for everything else. For gestation==0 species, the record's
        # due_at is now and we immediately call process_expecting to
        # place the babies as real creatures — same announcement
        # family the auto-breed pass uses for births.
        if status == "no_pairs":
            self.frame.announce_event("breed_no_pairs")
            play_sound("breed_fail")
        elif status == "all_young":
            self.frame.announce_event("breed_all_young")
            play_sound("breed_fail")
        elif status == "all_old":
            self.frame.announce_event("breed_all_old")
            play_sound("breed_fail")
        elif status == "still_bonding":
            bonding = closest_bonding_pair(self.frame.state, self.room_id)
            if bonding is not None:
                cat_a, cat_b, remaining = bonding
                self.frame.announce_event(
                    "breed_still_bonding",
                    cat_a_name=cat_a["name"],
                    cat_b_name=cat_b["name"],
                    remaining=format_duration(remaining),
                )
            else:
                # Defensive fallback — attempt_breed said still_bonding
                # but we somehow couldn't find a candidate. Treat as
                # the original no_pairs case so the player still hears
                # something rather than silence.
                self.frame.announce_event("breed_no_pairs")
            play_sound("breed_fail")
        elif status == "still_growing":
            growing = closest_growing_pair(self.frame.state, self.room_id)
            if growing is not None:
                cat_a, cat_b, remaining = growing
                self.frame.announce_event(
                    "breed_still_growing",
                    cat_a_name=cat_a["name"],
                    cat_b_name=cat_b["name"],
                    remaining=format_duration(remaining),
                )
            else:
                self.frame.announce_event("breed_no_pairs")
            play_sound("breed_fail")
        elif status == "all_resting":
            # Compute each resting pair's remaining cooldown so the
            # player knows how long to wait per pair, not just "later."
            # eligible_pairs already filters to M+F mature non-retired
            # pairs in this room — same set attempt_breed considered.
            # Cooldown is per-species; each pair's species comes from
            # any of its members.
            cooldowns = self.frame.state.get("last_breed_per_pair", {})
            now = time.time()
            resting = []
            room_obj = self.get_room()
            for pid in eligible_pairs(self.frame.state, self.room_id):
                pair_member = next(
                    (c for c in room_obj["creatures"] if c.get("pair_id") == pid),
                    None,
                )
                if pair_member is None:
                    continue
                sid = pair_member.get("species", "cat")
                pair_spec = SPECIES_DATA.get(sid, {}).get("spec", {})
                pair_cooldown = species_breed_cooldown_seconds(pair_spec)
                last = float(cooldowns.get(pid, 0) or 0)
                remaining = max(0, int((last + pair_cooldown) - now))
                resting.append((pid, remaining))
            litter_label = room_litter_label(self.get_room())
            if len(resting) == 1:
                pid, remaining = resting[0]
                self.frame.announce_event(
                    "breed_all_resting_one",
                    pair_id=pid,
                    litter_label=litter_label,
                    remaining=format_duration(remaining),
                )
            else:
                pairs_status = "; ".join(
                    f"pair {pid} ready in {format_duration(remaining)}"
                    for pid, remaining in resting
                ) + "."
                self.frame.announce_event(
                    "breed_all_resting_many",
                    litter_label=litter_label,
                    pairs_status=pairs_status,
                )
            play_sound("breed_fail")
        elif status == "low_care":
            self.frame.announce_event("breed_low_care")
            play_sound("breed_fail")
        elif status == "fail":
            self.frame.announce_event(
                "breed_no_litter",
                label=room_litter_label(self.get_room()),
            )
            play_sound("breed_fail")
        else:  # status == "conceived"
            record = payload
            spec = SPECIES_DATA.get(record.get("species", "cat"), {}).get("spec", {})
            now = time.time()
            gestation_remaining = max(0, int(record.get("due_at", 0) - now))
            play_sound("breed_success")
            if gestation_remaining > 0:
                # Real gestation period — announce "expecting"; the
                # babies will arrive later via process_expecting on a
                # future tick.
                species_word_plural = spec.get(
                    "name_plural", spec.get("name", "creature").lower() + "s",
                ).lower()
                room = self.get_room()
                self.frame.announce_event(
                    "breed_conceived",
                    pair_id=record["from_pair"],
                    room_name=room["name"],
                    litter_label=_spec_litter_label(spec),
                    species_word_plural=species_word_plural,
                    gestation=format_duration(gestation_remaining),
                )
            else:
                # gestation == 0 — process_expecting inline to place
                # the babies as real creatures right now, then
                # announce the placements.
                births = process_expecting(self.frame.state, now=now)
                self.frame._announce_births(births, offline=False)
        self.frame.save_and_refresh()

    def on_edit_room(self, evt):
        room = self.get_room()
        with EditRoomDialog(self, room) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            new_name = dlg.get_name()
            new_type = dlg.selected_type_id()
            new_allowed = dlg.selected_allowed_species()

        # The engine works out the plan (what changes, and which creatures
        # would have to move where) without touching anything. We confirm
        # the relocations with the player, then tell the engine to apply it.
        plan = plan_room_retype(
            self.frame.state, self.room_id,
            new_name=new_name, new_type=new_type, allowed_species=new_allowed,
        )
        status = plan.get("status")
        if status in ("no_change", "no_room"):
            return
        if status == "no_allowed_species":
            wx.MessageBox(
                "Please tick at least one species under 'Allowed species "
                "in this room', or this room won't accept any creatures.",
                "Edit room", wx.OK | wx.ICON_WARNING, self,
            )
            return

        # Walk the player through the relocation plan creature-by-creature
        # so they're not surprised — they see exactly what will move and why
        # before clicking yes.
        relocations = plan["relocations"]
        if relocations:
            target_type_spec = ROOM_TYPES.get(plan["target_type"], {})
            lines = []
            place_name = self.frame.state.get("village_name", "Village")
            for cat, target, reason in relocations:
                species_spec = SPECIES_DATA.get(
                    cat.get("species", "cat"), {}).get("spec", {})
                species_word = species_spec.get("name", "creature").lower()
                if target is not None:
                    lines.append(
                        f"  • {cat['name']} → {target['name']}"
                    )
                elif reason == PLACEMENT_VILLAGE_NO_ROOM:
                    lines.append(
                        f"  • {cat['name']} → {place_name} "
                        f"(no rooms allow {species_word})"
                    )
                else:
                    lines.append(
                        f"  • {cat['name']} → {place_name} "
                        f"(no rooms have a free slot for {species_word})"
                    )

            preamble = (
                f"Changing this room to {target_type_spec.get('name', plan['target_type'])} "
                "and the species you've allowed means some creatures "
                "have to move."
                if plan["type_changed"]
                else "With the species you've allowed, some creatures "
                     "have to move."
            )
            msg = (
                preamble + "\n\nHere's where they'll go:\n\n"
                + "\n".join(lines)
                + "\n\nContinue?"
            )
            with wx.MessageDialog(
                self, msg, "Move incompatible creatures?",
                wx.YES_NO | wx.ICON_QUESTION,
            ) as confirm:
                if confirm.ShowModal() != wx.ID_YES:
                    return

        moved_names = apply_room_retype(self.frame.state, self.room_id, plan)

        play_sound("care")
        # Build the composite announcement from individual events so the
        # modder can re-word each part (or blank one to drop it) in
        # announcements.txt.
        parts = []
        if plan["name_changed"]:
            parts.append(format_announcement(
                "room_edit_renamed",
                old_name=plan["old_name"], new_name=plan["new_name"],
            ))
        if plan["type_changed"]:
            label = ROOM_TYPES.get(plan["new_type"], {}).get("name", plan["new_type"])
            parts.append(format_announcement(
                "room_edit_type_changed", type_name=label,
            ))
        if plan["allowed_changed"] and not plan["type_changed"]:
            parts.append(format_announcement("room_edit_allowed_changed"))
        if moved_names:
            parts.append(format_announcement(
                "room_edit_creatures_moved", names=join_names(moved_names),
                village_name=self.frame.state.get("village_name", "Village"),
            ))
        parts = [p for p in parts if p]
        if not parts:
            return  # nothing actually announced/saved (shouldn't happen)
        self.frame.announce(" ".join(parts))
        save_state(self.frame.state)
        if plan["type_changed"]:
            self.frame.rebuild_room_tab(self.room_id)
        else:
            self.frame.save_and_refresh()



class VillagePanel(wx.Panel):
    """The village — where gifted babies and released cats go to live.

    Read-only listing plus two actions: bring a cat back home (uses a free
    slot in any room) and rename a cat. No care meters; no breeding here.
    """

    def __init__(self, parent, frame):
        super().__init__(parent)
        self.frame = frame
        self._cat_ids = []
        self._filter_keys = ["all"]
        self._build()
        self.refresh()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Intro / cats-box label / villager noun all key off the
        # player-renameable village name. Stored on the panel so the
        # rename action can refresh them in place.
        self._intro_ctrl = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 56),
        )
        self._intro_ctrl.SetName("Village intro")
        sizer.Add(self._intro_ctrl, 0, wx.ALL | wx.EXPAND, 8)

        self._cats_box = wx.StaticBox(self, label="Villagers")
        cats_sizer = wx.StaticBoxSizer(self._cats_box, wx.VERTICAL)

        filter_row = wx.BoxSizer(wx.HORIZONTAL)
        filter_row.Add(
            wx.StaticText(self, label="Show:"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.species_filter = wx.Choice(self, choices=[])
        self.species_filter.SetName("Filter by species")
        self.species_filter.Bind(wx.EVT_CHOICE, self.on_filter_change)
        filter_row.Add(self.species_filter, 1)
        cats_sizer.Add(filter_row, 0, wx.ALL | wx.EXPAND, 4)

        self.cats_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.cats_list.SetName("Villagers")
        for col_label, w in [
            ("Name", 140), ("Species", 90), ("Sex", 60),
            ("Age", 130), ("In village for", 140),
        ]:
            self.cats_list.AppendColumn(col_label, width=w)
        self.cats_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_cat_selected)
        self.cats_list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.on_cat_selected)
        cats_sizer.Add(self.cats_list, 1, wx.ALL | wx.EXPAND, 4)

        self.cat_detail = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value="Select a creature to see their description.",
            size=(-1, 56),
        )
        self.cat_detail.SetName("Selected creature description")
        cats_sizer.Add(self.cat_detail, 0, wx.ALL | wx.EXPAND, 4)

        actions = wx.BoxSizer(wx.HORIZONTAL)
        self.bring_back_btn = wx.Button(self, label="Bring back home")
        self.bring_back_btn.Bind(wx.EVT_BUTTON, self.on_bring_back)
        rename_btn = wx.Button(self, label="Rename selected creature")
        rename_btn.Bind(wx.EVT_BUTTON, self.on_rename)
        add_villager_btn = wx.Button(self, label="Add a villager…")
        add_villager_btn.Bind(wx.EVT_BUTTON, self.on_add_villager)
        rename_place_btn = wx.Button(self, label="Rename this place…")
        rename_place_btn.SetToolTip(
            "Give the village a different name (Sanctuary, Home, "
            "Refuge — whatever fits). The 'Go to' picker updates "
            "immediately."
        )
        rename_place_btn.Bind(wx.EVT_BUTTON, self.on_rename_place)
        actions.Add(self.bring_back_btn, 0, wx.RIGHT, 8)
        actions.Add(rename_btn, 0, wx.RIGHT, 8)
        actions.Add(add_villager_btn, 0, wx.RIGHT, 8)
        actions.Add(rename_place_btn, 0)
        cats_sizer.Add(actions, 0, wx.ALL, 4)

        sizer.Add(cats_sizer, 1, wx.ALL | wx.EXPAND, 8)

        self.summary_text = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 28),
        )
        self.summary_text.SetName("Village summary")
        sizer.Add(self.summary_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 8)

        self.SetSizer(sizer)

    def refresh(self):
        village = self.frame.state.get("village", [])
        species_present = sorted({
            c.get("species") for c in village if c.get("species")
        })

        prev_filter = self._current_filter_key()
        prev_sel_id = self.selected_cat_id()

        # Rebuild the filter dropdown options based on what's actually in
        # the village right now. "All" is always first.
        self.species_filter.Clear()
        self._filter_keys = ["all"]
        self.species_filter.Append(f"All species ({len(village)})", "all")
        for sid in species_present:
            spec = SPECIES_DATA.get(sid, {}).get("spec", {})
            plural = spec.get("name_plural", spec.get("name", sid) + "s")
            count = sum(1 for c in village if c.get("species") == sid)
            self.species_filter.Append(f"{plural} ({count})", sid)
            self._filter_keys.append(sid)

        # Restore previous filter if it still exists.
        if prev_filter in self._filter_keys:
            self.species_filter.SetSelection(self._filter_keys.index(prev_filter))
        else:
            self.species_filter.SetSelection(0)

        self._refill_list(village, prev_sel_id)

        any_room_free = any(
            len(r["creatures"]) < r["slot_count"] for r in self.frame.state["rooms"]
        )
        self.bring_back_btn.Enable(bool(village) and any_room_free)
        place_name = self.frame.state.get("village_name", "Village")
        self.summary_text.ChangeValue(
            f"{place_name}: {len(village)} resident{'s' if len(village) != 1 else ''}."
        )
        self.refresh_intro_and_title()
        self.refresh_cat_detail()

    def refresh_intro_and_title(self):
        """Re-render the intro paragraph and the cats-box header from the
        current village_name. Called by refresh() and after a rename
        completes so NVDA hears the new name immediately.
        """
        place_name = self.frame.state.get("village_name", "Village")
        intro_text = (
            f"Creatures living in {place_name}. They're well looked "
            "after. Use the Show menu to filter by species. You can "
            "bring one back home if there's a free slot in a "
            "compatible room."
        )
        if self._intro_ctrl.GetValue() != intro_text:
            self._intro_ctrl.ChangeValue(intro_text)
        self._cats_box.SetLabel(f"Residents of {place_name}")

    def _refill_list(self, village, prev_sel_id):
        filter_key = self._current_filter_key()
        if filter_key == "all" or filter_key is None:
            filtered = village
        else:
            filtered = [c for c in village if c.get("species") == filter_key]

        self.cats_list.DeleteAllItems()
        self._cat_ids = []
        new_sel_row = -1
        now = time.time()
        for i, cat in enumerate(filtered):
            row = self.cats_list.InsertItem(i, cat["name"])
            sid = cat.get("species", "cat")
            spec = SPECIES_DATA.get(sid, {}).get("spec", {})
            self.cats_list.SetItem(row, 1, spec.get("name", sid))
            self.cats_list.SetItem(row, 2, cat["sex"])
            self.cats_list.SetItem(row, 3, format_age_for_list(cat_age_seconds(cat)))
            moved_at = cat.get("moved_to_village_at")
            in_village_secs = max(0, int(now - moved_at)) if moved_at else 0
            self.cats_list.SetItem(
                row, 4,
                format_age_for_list(in_village_secs, under_minute_label="just arrived")
                if moved_at else "—",
            )
            self._cat_ids.append(cat["id"])
            if cat["id"] == prev_sel_id:
                new_sel_row = i
        if new_sel_row >= 0:
            self.cats_list.Select(new_sel_row)
            self.cats_list.Focus(new_sel_row)

    def refresh_ages(self):
        """Tick-driven: update Age and In-village cells when their
        bucket-formatted value has changed. See RoomPanel.refresh_ages
        for the rationale.
        """
        by_id = {c["id"]: c for c in self.frame.state.get("village", [])}
        now = time.time()
        for row, cat_id in enumerate(self._cat_ids):
            cat = by_id.get(cat_id)
            if cat is None:
                continue
            new_age = format_age_for_list(cat_age_seconds(cat))
            if self.cats_list.GetItemText(row, 3) != new_age:
                self.cats_list.SetItem(row, 3, new_age)
            moved_at = cat.get("moved_to_village_at")
            new_in_village = (
                format_age_for_list(max(0, int(now - moved_at)),
                                    under_minute_label="just arrived")
                if moved_at else "—"
            )
            if self.cats_list.GetItemText(row, 4) != new_in_village:
                self.cats_list.SetItem(row, 4, new_in_village)

    def _current_filter_key(self):
        sel = self.species_filter.GetSelection()
        if sel < 0 or sel >= len(self._filter_keys):
            return None
        return self._filter_keys[sel]

    def refresh_cat_detail(self, announce=True):
        cat = self.selected_cat()
        if cat is None:
            text = "Select a creature to see their description."
        else:
            text = f"{cat['name']}: {cat_full_description(cat)}"
            text += _status_line_for(cat)
        if self.cat_detail.GetValue() != text:
            if announce:
                self.cat_detail.SetValue(text)
            else:
                self.cat_detail.ChangeValue(text)

    def selected_cat_id(self):
        sel = self.cats_list.GetFirstSelected()
        if sel < 0 or sel >= len(self._cat_ids):
            return None
        return self._cat_ids[sel]

    def selected_cat(self):
        cat_id = self.selected_cat_id()
        if not cat_id:
            return None
        return next(
            (c for c in self.frame.state.get("village", []) if c["id"] == cat_id),
            None,
        )

    def on_cat_selected(self, evt):
        self.refresh_cat_detail()
        evt.Skip()

    def on_filter_change(self, evt):
        # Re-filter the list using current state; preserve selected creature
        # if they're still visible under the new filter.
        prev_sel_id = self.selected_cat_id()
        self._refill_list(self.frame.state.get("village", []), prev_sel_id)
        self.refresh_cat_detail()

    def on_bring_back(self, evt):
        cat = self.selected_cat()
        if cat is None:
            self.frame.announce_event("select_creature")
            return
        species_id = cat.get("species", "cat")
        species_label = SPECIES_DATA.get(species_id, {}).get("spec", {}).get("name", species_id).lower()
        # Find the first room that accepts this species AND has a free slot.
        target_room = None
        any_compatible = False
        for r in self.frame.state["rooms"]:
            if species_id not in (r.get("allowed_species") or []):
                continue
            any_compatible = True
            if len(r["creatures"]) < r["slot_count"]:
                target_room = r
                break
        if target_room is None:
            if not any_compatible:
                msg = (
                    f"There's no room that takes a {species_label} yet. "
                    "Build one from the Park section first ('Go to' → Park)."
                )
            else:
                msg = (
                    f"All {species_label}-compatible rooms are full. "
                    "Make space (or build another) before bringing them home."
                )
            wx.MessageBox(msg, "No room ready", wx.OK | wx.ICON_INFORMATION, self)
            return
        self.frame.state["village"] = [
            c for c in self.frame.state["village"] if c["id"] != cat["id"]
        ]
        cat.pop("moved_to_village_at", None)
        cat["pair_id"] = None
        target_room["creatures"].append(cat)
        play_sound("arrival")
        self.frame.announce_event(
            "creature_came_home",
            name=cat["name"], room_name=target_room["name"],
        )
        self.frame.save_and_refresh()

    def on_rename(self, evt):
        cat = self.selected_cat()
        if cat is None:
            self.frame.announce_event("select_creature")
            return
        species_id = cat.get("species", "cat")
        result = prompt_rename(self, cat["name"], cat["sex"], species_id)
        if result is None:
            return
        new_name, save_to_file = result
        old_name = rename_creature(self.frame.state, cat["id"], new_name)
        play_sound("care")
        if save_to_file and append_name_to_file(new_name, cat["sex"], species_id):
            spec = SPECIES_DATA.get(species_id, {}).get("spec", {})
            sex_word = (
                spec.get("sex_label_female", "female")
                if cat["sex"] == "F"
                else spec.get("sex_label_male", "male")
            )
            self.frame.announce_event(
                "creature_renamed_saved",
                old_name=old_name, new_name=new_name,
                sex_word=sex_word,
                species_word=spec.get("name", "creature").lower(),
            )
        else:
            self.frame.announce_event(
                "creature_renamed", old_name=old_name, new_name=new_name,
            )
        self.frame.save_and_refresh()

    def on_rename_place(self, evt):
        """Player-renames the village. Saves to state, updates the
        notebook tab title, refreshes the panel intro / cats-box label
        immediately so NVDA hears the new name.
        """
        current = self.frame.state.get("village_name", "Village")
        with wx.TextEntryDialog(
            self,
            "What would you like to call this place? "
            "(Examples: Village, Sanctuary, Home, Refuge.)",
            "Rename this place",
            current,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            new_name = dlg.GetValue().strip()
        if not new_name:
            wx.MessageBox(
                "Please enter a name (or click Cancel to keep the "
                "current one).",
                "Rename this place", wx.OK | wx.ICON_WARNING, self,
            )
            return
        if new_name == current:
            return
        self.frame.state["village_name"] = new_name
        self.frame.set_village_tab_title(new_name)
        self.refresh_intro_and_title()
        self.frame.announce_event(
            "village_renamed", old_name=current, new_name=new_name,
        )
        save_state(self.frame.state)

    def on_add_villager(self, evt):
        if not SPECIES_DATA:
            wx.MessageBox(
                "There are no species defined yet. Use File → Species to "
                "pick one from the library or design a new one first.",
                "Add a villager", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        with AddVillagerDialog(self) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            species_id = dlg.species_id
            count = dlg.count
            sexes = dlg.sexes
        now = time.time()
        added_names = []
        # add_villager (engine) spawns a truly newborn creature -- age 0 with
        # mature_at set from the species' breeding age -- matching the other
        # seed paths. One call per requested sex.
        for sex in sexes[:count]:
            villager = add_villager(self.frame.state, species_id, sex, now=now)
            if villager is not None:
                added_names.append(villager["name"])
        spec = SPECIES_DATA.get(species_id, {}).get("spec", {})
        species_word = (spec.get("name_plural") if count != 1 else spec.get("name")) or species_id
        play_sound("care")
        self.frame.announce_event(
            "village_villagers_added",
            count=count,
            species_word=species_word.lower(),
            names=", ".join(added_names),
        )
        self.frame.save_and_refresh()


class StatsPanel(wx.Panel):
    """Read-only overview of your world: totals, per-species counts,
    per-room counts, and a per-creature lineage lookup.

    Pure aggregation; no actions, no state mutation. Refreshes every time
    the user switches to this section (via MainFrame._select_book_page).
    """

    def __init__(self, parent, frame):
        super().__init__(parent)
        self.frame = frame
        self._lookup_ids = []
        self._build()
        self.refresh()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=(
                "A snapshot of your world. Use the Family tree picker at the "
                "bottom to look up a single creature's parents, partner, "
                "siblings, and offspring."
            ),
            size=(-1, 56),
        )
        intro.SetName("Stats intro")
        sizer.Add(intro, 0, wx.ALL | wx.EXPAND, 8)

        self.overview_text = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 110),
        )
        self.overview_text.SetName("Overview")
        sizer.Add(self.overview_text, 0, wx.ALL | wx.EXPAND, 8)

        self.species_text = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 140),
        )
        self.species_text.SetName("By species")
        sizer.Add(self.species_text, 0, wx.ALL | wx.EXPAND, 8)

        self.rooms_text = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 140),
        )
        self.rooms_text.SetName("By room")
        sizer.Add(self.rooms_text, 0, wx.ALL | wx.EXPAND, 8)

        # Memorials — every creature that's left for the wild, kept here
        # forever. Read-only and additive, never pruned by the engine.
        # Cozy framing: leaving for the wild isn't deletion, it's a
        # creature whose life moved on past you, and you remember them.
        self.memorial_text = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 140),
        )
        self.memorial_text.SetName("In memory")
        sizer.Add(self.memorial_text, 0, wx.ALL | wx.EXPAND, 8)

        # Family-tree section: a Choice with every creature, plus a read-only
        # text box showing parents / partner / siblings / offspring.
        tree_box = wx.StaticBox(self, label="Family tree")
        tree_sizer = wx.StaticBoxSizer(tree_box, wx.VERTICAL)

        pick_row = wx.BoxSizer(wx.HORIZONTAL)
        pick_row.Add(
            wx.StaticText(self, label="Look up:"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.lookup_choice = wx.Choice(self, choices=[])
        self.lookup_choice.SetName("Look up creature")
        self.lookup_choice.Bind(wx.EVT_CHOICE, self.on_lookup_change)
        pick_row.Add(self.lookup_choice, 1)
        tree_sizer.Add(pick_row, 0, wx.ALL | wx.EXPAND, 4)

        self.lineage_text = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.BORDER_SIMPLE,
            value="Pick a creature above to see their family tree.",
            size=(-1, 180),
        )
        self.lineage_text.SetName("Family tree details")
        tree_sizer.Add(self.lineage_text, 1, wx.ALL | wx.EXPAND, 4)

        sizer.Add(tree_sizer, 1, wx.ALL | wx.EXPAND, 8)

        self.SetSizer(sizer)

    def refresh(self):
        state = self.frame.state

        rooms = state.get("rooms", [])
        village = state.get("village", [])
        expecting = state.get("expecting", [])

        total_in_rooms = sum(len(r.get("creatures", [])) for r in rooms)
        total_in_village = len(village)
        overall = total_in_rooms + total_in_village
        inv = state.get("inventory", {})
        commons = sum(inv.get("common", {}).values())
        uncommons = sum(inv.get("uncommon", {}).values())
        objects_n = total_collectible_count(inv.get("objects"))
        treasures_n = total_collectible_count(inv.get("treasures"))

        place_name = state.get("village_name", "Village")
        overview_lines = [
            f"Total creatures: {overall} ({total_in_rooms} in {len(rooms)} "
            f"room{'s' if len(rooms) != 1 else ''}, {total_in_village} in {place_name}).",
            f"Pairs expecting: {len(expecting)}.",
            f"Inventory: {commons} common item{'s' if commons != 1 else ''}, "
            f"{uncommons} uncommon, {objects_n} object{'s' if objects_n != 1 else ''}, "
            f"{treasures_n} treasure{'s' if treasures_n != 1 else ''}.",
        ]
        self.overview_text.SetValue("\n".join(overview_lines))

        # Per-species breakdown
        from collections import defaultdict
        per_species = defaultdict(lambda: {"rooms": 0, "village": 0})
        for room in rooms:
            for c in room.get("creatures", []):
                per_species[c.get("species", "?")]["rooms"] += 1
        for c in village:
            per_species[c.get("species", "?")]["village"] += 1

        if per_species:
            species_lines = []
            for sid in sorted(per_species.keys()):
                spec = SPECIES_DATA.get(sid, {}).get("spec", {})
                plural = spec.get("name_plural", sid + "s")
                cnt = per_species[sid]
                total = cnt["rooms"] + cnt["village"]
                species_lines.append(
                    f"{plural}: {total} total — {cnt['rooms']} in rooms, "
                    f"{cnt['village']} in {place_name}."
                )
            self.species_text.SetValue("\n".join(species_lines))
        else:
            self.species_text.SetValue("No creatures yet.")

        # Per-room breakdown
        if rooms:
            room_lines = []
            for room in rooms:
                type_spec = ROOM_TYPES.get(room.get("type", "indoor"), {})
                type_name = type_spec.get("name", room.get("type", "?"))
                used = len(room.get("creatures", []))
                cap = room.get("slot_count", 0)
                # Species mix in this room
                species_in = defaultdict(int)
                for c in room.get("creatures", []):
                    species_in[c.get("species", "?")] += 1
                if species_in:
                    mix = ", ".join(f"{n} {s}" for s, n in sorted(species_in.items()))
                    room_lines.append(
                        f"{room['name']} ({type_name}): {used}/{cap} slots — {mix}."
                    )
                else:
                    room_lines.append(
                        f"{room['name']} ({type_name}): empty ({used}/{cap})."
                    )
            self.rooms_text.SetValue("\n".join(room_lines))
        else:
            self.rooms_text.SetValue("No rooms yet.")

        # Memorials — newest first so a recent loss is the top line.
        # Each entry shows name, species, and how long ago they left;
        # the lifespan-at-leaving is included when known so the player
        # can see a "lived 3 days, 4 hours" remembrance.
        remembered = state.get("remembered", [])
        if remembered:
            now = time.time()
            mem_lines = ["In memory — creatures who left for the wild:"]
            for entry in reversed(remembered):
                species_id = entry.get("species", "")
                spec = SPECIES_DATA.get(species_id, {}).get("spec", {})
                species_word = spec.get("name", species_id).lower()
                ago_seconds = max(0, int(now - entry.get("left_at", now)))
                ago_text = format_duration_human(ago_seconds) or "moments"
                lifespan = int(entry.get("age_seconds_at_leaving", 0) or 0)
                if lifespan > 0:
                    life_text = format_duration_human(lifespan) or "a brief life"
                    mem_lines.append(
                        f"  {entry.get('name', '?')} ({species_word}) — "
                        f"left {ago_text} ago, lived {life_text}."
                    )
                else:
                    mem_lines.append(
                        f"  {entry.get('name', '?')} ({species_word}) — "
                        f"left {ago_text} ago."
                    )
            self.memorial_text.SetValue("\n".join(mem_lines))
        else:
            self.memorial_text.SetValue(
                "In memory — creatures who left for the wild:\n"
                "  (no one yet — none of your creatures have gone to the wild)"
            )

        # Family-tree picker — list every creature, ordered by species then name.
        prev_id = self._selected_lookup_id()
        creatures = sorted(
            list(all_creatures(state)),
            key=lambda c: (c.get("species", ""), c.get("name", "")),
        )
        self.lookup_choice.Clear()
        self._lookup_ids = []
        for c in creatures:
            spec = SPECIES_DATA.get(c.get("species", "cat"), {}).get("spec", {})
            label = f"{spec.get('name', c.get('species', '?'))}: {c.get('name', '?')}"
            self.lookup_choice.Append(label, c.get("id"))
            self._lookup_ids.append(c.get("id"))
        if prev_id in self._lookup_ids:
            self.lookup_choice.SetSelection(self._lookup_ids.index(prev_id))
        elif self._lookup_ids:
            self.lookup_choice.SetSelection(0)
        self._refresh_lineage()

    def _selected_lookup_id(self):
        sel = self.lookup_choice.GetSelection()
        if sel < 0 or sel >= len(self._lookup_ids):
            return None
        return self._lookup_ids[sel]

    def _refresh_lineage(self):
        cat_id = self._selected_lookup_id()
        if not cat_id:
            self.lineage_text.SetValue("Pick a creature above to see their family tree.")
            return
        state = self.frame.state
        cat = find_creature_by_id(state, cat_id)
        if cat is None:
            self.lineage_text.SetValue("(creature missing)")
            return

        spec = SPECIES_DATA.get(cat.get("species", "cat"), {}).get("spec", {})
        species_word = spec.get("name", cat.get("species", "creature")).lower()
        sex_word = (
            spec.get("sex_label_female", "female")
            if cat.get("sex") == "F"
            else spec.get("sex_label_male", "male")
        )
        location = creature_location(state, cat_id)

        age_text = format_duration_human(int(cat_age_seconds(cat))) or "less than a minute"
        lines = [
            f"{cat.get('name', '?')} — {sex_word} {species_word}, "
            f"age {age_text}, in {location}."
        ]

        mother, father = find_parents_of(state, cat)
        if mother or father:
            parts = []
            if mother:
                parts.append(f"{mother.get('name', '?')} (mother)")
            if father:
                parts.append(f"{father.get('name', '?')} (father)")
            lines.append("Parents: " + ", ".join(parts) + ".")
        else:
            lines.append("Parents: not known (or starter / village-born).")

        partner = find_partner_of(state, cat)
        if partner:
            lines.append(f"Currently paired with {partner.get('name', '?')}.")
        else:
            lines.append("Currently single.")

        siblings = find_siblings_of(state, cat)
        if siblings:
            lines.append(
                "Siblings: "
                + ", ".join(s.get("name", "?") for s in siblings)
                + "."
            )
        else:
            lines.append("Siblings: none recorded.")

        offspring = find_offspring_of(state, cat)
        if offspring:
            lines.append(
                f"Offspring ({len(offspring)}): "
                + ", ".join(c.get("name", "?") for c in offspring)
                + "."
            )
        else:
            lines.append("Offspring: none yet.")

        self.lineage_text.SetValue("\n".join(lines))

    def on_lookup_change(self, evt):
        self._refresh_lineage()


class ParkPanel(wx.Panel):
    """The Park — dig for items and build new rooms.

    Limited daily digs (settings: digs_per_day, default 5). Each dig rolls
    against DIG_OUTCOMES weights. Common/uncommon items stack as counts;
    objects and treasures are individual entries with descriptions.
    """

    def __init__(self, parent, frame):
        super().__init__(parent)
        self.frame = frame
        self._build()
        self.refresh()

    def _build(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value=(
                "Dig in the park to find sticks, stones, and other bits. "
                "Sometimes you'll find an object or a real treasure. "
                "Use what you find to build new rooms."
            ),
            size=(-1, 48),
        )
        intro.SetName("Park intro")
        sizer.Add(intro, 0, wx.ALL | wx.EXPAND, 8)

        self.digs_status = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 28),
        )
        self.digs_status.SetName("Digs remaining")
        sizer.Add(self.digs_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 8)

        dig_row = wx.BoxSizer(wx.HORIZONTAL)
        self.dig_btn = wx.Button(self, label="Dig here")
        self.dig_btn.Bind(wx.EVT_BUTTON, self.on_dig)
        # Native wx.Button on Windows activates on Enter key-down (so holding
        # Enter auto-repeats digs via OS key-repeat) but on Space key-up (so
        # holding Space does NOT repeat). Intercept Space key-down so it
        # behaves the same as Enter — players asked for both keys to repeat.
        self.dig_btn.Bind(wx.EVT_KEY_DOWN, self.on_dig_key_down)
        dig_row.Add(self.dig_btn, 0)
        sizer.Add(dig_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # Inventory section
        inv_box = wx.StaticBox(self, label="Inventory")
        inv_sizer = wx.StaticBoxSizer(inv_box, wx.VERTICAL)

        self.common_text = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 28),
        )
        self.common_text.SetName("Common items")
        inv_sizer.Add(self.common_text, 0, wx.ALL | wx.EXPAND, 4)

        self.uncommon_text = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 28),
        )
        self.uncommon_text.SetName("Uncommon items")
        inv_sizer.Add(self.uncommon_text, 0, wx.ALL | wx.EXPAND, 4)

        objects_label = wx.StaticText(self, label="Objects:")
        inv_sizer.Add(objects_label, 0, wx.LEFT | wx.TOP, 4)
        self.objects_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.objects_list.SetName("Objects collected")
        self.objects_list.AppendColumn("Name", width=180)
        self.objects_list.AppendColumn("Count", width=60)
        self.objects_list.AppendColumn("Description", width=380)
        inv_sizer.Add(self.objects_list, 1, wx.ALL | wx.EXPAND, 4)

        treasures_label = wx.StaticText(self, label="Treasures:")
        inv_sizer.Add(treasures_label, 0, wx.LEFT | wx.TOP, 4)
        self.treasures_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.treasures_list.SetName("Treasures collected")
        self.treasures_list.AppendColumn("Name", width=180)
        self.treasures_list.AppendColumn("Count", width=60)
        self.treasures_list.AppendColumn("Description", width=380)
        inv_sizer.Add(self.treasures_list, 1, wx.ALL | wx.EXPAND, 4)

        sizer.Add(inv_sizer, 1, wx.ALL | wx.EXPAND, 8)

        # Build room
        build_box = wx.StaticBox(self, label="Build")
        build_sizer = wx.StaticBoxSizer(build_box, wx.VERTICAL)
        self.build_status = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.BORDER_SIMPLE,
            value="",
            size=(-1, 28),
        )
        self.build_status.SetName("Build status")
        build_sizer.Add(self.build_status, 0, wx.ALL | wx.EXPAND, 4)
        self.build_btn = wx.Button(self, label="Build a new room")
        self.build_btn.Bind(wx.EVT_BUTTON, self.on_build_room)
        build_sizer.Add(self.build_btn, 0, wx.ALL, 4)
        sizer.Add(build_sizer, 0, wx.ALL | wx.EXPAND, 8)

        self.SetSizer(sizer)

    def refresh(self):
        state = self.frame.state
        reset_digs_if_new_day(state)
        remaining = digs_remaining(state)
        self.digs_status.ChangeValue(
            f"Digs remaining today: {remaining} of {SETTINGS['digs_per_day']}."
        )
        self.dig_btn.Enable(remaining > 0)

        inv = state.get("inventory", {})
        self.common_text.ChangeValue(self._format_counts("Common items", inv.get("common", {})))
        self.uncommon_text.ChangeValue(self._format_counts("Uncommon items", inv.get("uncommon", {})))

        # Objects and treasures stack by name now; iterate the dict in
        # alphabetical order so the list reads predictably to NVDA.
        self.objects_list.DeleteAllItems()
        for i, (name, entry) in enumerate(sorted(inv.get("objects", {}).items())):
            self.objects_list.InsertItem(i, name)
            self.objects_list.SetItem(i, 1, str(entry.get("count", 0)))
            self.objects_list.SetItem(i, 2, entry.get("description", ""))

        self.treasures_list.DeleteAllItems()
        for i, (name, entry) in enumerate(sorted(inv.get("treasures", {}).items())):
            self.treasures_list.InsertItem(i, name)
            self.treasures_list.SetItem(i, 1, str(entry.get("count", 0)))
            self.treasures_list.SetItem(i, 2, entry.get("description", ""))

        affordable = []
        for tid, spec in ROOM_TYPES.items():
            recipe = get_room_recipe(spec)
            if not recipe_shortfall(state, recipe, type_spec=spec):
                affordable.append(spec.get("name", tid))
        if affordable:
            status_text = (
                "You can build: " + ", ".join(affordable) + "."
            )
        elif ROOM_TYPES:
            status_text = "No room types are buildable yet — keep digging in the park!"
        else:
            status_text = "No room types are loaded."
        self.build_status.ChangeValue(status_text)
        self.build_btn.Enable(bool(affordable))

    @staticmethod
    def _format_counts(label, counts):
        if not counts:
            return f"{label}: none yet."
        parts = [f"{name} ({n})" for name, n in sorted(counts.items())]
        return f"{label}: " + ", ".join(parts) + "."

    def on_dig_key_down(self, evt):
        if evt.GetKeyCode() == wx.WXK_SPACE and self.dig_btn.IsEnabled():
            # Don't Skip() — eating the event prevents the button's default
            # Space-on-key-up handler from firing one extra dig on release.
            self.on_dig(None)
            return
        evt.Skip()

    def on_dig(self, evt):
        result = do_dig(self.frame.state)
        kind = result["kind"]
        if kind == "no_digs_left":
            self.frame.announce_event("dig_no_left")
            play_sound("breed_fail")
        elif kind == "nothing":
            play_sound("care")
            self.frame.announce_event("dig_nothing")
        elif kind == "item":
            play_sound("care")
            article = "an" if result["name"][:1].lower() in "aeiou" else "a"
            self.frame.announce_event("dig_item", article=article, name=result["name"])
        elif kind == "object":
            play_sound("arrival")
            self.frame.announce_event(
                "dig_object",
                name=result["name"],
                description=result["description"],
            )
        elif kind == "treasure":
            play_sound("breed_success")
            self.frame.announce_event(
                "dig_treasure",
                name=result["name"],
                description=result["description"],
            )
        self.frame.save_and_refresh()

    def on_build_room(self, evt):
        with BuildRoomDialog(self) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            new_name = dlg.get_room_name()
            type_id = dlg.selected_type_id()
            starter_species = dlg.selected_species_id()
            add_starters = dlg.add_starter_pairs()
            allowed_species = dlg.selected_allowed_species()
            treasure_name = dlg.selected_treasure_name()
        if not type_id:
            self.frame.announce_event("build_no_type")
            return
        # If the chosen type wants a treasure but the player has none
        # selected (or had none to choose), short-circuit with a friendly
        # warning rather than silently failing in build_new_room.
        type_spec = ROOM_TYPES.get(type_id, {})
        if get_treasure_cost(type_spec) > 0 and not treasure_name:
            wx.MessageBox(
                f"{type_spec.get('name', type_id)} needs a treasure to build, "
                "and you don't have one yet. Dig in the Park to find one.",
                "Build a new room", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        # Type's full compat is the upper bound. If the player ticked
        # nothing, fall back to the type's full list rather than letting
        # them lock the room down to nothing on accident.
        type_compat = room_type_compatible_species(type_id)
        if not allowed_species and type_compat:
            wx.MessageBox(
                "Please tick at least one species under 'Allowed species "
                "in this room', or this room won't accept any creatures.",
                "Build a new room", wx.OK | wx.ICON_WARNING, self,
            )
            return
        room, taken = build_new_room(
            self.frame.state, type_id, new_name, starter_species, add_starters,
            allowed_species=allowed_species, treasure_name=treasure_name,
        )
        if room is None:
            type_spec = ROOM_TYPES.get(type_id, {})
            recipe = get_room_recipe(type_spec)
            missing = recipe_shortfall(self.frame.state, recipe, type_spec=type_spec)
            type_name = type_spec.get("name", type_id)
            if missing:
                self.frame.announce_event(
                    "build_failed_missing",
                    type_name=type_name,
                    reason=format_shortfall(missing) + ".",
                )
            else:
                self.frame.announce_event("build_failed", type_name=type_name)
            return
        play_sound("breed_success")
        used = ", ".join(f"{n} {pluralize(name, n)}" for name, n in taken.items())
        self.frame.announce_event(
            "build_success", room_name=room["name"], used=used,
        )
        self.frame.add_room_tab(room)
        self.frame.save_and_refresh()
