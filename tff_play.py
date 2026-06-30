"""
Time for Family -- the headless "play by typing" layer (Phase 3).

Plain-English park descriptions out, validated structured commands in. This
sits on top of tff_engine (the wx-free brain) so an LLM -- Tensor, via a
hearthkin tool -- can play the game with no windows. Every function:

  * takes a `save_path` (the player/AI's own save file) as its first arg,
  * loads that save, lets real time pass (offline catch-up, so the world
    stays alive between turns), performs the action through the engine's
    pure action cores, saves, and
  * returns a plain-English string -- what the AI reads.

The verbs mirror the game's core loop: look, adopt, care, breed, build,
move. Each validates its inputs and explains failures in words, so the AI
can't break the rules and always gets a useful reply.

Content (species / room-type / text definitions) loads once from the TFF
install; only the SAVE differs per player, so an AI plays the same world
shape with its own separate park.
"""

import random
from pathlib import Path

import tff_engine
from tff_engine import (
    SPECIES_DATA, ROOM_TYPES, SETTINGS,
    new_state, load_state, save_state, apply_elapsed_time,
    state_is_fresh, all_creatures, find_room, find_creature_by_id,
    cat_age_seconds, is_mature, is_elder, format_age_for_list,
    seed_village_pair, build_new_room, attempt_breed, process_expecting,
    move_creature_to_room, move_creature_to_village,
    refill_meter, pet_cat, room_type_compatible_species,
    get_room_recipe, recipe_shortfall, format_recipe, format_shortfall,
    get_treasure_cost, do_dig,
    rename_creature, expand_room_slots, deduct_recipe, set_auto_breeding,
    plan_room_retype, apply_room_retype,
)

_content_loaded = False


# ----- loading / saving ------------------------------------------------- #

def _ensure_content():
    global _content_loaded
    if not _content_loaded:
        tff_engine.ensure_user_data_dir()
        tff_engine.load_types()
        tff_engine.load_text_assets()
        _content_loaded = True


def _load(save_path):
    """Load the save at `save_path`, advancing real time so the world stays
    alive between turns. Returns the state dict."""
    _ensure_content()
    tff_engine.STATE_FILE = Path(save_path)
    state = load_state()
    apply_elapsed_time(state)
    return state


def _save(state):
    save_state(state)


def reload_content():
    """Force a re-read of the species / room-type / text definitions from
    disk. The game caches these in memory for the life of the process, so a
    file edited outside the game (or by hand) isn't seen until this runs.
    Saves are unaffected (they're re-read on every command anyway)."""
    global _content_loaded
    tff_engine.ensure_user_data_dir()
    tff_engine.load_types()
    tff_engine.load_text_assets()
    _content_loaded = True
    species = ", ".join(sorted(_species_word(s) for s in SPECIES_DATA))
    types = ", ".join(sorted(ROOM_TYPES.keys()))
    return (f"Reloaded the game's definitions from disk. Species now loaded: "
            f"{species or '(none)'}. Room types: {types or '(none)'}.")


# ----- description helpers ---------------------------------------------- #

def _species_word(species_id):
    spec = (SPECIES_DATA.get(species_id) or {}).get("spec", {})
    return (spec.get("name") or species_id or "creature").lower()


def _stage_word(cat):
    if not is_mature(cat):
        return "baby"
    if is_elder(cat):
        return "elder"
    return "adult"


def _sex_word(cat, species_id):
    spec = (SPECIES_DATA.get(species_id) or {}).get("spec", {})
    if cat.get("sex") == "F":
        return spec.get("sex_label_female", "female")
    return spec.get("sex_label_male", "male")


def _mood_word(cat):
    a = cat.get("affection", 0.5)
    if a >= 0.9:
        return "beloved"
    if a >= 0.6:
        return "happy"
    if a >= 0.3:
        return "content"
    return "lonely"


def _an(word):
    return "an" if word[:1].lower() in "aeiou" else "a"


def _describe_creature(cat):
    sid = cat.get("species", "cat")
    name = cat.get("name", "(unnamed)")
    age = format_age_for_list(cat_age_seconds(cat))
    age_phrase = age if age == "newborn" else f"{age} old"
    return (f"{name} - a {_mood_word(cat)} {_stage_word(cat)} "
            f"{_sex_word(cat, sid)} {_species_word(sid)} ({age_phrase})")


def _personality(cat):
    """A creature's stored personality line (its `description`), or ''.
    Surfaced only when the focus is a SINGLE creature (look-at / petting);
    room and bulk-care views stay concise so a full room reads cleanly."""
    return (cat.get("description") or "").strip()


def _join_readable(items):
    """'A' / 'A and B' / 'A, B, and C' — scales to any number of creatures
    so caring for a full room reads as a sentence, not a wall of text."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


# How a creature REACTS to being petted, by species. A verb phrase that
# follows the creature's name ("Clove headbutts your hand and rumbles").
# Picked at random so the same creature reacts differently each time; falls
# back to the generic pool for any species not listed here.
_PET_REACTIONS = {
    "cat": [
        "headbutts your hand and rumbles",
        "flops over to show you a belly that is, you both know, a trap",
        "kneads your knee, purring",
        "winds around you with their tail held high",
        "melts into a warm puddle under your palm",
        "chirps and bumps a cheek against yours",
    ],
    "rabbit": [
        "does a delighted little binky",
        "flops onto their side with a contented sigh",
        "grinds their teeth softly - a rabbit's purr",
        "nudges your hand for more, nose going a mile a minute",
        "stretches out long and loose beside you",
        "gives your fingers one investigative nibble, then settles",
    ],
    "dog": [
        "leans their whole weight into you",
        "thumps their tail against the floor",
        "rolls shamelessly over for belly rubs",
        "rests their chin on your knee with a sigh",
        "wiggles their entire back end with joy",
    ],
    "chicken": [
        "fluffs up and settles into your hands",
        "makes a low, contented trill",
        "preens happily against your arm",
        "burbles and tucks one foot up, cozy",
    ],
    "fish": [
        "drifts up to mouth gently at your fingertip",
        "loops once, slow and pleased",
        "shimmies their fins and hovers close",
    ],
    "ai": [
        "shimmers a little warmer",
        "hums a low, contented tone",
        "pulses soft, like a held breath let go",
    ],
}
_PET_REACTIONS_DEFAULT = [
    "leans into the attention",
    "settles happily under your hand",
    "softens, plainly pleased",
]


def _pet_reaction(cat):
    """A random in-character reaction to being petted, chosen by species."""
    pool = _PET_REACTIONS.get(cat.get("species", "")) or _PET_REACTIONS_DEFAULT
    return random.choice(pool)


def _pet_reactions_for(creatures):
    """One '<name> <reaction>' per creature, avoiding repeated reactions
    within the same batch while the pools are large enough (so a room full
    of cats doesn't all do the identical thing)."""
    used, out = set(), []
    for c in creatures:
        pool = _PET_REACTIONS.get(c.get("species", "")) or _PET_REACTIONS_DEFAULT
        fresh = [r for r in pool if r not in used] or pool
        r = random.choice(fresh)
        used.add(r)
        out.append(f"{c['name']} {r}")
    return out


def _meter_word(value):
    if value >= 0.95:
        return "full"
    if value >= 0.5:
        return "fine"
    if value >= 0.2:
        return "getting low"
    return "needs attention"


def _meter_label(room_type, key):
    for m in (ROOM_TYPES.get(room_type, {}) or {}).get("meters", []):
        if m.get("key") == key:
            return m.get("label", key.title())
    return key.title()


def _describe_room(room, heading=True):
    rtype = room.get("type", "indoor")
    creatures = room.get("creatures", [])
    cap = room.get("slot_count", len(creatures))
    lines = []
    if heading:
        lines.append(f"{room['name']} ({_an(rtype)} {rtype} room) - "
                     f"{len(creatures)} of {cap} slots filled:")
    if creatures:
        for c in creatures:
            lines.append(f"  - {_describe_creature(c)}")
    else:
        lines.append("  (empty)")
    meters = room.get("meters", {})
    if meters:
        parts = [f"{_meter_label(rtype, k)} {_meter_word(v)}"
                 for k, v in meters.items()]
        lines.append("  Care: " + ", ".join(parts) + ".")
    return "\n".join(lines)


def _actions_hint():
    return (
        "You can: adopt <species>, build <room type>, care for <room>, "
        "breed <room>, move <creature> to <room or village>, dig <number>, "
        "or look at <room / creature / village>."
    )


def _help_text():
    return (
        "Type a command in plain words:\n"
        "  look                              - describe the whole park\n"
        "  look at <room / creature / village> - zoom in on one thing\n"
        "  adopt <species>                   - bring a pair home (adopt cat)\n"
        "  dig <number>                      - gather building materials\n"
        "  build <room type> [called <name>] - build a room (build indoor)\n"
        "  move <creature> to <room / village> - relocate a creature\n"
        "  care for <room>                   - refill meters + give affection\n"
        "  care for <creature>               - give one creature affection\n"
        "  breed <room>                      - try to breed the pairs there\n"
        "  rename <creature> to <name>       - give a creature a new name\n"
        "  expand <room>                     - add a slot (costs common items)\n"
        "  convert <room> to <room type>     - change a room's type\n"
        "  autobreed on / off                - let pairs breed on their own\n"
        "  reload                            - re-read species/room files after editing them\n"
        "  reset                             - wipe the park and start over"
    )


# ----- resolvers (forgiving name matching) ------------------------------ #

def _resolve_species(name):
    """Match a species by id or display name (case-insensitive). Returns the
    species id, or None."""
    if not name:
        return None
    key = name.strip().lower()
    for sid, data in SPECIES_DATA.items():
        if sid.lower() == key:
            return sid
        if ((data.get("spec") or {}).get("name", "")).lower() == key:
            return sid
    return None


_ROOM_QUALIFIERS = (
    "the indoor room ", "the outdoor room ", "indoor room ", "outdoor room ",
    "the room ", "indoor ", "outdoor ", "room ", "the ",
)


def _strip_room_qualifier(key):
    """Drop a leading room-type qualifier the model likes to prepend, so
    'indoor room Indoor 1' resolves to the room actually named 'Indoor 1'.
    Returns the stripped key, or the original if nothing matched."""
    for q in _ROOM_QUALIFIERS:
        if key.startswith(q):
            return key[len(q):].strip()
    return key


def _resolve_room(state, name):
    """Find a room by name or id. Forgiving on purpose: small local models
    tend to prepend the room TYPE ('indoor room Indoor 1') or bury the room
    name inside a longer phrase ('care for Juna and Otiscuit in Indoor 1').
    Match exact first, then a type-qualifier strip, then a longest substring
    match -- but never guess when two rooms are equally plausible."""
    if not name:
        return None
    rooms = state.get("rooms", [])
    key = name.strip().lower()
    # 1. exact name (case-insensitive) or exact id.
    for room in rooms:
        if room.get("name", "").lower() == key or room.get("id") == name:
            return room
    # 2. strip a leading room-type qualifier and retry exact.
    stripped = _strip_room_qualifier(key)
    if stripped and stripped != key:
        for room in rooms:
            if room.get("name", "").lower() == stripped:
                return room
    # 3. a room name appearing somewhere inside what they typed. Prefer the
    #    longest such name (so 'Indoor 1' doesn't shadow 'Indoor 10'); bail
    #    if the longest is still tied between two rooms.
    matches = [r for r in rooms
               if r.get("name", "").lower() and r.get("name", "").lower() in key]
    if matches:
        matches.sort(key=lambda r: len(r.get("name", "")), reverse=True)
        longest = matches[0].get("name", "").lower()
        top = [r for r in matches if r.get("name", "").lower() == longest]
        if len(top) == 1:
            return top[0]
    return None


def _resolve_room_type(name):
    if not name:
        return None
    key = name.strip().lower()
    for tid, spec in ROOM_TYPES.items():
        if tid.lower() == key or (spec.get("name", "")).lower() == key:
            return tid
    return None


def _resolve_creature(state, name):
    """Return (cat, location) where location is the room dict or the string
    'village'; (None, None) if not found. Exact name match first, then a
    substring fallback ('pet Juna please' -> Juna) -- but only when exactly
    one distinct creature is named, so an ambiguous phrase doesn't silently
    act on just one of several."""
    if not name:
        return None, None
    key = name.strip().lower()
    for room in state.get("rooms", []):
        for c in room.get("creatures", []):
            if c.get("name", "").lower() == key:
                return c, room
    for c in state.get("village", []):
        if c.get("name", "").lower() == key:
            return c, "village"
    # Substring fallback: a creature name buried in a longer phrase.
    cands = []
    for room in state.get("rooms", []):
        for c in room.get("creatures", []):
            cn = c.get("name", "").lower()
            if cn and cn in key:
                cands.append((c, room))
    for c in state.get("village", []):
        cn = c.get("name", "").lower()
        if cn and cn in key:
            cands.append((c, "village"))
    distinct = {t[0].get("name", "").lower() for t in cands}
    if len(distinct) == 1:
        return cands[0]
    return None, None


def _loaded_species_list():
    names = sorted(_species_word(sid) for sid in SPECIES_DATA)
    return ", ".join(names) if names else "(none loaded)"


# ----- the verbs -------------------------------------------------------- #

def look(save_path, focus=""):
    """Describe the park (or one room / creature / the village) in plain
    English. Call with no focus for the whole park; pass a room name, a
    creature name, or 'village' to zoom in. Reading-only -- it never changes
    anything except letting time pass."""
    state = _load(save_path)
    _save(state)  # persist the time that passed on load
    focus = (focus or "").strip()

    if focus:
        if focus.lower() == "village":
            village = state.get("village", [])
            if not village:
                return "The village is empty right now."
            lines = ["The village (creatures waiting for a room):"]
            lines += [f"  - {_describe_creature(c)}" for c in village]
            return "\n".join(lines)
        room = _resolve_room(state, focus)
        if room is not None:
            return _describe_room(room)
        cat, where = _resolve_creature(state, focus)
        if cat is not None:
            place = "the village" if where == "village" else where["name"]
            persona = _personality(cat)
            persona_line = f" {persona}" if persona else ""
            return f"{_describe_creature(cat)}.{persona_line}\n  Lives in: {place}."
        return (f"I couldn't find a room, creature, or 'village' called "
                f"'{focus}'. {_actions_hint()}")

    # Whole-park overview.
    if state_is_fresh(state):
        return ("Your park is empty and waiting. Loaded species you can "
                f"adopt: {_loaded_species_list()}. Start by adopting one "
                "(they'll arrive in the village), then build a room for "
                "them and move them in.")

    lines = ["YOUR CREATURE PARK", ""]
    rooms = state.get("rooms", [])
    if rooms:
        lines.append(f"Rooms ({len(rooms)}):")
        for room in rooms:
            lines.append(_describe_room(room))
    else:
        lines.append("No rooms built yet.")
    village = state.get("village", [])
    lines.append("")
    if village:
        lines.append(f"Village - {len(village)} creature(s) waiting for a room:")
        lines += [f"  - {_describe_creature(c)}" for c in village]
    else:
        lines.append("Village: empty.")
    expecting = state.get("expecting", [])
    if expecting:
        lines.append("")
        lines.append(f"Expecting: {len(expecting)} pair(s) are going to have "
                     "babies soon.")
    lines.append("")
    lines.append(_actions_hint())
    return "\n".join(lines)


def adopt(save_path, species):
    """Bring a starter pair of a species home to the village. `species` is a
    loaded species name like 'cat' or 'rabbit'. They arrive in the village;
    build a room and move them in to start caring for them."""
    state = _load(save_path)
    sid = _resolve_species(species)
    if sid is None:
        return (f"There's no species called '{species}'. You can adopt: "
                f"{_loaded_species_list()}.")
    before = len(state.get("village", []))
    seed_village_pair(state, sid)
    _save(state)
    added = state["village"][before:]
    names = " and ".join(c.get("name", "?") for c in added) or "a new pair"
    word = _species_word(sid)
    return (f"Welcomed {names} - a pair of {word}s - into the village. "
            f"Build a {word}-friendly room and move them in when you're ready.")


def dig(save_path, times=1):
    """Dig in the park to gather materials for building rooms. Pass `times`
    to dig many times in one go (1-100) — one big dig gathers a real haul so
    you can afford a room without spamming the command. The daily digs budget
    (digs_per_day) still applies; you just spend it in fewer, larger scoops."""
    state = _load(save_path)
    try:
        times = int(times)
    except (TypeError, ValueError):
        times = 1
    times = max(1, min(100, times))
    found = {}
    digs_done = 0
    for _ in range(times):
        event = do_dig(state)
        if event.get("kind") == "no_digs_left":
            break
        digs_done += 1
        if event.get("kind") in ("item", "object", "treasure"):
            found[event["name"]] = found.get(event["name"], 0) + 1
    _save(state)
    if digs_done == 0:
        return ("No digs left for today - the ground needs to rest. "
                "Try again later.")
    if not found:
        haul = "nothing this time"
    else:
        haul = ", ".join(f"{n} {name}" for name, n in sorted(found.items()))
    return f"Dug {digs_done} time(s) and found: {haul}."


def build(save_path, room_type, name=""):
    """Build a new room of a given type (e.g. 'indoor', 'outdoor', 'aquatic',
    'aviary', 'glade'). Optionally pass a name for the room. Needs the right
    materials in your inventory -- if you're short, this tells you what's
    missing."""
    state = _load(save_path)
    tid = _resolve_room_type(room_type)
    if tid is None:
        names = ", ".join(sorted(
            (s.get("name") or t) for t, s in ROOM_TYPES.items()))
        return (f"There's no room type called '{room_type}'. "
                f"Available types: {names}.")
    type_spec = ROOM_TYPES.get(tid, {})
    recipe = get_room_recipe(type_spec)
    missing = recipe_shortfall(state, recipe, type_spec=type_spec)
    if missing:
        return (f"Can't build a {type_spec.get('name', tid)} yet - you "
                f"{format_shortfall(missing)}. (Full recipe: "
                f"{format_recipe(recipe, type_spec=type_spec)}.) Dig in the "
                "park to gather materials.")
    # Treasure-gated types (the Glade) need a treasure picked; for the AI
    # we just spend any available one of the cheapest kind.
    treasure_name = None
    if get_treasure_cost(type_spec) > 0:
        treasures = (state.get("inventory") or {}).get("treasures") or {}
        treasure_name = next(iter(treasures), None)
    room, _taken = build_new_room(
        state, tid, name.strip() or None,
        add_starters=False, treasure_name=treasure_name,
    )
    if room is None:
        return (f"Couldn't build the {type_spec.get('name', tid)} - "
                "something was missing at the last moment.")
    _save(state)
    compat = ", ".join(_species_word(s) for s in room_type_compatible_species(tid))
    return (f"Built {room['name']} (a {tid} room, {room.get('slot_count', 4)} "
            f"slots). It can house: {compat or 'no species yet'}. Move a "
            "creature in with: move <creature> to " + room["name"] + ".")


def move(save_path, creature, destination):
    """Move a creature to a room (by name) or to the village. e.g. move
    'Mittens' to 'Indoor 1', or move 'Mittens' to 'village'. Paired creatures
    and nursing mothers bring their dependents along; a baby still nursing
    can't be moved without its mother."""
    state = _load(save_path)
    cat, where = _resolve_creature(state, creature)
    if cat is None:
        return f"I couldn't find a creature called '{creature}'."
    dest = (destination or "").strip()

    if dest.lower() == "village":
        if where == "village":
            return f"{cat['name']} is already in the village."
        moved, reason = move_creature_to_village(state, where["id"], cat["id"])
        if moved:
            _save(state)
            return f"Moved {cat['name']} to the village."
        return _move_refusal(cat, reason)

    dest_room = _resolve_room(state, dest)
    if dest_room is None:
        return (f"There's no room called '{destination}'. Build one first, "
                "or move to 'village'.")
    if where == dest_room:
        return f"{cat['name']} is already in {dest_room['name']}."
    if cat.get("species") not in (dest_room.get("allowed_species") or []):
        return (f"{dest_room['name']} doesn't accept "
                f"{_species_word(cat.get('species'))}s. Try a room that does, "
                "or build one.")
    if len(dest_room.get("creatures", [])) >= dest_room.get("slot_count", 0):
        return (f"{dest_room['name']} is full ({dest_room['slot_count']} "
                "slots). Free a slot or build another room.")
    source_id = None if where == "village" else where["id"]
    if source_id is None:
        # Village -> room: there's no source-room move helper, so place the
        # creature directly (mirrors how the engine seeds villagers into rooms).
        state["village"].remove(cat)
        dest_room["creatures"].append(cat)
        _save(state)
        return f"Moved {cat['name']} from the village into {dest_room['name']}."
    moved, reason = move_creature_to_room(
        state, source_id, dest_room["id"], cat["id"])
    if moved:
        _save(state)
        return f"Moved {cat['name']} to {dest_room['name']}."
    return _move_refusal(cat, reason)


def _move_refusal(cat, reason):
    if reason == "is_dependent":
        return (f"{cat['name']} is a baby still nursing - move its mother "
                "instead and the baby comes along.")
    if reason == "dest_full":
        return "That room is full."
    return f"Couldn't move {cat['name']} ({reason})."


def care(save_path, room):
    """Care for everyone in a room: refill all its meters (food, water, etc.)
    and give every creature there some affection. `room` is a room name."""
    state = _load(save_path)
    target = _resolve_room(state, room)
    if target is None:
        # Maybe they meant a single creature ("pet Mittens").
        cat, where = _resolve_creature(state, room)
        if cat is not None:
            if where == "village":
                return (f"{cat['name']} is in the village - move them into a "
                        "room first to care for them there.")
            was_lonely = cat.get("affection", 0.5) < 0.3
            pet_cat(state, where["id"], cat["id"])
            _save(state)
            reaction = _pet_reaction(cat)
            beat = " They'd clearly been craving the company." if was_lonely else ""
            return f"{cat['name']} {reaction}.{beat}"
        return f"There's no room or creature called '{room}'."
    meters = list(target.get("meters", {}).keys())
    for m in meters:
        refill_meter(state, target["id"], m)
    creatures = list(target.get("creatures", []))
    for c in creatures:
        pet_cat(state, target["id"], c["id"])
    _save(state)
    if not creatures and not meters:
        return f"{target['name']} has nothing to care for yet."
    if not creatures:
        return f"In {target['name']}: refilled all care meters."
    # A reaction for each creature, scaling readably to a full room, so the
    # bulk command the kins actually use still shows every animal responding.
    reactions = _join_readable(_pet_reactions_for(creatures))
    lead = "refilled all care meters; " if meters else ""
    return f"In {target['name']}: {lead}{reactions}."


def breed(save_path, room):
    """Try to breed the pairs in a room. Needs a bonded adult male+female of
    the same species with their care met. Tells you in words why it can't if
    the conditions aren't right yet."""
    state = _load(save_path)
    target = _resolve_room(state, room)
    if target is None:
        return f"There's no room called '{room}'."
    def _population(s):
        return (sum(len(r.get("creatures", [])) for r in s.get("rooms", []))
                + len(s.get("village", [])))

    before = _population(state)
    status, payload = attempt_breed(state, target["id"])

    if status == "conceived":
        process_expecting(state)  # places babies now if gestation is 0
        _save(state)
        born = _population(state) - before
        if born > 0:
            word = "baby" if born == 1 else "babies"
            return (f"A litter arrived from {target['name']}! {born} new "
                    f"{word}. Look at the park to meet them.")
        return (f"A pair in {target['name']} conceived - babies are on the "
                "way and will arrive after their gestation. Check back soon.")

    messages = {
        "fail": "The pair tried but didn't conceive this time - try again later.",
        "no_pairs": "There are no breeding pairs here yet. You need an adult "
                    "male and female of the same species in this room.",
        "all_young": "The creatures here are still too young to breed.",
        "still_bonding": "A male and female here are still bonding - give "
                         "them a little longer and they'll pair up.",
        "still_growing": "The only possible couple here is still growing up - "
                         "they'll be old enough to bond soon.",
        "all_resting": "The pair(s) here are resting after a recent litter - "
                       "they'll be ready to try again after their cooldown.",
        "low_care": "The care meters here are too low to breed - care for "
                    "the room first, then try again.",
    }
    return messages.get(status, f"Couldn't breed here ({status}).")


def rename(save_path, creature, new_name):
    """Rename a creature (anywhere in the park)."""
    state = _load(save_path)
    cat, _where = _resolve_creature(state, creature)
    if cat is None:
        return f"I couldn't find a creature called '{creature}'."
    new_name = (new_name or "").strip()
    if not new_name:
        return "What should the new name be? Say: rename <creature> to <new name>."
    old = rename_creature(state, cat["id"], new_name)
    _save(state)
    return f"Renamed {old} to {new_name}."


def expand(save_path, room):
    """Add one more slot to a room, paid for in common items (dig for them)."""
    state = _load(save_path)
    target = _resolve_room(state, room)
    if target is None:
        return f"There's no room called '{room}'."
    cost = int(SETTINGS.get("slot_expansion_common_cost", 5))
    recipe = {"_any_common": cost}
    if recipe_shortfall(state, recipe):
        return (f"Adding a slot to {target['name']} costs {cost} common "
                "items, and you don't have enough. Dig in the park for more.")
    deduct_recipe(state, recipe)
    new_count = expand_room_slots(state, target["id"])
    _save(state)
    return f"Added a slot to {target['name']} - it now holds {new_count}."


def convert(save_path, room, new_type):
    """Change a room's type. Creatures that don't fit the new type are moved
    to another room or the village."""
    state = _load(save_path)
    target = _resolve_room(state, room)
    if target is None:
        return f"There's no room called '{room}'."
    tid = _resolve_room_type(new_type)
    if tid is None:
        names = ", ".join(sorted(
            (s.get("name") or t) for t, s in ROOM_TYPES.items()))
        return f"There's no room type called '{new_type}'. Types: {names}."
    plan = plan_room_retype(state, target["id"], new_type=tid)
    status = plan.get("status")
    if status == "no_change":
        return f"{target['name']} is already {_an(tid)} {tid} room."
    if status == "no_allowed_species":
        return (f"Converting {target['name']} to {tid} would leave it with no "
                "species allowed, so I didn't.")
    if status != "ok":
        return f"Couldn't convert {target['name']} ({status})."
    moved = apply_room_retype(state, target["id"], plan)
    _save(state)
    msg = f"Converted {target['name']} to {_an(tid)} {tid} room."
    if moved:
        msg += " Moved out (didn't fit the new type): " + ", ".join(moved) + "."
    return msg


def autobreed(save_path, on):
    """Turn auto-breeding on or off for this park."""
    state = _load(save_path)
    set_auto_breeding(state, on)
    _save(state)
    if on:
        return ("Auto-breeding is ON - bonded pairs will have babies on their "
                "own over time, even between your visits.")
    return ("Auto-breeding is OFF - pairs will only breed when you say "
            "'breed <room>'.")


def reset(save_path, confirmed):
    """Wipe the park and start over empty. Requires confirmation."""
    if not confirmed:
        return ("This will WIPE your whole park - every creature, room, and "
                "item - and start over empty. If you're sure, say: "
                "reset confirm.")
    _ensure_content()
    tff_engine.STATE_FILE = Path(save_path)
    save_state(new_state())
    return ("Your park has been reset - it's empty now. Say 'adopt <species>' "
            "to begin again.")


# ----- the text-adventure front door ------------------------------------ #

def _strip_lead(text, leads):
    """Drop leading filler words (case-insensitive), e.g. 'at the village'
    -> 'village'."""
    words = text.split()
    while words and words[0].lower().strip(".,") in leads:
        words.pop(0)
    return " ".join(words)


def _first_int(text, default=1):
    for tok in text.replace(",", " ").split():
        if tok.isdigit():
            return int(tok)
    return default


def _parse_build(rest):
    rest = _strip_lead(rest, ("a", "an", "the"))
    low = rest.lower()
    name = ""
    for sep in (" called ", " named ", " name "):
        i = low.find(sep)
        if i != -1:
            rest, name = rest[:i].strip(), rest[i + len(sep):].strip()
            break
    if rest.lower().endswith(" room"):
        rest = rest[:-5].strip()
    return rest, name


def _parse_move(rest):
    low = rest.lower()
    i = low.rfind(" to ")
    if i == -1:
        return rest, None
    creature = rest[:i].strip()
    dest = _strip_lead(rest[i + 4:].strip(), ("the",))
    return creature, dest


def command(save_path, text):
    """The text-adventure front door: run ONE plain-English command and
    return the narrated result. The AI sends a line like 'adopt cat' or
    'move Mittens to Cozy Room'; this parses the verb, validates and performs
    it through the engine, and replies in words. Empty input or 'look'
    describes the whole park; 'help' lists the commands.

    Everything stays forgiving: unknown commands and bad arguments come back
    as a friendly explanation plus a hint, never an error -- so the model can
    always read the reply and try again.
    """
    text = (text or "").strip()
    if not text:
        return look(save_path)
    parts = text.split(None, 1)
    verb = parts[0].lower().strip(".,!?")
    rest = parts[1].strip() if len(parts) > 1 else ""

    if verb in ("look", "l", "examine", "inspect", "x", "see"):
        return look(save_path, _strip_lead(rest, ("at", "the", "around", "in")))
    if verb == "adopt":
        return adopt(save_path, _strip_lead(rest, ("a", "an", "the", "some")))
    if verb == "dig":
        return dig(save_path, _first_int(rest, default=1))
    if verb == "build":
        rtype, name = _parse_build(rest)
        return build(save_path, rtype, name)
    if verb in ("move", "put", "send"):
        creature, dest = _parse_move(rest)
        if dest is None:
            return ("To move a creature, say: move <creature> to <room or "
                    "village>. For example: move Mittens to Cozy Room.")
        return move(save_path, creature, dest)
    if verb in ("care", "feed", "tend", "clean", "pet"):
        return care(save_path, _strip_lead(rest, ("for", "the", "everyone", "in")))
    if verb == "breed":
        return breed(save_path, _strip_lead(rest, ("in", "the")))
    if verb == "rename":
        i = rest.lower().rfind(" to ")
        if i == -1:
            return "Say: rename <creature> to <new name>."
        return rename(save_path, rest[:i].strip(), rest[i + 4:].strip())
    if verb in ("expand", "enlarge", "grow"):
        return expand(save_path, _strip_lead(rest, ("the", "room")))
    if verb in ("convert", "change", "retype"):
        low = rest.lower()
        i = low.rfind(" into ")
        sep = 6
        if i == -1:
            i, sep = low.rfind(" to "), 4
        if i == -1:
            return "Say: convert <room> to <room type>."
        room_name = rest[:i].strip()
        new_type = _strip_lead(rest[i + sep:].strip(), ("a", "an", "the"))
        if new_type.lower().endswith(" room"):
            new_type = new_type[:-5].strip()
        return convert(save_path, room_name, new_type)
    if verb in ("autobreed", "auto-breed", "autobreeding"):
        low = rest.lower()
        return autobreed(save_path,
                         not any(w in low for w in ("off", "stop", "disable", "no")))
    if verb == "reset":
        return reset(save_path, "confirm" in rest.lower())
    if verb in ("reload", "refresh"):
        return reload_content()
    if verb in ("help", "commands", "?", "what"):
        return _help_text()
    return (f"I didn't understand '{text}'. {_actions_hint()} "
            "Say 'help' for the full list.")


# ----- play it yourself in the terminal --------------------------------- #

def play_in_terminal(save_path=None):
    """Play Time for Family at a terminal: type a command, read the reply.
    The same forgiving text layer the AI kin play through -- one line in, a
    plain-English reply out, nothing yanking your focus (screen-reader
    friendly by design). 'help' lists commands; 'quit', Ctrl-C, or Ctrl-D
    leaves. With no argument it shares the windowed game's save, so it's the
    same park; pass a path to use a different save file."""
    import tff_engine
    if not save_path:
        save_path = str(tff_engine.STATE_FILE)
    print("Time for Family -- type 'help' for commands, 'quit' to leave.\n")
    print(command(save_path, "look"))
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye for now.")
            return
        if line.lower() in ("quit", "exit", "q"):
            print("Bye for now.")
            return
        print(command(save_path, line))


if __name__ == "__main__":
    import sys
    play_in_terminal(sys.argv[1] if len(sys.argv) > 1 else None)
