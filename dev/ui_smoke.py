"""UI construction smoke test: builds the real windows headlessly.

Creates a wx.App (no MainLoop), constructs MainFrame against a TEMP COPY of
the real save (so all the real rooms/panels get built and apply_elapsed_time
runs), refreshes, then tears down. This catches the class of bug a pure
import check misses -- e.g. a name that didn't survive a file split and only
blows up when a panel actually builds.

Run from project root:   python dev/ui_smoke.py

Never touches the real state.json (save is redirected to a temp file).
Exit 0 = the windows build clean.
"""

import importlib.util
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_ui():
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        "tff_ui_under_test", ROOT / "time_for_family.pyw")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tff_ui_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    print("Importing the windowed app...")
    ui = load_ui()

    import tff_engine
    # Redirect save to a temp copy of the real one so we build real rooms.
    tmp = pathlib.Path(tempfile.gettempdir()) / "tff_ui_smoke_state.json"
    real = ROOT / "state.json"
    if real.exists():
        shutil.copy2(real, tmp)
        print(f"Using a temp copy of the real save: {tmp}")
    elif tmp.exists():
        tmp.unlink()
    tff_engine.STATE_FILE = tmp

    ui.ensure_user_data_dir()
    ui.load_types()
    ui.load_text_assets()

    app = ui.wx.App(False)
    frame = ui.MainFrame()
    n_pages = frame.book.GetPageCount()
    print(f"  MainFrame built; {n_pages} book page(s).")
    # Exercise a full refresh (touches every panel's refresh()).
    frame.save_and_refresh()
    print("  save_and_refresh() ran across all panels.")

    # Build every dialog headlessly -- especially the modding editors, which
    # are easy to never open in normal play. This runs each dialog's __init__
    # (and its sub-widgets), catching both file-split breakage and
    # construction-time bugs without anyone clicking.
    failures = []

    def build(label, factory):
        try:
            dlg = factory()
            dlg.Destroy()
            print(f"  PASS  built {label}")
        except Exception as e:
            print(f"  FAIL  {label}: {type(e).__name__}: {e}")
            failures.append(label)

    print("\nBuilding dialogs:")
    build("HelpDialog", lambda: ui.HelpDialog(frame))
    build("SettingsDialog", lambda: ui.SettingsDialog(frame))
    build("ManageAnnouncementsDialog", lambda: ui.ManageAnnouncementsDialog(frame))

    species_id = next(iter(ui.SPECIES_DATA), None)
    sd = ui.SpeciesDialog(frame)
    print("  PASS  built SpeciesDialog")
    build("SpeciesEditorDialog (new)", lambda: ui.SpeciesEditorDialog(sd))
    if species_id:
        build(f"SpeciesEditorDialog (edit {species_id})",
              lambda: ui.SpeciesEditorDialog(sd, species_id=species_id))
    sd.Destroy()

    room_type = next(iter(ui.ROOM_TYPES), None)
    mrt = ui.ManageRoomTypesDialog(frame)
    print("  PASS  built ManageRoomTypesDialog")
    build("RoomTypeEditorDialog (new)", lambda: ui.RoomTypeEditorDialog(mrt))
    if room_type:
        build(f"RoomTypeEditorDialog (edit {room_type})",
              lambda: ui.RoomTypeEditorDialog(mrt, type_id=room_type))
    mrt.Destroy()

    frame.Destroy()
    app.Destroy()

    if failures:
        print(f"\nUI smoke FAILED -- {len(failures)} dialog(s) broke: {failures}")
        return 1
    print("\nUI smoke PASSED -- windows + every dialog build clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
