"""Turns a spoken "play <song> [by <artist>]" (or "play the album ...", "play my
... playlist", "shuffle ...") into that song, album or playlist playing.

The command recognizer (Vosk, limited to a few words) hears "play" (or
"shuffle") and records
what follows; this transcribes it with Whisper, which runs offline and handles
song titles and artist names well, then has apple_music_ui search and play it.
"""

import difflib
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
SHUFFLE_WORDS = {"shuffle", "shuffles", "shuffled", "shuffling"}
FILLER = {"me", "the song", "song", "a song", "some"}
NOT_A_SONG = {"music", "the music", "something", "it", "again", "my music"}  # "play music" = resume
# How similar a spoken playlist name must be to one of the user's (0-1). "my ...
# playlist" (or "shuffle my ...") settles for a looser match; otherwise a close
# one, or it's looked up in Apple Music's catalog (or, without "playlist",
# played as a song).
MY_PLAYLIST_MATCH, PLAYLIST_MATCH = 0.7, 0.85


def parse_request(transcript, wake=None):
    """What a transcript like "Amphion, play Uptown Girl by Westlife." asks for:
      {"kind": "song", "query": "uptown girl westlife", "shuffle": False, "mine": False}
      "play the album Thriller"   -> "kind": "album", "query": "thriller"
      "play my Gym playlist"      -> "kind": "playlist", "query": "gym", "mine": True
      "play my Favourite Songs"   -> "kind": "song", "query": "my favourite songs",
                                     "library": "favourite songs"
      "shuffle the album Thriller", "play my Gym playlist on shuffle" -> "shuffle": True
      "shuffle Favourite Songs"   -> "kind": "song", "shuffle": True,
                                     "library": "favourite songs"
    ("library": the user's playlist of that name if there is one, else the song,
    or for "shuffle", Apple Music's top album or playlist).
    A bare "shuffle" gives {"action": "shuffle"}, "shuffle off" {"action":
    "shuffle off"}. None if nothing follows "play"."""
    words = re.sub(r"[^\w' ]+", " ", transcript.lower()).split()
    start = next((i for i, w in enumerate(words) if w in PLAY_WORDS | SHUFFLE_WORDS), None)
    if start is None:
        return None
    shuffle = words[start] in SHUFFLE_WORDS
    words = words[start + 1:]
    if wake and words and words[0] == wake:
        words = words[1:]
    if shuffle and words in (["off"], ["of"]):
        return {"action": "shuffle off"}
    for ending in (["on", "shuffle"], ["in", "shuffle"], ["shuffled"]):
        if len(words) > len(ending) and words[-len(ending):] == ending:
            words, shuffle = words[:-len(ending)], True
            break
    stripped = True
    while stripped:  # "play me the song ..." has more than one
        stripped = False
        for filler in sorted(FILLER, key=len, reverse=True):
            f = filler.split()
            if words[:len(f)] == f:
                words, stripped = words[len(f):], True
    words, playlist = _take(words, "playlist")
    words, album = (words, False) if playlist else _take(words, "album")
    words = [w for w in words if w not in ("by", "please")]
    query = " ".join(words)
    if not query or query in NOT_A_SONG:
        return {"action": "shuffle"} if shuffle else None
    mine = words[0] == "my"
    name = " ".join(words[1:]) if mine else query
    if playlist:
        if not name:
            return {"action": "shuffle"} if shuffle else None
        return {"kind": "playlist", "query": name, "shuffle": shuffle, "mine": mine}
    if album:
        return {"kind": "album", "query": query, "shuffle": shuffle, "mine": False}
    request = {"kind": "song", "query": query, "shuffle": shuffle, "mine": mine}
    if (mine or shuffle) and name:
        request["library"] = name
    return request


def best_playlist(spoken, names, cutoff):
    """The name in `names` closest to `spoken`, if it's at least `cutoff` similar
    (0-1): "favourite songs" finds "Favorite Songs"."""
    def simple(text):
        words = re.sub(r"[^\w ]+", " ", text.lower().replace("&", " and ")).split()
        return " ".join(words[1:] if words[:1] in (["the"], ["a"]) else words)
    spoken = simple(spoken)
    scored = [(difflib.SequenceMatcher(None, spoken, simple(n)).ratio(), n) for n in names]
    score, name = max(scored, default=(0, None))
    return name if score >= cutoff else None


def _take(words, marker):
    """Remove `marker` and an article just before it: "the album thriller" ->
    "thriller", "thriller album" -> "thriller". Returns (words, whether it was there)."""
    if marker not in words:
        return words, False
    i = words.index(marker)
    start = i - 1 if i and words[i - 1] in ("the", "a", "an") else i
    return words[:start] + words[i + 1:], True


class SongSearcher(threading.Thread):
    """Worker thread: audio of a "play ..." request in, song, album or playlist
    playing out.

    Reports to on_event("search", {...}) with a "stage" of:
      "heard"   - transcribed; {"kind"} is "song", "album" or "playlist", {"query"}
                  what will be searched for, {"shuffle"} whether to shuffle it
      "playing" - started; {"kind", "title", "artist"} ("artist" is the curator for
                  a playlist, "your library" for the user's own)
      "failed"  - {"error"}
    A request that names nothing is passed to send(): a bare "play" resumes
    playback, "shuffle" turns shuffle on ({"stage": "resumed", "action", "note"}).
    """

    def __init__(self, on_event, wake=None, send=None):
        super().__init__(daemon=True)
        self.on_event = on_event
        self.wake = wake
        # send(action) for requests that name nothing: "play" (resume), "shuffle",
        # "shuffle off". Returns a note for the console, or None.
        self.send = send
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

    def transcribe(self, audio_bytes, prompt=PROMPT):
        samples = np.frombuffer(audio_bytes, np.int16).astype(np.float32) / 32768
        segments, _ = self._model.transcribe(samples, language="en", beam_size=1,
                                             initial_prompt=prompt)
        return " ".join(s.text for s in segments).strip()

    def _handle(self, audio_bytes):
        if self._model is None:
            raise RuntimeError("speech model isn't loaded")
        transcript = self.transcribe(audio_bytes)
        request = parse_request(transcript, self.wake) or {"action": "play"}
        if "action" in request:
            note = self.send(request["action"]) if self.send else None
            self.on_event("search", {"stage": "resumed", "action": request["action"], "note": note,
                                     "transcript": transcript})
            return
        info = {"kind": request["kind"], "query": request["query"], "shuffle": request["shuffle"]}
        self.on_event("search", {"stage": "heard", "transcript": transcript, **info})
        try:
            kind, title, artist = self._play(request, audio_bytes)
        except apple_music_ui.PlayError as e:
            self.on_event("search", {"stage": "failed", "error": str(e), **info})
            return
        self.on_event("search", {"stage": "playing", "title": title, "artist": artist,
                                 **info, "kind": kind})

    def _play(self, request, audio_bytes):
        """Play what was asked for. Returns (kind, title, artist): kind as played,
        which differs from the request's when "my ..." turns out to be a playlist,
        or "shuffle <name>" an album or playlist."""
        kind, query, shuffle = request["kind"], request["query"], request["shuffle"]
        if kind == "playlist" or "library" in request:
            found = apple_music_ui.play_library_playlist(
                lambda names: self._pick_playlist(request, names, audio_bytes), shuffle)
            if found:
                return ("playlist",) + found
            if request["mine"] and (kind == "playlist" or shuffle):
                name = request.get("library", query)
                raise apple_music_ui.PlayError(f"no playlist called “{name}” in your library")
        if kind == "song" and shuffle:  # a song can't be shuffled: "shuffle Thriller" means the album
            title, artist, kind = apple_music_ui.play_album_or_playlist(query, shuffle=True)
            return kind, title, artist
        if kind == "song":
            return ("song",) + apple_music_ui.play_song(query)
        play = {"album": apple_music_ui.play_album, "playlist": apple_music_ui.play_playlist}[kind]
        return (kind,) + play(query, shuffle)

    def _pick_playlist(self, request, names, audio_bytes):
        """Which of the user's playlists (`names`) the request means, or None."""
        wants_playlist = request["kind"] == "playlist" or request["shuffle"]
        cutoff = MY_PLAYLIST_MATCH if request["mine"] and wants_playlist else PLAYLIST_MATCH
        name = best_playlist(request.get("library", request["query"]), names, cutoff)
        if name is None and wants_playlist:
            # Whisper may not know the name ("Gym" heard as "Jim"); listen again,
            # this time told what the user's playlists are called.
            again = parse_request(self.transcribe(audio_bytes, playlist_prompt(names)), self.wake)
            if again and "kind" in again:
                name = best_playlist(again.get("library", again["query"]), names, cutoff)
        return name


def playlist_prompt(names):
    return "Play my playlist. My playlists: " + ", ".join(names[:60]) + "."
