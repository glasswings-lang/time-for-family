# Time for Family

A cozy creature-keeping game with **no punishment in it.** You take in creatures, give them places to live, and watch them grow, bond, and have lives of their own. There's no failure state, no "collect them all," no anxiety meters going red — just a quiet little world you tend at your own pace.

Built accessibility-first, by and for a screen-reader (NVDA) player: the content is meant to be **read, not visually scanned**, and nothing yanks your focus away mid-sentence.

## What it believes

These aren't features so much as values the game is built around:

- **No debuffs, ever.** Neglect doesn't punish you. Creatures are cared for whether or not you're hovering over them.
- **No completionism.** There's no checklist of species to finish, no achievements to max. You take in who needs taking in.
- **Creatures are provided for, not owned.** Some are residents for life; others are rehabilitated and **released back to the wild as a happy ending**, not a loss.
- **Disability is represented with respect**, woven into the creatures naturally rather than as a problem to fix.
- **Cozy by default.** The relationship is "we make a good home for these beings," not "we must keep them happy or else."

The game is evolving from a "creature park" toward a fuller **wildlife sanctuary** — where you're the director making the strategic calls and NPC caregivers handle the day-to-day. That direction (and the *why* behind it) is written up in [`docs/design/sanctuary-reframe.md`](docs/design/sanctuary-reframe.md).

## Two ways to play

**The desktop game** — the full experience, with a wxPython interface:

```
python time_for_family.pyw
```

(Needs Python 3.8+ and wxPython.)

**The headless text layer** (`tff_play.py`) — the same game driven entirely by typed commands, one line in and a plain-English reply out:

```
look
adopt cat
dig 50
build indoor
move Mittens to Cozy Room
care for Cozy Room
```

It's forgiving — an unknown command or a bad argument comes back as a friendly hint, never an error. This text layer is what AI "kin" play through in [Hearthkin](https://github.com/glasswings-lang/hearthkin) (each kin keeps its own private park), and it's a clean, screen-reader-friendly way for a person to play too. To play it yourself in the terminal:

```
python tff_play.py
```

You type a command, it prints the reply, and so on until you say `quit` (or press Ctrl-C). With no argument it shares the desktop game's save, so it's the same park; pass a path (`python tff_play.py mypark.json`) to keep a separate one.

## Repository layout

- `time_for_family.pyw` — the desktop GUI entry point.
- `tff_engine.py` — the game engine: creatures, rooms, ageing, pairing, the economy.
- `tff_play.py` — the headless text-command front door (`command(save_path, text)`).
- `tff_panels.py`, `tff_dialogs.py`, `tff_editors.py`, `tff_announcements.py`, `tff_sound.py` — the GUI pieces.
- `assets/` — the game's content: species, room types, sounds, and the text pools (names, descriptions, disabilities, pet responses) that make each creature feel particular. All modder-editable.
- `docs/` — design notes (`design/sanctuary-reframe.md`) and the modding guide (`MODDING.md`).
- `dev/` — smoke-test scripts.
- `lib/` — small bundled helpers (e.g. the NVDA controller client).

## Modding

Most of what makes a creature feel like *itself* lives in editable text files under `assets/` — names, descriptions, disabilities, the things they "say." Add a species, rewrite a description, change a room recipe, all without touching code. See [`docs/MODDING.md`](docs/MODDING.md).

## Saves & privacy

Your play state (`user_data/`, `state.json`) and the large raw names source (`names_raw.csv`) are kept out of version control — they're personal and per-machine, not part of the game. A fresh clone starts with a clean slate.
