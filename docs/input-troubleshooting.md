# When text fields accept input but an instrument does not

Typing in the test pad, a browser address bar, or a document establishes that Windows is receiving LyreBot's input. It does not establish that an application's instrument consumes that input. A successful Windows SendInput return value is not an acknowledgement from the target application.

LyreBot already uses physical QWERTY scan codes through SendInput. PyDirectInput uses the same general mechanism, so replacing the sender with that library is not an established fix.

## A short comparison test

1. Open the intended instrument and confirm that pressing the physical A, S, and D keys plays its notes. Keep chat boxes and other overlays closed.
2. In LyreBot, open `examples/input_check.txt`. It uses 60 BPM and plays A, S, D two seconds apart. Select the actual instrument application window, not its launcher.
3. Keep Key tap at 35 ms and run Play to window. Switch to the instrument during the countdown. Record LyreBot's final status or exact error, as well as whether any notes sounded.
4. Run the same file with Key tap at 150 ms. Tempo remains 60 BPM; only the press length changes. If that succeeds, short-press recognition is a plausible cause.

## Permission comparison for an elevated target

Windows restricts SendInput across process integrity levels. If the target runs with administrator rights, a normally launched player can be blocked.

For Genshin Impact, public lyre-player projects instruct users to run their players as administrator. This is evidence that a permission comparison is useful, not proof that permissions caused this particular failure.

To compare using this locally created LyreBot: close LyreBot; in File Explorer, right-click `Start LyreBot.cmd`, choose **Run as administrator**, and approve Windows' prompt if you choose to run it. On Windows 11 this option may be under **Show more options**. Reopen the same test score, use the same tap length, and select the actual game window again. Python must still be accessible in the administrator session. If the launcher cannot find Python, report that error rather than installing additional tools.

Change only one setting per comparison. If administrator launch makes the same test work, that supports a permission mismatch. If LyreBot completes successfully with neither notes nor controls, acceptance of synthetic input remains unresolved; do not interpret the success status as proof the game received the notes. Whether Qin Xian currently works in the same instrument/session is another useful comparison.

## Sources

- [Microsoft: SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) — permission restrictions and return values.
- [AutoHotkey's official press-duration documentation](https://github.com/AutoHotkey/AutoHotkeyDocs/blob/v2/docs/lib/SetKeyDelay.htm) — some games require a delay between key-down and key-up.
- [PyDirectInput project](https://github.com/learncodebygaming/pydirectinput) — scan codes and SendInput for game input.
- [Genshin lyre player instructions](https://github.com/he7850/genshin-impact-lyre-song-autoplay) — administrator launch and switching to the instrument before playback.
