# Apple Music Voice Control

Control the Apple Music app on Windows with your voice, without switching windows:
**play**, **pause**, **next**, **previous**, and **"play \<song\> by \<artist\>"**.

Speech recognition runs offline. Commands use [Vosk](https://alphacephei.com/vosk/),
limited to the command words so it's fast; song requests are transcribed with
[Whisper](https://github.com/SYSTRAN/faster-whisper) (small.en).

## Setup

Needs Windows 10/11, Python 3.13+, and the Apple Music app from the Microsoft Store.

```
setup.bat
```

This creates `.venv`, installs the libraries from `requirements.txt` and downloads
the speech models (~520 MB) into `models/`, which aren't in the repo because GitHub
won't store files that large.

## Run

```
start.bat
```

Pick a microphone and, ideally, a wake word (much more reliable while music with
vocals is playing), then **Start listening**.

## How it works

| File | What it does |
|---|---|
| `app.py` | The window: settings, playback buttons, console, night mode |
| `controller.py` | Microphone → command recognition; play/pause/next/previous through Windows' media controls |
| `song_search.py` | Transcribes "play …" requests with Whisper and works out what to search for |
| `apple_music_ui.py` | Plays a specific song by driving Apple Music's search through UI Automation |
| `make_icon.py` | Draws the app icon into `assets/` |

Playing a specific song: Apple Music has no API for that, so `apple_music_ui.py`
types the request into Apple Music's search, then chooses "Play '…'" from the top
song's menu. Apple Music comes to the front for a few seconds while it does this;
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
