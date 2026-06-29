"""Headless test of the play-by-typing layer (tff_play.py).

Drives a full loop the way an AI (or a hearthkin tool) would — look, adopt,
dig, build, move, care, breed — against a throwaway save, and checks each
verb returns sensible plain-English. This is the Phase-3 regression guard;
it complements headless_smoke.py (which tests the engine mechanics) by
exercising the AI-facing narration + command layer.

Run from project root:   python dev/play_smoke.py
Never touches the real state.json (uses a temp save). Exit 0 = clean.
"""

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
_failures = []


def check(cond, label, sample=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f"  (got: {sample!r})" if sample else ""))
        _failures.append(label)


def main():
    sys.path.insert(0, str(ROOT))
    import tff_play
    import tff_engine

    save = pathlib.Path(tempfile.gettempdir()) / "tff_play_smoke_state.json"
    if save.exists():
        save.unlink()
    save = str(save)

    print("Playing a full loop via the text-adventure command parser:\n")

    def do(cmd):
        return tff_play.command(save, cmd)

    r = do("look")
    check("empty" in r.lower() or "waiting" in r.lower(),
          "'look' (fresh park) invites you to adopt", r[:80])

    r = do("adopt cat")
    check("village" in r.lower() and "welcom" in r.lower(),
          "'adopt cat' -> pair welcomed to village", r[:80])

    r = do("adopt a dragon")
    check("no species" in r.lower(),
          "'adopt a dragon' -> friendly refusal", r[:80])

    r = do("dig 5 times")
    check(r.lower().startswith("dug"), "'dig 5 times' -> reports the haul", r[:80])

    # Grant materials directly so build is deterministic (the AI would dig
    # for these; we don't want the test to depend on dig randomness).
    tff_engine.STATE_FILE = pathlib.Path(save)
    st = tff_engine.load_state()
    inv = st.setdefault(
        "inventory",
        {"common": {}, "uncommon": {}, "objects": {}, "treasures": {}})
    for nm in tff_engine.ITEMS_COMMON:
        inv["common"][nm] = 999
    for nm in tff_engine.ITEMS_UNCOMMON:
        inv["uncommon"][nm] = 999
    tff_engine.save_state(st)

    r = do("build an indoor room called Cozy Room")
    check("built" in r.lower() and "cozy room" in r.lower(),
          "'build an indoor room called Cozy Room' -> built", r[:80])

    r = do("build treehouse")
    check("no room type" in r.lower(),
          "'build treehouse' -> lists available types", r[:80])

    # Grab a villager name to move in.
    st = tff_engine.load_state()
    villager = st["village"][0]["name"]

    r = do(f"move {villager} to Cozy Room")
    check("moved" in r.lower() and "cozy room" in r.lower(),
          f"'move {villager} to Cozy Room'", r[:80])

    r = do("move Nobody to Cozy Room")
    check("couldn't find" in r.lower(),
          "'move Nobody to Cozy Room' -> not found", r[:80])

    r = do("care for Cozy Room")
    check("cozy room" in r.lower()
          and ("refill" in r.lower() or "affection" in r.lower()),
          "'care for Cozy Room' -> meters + affection", r[:80])

    r = do("breed Cozy Room")
    check(isinstance(r, str) and len(r) > 0,
          "'breed Cozy Room' -> a sensible status line", r[:80])

    r = do("look at Cozy Room")
    check("cozy room" in r.lower() and "slot" in r.lower(),
          "'look at Cozy Room' -> describes it", r[:80])

    r = do(f"look at {villager}")
    check(villager.lower() in r.lower() and "lives in" in r.lower(),
          f"'look at {villager}' -> describes the creature", r[:80])

    r = do("help")
    check("adopt" in r.lower() and "breed" in r.lower(),
          "'help' -> lists the commands", r[:80])

    r = do("flibbertigibbet the whatsit")
    check("understand" in r.lower(),
          "nonsense command -> friendly 'didn't understand'", r[:80])

    # --- the added gameplay verbs ---
    r = do(f"rename {villager} to Pebble")
    check("renamed" in r.lower() and "pebble" in r.lower(),
          "'rename <creature> to Pebble' -> renamed", r[:80])

    r = do("care for Pebble")
    check("pebble" in r.lower() and "affection" in r.lower(),
          "'care for Pebble' -> pets one creature", r[:80])

    r = do("expand Cozy Room")
    check("slot" in r.lower(), "'expand Cozy Room' -> added a slot", r[:80])

    r = do("convert Cozy Room to treehouse")
    check("no room type" in r.lower(),
          "'convert ... to treehouse' -> unknown type refused", r[:80])

    r = do("autobreed on")
    check("is on" in r.lower(), "'autobreed on' -> ON", r[:80])

    r = do("auto-breed off")
    check("is off" in r.lower(), "'auto-breed off' -> OFF", r[:80])

    r = do("reload")
    check("reloaded" in r.lower() and "species" in r.lower(),
          "'reload' -> re-reads definitions from disk", r[:80])

    r = do("reset")
    check("reset confirm" in r.lower(),
          "'reset' (no confirm) -> asks to confirm first", r[:80])

    r = do("look")
    check("park" in r.lower() and "village" in r.lower(),
          "'look' (whole park) -> full overview", r[:80])

    print("\n" + "=" * 48)
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        return 1
    print("ALL CHECKS PASSED -- the AI can play by typing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
