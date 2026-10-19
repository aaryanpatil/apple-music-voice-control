"""Checks that Windows lets everything Voice Control needs actually run.

Smart App Control (and work "App Control" policies) block unsigned files they
don't trust. Python itself is signed, but many libraries' native files aren't, so
this loads each one in its own process and reports which, if any, are blocked,
plus the exact files from Windows' Code Integrity log.

Run by setup.bat; can also be run on its own:  .venv\\Scripts\\python check_blocked.py
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
CHECKS = [
    ("numpy", "import numpy"),
    ("Pillow (images)", "from PIL import Image, ImageTk"),
    ("CustomTkinter (window)", "import customtkinter"),
    ("sounddevice (microphone)", "import sounddevice"),
    ("winrt (Windows media controls)",
     "from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager"),
    ("uiautomation (drives Apple Music)", "import uiautomation; uiautomation.GetRootControl().Name"),
    ("ctranslate2 (Whisper engine)", "import ctranslate2"),
    ("tokenizers (Whisper)", "import tokenizers"),
    ("Vosk (command recognition)",
     "import ctranslate2, vosk; vosk.SetLogLevel(-1); "
     "vosk.Model(r'models/vosk-model-small-en-us-0.15')"),
    ("Whisper model (song requests)",
     "from whisper_loader import import_faster_whisper; "
     "import_faster_whisper().WhisperModel(r'models/whisper-small.en', device='cpu', compute_type='int8')"),
]
BLOCK_SIGNS = ("application control", "blocked this file", "0x800711c7", "4551")


def recent_blocks(minutes=30):
    """Files Windows' Code Integrity recently refused to load (event 3077 = blocked,
    3033 = didn't meet signing requirements)."""
    script = (
        "Get-WinEvent -LogName 'Microsoft-Windows-CodeIntegrity/Operational' -MaxEvents 200 "
        "-ErrorAction SilentlyContinue | Where-Object { $_.Id -in 3033,3077 -and "
        f"$_.TimeCreated -gt (Get-Date).AddMinutes(-{minutes}) }} | "
        "ForEach-Object { $_.Properties[1].Value } | Sort-Object -Unique"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def main():
    problems = 0
    for name, code in CHECKS:
        result = subprocess.run([sys.executable, "-c", code], cwd=HERE,
                                capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            print(f"  ok       {name}")
            continue
        problems += 1
        error = (result.stderr.strip().splitlines() or ["(no error message)"])[-1]
        blocked = any(sign in error.lower() for sign in BLOCK_SIGNS)
        print(f"  {'BLOCKED' if blocked else 'FAILED '}  {name}\n             {error}")

    if not problems:
        print("\nEverything loads.")
        return 0
    files = recent_blocks()
    if files:
        print("\nFiles Windows blocked recently (from its Code Integrity log):")
        for f in files:
            print(f"  {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
