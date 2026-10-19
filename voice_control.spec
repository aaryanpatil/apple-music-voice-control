# PyInstaller recipe for Voice Control. Run build.bat rather than this directly.
#
# Produces dist\Voice Control\ : a self-contained folder (Python, libraries, speech
# model, fonts, icon) with "Voice Control.exe" inside. A folder rather than a single
# exe because a one-file build unpacks ~100 MB to a temp dir on every launch.

import os

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

here = SPECPATH  # noqa: F821 - provided by PyInstaller
model = "models/vosk-model-small-en-us-0.15"
whisper = "models/whisper-small.en"  # transcribes "play <song>" requests

a = Analysis(  # noqa: F821
    [os.path.join(here, "app.py")],
    datas=[
        (os.path.join(here, model), model),
        (os.path.join(here, whisper), whisper),
        (os.path.join(here, "fonts"), "fonts"),
        (os.path.join(here, "assets", "icon.ico"), "assets"),
    ],
    # These load their own DLLs, and PyInstaller has no hooks for them, so
    # collect the DLLs explicitly.
    binaries=sum((collect_dynamic_libs(pkg) for pkg in ("vosk", "winrt", "ctranslate2", "uiautomation")), []),
    hiddenimports=collect_submodules("winrt"),
    # Not used: onnxruntime is for faster-whisper's voice-activity filter, and av
    # for decoding audio files (whisper_loader.py stands in for it).
    excludes=["make_icon", "onnxruntime", "av"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Voice Control",
    icon=os.path.join(here, "assets", "icon.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Voice Control")  # noqa: F821
