"""
Time for Family -- sound generation + NVDA speech (the presentation's voice).

Synthesises the chime .wav files on first run, plays them, and pushes
announcements to the NVDA screen reader via its controller DLL. Imported by
the UI modules. The mute flag lives here with its only reader (play_sound);
the UI toggles it through set_muted()/is_muted().
"""

import ctypes
import math
import struct
import wave
from pathlib import Path

import wx
import wx.adv

from tff_engine import PROJECT_DIR, SOUNDS_DIR


# ===== Sound generation (stdlib only) =====

# Each entry: list of (frequency_hz, time_offset_seconds) — additive sine notes.
SOUND_RECIPES = {
    "welcome":       [(523, 0.00), (659, 0.10), (784, 0.20)],
    "care":          [(880, 0.00)],
    "pet":           [(659, 0.00)],
    "breed_success": [(523, 0.00), (659, 0.10), (784, 0.20), (1047, 0.30)],
    "breed_fail":    [(440, 0.00), (392, 0.10)],
    "arrival":          [(784, 0.00), (988, 0.15)],
    "expecting_summary": [(659, 0.00), (784, 0.10), (988, 0.20)],
    "pair_formed":   [(523, 0.00), (659, 0.08), (784, 0.16), (1047, 0.24)],
    "meter_low":     [(196, 0.00)],
}


def synthesize(notes, total_duration=0.5, sample_rate=22050, volume=0.25):
    n = int(total_duration * sample_rate)
    out = []
    for i in range(n):
        t = i / sample_rate
        s = 0.0
        for freq, offset in notes:
            note_t = t - offset
            if note_t < 0:
                continue
            note_dur = total_duration - offset
            if note_t >= note_dur:
                continue
            # per-note envelope: quick attack, slower decay, prevents clicks
            env = min(1.0, note_t * 30.0, (note_dur - note_t) * 8.0)
            s += volume * env * math.sin(2.0 * math.pi * freq * note_t)
        s = max(-1.0, min(1.0, s))
        out.append(int(s * 32767))
    return out, sample_rate


def write_wav(path, samples, sample_rate):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def ensure_sounds():
    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    for name, notes in SOUND_RECIPES.items():
        path = SOUNDS_DIR / f"{name}.wav"
        if not path.exists():
            samples, rate = synthesize(notes)
            write_wav(path, samples, rate)


# wx.adv.Sound async play needs the Sound object alive while playing;
# stash recent ones so they don't get garbage-collected mid-play.
_live_sounds = []

# Runtime toggles. Sound + Pause default off and reset each launch (so
# a paused / muted app doesn't surprise you on relaunch). Auto-breeding
# defaults ON and persists in state — players expect a cozy life sim to
# do its life-sim thing while they're away. Off-by-default meant new
# players never saw births until they hunted down a Tools menu toggle,
# which broke the whole "leave it running and check in later" promise.
SOUND_MUTED = False
def play_sound(name):
    if SOUND_MUTED:
        return
    path = SOUNDS_DIR / f"{name}.wav"
    if not path.exists():
        return
    sound = wx.adv.Sound(str(path))
    if not sound.IsOk():
        return
    sound.Play(wx.adv.SOUND_ASYNC)
    _live_sounds.append(sound)
    if len(_live_sounds) > 8:
        _live_sounds.pop(0)


def set_muted(on):
    """Set the mute flag. The UI's mute toggle calls this rather than
    reassigning SOUND_MUTED directly, so the flag and its only reader
    (play_sound) stay in one module once sound is split out."""
    global SOUND_MUTED
    SOUND_MUTED = bool(on)


def is_muted():
    return SOUND_MUTED


# ===== NVDA controller client integration =====
# NVDA ships nvdaControllerClient(32|64).dll. Loading it via ctypes lets us
# push announcements directly to the screen reader, which is much more
# reliable than relying on status-bar or text-control change events.

_nvda_lib = None
_nvda_attempted = False


def _try_load_nvda():
    is_64 = struct.calcsize("P") == 8
    primary = "nvdaControllerClient64.dll" if is_64 else "nvdaControllerClient32.dll"
    candidates = [
        str(PROJECT_DIR / "lib" / primary),
        str(PROJECT_DIR / primary),
        primary,
        str(Path(r"C:\Program Files (x86)\NVDA") / primary),
        str(Path(r"C:\Program Files\NVDA") / primary),
    ]
    for path in candidates:
        try:
            lib = ctypes.windll.LoadLibrary(path)
            lib.nvdaController_speakText.argtypes = [ctypes.c_wchar_p]
            lib.nvdaController_speakText.restype = ctypes.c_int
            lib.nvdaController_testIfRunning.restype = ctypes.c_int
            return lib
        except (OSError, AttributeError):
            continue
    return None


def nvda_available():
    global _nvda_lib, _nvda_attempted
    if not _nvda_attempted:
        _nvda_lib = _try_load_nvda()
        _nvda_attempted = True
    if _nvda_lib is None:
        return False
    try:
        return _nvda_lib.nvdaController_testIfRunning() == 0
    except OSError:
        return False


def nvda_speak(text):
    if not nvda_available():
        return False
    try:
        _nvda_lib.nvdaController_speakText(text)
        return True
    except OSError:
        return False


# ===== UI =====
