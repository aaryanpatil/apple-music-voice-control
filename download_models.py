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


def main():
    MODELS.mkdir(exist_ok=True)

    if (MODELS / VOSK_NAME).exists():
        print(f"{VOSK_NAME}: already there")
    else:
        print(f"{VOSK_NAME}: downloading (~40 MB)...")
        with urllib.request.urlopen(VOSK_URL) as response:
            zipfile.ZipFile(io.BytesIO(response.read())).extractall(MODELS)

    if (MODELS / WHISPER_NAME / "model.bin").exists():
        print(f"{WHISPER_NAME}: already there")
    else:
        print(f"{WHISPER_NAME}: downloading (~480 MB)...")
        from faster_whisper import download_model
        download_model("small.en", output_dir=str(MODELS / WHISPER_NAME))

    print("Models ready.")


if __name__ == "__main__":
    main()
