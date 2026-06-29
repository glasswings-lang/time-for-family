"""
Time for Family -- announcement templates + the formatter that fills them.

Every player-visible line the game speaks (NVDA), shows in the status bar,
or writes to the activity log flows through format_announcement(event_id,
**kwargs) here. DEFAULT_ANNOUNCEMENTS holds the shipped wording; modders
override any line in user_data/text/announcements.txt (seeded on first run
from these defaults). No imports, no engine dependency -- load_announcements
takes the text directory as an argument so this file stays standalone and a
bug in the words is contained to one place.

Shared-shelf rule: ANNOUNCEMENTS is the one live dict both the engine and the
UI read. Refill it IN PLACE (.clear()/.update()), never rebind it.
"""

# ===== Configurable announcement templates =====
# Every player-visible announcement the game makes (NVDA speech + status
# bar + activity log) flows through format_announcement(event_id, **kwargs)
# below. The defaults shipped here are the canonical texts. On first run
# we write assets/text/announcements.txt seeded from the defaults so
# modders can rewrite any message in plain language without touching
# code. Editing the file + restarting the game replaces just the events
# the modder defined; events they leave alone keep the defaults.
#
# Template syntax: Python str.format. Use {placeholder_name} for runtime
# values; the migration sites pass these as kwargs. Each event's
# placeholders are documented in the comments above the entry in the
# default file we write.
DEFAULT_ANNOUNCEMENTS = {
    # Welcome and time controls
    "welcome_home": "Welcome home.",
    "new_game_started": "Park reset. Welcome home.",
    "time_paused": "Time paused.",
    "time_resumed": "Time resumed.",
    "sounds_muted": "Sounds muted.",
    "sounds_unmuted": "Sounds unmuted.",
    "auto_breeding_off": "Auto-breeding off.",
    "auto_breeding_on": (
        "Auto-breeding on. Eligible pairs will breed on their own; "
        "you'll still get the babies dialog when one's ready."
    ),

    # Pair formation
    "pair_formed_one": "{cat_a_name} and {cat_b_name} have become a pair in {room_name}.",
    "pair_formed_many": "New pairs formed: {pair_descriptions}.",
    "pair_formed_offline_one": (
        "While you were away, {cat_a_name} and {cat_b_name} became a pair in {room_name}."
    ),
    "pair_formed_offline_many": "While you were away, new pairs formed: {pair_descriptions}.",

    # On-launch summary of pairs currently expecting (fired by
    # _announce_expecting_on_launch). {parts} is the rendered count
    # phrase, e.g. "3 pairs are expecting".
    "expecting_on_launch_summary": "Newborns: {parts}.",

    # Gestation events. Fired when a pair conceives but the babies
    # haven't been born yet — species with `gestation_seconds` > 0
    # wait this long between conception and birth. The "_one" /
    # "_many" / "_offline_*" pattern matches the auto-breeding family
    # and lets NVDA see one aggregated message rather than
    # per-conception lines if multiple rooms conceive in the same tick.
    "breed_conceived": (
        "Pair {pair_id} in {room_name} is expecting — "
        "a {litter_label} of {species_word_plural} arrives in {gestation}."
    ),
    "auto_breed_conceived_one": (
        "Auto-breeding: pair {pair_id} in {room_name} is expecting — "
        "a {litter_label} of {species_word_plural} arrives in {gestation}."
    ),
    "auto_breed_conceived_many": (
        "Auto-breeding: some pairs are expecting — {summary}."
    ),
    "auto_breed_conceived_offline_one": (
        "While you were away, pair {pair_id} in {room_name} started "
        "expecting — a {litter_label} of {species_word_plural} "
        "arrives in {gestation}."
    ),
    "auto_breed_conceived_offline_many": (
        "While you were away, some pairs started expecting — {summary}."
    ),
    "expecting_no_room_one": (
        "Pair {pair_id} is expecting in {room_name}, but no room has "
        "space for a {species_word} {baby_word}. Build or expand "
        "before the {litter_label} arrives in {gestation}."
    ),
    "expecting_no_room_many": (
        "Some expecting pairs have no room for their babies — "
        "{summary}. Build or expand before the babies arrive."
    ),
    "auto_breed_village_birth": "A new {species_word} was born in {village_name}: {name}.",
    "auto_breed_village_birth_offline_one": (
        "While you were away, a new {species_word} was born in {village_name}: {name}."
    ),
    "auto_breed_village_birth_offline_many": (
        "While you were away, new arrivals in {village_name}: {names}."
    ),

    # Meter status changes
    "meter_low_one": "{meter} is getting low in {room_name}.",
    "meter_low_many": "Meters getting low — {summary}.",
    "meter_low_returning_one": "While you were away, {meter} ran low in {room_name}.",
    "meter_low_returning_many": "While you were away, meters ran low — {summary}.",
    "meter_refilled": "{verb} {meter}.",
    "meters_all_refilled": "Refilled all care in {room_name}.",

    # Pet / care actions
    "pet_with_response": "{response} Affection now {affection_pct}%.",
    "pet_no_response": "{care} {name}. Affection now {affection_pct}%.",
    "pet_everyone_done": "Petted {n} {plural} in {room_name}.",
    "pet_everyone_empty": "No one to pet in {room_name} yet.",

    # Selection prompts and small UI feedback
    "select_creature": "Select a creature first.",
    "select_species": "Select a {species_label} first.",

    # Moves
    "creature_moved": "{name} moved to {room_name}.",

    # Park / dig
    "dig_no_left": "No digs left today. Come back tomorrow.",
    "dig_nothing": "You dug, but found nothing this time.",
    "dig_item": "You dug up {article} {name}.",
    "dig_object": "You found an object: {name} — {description}",
    "dig_treasure": "You found a treasure: {name} — {description}",

    # Build
    "build_no_type": "No room type selected.",
    "build_failed_missing": "Couldn't build {type_name} — {reason}",
    "build_failed": "Couldn't build {type_name}.",
    "build_success": "Built {room_name}! Used {used}.",

    # Breeding (manual)
    "breed_no_pairs": "No breeding pairs in this room.",
    "breed_all_young": (
        "The pairs in this room are still too young to breed. "
        "Select one to see how long until they're ready."
    ),
    "breed_all_old": (
        "The pairs in this room have retired from breeding — "
        "they're past their species' elder age."
    ),
    "breed_still_bonding": (
        "{cat_a_name} and {cat_b_name} are still bonding — "
        "about {remaining} until they pair. "
        "They'll pair on their own; come back then."
    ),
    "breed_still_growing": (
        "{cat_a_name} and {cat_b_name} are still growing up — "
        "about {remaining} until they're old enough to start bonding."
    ),
    "breed_all_resting_one": (
        "Pair {pair_id} is resting after a recent {litter_label} — "
        "ready to try again in {remaining}."
    ),
    "breed_all_resting_many": (
        "All breeding pairs in this room are resting after a recent "
        "{litter_label}. {pairs_status}"
    ),
    "breed_low_care": "The room needs better care first — refill the meters before trying to breed.",
    "breed_no_litter": "No {label} this time. Try again later.",

    # Renaming + village move
    "creature_renamed": "{old_name} is now {new_name}.",
    "creature_renamed_saved": (
        "{old_name} is now {new_name}. Added {new_name} to your "
        "{sex_word} {species_word} names list."
    ),
    "creature_to_village": (
        "{name} has gone to live in {village_name}. "
        "You can visit them any time."
    ),
    "creature_came_home": "{name} has come back home to {room_name}.",

    # Life-stage milestones — fire the moment a creature crosses each
    # threshold. Aggregated singular/plural variants follow the same
    # pattern as wild emigration to avoid NVDA-flooding a player whose
    # whole starter cohort hits a stage in the same offline window.
    "creature_became_elder_one": "{name} is now an elder.",
    "creature_became_elder_many": "Some have become elders: {names}.",
    "creature_retired_one": "{name} has retired from breeding.",
    "creature_retired_many": "Some have retired from breeding: {names}.",
    "creature_became_elder_offline_one": (
        "While you were away, {name} became an elder."
    ),
    "creature_became_elder_offline_many": (
        "While you were away, some became elders: {names}."
    ),
    "creature_retired_offline_one": (
        "While you were away, {name} retired from breeding."
    ),
    "creature_retired_offline_many": (
        "While you were away, some retired from breeding: {names}."
    ),
    # "Decided to stay" — when a creature's affection crosses the high
    # threshold, they commit to your home as theirs. Mechanically they
    # become exempt from the wild emigration roll (forever), but the
    # framing is the creature's choice, not the player's. Also fires
    # singular/plural and offline variants for NVDA tidiness.
    "creature_settled_one": "{name} has decided this is home.",
    "creature_settled_many": "Some have decided this is home: {names}.",
    "creature_settled_offline_one": (
        "While you were away, {name} decided this is home."
    ),
    "creature_settled_offline_many": (
        "While you were away, some decided this is home: {names}."
    ),

    # The wild — auto-emigration of retired healthy creatures, plus the
    # sanctuary-move for retired disabled creatures. Singular and plural
    # variants so a single check that retires several doesn't flood NVDA
    # with one line per name.
    "wild_emigration_one": "{name} has gone to live in the wild.",
    "wild_emigration_many": (
        "Some have gone to live in the wild: {names}."
    ),
    "wild_emigration_offline_one": (
        "While you were away, {name} went to live in the wild."
    ),
    "wild_emigration_offline_many": (
        "While you were away, some went to live in the wild: {names}."
    ),
    "sanctuary_arrival_one": (
        "{name} has retired to {village_name} for good."
    ),
    "sanctuary_arrival_many": (
        "Some have retired to {village_name} for good: {names}."
    ),
    "village_villagers_added": "Added {count} {species_word} to {village_name}: {names}.",
    "village_renamed": "Renamed {old_name} to {new_name}.",

    # Elder production
    "elders_produced": "Elders produced: {summary}.",
    "elders_produced_offline": "While you were away, elders produced: {summary}.",

    # Room edit (each is one part of a composite save announcement —
    # blank a line to drop it from the announcement).
    "room_edit_renamed": "Renamed {old_name} to {new_name}.",
    "room_edit_type_changed": "Changed room type to {type_name}.",
    "room_edit_allowed_changed": "Updated allowed species.",
    "room_edit_creatures_moved": "Moved {names} to {village_name}.",

    # Birth-placement composites — each is one part of the composite
    # message that fires when babies are born and placed into rooms.
    "birth_kept_in_room": "Welcomed {names} into {room_name}.",
    "birth_spilled_full": "{names} went to {room_name} ({primary_name} was full).",
    "birth_spilled_denies": "{names} went to {room_name} ({primary_name} doesn't allow {species_word}).",
    "birth_to_village_no_space": "{names} went to {village_name} (no rooms had a free slot for {species_word}).",
    "birth_to_village_no_room": "{names} went to {village_name} (no rooms allow {species_word}).",

    # Slot expansion
    "slot_added_commons": "Added a slot to {room_name} (now {total}). Used {used}.",
    "slot_added_treasure": (
        "Added a slot to {room_name} (now {total}). "
        "Spent treasure: {treasure_name}."
    ),
    "slot_added_object": (
        "Added a slot to {room_name} (now {total}). "
        "Spent object: {object_name}."
    ),

    # Mod menu — species
    "species_added": "Species added.",
    "species_added_with_seed": (
        "Species added. A starter pair of {plural} is in {village_name} — "
        "open the 'Go to' picker and switch to {village_name} to bring "
        "them home."
    ),
    "species_saved": "Species saved.",
    "species_deleted": "Species deleted.",
    "species_deleted_with_purge": (
        "Species '{name}' deleted. {n} creature(s) of this species "
        "were removed from your save."
    ),

    # Mod menu — room types
    "room_type_added": "Room type added.",
    "room_type_saved": "Room type saved.",
    "room_type_deleted": "Room type deleted.",
    "room_type_deleted_with_purge": "Room type deleted ({n} room(s) removed).",

    # Settings
    "settings_saved": "Settings saved.",
    "announcements_saved": "Announcements saved.",

    # Ambient observations — fired during quiet stretches. The {moment}
    # placeholder is one line, picked at random from
    # assets/text/ambient.txt. Modders can wrap or re-frame it (e.g.
    # "Ambient: {moment}") by editing this template.
    "ambient_moment": "{moment}",

    # Tools menu toggle for ambient.
    "ambient_on": "Ambient observations on.",
    "ambient_off": "Ambient observations off.",
}

# Live mutable copy used at runtime. load_announcements() merges the
# user's overrides from assets/text/announcements.txt over the defaults.
ANNOUNCEMENTS = dict(DEFAULT_ANNOUNCEMENTS)


def format_announcement(event_id, **kwargs):
    """Look up the template for `event_id` and format it with `kwargs`.

    Falls back to the default template if a user-edited template is
    malformed (KeyError on a missing placeholder, etc.). Returns "" for
    unknown event ids — caller should treat empty as "skip the announce".
    """
    template = ANNOUNCEMENTS.get(event_id, "")
    if not template:
        return ""
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        default = DEFAULT_ANNOUNCEMENTS.get(event_id, "")
        if not default or default == template:
            return ""
        try:
            return default.format(**kwargs)
        except Exception:
            return ""


_ANNOUNCEMENTS_FILE_HEADER = (
    "Announcement messages — every line of text the game speaks via NVDA / "
    "shows in the status bar / writes to the activity log.\n"
    "\n"
    "Format: event_id: template\n"
    "  • Lines starting with '#' are comments. Blank lines are skipped.\n"
    "  • Templates use {placeholder} for runtime values (cat names, room\n"
    "    names, counts, etc.). Each event's available placeholders are\n"
    "    listed in the comment above its entry below — keep those names\n"
    "    if you customise the wording.\n"
    "  • Unknown placeholders fall back to the shipped default for that\n"
    "    event, so a typo can't crash the game mid-announcement.\n"
    "  • Save the file and restart Time for Family to pick up changes."
)

# Per-event placeholder docs. Used when writing the default file the
# first time so modders see what variables they have available for each
# template. Keep in sync with the kwargs the call sites actually pass.
_ANNOUNCEMENT_DOCS = {
    "welcome_home": "(no placeholders)",
    "new_game_started": "(no placeholders)",
    "time_paused": "(no placeholders)",
    "time_resumed": "(no placeholders)",
    "sounds_muted": "(no placeholders)",
    "sounds_unmuted": "(no placeholders)",
    "auto_breeding_off": "(no placeholders)",
    "auto_breeding_on": "(no placeholders)",
    "pair_formed_one": "{cat_a_name}, {cat_b_name}, {room_name}",
    "pair_formed_many": "{pair_descriptions}",
    "pair_formed_offline_one": "{cat_a_name}, {cat_b_name}, {room_name}",
    "pair_formed_offline_many": "{pair_descriptions}",
    "expecting_on_launch_summary": "{parts}",
    "breed_conceived": "{pair_id}, {room_name}, {litter_label}, {species_word_plural}, {gestation}",
    "auto_breed_conceived_one": "{pair_id}, {room_name}, {litter_label}, {species_word_plural}, {gestation}",
    "auto_breed_conceived_many": "{summary}",
    "auto_breed_conceived_offline_one": "{pair_id}, {room_name}, {litter_label}, {species_word_plural}, {gestation}",
    "auto_breed_conceived_offline_many": "{summary}",
    "expecting_no_room_one": "{pair_id}, {room_name}, {species_word}, {baby_word}, {litter_label}, {gestation}",
    "expecting_no_room_many": "{summary}",
    "auto_breed_village_birth": "{species_word}, {village_name}, {name}",
    "auto_breed_village_birth_offline_one": "{species_word}, {village_name}, {name}",
    "auto_breed_village_birth_offline_many": "{village_name}, {names}",
    "meter_low_one": "{meter}, {room_name}",
    "meter_low_many": "{summary}",
    "meter_low_returning_one": "{meter}, {room_name}",
    "meter_low_returning_many": "{summary}",
    "meter_refilled": "{verb}, {meter}",
    "meters_all_refilled": "{room_name}",
    "pet_with_response": "{response}, {affection_pct}",
    "pet_no_response": "{care}, {name}, {affection_pct}",
    "pet_everyone_done": "{n}, {plural}, {room_name}",
    "pet_everyone_empty": "{room_name}",
    "select_creature": "(no placeholders)",
    "select_species": "{species_label}",
    "creature_moved": "{name}, {room_name}",
    "dig_no_left": "(no placeholders)",
    "dig_nothing": "(no placeholders)",
    "dig_item": "{article}, {name}",
    "dig_object": "{name}, {description}",
    "dig_treasure": "{name}, {description}",
    "build_no_type": "(no placeholders)",
    "build_failed_missing": "{type_name}, {reason}",
    "build_failed": "{type_name}",
    "build_success": "{room_name}, {used}",
    "breed_no_pairs": "(no placeholders)",
    "breed_all_young": "(no placeholders)",
    "breed_all_old": "(no placeholders)",
    "breed_still_bonding": "{cat_a_name}, {cat_b_name}, {remaining}",
    "breed_still_growing": "{cat_a_name}, {cat_b_name}, {remaining}",
    "breed_all_resting_one": "{pair_id}, {litter_label}, {remaining}",
    "breed_all_resting_many": "{litter_label}, {pairs_status}",
    "breed_low_care": "(no placeholders)",
    "breed_no_litter": "{label}",
    "creature_renamed": "{old_name}, {new_name}",
    "creature_renamed_saved": "{old_name}, {new_name}, {sex_word}, {species_word}",
    "creature_to_village": "{name}, {village_name}",
    "creature_came_home": "{name}, {room_name}",
    "creature_became_elder_one": "{name}",
    "creature_became_elder_many": "{names}",
    "creature_retired_one": "{name}",
    "creature_retired_many": "{names}",
    "creature_became_elder_offline_one": "{name}",
    "creature_became_elder_offline_many": "{names}",
    "creature_retired_offline_one": "{name}",
    "creature_retired_offline_many": "{names}",
    "creature_settled_one": "{name}",
    "creature_settled_many": "{names}",
    "creature_settled_offline_one": "{name}",
    "creature_settled_offline_many": "{names}",
    "wild_emigration_one": "{name}",
    "wild_emigration_many": "{names}",
    "wild_emigration_offline_one": "{name}",
    "wild_emigration_offline_many": "{names}",
    "sanctuary_arrival_one": "{name}, {village_name}",
    "sanctuary_arrival_many": "{names}, {village_name}",
    "village_villagers_added": "{count}, {species_word}, {names}, {village_name}",
    "village_renamed": "{old_name}, {new_name}",
    "elders_produced": "{summary}",
    "elders_produced_offline": "{summary}",
    "room_edit_renamed": "{old_name}, {new_name}",
    "room_edit_type_changed": "{type_name}",
    "room_edit_allowed_changed": "(no placeholders)",
    "room_edit_creatures_moved": "{names}",
    "birth_kept_in_room": "{names}, {room_name}",
    "birth_spilled_full": "{names}, {room_name}, {primary_name}",
    "birth_spilled_denies": "{names}, {room_name}, {primary_name}, {species_word}",
    "birth_to_village_no_space": "{names}, {species_word}",
    "birth_to_village_no_room": "{names}, {species_word}",
    "slot_added_commons": "{room_name}, {total}, {used}",
    "slot_added_treasure": "{room_name}, {total}, {treasure_name}",
    "slot_added_object": "{room_name}, {total}, {object_name}",
    "species_added": "(no placeholders)",
    "species_added_with_seed": "{plural}",
    "species_saved": "(no placeholders)",
    "species_deleted": "(no placeholders)",
    "species_deleted_with_purge": "{name}, {n}",
    "room_type_added": "(no placeholders)",
    "room_type_saved": "(no placeholders)",
    "room_type_deleted": "(no placeholders)",
    "room_type_deleted_with_purge": "{n}",
    "settings_saved": "(no placeholders)",
    "announcements_saved": "(no placeholders)",
    "ambient_moment": "{moment}",
    "ambient_on": "(no placeholders)",
    "ambient_off": "(no placeholders)",
}


def _write_announcements_file(path, templates):
    """Write `assets/text/announcements.txt` with the given
    `{event_id: template}` mapping. The standard header + per-event
    placeholder comments are interleaved so the file always reads the
    same way no matter who wrote it (first-run seed, the Mods menu
    editor, or a hand edit). Iterates DEFAULT_ANNOUNCEMENTS for canonical
    ordering; any extra keys in `templates` (none expected today)
    append at the end.
    """
    with open(path, "w", encoding="utf-8") as f:
        for header_line in _ANNOUNCEMENTS_FILE_HEADER.splitlines():
            f.write(f"# {header_line}\n")
        f.write("\n")
        seen = set()
        for key, default_template in DEFAULT_ANNOUNCEMENTS.items():
            template = templates.get(key, default_template)
            placeholders = _ANNOUNCEMENT_DOCS.get(key, "")
            if placeholders:
                f.write(f"# placeholders: {placeholders}\n")
            f.write(f"{key}: {template}\n\n")
            seen.add(key)
        for key, template in templates.items():
            if key in seen:
                continue
            f.write(f"{key}: {template}\n\n")


def _write_default_announcements_file(path):
    """Seed assets/text/announcements.txt with the shipped defaults.
    First-run convenience wrapper around _write_announcements_file().
    """
    _write_announcements_file(path, DEFAULT_ANNOUNCEMENTS)


def load_announcements(text_dir):
    """Merge user overrides from assets/text/announcements.txt into the
    live ANNOUNCEMENTS dict, on top of the shipped defaults. Creates the
    file (with all defaults) on first run so modders have a starting
    point. Tolerant of malformed lines (skipped silently).
    """
    # Refill the live ANNOUNCEMENTS dict IN PLACE (clear + repopulate)
    # rather than rebinding the name. The engine and the UI share this
    # one dict object across the module boundary; reassigning it here
    # would leave the other module pointing at the stale original.
    ANNOUNCEMENTS.clear()
    ANNOUNCEMENTS.update(DEFAULT_ANNOUNCEMENTS)
    path = text_dir / "announcements.txt"
    if not path.exists():
        try:
            text_dir.mkdir(parents=True, exist_ok=True)
            _write_default_announcements_file(path)
        except OSError:
            pass
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" not in line:
                    continue
                key, _, template = line.partition(":")
                key = key.strip()
                template = template.strip()
                if key:
                    ANNOUNCEMENTS[key] = template
    except OSError:
        pass

