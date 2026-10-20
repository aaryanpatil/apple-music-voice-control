# Apple Music Voice Control

Control the Apple Music app on Windows with your voice, without switching windows:
**play**, **pause**, **next**, **previous**, **"play \<song\> by \<artist\>"**,
**"play the album \<album\>"** and **"play my \<name\> playlist"** (or
**"play the playlist \<name\>"** for one of Apple Music's). Say **"shuffle"** instead
of "play" (or add "on shuffle") to shuffle an album or playlist; **"shuffle
\<name\>"** finds your playlist of that name, or else Apple Music's top album or
playlist. A bare **"shuffle"** / **"shuffle off"** turns shuffle on or off.

Speech recognition runs offline. Commands use [Vosk](https://alphacephei.com/vosk/),
limited to the command words so it's fast; song requests are transcribed with
[Whisper](https://github.com/SYSTRAN/faster-whisper) (small.en).

## Setup

Needs Windows 10/11 on an Intel/AMD PC, 64-bit Python 3.10 to 3.14 (developed on
3.14; **3.15 is too new** for some libraries, but can be installed alongside), and
the Apple Music app from the Microsoft Store.

Double-click `setup.bat`, or from a PowerShell terminal (VS Code's default) run:

```
.\setup.bat
```

(PowerShell needs the `.\` to run a script from the current folder.) It shows each
step and waits for a key press at the end, so you can see whether it worked. It
creates `.venv`, installs the libraries from `requirements.txt` and downloads the
speech models (~520 MB) into `models/`, which aren't in the repo because GitHub
won't store files that large.

## Run

Double-click `start.bat` (or run `.\start.bat`).

Pick a microphone, then **Start listening**. Commands start with the assistant's
name, Amphion: say "Amphion" (or "Hey Amphion"), wait for the chime, then the
command, or say it in one go: "Amphion, next".

## How it works

| File | What it does |
|---|---|
| `app.py` | The window: settings, playback buttons, console, night mode |
| `controller.py` | Microphone → command recognition; play/pause/next/previous through Windows' media controls |
| `song_search.py` | Transcribes "play …" requests with Whisper and works out what to search for |
| `apple_music_ui.py` | Plays a specific song, album or playlist by driving Apple Music through UI Automation |
| `make_icon.py` | Draws the app icon into `assets/` |

Playing a specific song: Apple Music has no API for that, so `apple_music_ui.py`
types the request into Apple Music's search, then chooses "Play '…'" from the top
song's menu (for an album: opens the top album and presses its Play button).
Your own playlists are opened from the sidebar instead, matching the spoken name
loosely ("current obsession" finds "Current obssession"); a playlist request
without "my" plays yours if the name matches closely, else Apple Music's top
playlist. Apple Music comes to the front for a few seconds while it does this;
focus is then returned to the window you were using.

## Build the exe

```
build.bat
```

Builds `dist\Voice Control\Voice Control.exe` (a self-contained folder; no Python
needed to run it) with PyInstaller, then runs a self-test on it. The exe keeps its
settings in `%APPDATA%\Voice Control`.

## Licenses

The bundled fonts (DM Sans, Source Serif 4) are under the SIL Open Font License;
see `fonts/`.

## Troubleshooting

**"An Application Control policy has blocked this file"** — Windows is refusing to
run an unsigned file (Python libraries usually aren't signed). Two possible causes:

- **Smart App Control** (Windows 11). Check in *Windows Security → App & browser
  control → Smart App Control*. If it's **On**, it blocks most Python setups; turning
  it **Off** fixes this, but Windows only lets you turn it back on by resetting or
  reinstalling Windows, so decide whether you're OK with that.
- **A work/school policy** (App Control for Business or AppLocker) on a managed PC.
  Only the IT department can allow it.

**"Failed building wheel" / "getting requirements to build wheel"** — no ready-made
copy of a library exists for that Python, so pip tried to compile it. Usually the
Python is too new (3.15) or 32-bit: install 64-bit Python 3.14 alongside it
(`py install 3.14`, or the installer from python.org) and run setup again; it
picks 3.14 and rebuilds `.venv` by itself.
