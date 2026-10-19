"""Imports faster_whisper without PyAV.

faster-whisper only uses PyAV (av) to decode audio files; this app passes it raw
microphone samples. Not installing av avoids ~60 MB of unsigned FFmpeg DLLs, which
Windows' Smart App Control may block (as may compiling av where no ready-made copy
exists). faster_whisper still imports av at load time, so stand in an empty module.
"""

import sys
import types


def import_faster_whisper():
    try:
        import av  # noqa: F401
    except ImportError:  # not installed, or blocked by Windows
        sys.modules["av"] = types.ModuleType("av")
    import faster_whisper
    return faster_whisper
