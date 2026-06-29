# Modding Time for Family

Everything in Time for Family that's user-facing — the species, the room
types, the names, the descriptions, the things you find while digging — is
loaded from plain files. Edit those files and the game changes the next
time you launch.

You don't need to write any code to mod the game. There are two ways to
add or change content:

1. **The Mods menu inside the game** (easiest). Lets you add, edit, and
   delete species and room types through dialogs. No JSON to write by
   hand. See **Modding via the Mods menu** below.
2. **Editing the files directly** (still fully supported). Useful for
   bulk edits, sharing mods as a zip, or tweaking the text pools (names,
   descriptions, pet responses, dig items). The rest of this guide
   covers this path.

> **Restart Time for Family after editing any file by hand.** Changes
> made through the Mods menu apply immediately.

## Starter set + adding species

A fresh save starts **completely empty** — no rooms, no village
creatures. The first time you launch (or after **File → New game**)
the **Species** dialog opens and asks you to pick one species from
the loaded library, or design a brand-new one with **Create a new
species…**. A starter pair of babies arrives in the village; from
there you build rooms and play normally. The dialog stays open after
each action so you can keep curating, and it re-opens any time from
**File → Species** when you want to bring another species in, edit
one, or delete one you don't want any more.

Five species ship loaded by default: **cat** (indoor),
**dog** (indoor + outdoor), **rabbit** (outdoor), **fish** (aquatic),
and **chicken** (aviary). These all appear in the Species picker
without any extra setup.

To add a new species (yours or someone else's), drop its JSON spec
into `user_data/types/species/<id>.json` and its text pools in
`user_data/text/species/<id>/`. Restart the game; it'll appear in
the Species dialog. If you also want it to survive a `user_data/`
wipe (a "factory reset"), put copies in `assets/types/species/` and
`assets/text/species/` too — `ensure_user_data_dir()` copies any
missing assets/ subtree into user_data/ on launch.

Removing a species through the Species dialog deletes the spec
from `user_data/types/species/` and the text pool directory from
`user_data/text/species/`. The shipped factory copy in `assets/`
is not touched — you can always copy it back to undo the removal.
If the species has creatures in your save, the delete confirmation
asks whether to also remove those creatures (rooms, village, and
any pending births) before clearing the spec; declining cancels the
whole operation.

## Two folders: `assets/` (factory) and `user_data/` (live)

Time for Family ships factory copies of every species, room type, name
pool, and announcement template under `assets/`. The first time you
launch the game, those are copied into a sibling `user_data/` folder
and from then on the game reads and writes only to `user_data/`. The
`assets/` tree is treated as read-only — the in-game tools (File →
Species, Mods → Manage room types, the rename-and-save flow, etc.)
never modify it.

What this means in practice:

- **End users** edit files under `user_data/`. To revert a species or
  room type to its shipped default, copy the file from
  `assets/types/species/cat.json` (or wherever) back into
  `user_data/types/species/`. The next launch will load the original.
- **Modders sharing a mod** put their files in `assets/` (or zip them
  for distribution as if they were `assets/` overlays). When a player
  installs the zip into their `assets/`, the next launch propagates
  it into their `user_data/`. The factory-then-live separation means
  player customisations don't get clobbered by mod updates.
- **Hand-editing while playing**: write to `user_data/` for changes
  you want active immediately, or to `assets/` if you intend it as a
  new factory default. Editing `assets/` only affects what gets
  copied in on a fresh `user_data/`; existing `user_data/` is not
  touched after first launch.

## Modding via the in-game menus

Open **File → Species** for everything to do with species (pick a
starter pair, add, edit, delete) and **Mods → Manage room types…**
for room types.

The Species dialog is browser-style: list of loaded species, a detail
panel that updates per selection, and four buttons — **Bring [species]
home** (seeds a starter pair into the village), **Edit selected…**
(opens the species editor), **Delete selected…** (with a confirmation
that names how many creatures of that species are currently in your
save), and **Create a new species…** (opens the editor blank). It
stays open after each action so you can keep working without
re-opening it. The species editor's basic view stays focused on the
fields most species need; rarely-touched knobs (sex shorts, starter
age range, twin / disability chances, litter overrides) hide inside
an "Advanced settings" section that you expand only when you need it.

Manage room types shows a list of room types with Add…, Edit…, and
Delete buttons. The Add and Edit dialog exposes every field this
guide describes — IDs, names, meters, recipes, compatible species,
treasure cost, and so on.

Deleting:

- **Deleting a species** removes every creature of that species from your
  rooms, your village, and any pending births. The game warns you with
  the count before going through. There is no undo, so if you want to
  preserve creatures, move or rename the species instead.
- **Deleting a room type** removes every room of that type. Creatures
  living in those rooms are moved back to the village. Their pair bonds
  are cleared (they'll re-pair automatically once you build a compatible
  room). After a room-type delete, restart the game so the room picker
  refreshes.

The Mods menu writes the same JSON files documented below, into
`user_data/types/species/` and `user_data/types/room_types/`. You can
edit the files by hand later, or hand off your mod as a zip.

## What lives where

```
time-for-family/
├── time_for_family.pyw      # the game itself — don't touch unless you know Python
├── state.json               # your save file — also don't touch unless you know what you're doing
├── assets/                  # SHIPPED — factory copies, never modified after first launch
│   ├── sounds/              # .wav files — replace any with your own to swap a sound
│   ├── types/
│   │   ├── species/         # factory species specs
│   │   └── room_types/      # factory room-type specs
│   └── text/
│       ├── species/<id>/    # factory text pools (names, descriptions, pet responses, disabilities)
│       ├── items_common.txt
│       ├── items_uncommon.txt
│       ├── objects.txt
│       ├── treasures.txt
│       ├── ambient.txt
│       └── announcements.txt
├── user_data/               # LIVE — created on first launch, copied from assets/
│   ├── types/
│   │   ├── species/         # what the game reads and the in-game editors write
│   │   └── room_types/
│   └── text/
│       ├── species/<id>/    # per-species name, description, and pet-response pools
│       │   ├── names_female.txt
│       │   ├── names_male.txt
│       │   ├── descriptions.txt
│       │   ├── pet_responses.txt
│       │   └── disabilities.txt
│       ├── items_common.txt        # park-wide: common dig finds (used in recipes too)
│       ├── items_uncommon.txt      # park-wide: uncommon dig finds
│       ├── objects.txt             # park-wide: dig-found objects (`name | description`)
│       ├── treasures.txt           # park-wide: dig-found treasures (`name | description`)
│       ├── ambient.txt             # park-wide: ambient observation pool
│       └── announcements.txt       # every line of NVDA / status-bar text the game can speak
└── lib/                     # third-party libraries (NVDA controller client)
```

The park-wide text files (`items_common.txt`, `items_uncommon.txt`,
`objects.txt`, `treasures.txt`, `ambient.txt`) and the per-species
text files get recreated empty if missing, so you can delete one to
clear it or copy one to use as a template.

The species and room-type JSON files in `user_data/types/` are **not**
auto-recreated if you delete them. If you delete one and want it back,
copy it from `assets/types/` (the factory copy is still there), or
recreate it from the **Mods** menu.

## Colours and inheritance

Each species has a `colors.txt` pool alongside the names and
descriptions files. Each creature is born with two colours, one
inherited from each parent:

- Most births: the baby gets one colour from each parent's colour
  list (random pick from the parent's two colours per slot).
- Mutation slot (default ~5% per colour, per Settings →
  `color_mutation_chance`): instead of inheriting that slot, the
  baby rolls a fresh colour from the species' pool. Set the chance
  to 0 for strict inheritance, or to 1 for parents-don't-matter.

Empty pool (`colors.txt` with no entries) = no colours; the detail
panel hides the "Colour:" line entirely. Old saves get a one-time
retroactive roll on first launch under this version, so existing
creatures don't suddenly look identical.

Descriptions can use `{color}` and `{color2}` to mention the
creature's colours inline:

```
A {color} cat with a {color2} blaze on their forehead.
Soft {color} fur and even softer manners.
```

The placeholders are filled in from the creature's stamped colours
at birth, so the prose stays stable across the creature's life.
Descriptions without placeholders work unchanged. If you write
`{color}` but the species' colour pool is empty, the placeholder
becomes an empty string and the engine tidies up the resulting
double-spaces and stray articles.

## Editing the simple text files

The text files (everything in `assets/text/`) all follow the same
conventions:

- **One entry per line.**
- **Lines starting with `#` are comments** — used for headers and notes.
- **Empty lines are ignored.**
- **Trailing whitespace is trimmed**, so don't worry about extra spaces.

Two of them (`objects.txt`, `treasures.txt`, and per-species `descriptions`
files in some cases) use a `name | description` format with a pipe between
the name and its description:

```
tarnished silver locket | A small clasp marks where a photo once lived.
```

Pet responses use `{name}` as a placeholder for the creature's name:

```
{name} purrs softly.
{name} nuzzles your hand.
```

When the game picks one of those, `{name}` gets replaced with the actual
cat's name.

## Renaming creatures and growing your name list

When you rename a creature in-game, there's a checkbox: **"Also add this
name to my [doe rabbit] names list"**. Tick that, and the new name gets
appended to the right text file (e.g. `assets/text/species/rabbit/names_female.txt`)
so future babies can be born with it. The file gets a clean newline appended
even if the previous line didn't end with one.

## Adding a new species

The fastest path is **File → Species → Create a new species…** in-game — that
dialog has the same fields described below. Read on if you'd rather edit
the JSON directly, or you want to understand what each field does.

Say you want to add **dragons**. Three steps:

### 1. Make the species spec

Create `assets/types/species/dragon.json`:

```json
{
  "id": "dragon",
  "name": "Dragon",
  "name_plural": "Dragons",
  "sex_label_female": "queen",
  "sex_label_male": "king",
  "sex_short_female": "F",
  "sex_short_male": "M",
  "compatible_room_types": ["lair"],
  "starter_age_min": 100,
  "starter_age_max": 500,
  "starter_pairs": 2,
  "text_directory": "dragon",
  "care_action_label": "Honor"
}
```

What each field does:

- **`id`** — internal name, lowercase, no spaces. Must match the filename
  before `.json`.
- **`name` / `name_plural`** — what the UI says ("Dragon", "Dragons").
- **`sex_label_*`** — what the UI says when describing a creature's sex
  ("queen dragon", "king dragon"). For cats this is "female"/"male"; for
  rabbits it's "doe"/"buck".
- **`sex_short_*`** — short tags shown in the cats-list column ("F"/"M"
  by default; you can use anything short, like "Q"/"K", but most people
  expect F/M).
- **`compatible_room_types`** — list of room type IDs this species can
  live in. They have to exist (see next section).
- **`starter_age_min` / `starter_age_max`** — when a new creature is
  created, their starting age (in days) is a random number in this range.
- **`starter_pairs`** — when the player builds a room compatible with
  this species, this many breeding pairs are spawned automatically.
- **`text_directory`** — name of the folder under `assets/text/species/`
  that holds this species' text pools. Usually matches the `id`.
- **`care_action_label`** — what the pet button says ("Honor selected
  dragon" instead of "Pet selected cat"). Common choices: "Pet", "Feed",
  "Visit", "Brush".
- **`twin_chance`** *(optional, default 0)* — probability per baby in a
  clutch of producing a fraternal twin (fresh sex, name, and description,
  same parent pair). Birds use `0.10`. Set to `0.0` (or omit) for species
  that don't twin.
- **`litter_label`** *(optional, default "litter")* — singular word for
  one group of newborns from this species, used in birth announcements
  ("A clutch arrived from pair p3…"). Cats / dogs / rabbits use
  `"litter"`; chickens / fish use `"clutch"`; mod something else if your
  species has its own word. Read with a back-compat fallback to the
  older `basket_label` field, so legacy mod JSONs keep working.
- **`litter_label_plural`** *(optional, default singular + "s")* — plural
  of `litter_label`. Used in summary lines that span multiple births of
  the same species ("Three clutches arrived…"). Same back-compat
  fallback to the older `basket_label_plural`.
- **`breeding_age_seconds`** *(optional, default 0)* — how long a baby of
  this species must wait *after they're born* before they can
  auto-pair or breed. 0 (or missing) = mature immediately. Defaults
  shipped (in real seconds): cats / fish = 86400 (24h), bird / chicken =
  64800 (18h), rabbit = 43200 (12h), hamster = 21600 (6h). Existing
  creatures with no `mature_at` timestamp on them are treated as already
  mature, so older saves don't break.
- **`elder_age_seconds`** *(optional, default 0)* — the **single
  "they're old now" milestone**. When a creature's `age_seconds`
  reaches this number, three things happen at once:
  1. The description box shows an "Elder." tag.
  2. They **retire from breeding**: pairs with them are skipped by
     both auto-pairing and breed attempts.
  3. (If **the wild** is enabled in Settings) they become eligible
     for wild emigration — see "The wild" below — unless they have
     a sanctuary disability.
  0 (or missing) = this species never becomes elder and never retires.
  All life-stage fields are in real (wall-clock) seconds; the species
  editor accepts plain language like `"3 days"` or `"60 hours"` and
  stores the parsed seconds. Defaults shipped (after the May 2026 4×
  speed-up): cat / dog = 72000 (20h), chicken = 54000 (15h),
  rabbit / fish = 45000 (12.5h).
- **`max_breeding_age_seconds`** *(legacy, deprecated)* — pre-merge
  specs split "becomes elder" from "retires from breeding" into two
  stages. The two have been merged into `elder_age_seconds`. If a
  legacy modder spec carries only `max_breeding_age_seconds` (no
  `elder_age_seconds`), the engine falls back to it via the
  `species_old_age_seconds()` helper, so the spec keeps working
  without a forced re-edit. Don't write new specs that use this key
  — open the spec in the species editor and Save once to migrate
  cleanly.
- **`min_babies` / `max_babies`** *(optional, override the global
  Settings)* — the smallest and biggest number of babies that arrive
  in a single successful breeding for this species. Same fall-back
  pattern as a meter's `decay_seconds`: omit to use the global
  Smallest / Biggest litter size from File → Settings (default 1 and
  4); set either or both to give this species its own range. Both
  must be ≥ 1 and `min_babies` must be ≤ `max_babies` if both are
  set. The species editor UI exposes the same two fields with empty
  meaning "inherit". No species ships with overrides — the global
  defaults are deliberately gentle and apply uniformly until a modder
  opts in.

Old saves and old species JSONs that used the legacy game-day fields
(`elder_age_days`, `max_breeding_age_days`, `starter_age_min`,
`starter_age_max`, and `age_days` on creatures) are auto-migrated to
the new `_seconds` fields on load — multiplied by the legacy default
of 3600 seconds per game day. Modder JSONs are not rewritten on disk
until you next Save them in the File → Species editor.

After the **elder/retire merge**, a legacy spec carrying both
`elder_age_seconds` and `max_breeding_age_seconds` will treat
`elder_age_seconds` as the single milestone for both becoming an
elder and retiring from breeding. Specs carrying only
`max_breeding_age_seconds` are honored as a fallback. Saving the
spec through the species editor drops the legacy key.

### 2. Make the species' text files

Create the folder `assets/text/species/dragon/` and put four files inside:

**`names_female.txt`**
```
# Female dragon names (queens). One per line.
Vermithrax
Glaurung
Smaug-Adjacent
Fafnir's Sister
```

**`names_male.txt`**
```
# Male dragon names (kings). One per line.
Drogon
Saphira
Ancalagon
```

**`descriptions.txt`**
```
# Dragon descriptions. One per line.
Has scales the colour of polished obsidian.
Hoards copper coins, but only the shiny ones.
Breathes a single faint puff of smoke when surprised.
```

**`pet_responses.txt`**
```
# Pet responses for dragons. {name} is replaced with the dragon's name.
{name} dips their head in regal acknowledgement.
{name} blows a polite stream of warm air.
{name} stretches their wings and yawns.
```

### 3. Make the matching room type

Dragons need somewhere to live. See **Adding a new room type** below.

That's the whole flow. Restart the game and Dragons will show up as a
buildable room type's compatible species.

## Adding a new room type

Same deal: **Mods → Manage room types… → Add…** in-game does this with
no JSON. The dialog mirrors the fields below.

Continuing the dragon example, create `assets/types/room_types/lair.json`:

```json
{
  "id": "lair",
  "name": "Lair",
  "description": "A cavern. The kind of place a dragon might call home.",
  "meters": [
    {
      "key": "food",
      "label": "Hoard",
      "verb_present": "Refresh",
      "verb_past": "Refreshed",
      "low_word": "depleted in",
      "empty_word": "depleted",
      "full_word": "stocked"
    },
    {
      "key": "water",
      "label": "Spring water",
      "verb_present": "Refill",
      "verb_past": "Refilled",
      "low_word": "empty in",
      "empty_word": "empty",
      "full_word": "full"
    },
    {
      "key": "warmth",
      "label": "Cavern warmth",
      "verb_present": "Stoke",
      "verb_past": "Stoked",
      "low_word": "cold in",
      "empty_word": "cold",
      "full_word": "toasty"
    }
  ],
  "build_recipe": {"stone": 8, "pebble": 8, "small bell": 1},
  "default_slots": 4
}
```

(Room-type JSONs used to also carry a `compatible_species` field
naming which species can live in this room type. That field is now
ignored — the species ↔ room-type relationship is stored entirely on
the species side as `compatible_room_types`. Both in-game editors
let you change the relationship though: ticking a species in the
room-type editor's Compatible-species checklist writes that room
type into the species' `compatible_room_types`, the same place the
species editor writes when you tick a room type there. Two views,
one source of truth. Existing JSONs that still have the legacy
`compatible_species` field keep it harmlessly; the in-game room-type
editor doesn't write it any more.)

What each field does:

- **`id` / `name` / `description`** — same idea as species.
- **`meters`** — every room type defines its own care meters. Each meter
  is a dict with:
  - `key` — the internal name; this is the key in `room["meters"]`.
  - `label` — what the user sees (and hears).
  - `verb_present` — the button verb ("Refill", "Clean", "Refresh").
  - `verb_past` — past-tense verb shown in status ("Refilled 5 minutes
    ago").
  - `low_word` — what the status says when the meter is partially full
    ("empty in 30 minutes", "needs cleaning in", "stale in").
  - `empty_word` — what the status says at 0% ("empty", "needs cleaning
    now").
  - `full_word` — what the status says at 100% ("full", "fresh", "clear").
  - `decay_seconds` (optional) — how long this meter takes to drop from
    full to empty, in seconds. If absent, falls back to the global
    `full_decay_seconds` setting. Lets you give different meters different
    rates (a fish tank's water can decay slower than a litter box, etc.).
    The in-game room-type editor accepts plain language here ("1 hour",
    "30 minutes", "1h 30m") and stores the parsed integer; you can also
    write the integer directly in the JSON.
- **`build_recipe`** — what items the player has to spend to build one of
  these rooms. Keys are item names from `items_common.txt` or
  `items_uncommon.txt`; values are how many of each. The game checks both
  tiers when looking up items.
- **`default_slots`** — how many creatures fit in a freshly-built room
  of this type. The room can hold more if you increase the slot count
  later (not yet implemented in V1).

A new room of this type, when built, automatically gets its meters
initialised to 100% (full / fresh / etc.) — so you don't need to set
starting values.

#### Per-instance allowed species

The species that "live in this room type" are computed from the
species side: any species whose `compatible_room_types` includes this
room type's id is in. That's the upper bound for what a built room of
this type can accept. A specific room **instance** can narrow that
further: a player can build a single Indoor room and restrict it to
"cats only" even though Indoor allows both cats and dogs. The
narrowing is set in the Build a new room dialog and can be changed
later via Edit room.

The narrowing is stored as `allowed_species` on the room dict in
`state.json` — always a subset of the species-derived compat list for
the room's type. Modders adding a species to a room type's compat
list do it by ticking the room type in the species editor's
"Compatible room types" section, not by touching the room-type JSON.
If you tick a new room type for an existing species, existing rooms
of that type keep their per-instance narrowing; the player can opt
the new species in via Edit room.

### Recipes — what to know

- Item names in recipes are matched against your inventory by *exact*
  string. Capitalization and spaces matter. *"Stick"* and *"stick"* are
  different items.
- You can mix common and uncommon items in a recipe. The game looks up
  each item across both tiers automatically.
- Objects and treasures aren't usable in recipes (yet). They're collected
  individually for flavour.
- If your recipe references an item that doesn't exist in either tier, the
  player will never be able to afford it. Add the item to the matching
  text file first.

### Older `default_cost` format

If you see `"default_cost": 10` in older / external mods, it means "any
10 common items" (greedy from the largest stack). The game still honours
this for backward compatibility, but new room types should prefer the
explicit `build_recipe`.

## Adding new items, objects, or treasures

Open the matching text file in `assets/text/`:

- **`items_common.txt`** — common dig finds. Plain names, one per line.
  These are also the items recipes can require.
- **`items_uncommon.txt`** — same format, slightly rarer. Recipes can
  require these too.
- **`objects.txt`** — dig-found objects. `name | description` per line.
  In V1 they're collectibles only; placement-in-rooms is a future feature.
- **`treasures.txt`** — same format. Rarer, more flavourful.

Add a line to any file, save, restart the game, and the new item will
start appearing in the dig pool.

The proportions of nothing/common/uncommon/object/treasure that come out
of digs are tunable in the game's **Settings** dialog (File → Settings…).
They normalise at runtime, so they don't have to add up to 1.0.

## Disabilities (representational)

Each species can have a `disability_chance` (0.0 to 1.0) and an
accompanying `disabilities.txt` pool. When a creature is born — through
a room birth or as a village starter — the game rolls that chance, and
on a hit picks one entry from the pool to attach as the creature's `disability`
field. The disability text shows alongside the creature's description
in the detail panels.

**This is a representational feature, not a debuff.** A creature with
a disability:

- Pairs the same way as anyone else
- Breeds at the same rate, with the same success chance
- Receives affection the same way
- Ages, becomes elder, and produces (when elder) the same way
- Is just as adoptable and just as loved

The only mechanical effect lives in **the wild** (see its own section
below): healthy creatures past max breeding age may auto-emigrate to
the wild, but disabled creatures stay in the village. Some folks need
a home that knows them. That's the entire systemic intent — a
deliberate "I see you and I think you're capable" stitched into the
design.

How to enable for a species:

1. Open File → Species → Edit → set **Disability chance** to
   something non-zero (e.g. `0.05`).
2. Add lines to the **Disabilities** pool — one per line, written in
   factual, neutral language: *"Born blind."*, *"Three legs."*,
   *"Doesn't see well in dim light."* Avoid pity language; these are
   descriptions, not problems to solve.
3. Save. New creatures of this species roll at birth and may carry one
   of the descriptions you wrote.

Default: every shipped species has `disability_chance: 0` and an empty
pool — the mechanic is dormant until you opt in per species. No
defaults are shipped in the pool because choosing the right wording
for a species is the modder's call, not the game's.

## Elders and what they produce

When a creature crosses its species' `elder_age_seconds` threshold,
**three** things happen at once (the elder and retire stages are
merged):

1. **Retired from breeding.** Pairs with them are skipped by both
   auto-pairing and breed attempts; the description box shows
   "Elder. Retired from breeding."
2. **Eligible for the wild.** Healthy retirees may auto-emigrate
   (see "The wild" below) unless they have a sanctuary disability.
3. **Production** — every `elder_production_seconds` of real time
   (default 3 hours, set in Settings), each elder picks one item at
   random from its **room-type's `build_recipe`** and adds it to
   your inventory. So elders in an Indoor room produce
   sticks/leaves/acorns (Indoor's recipe is
   `{"stick": 4, "leaf": 4, "acorn": 2}`); elders in an Aquatic
   room produce stones/pebbles/fabric scraps. Whatever you put in
   the recipe is what its elders make.

Tuning levers:

- **Speed it up or slow it down** in Settings → "How often each elder
  produces one item from their room's build recipe (seconds)".
- **Change what a room's elders produce** by editing the room type's
  recipe (Mods → Manage room types). The recipe doubles as both the
  build cost AND the elder production pool.
- **Mute the announcements** by blanking `elders_produced` and
  `elders_produced_offline` in `assets/text/announcements.txt`. The
  items still appear in inventory; you just won't hear about each
  one. (Useful if you have lots of elders and the chatter gets old.)

When you come back to a save after being away, any production you
missed is caught up at startup and announced via the
`elders_produced_offline` event.

## The wild

When a creature crosses its `elder_age_seconds` threshold (the merged
"they're old now" milestone), two things can happen — and which one
depends on whether they have a sanctuary disability (any disability
without the `| ok` flag):

- **Healthy retirees** may auto-emigrate to the wild. Every
  `wild_emigration_check_seconds` of real time (default 1 hour),
  each one rolls `wild_emigration_chance` (default 0.05 = 5%/check)
  to leave the save. Departures fire the `wild_emigration_one` /
  `wild_emigration_many` announcements. They don't come back — the
  wild is a soft permadeath, framed as them living their own life
  out there.
- **Sanctuary retirees** (creatures with a disability that doesn't
  have the `| ok` flag in their species' `disabilities.txt`) never
  emigrate. If they're still in a room when they retire, the
  emigration pass moves them into the village (announcing via
  `sanctuary_arrival_one` / `_many`). They stay there permanently —
  the village earns its "sanctuary of the world" framing literally.

Offline catch-up: if the game was closed for hours when the player
returns, the cumulative emigration probability over the away window
is rolled in one shot — so retirees keep moving on whether or not
the player is there.

Tuning levers (Settings):

- **Turn it off** by setting `wild_emigration_chance` to 0. Nothing
  emigrates, nothing moves to sanctuary, retirees just sit forever.
- **Speed it up** by raising `wild_emigration_chance` (toward 1.0)
  or lowering `wild_emigration_check_seconds` (toward 60).
- **Mute the announcements** by blanking the relevant lines in
  `assets/text/announcements.txt` (`wild_emigration_one`,
  `wild_emigration_many`, `wild_emigration_offline_one/many`,
  `sanctuary_arrival_one/many`).

## Renaming the village

The Village section is where un-adopted creatures live, where ones
you've sent away from rooms go, and where disabled retirees stay
rather than auto-emigrating to the wild. It's the sanctuary of the
world.

You can rename it. From the Village section, click **Rename this
place…** — Sanctuary, Home, Refuge, whatever fits the tone of your
save. The room picker updates immediately, the panel intro updates
immediately, and every announcement, dialog, and Help text that
references the village uses the new name.

The name is stored in `state.json` as `village_name`. Default is
`"Village"`. The internal dict key for the creatures themselves stays
`village` — that's a code identifier, not user-facing — so renaming
doesn't break any save data.

If you write your own announcement templates (see the next section),
the placeholder `{village_name}` interpolates the player's chosen
name. It's auto-passed to every event, so any template line you add
or rewrite can use it freely.

## Customising the announcement messages

Every line of text the game speaks (via NVDA) / shows in the status
bar / writes to the activity log lives in
`assets/text/announcements.txt`. The first time you launch Time for
Family, that file is written for you with all the default messages —
edit it freely.

Each entry is `event_id: template`, one per line. Templates use
`{placeholder}` for runtime values like creature names, room names,
counts. The header inside the file lists the available placeholders
for every event in a comment line just above the template, so you
don't have to memorise them.

Examples:

```
welcome_home: Welcome home.
time_paused: Time paused.
pair_formed_one: {cat_a_name} and {cat_b_name} have become a pair in {room_name}.
birth_kept_in_room: Welcomed {names} into {room_name}.
```

A few useful tricks:

- **Re-word freely.** Change *"Welcome home."* to *"Hi! Welcome back."* —
  whatever fits the voice of your mod.
- **Drop placeholders you don't want** — `pair_formed_one: {cat_a_name}
  and {cat_b_name} are a pair now.` works (drops `{room_name}`).
- **Mute an event** by leaving its template blank: `time_paused:` (the
  game just won't announce when time pauses). The status bar still
  updates because that's tied to the announce call, not the message
  text — but NVDA will say nothing for that event.
- **Typos in placeholder names** fall back to the shipped default for
  that event, so you can't crash the game mid-announcement by
  forgetting a brace.

If you delete the file, it'll be regenerated from defaults on the next
launch. If you delete just one entry, that event uses its shipped
default; other entries you customised stay customised.

Save the file and restart the game to pick up changes.

## Replacing sounds

The sounds in `assets/sounds/` are tone-synthesized .wav files generated
the first time the game starts. To swap one out, just put a `.wav` file
with the same filename in there. The current sound names are:

- `welcome.wav` — opening chime when the app launches
- `care.wav` — a "ding" when you refill / clean a meter
- `pet.wav` — when you pet (or feed) a creature
- `breed_success.wav` — a litter was born
- `breed_fail.wav` — breed attempt didn't work
- `arrival.wav` — a creature arrived in a room or village
- `expecting_summary.wav` — launched into a game with pairs still expecting
- `pair_formed.wav` — two creatures became a breeding pair
- `meter_low.wav` — a care meter dropped past the warning threshold

Use any short .wav file. They play asynchronously, so longer files won't
block the game; they just keep playing.

## File format gotchas

- **JSON files are strict.** A trailing comma, a missing quote, or a
  curly-quote pasted from a Word doc will make the game silently skip
  the file (or replace it with defaults). If your mod isn't loading,
  try [jsonlint.com](https://jsonlint.com) to check the syntax.
- **Text files are forgiving.** They're just lines of text; you can't
  really break them.
- **Filenames are case-sensitive on some operating systems.** Stick to
  lowercase for safety.
- **Don't put species data in room-type files** (or vice versa). The
  loader inspects the `id` and the filename / folder; mismatched IDs are
  ignored.

## Testing your mod

The fastest way to see if your mod works:

1. Save your file.
2. Restart Time for Family.
3. Open **File → About** to confirm the game launched without crashing.
4. Open the room picker and switch to the **Park** section — your new
   room type should show up in **Build a new room** with its recipe and
   status.
5. If it doesn't show up, check `crash.log` in the project folder for
   any error from the loader.

## Sharing mods

Zip up your edited files preserving the folder structure, with paths
rooted at `assets/`. When a player unzips into their own `assets/`
folder, the next launch propagates your mod into their `user_data/`.
This way your mod becomes their factory default — they can still edit
on top of it via the in-game tools without losing the unmodified
version. The state file (`state.json`) is per-user and doesn't need
to ship with the mod.

If your mod adds a species, your zip should include:

- `assets/types/species/<your-species>.json`
- `assets/text/species/<your-species>/names_female.txt`
- `assets/text/species/<your-species>/names_male.txt`
- `assets/text/species/<your-species>/descriptions.txt`
- `assets/text/species/<your-species>/pet_responses.txt`
- `assets/text/species/<your-species>/disabilities.txt`
- `assets/text/species/<your-species>/colors.txt`

If it adds a room type, include:

- `assets/types/room_types/<your-room-type>.json`

If it tweaks an existing pool (names, items, descriptions), share just
that file.

> If a player has already played and their `user_data/` is populated,
> they have to copy your new files from `assets/` into `user_data/`
> manually for the change to take effect — `user_data/` is only
> auto-populated from `assets/` on first launch and to fill in
> missing subtrees, not to overlay updates onto an existing live tree.
> The simplest install instruction: "delete `user_data/` and relaunch"
> (after backing up any of their own customisations).

Have fun.
