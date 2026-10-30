# Windows input gauntlet (local, outside CI)

From this worktree, in a non-elevated PowerShell 7 prompt:

```powershell
just test-windows-input -AllowInputInjection
```

This initializes the pinned Ghostty submodule, builds this worktree's app,
then runs 75 cases across five separate observer runs: native console records,
legacy VT bytes, and Kitty flags 1, 5, and 9 with ConPTY Win32 mode enabled.
Allow roughly 1–2 minutes after building; a cold Rust build can take 15–30 minutes.
Requires Python 3, PowerShell 7, just, the app's normal Rust/Zig 0.16/LLVM/MSVC
build tools, and installed `hc.exe` with its adjacent pinned ConPTY bundle.
The build takes the shared Rust lock for at most 30 minutes, then has a
five-minute submodule setup deadline and 30-minute build deadline. The desktop
portion has a four-minute deadline plus bounded cleanup (up to 50 seconds).

Defaults select `%LOCALAPPDATA%/Programs/herdr-core/hc.exe` and the pin/source
checkout `C:/Code/herdr-core` (read-only). Override explicitly if needed:

```powershell
just test-windows-input -AppPath C:/build/herdr-app.exe -CorePath C:/build/hc.exe -CoreSource C:/src/herdr-core -AllowInputInjection
```

An explicit app path skips the build. The core executable is copied into each
new run directory together with `conpty/conpty.dll` and
`conpty/x64/OpenConsole.exe`. Both bundle hashes must match that core source's
`scripts/fetch-conpty.ps1` pin. Missing/mismatched files stop the run.
Source heads identify the selected checkouts; they do not prove the binaries
were built from those heads. SHA256 and version evidence identify tested binaries.

Leave the desktop unlocked and alone until the command finishes. It opens a
dedicated scratch app window and focuses it once per observer run. Before every
balanced scan-code gesture it checks the exact foreground HWND and owner PID.
Losing focus stops the entire run without refocusing or further typing. F12
aborts. Physical modifiers/mouse buttons must be released. There remains an
unavoidable Windows race between checking focus and calling SendInput: do not
use the desktop concurrently. The harness never accesses the clipboard.

The app home and all XDG directories are fresh; inherited `HC_*` and `HERDR_*`
variables are removed. Only this harness's core session, terminals and app
process are stopped on exit. A probe lease expires after 90 seconds if its
controller disappears. Existing apps, installed files, and user core sessions
are not changed. Scratch homes bypass onboarding and disable telemetry and OSC52
clipboard writes. Default keyboard bindings stay enabled.

Each run writes `.local/windows-input/<timestamp-id>/report.json`, `summary.txt`,
the matrix, raw observer captures and viewer output. Reports include binary
hashes, source heads, bundle identities, negotiated modes, observer input console
mode (third geometry element), actual pane cell geometry and pass/fail per case.
Unexpected extra input fails, including VK_CANCEL, stray Alt, duplicates and
trailing text. An aborted/incomplete run fails overall. Nonzero exit means failure.

Safe development checks (no GUI, focus change, SendInput or clipboard access):

```powershell
just test-windows-input -MatrixOnly
just test-windows-input -ProbeOnly
python -m unittest discover -s scripts/windows_input -p test_report.py
```

ProbeOnly verifies all five observer handshakes and mode restoration against an
isolated core. Native byte controls compare coalesced bare Escape/Ctrl+C (known
malformed), delayed bytes, and Win32 Escape plus Ctrl+C. Kitty controls check
distinct Escape/Ctrl+C bytes through ConPTY. They do not prove app keyboard dispatch.
No automated desktop case is claimed passed by ProbeOnly.

Manual follow-up: dead keys and composed characters; AltGr on the actual keyboard
layout; IME start/commit/cancel; held-key repeat and releases. Shift+Escape covers
modified Escape automatically; Ctrl+Escape and Alt+Escape are OS shortcuts and are
excluded to avoid opening Start or switching windows. Remote/SSH, clipboard,
images, Windows Terminal hosts and geometry sweeps are outside this slice.

ConsoleProbe and its request/lease helpers are adapted from the Apache-2.0 legacy
Herdr harness. This port uses only existing app/core commands and protocols.
