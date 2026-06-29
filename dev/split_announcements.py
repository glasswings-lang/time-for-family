"""One-off: lift the announcement subsystem out of tff_engine.py into its own
tff_announcements.py, so a wording/announcement bug is contained to one small
file. Run from project root:

    python dev/split_announcements.py

Reads the CURRENT tff_engine.py; run once. Boundaries found by content
markers. The new file has NO imports and NO engine dependency -- the only
outside thing the loader needed was the text directory, which is now a
parameter.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / "tff_engine.py"
ANN = ROOT / "tff_announcements.py"

ANN_HEADER = '''\
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
'''

ENGINE_GLUE = '''\
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


'''


def main():
    lines = ENGINE.read_text(encoding="utf-8").splitlines(keepends=True)

    start = next(i for i, l in enumerate(lines)
                 if l.startswith("# ===== Configurable announcement templates"))
    end = next(i for i, l in enumerate(lines)
               if l.startswith("_TEXT_FILE_HEADERS"))

    block = lines[start:end]
    while block and block[-1].strip() == "":
        block.pop()
    block_text = "".join(block)

    # The only engine dependency in the moved code is TEXT_DIR, used by
    # load_announcements. Make it a parameter so the new file imports nothing.
    if "def load_announcements():" not in block_text:
        raise SystemExit("load_announcements signature not found as expected")
    block_text = block_text.replace(
        "def load_announcements():",
        "def load_announcements(text_dir):",
    )
    # Inside load_announcements only -- it's the last function in the block,
    # so replacing TEXT_DIR after its def is safe.
    head, _, tail = block_text.partition("def load_announcements(text_dir):")
    tail = tail.replace("TEXT_DIR", "text_dir")
    block_text = head + "def load_announcements(text_dir):" + tail

    ANN.write_text(ANN_HEADER + "\n" + block_text + "\n", encoding="utf-8")

    new_engine = "".join(lines[:start]) + ENGINE_GLUE + "".join(lines[end:])
    # Update the one caller to pass the directory.
    if "    load_announcements()\n" not in new_engine:
        raise SystemExit("load_announcements() call site not found")
    new_engine = new_engine.replace(
        "    load_announcements()\n",
        "    load_announcements(TEXT_DIR)\n",
    )
    ENGINE.write_text(new_engine, encoding="utf-8")

    print("Announcements extracted.")
    print(f"  block moved          lines {start + 1}..{end}")
    print(f"  tff_announcements.py {ANN.read_text(encoding='utf-8').count(chr(10))} lines")
    print(f"  tff_engine.py        {new_engine.count(chr(10))} lines")


if __name__ == "__main__":
    main()
