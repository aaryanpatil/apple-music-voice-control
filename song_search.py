"""Turns a spoken "play <song> [by <artist>]" into that song playing.

The command recognizer (Vosk, limited to a few words) hears "play" and records
what follows; this transcribes it with Whisper, which runs offline and handles
song titles and artist names well, then has apple_music_ui search and play it.
"""

import queue
import re
import threading
from pathlib import Path

import numpy as np
import uiautomation as auto

import apple_music_ui

WHISPER_DIR = Path(__file__).parent / "models" / "whisper-small.en"
PROMPT = "Play a song by an artist."  # nudges Whisper towards titles and names

# Whisper sometimes hears "play" as one of these.
PLAY_WORDS = {"play", "plays", "played", "playing", "place", "plate", "pray"}
FILLER = {"me", "the song", "song", "a song", "some"}
NOT_A_SONG = {"music", "the music", "something", "it", "again", "my music"}  # "play music" = resume


def search_text(transcript, wake=None):
    """What to search for in a transcript like "Jarvis, play Uptown Girl by Westlife."
    Returns "uptown girl westlife", or None if nothing follows "play"."""
    words = re.sub(r"[^\w' ]+", " ", transcript.lower()).split()
    start = next((i for i, w in enumerate(words) if w in PLAY_WORDS), None)
    if start is None:
        return None
    words = words[start + 1:]
    if wake and words and words[0] == wake:
        words = words[1:]
    stripped = True
    while stripped:  # "play me the song ..." has more than one
        stripped = False
        for filler in sorted(FILLER, key=len, reverse=True):
            f = filler.split()
            if words[:len(f)] == f:
                words, stripped = words[len(f):], True
    words = [w for w in words if w not in ("by", "please")]
    query = " ".join(words)
    return query if query and query not in NOT_A_SONG else None


class SongSearcher(threading.Thread):
    """Worker thread: audio of a "play ..." request in, song playing out.

    Reports to on_event("search", {...}) with a "stage" of:
      "heard"   - transcribed; {"query"} is what will be searched for
      "playing" - started; {"title", "artist"}
      "failed"  - {"error"}
    A request with nothing after "play" just resumes playback ({"stage": "resumed"}).
    """

    def __init__(self, on_event, wake=None, send_play=None):
        super().__init__(daemon=True)
        self.on_event = on_event
        self.wake = wake
        self.send_play = send_play  # plain "play" for requests with no song in them
        self.jobs = queue.Queue()
        self.busy = False
        self._model = None

    def submit(self, audio_bytes):
        """Queue a request; ignored while one is already being handled."""
        if self.busy:
            return False
        self.busy = True
        self.jobs.put(audio_bytes)
        return True

    def stop(self):
        self.jobs.put(None)

    def run(self):
        # UI Automation needs COM set up in the thread that uses it.
        with auto.UIAutomationInitializerInThread():
            try:
                self._load()
            except Exception as e:
                self.on_event("search", {"stage": "failed", "error": f"speech model: {e}"})
            while (audio := self.jobs.get()) is not None:
                try:
                    self._handle(audio)
                except Exception as e:
                    self.on_event("search", {"stage": "failed", "error": str(e)})
                finally:
                    self.busy = False

    def _load(self):
        from faster_whisper import WhisperModel  # slow import; keep it off the UI thread
        if not WHISPER_DIR.exists():
            raise RuntimeError(f"not found at {WHISPER_DIR}")
        self._model = WhisperModel(str(WHISPER_DIR), device="cpu", compute_type="int8")

    def transcribe(self, audio_bytes):
        samples = np.frombuffer(audio_bytes, np.int16).astype(np.float32) / 32768
        segments, _ = self._model.transcribe(samples, language="en", beam_size=1,
                                             initial_prompt=PROMPT)
        return " ".join(s.text for s in segments).strip()

    def _handle(self, audio_bytes):
        if self._model is None:
            raise RuntimeError("speech model isn't loaded")
        transcript = self.transcribe(audio_bytes)
        query = search_text(transcript, self.wake)
        if not query:
            if self.send_play:
                self.send_play()
            self.on_event("search", {"stage": "resumed", "transcript": transcript})
            return
        self.on_event("search", {"stage": "heard", "query": query, "transcript": transcript})
        try:
            title, artist = apple_music_ui.play_song(query)
        except apple_music_ui.PlayError as e:
            self.on_event("search", {"stage": "failed", "query": query, "error": str(e)})
            return
        self.on_event("search", {"stage": "playing", "query": query, "title": title, "artist": artist})
