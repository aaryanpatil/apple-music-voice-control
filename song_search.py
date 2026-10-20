"""Turns a spoken "play <song> [by <artist>]" (or "play the album ...") into that
song or album playing.

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


def parse_request(transcript, wake=None):
    """What a transcript like "Amphion, play Uptown Girl by Westlife." asks for:
    {"kind": "song", "query": "uptown girl westlife"}. "play the album Thriller"
    gives {"kind": "album", "query": "thriller"}. None if nothing follows "play"."""
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
    words, album = _take(words, "album")
    words = [w for w in words if w not in ("by", "please")]
    query = " ".join(words)
    if not query or query in NOT_A_SONG:
        return None
    return {"kind": "album" if album else "song", "query": query}


def _take(words, marker):
    """Remove `marker` and an article just before it: "the album thriller" ->
    "thriller", "thriller album" -> "thriller". Returns (words, whether it was there)."""
    if marker not in words:
        return words, False
    i = words.index(marker)
    start = i - 1 if i and words[i - 1] in ("the", "a", "an") else i
    return words[:start] + words[i + 1:], True


class SongSearcher(threading.Thread):
    """Worker thread: audio of a "play ..." request in, song or album playing out.

    Reports to on_event("search", {...}) with a "stage" of:
      "heard"   - transcribed; {"kind"} is "song" or "album", {"query"} what will
                  be searched for
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
        from whisper_loader import import_faster_whisper  # slow import; keep it off the UI thread
        WhisperModel = import_faster_whisper().WhisperModel
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
        request = parse_request(transcript, self.wake)
        if not request:
            if self.send_play:
                self.send_play()
            self.on_event("search", {"stage": "resumed", "transcript": transcript})
            return
        info = {"kind": request["kind"], "query": request["query"]}
        self.on_event("search", {"stage": "heard", "transcript": transcript, **info})
        play = {"song": apple_music_ui.play_song, "album": apple_music_ui.play_album}[request["kind"]]
        try:
            title, artist = play(request["query"])
        except apple_music_ui.PlayError as e:
            self.on_event("search", {"stage": "failed", "error": str(e), **info})
            return
        self.on_event("search", {"stage": "playing", "title": title, "artist": artist, **info})
