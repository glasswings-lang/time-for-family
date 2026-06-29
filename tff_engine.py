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


PROJECT_DIR = Path(__file__).parent
STATE_FILE = PROJECT_DIR / "state.json"
SOUNDS_DIR = PROJECT_DIR / "assets" / "sounds"
CRASH_LOG = PROJECT_DIR / "crash.log"

# Shipped factory templates — read-only after first launch. The species
# editor / room-type editor / text-pool editors NEVER write here; instead,
# they write to user_data/ below. assets/ exists as the "factory reset"
# reference: copy a file from here back into user_data/ to revert.
ASSETS_TEXT_DIR = PROJECT_DIR / "assets" / "text"
ASSETS_TYPES_DIR = PROJECT_DIR / "assets" / "types"

# Player-editable game data lives under user_data/, populated on first
# launch by copying from assets/. Everything the in-game editors can
# modify (species specs, room-type specs, name pools, descriptions, pet
# responses, disabilities, announcements.txt, ambient.txt, items lists,
# objects + treasures pools) lives here. Hand-editing is fine — the
# editors round-trip through the same files.
USER_DATA_DIR = PROJECT_DIR / "user_data"
TEXT_DIR = USER_DATA_DIR / "text"
TYPES_DIR = USER_DATA_DIR / "types"
SPECIES_DIR = TYPES_DIR / "species"
ROOM_TYPES_DIR = TYPES_DIR / "room_types"

TICK_INTERVAL_MS = 1000  # engine internal — not user-tunable

# All gameplay-relevant tunables live here as a single source of truth.
# DEFAULT_SETTINGS is read-only; SETTINGS is the live mutable copy that the
# settings dialog and game logic both read from. State persistence lives in
# state["settings"]; load_state() syncs SETTINGS from there on launch.
DEFAULT_SETTINGS = {
    # Time. Defaults are tuned toward "feels like real life" pacing —
    # slow enough that a quiet two-hour session doesn't yield 35 litters
    # and three rooms full of empty meters. Players who want faster
    # turnaround can shrink any of these in Settings.
    "full_decay_seconds": 21600,       # default rate: 6 hours full→empty.
                                       # Room-type meters override per-meter
                                       # via decay_seconds in the room-type JSON.
    "affection_decay_seconds": 21600,  # 6 hours full→empty without petting
    # `mature_seconds` was the global "how long a basket waits before
    # you can open it" setting. Removed in late May 2026 alongside the
    # gestation/mother-dependency rework: the wait was an artificial
    # delay with no biological referent. Gestation is now the
    # pre-birth phase, and mother-dependency is the post-acceptance
    # tethering phase — both per-species. Baskets are openable as
    # soon as they arrive. Kept as a comment here as a tombstone in
    # case a save still has the key (load_state ignores unknown
    # SETTINGS keys, so old saves don't break).
    "pair_formation_seconds": 1800,    # 30 minutes together before bonding
    # Global fallback for the per-species "rest between litters" duration.
    # The active value is read from spec["breed_cooldown_seconds"] in the
    # species editor's Advanced section; this is what species_breed_cooldown_seconds
    # falls back to when a species hasn't set its own. Not editable in the
    # Settings dialog any more — kept here so old saves and unedited
    # modder specs keep working without forcing a re-edit.
    "breed_cooldown_seconds": 86400,   # 1 day rest between litters
    # Breeding
    "breed_success_chance": 0.6,       # 0–1, probability of a breed attempt yielding a litter
    "breed_min_care": 0.3,             # all meters must be at least this to breed
    "min_babies": 1,
    "max_babies": 4,
    # When parents are related (full siblings, half-siblings, or one is the
    # other's parent), the species' disability_chance is multiplied by this
    # factor for each baby's roll. Reflects real-world inbreeding depression.
    # Disability framing in TFF is unchanged — this only affects how often
    # the existing roll succeeds, not what disability means or does.
    "inbreeding_disability_mult": 3.0,
    # Per-color chance that a baby gets a fresh roll from the species'
    # color pool instead of inheriting that slot from a parent. Models
    # recessive ancestor traits popping up. 0 = strict inheritance
    # (always one color from each parent); 1 = always fresh roll
    # (parents irrelevant to color). Default 0.05 = ~5% per color slot.
    "color_mutation_chance": 0.05,
    # Care
    "low_meter_threshold": 0.5,        # 0–1, meter drop below this fires the warning sound
    # Legacy time-scale knob. Pre-redesign, this controlled how fast
    # creatures aged by mapping real seconds to "game days." The
    # redesign drops the game-days abstraction in favor of pure real
    # time — `lifecycle_pace` is now the user-facing speed knob. The
    # value is kept here ONLY so old `state.json` saves with `age_days`
    # creatures can be migrated on load (load_state multiplies the old
    # age_days by this to get age_seconds). Don't remove without a
    # migration plan for old saves.
    "seconds_per_game_day": 3600,
    # Single multiplier applied to every life-stage timing — baby maturity
    # AND continuous aging (the latter via seconds_per_game_day). Values
    # below 1 make the whole lifecycle faster; above 1 makes it slower.
    # 1.0 = no change. 0.5 = babies grow up and age toward elder twice as
    # fast. 2.0 = twice as slow. Lets a player tune the pace once instead
    # of editing each species' breeding_age_seconds in Mods → Manage
    # species and seconds_per_game_day separately. Clamped to a small
    # positive minimum at read time so 0 never zeros out aging math.
    "lifecycle_pace": 4.0,
    # The wild — auto-emigration for retired healthy creatures. Every
    # `wild_emigration_check_seconds` of real time, every creature past
    # their species' elder age (the merged "they're old now" milestone
    # — same threshold that gates breeding retirement) rolls a `wild_emigration_chance`
    # to leave the save for "the wild" (a soft permadeath: gone but
    # loved). Disabled creatures are exempt — the village is their
    # sanctuary, see disability_blocks_emigration. Set chance to 0 to
    # turn the mechanic off; raise it to push retired creatures out
    # faster. Healthy retirees in rooms emigrate from the room directly.
    "wild_emigration_chance": 0.05,
    "wild_emigration_check_seconds": 3600,
    # Park / crafting
    "digs_per_day": 500,                # how many times you can dig in the park per real day
    "slot_expansion_common_cost": 5,    # common items to add one slot to a room
    # Auto-breeding (Tools menu toggle) checks every this many real seconds.
    # When auto-breeding is on, every interval tries one breed in each room
    # and rolls a low village-wide chance of an offscreen birth.
    "auto_breed_interval_seconds": 3600,
    # Elder lifecycle: each elder in a room produces one item every this
    # many real seconds, drawn at random from the room-type's build_recipe
    # (so cats in an Indoor room produce sticks/leaves/acorns; aquatic
    # elders produce stones/pebbles/etc.). Default 3 hours — a player
    # who pops in once or twice a day sees a small but steady trickle.
    "elder_production_seconds": 10800,
    # Ambient announcements — short flavour lines from
    # assets/text/ambient.txt, fired during quiet stretches when nothing
    # else has happened. Default: at most one every 20 minutes, and
    # only if the last real announcement was 5+ minutes ago. Set
    # ambient_interval_seconds to 0 (or toggle Tools → Ambient
    # observations off) to disable.
    "ambient_interval_seconds": 1200,
    "ambient_quiet_seconds": 300,
    # Note: per-room-type cost and slot count live in assets/types/room_types/<type>.json
    # (default_cost, default_slots) so they're moddable per type. Edit those JSON files
    # to change them. The settings dialog stays focused on global tunables.
    # Dig outcome weights — get normalized at runtime, so they don't have to sum to 1.0.
    # Default mix: ~10% nothing, the rest distributed across the four good outcomes.
    "dig_chance_nothing":  0.10,
    "dig_chance_common":   0.45,
    "dig_chance_uncommon": 0.25,
    "dig_chance_object":   0.13,
    "dig_chance_treasure": 0.07,
}

SETTINGS = dict(DEFAULT_SETTINGS)

# Per-species text pools (names, descriptions, pet responses) live entirely
# in assets/text/species/<id>/*.txt. New species created via Mods → Manage
# species start with empty pools that the user fills in via the editor.
# Park dig pools. Common/uncommon items are stackable counts; objects and
# treasures are individuals (each has its own description). The "name |
# description" format in objects/treasures files lets users add flavour.
DEFAULT_ITEMS_COMMON = [
    "stick", "stone", "acorn", "leaf", "pebble", "twig",
    # Glade-themed commons. Diggable in the Park alongside everything
    # else; gathered in quantity to build a Glade room (which also
    # requires one treasure).
    "moss", "dewdrop", "mushroom cap", "pinecone",
]
DEFAULT_ITEMS_UNCOMMON = [
    "ribbon", "feather", "twine", "fabric scrap", "small bell", "bottle cap",
]
DEFAULT_OBJECTS = [
    ("cozy basket", "A woven basket lined with soft fabric."),
    ("scratching post", "Sisal-wrapped, well-loved."),
    ("sun-warmed mat", "A square of warmth in any room."),
    ("ceramic bowl", "A wide ceramic bowl with chipped edges."),
    ("toy mouse", "A felt mouse, slightly nibbled."),
    ("rope tassel", "Just begging to be batted at."),
    ("bell on a string", "Tinkles softly at the slightest breath."),
    ("padded cushion", "Sized for one curled-up cat."),
]
DEFAULT_TREASURES = [
    ("tarnished silver locket", "A small clasp marks where a photo once lived."),
    ("sea glass marble", "Frosted green, smooth as a worry stone."),
    ("antique brass button", "Edges worn round by decades of fingertips."),
    ("tiny porcelain cat", "Three inches tall, clearly loved."),
    ("weathered postcard", "The address has faded; the wishes are still there."),
    ("silver thimble", "Just the size for a sparrow's hat."),
    ("woven friendship bracelet", "Faded threads, three knots tied tight."),
    ("wooden bird whistle", "Plays one clear note, a little out of tune."),
    ("moss-covered stone", "Heavier than it looks, soft to the touch."),
    ("old key", "Doesn't seem to fit anything around here."),
]

# Default ambient lines shipped if assets/text/ambient.txt doesn't
# exist. Neutral, calm, generic — modders will replace these with
# voice that fits their save.
DEFAULT_AMBIENT = [
    "A quiet afternoon.",
    "Soft sounds from the rooms.",
    "The world is calm for now.",
    "A breeze drifts through.",
    "The day passes gently.",
    "Light shifts across the floor.",
    "Everyone seems content.",
    "A small, ordinary moment.",
]

# Live, runtime-loaded versions; populated by load_text_assets() on startup.
# NAMES_F/M, DESCRIPTIONS, PET_RESPONSES are legacy globals — they mirror
# whichever species first loads (typically cat) so any unmigrated call site
# still finds something. Park-wide pools are seeded from defaults if the
# corresponding text files don't exist yet.
NAMES_F = []
NAMES_M = []
DESCRIPTIONS = []
PET_RESPONSES = []
ITEMS_COMMON = list(DEFAULT_ITEMS_COMMON)
ITEMS_UNCOMMON = list(DEFAULT_ITEMS_UNCOMMON)
OBJECTS = [{"name": n, "description": d} for n, d in DEFAULT_OBJECTS]
TREASURES = [{"name": n, "description": d} for n, d in DEFAULT_TREASURES]
# Ambient flavour pool — short observation lines played during quiet
# stretches when nothing else is happening. Populated from
# assets/text/ambient.txt by load_text_assets().
AMBIENT_MOMENTS = list(DEFAULT_AMBIENT)

# Loaded from user_data/types/species/*.json on startup (seeded from the
# shipped assets/types/species/ on first run). Each entry holds the
# species spec plus its loaded text pools (names, descriptions, pet responses).
SPECIES_DATA = {}

# Loaded from user_data/types/room_types/*.json on startup. Each entry is the
# JSON body verbatim (id, name, meters list, default_cost, default_slots, etc.).
ROOM_TYPES = {}

# Species and room types are user-data: they live entirely as JSON files
# in user_data/types/species/ and user_data/types/room_types/. Species
# are managed via File → Species (the SpeciesDialog — pick a starter
# pair, edit, delete, or design a new one), and room types via Mods →
# Manage room types. The factory copies in assets/types/ are read-only
# and never touched after first launch — copy a file from there back
# into user_data/ to revert any species or room type to its shipped
# default.


def random_cat_name(sex):
    """Legacy alias — pick a name from the cat species pool."""
    return random_creature_name("cat", sex)


def _markov_name_from_corpus(corpus, min_len=3, max_len=12, max_tries=12):
    """Generate a single creature name in the style of `corpus` using a
    second-order character Markov chain. Trains afresh per call (cheap
    for 20-40 short names; cached up the stack would be premature
    optimisation given how rarely creature names are minted).

    Returns a generated name on success, or None if the corpus is too
    thin / the chain wandered into a dead end. Caller is expected to
    fall back to plain pool-pick when None comes back.

    Why second-order: first-order produces incoherent gibberish, third-
    order tends to regurgitate the input verbatim. Second-order hits
    the sweet spot where output sounds like the source pool without
    being a copy of it.
    """
    if not corpus:
        return None
    transitions = {}
    starts = []
    for raw in corpus:
        name = (raw or "").strip()
        # Two-char minimum so the second-order chain has at least one
        # seed pair to work from. Single-letter or empty entries skipped.
        if len(name) < 2:
            continue
        starts.append(name[:2])
        padded = name + " "
        for i in range(len(padded) - 2):
            key = padded[i:i + 2]
            nxt = padded[i + 2]
            transitions.setdefault(key, []).append(nxt)
    if not starts or not transitions:
        return None
    for _ in range(max_tries):
        seed = random.choice(starts)
        out = list(seed)
        state = seed
        while len(out) < max_len:
            choices = transitions.get(state)
            if not choices:
                break
            nxt = random.choice(choices)
            if nxt == " ":
                break  # natural end of word
            out.append(nxt)
            state = state[1] + nxt
        # Title-case the first letter (corpus may include lowercase
        # noise) and trim. Reject too-short fragments and any that
        # accidentally exactly match a corpus entry — generated names
        # should feel different from the pool.
        candidate = "".join(out).strip()
        if not candidate:
            continue
        candidate = candidate[0].upper() + candidate[1:]
        if len(candidate) < min_len:
            continue
        if candidate in corpus:
            continue
        return candidate
    return None


def random_creature_name(species_id, sex):
    species = SPECIES_DATA.get(species_id)
    if species:
        pool = species.get("name_pool_f" if sex == "F" else "name_pool_m", [])
        # Per-species opt-in: when `name_generation` is "markov", roll a
        # generated name first and fall back to a plain pool pick if the
        # chain didn't produce anything usable. Default ("pool" or
        # missing) preserves the original behaviour for modder species
        # that haven't opted in.
        spec = species.get("spec", {})
        mode = (spec.get("name_generation") or "pool").lower()
        if mode == "markov":
            generated = _markov_name_from_corpus(pool)
            if generated:
                return generated
        if pool:
            return random.choice(pool)
    # Species pool is empty — return a species-neutral placeholder
    # rather than the cat-default NAMES_F/M pool. Falling back to cat
    # names silently bled "Smokey", "Daisy", etc. into any species
    # whose name pool happened to be empty at creature-creation time
    # (e.g. a brand-new species saved before its pools were filled
    # in). The placeholder makes it obvious the creature needs a name.
    generic = (
        "Newcomer", "Visitor", "Wanderer", "Stranger", "Friend",
        "Guest", "Drifter", "Pilgrim", "Traveler", "Sojourner",
        "Roamer", "Rambler", "Foundling", "Arrival", "Newbie",
    )
    return random.choice(generic)


def random_description(species_id="cat", colors=None):
    """Pick a description from the species' pool, with optional
    color substitution. If `colors` is a list of strings (typically
    the creature's two colors), `{color}` and `{color2}` placeholders
    in the chosen template get filled in. Templates without
    placeholders pass through unchanged. Empty colors list = the
    placeholders are left as empty strings, with double-spaces and
    leading articles tidied up afterward so the prose still reads.
    """
    species = SPECIES_DATA.get(species_id)
    if species:
        descs = species.get("descriptions", [])
    else:
        descs = []
    # No species-side cat fallback — silently substituting cat
    # descriptions ("A sleepy tabby…") for any species with an empty
    # description pool was the same class of bug as the names
    # fallback. Empty pool = empty description; the player can fill
    # the pool in and re-create or accept the blank.
    if not descs:
        return ""
    template = random.choice(descs)
    if "{" not in template:
        return template
    color_kwargs = {"color": "", "color2": "", "color3": ""}
    if colors:
        # Cycle through the available colors so {color2} differs from
        # {color} when the creature has two; {color3} reuses if there's
        # only one. Pads with empty strings so missing placeholders
        # vanish gracefully.
        for i, c in enumerate(colors[:3]):
            color_kwargs[f"color{i+1}" if i else "color"] = c
        if not color_kwargs["color2"] and colors:
            color_kwargs["color2"] = colors[0]
        if not color_kwargs["color3"] and colors:
            color_kwargs["color3"] = colors[-1]
    try:
        out = template.format(**color_kwargs)
    except (KeyError, IndexError):
        # Template uses placeholders we don't pass — fall back to
        # raw template rather than crash. Modder typo guard.
        return template
    # Tidy up artifacts from empty substitutions: collapse double
    # spaces and "a  cat" / "the  rabbit" patterns into something
    # readable.
    out = re.sub(r"\s{2,}", " ", out).replace(" ,", ",").replace(" .", ".")
    return out.strip()


def species_color_pool(species_id):
    """Return the list of colors for a species, or an empty list if
    none configured. Reads from SPECIES_DATA so the species editor's
    in-session edits are picked up immediately.
    """
    species = SPECIES_DATA.get(species_id)
    if not species:
        return []
    return list(species.get("colors") or [])


def roll_creature_colors(species_id, parents=None):
    """Pick the two colors for a new creature. With no parents, both
    colors come from the species' pool (or empty if the pool is empty).
    With parents, the baby gets one color from each parent slot —
    each slot has `color_mutation_chance` of a fresh pool roll
    instead, modelling a recessive ancestor.

    `parents` is an iterable of creature dicts; we look at each
    parent's `colors` field. Missing-colors parents fall back to
    pool rolls for that slot. Returns a list of two strings (or
    fewer if the pool is empty AND no parents have colors).
    """
    pool = species_color_pool(species_id)
    if parents is None or not parents:
        if not pool:
            return []
        if len(pool) == 1:
            return [pool[0], pool[0]]
        return [random.choice(pool), random.choice(pool)]

    mutation_chance = float(SETTINGS.get("color_mutation_chance", 0.05) or 0)
    parent_color_lists = [list(p.get("colors") or []) for p in parents]

    def _slot_color(parent_idx):
        # 1. Mutation roll: fresh pool pick (only if pool has any).
        if pool and random.random() < mutation_chance:
            return random.choice(pool)
        # 2. Try the matching parent's color list.
        if parent_idx < len(parent_color_lists):
            pc = parent_color_lists[parent_idx]
            if pc:
                return random.choice(pc)
        # 3. Fall back to any parent's colors.
        all_parent_colors = [c for pc in parent_color_lists for c in pc]
        if all_parent_colors:
            return random.choice(all_parent_colors)
        # 4. Last-ditch: species pool.
        if pool:
            return random.choice(pool)
        return None

    out = [_slot_color(0), _slot_color(1)]
    return [c for c in out if c]


def format_creature_colors(creature):
    """Render a creature's two colors as a friendly phrase for UI:
    'ginger and white', or 'ginger' if only one is set, or '' if
    none. The detail panel hides the Colour line when this returns
    empty so we don't show a blank label.
    """
    colors = creature.get("colors") or []
    colors = [c for c in colors if c]
    if not colors:
        return ""
    if len(colors) == 1:
        return colors[0]
    if len(colors) == 2 and colors[0] == colors[1]:
        return colors[0]
    return " and ".join(colors)


def parse_disability_entry(raw):
    """Parse one line from `disabilities.txt` into (description, flags).

    Lines look like:
        Born blind.                       → ("Born blind.", set())
        Three legs. | ok                  → ("Three legs.", {"ok"})
        Heart condition. | no_produce     → ("Heart condition.", {"no_produce"})
        Combined. | ok | no_produce       → ("Combined.", {"ok", "no_produce"})

    Recognized flags (default behaviour applies when the flag is absent):
      * ``ok`` — this disability does NOT block emigration to the wild
        (default: it does — disability is the reason the village/sanctuary
        exists; modders mark exceptions with ``| ok``).
      * ``no_produce`` — this disability blocks elder production
        (default: it doesn't — modders mark blockers with
        ``| no_produce``).

    Unrecognized flags are dropped silently so future flags can be
    introduced without crashing older saves' parsing. Lower-cased on
    parse so the user's casing doesn't matter.
    """
    parts = [p.strip() for p in (raw or "").split("|")]
    description = parts[0]
    recognized = {"ok", "no_produce"}
    flags = {p.lower() for p in parts[1:] if p.lower() in recognized}
    return description, flags


def _species_disabilities_parsed(species_id):
    """Return the species' disabilities pool as a list of (desc, flags)
    tuples, computed lazily and cached on the species record. Falls back
    to an empty list if the species or its pool is missing.
    """
    species = SPECIES_DATA.get(species_id)
    if not species:
        return []
    cached = species.get("disabilities_parsed")
    if cached is not None:
        return cached
    pool = species.get("disabilities") or []
    parsed = [parse_disability_entry(line) for line in pool if line]
    species["disabilities_parsed"] = parsed
    return parsed


def disability_blocks_emigration(species_id, description):
    """True if a creature with this disability description should stay
    in the village (the sanctuary) rather than auto-emigrate to the
    wild. Default behaviour: yes — disability is the design's stated
    reason for the village to exist. Modders flip this per disability
    line by appending ``| ok`` in the species' disabilities.txt.

    A description that isn't found in the pool (e.g. modder removed
    the line after a creature already had it) defaults to "stays" —
    the creature still has the disability on their record.
    """
    if not description:
        return False
    for desc, flags in _species_disabilities_parsed(species_id):
        if desc == description:
            return "ok" not in flags
    return True  # unknown description: keep them safe in the village


def disability_blocks_production(species_id, description):
    """True if this disability prevents the elder-production roll from
    succeeding for this creature. Default: doesn't block. Modders mark
    blockers per-line with ``| no_produce``.
    """
    if not description:
        return False
    for desc, flags in _species_disabilities_parsed(species_id):
        if desc == description:
            return "no_produce" in flags
    return False


def maybe_disability(species_id, chance_mult=1.0):
    """Roll the species' `disability_chance` and return one description
    from its disabilities pool if it hits — else None.

    `chance_mult` is applied to the per-species rate at roll time. It's
    used to model inbreeding depression — when parents are related, the
    breeding code passes a >1 multiplier so the same disability pool just
    gets sampled more often. This does NOT change what disability means
    in the game; the design intent below is unchanged.

    Design intent (read this before tweaking the call sites or adding
    "balance" logic that gates anything by disability):

      Disability is a respectful representation feature, not a debuff.
      A creature with a disability can pair, breed, raise babies, take
      affection, age, and be adopted exactly like any other creature.
      No mechanical penalty applies — not to fertility, not to care
      meters, not to affection decay, not to anything. The only place
      `cat["disability"]` will be consulted (once the wild ships in a
      future round) is auto-emigration: disabled creatures stay in the
      village rather than auto-leaving for the wild, because some folks
      need a home that knows them. That's the entire mechanical effect.

      The CONTENT of disability descriptions is written by the
      user/modder in `assets/text/species/<id>/disabilities.txt`.
      Shipped species default to `disability_chance: 0` and an empty
      pool — modders opt in per-species when they're ready to write
      respectful, factual descriptions for that species.
    """
    species_spec = SPECIES_DATA.get(species_id, {}).get("spec", {})
    chance = float(species_spec.get("disability_chance", 0) or 0) * chance_mult
    chance = min(max(chance, 0.0), 1.0)
    if chance <= 0:
        return None
    if random.random() >= chance:
        return None
    parsed = _species_disabilities_parsed(species_id)
    if not parsed:
        return None
    # Pick a parsed entry and store only the description text on the
    # creature — flags are looked up from the pool at runtime via the
    # disability_blocks_* helpers, so the cat's record stays clean.
    description, _flags = random.choice(parsed)
    return description if description else None


def random_pet_response(species_id, cat_name):
    # No species-side cat fallback — third member of the cat-leak
    # family (after random_creature_name and random_description). When
    # the species id is missing from SPECIES_DATA (stale id on a
    # creature, etc.) we used to fall through to the module-level
    # PET_RESPONSES, which is mirrored from cat at startup. That
    # silently bled "{name} purrs softly" into other species. Empty
    # pool now returns "" — silent rather than wrong.
    species = SPECIES_DATA.get(species_id)
    pool = species.get("pet_responses", []) if species else []
    if not pool:
        return ""
    template = random.choice(pool)
    try:
        return template.format(name=cat_name)
    except (KeyError, IndexError):
        return template


# ===== User-editable text assets =====
# TEXT_DIR moved up top with the other path constants; see USER_DATA_DIR
# block. This stub kept here for orientation when scrolling the file.

# ===== Announcements live in tff_announcements.py =====
# The templates + formatter were lifted into their own file so a wording bug
# is one small file to look at. Pull every name across (including the
# _underscore helpers a plain star-import skips) so the unqualified
# references here and in the UI keep resolving. ANNOUNCEMENTS comes across as
# a reference to the SAME dict tff_announcements mutates in place -- never
# rebind it.
import tff_announcements
globals().update({_k: _v for _k, _v in vars(tff_announcements).items()
                  if not _k.startswith("__")})


_TEXT_FILE_HEADERS = {
    # Species-specific headers — resolved through _resolve_text_file_header
    # at write time. Available substitutions: {species_name} (lowercase,
    # e.g. "cat"), {species_name_cap} (capitalised, e.g. "Cat"),
    # {sex_label_female_paren} / {sex_label_male_paren} (a parenthetical
    # like " (sow)" or " (doe)" when the species uses a non-default sex
    # label, empty string when the species just uses "female" / "male").
    # The literal token {{name}} renders as `{name}` in the output — that
    # one is a *runtime* placeholder for the creature's name, not a
    # write-time substitution.
    "names_female.txt": (
        "Female {species_name}{sex_label_female_paren} names. One per line.\n"
        "Lines starting with # are ignored. Empty lines are skipped.\n"
        "Save the file and restart Time for Family to pick up changes."
    ),
    "names_male.txt": (
        "Male {species_name}{sex_label_male_paren} names. One per line.\n"
        "Lines starting with # are ignored. Empty lines are skipped.\n"
        "Save the file and restart Time for Family to pick up changes."
    ),
    "descriptions.txt": (
        "{species_name_cap} descriptions. One per line.\n"
        "Each new {species_name} is given a random description from this pool when born.\n"
        "Lines starting with # are ignored. Empty lines are skipped.\n"
        "Save the file and restart Time for Family to pick up changes."
    ),
    "pet_responses.txt": (
        "Pet responses. One per line. {{name}} is replaced with the {species_name}'s name.\n"
        "Picked at random each time you pet a {species_name}.\n"
        "Lines starting with # are ignored. Empty lines are skipped.\n"
        "Save the file and restart Time for Family to pick up changes."
    ),
    "colors.txt": (
        "Colors for {species_name_cap}s. One per line.\n"
        "\n"
        "Each new {species_name} is born with two colors — one inherited\n"
        "from each parent. If both parents are present in the save, the\n"
        "baby gets one color from each; otherwise both come from this\n"
        "pool. There's a small chance per color (Settings: Color\n"
        "mutation chance) that a baby gets a fresh roll from this pool\n"
        "instead of inheriting — like a recessive ancestor trait.\n"
        "\n"
        "Descriptions can reference {{color}} and {{color2}} — see\n"
        "descriptions.txt. Placeholders fill in at birth from the\n"
        "creature's two colors, so they stay stable forever after.\n"
        "Empty pool = no colors; the detail panel hides the Colour\n"
        "line rather than showing it blank.\n"
        "\n"
        "Lines starting with # are ignored. Empty lines are skipped.\n"
        "Save the file and restart Time for Family to pick up changes."
    ),
    "disabilities.txt": (
        "Disabilities — physical or sensory differences this species can be\n"
        "born with. One per line.\n"
        "\n"
        "This is a respectful representation feature, not a debuff. Disabled\n"
        "creatures pair, breed, age, and are loved like any other. The\n"
        "only systemic effect (when the wild ships) is that disabled\n"
        "creatures stay in the village rather than auto-emigrating —\n"
        "some folks need a home that knows them.\n"
        "\n"
        "Write factual, neutral language — descriptions, not pity-trips.\n"
        "Examples to start from (delete or keep): 'Born blind.',\n"
        "'Three legs.', 'Doesn't see well in dim light.'\n"
        "\n"
        "Optional flags after a description, separated by ' | '. Default\n"
        "behaviour applies when the flag is absent:\n"
        "  '| ok'         = this disability does NOT keep the creature in\n"
        "                   the village. They can auto-emigrate to the\n"
        "                   wild like undisabled creatures.\n"
        "                   (Default: they stay in the village.)\n"
        "  '| no_produce' = elder production is blocked for creatures with\n"
        "                   this disability.\n"
        "                   (Default: they produce normally.)\n"
        "Combine flags freely. Example: 'Heart condition. | no_produce'\n"
        "\n"
        "The roll fires at every creature's creation; chance is the species'\n"
        "disability_chance (set in the species spec or via the Mods menu's\n"
        "species editor). Default is 0 — opt in per species when you've\n"
        "decided what to write.\n"
        "\n"
        "Lines starting with # are ignored. Empty lines are skipped.\n"
        "Save the file and restart Time for Family to pick up changes."
    ),
    "items_common.txt": (
        "Common items found while digging in the park. One per line.\n"
        "Used as building materials for new rooms.\n"
        "Lines starting with # are ignored. Empty lines are skipped.\n"
        "Save the file and restart Time for Family to pick up changes."
    ),
    "items_uncommon.txt": (
        "Uncommon items found while digging in the park. One per line.\n"
        "Lines starting with # are ignored. Empty lines are skipped.\n"
        "Save the file and restart Time for Family to pick up changes."
    ),
    "objects.txt": (
        "Practical objects found while digging in the park.\n"
        "Format: name | description (one per line).\n"
        "Lines starting with # are ignored. Empty lines are skipped.\n"
        "Save the file and restart Time for Family to pick up changes."
    ),
    "treasures.txt": (
        "Rare named treasures found while digging in the park.\n"
        "Format: name | description (one per line).\n"
        "Lines starting with # are ignored. Empty lines are skipped.\n"
        "Save the file and restart Time for Family to pick up changes."
    ),
    "ambient.txt": (
        "Ambient observations — short, neutral one-liners the game says\n"
        "during quiet stretches when nothing else is happening. One per\n"
        "line.\n"
        "\n"
        "These fire on a slow timer (Settings: Ambient interval) and only\n"
        "if no other announcement has happened recently (Ambient quiet\n"
        "time). Toggle Tools → Ambient observations off to silence them\n"
        "entirely. Lines starting with # are ignored. Empty lines are\n"
        "skipped.\n"
        "\n"
        "Write whatever fits the tone of your save — short, gentle,\n"
        "noun-phrasey works best. The game doesn't substitute creature\n"
        "or room names into these (they're meant to be ambient — true\n"
        "of the world rather than tied to a particular character).\n"
        "\n"
        "Save the file and restart Time for Family to pick up changes."
    ),
}


def _resolve_text_file_header(fname, species_spec=None):
    """Look up the header for a text file and substitute species-aware
    placeholders. For park-wide files (items_common, treasures, ambient,
    etc.) the template has no placeholders and is returned unchanged. For
    per-species files the template is filled with the species' name and
    sex labels — so a hedgehog's auto-created names_female.txt reads
    'Female hedgehog (sow) names…' and a cat's reads 'Female cat names…'
    (no parenthetical when the sex label is just 'female' or 'male').
    """
    template = _TEXT_FILE_HEADERS.get(fname, "")
    if not template:
        return ""
    if species_spec is None or "{species_name}" not in template:
        # Park-wide file, or template has no species placeholders. Return
        # as-is — but if there's no spec, we can't safely call .format()
        # on a template that does have placeholders, so guard accordingly.
        return template
    species_name = (species_spec.get("name") or species_spec.get("id") or "creature").lower()
    species_name_cap = species_name.capitalize()
    fem_label = (species_spec.get("sex_label_female") or "female").strip().lower()
    mal_label = (species_spec.get("sex_label_male") or "male").strip().lower()
    fem_paren = "" if fem_label in ("female", "f", "") else f" ({fem_label})"
    mal_paren = "" if mal_label in ("male", "m", "") else f" ({mal_label})"
    return template.format(
        species_name=species_name,
        species_name_cap=species_name_cap,
        sex_label_female_paren=fem_paren,
        sex_label_male_paren=mal_paren,
    )


def _read_lines(path, defaults):
    if not path.exists():
        return list(defaults)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [
                line.strip() for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    except OSError:
        return list(defaults)
    return lines if lines else list(defaults)


def _read_named_lines(path, defaults_pairs):
    """Parse 'name | description' lines into a list of {name, description} dicts.
    `defaults_pairs` is a list of (name, description) tuples used as fallback.
    """
    fallback = [{"name": n, "description": d} for n, d in defaults_pairs]
    if not path.exists():
        return list(fallback)
    try:
        result = []
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    name, _, desc = line.partition("|")
                    name = name.strip()
                    desc = desc.strip()
                else:
                    name, desc = line, ""
                if name:
                    result.append({"name": name, "description": desc})
    except OSError:
        return list(fallback)
    return result if result else list(fallback)


def _write_default_file(path, header, defaults):
    with open(path, "w", encoding="utf-8") as f:
        for header_line in header.splitlines():
            f.write(f"# {header_line}\n")
        f.write("\n")
        for entry in defaults:
            if isinstance(entry, tuple):
                name, desc = entry
                if desc:
                    f.write(f"{name} | {desc}\n")
                else:
                    f.write(f"{name}\n")
            else:
                f.write(entry + "\n")


def ensure_user_data_dir():
    """Populate user_data/ from shipped assets/ on first launch (or after
    a folder gets nuked). Idempotent — only fills what's missing.

    Layout after this runs:

        assets/text/, assets/types/   read-only "factory" copies
        user_data/text/, .../types/   live, editable, what the game
                                       actually reads and writes

    A first-time player ends up with both. A returning player who
    pre-dates this refactor gets a one-time migration: their existing
    assets/text/ + assets/types/ (which they may have edited via the
    in-game tools) get copied to user_data/ so nothing is lost; from
    then on the editors only touch user_data/.

    The shipped assets/ tree is never written to by the game after
    this. Players who want to revert a species or room type to its
    factory default can just copy the file back from assets/.
    """
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    pairs = [
        (ASSETS_TEXT_DIR, TEXT_DIR),
        (ASSETS_TYPES_DIR, TYPES_DIR),
    ]
    for src, dest in pairs:
        if not src.exists():
            # No shipped templates to copy. The game's auto-creation
            # paths (ensure_text_assets, the species editor's Save)
            # will populate dest from defaults as needed.
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if not dest.exists():
            shutil.copytree(src, dest)
            continue
        # dest exists — fill in any subtrees the user is missing
        # without clobbering anything they have. shutil.copytree's
        # dirs_exist_ok needs Python 3.8+; TFF requires a newer
        # interpreter than that, so we can use it directly.
        for child in src.iterdir():
            target = dest / child.name
            if target.exists():
                continue
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)


def _migrate_flat_text_files():
    """Move pre-multi-species flat text files into assets/text/species/cat/.

    Older versions of Time for Family kept names_female.txt etc. directly in
    assets/text/. Now each species has its own subdirectory. If we see the
    old layout, move files into the cat subfolder once. Park-wide files
    (items_common, treasures, etc.) stay in assets/text/.
    """
    species_cat_dir = TEXT_DIR / "species" / "cat"
    species_cat_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("names_female.txt", "names_male.txt", "descriptions.txt", "pet_responses.txt"):
        old_path = TEXT_DIR / fname
        new_path = species_cat_dir / fname
        if old_path.exists() and not new_path.exists():
            old_path.rename(new_path)


def ensure_text_assets():
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_flat_text_files()

    # Park-wide text files (not species-specific)
    park_files = [
        ("items_common.txt", DEFAULT_ITEMS_COMMON),
        ("items_uncommon.txt", DEFAULT_ITEMS_UNCOMMON),
        ("objects.txt", DEFAULT_OBJECTS),
        ("treasures.txt", DEFAULT_TREASURES),
        ("ambient.txt", DEFAULT_AMBIENT),
    ]
    for fname, defaults in park_files:
        path = TEXT_DIR / fname
        if not path.exists():
            _write_default_file(path, _resolve_text_file_header(fname), defaults)

    # Per-species text files. Each species defines its own text_directory.
    # Headers go through _resolve_text_file_header so a freshly-modded
    # species (e.g. dragon) gets "Female dragon (queen) names" rather
    # than the inherited "Female cat names" the original code produced.
    species_files = [
        ("names_female.txt", []),
        ("names_male.txt", []),
        ("descriptions.txt", []),
        ("pet_responses.txt", []),
        ("disabilities.txt", []),
        ("colors.txt", []),
    ]
    for species in SPECIES_DATA.values():
        spec = species["spec"]
        text_dir_name = spec.get("text_directory", spec["id"])
        species_dir = TEXT_DIR / "species" / text_dir_name
        species_dir.mkdir(parents=True, exist_ok=True)
        for fname, defaults in species_files:
            path = species_dir / fname
            if not path.exists():
                _write_default_file(
                    path, _resolve_text_file_header(fname, spec), defaults,
                )


def load_text_assets():
    """Refresh runtime pools from the text files.

    Files are seeded with defaults on first call (via ensure_text_assets).
    Per-species pools live in SPECIES_DATA[<species>]["name_pool_f"] etc.
    Park-wide pools live in module-level ITEMS_COMMON, OBJECTS, etc.
    """
    ensure_text_assets()

    # Per-species pools
    for species in SPECIES_DATA.values():
        text_dir_name = species["spec"].get("text_directory", species["spec"]["id"])
        species_dir = TEXT_DIR / "species" / text_dir_name
        species["name_pool_f"] = _read_lines(species_dir / "names_female.txt", [])
        species["name_pool_m"] = _read_lines(species_dir / "names_male.txt", [])
        species["descriptions"] = _read_lines(species_dir / "descriptions.txt", [])
        species["pet_responses"] = _read_lines(species_dir / "pet_responses.txt", [])
        species["disabilities"] = _read_lines(species_dir / "disabilities.txt", [])
        species["colors"] = _read_lines(species_dir / "colors.txt", [])
        # Drop any cached parse so the next disability_blocks_* lookup
        # re-parses against the freshly-loaded pool. Cheap and avoids
        # stale flag data after a reload.
        species.pop("disabilities_parsed", None)

    # Mirror the cat species into the legacy module-level globals so any
    # code path that hasn't been migrated yet still finds something.
    cat = SPECIES_DATA.get("cat")
    if cat:
        NAMES_F[:] = cat.get("name_pool_f", [])
        NAMES_M[:] = cat.get("name_pool_m", [])
        DESCRIPTIONS[:] = cat.get("descriptions", [])
        PET_RESPONSES[:] = cat.get("pet_responses", [])

    # Park-wide pools
    ITEMS_COMMON[:] = _read_lines(TEXT_DIR / "items_common.txt", DEFAULT_ITEMS_COMMON)
    ITEMS_UNCOMMON[:] = _read_lines(TEXT_DIR / "items_uncommon.txt", DEFAULT_ITEMS_UNCOMMON)
    OBJECTS[:] = _read_named_lines(TEXT_DIR / "objects.txt", DEFAULT_OBJECTS)
    TREASURES[:] = _read_named_lines(TEXT_DIR / "treasures.txt", DEFAULT_TREASURES)
    AMBIENT_MOMENTS[:] = _read_lines(TEXT_DIR / "ambient.txt", DEFAULT_AMBIENT)

    # Configurable announcement templates (assets/text/announcements.txt).
    # Loads after ensure_text_assets() seeds the file on first run.
    load_announcements(TEXT_DIR)


def load_types():
    """Read every JSON in user_data/types/species and user_data/types/
    room_types and re-populate SPECIES_DATA + ROOM_TYPES.

    **Always finishes by calling `load_text_assets()`** so per-species
    text pools (`name_pool_f`, `name_pool_m`, `descriptions`,
    `pet_responses`, `disabilities`, `colors`) get re-loaded into the
    fresh `SPECIES_DATA` entries. This pairing is non-negotiable: the
    species editor and the room-type editor both call `load_types()`
    after a save, and if the text-asset reload were ever skipped,
    every species in memory would have empty pools — name/description
    rolls would fall back to placeholders (or, before May 2026 session
    4, the cat-default lists). That bug bit AI: editing the "server"
    room type cleared SPECIES_DATA["ai"]["name_pool_m"], the next
    breeding pulled cat names from the fallback, and a whole litter
    of AI babies got stamped "Snowy / Coco / Otis / Daisy". Folding
    the call in here makes the foot-gun unreachable.
    """
    SPECIES_DIR.mkdir(parents=True, exist_ok=True)
    ROOM_TYPES_DIR.mkdir(parents=True, exist_ok=True)
    SPECIES_DATA.clear()
    for path in sorted(SPECIES_DIR.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            sid = data.get("id")
            if sid:
                SPECIES_DATA[sid] = {"spec": data}
        except (OSError, json.JSONDecodeError):
            continue
    ROOM_TYPES.clear()
    for path in sorted(ROOM_TYPES_DIR.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rid = data.get("id")
            if rid:
                ROOM_TYPES[rid] = data
        except (OSError, json.JSONDecodeError):
            continue
    load_text_assets()


def append_name_to_file(name, sex, species_id="cat"):
    """Append `name` to the species' names_female.txt or names_male.txt if
    not already in the pool. Returns True on file change, False otherwise.
    """
    if not name:
        return False
    species = SPECIES_DATA.get(species_id, {})
    text_dir_name = species.get("spec", {}).get("text_directory", species_id)
    species_dir = TEXT_DIR / "species" / text_dir_name
    if sex == "F":
        path = species_dir / "names_female.txt"
        pool = species.get("name_pool_f", NAMES_F)
    else:
        path = species_dir / "names_male.txt"
        pool = species.get("name_pool_m", NAMES_M)
    if name in pool:
        return False
    needs_leading_newline = False
    try:
        if path.exists() and path.stat().st_size > 0:
            with open(path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) not in (b"\n", b"\r"):
                    needs_leading_newline = True
        with open(path, "a", encoding="utf-8") as f:
            if needs_leading_newline:
                f.write("\n")
            f.write(name + "\n")
    except OSError:
        return False
    pool.append(name)
    return True


# ===== Data model =====

def new_creature(species_id, sex, pair_id=None, name=None, age_seconds=None,
                 parent_pair_id=None, description=None, disability_chance_mult=1.0,
                 parents=None):
    """Build a fresh creature dict for the given species and sex.

    Pulls starter age range (in real seconds), name pool, and description
    pool from the species' loaded JSON spec / text files. Also rolls the
    species' `disability_chance` — if it hits, the creature gets a
    `disability` string describing a physical or sensory difference. See
    `maybe_disability()` for the design intent: disability is purely
    representational and never gates breeding, pairing, affection, or
    care. The hook for keeping disabled creatures in the village
    rather than emigrating to the wild lives in future code.

    `disability_chance_mult` is forwarded to the disability roll. Used
    by breeding code to apply inbreeding depression when parents are
    related (see `are_related`).
    """
    species = SPECIES_DATA.get(species_id, {})
    spec = species.get("spec", {})
    if age_seconds is None:
        lo, hi = species_starter_age_range_seconds(spec)
        # Respect specs that left the starter range zero by falling back
        # to a small fresh-creature default (~25-60 minutes of real time).
        if hi <= 0:
            lo, hi = 1500.0, 3600.0
        age_seconds = random.uniform(lo, hi)
    colors = roll_creature_colors(species_id, parents=parents)
    cat = {
        "id": str(uuid.uuid4())[:8],
        "name": name or random_creature_name(species_id, sex),
        "species": species_id,
        "sex": sex,
        "affection": 0.5,
        "age_seconds": float(age_seconds),
        "pair_id": pair_id,
        "parent_pair_id": parent_pair_id,
        "description": (
            description if description is not None
            else random_description(species_id, colors=colors)
        ),
    }
    if colors:
        cat["colors"] = colors
    disability = maybe_disability(species_id, chance_mult=disability_chance_mult)
    if disability:
        cat["disability"] = disability
    return cat


def new_cat(sex, pair_id=None, name=None, age_seconds=None, parent_pair_id=None,
            description=None, disability_chance_mult=1.0):
    """Legacy alias. Kept so existing call sites keep working."""
    return new_creature("cat", sex, pair_id=pair_id, name=name, age_seconds=age_seconds,
                        parent_pair_id=parent_pair_id, description=description,
                        disability_chance_mult=disability_chance_mult)


def are_related(a, b):
    """True if `a` and `b` are full siblings (same parent_pair_id), share
    any parent (half-siblings), or one is the other's direct parent.

    Catches first-degree relations only — doesn't walk further up the
    family tree, so cousins, grandparent-grandchild, and aunt-niece
    pairings read as unrelated. Good enough for inbreeding-depression
    modeling at the closeness levels that matter most biologically."""
    pa = a.get("parent_pair_id")
    pb = b.get("parent_pair_id")
    if pa is not None and pb is not None and pa == pb:
        return True  # full siblings (same parent pair)

    a_parents = set(a.get("parent_ids") or [])
    b_parents = set(b.get("parent_ids") or [])
    if a_parents & b_parents:
        return True  # half-siblings (share at least one parent)

    a_id = a.get("id")
    b_id = b.get("id")
    if a_id and a_id in b_parents:
        return True  # a is b's parent
    if b_id and b_id in a_parents:
        return True  # b is a's parent

    return False


def seed_village_pair(state, species_id):
    """Drop a single female + male pair of `species_id` into the village.

    Used by SpeciesDialog when the player picks a species to bring home,
    creates a new one, or auto-fires from the first-launch picker.
    Without this, the species exists as a spec but has no living
    creatures anywhere, so the player can't actually play with it.
    Each villager gets a random age within the species' starter
    range so they don't all hit life-stage transitions at the same
    instant.

    Returns True if the pair was added, False if `species_id` isn't a
    known species (caller probably hit a save error and we shouldn't
    quietly succeed).
    """
    species_spec = SPECIES_DATA.get(species_id, {}).get("spec")
    if not species_spec:
        return False
    # Mods → Add Species drops a baby pair into the village, matching the
    # new-game seed behavior (see new_state). Player adopts, waits for them
    # to mature (breeding_age_seconds, in real time), then breeds. The
    # asymmetry where mid-game additions used to spawn as breeding-ready
    # adults was a usability quirk, not a design intent.
    breeding_age = float(species_spec.get("breeding_age_seconds", 0) or 0)
    pace = lifecycle_pace()
    village = state.setdefault("village", [])
    now = time.time()
    for sex in ("F", "M"):
        # Spawn as truly newborn — they grow up in real time per the
        # species' breeding_age_seconds (scaled by lifecycle_pace), and
        # `mature_at` below is the authoritative grow-up time. The
        # previous random.uniform(0, 3600) age range overshot maturity
        # for fast-cycle species (mouse breeding_age_seconds = 600 meant
        # most "babies" arrived already past maturity) and was a tiny
        # fraction of life for slow species — inconsistent and at odds
        # with the babies-only watch-them-grow intent.
        villager = new_creature(species_id, sex, age_seconds=0.0)
        # Village creatures aren't half of any pair — they're free to be
        # adopted independently and re-paired in whichever room they end
        # up in.
        villager["pair_id"] = None
        villager["moved_to_village_at"] = now
        if breeding_age > 0:
            villager["mature_at"] = now + breeding_age * pace
        village.append(villager)
    return True


def new_state():
    """Returns an *empty* park: no rooms, no village creatures, no
    expecting pairs, no inventory. The first-launch SpeciesDialog
    (and the File → Species menu) is responsible for putting
    something in the village so the player has someone to adopt.

    Empty-by-default replaces the previous "every species pre-seeded
    with babies" behaviour. Pre-seeding pulled the player toward
    completionism and made every shared room type (e.g. outdoor)
    crowded with three or four species at once — both bad for
    accessibility and for the cozy intent of the game.
    """
    return {
        "version": 1,
        "last_tick": time.time(),
        "rooms": [],
        # Expecting records — pairs whose breeding succeeded but whose
        # babies haven't been born yet. Each record is a dict with
        # from_pair, room_id, species, babies (pre-rolled at conception),
        # conceived_at, due_at, no_room_warned. process_expecting()
        # converts ripe records into placed creatures (in rooms or
        # village). For species with gestation_seconds=0 (the default),
        # due_at == conception time and the next tick places the
        # babies immediately.
        "expecting": [],
        "village": [],
        # The "village" is the player-facing place where un-adopted /
        # waiting / disability-staying creatures live. Its display name
        # is renameable from the Village tab — the dict key stays as
        # "village" because it's an internal identifier.
        "village_name": "Village",
        "pair_progress": {},
        "last_breed_per_pair": {},
        "next_pair_num": 1,
        "settings": dict(DEFAULT_SETTINGS),
        "inventory": {"common": {}, "uncommon": {}, "objects": {}, "treasures": {}},
        "last_dig_date": "",
        "digs_used_today": 0,
        "next_room_num": 1,
        "seen_help": False,
    }


def state_is_fresh(state):
    """True when a state has no rooms, no village creatures, no expecting
    (gestating) pairs, and no remembered (wild-emigrated) creatures.
    Used to decide whether to auto-open the SpeciesDialog at launch /
    after Reset park. (The pre-redesign state["baskets"] key isn't
    checked because load_state migrates any entries it finds into
    expecting records and clears the legacy key.)
    """
    return (
        not state.get("rooms")
        and not state.get("village")
        and not state.get("expecting")
        and not state.get("remembered")
    )


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            state = new_state()
    else:
        state = new_state()
    state.setdefault("village", [])
    state.setdefault("village_name", "Village")
    # Pairs whose breeding succeeded but whose babies haven't been born
    # yet (gestation phase, added late May 2026). Pre-feature saves
    # initialise this to an empty list; gestation_seconds defaults to 0
    # for shipped/legacy species so attempt_breed still produces an
    # immediate birth as before.
    state.setdefault("expecting", [])
    # Persistent list of creatures that left for the wild. Each entry is
    # a small dict (name, species, left_at, age_seconds_at_leaving). The
    # Stats section's "In memory" panel reads this; nothing else mutates
    # it except the wild-emigration pass when it removes a creature.
    state.setdefault("remembered", [])
    state.setdefault("pair_progress", {})
    state.setdefault("last_breed_per_pair", {})
    state.setdefault("next_pair_num", 1)
    state.setdefault("settings", {})
    inventory = state.setdefault("inventory", {})
    inventory.setdefault("common", {})
    inventory.setdefault("uncommon", {})
    # Objects and treasures used to be stored as flat lists where every
    # find appended a new dict — so 3 cozy baskets meant 3 list entries
    # cluttering the inventory. They're now stored as
    # {name: {"count": N, "description": "..."}} dicts, the same shape
    # as the common/uncommon item buckets. Migrate older saves on load.
    inventory["objects"] = _migrate_collectibles(inventory.get("objects"))
    inventory["treasures"] = _migrate_collectibles(inventory.get("treasures"))
    state.setdefault("last_dig_date", "")
    state.setdefault("digs_used_today", 0)
    state.setdefault("next_room_num", len(state.get("rooms", [])) + 1)
    # Existing saves are treated as "already seen the how-to-play" — only
    # genuinely fresh new_state() launches with seen_help=False.
    state.setdefault("seen_help", True)
    for room in state.get("rooms", []):
        room.setdefault("meter_last_refilled", {})
        room.setdefault("type", "indoor")
        # Reconcile the room's allowed_species against the species-derived
        # compatible-species list for this room type. Two goals here, in
        # tension:
        #  1. Preserve any per-instance narrowing the player set (so if
        #     they said "this room only allows cats" out of a type that
        #     allows [cat, hamster], that narrowing survives reloads).
        #  2. Prune species whose specs no longer claim this room type
        #     (so a room can't allow species the species itself doesn't
        #     say it lives in).
        # Resolution: intersect existing allowed_species with type compat.
        # If allowed_species is missing entirely (pre-feature save, or new
        # migration), fall back to the full type compat list.
        type_compat = room_type_compatible_species(room["type"])
        existing = room.get("allowed_species")
        if existing is None:
            room["allowed_species"] = list(type_compat) if type_compat else ["cat"]
        else:
            intersected = [s for s in existing if s in type_compat]
            if intersected:
                room["allowed_species"] = intersected
            elif type_compat:
                # Empty intersection (e.g., type was edited and nothing
                # the user picked is still supported). Reset to full
                # compat rather than silently locking the room down.
                room["allowed_species"] = list(type_compat)
            else:
                room["allowed_species"] = ["cat"]
        for cat in room.get("creatures", []):
            cat.setdefault("species", "cat")
            cat.setdefault("parent_pair_id", None)
            if not cat.get("description"):
                # random_description's species_id default is "cat", which
                # silently leaked cat descriptions ("A sleepy tabby…")
                # onto every non-cat creature missing a description in a
                # legacy save. Pass the creature's actual species so the
                # backfill draws from the right pool (or returns "" if
                # that species' pool is empty).
                cat["description"] = random_description(
                    cat.get("species", "cat"),
                )
    for cat in state.get("village", []):
        if not cat.get("description"):
            cat["description"] = random_description(
                cat.get("species", "cat"),
            )
    sync_settings_from_state(state)
    # One-shot migration: pre-redesign saves stored creature ages in
    # game-days (`age_days`). The redesign uses `age_seconds` directly.
    # Convert and drop the old key so the rest of the code only sees one
    # field. Using SETTINGS["seconds_per_game_day"] (synced from state
    # above) preserves the original time scale a player was using.
    legacy_spgd = float(SETTINGS.get("seconds_per_game_day", 3600) or 3600)
    def _migrate_age(cat):
        if "age_days" in cat and "age_seconds" not in cat:
            try:
                cat["age_seconds"] = float(cat["age_days"]) * legacy_spgd
            except (TypeError, ValueError):
                cat["age_seconds"] = 0.0
        cat.pop("age_days", None)
        # Backfill life-stage announcement stamps so creatures that were
        # already elder/retired BEFORE the milestone-announcement feature
        # shipped don't all fire announcements on the first check after
        # upgrade. A player loading a save with 30 elders shouldn't hear
        # 30 "is now an elder" lines on launch — those transitions
        # happened before the feature existed.
        if is_too_old_to_breed(cat):
            cat.setdefault("elder_announced", True)
            cat.setdefault("retired_announced", True)
        elif is_elder(cat):
            cat.setdefault("elder_announced", True)

    def _migrate_colors(cat):
        # One-time roll for creatures that pre-date the colors feature.
        # Once stamped, this never re-rolls — the creature's colors are
        # part of who they are. Species with an empty colors.txt skip
        # the stamp entirely (the detail panel just hides the line).
        if "colors" in cat and cat["colors"]:
            return
        rolled = roll_creature_colors(cat.get("species", "cat"))
        if rolled:
            cat["colors"] = rolled

    for room in state.get("rooms", []):
        for cat in room.get("creatures", []):
            _migrate_age(cat)
            _migrate_colors(cat)
    for cat in state.get("village", []):
        _migrate_age(cat)
        _migrate_colors(cat)
    # Repair villagers whose age was frozen at 0 during an offline
    # catch-up birth. The catch-up loop used to set moved_to_village_at
    # to the simulated birth moment (potentially well in the past) while
    # baby_to_cat hard-coded age_seconds = 0 — so a baby born 30 minutes
    # into an away window showed "Age 0 / In village for 30m" forever
    # after. baby_to_cat is fixed prospectively, but creatures already
    # carrying the bad data need a one-shot snap. Anyone in the village
    # who's been there longer than their recorded age gets their age
    # snapped up to match the time they've been in the village — the
    # only way to land in that state is the catch-up bug.
    now = time.time()
    for cat in state.get("village", []):
        moved_at = cat.get("moved_to_village_at")
        if not moved_at:
            continue
        in_village = max(0.0, now - float(moved_at))
        if cat_age_seconds(cat) < in_village:
            cat["age_seconds"] = in_village
            cat.pop("age_days", None)
    # Migrate any pre-redesign basket records into expecting records
    # with due_at = now. The next process_expecting tick (which runs
    # immediately during apply_elapsed_time) places the babies as real
    # creatures. The old state["baskets"] key is the only place this
    # code knows "basket" — everything downstream is the new
    # expecting / birth model. Color-migration happens here too since
    # the babies might pre-date the colors feature.
    legacy_basket_records = state.get("baskets") or []
    if legacy_basket_records:
        now = time.time()
        for legacy in legacy_basket_records:
            for baby in legacy.get("babies", []):
                _migrate_colors(baby)
            babies = legacy.get("babies") or []
            species_id = (
                babies[0].get("species", "cat") if babies else "cat"
            )
            state.setdefault("expecting", []).append({
                "id": legacy.get("id") or str(uuid.uuid4())[:8],
                "from_pair": legacy.get("from_pair"),
                "room_id": legacy.get("room_id"),
                "species": species_id,
                "babies": babies,
                "conceived_at": legacy.get("created_at", now),
                "due_at": now,  # ripe immediately on next tick
                "no_room_warned": False,
            })
    state["baskets"] = []
    return state


def sync_settings_from_state(state):
    """Pull persisted settings from state into the live SETTINGS dict.

    Unknown keys in state["settings"] are ignored; missing keys keep
    their default values.
    """
    saved = state.get("settings", {})
    for key, default in DEFAULT_SETTINGS.items():
        value = saved.get(key, default)
        try:
            value = type(default)(value)
        except (TypeError, ValueError):
            value = default
        SETTINGS[key] = value


def save_state(state):
    tmp = STATE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def meter_decay_seconds_for(meter_spec):
    """Return the decay time (seconds, full->empty) for a meter spec.

    Per-meter decay_seconds overrides the global full_decay_seconds. Falls
    back to full_decay_seconds when the meter has no override or the value
    is invalid. Always returns at least 1 to prevent div-by-zero.
    """
    raw = meter_spec.get("decay_seconds") if isinstance(meter_spec, dict) else None
    if isinstance(raw, (int, float)) and raw > 0:
        return max(1, int(raw))
    return max(1, int(SETTINGS.get("full_decay_seconds", 3600)))


def apply_elapsed_time(state):
    now = time.time()
    elapsed = max(0.0, now - state.get("last_tick", now))
    # last_tick is advanced at the END of this function (not here at the
    # top) so that if any catch-up pass throws, the elapsed window still
    # gets applied on the next launch instead of being silently lost.
    # Each pass is wrapped in try/except below for the same reason — one
    # bad pass shouldn't poison the others.
    affection_decay = elapsed / max(1, int(SETTINGS.get("affection_decay_seconds", 3600)))
    for room in state["rooms"]:
        type_spec = ROOM_TYPES.get(room.get("type", "indoor"), {})
        meter_lookup = {m["key"]: m for m in type_spec.get("meters", [])}
        for meter in list(room["meters"].keys()):
            # Each meter has its own decay rate (per-meter override falls
            # back to global full_decay_seconds when not set).
            meter_decay = elapsed / meter_decay_seconds_for(meter_lookup.get(meter, {}))
            room["meters"][meter] = max(0.0, room["meters"][meter] - meter_decay)
        # Affection decays for room creatures only — pet them or it drifts
        # toward 0. Village creatures are left alone since you can't pet
        # them and decaying their affection would steadily kill offscreen
        # breeding chances.
        for cat in room["creatures"]:
            cat["affection"] = max(0.0, cat.get("affection", 0.5) - affection_decay)
    # Also age every creature by elapsed real time — village creatures
    # age too, they're living their lives just not in the player's home.
    # lifecycle_pace divides the elapsed wall-clock seconds so a value of
    # 0.5 means each real second counts as 2 seconds of aging (twice as
    # fast); 2.0 means each real second counts as 0.5 (twice as slow).
    age_delta = elapsed / lifecycle_pace()
    if age_delta > 0:
        for room in state["rooms"]:
            for cat in room["creatures"]:
                cat["age_seconds"] = cat_age_seconds(cat) + age_delta
                cat.pop("age_days", None)
        for cat in state.get("village", []):
            cat["age_seconds"] = cat_age_seconds(cat) + age_delta
            cat.pop("age_days", None)
    # Each catch-up pass is wrapped in its own try/except: a crash in
    # one (e.g., a corrupted creature record) shouldn't stop the others
    # from running, AND shouldn't prevent last_tick from being advanced
    # below — without that, the same crashing pass would re-replay the
    # same elapsed window every relaunch.
    def _safe(key, fn, *args, **kwargs):
        try:
            state[key] = fn(*args, **kwargs)
        except Exception:
            # Drop the key entirely. The MainFrame announcers all do
            # state.pop(key, None) and short-circuit on a falsy result,
            # so a missing key cleanly skips the announcement instead
            # of unpacking `[]` into a tuple expectation.
            state.pop(key, None)
    # Catch up elder production after aging so creatures who crossed the
    # elder threshold during the away period are correctly handled. The
    # helper pure-mutates state["inventory"] and creature timestamps;
    # caller can read state["_offline_production"] to surface what
    # happened in a "while you were away" announcement.
    _safe("_offline_production", elder_production_pass, state, now=now)
    # Pair formation accumulates during the away period too — without
    # this, the timer only ticks while the game window is foreground,
    # which (for someone who closes the game between sessions) makes
    # pairs effectively never form. progress_pairing returns newly
    # formed pairs so we can announce them on relaunch.
    _safe("_offline_pairs", progress_pairing, state, elapsed)
    # Auto-breeding catch-up runs after pair formation so any newly
    # formed pair gets a chance to breed during the same offline
    # window. No-op when AUTO_BREEDING is off — matches live behaviour.
    _safe("_offline_breeding", auto_breed_offline_catchup, state, elapsed)
    # Independently mature any expecting records whose due_at has
    # passed. Covers the case where AUTO_BREEDING was off (the catchup
    # above early-returned and didn't process_expecting), so the
    # player who manually bred a pair, closed the game, and came back
    # after gestation completed still sees their babies arrive. When
    # AUTO_BREEDING is on, the catchup loop already matured records
    # during its iteration and this final pass usually finds nothing
    # — that's fine, returns an empty list.
    _safe("_offline_births", process_expecting, state)
    # Clear any mother-dependency tethers whose dependent_until passed
    # during the away period. No announcement (consistent with the
    # live tick): babies become independent quietly, and the player
    # discovers it the next time they try to move one solo.
    _safe("_offline_dep_cleared", clear_expired_dependencies, state)
    # Life-stage transitions BEFORE wild emigration so a creature that
    # crossed max-breeding-age while away first gets the "retired"
    # announcement, then becomes eligible for wild emigration in the
    # same offline pass. Each list is one-shot per creature.
    _safe("_offline_life_stages", life_stage_transitions_pass, state)
    # Wild emigration deliberately does NOT run during offline catch-up.
    # Earlier behavior compounded the per-check chance over the offline
    # window — closing the game for a day (24 chances at 5%/hr) gave
    # every elder a ~71% chance of being gone on return, which broke
    # the cozy promise: come back to a depopulated park you didn't get
    # to say goodbye to. Emigration now only happens during active play
    # (one creature at a time, with the player there to see it and the
    # announcement firing). Players who want offline emigration can
    # re-enable by adding the call back here — keeping the call site
    # commented rather than deleted to make that obvious.
    # _safe("_offline_emigration", wild_emigration_pass, state, elapsed_seconds=elapsed)
    # Disabled-retiree sanctuary moves still need to happen offline
    # though — without it, a disabled creature stays in their room
    # forever rather than retiring to the village. Run with
    # elapsed_seconds=0 so the wild-emigration chance compounds to
    # zero but the disability-blocks-emigration branch still moves
    # disabled retirees into the village.
    _safe("_offline_sanctuary", wild_emigration_pass, state, elapsed_seconds=0)
    # Advance last_tick LAST so that if any of the passes above raised
    # (and we caught it), the elapsed window is still consumed normally
    # — but if Python itself bails (KeyboardInterrupt, OOM, etc.) before
    # this line, the next launch replays the window instead of silently
    # losing it.
    state["last_tick"] = now


def find_room(state, room_id):
    # Returns None when no room matches (e.g. a stale UI panel still
    # holding the id of a room that was removed by a room-type delete).
    # Callers that pass a known-good id can index the result directly;
    # callers handling possibly-stale ids guard for None.
    return next((r for r in state["rooms"] if r["id"] == room_id), None)


# Reasons returned by find_room_for_species. Strings are stable-ish for
# message dispatch elsewhere — don't rename without updating callers.
PLACEMENT_PRIMARY               = "primary"
PLACEMENT_SPILL_PRIMARY_FULL    = "spilled_primary_full"
PLACEMENT_SPILL_PRIMARY_DENIES  = "spilled_primary_denies"
PLACEMENT_VILLAGE_NO_ROOM       = "village_no_room_allows"
PLACEMENT_VILLAGE_NO_SPACE      = "village_no_space"


def find_room_for_species(rooms, species_id, primary_room_id=None,
                          sim_used=None):
    """Pick the best room for a creature of `species_id`, following the
    same priority everywhere this question comes up:

      1. The primary room — if it allows the species AND has a free slot.
      2. Any other room that allows the species AND has a free slot.
      3. None — caller falls back to the village.

    Returns (room_dict_or_None, reason_string). The reason distinguishes
    between "primary is full" and "primary doesn't allow this species" so
    callers can show the player a message that matches what actually
    happened. Reasons are the PLACEMENT_* constants above.

    `sim_used` is an optional `{room_id: int}` of simulated occupancy —
    useful when planning to place several creatures in one pass (so the
    first baby filling a 4-slot room correctly bumps the second one
    elsewhere). When omitted, live `len(creatures)` is used.

    `primary_room_id` is optional. With None, every room is "other" and
    the spill reasons fall back to PRIMARY_FULL semantics; in practice
    callers always pass a primary (the mother's room, the source room
    being narrowed, etc.).
    """
    primary_room = None
    if primary_room_id is not None:
        primary_room = next(
            (r for r in rooms if r["id"] == primary_room_id), None,
        )

    def used(r):
        if sim_used is not None:
            return sim_used.get(r["id"], 0)
        return len(r["creatures"])

    primary_allows = (
        primary_room is not None
        and species_id in (primary_room.get("allowed_species") or [])
    )
    primary_has_space = (
        primary_room is not None
        and used(primary_room) < primary_room["slot_count"]
    )

    if primary_allows and primary_has_space:
        return primary_room, PLACEMENT_PRIMARY

    # Search other rooms for one that allows the species and has space.
    other_allowing = [
        r for r in rooms
        if r is not primary_room
        and species_id in (r.get("allowed_species") or [])
    ]
    for r in other_allowing:
        if used(r) < r["slot_count"]:
            if primary_room is not None and not primary_allows:
                return r, PLACEMENT_SPILL_PRIMARY_DENIES
            return r, PLACEMENT_SPILL_PRIMARY_FULL

    # Nothing fits → village. Distinguish "no room allows this species"
    # (educational — tells the player they need to allow it somewhere or
    # accept the village) vs "every room that allows is full" (capacity
    # problem — could be solved with a slot expansion or a new room).
    if not other_allowing and not primary_allows:
        return None, PLACEMENT_VILLAGE_NO_ROOM
    return None, PLACEMENT_VILLAGE_NO_SPACE


# ===== Game actions =====

def refill_meter(state, room_id, meter):
    room = find_room(state, room_id)
    room["meters"][meter] = 1.0
    room.setdefault("meter_last_refilled", {})[meter] = time.time()


def pet_cat(state, room_id, cat_id):
    room = find_room(state, room_id)
    if room is None:
        return None
    for cat in room["creatures"]:
        if cat["id"] == cat_id:
            cat["affection"] = min(1.0, cat.get("affection", 0.5) + 0.05)
            return cat
    return None


def move_creature_to_room(state, source_room_id, dest_room_id, cat_id):
    """Move a creature from one room to another.

    Pairs SURVIVE the move: the moved creature keeps its `pair_id`, and
    the partner left behind keeps theirs too. They're a couple even
    while temporarily in different rooms — they just can't breed until
    they're reunited. Pairs only dissolve when one half dies — see
    `_release_partner` in `wild_emigration_pass`.

    Mother-dependency is enforced as a tethered group: when a creature
    being moved has babies whose `dependent_on` points at them and
    those babies are in the source room, the babies move with her and
    the destination must have room for everyone. Moving a *dependent*
    baby alone (whose mother is in the source room) is refused with
    `"is_dependent"` so the caller can show a friendly redirect
    message ("move {Mother} instead"). Once `dependent_until` has
    passed, the tick clears `dependent_on` and the baby moves freely.

    Returns `(moved_creature, reason)` where reason is None on success
    or one of the strings: `"no_source"`, `"no_dest"`, `"not_found"`,
    `"dest_full"`, `"is_dependent"`. Callers use the reason to choose
    the right message.
    """
    if source_room_id == dest_room_id:
        return None, "no_dest"
    # find_room raises StopIteration on miss (it uses next() over a
    # generator) — guard both lookups so a stale/invalid id from a
    # background tick doesn't crash the whole tick.
    try:
        source = find_room(state, source_room_id)
    except StopIteration:
        return None, "no_source"
    try:
        dest = find_room(state, dest_room_id)
    except StopIteration:
        return None, "no_dest"
    if source is None:
        return None, "no_source"
    if dest is None:
        return None, "no_dest"
    cat = next((c for c in source["creatures"] if c["id"] == cat_id), None)
    if cat is None:
        return None, "not_found"
    # Refuse moving a dependent baby alone — the mother stays in the
    # source room and we don't want to split them. The caller should
    # redirect the user to move the mother instead. If the mother
    # isn't in the source room (e.g., a birth-time spillover landed
    # the baby in a different room from mom), the tether is moot for
    # move purposes and the move is allowed.
    dep_on = cat.get("dependent_on")
    if dep_on:
        mother_in_source = any(
            c.get("id") == dep_on for c in source["creatures"]
        )
        if mother_in_source:
            return None, "is_dependent"
    # Find any dependents of this cat in the source room. They move
    # with the cat as a group. (Dependents in other rooms are out of
    # scope for this move — the move logic operates on what's in
    # source.)
    dependents = [
        c for c in source["creatures"]
        if c.get("dependent_on") == cat_id
    ]
    group_size = 1 + len(dependents)
    if len(dest["creatures"]) + group_size > dest["slot_count"]:
        return None, "dest_full"
    # Pop the cat + dependents from source, append to dest. Order
    # preserved so the cat itself remains the "primary" return.
    source["creatures"] = [
        c for c in source["creatures"]
        if c["id"] != cat_id and c.get("dependent_on") != cat_id
    ]
    dest["creatures"].append(cat)
    for dep in dependents:
        dest["creatures"].append(dep)
    return cat, None


def expand_room_slots(state, room_id, count=1):
    """Add `count` slots to a room. Returns the new slot_count."""
    room = find_room(state, room_id)
    room["slot_count"] = room.get("slot_count", 0) + count
    return room["slot_count"]


def _consume_collectible(state, section_key, name):
    """Decrement one count of `name` in inventory[section_key]. Removes
    the key entirely when the count hits zero. Returns a dict with the
    name + description on success, or None if the item isn't in stock.
    """
    section = state.get("inventory", {}).get(section_key, {})
    if not isinstance(section, dict):
        return None
    slot = section.get(name)
    if not slot or int(slot.get("count", 0)) <= 0:
        return None
    description = slot.get("description", "")
    slot["count"] = int(slot["count"]) - 1
    if slot["count"] <= 0:
        del section[name]
    return {"name": name, "description": description}


def consume_treasure(state, name):
    """Remove one treasure of `name` from inventory. Returns a
    {name, description} dict on success, or None if none in stock.
    """
    return _consume_collectible(state, "treasures", name)


def consume_object(state, name):
    """Remove one object of `name` from inventory. Returns a
    {name, description} dict on success, or None if none in stock.
    """
    return _consume_collectible(state, "objects", name)


def move_creature_to_village(state, room_id, cat_id):
    """Move an adult creature from a room to the village.

    Pairs SURVIVE the move (same model as `move_creature_to_room`): the
    moved creature keeps its `pair_id`, the partner left in the room
    keeps theirs. They're still a couple — they just can't breed across
    locations (eligible_pairs is room-scoped, and the village doesn't
    breed via pair logic at all). When the player adopts the village
    half back into a room and the partner is also there, breeding
    resumes without a fresh bonding period. Pairs only dissolve on
    death / wild emigration — see `_release_partner` in
    `wild_emigration_pass`.

    Mother-dependency works the same way as in `move_creature_to_room`:
    moving a mother brings all her source-room dependents along, and
    moving a dependent baby alone is refused. The village has no slot
    cap, so dest_full never fires here.

    Returns `(moved_creature, reason)` where reason is None on success
    or `"no_source"` / `"not_found"` / `"is_dependent"`.
    """
    try:
        room = find_room(state, room_id)
    except StopIteration:
        return None, "no_source"
    if room is None:
        return None, "no_source"
    cat = next((c for c in room["creatures"] if c["id"] == cat_id), None)
    if cat is None:
        return None, "not_found"
    dep_on = cat.get("dependent_on")
    if dep_on:
        mother_in_source = any(
            c.get("id") == dep_on for c in room["creatures"]
        )
        if mother_in_source:
            return None, "is_dependent"
    dependents = [
        c for c in room["creatures"]
        if c.get("dependent_on") == cat_id
    ]
    room["creatures"] = [
        c for c in room["creatures"]
        if c["id"] != cat_id and c.get("dependent_on") != cat_id
    ]
    now = time.time()
    cat["moved_to_village_at"] = now
    state.setdefault("village", []).append(cat)
    for dep in dependents:
        dep["moved_to_village_at"] = now
        state.setdefault("village", []).append(dep)
    return cat, None


def eligible_pairs(state, room_id):
    """Return pair_ids that are M+F, both members mature and not retired."""
    pairs = {}
    for cat in find_room(state, room_id)["creatures"]:
        pid = cat.get("pair_id")
        if not pid:
            continue
        info = pairs.setdefault(pid, {"sexes": set(), "all_eligible": True})
        info["sexes"].add(cat["sex"])
        if not is_mature(cat) or is_too_old_to_breed(cat):
            info["all_eligible"] = False
    return [
        pid for pid, info in pairs.items()
        if "M" in info["sexes"] and "F" in info["sexes"] and info["all_eligible"]
    ]


def attempt_breed(state, room_id, now=None):
    """Returns (status, payload) where status is one of:
    'conceived' | 'fail' | 'no_pairs' | 'all_young' |
    'still_bonding' | 'still_growing' | 'all_resting' | 'low_care'.

    On 'conceived', payload is the expecting record (added to
    state["expecting"]). process_expecting() places the babies as
    real creatures in rooms (or village if no compatible room has
    space) when due_at passes. For species with `gestation_seconds`
    of 0, due_at == now and the caller can call process_expecting
    inline to place the babies in the same call. For non-zero
    gestation, the player gets an "expecting" announcement first and
    placement happens later via the next process_expecting tick.

    'still_bonding' fires when the room has unpaired-but-eligible
    M and F of the same species who haven't yet crossed the
    pair_formation_seconds threshold. 'still_growing' fires when the
    only candidate couples in the room are too young to bond yet —
    so the announcement can name them and how long until they're
    old enough, instead of the misleading 'no breeding pairs'.

    `now` lets the offline-catchup loop pass a simulated timestamp so
    expecting records' due_at lines up with the moment the conception
    "happened" inside the away window, not the moment of relaunch.
    """
    if now is None:
        now = time.time()
    room = find_room(state, room_id)
    pairs = eligible_pairs(state, room_id)
    if not pairs:
        # Distinguish "no M+F pair structure exists" from "pairs exist but
        # at least one half is too young / too old", so the announcement
        # is truthful. The old code returned "all_young" for both cases,
        # which mis-announced elder pairs as "too young to breed."
        structural = {}
        structural_members = {}
        for cat in room["creatures"]:
            pid = cat.get("pair_id")
            if pid:
                structural.setdefault(pid, set()).add(cat["sex"])
                structural_members.setdefault(pid, []).append(cat)
        mf_pair_ids = [
            pid for pid, sexes in structural.items()
            if "M" in sexes and "F" in sexes
        ]
        if mf_pair_ids:
            # Pick the right "why can't they breed" reason. If any member
            # of any M+F pair is past elder age, report "all_old". The
            # too-old check takes priority because an elder creature is
            # never going to become eligible again, whereas "too young"
            # implies a wait that resolves on its own.
            mf_members = [
                c for pid in mf_pair_ids for c in structural_members[pid]
            ]
            if any(is_too_old_to_breed(c) for c in mf_members):
                return ("all_old", None)
            return ("all_young", None)
        # Check unpaired same-species M+F who could potentially form a
        # pair. If both are mature → still bonding (waiting on the
        # pair_formation_seconds timer). If at least one is still a
        # baby → still growing (waiting on maturity). The babies-only
        # village seed means the still_growing case is the common
        # first-room experience and absolutely cannot land on the
        # generic 'no pairs' message.
        unpaired_m = [
            c for c in room["creatures"]
            if c.get("pair_id") is None and c["sex"] == "M"
            and not is_too_old_to_breed(c)
        ]
        unpaired_f = [
            c for c in room["creatures"]
            if c.get("pair_id") is None and c["sex"] == "F"
            and not is_too_old_to_breed(c)
        ]
        saw_growing = False
        for m in unpaired_m:
            for f in unpaired_f:
                if m.get("species") != f.get("species"):
                    continue
                pa = m.get("parent_pair_id")
                pb = f.get("parent_pair_id")
                if pa is not None and pb is not None and pa == pb:
                    continue
                if is_mature(m) and is_mature(f):
                    return ("still_bonding", None)
                saw_growing = True
        if saw_growing:
            return ("still_growing", None)
        return ("no_pairs", None)
    # Cooldown is per-species (a cat pair's rest between litters is
    # not biologically the same as a chicken pair's between clutches).
    # eligible_pairs already filtered to same-species M+F pairs, so
    # any member of the pair carries the right species id.
    cooldown_table = state.setdefault("last_breed_per_pair", {})
    ready = []
    for pid in pairs:
        pair_member = next(
            (c for c in room["creatures"] if c.get("pair_id") == pid),
            None,
        )
        if pair_member is None:
            continue
        sid = pair_member.get("species", "cat")
        pair_spec = SPECIES_DATA.get(sid, {}).get("spec", {})
        pair_cooldown = species_breed_cooldown_seconds(pair_spec)
        if now - cooldown_table.get(pid, 0) >= pair_cooldown:
            ready.append(pid)
    if not ready:
        return ("all_resting", None)
    if min(room["meters"].values()) < SETTINGS["breed_min_care"]:
        return ("low_care", None)

    # Pick the pair first so we can scale success by their affection.
    pair_id = random.choice(ready)
    pair_creatures = [c for c in room["creatures"] if c.get("pair_id") == pair_id]
    # Derive species from any pair member that has one — silently
    # defaulting to "cat" used to be the fallback, which was the same
    # cat-leak pattern that bit AI babies (the birth record got stamped
    # as cat species in a non-cat room). If neither pair member has a
    # species field, the data is corrupted; fail the breed cleanly
    # rather than mint cat babies in defiance of physics.
    pair_species = next(
        (c.get("species") for c in pair_creatures if c.get("species")),
        None,
    )
    if pair_species is None:
        return ("no_pairs", None)
    # Stamp explicit parent IDs so the lineage view can link back even
    # after the parents have been re-paired or retired.
    parent_ids = [c["id"] for c in pair_creatures if c.get("id")]
    avg_affection = (
        sum(c.get("affection", 0.5) for c in pair_creatures) / max(1, len(pair_creatures))
    )
    # Affection scales success: 0 affection = 70% of base, 1 affection = 130%.
    # Beloved pairs (avg ≥ 0.9) breed noticeably more successfully.
    affection_mult = 0.7 + 0.6 * avg_affection
    effective_chance = min(1.0, SETTINGS["breed_success_chance"] * affection_mult)
    if random.random() > effective_chance:
        return ("fail", None)
    # If the pair is related, every baby's disability roll uses the
    # inbreeding multiplier (see `maybe_disability` — only the roll
    # rate shifts; framing is unchanged).
    pair_related = (
        len(pair_creatures) >= 2 and are_related(pair_creatures[0], pair_creatures[1])
    )
    inbreed_mult = float(SETTINGS.get("inbreeding_disability_mult", 1.0))
    chance_mult = inbreed_mult if pair_related else 1.0

    def _make_baby(sex, twin_of=None):
        baby_colors = roll_creature_colors(pair_species, parents=pair_creatures)
        baby = {
            "id": str(uuid.uuid4())[:8],
            "name": random_creature_name(pair_species, sex),
            "species": pair_species,
            "sex": sex,
            "parent_pair_id": pair_id,
            "parent_ids": list(parent_ids),
            "description": random_description(pair_species, colors=baby_colors),
        }
        if baby_colors:
            baby["colors"] = baby_colors
        if twin_of is not None:
            baby["twin_of"] = twin_of
        disability = maybe_disability(pair_species, chance_mult=chance_mult)
        if disability:
            baby["disability"] = disability
        return baby

    species_spec = SPECIES_DATA.get(pair_species, {}).get("spec", {})
    # Per-species min_babies / max_babies override the global defaults
    # in SETTINGS; same fallback pattern as meter decay_seconds.
    min_babies, max_babies = species_litter_size_range(species_spec)
    n_babies = random.randint(min_babies, max_babies)
    babies = []
    for _ in range(n_babies):
        babies.append(_make_baby(random.choice(["F", "M"])))
    # Twin roll: each baby has species twin_chance probability of producing a
    # twin (fraternal — fresh sex, name, and description).
    twin_chance = float(species_spec.get("twin_chance", 0.0))
    if twin_chance > 0:
        for original in list(babies):
            if random.random() < twin_chance:
                babies.append(_make_baby(random.choice(["F", "M"]), twin_of=original["id"]))
    cooldown_table[pair_id] = now

    # Conception always goes through an expecting record. Gestation > 0
    # means the babies aren't born yet (player gets the "expecting"
    # advance warning); gestation == 0 means due_at == now and the
    # caller can immediately call process_expecting to place the
    # babies. Babies are pre-rolled at conception so color
    # inheritance, disability rolls, etc. are locked to the parents
    # who actually conceived — even if the parents change rooms or
    # die before birth.
    gestation = float(species_spec.get("gestation_seconds", 0) or 0)
    record = {
        "id": str(uuid.uuid4())[:8],
        "from_pair": pair_id,
        "room_id": room_id,
        "species": pair_species,
        "babies": babies,
        "conceived_at": now,
        "due_at": now + gestation,
        "no_room_warned": False,
    }
    state.setdefault("expecting", []).append(record)
    return ("conceived", record)


def auto_breed_village(state):
    """Roll for offscreen births among the village's residents.

    For each species with at least one mature, non-retired M and F, roll
    once at a fraction of the breed_success_chance and add a single baby
    on success. The baby joins the village directly (village breedings
    are "we heard about it later"). Related parents are
    allowed to breed; the baby's disability roll is multiplied by
    SETTINGS["inbreeding_disability_mult"] to model inbreeding depression
    (see `maybe_disability` for design intent — disability framing is
    unchanged, only the roll probability shifts).

    Returns a list of newly-added creatures (length 0 or more).
    """
    village = state.get("village", [])
    by_species = {}
    for cat in village:
        if not is_mature(cat) or is_too_old_to_breed(cat):
            continue
        sid = cat.get("species")
        sex = cat.get("sex")
        if sid and sex:
            by_species.setdefault(sid, {"F": [], "M": []})[sex].append(cat)

    new_babies = []
    base_chance = SETTINGS.get("breed_success_chance", 0.6)
    village_chance = base_chance * 0.3
    inbreed_mult = float(SETTINGS.get("inbreeding_disability_mult", 1.0))
    for sid, groups in by_species.items():
        if not groups["F"] or not groups["M"]:
            continue
        if random.random() > village_chance:
            continue
        f = random.choice(groups["F"])
        m = random.choice(groups["M"])
        chance_mult = inbreed_mult if are_related(f, m) else 1.0
        sex = random.choice(["F", "M"])
        baby = new_creature(
            sid, sex,
            disability_chance_mult=chance_mult,
            parents=[f, m],
        )
        baby["pair_id"] = None
        baby["moved_to_village_at"] = time.time()
        # Stamp a synthetic parent pair id derived from the sorted parent
        # ids so siblings born from the same village couple share an id
        # (full-sibling detection works) AND babies of different couples
        # don't collide just because they were born in the same second
        # (the previous `village_{int(time.time())}_{sid}` key did both
        # wrong: same-second/same-species babies of different parents
        # read as siblings; same-couple babies seconds apart read as
        # unrelated).
        parent_ids_sorted = tuple(sorted([m["id"], f["id"]]))
        baby["parent_pair_id"] = f"village_{parent_ids_sorted[0]}_{parent_ids_sorted[1]}"
        baby["parent_ids"] = [m["id"], f["id"]]
        village.append(baby)
        new_babies.append(baby)
    return new_babies


def auto_breed_offline_catchup(state, elapsed_seconds):
    """Run auto-breeding catch-up rolls for time the player was away.

    Mirrors the on-tick cadence: one full pass per
    auto_breed_interval_seconds of elapsed real time, capped at one
    week of catch-up so a save left for years doesn't spawn thousands
    of babies in a single relaunch. No-ops when AUTO_BREEDING is off,
    matching live behaviour exactly.

    For each interval we attempt a breed in every room (per-pair
    cooldowns inside attempt_breed naturally limit this — a pair with
    a 24h cooldown won't double-up just because the loop iterates 24
    times) and roll village offscreen births. Returns
    (room_births, village_births) where room_births is the list of
    birth records from process_expecting (same shape as live births)
    and village_births is a list of new baby creature dicts. Caller
    surfaces these via "while you were away" announcements.
    """
    if not state.get("auto_breeding", True) or elapsed_seconds <= 0:
        return [], []
    interval = max(60, int(SETTINGS.get("auto_breed_interval_seconds", 3600) or 3600))
    n_intervals = min(168, int(elapsed_seconds // interval))
    if n_intervals <= 0:
        return [], []
    room_births = []
    village_births = []
    # Track conceptions that started during this catchup window. We
    # filter them down at the end to only those still pending (i.e.,
    # gestation didn't complete during the window) — those need the
    # "started expecting" announcement on relaunch. Conceptions that
    # both started and matured within the window become births and
    # get the birth announcement instead.
    conceptions_during_catchup = []  # list of (room_name, record)
    # Walk the away window in `interval`-sized steps with a simulated
    # clock. attempt_breed and process_expecting both accept `now=` so
    # gestation timing lines up with when the conception "happened" —
    # a pair that conceives in iteration 0 with a gestation shorter
    # than the window will give birth in a later iteration, not stay
    # pending until live play resumes.
    sim_now = time.time() - elapsed_seconds
    for _ in range(n_intervals):
        sim_now += interval
        for room in state["rooms"]:
            status, payload = attempt_breed(state, room["id"], now=sim_now)
            if status == "conceived" and payload is not None:
                conceptions_during_catchup.append((room["name"], payload))
        village_births.extend(auto_breed_village(state))
        # Mature any expecting records whose due_at has passed by this
        # iteration's simulated time. Each becomes a birth record.
        for birth in process_expecting(state, now=sim_now):
            room_births.append(birth)
    # Conceptions that matured during the window are already in
    # room_births. Drop their from_pair from the "still expecting"
    # list so the player sees one announcement per event. Match by
    # the expecting record's id (which equals the birth's
    # from_pair... wait no, by from_pair only).
    born_pair_ids = {b.get("from_pair") for b in room_births}
    conceptions_pending = [
        (rn, rec) for rn, rec in conceptions_during_catchup
        if rec.get("from_pair") not in born_pair_ids
    ]
    # Stash on state for the offline announcer to surface. Side-effect
    # rather than tuple-extension to avoid touching every caller of
    # auto_breed_offline_catchup; the announcer pops the key.
    state["_offline_conceptions"] = conceptions_pending
    return room_births, village_births


def check_expecting_room_space(state):
    """For each expecting (gestating) record, check if any compatible
    room has space for at least one baby. If none does, fire a
    one-shot warning (toggled via the record's `no_room_warned` flag)
    so the player has time to build / expand before the babies
    actually arrive.

    Returns the list of records that just transitioned from "ok" to
    "warned" so the caller can build an aggregated announcement. The
    flag also flips back to False when space reappears (e.g., player
    builds a room) so a second loss-of-space could re-warn — but the
    common case is one-shot.

    The check uses `find_room_for_species` with `sim_used` accounting
    for already-stamped expecting records, so if two pairs are both
    gestating the same species into a 1-slot-free room, only one of
    them gets the all-clear and the other gets warned.
    """
    expecting = state.get("expecting", [])
    if not expecting:
        return []
    rooms = state.get("rooms", [])
    # Simulate occupancy as if every pending expecting record has
    # already given birth. That way two pending records targeting the
    # same room don't both think there's space.
    sim_used = {r["id"]: len(r["creatures"]) for r in rooms}
    newly_warned = []
    for rec in expecting:
        species_id = rec.get("species") or "cat"
        n_babies = len(rec.get("babies") or []) or 1
        # Walk this record's babies through find_room_for_species,
        # decrementing sim_used as we "place" each one. If any baby
        # can't be placed in a compatible room (returns None), this
        # record is in trouble.
        any_blocked = False
        for _ in range(n_babies):
            target, _reason = find_room_for_species(
                rooms, species_id,
                primary_room_id=rec.get("room_id"),
                sim_used=sim_used,
            )
            if target is None:
                any_blocked = True
                break
            sim_used[target["id"]] += 1
        was_warned = bool(rec.get("no_room_warned"))
        if any_blocked and not was_warned:
            rec["no_room_warned"] = True
            newly_warned.append(rec)
        elif not any_blocked and was_warned:
            # Space reappeared (player built a room or moved someone
            # out) — clear the flag so a future loss can re-warn.
            rec["no_room_warned"] = False
    return newly_warned


def clear_expired_dependencies(state, now=None):
    """Sweep every creature; if their `dependent_until` has passed,
    drop the `dependent_on` and `dependent_until` fields. Cheap when
    there are no dependents (most ticks). Also tolerates the case
    where the mother no longer exists — `dependent_until` still
    expires the link cleanly even if `dependent_on` points at a dead
    id, so a baby orphaned mid-nursing isn't permanently stuck.
    """
    if now is None:
        now = time.time()
    cleared = 0
    for room in state.get("rooms", []):
        for cat in room.get("creatures", []):
            if "dependent_until" in cat and cat["dependent_until"] <= now:
                cat.pop("dependent_on", None)
                cat.pop("dependent_until", None)
                cleared += 1
    for cat in state.get("village", []):
        if "dependent_until" in cat and cat["dependent_until"] <= now:
            cat.pop("dependent_on", None)
            cat.pop("dependent_until", None)
            cleared += 1
    return cleared


def process_expecting(state, now=None):
    """Convert any expecting records whose due_at has passed into
    placed creatures. Babies become real residents of rooms (or the
    village, if no compatible room has space). Mother-dependency is
    stamped at placement time so the move-logic tethering kicks in
    immediately.

    Mutates state["expecting"] (drops matured records), and state's
    rooms / village (appends placed babies). Returns a list of birth
    records, one per matured expecting:

        {
            "from_pair": pair_id,
            "room_id": original room id from the expecting record,
            "species": species_id,
            "kept_by_room": {room_id: [name, ...]},
            "spill_full_by_room": {room_id: [name, ...]},
            "spill_denies_by_room": {room_id: [name, ...]},
            "village_no_space": [name, ...],
            "village_no_room": [name, ...],
        }

    The bucketing feeds the birth_kept_in_room / birth_spilled_full /
    birth_spilled_denies / birth_to_village_* announcement family in
    _announce_births — one composite per birth record.

    The "intentional release" bucket is gone: players don't choose to
    discard newborns at birth any more. Babies that don't fit in
    compatible rooms go to the village as a *consequence* (no slots
    available), not as a *choice*.
    """
    if now is None:
        now = time.time()
    expecting = state.get("expecting", [])
    if not expecting:
        return []
    rooms = state.get("rooms", [])
    births = []
    surviving = []
    for record in expecting:
        if record.get("due_at", 0) > now:
            surviving.append(record)
            continue
        # Find the mother for dependency stamping. parent_ids on each
        # baby is the canonical link; look up the female parent once
        # per litter and reuse for every baby. Tolerates a missing
        # mother (e.g., emigrated during gestation) — babies born
        # mother-less just skip the dependency stamp.
        species_id = record.get("species") or "cat"
        species_spec = SPECIES_DATA.get(species_id, {}).get("spec", {})
        mother_dependency = float(
            species_spec.get("mother_dependency_seconds", 0) or 0
        )
        mother_id = None
        babies = record.get("babies") or []
        if babies:
            parent_ids = babies[0].get("parent_ids") or []
            for pid in parent_ids:
                cat = find_creature_by_id(state, pid)
                if cat and cat.get("sex") == "F":
                    mother_id = cat.get("id")
                    break

        # Walk each baby through find_room_for_species with simulated
        # occupancy so a litter of 5 cats correctly fills a 4-slot
        # room and overflows the 5th somewhere else.
        sim_used = {r["id"]: len(r["creatures"]) for r in rooms}
        primary_room_id = record.get("room_id")
        kept_by_room = {}
        spill_full_by_room = {}
        spill_denies_by_room = {}
        village_no_space = []
        village_no_room = []
        for baby in babies:
            cat = baby_to_cat(baby, now=now)
            target, reason = find_room_for_species(
                rooms, baby.get("species", species_id),
                primary_room_id=primary_room_id, sim_used=sim_used,
            )
            if target is None:
                # No room had space → village. Mother-dependency stamp
                # is skipped: mother is in some room, baby is in the
                # village; tethering across that gap doesn't help and
                # would confuse move logic.
                cat["moved_to_village_at"] = now
                state.setdefault("village", []).append(cat)
                if reason == PLACEMENT_VILLAGE_NO_ROOM:
                    village_no_room.append(baby["name"])
                else:
                    village_no_space.append(baby["name"])
                continue
            sim_used[target["id"]] += 1
            # Stamp dependency only when mother+baby end up in the
            # same room. Spillover babies (placed in a non-mother
            # room) skip the stamp for the same reason as
            # village-bound babies.
            if (mother_dependency > 0 and mother_id
                    and any(c.get("id") == mother_id for c in target["creatures"])):
                cat["dependent_on"] = mother_id
                cat["dependent_until"] = now + mother_dependency
            target["creatures"].append(cat)
            if reason == PLACEMENT_PRIMARY:
                kept_by_room.setdefault(target["id"], []).append(baby["name"])
            elif reason == PLACEMENT_SPILL_PRIMARY_DENIES:
                spill_denies_by_room.setdefault(target["id"], []).append(baby["name"])
            else:
                spill_full_by_room.setdefault(target["id"], []).append(baby["name"])
        births.append({
            "from_pair": record.get("from_pair"),
            "room_id": primary_room_id,
            "species": species_id,
            "kept_by_room": kept_by_room,
            "spill_full_by_room": spill_full_by_room,
            "spill_denies_by_room": spill_denies_by_room,
            "village_no_space": village_no_space,
            "village_no_room": village_no_room,
        })
    state["expecting"] = surviving
    return births


def wild_emigration_pass(state, elapsed_seconds=None):
    """Run a single auto-emigration check. Healthy retirees (creatures
    past their species' elder age — the merged stage where they retire
    from breeding — with no emigration-blocking disability) may
    auto-emigrate to the wild — removed from the save
    with an announcement. Disabled retirees never emigrate; if they're
    still in a room when this runs, they get moved into the village
    (the sanctuary) so they have a permanent home.

    Probabilistic: each eligible creature rolls one chance. The roll
    rate uses `wild_emigration_chance` per check. When `elapsed_seconds`
    is given (offline catch-up), the per-check chance is compounded
    over (elapsed / check_seconds) checks so a player who closes the
    game for hours doesn't come back to a frozen retirement queue.

    Returns (emigrants, sanctuary_arrivals) — both lists of (name,
    species_id) tuples — so the caller can build a single aggregated
    announcement instead of one per creature (NVDA flood prevention).
    """
    chance_per_check = float(SETTINGS.get("wild_emigration_chance", 0.0) or 0.0)
    if chance_per_check <= 0:
        chance = 0.0
    else:
        check_seconds = max(60, int(SETTINGS.get("wild_emigration_check_seconds", 3600) or 3600))
        if elapsed_seconds is None:
            chance = chance_per_check
        else:
            n_checks = max(0.0, float(elapsed_seconds) / float(check_seconds))
            chance = 1.0 - (1.0 - chance_per_check) ** n_checks
    chance = max(0.0, min(1.0, chance))

    emigrants = []           # (name, species_id) — left for the wild
    sanctuary_arrivals = []  # (name, species_id) — disabled, moved to village

    village = state.setdefault("village", [])
    remembered = state.setdefault("remembered", [])
    now = time.time()

    def _release_partner(leaving_cat):
        """When a creature dies (wild emigration), free the other half of
        any pair so the survivor can re-pair via progress_pairing. The
        partner could be anywhere — same room, another room, or the
        village — because pairs survive moves now. Scans everywhere
        rather than just the leaving cat's room.

        Death is the only event that dissolves a pair. Move and
        sanctuary-move keep pairs intact (the player might bring them
        back together later); only true loss clears the pair_id so the
        survivor isn't left committed to a ghost.
        """
        pair_id = leaving_cat.get("pair_id")
        if not pair_id:
            return
        leaving_id = leaving_cat.get("id")
        for r in state.get("rooms", []):
            for c in r["creatures"]:
                if c.get("pair_id") == pair_id:
                    c["pair_id"] = None
        for c in state.get("village", []):
            if c.get("pair_id") == pair_id:
                c["pair_id"] = None
        progress = state.get("pair_progress", {})
        if leaving_id:
            for key in list(progress.keys()):
                if leaving_id in key.split("+"):
                    del progress[key]
        state.get("last_breed_per_pair", {}).pop(pair_id, None)

    def remember(cat):
        """Append a memorial entry. Each emigrant gets a small permanent
        record so they're not just deleted — the cozy ethos requires
        that creatures who 'leave for the wild' read as having had a
        life worth noting, not as having been removed.
        """
        remembered.append({
            "name": cat.get("name", "?"),
            "species": cat.get("species", "cat"),
            "left_at": now,
            "age_seconds_at_leaving": cat_age_seconds(cat),
        })

    # Walk room creatures first. Retirees with an emigration-blocking
    # disability get sanctuary-moved. Settled retirees stay where they
    # are — they decided this is home, no relocation needed. Everyone
    # else healthy rolls for the wild right from their room.
    for room in state["rooms"]:
        for cat in list(room["creatures"]):
            if not is_too_old_to_breed(cat):
                continue
            if is_settled(cat):
                continue  # decided to stay; no roll, no move
            disability = cat.get("disability") or ""
            if disability and disability_blocks_emigration(cat.get("species", "cat"), disability):
                # Sanctuary move: same rules as a normal move-to-village
                # — pair survives. The retired disabled creature keeps
                # their pair_id; their partner (anywhere) keeps theirs.
                # Only death dissolves a pair.
                room["creatures"].remove(cat)
                cat["moved_to_village_at"] = now
                village.append(cat)
                sanctuary_arrivals.append((cat["name"], cat.get("species", "cat")))
                continue
            if chance > 0 and random.random() < chance:
                # Wild emigration is the one event that ends a pair —
                # the leaving creature is gone for good, so release the
                # surviving partner (wherever they are) to re-pair.
                _release_partner(cat)
                room["creatures"].remove(cat)
                remember(cat)
                emigrants.append((cat["name"], cat.get("species", "cat")))

    # Then village creatures. Settled retirees stay (rare in the village
    # since affection doesn't grow there, but possible if they settled
    # in a room and were moved out). Disabled retirees in sanctuary
    # already; leave them. Healthy retirees roll for the wild.
    for cat in list(village):
        if not is_too_old_to_breed(cat):
            continue
        if is_settled(cat):
            continue
        disability = cat.get("disability") or ""
        if disability and disability_blocks_emigration(cat.get("species", "cat"), disability):
            continue
        if chance > 0 and random.random() < chance:
            # Same as the room-emigration branch above: death dissolves
            # the pair. The surviving partner could be in any room or
            # still in the village — _release_partner scans everywhere.
            _release_partner(cat)
            village.remove(cat)
            remember(cat)
            emigrants.append((cat["name"], cat.get("species", "cat")))

    return emigrants, sanctuary_arrivals


def elder_production_pass(state, now=None):
    """Run a single elder-production check across every room.

    Each elder creature in a room produces one item every
    ``elder_production_seconds`` of wall-clock time, drawn at random
    from the keys of the room-type's ``build_recipe``. So cats in an
    Indoor room produce sticks/leaves/acorns; aquatic elders produce
    stones/pebbles/fabric scraps; whatever the modder set as the type's
    recipe is what its elders make.

    The helper is pure-ish: mutates the state's inventory and stamps
    ``last_produced_at`` on each producing creature. Returns a list of
    ``(item_name, room_name)`` tuples for the items produced this pass
    so the caller can build a single aggregated announcement.

    Catches up multiple intervals if a long gap elapsed (mostly matters
    in apply_elapsed_time, where a player coming back after eight
    hours should find that their elders have been busy).
    """
    if now is None:
        now = time.time()
    interval = max(60, int(SETTINGS.get("elder_production_seconds", 10800)))
    produced = []
    for room in state.get("rooms", []):
        type_spec = ROOM_TYPES.get(room.get("type", "indoor"), {})
        recipe = type_spec.get("build_recipe") or {}
        recipe_items = list(recipe.keys())
        if not recipe_items:
            continue
        for cat in room.get("creatures", []):
            if not is_elder(cat):
                continue
            # If the modder marked this creature's disability with the
            # ``no_produce`` flag, they participate in everything else
            # but skip the production roll. They still bump
            # ``last_produced_at`` so resuming production (e.g. flag
            # removed later) starts fresh, not with a cycle backlog.
            if disability_blocks_production(
                cat.get("species", "cat"), cat.get("disability"),
            ):
                cat["last_produced_at"] = now
                continue
            last = float(cat.get("last_produced_at", 0) or 0)
            if last == 0:
                # First check after they became elder (or pre-feature
                # save). Bootstrap the timer so they wait one full
                # interval before their first contribution.
                cat["last_produced_at"] = now
                continue
            elapsed = now - last
            if elapsed < interval:
                continue
            cycles = int(elapsed / interval)
            for _ in range(cycles):
                item = random.choice(recipe_items)
                tier = "uncommon" if item in ITEMS_UNCOMMON else "common"
                inventory = state.setdefault("inventory", {})
                inventory.setdefault(tier, {})
                inventory[tier][item] = inventory[tier].get(item, 0) + 1
                produced.append((item, room["name"]))
            cat["last_produced_at"] = last + cycles * interval
    return produced


def summarize_production(produced):
    """Aggregate (item, room) pairs into 'N items (room), M items (room)'
    suitable for an announcement template. Returns "" for empty input.
    """
    if not produced:
        return ""
    from collections import Counter
    counts = Counter()
    for item, room_name in produced:
        counts[(item, room_name)] += 1
    parts = []
    for (item, room_name), n in sorted(counts.items()):
        parts.append(f"{n} {pluralize(item, n)} in {room_name}")
    return ", ".join(parts)


def _spec_litter_label(spec):
    """Read a spec's singular litter word ('litter', 'clutch', 'spawn',
    etc.). Prefers the current `litter_label` field; falls back to the
    historical `basket_label` so a modder's unmigrated species JSON
    keeps working. Defaults to 'litter' if neither is set.
    """
    if not spec:
        return "litter"
    return spec.get("litter_label") or spec.get("basket_label") or "litter"


def _spec_litter_label_plural(spec):
    """Plural form of _spec_litter_label. Same back-compat shim, plus
    auto-pluralization (singular + 's') if no explicit plural is set.
    """
    if not spec:
        return "litters"
    explicit = spec.get("litter_label_plural") or spec.get("basket_label_plural")
    if explicit:
        return explicit
    return _spec_litter_label(spec) + "s"


def litter_label_for(record):
    """Return the species-appropriate singular litter word for this
    birth record. Looks at the first baby's species. Defaults to
    'litter' if the record is empty.
    """
    babies = record.get("babies") if record else None
    if not babies:
        return "litter"
    species_id = babies[0].get("species", "cat")
    spec = SPECIES_DATA.get(species_id, {}).get("spec", {})
    return _spec_litter_label(spec)


def room_litter_label(room):
    """Pick the most-appropriate litter word for messages tied to a
    specific room — e.g. 'No litter this time' when a cat-room breed
    attempt fails. Looks at the first resident's species, then falls
    back to the room's allowed_species, then to 'litter'.
    """
    creatures = room.get("creatures") or []
    sid = creatures[0].get("species") if creatures else None
    if not sid:
        allowed = room.get("allowed_species") or []
        sid = allowed[0] if allowed else None
    if sid:
        return _spec_litter_label(SPECIES_DATA.get(sid, {}).get("spec", {}))
    return "litter"


def litter_summary_label(records, plural):
    """Return one word that describes a *collection* of births / litters
    across species. If every entry is the same species, use that species'
    label (singular or plural, per `plural`). Otherwise fall back to the
    generic 'litter' / 'litters' — a summary label has nowhere to put
    six different species words at once.
    """
    species_ids = set()
    for r in records:
        babies = r.get("babies") or []
        if babies:
            species_ids.add(babies[0].get("species"))
    species_ids.discard(None)
    if len(species_ids) == 1:
        sid = next(iter(species_ids))
        spec = SPECIES_DATA.get(sid, {}).get("spec", {})
        if plural:
            return _spec_litter_label_plural(spec)
        return _spec_litter_label(spec)
    return "litters" if plural else "litter"




def baby_to_cat(baby, now=None):
    """Materialize a basket-baby record into a real creature.

    `now` is the moment the baby is being born — defaults to real time
    for live births. Offline catch-up passes a `sim_now` from the past
    so a baby born during the away window correctly ages up by
    (real_now - sim_now) and has its maturity timer anchored to the
    birth moment, not the relaunch moment. Without this, a baby born
    30 minutes into a 60-minute away window would show "Age 0 / In
    village for 30m" on relaunch (visible contradiction) and would
    take 30 extra minutes to grow up.
    """
    real_now = time.time()
    if now is None:
        now = real_now
    elapsed_since_birth = max(0.0, real_now - now)
    species_id = baby.get("species", "cat")
    spec = SPECIES_DATA.get(species_id, {}).get("spec", {})
    cat = {
        "id": baby["id"],
        "name": baby["name"],
        "species": species_id,
        "sex": baby["sex"],
        "affection": 0.5,
        "age_seconds": elapsed_since_birth,
        "pair_id": None,
        "parent_pair_id": baby.get("parent_pair_id"),
        "description": baby.get("description") or random_description(species_id),
    }
    # Babies grow up before they can auto-pair or breed. Set a real-time
    # mature-at timestamp; species' breeding_age_seconds tunes how long it
    # takes, lifecycle_pace then scales the wait so a single setting moves
    # all life-stages together. 0 (or missing) breeding_age = mature
    # immediately, current behavior. Anchored to `now` (the birth moment),
    # so a baby born during offline catch-up matures on schedule from
    # its own birth, not from when the player relaunched.
    breeding_age = float(spec.get("breeding_age_seconds", 0) or 0)
    if breeding_age > 0:
        cat["mature_at"] = now + breeding_age * lifecycle_pace()
    return cat


def _legacy_days_to_seconds(days):
    """Convert a value stored in the legacy 'game days' unit to real
    seconds. Game days were a vestigial sim abstraction where 1 game day
    defaulted to seconds_per_game_day (3600) of real time. The redesign
    drops that abstraction; this helper exists only so old saves and
    pre-redesign species JSONs still work after upgrade.
    """
    spgd = float(SETTINGS.get("seconds_per_game_day", 3600) or 3600)
    return float(days) * spgd


def cat_age_seconds(cat):
    """Read a creature's age in real seconds. Prefers the new
    `age_seconds` field; falls back to converting an old `age_days`
    value with the saved (or default) seconds_per_game_day. Always
    returns a non-negative float.
    """
    if "age_seconds" in cat:
        try:
            return max(0.0, float(cat["age_seconds"]))
        except (TypeError, ValueError):
            return 0.0
    if "age_days" in cat:
        try:
            return max(0.0, _legacy_days_to_seconds(cat["age_days"]))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _spec_seconds(spec, seconds_key, days_key):
    """Read a species-spec field that's now stored in seconds, falling
    back to the old _days-suffixed variant for backward compat. Used by
    elder_age, breeding_age, and starter_age min/max accessors.
    """
    if seconds_key in spec:
        try:
            return float(spec[seconds_key] or 0)
        except (TypeError, ValueError):
            return 0.0
    if days_key in spec:
        try:
            return _legacy_days_to_seconds(spec[days_key] or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def species_elder_age_seconds(spec):
    return _spec_seconds(spec, "elder_age_seconds", "elder_age_days")


def room_type_compatible_species(type_id):
    """Return a list of species ids whose specs claim this room type as
    compatible. **Species `compatible_room_types` is the single source
    of truth** for the species ↔ room-type relationship; the legacy
    `compatible_species` field on room-type JSONs is ignored at read
    time so the two can never disagree.

    The previous bidirectional storage allowed silent inconsistency:
    a species could be saved with no compatible room types, while a
    room type's `compatible_species` list happily included it (or
    vice versa). Closing the loop on one editor's save validation
    didn't help — the other editor was still writing to the other
    side. Computing on the fly removes the entire class of bug.
    """
    out = []
    for sid, data in SPECIES_DATA.items():
        spec = data.get("spec") or {}
        compat = spec.get("compatible_room_types") or []
        if type_id in compat:
            out.append(sid)
    return out


def room_type_delete_impact(type_id):
    """Assess deleting room type `type_id`. Returns (would_strand, also_listed):

      would_strand -- list of species *names* whose ONLY compatible room type
                      is this one; deleting it would leave their creatures
                      stuck in the village forever, so the caller should
                      refuse the delete.
      also_listed  -- list of species *ids* that list this type alongside
                      other homes; safe to delete, but the dead id should be
                      stripped from their compatible_room_types so no species
                      is left referencing a room type that no longer exists.

    Pure (reads SPECIES_DATA). The UI's room-type delete uses this to refuse
    + tidy; a headless driver should honour the same guard.
    """
    would_strand, also_listed = [], []
    for sid, data in SPECIES_DATA.items():
        compat = list((data.get("spec") or {}).get("compatible_room_types") or [])
        if type_id not in compat:
            continue
        if compat == [type_id]:
            would_strand.append((data.get("spec") or {}).get("name", sid))
        else:
            also_listed.append(sid)
    return would_strand, also_listed


def species_old_age_seconds(spec):
    """Returns the 'they're old now' threshold — the single milestone at
    which a creature both becomes an elder (eligible for the production
    pass) AND retires from breeding (eligible for wild emigration).

    After the elder/retire merge, this is just elder_age_seconds. For
    legacy modder specs that only set max_breeding_age_seconds (the
    pre-merge retirement threshold), we fall back to that so the spec
    keeps working without a forced re-edit.
    """
    if "elder_age_seconds" in spec or "elder_age_days" in spec:
        return species_elder_age_seconds(spec)
    return _spec_seconds(spec, "max_breeding_age_seconds", "max_breeding_age_days")


def species_breed_cooldown_seconds(spec):
    """Returns this species' rest-between-litters duration, in seconds.

    Per-species value when set in the spec (different species have
    different biological cadences — a cat between litters is not a
    chicken between clutches). Falls back to the legacy global
    SETTINGS["breed_cooldown_seconds"] so existing saves and any spec
    that predates the per-species field keep working without a forced
    re-edit. A value of 0 (or negative) is treated as "no cooldown"
    — pairs can attempt breeding every tick.
    """
    if isinstance(spec, dict) and "breed_cooldown_seconds" in spec:
        try:
            v = float(spec["breed_cooldown_seconds"])
            return max(0.0, v)
        except (TypeError, ValueError):
            pass
    try:
        return max(0.0, float(SETTINGS.get("breed_cooldown_seconds", 86400) or 0))
    except (TypeError, ValueError):
        return 86400.0


def species_starter_age_range_seconds(spec):
    """Return (min_seconds, max_seconds) for a fresh creature of this
    species, with min/max swapped if the spec has them backwards.
    """
    lo = _spec_seconds(spec, "starter_age_min_seconds", "starter_age_min")
    hi = _spec_seconds(spec, "starter_age_max_seconds", "starter_age_max")
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def species_litter_size_range(spec):
    """Return (min_babies, max_babies) for this species' breeding rolls.

    Per-species `min_babies` / `max_babies` in the spec override the
    global SETTINGS defaults; either or both can be omitted to fall
    back. Same convention as meter `decay_seconds` falling back to
    `full_decay_seconds`. Output is clamped to a sane range (min ≥ 1,
    max ≥ min) so a malformed spec can't produce zero or negative
    litters.
    """
    def _read(key):
        if not spec or key not in spec or spec[key] in (None, ""):
            return None
        try:
            return int(spec[key])
        except (TypeError, ValueError):
            return None
    lo = _read("min_babies")
    hi = _read("max_babies")
    if lo is None:
        lo = int(SETTINGS.get("min_babies", 1) or 1)
    if hi is None:
        hi = int(SETTINGS.get("max_babies", 4) or 4)
    lo = max(1, lo)
    hi = max(lo, hi)
    return lo, hi


def lifecycle_pace():
    """Read the lifecycle pace multiplier and clamp it to a safe positive
    floor. Used to scale baby maturity and continuous aging together so a
    single setting controls how fast creatures move through their life
    stages. See the SETTINGS comment on `lifecycle_pace` for semantics.
    """
    try:
        pace = float(SETTINGS.get("lifecycle_pace", 1.0))
    except (TypeError, ValueError):
        pace = 1.0
    return max(0.01, pace)


def is_mature(cat):
    """A creature is mature if no mature_at timestamp is set (legacy / starter
    creatures) or if the current time has passed the timestamp.
    """
    mature_at = cat.get("mature_at")
    if mature_at is None:
        return True
    return time.time() >= mature_at


def time_until_mature(cat):
    """Seconds remaining until this creature is mature, or 0 if already mature."""
    mature_at = cat.get("mature_at")
    if mature_at is None:
        return 0
    return max(0, int(mature_at - time.time()))


def is_settled(cat):
    """A creature has decided to stay — once true, they're exempt from
    wild emigration forever. Set when affection crosses the beloved
    threshold (see SETTLED_AFFECTION below) on a life-stage check.
    """
    return bool(cat.get("settled"))


# Affection threshold at which a creature "decides this is home." Same
# value as is_beloved's 0.9 default — beloved creatures and settled
# creatures are essentially the same emotional bar, but settled is the
# one-shot stamp that gates wild-emigration exemption.
SETTLED_AFFECTION = 0.9


def life_stage_transitions_pass(state):
    """Detect creatures that have JUST crossed a life-stage threshold —
    became elder, retired from breeding, or decided this is home —
    since the last check. Stamps a one-shot flag on the creature so
    each transition fires exactly once per creature, even if the
    engine re-checks across many ticks. Returns
    (new_elders, new_retirees, new_settled) — all lists of
    (name, species_id).

    A creature can appear in BOTH new_elders/new_retirees and
    new_settled in the same pass (becoming an elder doesn't preclude
    deciding to stay). The elder/retired tracks de-dup against each
    other — retired implies elder, so a creature that crossed both at
    once goes in retired only.
    """
    new_elders = []
    new_retirees = []
    new_settled = []

    def check(cat):
        species = cat.get("species", "cat")
        if cat_age_seconds(cat) <= 0:
            return
        if is_too_old_to_breed(cat) and not cat.get("retired_announced"):
            cat["retired_announced"] = True
            cat["elder_announced"] = True  # implies elder, no double-fire
            new_retirees.append((cat.get("name", "?"), species))
        elif is_elder(cat) and not cat.get("elder_announced"):
            cat["elder_announced"] = True
            new_elders.append((cat.get("name", "?"), species))
        # Settling check is independent of elder/retired — a creature
        # can settle at any age once their affection is high enough.
        # Only room creatures gain affection (village affection doesn't
        # decay or grow), but a creature moved to the village while
        # already settled keeps the stamp; the check below silently
        # no-ops for already-settled cats.
        if (not cat.get("settled")
                and float(cat.get("affection", 0.0)) >= SETTLED_AFFECTION):
            cat["settled"] = True
            new_settled.append((cat.get("name", "?"), species))

    for room in state.get("rooms", []):
        for cat in room.get("creatures", []):
            check(cat)
    for cat in state.get("village", []):
        check(cat)

    return new_elders, new_retirees, new_settled


def is_elder(cat):
    """True if the creature has crossed their species' 'old enough'
    threshold — the same milestone as is_too_old_to_breed after the
    elder/retire merge. 0 (or missing) means this species never
    becomes elder. Uses species_old_age_seconds so legacy specs that
    only set max_breeding_age_seconds still flip both flags together.
    """
    sid = cat.get("species", "cat")
    spec = SPECIES_DATA.get(sid, {}).get("spec", {})
    threshold = species_old_age_seconds(spec)
    return threshold > 0 and cat_age_seconds(cat) >= threshold


def is_too_old_to_breed(cat):
    """True once the creature is old (= elder). After the elder/retire
    merge, the same threshold gates both becoming an elder and retiring
    from breeding — there is no in-between stage. Legacy specs with
    only max_breeding_age_seconds still work via species_old_age_seconds.
    """
    sid = cat.get("species", "cat")
    spec = SPECIES_DATA.get(sid, {}).get("spec", {})
    threshold = species_old_age_seconds(spec)
    return threshold > 0 and cat_age_seconds(cat) >= threshold


def is_beloved(cat, threshold=0.9):
    """High-affection creatures show a beloved tag and get small boosts."""
    return cat.get("affection", 0) >= threshold


def all_creatures(state):
    """Iterator over every creature, regardless of which room or the village."""
    for room in state.get("rooms", []):
        for cat in room.get("creatures", []):
            yield cat
    for cat in state.get("village", []):
        yield cat


def find_creature_by_id(state, cat_id):
    if not cat_id:
        return None
    for cat in all_creatures(state):
        if cat.get("id") == cat_id:
            return cat
    return None


def find_parents_of(state, cat):
    """Return (mother, father) for a creature, or (None, None) if unknowable.

    Prefers explicit `parent_ids` (set on births from this version forward).
    Falls back to a best-effort search by `parent_pair_id` against current
    pair memberships, which works as long as the parents are still paired.
    """
    pids = cat.get("parent_ids") or []
    found = [find_creature_by_id(state, pid) for pid in pids]
    found = [c for c in found if c is not None]
    if found:
        mother = next((c for c in found if c.get("sex") == "F"), None)
        father = next((c for c in found if c.get("sex") == "M"), None)
        return mother, father
    parent_pair = cat.get("parent_pair_id")
    if not parent_pair:
        return None, None
    candidates = [c for c in all_creatures(state) if c.get("pair_id") == parent_pair]
    mother = next((c for c in candidates if c.get("sex") == "F"), None)
    father = next((c for c in candidates if c.get("sex") == "M"), None)
    return mother, father


def find_partner_of(state, cat):
    pid = cat.get("pair_id")
    if not pid:
        return None
    for other in all_creatures(state):
        if other is cat:
            continue
        if other.get("pair_id") == pid:
            return other
    return None


def find_offspring_of(state, cat):
    """Every creature whose `parent_ids` includes this cat's id, plus
    (best-effort) creatures whose `parent_pair_id` matches the cat's
    current pair_id (covers older offspring without explicit parent ids).
    """
    cat_id = cat.get("id")
    pair_id = cat.get("pair_id")
    seen = set()
    children = []
    for other in all_creatures(state):
        if other.get("id") == cat_id:
            continue
        oid = other.get("id")
        if oid in seen:
            continue
        if cat_id and cat_id in (other.get("parent_ids") or []):
            children.append(other)
            seen.add(oid)
            continue
        if pair_id and other.get("parent_pair_id") == pair_id:
            children.append(other)
            seen.add(oid)
    return children


def find_siblings_of(state, cat):
    """Creatures sharing this cat's parent_pair_id, excluding self."""
    parent_pair = cat.get("parent_pair_id")
    cat_id = cat.get("id")
    if not parent_pair:
        return []
    return [
        other for other in all_creatures(state)
        if other.get("id") != cat_id
        and other.get("parent_pair_id") == parent_pair
    ]


def creature_location(state, cat_id):
    """Return a friendly string for where the creature lives — room name or 'the village'."""
    for room in state.get("rooms", []):
        for c in room.get("creatures", []):
            if c.get("id") == cat_id:
                return room.get("name", "a room")
    for c in state.get("village", []):
        if c.get("id") == cat_id:
            return state.get("village_name", "Village")
    return "(missing)"


def cat_full_description(cat):
    """Combine a creature's freeform description with its colour and
    disability (if any) into a sentence-fragment for the detail panels.

    Three fields are stored separately on the creature dict — the
    `description` is drawn from the species' descriptions.txt at
    creation, `colors` is rolled at birth (inherited from parents
    where possible), and `disability` is drawn from disabilities.txt
    only if the species' disability_chance roll hit. Joining them at
    display time means later code can still read `cat["disability"]`
    or `cat["colors"]` directly.

    The colour line reads as "Colour: ginger and white." and is
    omitted entirely when the creature has no colors stamped (e.g.
    a species with an empty colors.txt). NVDA reads each line on its
    own when these are joined with newlines.
    """
    desc = (cat.get("description") or "").strip()
    disability = (cat.get("disability") or "").strip()
    colour_phrase = format_creature_colors(cat)
    parts = []
    if colour_phrase:
        parts.append(f"Colour: {colour_phrase}.")
    if desc:
        parts.append(desc)
    if disability:
        parts.append(disability)
    if not parts:
        return "(no description set)"
    return "\n".join(parts)


def _status_line_for(cat):
    """Return any status suffix the cat description should append, with a
    leading newline so callers can do `text += _status_line_for(cat)`.

    Combines: precise current age (this is the live region — the cat
    list shows a coarse snapshot, the detail panel here is what
    actually ticks up), young countdown when immature, elder /
    retired age tags, and the beloved high-affection marker.
    """
    parts = []
    age = int(cat_age_seconds(cat))
    if age <= 0:
        parts.append("Age: just born.")
    else:
        parts.append(f"Age: {format_duration_human(age)}.")
    if not is_mature(cat):
        remaining = time_until_mature(cat)
        parts.append(f"Still young: can pair or breed in {format_duration(remaining)}.")
    elif is_too_old_to_breed(cat):
        parts.append("Elder. Retired from breeding.")
    elif is_elder(cat):
        parts.append("Elder.")
    if is_beloved(cat):
        parts.append("Beloved (high affection).")
    return ("\n" + " ".join(parts)) if parts else ""


def format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds == 0:
        return "now"
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    mins, secs = divmod(seconds, 60)
    if secs == 0:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    return f"{mins}m {secs}s"


# Plain-language duration parsing for the settings dialog and room-type
# editor. Accepts forms like "1 hour", "30 min", "1h 30m", "an hour", etc.
# Digits only — word numbers like "one" / "two" are rejected with a friendly
# error so users get pointed at the digit form.
_DURATION_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}
_DURATION_WORD_NUMBERS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "twenty", "thirty",
    "forty", "fifty", "sixty", "hundred",
}


def parse_duration(text):
    """Parse a plain-language duration like '1 hour' or '1h 30m' into seconds.

    Treats 'a' / 'an' as 1 (so 'an hour' = 3600). Case-insensitive,
    whitespace-tolerant. Combined forms work: '1 hour 30 minutes', '1h30m'.

    Raises ValueError with a user-friendly message on bad input. Word
    numbers like 'one' or 'thirty' are explicitly rejected with a hint to
    use digits.
    """
    if text is None:
        raise ValueError("Please enter a duration like '1 hour' or '30 minutes'.")
    cleaned = text.strip().lower()
    if not cleaned:
        raise ValueError("Please enter a duration like '1 hour' or '30 minutes'.")

    # Catch word numbers up front so the error message points at the fix.
    tokens = re.findall(r"[a-z]+", cleaned)
    for tok in tokens:
        if tok in _DURATION_WORD_NUMBERS:
            raise ValueError(
                f"'{tok}' isn't a digit. Use numbers, like '1 hour' "
                "instead of 'one hour'."
            )

    # Replace 'a'/'an' standalone with '1' so 'an hour' parses cleanly.
    normalized = re.sub(r"\b(?:a|an)\b", "1", cleaned)

    # Match every (number, unit) pair. Number is required; unit defaults
    # to seconds if the user just types a bare integer (so '60' = 60s).
    pattern = re.compile(r"(\d+)\s*([a-z]*)")
    total = 0
    matched_any = False
    consumed = 0
    for m in pattern.finditer(normalized):
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "":
            multiplier = 1  # bare integer = seconds
        elif unit in _DURATION_UNITS:
            multiplier = _DURATION_UNITS[unit]
        else:
            raise ValueError(
                f"'{unit}' isn't a unit I recognize. Try 'seconds', "
                "'minutes', 'hours', or 'days'."
            )
        total += n * multiplier
        matched_any = True
        consumed = m.end()

    if not matched_any:
        raise ValueError(
            f"'{text.strip()}' isn't a duration I can read. "
            "Try '1 hour' or '30 minutes'."
        )

    # Reject leftover gibberish after the last match (e.g., '1 hour banana').
    leftover = normalized[consumed:].strip()
    if leftover:
        raise ValueError(
            f"I read part of '{text.strip()}' but '{leftover}' didn't fit. "
            "Try '1 hour' or '1h 30m'."
        )

    return total


def format_duration_human(seconds):
    """Friendly version of a seconds count: '1 hour', '2 days', '1h 30m'.

    Used for helper text and round-tripping decay_seconds through the
    room-type editor. Differs from format_duration() in preferring whole
    larger units (1 hour vs 60 minutes) and supporting days.
    """
    seconds = max(0, int(seconds))
    if seconds == 0:
        return "0 seconds"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    if secs:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " ".join(parts)


def format_age_for_list(seconds, under_minute_label="newborn"):
    """Coarser age format used on the cat list rows where the value
    updates every tick. format_duration_human shows seconds, which
    means a creature under a minute old rewrites its cell every
    second — NVDA gets interrupted on every update and can't read
    the row through. This bucket-rounds:

      - under 1 minute -> under_minute_label (default 'newborn')
      - 1-59 minutes   -> 'X minutes'
      - 1+ hours       -> 'X hours Y minutes' or just 'X hours' if mins=0
      - 1+ days        -> 'X days Y hours' or just 'X days' if hours=0

    The under_minute_label is overridable so the same formatter can be
    used for non-age durations. The village list's "In village for"
    column passes 'just arrived' instead of 'newborn' — 'newborn' is a
    life-stage word, wrong for a duration measurement.

    So the cell only flips on a real unit boundary (every 60 seconds
    at the worst, every hour after that, every day after that). The
    detail panel for a selected creature still uses
    format_duration_human for precision.
    """
    seconds = max(0, int(seconds))
    if seconds < 60:
        return under_minute_label
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        if hours:
            return f"{days} day{'s' if days != 1 else ''} {hours} hour{'s' if hours != 1 else ''}"
        return f"{days} day{'s' if days != 1 else ''}"
    if hours:
        if mins:
            return f"{hours} hour{'s' if hours != 1 else ''} {mins} minute{'s' if mins != 1 else ''}"
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{mins} minute{'s' if mins != 1 else ''}"


def join_names(names):
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


# prompt_rename and RenameDialog live near the other UI dialogs further
# down — they need wx widgets, and grouping with the dialogs keeps the
# data-model section clean.


def pair_key(id_a, id_b):
    return "+".join(sorted([id_a, id_b]))


def can_pair(cat_a, cat_b):
    """True if these two creatures can become a breeding pair.

    Same species required, both must be mature, neither past breeding-age
    retirement, and they can't be siblings (same parent pair). We don't
    track multi-generation lineage in V1.
    """
    if cat_a.get("species") != cat_b.get("species"):
        return False
    if not is_mature(cat_a) or not is_mature(cat_b):
        return False
    if is_too_old_to_breed(cat_a) or is_too_old_to_breed(cat_b):
        return False
    pa = cat_a.get("parent_pair_id")
    pb = cat_b.get("parent_pair_id")
    if pa is None or pb is None:
        return True
    return pa != pb


def closest_bonding_pair(state, room_id):
    """Return (cat_a, cat_b, remaining_seconds) for the unpaired-but-
    eligible M+F couple in `room_id` who are nearest to forming a pair,
    or None if no such couple exists.

    Reads pair_progress for the elapsed bonding time; if a couple has
    no entry yet (the timer hasn't been ticked since they became
    eligible), they're treated as 0 seconds in. Mirrors the eligibility
    rules in progress_pairing so the message matches what the
    auto-pair pass actually sees.
    """
    room = find_room(state, room_id)
    progress = state.get("pair_progress", {}) or {}
    threshold = float(SETTINGS.get("pair_formation_seconds", 1800) or 1800)
    unpaired_m = [
        c for c in room["creatures"]
        if c.get("pair_id") is None and c["sex"] == "M"
        and is_mature(c) and not is_too_old_to_breed(c)
    ]
    unpaired_f = [
        c for c in room["creatures"]
        if c.get("pair_id") is None and c["sex"] == "F"
        and is_mature(c) and not is_too_old_to_breed(c)
    ]
    best = None  # (remaining, cat_a, cat_b)
    for m in unpaired_m:
        for f in unpaired_f:
            if not can_pair(m, f):
                continue
            elapsed = float(progress.get(pair_key(m["id"], f["id"]), 0.0))
            remaining = max(0.0, threshold - elapsed)
            if best is None or remaining < best[0]:
                best = (remaining, m, f)
    if best is None:
        return None
    remaining, cat_a, cat_b = best
    return (cat_a, cat_b, remaining)


def closest_growing_pair(state, room_id):
    """Return (cat_a, cat_b, remaining_seconds) for the unpaired same-
    species opposite-sex couple in `room_id` whose later-maturing half
    matures soonest, or None if no such couple exists.

    Mirrors the eligibility rules in can_pair except for the maturity
    check — by definition this is for couples too young to bond yet.
    Used to make the breed_still_growing message specific.
    """
    room = find_room(state, room_id)
    unpaired_m = [
        c for c in room["creatures"]
        if c.get("pair_id") is None and c["sex"] == "M"
        and not is_too_old_to_breed(c)
    ]
    unpaired_f = [
        c for c in room["creatures"]
        if c.get("pair_id") is None and c["sex"] == "F"
        and not is_too_old_to_breed(c)
    ]
    best = None
    for m in unpaired_m:
        for f in unpaired_f:
            if m.get("species") != f.get("species"):
                continue
            pa = m.get("parent_pair_id")
            pb = f.get("parent_pair_id")
            if pa is not None and pb is not None and pa == pb:
                continue
            remaining = max(time_until_mature(m), time_until_mature(f))
            if best is None or remaining < best[0]:
                best = (remaining, m, f)
    if best is None:
        return None
    remaining, cat_a, cat_b = best
    return (cat_a, cat_b, remaining)


def progress_pairing(state, delta_seconds):
    """Advance pair-formation timers; return list of (cat_a, cat_b, room_name) for newly-formed pairs."""
    progress = state.setdefault("pair_progress", {})
    next_num = state.get("next_pair_num", 1)
    cats_by_id = {}
    cat_to_room_name = {}
    eligible_keys = set()

    for room in state["rooms"]:
        for c in room["creatures"]:
            cats_by_id[c["id"]] = c
            cat_to_room_name[c["id"]] = room["name"]
        unpaired_m = [
            c for c in room["creatures"]
            if c.get("pair_id") is None and c["sex"] == "M"
            and is_mature(c) and not is_too_old_to_breed(c)
        ]
        unpaired_f = [
            c for c in room["creatures"]
            if c.get("pair_id") is None and c["sex"] == "F"
            and is_mature(c) and not is_too_old_to_breed(c)
        ]
        for m in unpaired_m:
            for f in unpaired_f:
                if not can_pair(m, f):
                    continue
                key = pair_key(m["id"], f["id"])
                eligible_keys.add(key)
                progress[key] = progress.get(key, 0.0) + delta_seconds

    formed = []
    paired_now = set()
    ripe = sorted(
        ((k, v) for k, v in progress.items() if v >= SETTINGS["pair_formation_seconds"]),
        key=lambda kv: -kv[1],
    )
    for key, _ in ripe:
        id_a, id_b = key.split("+")
        if id_a in paired_now or id_b in paired_now:
            continue
        cat_a = cats_by_id.get(id_a)
        cat_b = cats_by_id.get(id_b)
        if not cat_a or not cat_b:
            continue
        if cat_a.get("pair_id") is not None or cat_b.get("pair_id") is not None:
            continue
        new_pid = f"p{next_num}"
        next_num += 1
        cat_a["pair_id"] = new_pid
        cat_b["pair_id"] = new_pid
        paired_now.update([id_a, id_b])
        formed.append((cat_a, cat_b, cat_to_room_name.get(id_a, "the room")))
        del progress[key]

    for key in list(progress.keys()):
        if key not in eligible_keys:
            del progress[key]

    state["next_pair_num"] = next_num
    return formed


# ===== Player action cores =====
# Pure state-mutation cores for the player actions whose logic used to live
# tangled inside wx event handlers. The UI handlers now gather input from
# widgets, call these, and handle their own feedback (sound, announce,
# refresh); a headless driver (an AI player) calls them directly. Each
# returns enough for the caller to compose an announcement. None of them
# touch wx, sound, or the screen.


def rename_creature(state, cat_id, new_name):
    """Rename the creature with `cat_id` to `new_name`, wherever it lives (a
    room or the village). Returns the old name on success, or None if no
    creature has that id or the new name is blank.
    """
    new_name = (new_name or "").strip()
    if not new_name:
        return None
    cat = find_creature_by_id(state, cat_id)
    if cat is None:
        return None
    old_name = cat.get("name", "")
    cat["name"] = new_name
    return old_name


def add_villager(state, species_id, sex, now=None):
    """Create one newborn villager of `species_id`/`sex` and append it to the
    village. Matches the seed paths: age 0, with mature_at set from the
    species' breeding age so it grows up on the normal timeline. Returns the
    new creature dict, or None if the species isn't loaded.
    """
    if species_id not in SPECIES_DATA:
        return None
    if now is None:
        now = time.time()
    spec = SPECIES_DATA.get(species_id, {}).get("spec", {})
    breeding_age = float(spec.get("breeding_age_seconds", 0) or 0)
    villager = new_creature(species_id, sex, age_seconds=0.0)
    villager["pair_id"] = None
    villager["moved_to_village_at"] = now
    if breeding_age > 0:
        villager["mature_at"] = now + breeding_age * lifecycle_pace()
    state.setdefault("village", []).append(villager)
    return villager


def set_auto_breeding(state, on):
    """Persist the auto-breeding choice on the save. Returns the new bool.
    The engine's offline catch-up reads this value off `state`; the UI keeps
    its own menu-label mirror.
    """
    value = bool(on)
    state["auto_breeding"] = value
    return value


def apply_settings(state, new_settings):
    """Write a dict of {setting_key: value} to the live SETTINGS shelf (in
    place -- it's shared across modules) and persist each on the save.
    Returns the list of keys applied. The caller is responsible for parsing
    / clamping the values before handing them over.
    """
    applied = []
    for key, value in new_settings.items():
        SETTINGS[key] = value
        state.setdefault("settings", {})[key] = value
        applied.append(key)
    return applied


def plan_room_retype(state, room_id, new_name=None, new_type=None,
                     allowed_species=None):
    """Work out what changing a room's name / type / allowed-species would do,
    WITHOUT mutating anything. Returns a plan dict whose "status" is one of:

      "no_room"            -- room_id matches no room
      "no_change"          -- nothing differs from the current room
      "no_allowed_species" -- the new type allows species but none were ticked
      "ok"                 -- the change is valid; the rest of the dict holds
                              name_changed / type_changed / allowed_changed
                              flags, old_name / new_name / old_type / new_type,
                              target_type, effective_allowed, and relocations.

    `relocations` is a list of (cat, target_room_or_None, reason) for the
    creatures that would no longer fit -- target None means the village, and
    `reason` is a find_room_for_species placement code. apply_room_retype
    consumes the plan. Computing it changes nothing, so the UI can show the
    plan and ask for confirmation first.
    """
    room = find_room(state, room_id)
    if room is None:
        return {"status": "no_room"}
    old_name = room["name"]
    old_type = room.get("type", "indoor")
    old_allowed = list(room.get("allowed_species") or [])
    name_changed = bool(new_name) and new_name != old_name
    type_changed = bool(new_type) and new_type != old_type
    new_allowed = (list(allowed_species)
                   if allowed_species is not None else old_allowed)
    allowed_changed = set(new_allowed) != set(old_allowed)
    if not name_changed and not type_changed and not allowed_changed:
        return {"status": "no_change"}

    target_type = new_type if type_changed else old_type
    target_compat = room_type_compatible_species(target_type)
    effective_allowed = [s for s in new_allowed if s in target_compat]
    if not effective_allowed and target_compat:
        return {"status": "no_allowed_species"}

    incompatible = [
        c for c in room["creatures"]
        if c.get("species") not in effective_allowed
    ]
    relocations = []
    if incompatible:
        # Plan with simulated occupancy so several same-species evictees
        # don't all get assigned to one single-slot fallback room. Skip the
        # source room -- we're evicting from it.
        other_rooms = [r for r in state["rooms"] if r["id"] != room_id]
        sim_used = {r["id"]: len(r["creatures"]) for r in other_rooms}
        for cat in incompatible:
            target, reason = find_room_for_species(
                other_rooms, cat.get("species", "cat"),
                primary_room_id=None, sim_used=sim_used,
            )
            if target is not None:
                sim_used[target["id"]] += 1
            relocations.append((cat, target, reason))

    return {
        "status": "ok",
        "name_changed": name_changed,
        "type_changed": type_changed,
        "allowed_changed": allowed_changed,
        "old_name": old_name,
        "new_name": new_name,
        "old_type": old_type,
        "new_type": target_type,
        "target_type": target_type,
        "effective_allowed": effective_allowed,
        "relocations": relocations,
    }


def apply_room_retype(state, room_id, plan):
    """Execute a plan from plan_room_retype: relocate the incompatible
    creatures, then apply the name / type / allowed-species changes. Returns
    the list of moved creature names (empty if none moved). Safe no-op if the
    room vanished or the plan isn't "ok". Mutates state.
    """
    room = find_room(state, room_id)
    if room is None or plan.get("status") != "ok":
        return []
    moved_names = []
    for cat, target, _reason in plan.get("relocations", []):
        if target is not None:
            move_creature_to_room(state, room_id, target["id"], cat["id"])
        else:
            move_creature_to_village(state, room_id, cat["id"])
        moved_names.append(cat["name"])
    if plan["name_changed"]:
        room["name"] = plan["new_name"]
    if plan["type_changed"]:
        target_type_spec = ROOM_TYPES.get(plan["new_type"], {})
        room["type"] = plan["new_type"]
        room["meters"] = {m["key"]: 1.0
                          for m in target_type_spec.get("meters", [])}
        room["meter_last_refilled"] = {}
    room["allowed_species"] = plan["effective_allowed"]
    return moved_names


# ===== Park / inventory / room building =====

def reset_digs_if_new_day(state):
    today = date.today().isoformat()
    if state.get("last_dig_date") != today:
        state["last_dig_date"] = today
        state["digs_used_today"] = 0


def get_dig_outcome_table():
    """Build the dig outcome probability table from current SETTINGS, normalized."""
    raw = [
        ("nothing", SETTINGS["dig_chance_nothing"]),
        ("common", SETTINGS["dig_chance_common"]),
        ("uncommon", SETTINGS["dig_chance_uncommon"]),
        ("object", SETTINGS["dig_chance_object"]),
        ("treasure", SETTINGS["dig_chance_treasure"]),
    ]
    total = sum(max(0.0, w) for _, w in raw)
    if total <= 0:
        return [("nothing", 1.0)]
    return [(cat, max(0.0, w) / total) for cat, w in raw]


def digs_remaining(state):
    reset_digs_if_new_day(state)
    return max(0, SETTINGS["digs_per_day"] - state.get("digs_used_today", 0))


def do_dig(state):
    """Run a single dig. Returns an event dict describing the outcome.

    {kind: 'no_digs_left'}
    {kind: 'nothing'}
    {kind: 'item', tier: 'common'|'uncommon', name: str}
    {kind: 'object'|'treasure', name: str, description: str}
    """
    reset_digs_if_new_day(state)
    if state.get("digs_used_today", 0) >= SETTINGS["digs_per_day"]:
        return {"kind": "no_digs_left"}
    state["digs_used_today"] = state.get("digs_used_today", 0) + 1
    inventory = state.setdefault("inventory", {})
    inventory.setdefault("common", {})
    inventory.setdefault("uncommon", {})
    inventory.setdefault("objects", {})
    inventory.setdefault("treasures", {})

    roll = random.random()
    cumulative = 0.0
    chosen = "nothing"
    for category, weight in get_dig_outcome_table():
        cumulative += weight
        if roll < cumulative:
            chosen = category
            break

    if chosen == "common" and ITEMS_COMMON:
        name = random.choice(ITEMS_COMMON)
        inventory["common"][name] = inventory["common"].get(name, 0) + 1
        return {"kind": "item", "tier": "common", "name": name}

    if chosen == "uncommon" and ITEMS_UNCOMMON:
        name = random.choice(ITEMS_UNCOMMON)
        inventory["uncommon"][name] = inventory["uncommon"].get(name, 0) + 1
        return {"kind": "item", "tier": "uncommon", "name": name}

    def _stack_collectible(section_key, entry):
        section = inventory.setdefault(section_key, {})
        slot = section.setdefault(
            entry["name"],
            {"count": 0, "description": entry.get("description", "")},
        )
        slot["count"] += 1
        # Refresh description in case the modder updated it; takes the
        # newest non-empty version.
        if entry.get("description"):
            slot["description"] = entry["description"]

    if chosen == "object" and OBJECTS:
        entry = random.choice(OBJECTS)
        _stack_collectible("objects", entry)
        return {"kind": "object", "name": entry["name"], "description": entry["description"]}

    if chosen == "treasure" and TREASURES:
        entry = random.choice(TREASURES)
        _stack_collectible("treasures", entry)
        return {"kind": "treasure", "name": entry["name"], "description": entry["description"]}

    return {"kind": "nothing"}


def _migrate_collectibles(raw):
    """Coerce an objects/treasures inventory section to the dict-of-stacks
    shape: ``{name: {"count": N, "description": "..."}}``. Tolerant of:

    - missing / None     → returns empty dict
    - already-a-dict     → trusts the values; ensures count + description
                           are present
    - legacy list[{name, description, ...}]
                         → groups by name and counts entries

    Returns a fresh dict so the caller can `inventory["objects"] = ...`
    without aliasing concerns.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        out = {}
        for name, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            count = max(0, int(entry.get("count", 0)))
            if count == 0:
                continue
            out[name] = {
                "count": count,
                "description": str(entry.get("description", "")),
            }
        return out
    if isinstance(raw, list):
        out = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name:
                continue
            slot = out.setdefault(
                name,
                {"count": 0, "description": entry.get("description", "")},
            )
            slot["count"] += 1
            # If the first occurrence had no description but a later one
            # does, take the later one — better than nothing.
            if not slot["description"] and entry.get("description"):
                slot["description"] = entry["description"]
        return out
    return {}


def total_collectible_count(section):
    """Sum the counts across a stacked inventory section. Tolerant of the
    legacy list shape for safety, though normalisation should have
    converted it to the dict shape on state load.
    """
    if isinstance(section, dict):
        return sum(int(e.get("count", 0)) for e in section.values())
    if isinstance(section, list):
        return len(section)
    return 0


def total_common_items(state):
    return sum(state.get("inventory", {}).get("common", {}).values())


def find_item_tier(state, item_name):
    """Return ('common'|'uncommon', count) for the given item, or (None, 0)."""
    inv = state.get("inventory", {})
    if item_name in inv.get("common", {}):
        return "common", inv["common"][item_name]
    if item_name in inv.get("uncommon", {}):
        return "uncommon", inv["uncommon"][item_name]
    return None, 0


def get_room_recipe(type_spec):
    """Return the build recipe dict, falling back to default_cost as a generic
    pile of common items if the type still uses the older flat-cost format.
    """
    recipe = type_spec.get("build_recipe")
    if recipe:
        return dict(recipe)
    cost = type_spec.get("default_cost")
    if cost:
        # Legacy fallback: any common items totalling `cost`. Rendered to
        # the user as a special pseudo-recipe key 'common items'.
        return {"_any_common": int(cost)}
    return {}


def get_treasure_cost(type_spec):
    """How many treasures (any kind) this room type costs to build, beyond
    its build_recipe items. 0 = none required (the common case). The
    player picks which treasure to spend at build time.
    """
    if not type_spec:
        return 0
    try:
        return max(0, int(type_spec.get("treasure_cost", 0) or 0))
    except (TypeError, ValueError):
        return 0


def list_treasures(state):
    """Return [(name, count, description), ...] for every treasure the
    player owns (count > 0). Stable ordering by name so UIs can render
    a consistent picker.
    """
    treasures = (state.get("inventory") or {}).get("treasures") or {}
    out = []
    for name in sorted(treasures.keys()):
        info = treasures[name]
        if isinstance(info, dict):
            count = int(info.get("count", 0) or 0)
            desc = str(info.get("description", "") or "")
        else:
            count = int(info or 0)
            desc = ""
        if count > 0:
            out.append((name, count, desc))
    return out


def total_treasures(state):
    return sum(c for _, c, _ in list_treasures(state))


def recipe_shortfall(state, recipe, type_spec=None):
    """Returns a dict of {item: missing_count} for items the recipe needs but
    the player doesn't have enough of. Empty dict means recipe is affordable.

    If `type_spec` is given AND it has a non-zero `treasure_cost`, the
    return dict will include a pseudo-key `_treasure` with the missing
    treasure count when the player is short. Existing callers without a
    type_spec keep the old behaviour (recipe-only check, no treasure).
    """
    missing = {}
    for item_name, needed in recipe.items():
        if item_name == "_any_common":
            available = total_common_items(state)
            if available < needed:
                missing[item_name] = needed - available
            continue
        _, available = find_item_tier(state, item_name)
        if available < needed:
            missing[item_name] = needed - available
    cost = get_treasure_cost(type_spec)
    if cost > 0:
        have = total_treasures(state)
        if have < cost:
            missing["_treasure"] = cost - have
    return missing


def deduct_recipe(state, recipe):
    """Spend the recipe from inventory.

    Returns dict {name: taken_count} on success, or None if any item is short.
    Names with a leading underscore are pseudo-keys (e.g., '_any_common'
    means 'this many common items, any kind').
    """
    if recipe_shortfall(state, recipe):
        return None
    taken = {}
    inv = state.get("inventory", {})
    for item_name, needed in recipe.items():
        if item_name == "_any_common":
            common = inv.setdefault("common", {})
            remaining = needed
            for name, _ in sorted(common.items(), key=lambda kv: -kv[1]):
                if remaining == 0:
                    break
                take = min(common[name], remaining)
                taken[name] = taken.get(name, 0) + take
                remaining -= take
            for name in list(common.keys()):
                if name in taken:
                    common[name] -= taken[name]
                    if common[name] == 0:
                        del common[name]
            continue
        tier, _ = find_item_tier(state, item_name)
        if tier:
            inv[tier][item_name] -= needed
            if inv[tier][item_name] == 0:
                del inv[tier][item_name]
        taken[item_name] = needed
    return taken


_IRREGULAR_PLURALS = {
    "leaf": "leaves",
    "fish": "fish",
    "moss": "moss",
}


def pluralize(word, count):
    """Best-effort English pluralization for item names.

    Handles a small irregular table plus the s/sh/ch/y endings; everything
    else gets a plain "+s". Modders can extend the irregular table here.
    """
    if count == 1 or not word:
        return word
    low = word.lower()
    if low in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[low]
    if low.endswith(("s", "sh", "ch", "x", "z")):
        return word + "es"
    if low.endswith("y") and (len(word) < 2 or word[-2].lower() not in "aeiou"):
        return word[:-1] + "ies"
    return word + "s"


def format_recipe(recipe, type_spec=None):
    """Pretty-print a recipe for status text. Empty recipe -> 'free'.
    When a `type_spec` with a `treasure_cost` is given, appends "+ N
    treasure(s)" so the player sees the full build cost in one line.
    """
    parts = []
    for name, count in recipe.items():
        if name == "_any_common":
            parts.append(f"{count} {pluralize('common item', count)}")
        else:
            parts.append(f"{count} {pluralize(name, count)}")
    cost = get_treasure_cost(type_spec)
    if cost > 0:
        parts.append(f"{cost} {pluralize('treasure', cost)}")
    if not parts:
        return "free"
    return ", ".join(parts)


def format_shortfall(missing):
    if not missing:
        return "have all"
    parts = []
    for name, count in missing.items():
        if name == "_any_common":
            parts.append(f"{count} more {pluralize('common item', count)}")
        elif name == "_treasure":
            parts.append(f"{count} more {pluralize('treasure', count)}")
        else:
            parts.append(f"{count} more {pluralize(name, count)}")
    return "need " + ", ".join(parts)


def build_new_room(state, room_type_id, room_name, starter_species_id=None,
                   add_starters=True, allowed_species=None, treasure_name=None):
    """Spend the type's recipe from inventory and add a new room of that type.

    `starter_species_id` picks which compatible species to seed the room with
    (when a type has multiple). Falls back to the first compatible species.
    If `add_starters` is True (default) and the chosen species has
    starter_pairs > 0, the room is auto-populated. Pass False for an empty
    room.

    `allowed_species` (optional) lets the caller narrow the room's
    allowed-species list to a subset of `room_type_compatible_species`
    (the species whose specs claim this room type). Items in the
    supplied list that aren't compatible are silently dropped; an
    empty result falls back to the full compatible list (so a footgun
    mis-narrowing can't lock the room down). Pass None to default to
    the type's full compat list.

    `treasure_name` is required when the type has a non-zero treasure_cost
    (the Glade does). The named treasure is consumed before the rest of
    the recipe; if it doesn't exist or the player has none, the build
    fails before any inventory is spent.

    Returns (room_dict, taken_items) on success; (None, None) if the type
    doesn't exist or the player can't afford the recipe.
    """
    type_spec = ROOM_TYPES.get(room_type_id)
    if type_spec is None:
        return None, None
    treasure_needed = get_treasure_cost(type_spec)
    recipe = get_room_recipe(type_spec)
    # Affordability check FIRST — including the treasure availability —
    # so a partial-success state where items are spent but the treasure
    # is missing can't happen.
    if recipe_shortfall(state, recipe, type_spec=type_spec):
        return None, None
    if treasure_needed > 0:
        if not treasure_name:
            return None, None
        # Consume the named treasure first. If the named treasure isn't
        # present (race / stale picker), fail before spending the rest.
        consumed = consume_treasure(state, treasure_name)
        if consumed is None:
            return None, None
    taken = deduct_recipe(state, recipe)
    if taken is None:
        # Shouldn't happen given the shortfall check above, but if it
        # somehow does and we already consumed a treasure, we won't
        # rollback — this is a defensive return that mirrors prior
        # behaviour rather than risking a worse half-state.
        return None, None
    if treasure_needed > 0 and treasure_name:
        # Surface the spent treasure in the taken dict so the caller's
        # announcement reads "Used: ..., 1 cozy basket (treasure)."
        taken[f"{treasure_name} (treasure)"] = treasure_needed
    next_num = state.get("next_room_num", len(state.get("rooms", [])) + 1)
    meters = {m["key"]: 1.0 for m in type_spec.get("meters", [])}
    compatible = room_type_compatible_species(room_type_id)
    default_slots = type_spec.get("default_slots", 4)

    if allowed_species is not None:
        narrowed = [s for s in allowed_species if s in compatible]
        allowed = narrowed if narrowed else list(compatible)
    else:
        allowed = list(compatible)

    creatures = []
    if starter_species_id and starter_species_id in allowed:
        species_id = starter_species_id
    elif allowed:
        species_id = allowed[0]
    else:
        species_id = compatible[0] if compatible else None
    if add_starters and species_id and species_id in SPECIES_DATA:
        spec = SPECIES_DATA[species_id]["spec"]
        starter_pairs = spec.get("starter_pairs", 0)
        if starter_pairs > 0:
            next_pair = state.get("next_pair_num", 1)
            for _ in range(starter_pairs):
                pair_id = f"p{next_pair}"
                next_pair += 1
                creatures.append(new_creature(species_id, "F", pair_id=pair_id))
                creatures.append(new_creature(species_id, "M", pair_id=pair_id))
            state["next_pair_num"] = next_pair
            # Make sure the default slot count fits the starters comfortably.
            default_slots = max(default_slots, len(creatures))

    room = {
        "id": f"room_{next_num}",
        "name": room_name or f"{type_spec.get('name', 'Room')} {next_num}",
        "type": room_type_id,
        "slot_count": default_slots,
        "allowed_species": allowed,
        "meters": meters,
        "meter_last_refilled": {},
        "creatures": creatures,
    }
    state["rooms"].append(room)
    state["next_room_num"] = next_num + 1
    return room, taken


