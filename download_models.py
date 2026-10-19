"""Downloads the two offline speech models into models/ (skips any already there).

  - Vosk small English: recognizes the commands (play, pause, next, ...)
  - Whisper small.en:   transcribes "play <song> by <artist>" requests

They're not in the repo because GitHub won't store files this large.
"""

import io
import urllib.request
import zipfile
from pathlib import Path

MODELS = Path(__file__).parent / "models"
VOSK_NAME = "vosk-model-small-en-us-0.15"
VOSK_URL = f"https://alphacephei.com/vosk/models/{VOSK_NAME}.zip"
WHISPER_NAME = "whisper-small.en"


def download(url):
    """Fetch url into memory, showing progress."""
    with urllib.request.urlopen(url) as response:
        total = int(response.headers.get("Content-Length", 0))
        data = bytearray()
        while chunk := response.read(1 << 20):
            data += chunk
            if total:
                print(f"\r  {len(data) / 1e6:.0f} / {total / 1e6:.0f} MB", end="", flush=True)
        print()
    return bytes(data)


def main():
    MODELS.mkdir(exist_ok=True)

    if (MODELS / VOSK_NAME).exists():
        print(f"{VOSK_NAME}: already there")
    else:
        print(f"{VOSK_NAME}: downloading (~40 MB)...")
        zipfile.ZipFile(io.BytesIO(download(VOSK_URL))).extractall(MODELS)

    if (MODELS / WHISPER_NAME / "model.bin").exists():
        print(f"{WHISPER_NAME}: already there")
    else:
        print(f"{WHISPER_NAME}: downloading (~480 MB)...")
        from faster_whisper import download_model
        download_model("small.en", output_dir=str(MODELS / WHISPER_NAME))

    print("Models ready.")


if __name__ == "__main__":
    main()
