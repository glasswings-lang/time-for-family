"""Headless smoke test for Time for Family's game logic (the "brain").

Runs the game's rules with NO windows: loads content, starts a fresh park,
adopts creatures, saves/reloads, lets time pass, and breeds a pair — then
checks everything came out sane. It deliberately exercises the parts most
sensitive to a careless file-split: the shared content shelves (loaded
species, name pools) that both the brain and the screen read from.

Run from the project root:   python dev/headless_smoke.py

It NEVER touches your real save — save/load are redirected to a temp file.
Exit code 0 = all good; non-zero = something regressed.

This is the safety check to re-run after every modularization step.
"""

import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Species-neutral placeholders the name generator falls back to ONLY when a
# species' name pool is empty. Seeing one of these for a shipped species means
# the name pools went stale — the classic "cat names leaked onto X" bug.
PLACEHOLDERS = {
    "Newcomer", "Visitor", "Wanderer", "Stranger", "Friend",
    "Guest", "Drifter", "Pilgrim", "Traveler", "Sojourner",
    "Roamer", "Rambler", "Foundling", "Arrival", "Newbie",
}

SHIPPED_SPECIES = ["cat", "dog", "rabbit", "chicken", "fish"]

_failures = []


def check(cond, label):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        _failures.append(label)


def load_game():
    """Import the engine as a normal module. This must succeed with NO
    wxPython involved -- that's the whole point of the engine split."""
    sys.path.insert(0, str(ROOT))
    import tff_engine
    if "wx" in sys.modules:
        print("  WARN  importing the engine pulled in wx (it shouldn't)")
    else:
        print("  PASS  engine imported with no wx loaded")
    return tff_engine


def main():
    print("Loading engine module: tff_engine.py")
    tff = load_game()

    # Mirror main()'s headless setup (no sounds, no wx.App).
    tff.ensure_user_data_dir()
    tff.load_types()
    tff.load_text_assets()

    # Redirect save/load to a throwaway file so the real state.json is safe.
    tmp = pathlib.Path(tempfile.gettempdir()) / "tff_headless_smoke_state.json"
    if tmp.exists():
        tmp.unlink()
    tff.STATE_FILE = tmp
    print(f"Save redirected to temp: {tmp}\n")

    # --- Content loaded ---
    print("Content loading:")
    for sp in SHIPPED_SPECIES:
        check(sp in tff.SPECIES_DATA, f"species '{sp}' is loaded")
    # Announcements now live in tff_announcements.py; the engine re-exports
    # them. Confirm the shared ANNOUNCEMENTS shelf filled and the formatter
    # resolves through it.
    check(bool(tff.ANNOUNCEMENTS), "announcements shelf is populated")
    check(tff.format_announcement("welcome_home") == "Welcome home.",
          "format_announcement resolves a template")

    # --- Name generator canary (the global-staleness target) ---
    print("\nName generators (cat-leak canary):")
    for sp in SHIPPED_SPECIES:
        for sex in ("F", "M"):
            name = tff.random_creature_name(sp, sex)
            ok = bool(name) and name not in PLACEHOLDERS
            check(ok, f"{sp}/{sex} -> real name ({name!r})")
        desc = tff.random_description(sp)
        check(isinstance(desc, str), f"{sp} description is a string")

    # --- Fresh park ---
    print("\nFresh park:")
    state = tff.new_state()
    check(tff.state_is_fresh(state), "new_state() reports fresh")
    check(not state["rooms"], "new park has no rooms")
    check(not state["village"], "new park has no villagers")

    # --- Adopt ---
    print("\nAdopt a cat pair (seed_village_pair):")
    tff.seed_village_pair(state, "cat")
    villagers = list(state["village"])
    check(len(villagers) >= 2, f"village grew to {len(villagers)}")
    check(all(c.get("species") == "cat" for c in villagers),
          "all adopted are cats")
    check(all(c.get("name") and c["name"] not in PLACEHOLDERS for c in villagers),
          "adopted cats have real names")

    # --- Save / reload round trip ---
    print("\nSave + reload round trip:")
    tff.save_state(state)
    check(tmp.exists(), "state file written")
    reloaded = tff.load_state()
    check(len(reloaded["village"]) == len(villagers),
          "village count survives reload")

    # --- Time passing ---
    print("\nLet time pass (apply_elapsed_time):")
    before_ages = [tff.cat_age_seconds(c) for c in state["village"]]
    state["last_tick"] = time.time() - 3 * 3600  # pretend 3 hours elapsed
    tff.apply_elapsed_time(state)
    after_ages = [tff.cat_age_seconds(c) for c in state["village"]
                  if c in state["village"]]
    check(any(a > b for a, b in zip(after_ages, before_ages))
          or not after_ages,  # (empty only if everyone emigrated, unlikely)
          "creatures aged after elapsed time")

    # --- Breeding integration canary ---
    print("\nBreed the village pair (auto_breed_village):")
    # Force a mature, eligible, willing pair: age them between breeding age and
    # elder age, drop the care/chance gates so a breed is near-certain.
    for c in state["village"]:
        c["age_seconds"] = 30000.0  # ~8.3h: mature, not elder for cats
        c["mature_at"] = time.time() - 1
        c["affection"] = 1.0
    tff.SETTINGS["breed_success_chance"] = 1.0
    tff.SETTINGS["breed_min_care"] = 0.0
    start_count = len(state["village"])
    bred = False
    for _ in range(50):
        tff.auto_breed_village(state)
        tff.process_expecting(state)  # in case the species gestates
        if len(state["village"]) > start_count:
            bred = True
            break
    check(bred, "a litter was produced")
    if bred:
        babies = state["village"][start_count:]
        check(all(b.get("species") == "cat" for b in babies),
              "babies are cats (no species leak)")
        check(all(b.get("name") and b["name"] not in PLACEHOLDERS
                  for b in babies),
              "babies have real cat names (no name leak)")

    # --- Room build + meter decay ---
    # (Regression guard: apply_elapsed_time decays room meters via
    # meter_decay_seconds_for. A park with no rooms never exercises that
    # path -- which is exactly how a stranded helper slipped through once.)
    print("\nBuild a room and age its meters (meter_decay_seconds_for):")
    inv = state.setdefault(
        "inventory",
        {"common": {}, "uncommon": {}, "objects": {}, "treasures": {}},
    )
    for nm in tff.ITEMS_COMMON:
        inv["common"][nm] = 999
    for nm in tff.ITEMS_UNCOMMON:
        inv["uncommon"][nm] = 999
    before_rooms = len(state["rooms"])
    tff.build_new_room(state, "indoor", "Test Indoor", add_starters=False)
    check(len(state["rooms"]) > before_rooms, "indoor room built from inventory")
    if len(state["rooms"]) > before_rooms:
        room = state["rooms"][-1]
        for k in room["meters"]:
            room["meters"][k] = 1.0
        check(bool(room["meters"]), "room has care meters")
        state["last_tick"] = time.time() - 3 * 3600
        tff.apply_elapsed_time(state)
        check(any(v < 1.0 for v in room["meters"].values()),
              "room meters decayed after time passed")

    # --- Player action cores (the de-tangled handles Phase 3 needs) ---
    print("\nPlayer action cores:")
    v = state["village"][0]
    old = tff.rename_creature(state, v["id"], "Testname")
    check(old is not None and v["name"] == "Testname",
          "rename_creature renamed a villager")
    n0 = len(state["village"])
    nv = tff.add_villager(state, "cat", "F")
    check(nv is not None and len(state["village"]) == n0 + 1,
          "add_villager added a cat to the village")
    tff.set_auto_breeding(state, True)
    check(state.get("auto_breeding") is True,
          "set_auto_breeding wrote the save flag")
    tff.apply_settings(state, {"breed_min_care": 0.25})
    check(tff.SETTINGS.get("breed_min_care") == 0.25
          and state.get("settings", {}).get("breed_min_care") == 0.25,
          "apply_settings updated SETTINGS and the save")
    if state["rooms"]:
        rid = state["rooms"][-1]["id"]
        plan = tff.plan_room_retype(state, rid, new_name="Renamed Room")
        check(plan.get("status") == "ok" and plan.get("name_changed"),
              "plan_room_retype detects a rename")
        tff.apply_room_retype(state, rid, plan)
        check(state["rooms"][-1]["name"] == "Renamed Room",
              "apply_room_retype applied the rename")

    # --- Room-type delete guard ---
    print("\nRoom-type delete guard (room_type_delete_impact):")
    saved_species = dict(tff.SPECIES_DATA)
    try:
        tff.SPECIES_DATA["only_here"] = {
            "spec": {"name": "Onlyhere",
                     "compatible_room_types": ["testroom"]}}
        tff.SPECIES_DATA["also_else"] = {
            "spec": {"name": "Alsoelse",
                     "compatible_room_types": ["testroom", "indoor"]}}
        strand, also = tff.room_type_delete_impact("testroom")
        check("Onlyhere" in strand,
              "single-home species flagged as would-strand (delete refused)")
        check("Alsoelse" not in strand and "also_else" in also,
              "multi-home species is safe + marked for tidy-up, not stranded")
        strand2, also2 = tff.room_type_delete_impact("unreferenced_type")
        check(not strand2 and not also2,
              "deleting an unreferenced room type strands no one")
    finally:
        tff.SPECIES_DATA.clear()
        tff.SPECIES_DATA.update(saved_species)

    # --- Summary ---
    print("\n" + "=" * 48)
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) regressed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED -- the brain runs clean with no windows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
