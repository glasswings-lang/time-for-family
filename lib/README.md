# Bundled libraries

This directory holds third-party libraries Time for Family loads at runtime.
They are kept here so the app stays self-contained and the libraries remain
user-replaceable.

## NVDA Controller Client

Files:

- `nvdaControllerClient64.dll`
- `nvdaControllerClient32.dll`
- `nvdaControllerClient-license.txt`

Used to push announcement text directly to the NVDA screen reader. Without
the DLL, the app falls back to the visible status bar and activity log.

- **Source / project:** https://github.com/nvaccess/nvda
- **Specific subproject:** `extras/controllerClient/`
- **Copyright:** NV Access Limited and contributors
- **License:** GNU Lesser General Public License, version 2.1
  (see `nvdaControllerClient-license.txt`)

The DLLs bundled here are unmodified copies. They can be replaced with a
different version (e.g. a newer build) by overwriting the files — Time for
Family loads them dynamically each launch.
