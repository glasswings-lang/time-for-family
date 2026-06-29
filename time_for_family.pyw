"""
Time for Family -- a cozy creature-park sim (windowed app; launch this file).

The top-level window (MainFrame) and main(). The rest of the presentation
lives in tff_panels (the notebook pages), tff_dialogs (pop-ups), and
tff_sound (chimes + NVDA); all the game rules are in tff_engine. PAUSED,
AUTO_BREEDING, and AMBIENT_ENABLED are MainFrame-only runtime toggles and
live here with the frame that owns them.

Save lives in state.json next to this file.
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
import tff_editors
import tff_panels
globals().update({_k: _v for _k, _v in vars(tff_engine).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_sound).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_dialogs).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_editors).items()
                  if not _k.startswith("__")})
globals().update({_k: _v for _k, _v in vars(tff_panels).items()
                  if not _k.startswith("__")})


# MainFrame-only runtime toggles. PAUSED and SOUND_MUTED reset off each
# launch; AUTO_BREEDING is restored from the save in MainFrame.__init__;
# AMBIENT_ENABLED resets to True and isn't persisted. SOUND_MUTED lives in
# tff_sound with play_sound; the other three live here with MainFrame.
PAUSED = False
AUTO_BREEDING = True
AMBIENT_ENABLED = True


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="Time for Family", size=(820, 640))
        self.state = load_state()
        # Restore AUTO_BREEDING from the save BEFORE apply_elapsed_time
        # runs — otherwise auto_breed_offline_catchup checks the
        # module-default (False) and skips the away period entirely on
        # the very first relaunch after a player turned auto-breeding
        # on. AUTO_BREEDING is a global because the toggle handler
        # mutates it; persisting a copy on the save lets it survive
        # across launches without restructuring.
        global AUTO_BREEDING
        # Default True for saves that never had this key set — matches
        # the new module-level default. Existing saves that explicitly
        # turned it off keep auto_breeding=False stored and stay off.
        AUTO_BREEDING = bool(self.state.get("auto_breeding", True))
        apply_elapsed_time(self.state)
        save_state(self.state)

        self._build()

        self._prev_meters = self._snapshot_meters()
        self._last_auto_breed = 0
        # Menu was constructed with the "off" label; reflect persisted
        # state so the label matches AUTO_BREEDING right after launch.
        if AUTO_BREEDING:
            self.auto_breed_item.SetItemLabel(
                "Turn off &auto-breeding\tCtrl+B"
            )
        # Tracks the last time on_tick ran the wild-emigration check.
        # Init to 'now' so a fresh launch doesn't immediately roll for
        # everyone — the first online check fires after one full
        # wild_emigration_check_seconds interval. Offline emigration is
        # handled separately in apply_elapsed_time using the elapsed
        # away-time, so closing the game for hours doesn't get skipped.
        self._last_wild_check = time.time()
        # Ambient announcement bookkeeping. _last_announce_at advances on
        # every announce() call (real or ambient); _last_ambient_at on
        # ambient ones only. Together they gate when the next ambient
        # line is allowed to fire.
        self._last_announce_at = time.time()
        self._last_ambient_at = 0.0

        self.tick_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_tick, source=self.tick_timer)
        self.tick_timer.Start(TICK_INTERVAL_MS)

        self.Bind(wx.EVT_CLOSE, self.on_close)

        play_sound("welcome")
        self.announce_event("welcome_home")
        wx.CallAfter(self._announce_expecting_on_launch)
        wx.CallAfter(self._announce_already_low)
        wx.CallAfter(self._announce_offline_production)
        wx.CallAfter(self._announce_offline_pairs)
        wx.CallAfter(self._announce_offline_breeding)
        wx.CallAfter(self._announce_offline_births)
        wx.CallAfter(self._announce_offline_conceptions)
        wx.CallAfter(self._announce_offline_life_stages)
        wx.CallAfter(self._announce_offline_emigration)
        # If this is a brand-new save (no rooms / village / expecting
        # pairs / remembered creatures) the Species dialog fires first
        # so the player has someone to play with before any other
        # prompts hit.
        # Auto-show takes precedence over the first-run help — the help
        # dialog still fires after the Species dialog closes (or
        # immediately if the player isn't on a fresh save).
        if state_is_fresh(self.state):
            wx.CallAfter(self._show_species_dialog, then_show_help=True)
        elif not self.state.get("seen_help"):
            wx.CallAfter(self._show_first_run_help)

    def _show_first_run_help(self):
        with HelpDialog(self) as dlg:
            dlg.ShowModal()
        self.state["seen_help"] = True
        save_state(self.state)

    def _show_species_dialog(self, then_show_help=False):
        """Open the Species dialog. If `then_show_help` is True and
        the player hasn't seen the help dialog yet, fire that next so
        a first-time player gets the picker first, then the explainer.
        """
        with SpeciesDialog(self) as dlg:
            dlg.ShowModal()
        if then_show_help and not self.state.get("seen_help"):
            self._show_first_run_help()

    def on_open_species_dialog(self, _evt):
        """File → Species. Opens the one-stop species curator: pick a
        starter pair, design a new species, or edit / delete an existing
        one. Replaces the old Welcome / Manage species / Extra species
        menu items.
        """
        self._show_species_dialog(then_show_help=False)

    def _build(self):
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Room picker: a single combo box drives a wx.Simplebook below.
        # Replaces the previous wx.Notebook tab strip — with many rooms the
        # tab strip became a horizontal scroll horror, and NVDA + cog-acc
        # users had to page through every tab to find one. The combo box:
        #   * shows the whole list at once when opened,
        #   * supports type-ahead (Windows incremental search on
        #     CB_READONLY combos — type "ind" to land on Indoor Room 1),
        #   * gets a focus accelerator (Ctrl+G, see Tools menu) so the
        #     user never has to hunt for it with Tab.
        # Page changes flow through _select_book_page so the combo and the
        # simplebook stay in sync no matter who triggered the change.
        picker_row = wx.BoxSizer(wx.HORIZONTAL)
        picker_row.Add(
            wx.StaticText(panel, label="Go to:"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.room_picker = wx.ComboBox(panel, choices=[], style=wx.CB_READONLY)
        self.room_picker.SetName("Go to")
        self.room_picker.SetToolTip(
            "Pick a room or section to show. Open the dropdown and start "
            "typing the first few letters to jump to a match (e.g. 'ind' "
            "lands on Indoor Room 1). Ctrl+G focuses this from anywhere."
        )
        self.room_picker.Bind(wx.EVT_COMBOBOX, self.on_room_picker_changed)
        picker_row.Add(self.room_picker, 1, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(picker_row, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 6)

        self.book = wx.Simplebook(panel)
        for room in self.state["rooms"]:
            self.book.AddPage(RoomPanel(self.book, self, room["id"]), room["name"])
        self.book.AddPage(
            VillagePanel(self.book, self),
            self.state.get("village_name", "Village"),
        )
        self.book.AddPage(ParkPanel(self.book, self), "Park")
        self.book.AddPage(StatsPanel(self.book, self), "Stats")
        sizer.Add(self.book, 1, wx.EXPAND)
        self._refresh_room_picker()
        if self.book.GetPageCount() > 0:
            self.book.ChangeSelection(0)
            self.room_picker.SetSelection(0)
            self._hide_inactive_book_pages()

        log_label = wx.StaticText(panel, label="Recent activity:")
        sizer.Add(log_label, 0, wx.LEFT | wx.TOP, 4)
        self.activity_log = wx.TextCtrl(
            panel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 110)
        )
        self.activity_log.SetName("Recent activity")
        sizer.Add(self.activity_log, 0, wx.EXPAND | wx.ALL, 4)

        panel.SetSizer(sizer)

        self.CreateStatusBar()
        self.SetStatusText("Welcome home.")

        bar = wx.MenuBar()
        file_menu = wx.Menu()
        new_game_id = file_menu.Append(wx.ID_NEW, "&Reset park…").GetId()
        species_dialog_id = file_menu.Append(
            wx.ID_ANY, "Sp&ecies…",
        ).GetId()
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_PREFERENCES, "&Settings…\tCtrl+,")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, "E&xit\tAlt+F4")
        bar.Append(file_menu, "&File")

        tools_menu = wx.Menu()
        # Plain menu items (not check-items) so the label itself toggles
        # between "Pause" / "Resume" etc. NVDA reads the new label on
        # next focus, which is clearer than relying on a "checked" /
        # "not checked" state announcement to indicate what the player
        # is actually toggling. The labels are kept in sync inside the
        # on_*_toggle handlers below.
        # The "Go to room or section" item focuses the room picker combo
        # box. Ctrl+G is unbound on Windows in this app and reads as "go"
        # — gives keyboard users a one-key jump back to the picker from
        # anywhere in the active panel without hunting via Tab.
        go_to_picker_id = tools_menu.Append(
            wx.ID_ANY, "&Go to room or section\tCtrl+G",
        ).GetId()
        tools_menu.AppendSeparator()
        self.pause_item = tools_menu.Append(wx.ID_ANY, "&Pause time\tCtrl+P")
        self.mute_item = tools_menu.Append(wx.ID_ANY, "&Mute sounds\tCtrl+M")
        self.auto_breed_item = tools_menu.Append(wx.ID_ANY, "Turn on &auto-breeding\tCtrl+B")
        self.ambient_item = tools_menu.Append(
            wx.ID_ANY, "Turn off ambient &observations",
        )
        bar.Append(tools_menu, "&Tools")

        mods_menu = wx.Menu()
        # Species curation lives in File → Species — adding, editing,
        # deleting, and bringing a starter pair home are all the same
        # one dialog now. Mods menu keeps the rest of the modder
        # surfaces (room types, announcements).
        manage_room_types_id = mods_menu.Append(wx.ID_ANY, "Manage &room types…").GetId()
        manage_announcements_id = mods_menu.Append(
            wx.ID_ANY, "Manage &announcements…",
        ).GetId()
        bar.Append(mods_menu, "&Mods")

        help_menu = wx.Menu()
        how_id = help_menu.Append(wx.ID_ANY, "&How to play…").GetId()
        about_id = help_menu.Append(wx.ID_ABOUT, "&About").GetId()
        bar.Append(help_menu, "&Help")
        self.SetMenuBar(bar)
        self.Bind(wx.EVT_MENU, lambda e: self.Close(), id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self.on_settings, id=wx.ID_PREFERENCES)
        self.Bind(wx.EVT_MENU, self.on_about, id=about_id)
        self.Bind(wx.EVT_MENU, self.on_new_game, id=new_game_id)
        self.Bind(wx.EVT_MENU, self.on_open_species_dialog, id=species_dialog_id)
        self.Bind(wx.EVT_MENU, self.on_pause_toggle, self.pause_item)
        self.Bind(wx.EVT_MENU, self.on_mute_toggle, self.mute_item)
        self.Bind(wx.EVT_MENU, self.on_auto_breed_toggle, self.auto_breed_item)
        self.Bind(wx.EVT_MENU, self.on_ambient_toggle, self.ambient_item)
        self.Bind(wx.EVT_MENU, self.on_show_help, id=how_id)
        self.Bind(wx.EVT_MENU, self.on_manage_room_types, id=manage_room_types_id)
        self.Bind(wx.EVT_MENU, self.on_manage_announcements, id=manage_announcements_id)
        self.Bind(wx.EVT_MENU, self.on_focus_room_picker, id=go_to_picker_id)

    def announce(self, text):
        nvda_speak(text)
        self.SetStatusText(text)
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {text}\n"
        existing = self.activity_log.GetValue()
        combined = existing + line
        if len(combined) > 4000:
            combined = combined[-4000:]
        self.activity_log.SetValue(combined)
        self.activity_log.SetInsertionPointEnd()
        # Ambient gating reads this — every announcement (including
        # ambient ones) bumps the clock so a quiet stretch of length
        # ambient_quiet_seconds has to elapse before the next ambient
        # line is allowed to fire.
        self._last_announce_at = time.time()

    def _announce_births(self, births, offline=False):
        """Compose one-or-many announcements for a list of birth
        records (the shape `process_expecting` returns: from_pair,
        species, kept_by_room, spill_full_by_room, spill_denies_by_room,
        village_no_space, village_no_room).

        Each birth fires its own composite — the parts (kept-here,
        spilled-elsewhere, sent-to-village) are individual
        `birth_kept_in_room` / `birth_spilled_full` / etc.
        announcement events that get concatenated into one message.
        """
        if not births:
            return
        play_sound("breed_success")
        rooms = self.state.get("rooms", [])
        place_name = self.state.get("village_name", "Village")
        prefix = "While you were away, " if offline else ""
        for birth in births:
            spec = SPECIES_DATA.get(birth.get("species", "cat"), {}).get("spec", {})
            species_word = spec.get("name", "creature").lower()
            litter_label = _spec_litter_label(spec)
            primary_room_name = next(
                (r["name"] for r in rooms if r["id"] == birth.get("room_id")),
                "the room",
            )
            n_total = (
                sum(len(v) for v in birth["kept_by_room"].values())
                + sum(len(v) for v in birth["spill_full_by_room"].values())
                + sum(len(v) for v in birth["spill_denies_by_room"].values())
                + len(birth["village_no_space"])
                + len(birth["village_no_room"])
            )
            baby_word = "baby" if n_total == 1 else "babies"
            header = (
                f"{prefix}A {litter_label} arrived from pair "
                f"{birth['from_pair']} in {primary_room_name} — "
                f"{n_total} {species_word} {baby_word}."
            )

            def _room_name(rid):
                return next(
                    (r["name"] for r in rooms if r["id"] == rid),
                    rid,
                )

            parts = [header]
            for rid, names in birth["kept_by_room"].items():
                parts.append(format_announcement(
                    "birth_kept_in_room",
                    names=join_names(names), room_name=_room_name(rid),
                ))
            for rid, names in birth["spill_full_by_room"].items():
                parts.append(format_announcement(
                    "birth_spilled_full",
                    names=join_names(names), room_name=_room_name(rid),
                    primary_name=primary_room_name,
                ))
            for rid, names in birth["spill_denies_by_room"].items():
                parts.append(format_announcement(
                    "birth_spilled_denies",
                    names=join_names(names), room_name=_room_name(rid),
                    primary_name=primary_room_name, species_word=species_word,
                ))
            village_name = self.state.get("village_name", "Village")
            if birth["village_no_space"]:
                parts.append(format_announcement(
                    "birth_to_village_no_space",
                    names=join_names(birth["village_no_space"]),
                    species_word=species_word, village_name=village_name,
                ))
            if birth["village_no_room"]:
                parts.append(format_announcement(
                    "birth_to_village_no_room",
                    names=join_names(birth["village_no_room"]),
                    species_word=species_word, village_name=village_name,
                ))
            parts = [p for p in parts if p]
            self.announce(" ".join(parts))

    def _maybe_emit_ambient(self):
        """Fire one ambient observation if the gates are met:

          * Tools → Ambient observations is on (AMBIENT_ENABLED).
          * It's been at least `ambient_quiet_seconds` since the last
            announcement of any kind (so ambient never talks over a
            real event).
          * It's been at least `ambient_interval_seconds` since the
            last ambient line (cap on overall frequency).
          * The ambient pool has at least one entry.

        ambient_interval_seconds = 0 disables the mechanism entirely
        (matches modder expectations for "off via setting"). The
        AMBIENT_ENABLED toggle is the in-game runtime control.
        """
        if not AMBIENT_ENABLED:
            return
        interval = int(SETTINGS.get("ambient_interval_seconds", 1200))
        if interval <= 0:
            return
        quiet = max(0, int(SETTINGS.get("ambient_quiet_seconds", 300)))
        now = time.time()
        if now - self._last_announce_at < quiet:
            return
        if now - self._last_ambient_at < interval:
            return
        if not AMBIENT_MOMENTS:
            return
        moment = random.choice(AMBIENT_MOMENTS)
        self.announce_event("ambient_moment", moment=moment)
        self._last_ambient_at = now

    def announce_event(self, event_id, **kwargs):
        """Render the template for `event_id` with `kwargs` and announce
        the result. Empty templates / unknown event ids are skipped
        silently — modders can blank a line in announcements.txt to mute
        a particular notification.

        Auto-passes `village_name` from state for every event, so any
        template that uses {village_name} works without the call site
        needing to remember to pass it. Templates that don't use it
        ignore the extra kwarg harmlessly (str.format only consults
        placeholders that appear in the template).
        """
        kwargs.setdefault(
            "village_name", self.state.get("village_name", "Village"),
        )
        text = format_announcement(event_id, **kwargs)
        if text:
            self.announce(text)

    def save_and_refresh(self):
        save_state(self.state)
        for i in range(self.book.GetPageCount()):
            page = self.book.GetPage(i)
            if hasattr(page, "refresh"):
                page.refresh()
            # If the room behind this page got renamed in state, push the
            # new name into the simplebook's stored page text. Without
            # this the picker (which reads book.GetPageText) keeps
            # showing the old name until the app restarts.
            room_id = getattr(page, "room_id", None)
            if room_id is not None:
                room = find_room(self.state, room_id)
                if room and self.book.GetPageText(i) != room["name"]:
                    self.book.SetPageText(i, room["name"])
        self._refresh_room_picker()
        # Calling .refresh() on a hidden page can side-effect re-show its
        # widgets in the tab chain on wxMSW (the Village panel's
        # Choice.Clear() + Append() rebuild is the worst offender). The
        # CONTENT isn't visible — the Simplebook still draws only the
        # current page — but the hidden page's controls slip back into
        # the focus walk for a tab or two. Re-hide every non-current
        # page to scrub that state. Same helper _select_book_page calls;
        # safe to call repeatedly.
        self._hide_inactive_book_pages()

    def _refresh_room_picker(self):
        """Repopulate the room-picker combo box from the simplebook's page
        labels and restore the current selection. Call this any time the
        set of pages, or one of their labels, changes.
        """
        labels = [self.book.GetPageText(i) for i in range(self.book.GetPageCount())]
        current = self.book.GetSelection()
        # ComboBox.Set replaces all items; the displayed text clears, so we
        # also re-set the selection below.
        self.room_picker.Set(labels)
        if 0 <= current < len(labels):
            self.room_picker.SetSelection(current)

    def _hide_inactive_book_pages(self):
        """Explicitly Hide() every non-current Simplebook page.

        wx.Simplebook is supposed to hide non-selected pages — that's
        how a docked Notebook keeps inactive pages out of tab traversal
        and out of focus chains. In practice on Windows / wxPython,
        pages added via AddPage are not always explicitly hidden until
        the first programmatic ChangeSelection away from them. The
        net effect we saw: the Park screen's controls stayed
        tab-reachable via NVDA even when Village (or any other room)
        was the visible page — focus could walk INTO Park's Dig button
        and inventory lists from outside their page's normal flow.

        Belt-and-suspenders: call this after the initial book build,
        and from _select_book_page after every change, so non-current
        pages are guaranteed-hidden regardless of what Simplebook's
        own ChangeSelection did or didn't do.
        """
        current = self.book.GetSelection()
        for i in range(self.book.GetPageCount()):
            page = self.book.GetPage(i)
            if page is None:
                continue
            if i == current:
                if not page.IsShown():
                    page.Show()
            else:
                if page.IsShown():
                    page.Hide()

    def _select_book_page(self, idx):
        """Switch to page `idx` in the simplebook, sync the picker, and
        refresh the new page. Single funnel for every page change so the
        combo and the simplebook can never drift apart, regardless of who
        triggered the change (the picker, a programmatic call, etc.).

        Uses ChangeSelection (not SetSelection) so the simplebook does not
        yank focus to the newly-shown page. That matters for the picker:
        on Windows, arrow-keying the combo fires EVT_COMBOBOX on every
        keystroke; if SetSelection moved focus into the page, the user
        could only ever advance once before being thrown out of the
        picker. Callers that want focus to land somewhere on the new page
        (e.g. landing on the cats list after a programmatic navigation)
        call SetFocus explicitly after this returns.
        """
        if idx is None or idx < 0 or idx >= self.book.GetPageCount():
            return
        self.book.ChangeSelection(idx)
        if self.room_picker.GetSelection() != idx:
            self.room_picker.SetSelection(idx)
        # Force the outgoing page hidden + incoming page shown. See
        # _hide_inactive_book_pages for the why; the short version is
        # that Simplebook's own ChangeSelection sometimes leaves the
        # previous page's controls tab-reachable on wxMSW.
        self._hide_inactive_book_pages()
        page = self.book.GetPage(idx)
        if hasattr(page, "refresh"):
            page.refresh()

    def on_room_picker_changed(self, evt):
        self._select_book_page(self.room_picker.GetSelection())
        # On Windows, swapping the visible page in a wx.Simplebook can
        # still hand focus to the newly-shown page even when we use
        # ChangeSelection (which is the documented "no event, no focus
        # move" variant). EVT_COMBOBOX only fires when the user actively
        # changed the picker — they are by definition still in it — so
        # snap focus back so down/up arrow keeps walking the list
        # instead of dumping the user into the new page after one step.
        wx.CallAfter(self.room_picker.SetFocus)

    def on_focus_room_picker(self, evt):
        # Tools → Go to room or section (Ctrl+G). Just shifts focus to the
        # combo box; doesn't auto-open the dropdown (that would talk over
        # any in-progress NVDA announcement).
        self.room_picker.SetFocus()

    def on_tick(self, evt):
        if PAUSED:
            return
        delta_seconds = TICK_INTERVAL_MS / 1000.0
        affection_decay = delta_seconds / max(1, int(SETTINGS.get("affection_decay_seconds", 3600)))
        for room in self.state["rooms"]:
            type_spec = ROOM_TYPES.get(room.get("type", "indoor"), {})
            meter_lookup = {m["key"]: m for m in type_spec.get("meters", [])}
            for meter in list(room["meters"].keys()):
                meter_decay = delta_seconds / meter_decay_seconds_for(meter_lookup.get(meter, {}))
                room["meters"][meter] = max(0.0, room["meters"][meter] - meter_decay)
            for cat in room["creatures"]:
                cat["affection"] = max(0.0, cat.get("affection", 0.5) - affection_decay)
        # Age every creature by this tick's elapsed wall-clock seconds,
        # divided by lifecycle_pace (same formula apply_elapsed_time
        # uses on the offline catch-up path). Without this, age_seconds
        # only ever advances at game launch — a creature born during a
        # session sits at age 0 the entire session, and elder /
        # too-old-to-breed checks (which read cat_age_seconds) never
        # see the creature crossing thresholds. Worse, the life-stage
        # pass short-circuits on age <= 0, so newborns get skipped from
        # every transition check until the next relaunch.
        age_delta = delta_seconds / lifecycle_pace()
        if age_delta > 0:
            for room in self.state["rooms"]:
                for cat in room["creatures"]:
                    cat["age_seconds"] = cat_age_seconds(cat) + age_delta
                    cat.pop("age_days", None)
            for cat in self.state.get("village", []):
                cat["age_seconds"] = cat_age_seconds(cat) + age_delta
                cat.pop("age_days", None)
        self.state["last_tick"] = time.time()
        self._check_meter_crossings()
        formed = progress_pairing(self.state, delta_seconds)
        if formed:
            self._announce_new_pairs(formed)

        if AUTO_BREEDING:
            interval = max(5, int(SETTINGS.get("auto_breed_interval_seconds", 60)))
            if time.time() - self._last_auto_breed >= interval:
                self._last_auto_breed = time.time()
                self._auto_breed_pass()
        # Elder production runs every tick, but the helper itself
        # rate-limits per-creature, so most ticks no-op. When something
        # is produced we announce + persist so the player sees the new
        # items in inventory without having to wait for another action.
        produced = elder_production_pass(self.state)
        if produced:
            play_sound("care")
            self.announce_event(
                "elders_produced", summary=summarize_production(produced),
            )
            save_state(self.state)
        # Clear mother-dependency tethers whose dependent_until has
        # passed. Quiet operation — no announcement; the player just
        # notices that previously-tethered babies can now move on
        # their own. Runs every tick because the check is cheap.
        clear_expired_dependencies(self.state)
        # Warn the player if any expecting pair has no room to put
        # their babies in. One-shot per record (no_room_warned flag
        # on the expecting record); re-warn fires only if space
        # disappears again after being restored.
        newly_warned = check_expecting_room_space(self.state)
        if newly_warned:
            now = time.time()
            if len(newly_warned) == 1:
                rec = newly_warned[0]
                spec = SPECIES_DATA.get(rec.get("species", "cat"), {}).get("spec", {})
                room_name = next(
                    (r["name"] for r in self.state.get("rooms", [])
                     if r["id"] == rec.get("room_id")),
                    "the room",
                )
                n_babies = len(rec.get("babies") or [])
                gestation_remaining = max(0, int(rec.get("due_at", 0) - now))
                self.announce_event(
                    "expecting_no_room_one",
                    pair_id=rec["from_pair"],
                    room_name=room_name,
                    species_word=spec.get("name", "creature").lower(),
                    baby_word="baby" if n_babies == 1 else "babies",
                    litter_label=_spec_litter_label(spec),
                    gestation=format_duration(gestation_remaining),
                )
            else:
                parts = []
                for rec in newly_warned:
                    spec = SPECIES_DATA.get(rec.get("species", "cat"), {}).get("spec", {})
                    gestation_remaining = max(0, int(rec.get("due_at", 0) - now))
                    parts.append(
                        f"pair {rec['from_pair']} ({spec.get('name', 'creature').lower()}, "
                        f"due in {format_duration(gestation_remaining)})"
                    )
                self.announce_event(
                    "expecting_no_room_many",
                    summary="; ".join(parts) + ".",
                )
        # Mature any expecting (gestating) records whose due_at has
        # passed. Babies become real creatures placed into rooms (or
        # village if no compatible room has space). Cheap when nothing
        # is gestating.
        births = process_expecting(self.state)
        if births:
            self._announce_births(births, offline=False)
            save_state(self.state)
        # Life-stage transitions: cheap (just compares age vs threshold
        # with a one-shot stamp), runs every tick. The pass returns
        # only NEW transitions, so when no one's crossing it's a no-op.
        new_elders, new_retirees, new_settled = life_stage_transitions_pass(self.state)
        if new_elders or new_retirees or new_settled:
            self._announce_life_stages(
                new_elders, new_retirees, new_settled, offline=False,
            )
            save_state(self.state)
        # Wild emigration check fires at most once per
        # wild_emigration_check_seconds, even though on_tick runs many
        # times per minute. Each fire is one chance per eligible
        # creature; offline catch-up uses the cumulative-probability
        # variant in apply_elapsed_time.
        wild_interval = max(60, int(SETTINGS.get("wild_emigration_check_seconds", 3600) or 3600))
        if time.time() - self._last_wild_check >= wild_interval:
            self._last_wild_check = time.time()
            emigrants, sanctuary_arrivals = wild_emigration_pass(self.state)
            if emigrants or sanctuary_arrivals:
                self._announce_emigration(emigrants, sanctuary_arrivals, offline=False)
                save_state(self.state)
        # Fire an ambient observation if it's been quiet long enough.
        # Cheap when the gate isn't met (a couple of timestamp diffs);
        # only does work when actually emitting.
        self._maybe_emit_ambient()
        refresh_cats = bool(formed)
        for i in range(self.book.GetPageCount()):
            page = self.book.GetPage(i)
            if refresh_cats and hasattr(page, "refresh_cats"):
                page.refresh_cats()
            if hasattr(page, "refresh_meters"):
                page.refresh_meters()
            # Age cells advance per tick, but only the cells whose
            # bucket-formatted value actually changed get rewritten.
            # format_age_for_list buckets to minute / hour / day
            # boundaries so a cell flips at most once per minute (and
            # usually much less often) — well below the NVDA-flooding
            # threshold that the full refresh_cats path would hit.
            # Structural changes (creature added / removed / pair
            # formed) still go through refresh_cats above.
            if hasattr(page, "refresh_ages"):
                page.refresh_ages()
            if hasattr(page, "refresh_cat_detail"):
                try:
                    page.refresh_cat_detail(announce=False)
                except TypeError:
                    page.refresh_cat_detail()

    def _auto_breed_pass(self):
        """One sweep of auto-breeding: try a breed in each room, then roll
        village offscreen births. Conceptions split between "expecting"
        (gestation > 0, babies arrive later via process_expecting) and
        "born now" (gestation = 0, babies are
        placed immediately).
        """
        state = self.state
        now = time.time()
        conceptions = []  # list of (room_name, expecting_record) — gestating
        for room in state["rooms"]:
            status, payload = attempt_breed(state, room["id"], now=now)
            if status == "conceived" and payload is not None:
                # If this species has zero gestation, due_at == now and
                # process_expecting at end of pass will place the
                # babies; track the conception only if there's real
                # gestation to wait through. The births announcement
                # for gestation==0 fires below from the
                # process_expecting result.
                gestation_remaining = payload.get("due_at", 0) - now
                if gestation_remaining > 0:
                    conceptions.append((room["name"], payload))
        # Place any newly-due babies (any gestation==0 conceptions
        # from this pass + any pre-existing expecting records that
        # ripened naturally).
        births = process_expecting(state, now=now)
        if births:
            self._announce_births(births, offline=False)
        # Aggregate conceptions into one announcement so a tick that
        # triggered several gestating breeds doesn't flood NVDA with
        # near-identical lines.
        if conceptions:
            play_sound("breed_success")
            now = time.time()
            if len(conceptions) == 1:
                room_name, record = conceptions[0]
                spec = SPECIES_DATA.get(record.get("species", "cat"), {}).get("spec", {})
                species_word_plural = spec.get(
                    "name_plural", spec.get("name", "creature").lower() + "s",
                ).lower()
                gestation_remaining = max(0, int(record.get("due_at", 0) - now))
                self.announce_event(
                    "auto_breed_conceived_one",
                    pair_id=record["from_pair"],
                    room_name=room_name,
                    litter_label=_spec_litter_label(spec),
                    species_word_plural=species_word_plural,
                    gestation=format_duration(gestation_remaining),
                )
            else:
                parts = []
                for room_name, record in conceptions:
                    spec = SPECIES_DATA.get(record.get("species", "cat"), {}).get("spec", {})
                    plural = spec.get(
                        "name_plural", spec.get("name", "creature").lower() + "s",
                    ).lower()
                    gestation_remaining = max(0, int(record.get("due_at", 0) - now))
                    parts.append(
                        f"pair {record['from_pair']} in {room_name} "
                        f"({plural}, due in {format_duration(gestation_remaining)})"
                    )
                self.announce_event(
                    "auto_breed_conceived_many",
                    summary="; ".join(parts) + ".",
                )
        village_name = state.get("village_name", "Village")
        for baby in auto_breed_village(state):
            spec = SPECIES_DATA.get(baby.get("species", "cat"), {}).get("spec", {})
            self.announce_event(
                "auto_breed_village_birth",
                species_word=spec.get("name", "creature").lower(),
                village_name=village_name,
                name=baby["name"],
            )
        save_state(state)

    def _announce_life_stages(self, new_elders, new_retirees, new_settled, offline):
        """One aggregated announcement per category: elders, retirees,
        and creatures who decided to stay. Plural form kicks in past
        one to keep NVDA tidy. `offline` switches the template family
        between live and 'while you were away' phrasing.
        """
        suffix = "_offline" if offline else ""
        if new_elders:
            if len(new_elders) == 1:
                self.announce_event(
                    f"creature_became_elder{suffix}_one",
                    name=new_elders[0][0],
                )
            else:
                self.announce_event(
                    f"creature_became_elder{suffix}_many",
                    names=join_names([n for n, _ in new_elders]),
                )
        if new_retirees:
            if len(new_retirees) == 1:
                self.announce_event(
                    f"creature_retired{suffix}_one",
                    name=new_retirees[0][0],
                )
            else:
                self.announce_event(
                    f"creature_retired{suffix}_many",
                    names=join_names([n for n, _ in new_retirees]),
                )
        if new_settled:
            if len(new_settled) == 1:
                self.announce_event(
                    f"creature_settled{suffix}_one",
                    name=new_settled[0][0],
                )
            else:
                self.announce_event(
                    f"creature_settled{suffix}_many",
                    names=join_names([n for n, _ in new_settled]),
                )

    def _announce_emigration(self, emigrants, sanctuary_arrivals, offline):
        """Build a single aggregated announcement for a wild-emigration
        pass. Splits into emigrants (gone to the wild) and sanctuary
        arrivals (disabled retirees moved to the village). Uses the
        plural template when more than one to avoid an NVDA flood of
        near-identical lines.
        """
        if emigrants:
            base = "wild_emigration_offline" if offline else "wild_emigration"
            if len(emigrants) == 1:
                name = emigrants[0][0]
                self.announce_event(f"{base}_one", name=name)
            else:
                names = join_names([n for n, _ in emigrants])
                self.announce_event(f"{base}_many", names=names)
        if sanctuary_arrivals:
            if len(sanctuary_arrivals) == 1:
                name = sanctuary_arrivals[0][0]
                self.announce_event("sanctuary_arrival_one", name=name)
            else:
                names = join_names([n for n, _ in sanctuary_arrivals])
                self.announce_event("sanctuary_arrival_many", names=names)

    def _announce_new_pairs(self, formed):
        play_sound("pair_formed")
        if len(formed) == 1:
            cat_a, cat_b, room_name = formed[0]
            self.announce_event(
                "pair_formed_one",
                cat_a_name=cat_a["name"],
                cat_b_name=cat_b["name"],
                room_name=room_name,
            )
        else:
            descs = [f"{a['name']} & {b['name']}" for a, b, _ in formed]
            self.announce_event(
                "pair_formed_many",
                pair_descriptions=", ".join(descs),
            )

    def _snapshot_meters(self):
        return {
            (room["id"], meter): value
            for room in self.state["rooms"]
            for meter, value in room["meters"].items()
        }

    def _check_meter_crossings(self):
        crossed = []
        for room in self.state["rooms"]:
            for meter, value in room["meters"].items():
                key = (room["id"], meter)
                prev = self._prev_meters.get(key, 1.0)
                threshold = SETTINGS["low_meter_threshold"]
                if prev >= threshold and value < threshold:
                    crossed.append((room["name"], meter))
                self._prev_meters[key] = value
        if crossed:
            play_sound("meter_low")
            if len(crossed) == 1:
                room_name, meter = crossed[0]
                self.announce_event(
                    "meter_low_one", meter=meter.title(), room_name=room_name,
                )
            else:
                summary = "; ".join(f"{m} in {r}" for r, m in crossed)
                self.announce_event("meter_low_many", summary=summary)

    def _announce_already_low(self):
        low = [
            (room["name"], meter)
            for room in self.state["rooms"]
            for meter, value in room["meters"].items()
            if value < SETTINGS["low_meter_threshold"]
        ]
        if not low:
            return
        play_sound("meter_low")
        if len(low) == 1:
            room_name, meter = low[0]
            self.announce_event(
                "meter_low_returning_one", meter=meter, room_name=room_name,
            )
        else:
            summary = "; ".join(f"{m} in {r}" for r, m in low)
            self.announce_event("meter_low_returning_many", summary=summary)

    def _announce_offline_production(self):
        """Surface what elders produced while the player was away.

        apply_elapsed_time() stashes the production tuples on
        state["_offline_production"]; we read them here and announce
        as a 'while you were away' message. Pop the key after so a
        second startup announcement doesn't repeat.
        """
        produced = self.state.pop("_offline_production", None)
        if not produced:
            return
        play_sound("care")
        self.announce_event(
            "elders_produced_offline", summary=summarize_production(produced),
        )

    def _announce_offline_life_stages(self):
        """Surface newly-elder, newly-retired, and newly-settled creatures
        from the away period. apply_elapsed_time stashes the
        (elders, retirees, settled) tuple on state["_offline_life_stages"];
        pop and announce once at startup, mirror of
        _announce_offline_production / _emigration.
        """
        result = self.state.pop("_offline_life_stages", None)
        if not result:
            return
        new_elders, new_retirees, new_settled = result
        if not (new_elders or new_retirees or new_settled):
            return
        self._announce_life_stages(
            new_elders, new_retirees, new_settled, offline=True,
        )

    def _announce_offline_emigration(self):
        """Surface emigrants and sanctuary arrivals from the away period.
        Mirror of _announce_offline_production: apply_elapsed_time stashes
        the result on state, we pop and announce once at startup.
        """
        result = self.state.pop("_offline_emigration", None)
        if not result:
            return
        emigrants, sanctuary_arrivals = result
        if not emigrants and not sanctuary_arrivals:
            return
        self._announce_emigration(emigrants, sanctuary_arrivals, offline=True)

    def _announce_offline_pairs(self):
        """Surface pairs that formed while the player was away.
        apply_elapsed_time runs progress_pairing over the away window
        and stashes the (cat_a, cat_b, room_name) tuples on
        state["_offline_pairs"]. Same one-shot pop-and-announce pattern
        as the other offline surfacers.
        """
        formed = self.state.pop("_offline_pairs", None)
        if not formed:
            return
        play_sound("pair_formed")
        if len(formed) == 1:
            cat_a, cat_b, room_name = formed[0]
            self.announce_event(
                "pair_formed_offline_one",
                cat_a_name=cat_a["name"],
                cat_b_name=cat_b["name"],
                room_name=room_name,
            )
        else:
            descs = [f"{a['name']} & {b['name']}" for a, b, _ in formed]
            self.announce_event(
                "pair_formed_offline_many",
                pair_descriptions=", ".join(descs),
            )

    def _announce_offline_breeding(self):
        """Surface auto-breeding outcomes from while the player was away.
        apply_elapsed_time stashes (room_births, village_births) on
        state["_offline_breeding"]; room_births is the same shape as
        live births (process_expecting's return), so we pass through
        to _announce_births. Village births get the existing per-baby
        announcement family.
        """
        result = self.state.pop("_offline_breeding", None)
        if not result:
            return
        room_births, village_births = result
        if not room_births and not village_births:
            return
        self._announce_births(room_births, offline=True)
        if village_births:
            village_name = self.state.get("village_name", "Village")
            if len(village_births) == 1:
                baby = village_births[0]
                spec = SPECIES_DATA.get(baby.get("species", "cat"), {}).get("spec", {})
                self.announce_event(
                    "auto_breed_village_birth_offline_one",
                    species_word=spec.get("name", "creature").lower(),
                    village_name=village_name,
                    name=baby["name"],
                )
            else:
                names = join_names([b["name"] for b in village_births])
                self.announce_event(
                    "auto_breed_village_birth_offline_many",
                    village_name=village_name,
                    names=names,
                )

    def _announce_offline_conceptions(self):
        """Surface conceptions that started during the away period and
        whose gestation hasn't completed yet. Set as a side effect by
        auto_breed_offline_catchup; matured ones get the birth
        announcement instead and aren't in this list. Aggregated to one
        announcement so a long away period with several conceptions
        doesn't flood NVDA.
        """
        conceptions = self.state.pop("_offline_conceptions", None)
        if not conceptions:
            return
        play_sound("breed_success")
        now = time.time()
        if len(conceptions) == 1:
            room_name, record = conceptions[0]
            spec = SPECIES_DATA.get(record.get("species", "cat"), {}).get("spec", {})
            species_word_plural = spec.get(
                "name_plural", spec.get("name", "creature").lower() + "s",
            ).lower()
            gestation_remaining = max(0, int(record.get("due_at", 0) - now))
            self.announce_event(
                "auto_breed_conceived_offline_one",
                pair_id=record["from_pair"],
                room_name=room_name,
                litter_label=_spec_litter_label(spec),
                species_word_plural=species_word_plural,
                gestation=format_duration(gestation_remaining),
            )
        else:
            parts = []
            for room_name, record in conceptions:
                spec = SPECIES_DATA.get(record.get("species", "cat"), {}).get("spec", {})
                plural = spec.get(
                    "name_plural", spec.get("name", "creature").lower() + "s",
                ).lower()
                gestation_remaining = max(0, int(record.get("due_at", 0) - now))
                parts.append(
                    f"pair {record['from_pair']} in {room_name} "
                    f"({plural}, due in {format_duration(gestation_remaining)})"
                )
            self.announce_event(
                "auto_breed_conceived_offline_many",
                summary="; ".join(parts) + ".",
            )

    def _announce_offline_births(self):
        """Surface births that happened during the away period when
        AUTO_BREEDING was off. apply_elapsed_time runs its own
        independent process_expecting after the catchup function
        early-returns in that case, so the player who manually bred
        a pair before going away still gets their birth announcement
        on relaunch. When AUTO_BREEDING is on, the catchup loop
        already pulled those births into _offline_breeding's
        room_births; this final pass usually finds nothing.
        """
        births = self.state.pop("_offline_births", None)
        if not births:
            return
        self._announce_births(births, offline=True)


    def _announce_expecting_on_launch(self):
        # Surfaces pairs currently expecting (gestation in progress) on
        # relaunch so the player knows what's incoming.
        expecting = self.state.get("expecting", [])
        if not expecting:
            return
        n = len(expecting)
        play_sound("expecting_summary")
        word = "pair is" if n == 1 else "pairs are"
        self.announce_event(
            "expecting_on_launch_summary",
            parts=f"{n} {word} expecting",
        )

    def on_close(self, evt):
        self.tick_timer.Stop()
        save_state(self.state)
        self.Destroy()

    def on_about(self, evt):
        wx.MessageBox(
            "Time for Family — a cozy life sim.\n\n"
            "Care for your creatures — cats, hamsters, rabbits, chickens, "
            "birds, fish, or whatever species you (or a modder) bring into "
            "the world. Pairs form, and sometimes a litter or clutch of "
            "babies is born straight into the room. They grow up, find "
            "their own pairs, and the family carries on.\n\n"
            "Accessibility: this app uses the NVDA Controller Client to push "
            "announcements directly to NVDA. The library is bundled in "
            "lib/ and is © NV Access Limited and contributors, distributed "
            "under the GNU Lesser General Public License version 2.1 — "
            "see lib/nvdaControllerClient-license.txt for the full text "
            "and lib/README.md for attribution details.",
            "About",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    def on_settings(self, evt):
        with SettingsDialog(self) as dlg:
            dlg.ShowModal()

    def on_pause_toggle(self, evt):
        global PAUSED
        PAUSED = not PAUSED
        if not PAUSED:
            # Resume — snap last_tick to now so the paused gap doesn't decay.
            self.state["last_tick"] = time.time()
            self._prev_meters = self._snapshot_meters()
            self.pause_item.SetItemLabel("&Pause time\tCtrl+P")
            self.announce_event("time_resumed")
        else:
            self.pause_item.SetItemLabel("&Resume time\tCtrl+P")
            self.announce_event("time_paused")

    def on_mute_toggle(self, evt):
        # Go through set_muted/is_muted (which live with play_sound) rather
        # than reassigning the SOUND_MUTED global directly. Once sound moves
        # to its own module, the flag and its only reader (play_sound) must
        # live together; a direct `global SOUND_MUTED` here would update a
        # stale copy and the mute wouldn't take effect.
        new_muted = not is_muted()
        set_muted(new_muted)
        # The menu label always describes the next click's action, so
        # when sounds are currently muted the option reads "Unmute".
        self.mute_item.SetItemLabel(
            "&Unmute sounds\tCtrl+M" if new_muted else "&Mute sounds\tCtrl+M"
        )
        # Announce via NVDA only — playing a sound to confirm a mute is silly.
        self.announce_event("sounds_muted" if new_muted else "sounds_unmuted")

    def on_auto_breed_toggle(self, evt):
        global AUTO_BREEDING
        # set_auto_breeding (engine) writes state["auto_breeding"], which the
        # offline catch-up reads; the global here is just the UI mirror for
        # the menu label and the live-tick gate. Restored from the save in
        # MainFrame.__init__ before apply_elapsed_time so catch-up honors it.
        AUTO_BREEDING = set_auto_breeding(self.state, not AUTO_BREEDING)
        save_state(self.state)
        if AUTO_BREEDING:
            # Reset the timer so the first auto-breed pass fires after the
            # configured interval, not immediately on toggle.
            self._last_auto_breed = time.time()
            self.auto_breed_item.SetItemLabel(
                "Turn off &auto-breeding\tCtrl+B"
            )
            self.announce_event("auto_breeding_on")
        else:
            self.auto_breed_item.SetItemLabel(
                "Turn on &auto-breeding\tCtrl+B"
            )
            self.announce_event("auto_breeding_off")

    def on_ambient_toggle(self, evt):
        global AMBIENT_ENABLED
        AMBIENT_ENABLED = not AMBIENT_ENABLED
        if AMBIENT_ENABLED:
            self.ambient_item.SetItemLabel(
                "Turn off ambient &observations"
            )
            # Reset the ambient clock so the first new line fires after
            # one full quiet+interval window, not immediately.
            self._last_ambient_at = time.time()
            self.announce_event("ambient_on")
        else:
            self.ambient_item.SetItemLabel(
                "Turn on ambient &observations"
            )
            self.announce_event("ambient_off")

    def on_show_help(self, evt):
        with HelpDialog(self) as dlg:
            dlg.ShowModal()

    def on_manage_room_types(self, evt):
        with ManageRoomTypesDialog(self) as dlg:
            dlg.ShowModal()

    def on_manage_announcements(self, evt):
        with ManageAnnouncementsDialog(self) as dlg:
            dlg.ShowModal()

    def on_new_game(self, evt):
        """Wipe the current save and reset the park.

        Backs up the current state.json to state.json.backup first (overwriting
        any previous backup) so the user can manually restore if they regret
        it. Custom text files in assets/text/ are left untouched.

        The action is named "Reset park" in the UI rather than "Start a
        new game" to avoid being confused with closing and reopening
        the game (which is just a relaunch — keeps the save intact).
        Method name kept as on_new_game for code stability.
        """
        with wx.MessageDialog(
            self,
            "Reset the park?\n\n"
            "This wipes your current creatures, rooms, village, "
            "inventory, and breeding progress and gives you an empty "
            "park to start over with. You'll then pick a species (or "
            "design a new one) to bring home as your starting pair. "
            "Your custom names, descriptions, and other text-file "
            "edits are kept.\n\n"
            "(This is different from closing and reopening the "
            "game — that just relaunches and keeps everything as it "
            "is.)\n\n"
            "Your current save will be backed up to state.json.backup "
            "in case you change your mind.",
            "Reset park?",
            wx.YES_NO | wx.ICON_QUESTION,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_YES:
                return

        if STATE_FILE.exists():
            try:
                backup_path = STATE_FILE.with_suffix(".json.backup")
                with open(STATE_FILE, "rb") as src, open(backup_path, "wb") as dst:
                    dst.write(src.read())
            except OSError:
                pass

        self.state = new_state()
        sync_settings_from_state(self.state)
        save_state(self.state)

        # Tear down old simplebook pages (rooms + village + park + stats),
        # then rebuild from the new state and resync the picker.
        while self.book.GetPageCount() > 0:
            self.book.DeletePage(0)
        for room in self.state["rooms"]:
            self.book.AddPage(RoomPanel(self.book, self, room["id"]), room["name"])
        self.book.AddPage(
            VillagePanel(self.book, self),
            self.state.get("village_name", "Village"),
        )
        self.book.AddPage(ParkPanel(self.book, self), "Park")
        self.book.AddPage(StatsPanel(self.book, self), "Stats")
        self._refresh_room_picker()
        self._select_book_page(0)
        # _select_book_page already calls _hide_inactive_book_pages,
        # but make it explicit here too — the new-game rebuild path
        # tore down the previous book entirely so it's a fresh-init
        # situation (same shape as the original __init__ build).
        self._hide_inactive_book_pages()

        # Reset internal trackers tied to the old state.
        self._prev_meters = self._snapshot_meters()
        self.activity_log.ChangeValue("")

        play_sound("welcome")
        self.announce_event("new_game_started")
        # Brand-new state is empty — fire the Species dialog so the
        # player has a clear first action rather than landing on an
        # empty village with no obvious next step.
        wx.CallAfter(self._show_species_dialog, then_show_help=False)

    def _village_tab_index(self):
        """Return the simplebook index of the Village page, or None if it
        isn't there. Looks up by panel type (not page text) so the tab
        title can be customised without breaking insertion logic.
        """
        for i in range(self.book.GetPageCount()):
            if isinstance(self.book.GetPage(i), VillagePanel):
                return i
        return None

    def set_village_tab_title(self, name):
        """Update the Village page's display label after a player rename.
        State persistence happens in the caller.
        """
        idx = self._village_tab_index()
        if idx is not None:
            self.book.SetPageText(idx, name)
            self._refresh_room_picker()

    def add_room_tab(self, room):
        """Insert a freshly-built room as a simplebook page, before
        Village/Park, refresh the picker, and switch to it so the user can
        see it was added.

        Build originates from the Park section, so after the build dialog
        closes focus would otherwise stay on the Park's "Build" button
        even though the visible page is now the new room — the player
        sees one room but their keyboard is in a different one. Move
        focus to the picker (which now reads the new room's name) so
        the visible state and focus state match. Tabbing once from
        there enters the new room's panel.
        """
        village_idx = self._village_tab_index()
        panel = RoomPanel(self.book, self, room["id"])
        if village_idx is None:
            self.book.AddPage(panel, room["name"])
            new_idx = self.book.GetPageCount() - 1
        else:
            self.book.InsertPage(village_idx, panel, room["name"])
            new_idx = village_idx
        self._refresh_room_picker()
        self._select_book_page(new_idx)
        wx.CallAfter(self.room_picker.SetFocus)

    def rebuild_room_tab(self, room_id):
        """Replace the existing page for a room with a freshly-built RoomPanel.

        Used after a room's type or meters change — the old panel was built
        for the old type's meter layout and can't be patched in place.
        """
        room = find_room(self.state, room_id)
        for i in range(self.book.GetPageCount()):
            page = self.book.GetPage(i)
            if getattr(page, "room_id", None) != room_id:
                continue
            new_panel = RoomPanel(self.book, self, room_id)
            was_selected = (self.book.GetSelection() == i)
            self.book.RemovePage(i)
            page.Destroy()
            self.book.InsertPage(i, new_panel, room["name"])
            self._refresh_room_picker()
            if was_selected:
                self._select_book_page(i)
            return


def main():
    try:
        ensure_user_data_dir()
        load_types()
        load_text_assets()
        ensure_sounds()
        app = wx.App(False)
        frame = MainFrame()
        frame.Show()
        app.MainLoop()
    except Exception:
        import traceback
        try:
            with open(CRASH_LOG, "w", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        except OSError:
            pass
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"Time for Family crashed. Details written to:\n{CRASH_LOG}",
                "Time for Family",
                0x10,
            )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
