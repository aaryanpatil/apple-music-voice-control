"""Voice controller for Apple Music on Windows.

Listens on a microphone (offline, via Vosk) and sends play / pause / next /
previous to Apple Music through Windows' media session API, so the app never
needs focus. Falls back to global media keys if no session is found.
"play <song> [by <artist>]" is recorded and handed to song_search, which
transcribes it (offline, Whisper) and plays that song.

Every command starts with the assistant's name, Amphion: "Amphion, next", or
"Hey Amphion" ... "pause".

The UI lives in app.py. This file can also run on its own from a terminal:
    python controller.py
"""

import argparse
import asyncio
import collections
import ctypes
import json
import math
import queue
import threading
from array import array
from pathlib import Path

# Whisper's engine must load its native libraries before Vosk's model does:
# the other way round, the two clash and the process crashes outright.
import ctranslate2  # noqa: F401
import sounddevice as sd
from vosk import KaldiRecognizer, Model, SetLogLevel
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)

MODEL_DIR = Path(__file__).parent / "models" / "vosk-model-small-en-us-0.15"
SAMPLE_RATE = 16000
APP_ID_HINT = "AppleMusic"

# Spoken phrase -> action. Longer phrases are listed so the recognizer can
# match them whole instead of guessing at single words. No "play ..." phrases:
# the recognizer would squeeze song titles into them ("play music"); a bare
# "play" is followed up by song_search instead.
PHRASES = {
    "play": "play",
    "resume": "play",
    "pause": "pause",
    "stop": "pause",
    "pause music": "pause",
    "next": "next",
    "skip": "next",
    "next song": "next",
    "skip song": "next",
    "previous": "previous",
    "back": "previous",
    "go back": "previous",
    "previous song": "previous",
    "last song": "previous",
    "shuffle": "shuffle",
    "shuffle off": "shuffle off",
}
# Command words that may be followed by what to play ("play Thriller", "shuffle
# my Gym playlist"): what follows is recorded for song_search.
REQUEST_WORDS = {"play", "shuffle"}

# The assistant's name: commands only count after it, so singing along or game
# audio can't trigger them.
WAKE_WORD = "amphion"

# "Amphion" isn't in Vosk's vocabulary, so it listens for words it knows that
# sound the same. "am phi on" caught am-fee-on, am-feh-on and am-fe-uhn (60/60
# test clips) with no false wakes on lyrics; adding "am fe on" also matched
# "I'm feeling ..." (up to 20% of lines), and "... un"/"... an" spellings matched
# "I am free and I am on".
WAKE_SOUNDS = ["am phi on"]

# After the wake word on its own ("Amphion"), how long a command is listened for.
ARM_WINDOW = 5.0
# Least confidence Vosk must have in the wake word, where it reports one (final
# results; in-progress ones don't have confidences). Real "Amphion"s scored 0.84+.
WAKE_CONF = 0.75

# After a bare "play": how long to wait for more words before treating it as
# "resume", the longest song request recorded after it, and the pause that ends
# one (seconds).
PLAY_WAIT, REQUEST_MAX, REQUEST_PAUSE = 0.6, 4.0, 0.7
PREROLL = 1.0  # audio kept from before "play" was recognized, so it's included
# Telling speech after "play" from background music, by loudness: the background
# level is this percentile of the last few seconds, the threshold this fraction
# of the way from it to the voice level, and speech needs this many loud
# 0.1s chunks in a row.
BG_PERCENTILE, VOICE_FRACTION, LOUD_RUN = 0.5, 0.6, 2
VOICE_HEADROOM = 6.0  # the threshold stays at least this many dB below the voice

# Virtual-key codes for the fallback path.
MEDIA_KEYS = {"play": 0xB3, "pause": 0xB3, "next": 0xB0, "previous": 0xB1}

_model = None


def load_model():
    global _model
    if _model is None:
        if not MODEL_DIR.exists():
            raise RuntimeError(f"Speech model not found at {MODEL_DIR}")
        SetLogLevel(-1)
        _model = Model(str(MODEL_DIR))
    return _model


class AppleMusic:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.manager = self.loop.run_until_complete(SessionManager.request_async())

    def _session(self):
        for s in self.manager.get_sessions():
            if APP_ID_HINT.lower() in s.source_app_user_model_id.lower():
                return s
        return None

    def is_open(self):
        return self._session() is not None

    def state(self):
        """'closed', 'playing' or 'paused' (anything not playing counts as paused)."""
        session = self._session()
        if session is None:
            return "closed"
        PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
        return "playing" if session.get_playback_info().playback_status == PLAYING else "paused"

    def send(self, action):
        """Send an action. Returns a short note if something went wrong."""
        if action in ("shuffle", "shuffle off"):
            return self._shuffle(action == "shuffle")
        session = self._session()
        if session is None:
            press_media_key(MEDIA_KEYS[action])
            return "Apple Music not found, sent media key"
        controls = session.get_playback_info().controls
        call, enabled = {
            "play": (session.try_play_async, controls.is_play_enabled),
            "pause": (session.try_pause_async, controls.is_pause_enabled),
            "next": (session.try_skip_next_async, controls.is_next_enabled),
            "previous": (session.try_skip_previous_async, controls.is_previous_enabled),
        }[action]
        note = None
        if not enabled:
            # Apple Music's reported state can be stale (e.g. "playing" with
            # nothing loaded), which greys out play or pause. The toggle still
            # acts on its real state.
            if action in ("play", "pause") and controls.is_play_pause_toggle_enabled:
                call = session.try_toggle_play_pause_async
                note = f"{action} unavailable, used play/pause toggle"
            else:
                note = f"Apple Music has {action} disabled; is a song loaded?"
        if not self.loop.run_until_complete(call()):
            return "Apple Music rejected the command"
        return note

    def _shuffle(self, on):
        # Apple Music doesn't offer shuffle through Windows' media controls, so
        # this presses its Shuffle button.
        import apple_music_ui
        import uiautomation
        try:
            with uiautomation.UIAutomationInitializerInThread():
                changed = apple_music_ui.set_shuffle(on)
        except apple_music_ui.PlayError as e:
            return str(e)
        return None if changed else f"shuffle was already {'on' if on else 'off'}"


def press_media_key(vk):
    KEYEVENTF_KEYUP = 0x0002
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def parse(text):
    """The action for command words like "next song", or None."""
    words = text.split()
    phrase = " ".join(words)
    if phrase in PHRASES:
        return PHRASES[phrase]
    # Tolerate extra recognized words: take the first known single word.
    for w in words:
        if w in PHRASES:
            return PHRASES[w]
    return None


def list_microphones():
    """Return [(device_index, display_name)] for each physical input.

    Windows exposes every mic once per audio API. We use the MME entries (most
    compatible, resample to 16 kHz for us) but MME truncates names to 31
    characters, so borrow the full name from another API when one matches.
    """
    apis = sd.query_hostapis()
    devices = sd.query_devices()
    full_names = [d["name"] for d in devices
                  if d["max_input_channels"] > 0 and apis[d["hostapi"]]["name"] != "MME"]
    mics = []
    for i, d in enumerate(devices):
        if d["max_input_channels"] <= 0 or apis[d["hostapi"]]["name"] != "MME":
            continue
        if d["name"].startswith("Microsoft Sound Mapper"):
            continue
        name = next((f for f in full_names if f.startswith(d["name"])), d["name"])
        mics.append((i, name))
    return mics


class Listener(threading.Thread):
    """Background thread: mic -> speech recognition -> Apple Music.

    Reports to on_event(kind, data) from its own thread:
      ("status", str)   ("level", float 0-1)   ("error", str)
      ("heard", {"text", "conf", "action", "note"})
      ("search", {"stage", ...})  - a "play <song>" request; see song_search
      ("wake", bool)              - started / stopped listening for a command
                                    after hearing "Amphion" on its own
    action is None when speech was heard but ignored (no "Amphion", low
    confidence, not a command).
    """

    def __init__(self, on_event, device=None, min_conf=0.6):
        super().__init__(daemon=True)
        self.on_event = on_event
        self.device = device
        self.min_conf = min_conf  # read live, may be changed while running
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            self._run()
        except Exception as e:
            self.on_event("error", str(e))
        finally:
            self.on_event("status", "stopped")

    def _run(self):
        import song_search  # loads Whisper's libraries; only needed once listening
        detector = CommandDetector(self.min_conf)
        music = AppleMusic()
        searcher = song_search.SongSearcher(self.on_event, WAKE_WORD,
                                            send=lambda action: AppleMusic().send(action))
        searcher.start()
        try:
            self._listen(detector, music, searcher)
        finally:
            searcher.stop()

    def _listen(self, detector, music, searcher):
        audio = queue.Queue()

        def on_audio(data, frames, time, status):
            audio.put(bytes(data))

        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=1600, dtype="int16",
                               channels=1, callback=on_audio, device=self.device,
                               latency="low"):
            self.on_event("status", "listening")
            while not self._stop_event.is_set():
                try:
                    data = audio.get(timeout=0.2)
                except queue.Empty:
                    continue
                self.on_event("level", _level(data))
                detector.min_conf = self.min_conf
                was_armed = detector.armed
                heard = detector.feed(data)
                if detector.armed != was_armed:
                    self.on_event("wake", detector.armed)
                if heard is None:
                    continue
                if heard["action"] == "search":
                    if searcher.submit(heard.pop("audio")):
                        heard["note"] = None
                    else:
                        heard["action"], heard["note"] = None, "still busy with the last song"
                else:
                    heard["note"] = music.send(heard["action"]) if heard["action"] else None
                self.on_event("heard", heard)


class CommandDetector:
    """Turns 16 kHz int16 audio chunks into commands.

    Vosk only finalizes an utterance after a stretch of silence, which never
    comes while music with vocals is playing. So we also watch the in-progress
    (partial) hypothesis and act once it has named the same command for a few
    chunks in a row. Only words heard since the last command count, so lyrics
    recognized earlier in a long utterance can't leak into the next command.

    A bare "play" (or "shuffle") starts a recording: if more words follow, the
    result is a "search" action carrying that audio (for Whisper); if not, it's
    "play" (or "shuffle").

    Commands count only after "Amphion": in one go ("Amphion, next"), or
    Siri-style: the name on its own arms the detector (`armed`) and the next
    command within ARM_WINDOW seconds counts without repeating it.
    """

    def __init__(self, min_conf=0.6):
        self.wake = WAKE_WORD
        self.min_conf = min_conf
        self._sounds = [s.split() for s in WAKE_SOUNDS]
        vocab = set(PHRASES)
        # "Hey Amphion" needs no entry: "hey" comes through as [unk], which is skipped.
        # (Listing it made "Hotel" sound like "hey am...".)
        for sound in WAKE_SOUNDS:
            vocab |= {sound} | {f"{sound} {p}" for p in PHRASES}
        self.rec = KaldiRecognizer(load_model(), SAMPLE_RATE,
                                   json.dumps(sorted(vocab) + ["[unk]"]))
        self.rec.SetWords(True)
        self._consumed = 0   # words of the current utterance already acted on
        self._last = None    # last partial command text
        self._streak = 0     # chunks in a row it has been the same
        self._history = collections.deque()  # recent audio, for the PREROLL
        self._history_s = 0.0
        self._request = None  # recording in progress after a bare "play"
        self._clock = 0.0          # seconds of audio fed so far
        self._armed_until = None   # set while listening for a command after the wake word

    @property
    def armed(self):
        return self._armed_until is not None

    def _merge_wake(self, items):
        """Turn the wake word's spoken form back into one word: [("am", c1), ("phi", c2),
        ("on", c3), ("next", c4)] -> [("amphion", avg), ("next", c4)]. Items are
        (word, confidence or None) pairs."""
        out, i = [], 0
        while i < len(items):
            for sound in self._sounds:
                if [w for w, _ in items[i:i + len(sound)]] == sound:
                    confs = [c for _, c in items[i:i + len(sound)] if c is not None]
                    out.append((self.wake, sum(confs) / len(confs) if confs else None))
                    i += len(sound)
                    break
            else:
                out.append(items[i])
                i += 1
        return out

    def _understand(self, items):
        """("wake", None) for the wake word on its own, ("command", action) for a
        command that counts (after the wake word, or while armed), else (None, None).
        items are (word, confidence or None) pairs."""
        words = [w for w, _ in items]
        if self.wake in words:
            last = len(words) - 1 - words[::-1].index(self.wake)
            conf = items[last][1]
            if conf is not None and conf < WAKE_CONF:
                return None, None  # a shaky match, like "I'm feeling" in a chorus
            words = words[last + 1:]
            if not words:
                return "wake", None
        elif not self.armed:
            return None, None
        action = parse(" ".join(words)) if words else None
        return ("command", action) if action else (None, None)

    def stable_chunks(self):
        """Chunks (0.1 s each) a partial must hold before acting: 2-4 by strictness."""
        return 2 + max(0, min(2, round((self.min_conf - 0.3) / 0.65 * 2)))

    def feed(self, data):
        """Returns {"text", "conf", "action", "early"} when something was heard, else None.
        action is None for speech that was ignored; conf is None for early results.
        A "search" action also has "audio": the recorded request."""
        self._clock += len(data) / 2 / SAMPLE_RATE
        self._remember(data)
        if self._request is not None:
            return self._record(data)
        if self.armed and self._clock > self._armed_until:
            self._armed_until = None  # no command came

        if self.rec.AcceptWaveform(data):
            result = json.loads(self.rec.Result())
            raw = [w["word"] for w in result.get("result", [])]
            words = self._merge_wake([(w["word"], w["conf"]) for w in result.get("result", [])
                                      if w["word"] != "[unk]"])
            new = words[self._consumed:]
            self._reset_partial(consumed=0)
            if not new:
                return None
            text = " ".join(w for w, _ in new)
            conf = sum(c for _, c in new) / len(new)
            kind, action = self._understand(new) if conf >= self.min_conf else (None, None)
            if kind == "wake":
                self._armed_until = self._clock + ARM_WINDOW
                return None
            if kind == "command":
                self._armed_until = None
                word = self._request_word(text)
                if word and action == PHRASES[word] and raw[-1] != word:
                    # The whole request ended before "play" was caught early;
                    # it's all in the recent audio.
                    return self._heard_search(b"".join(self._history), word)
            return {"text": text, "conf": conf, "action": action, "early": False}

        partial = json.loads(self.rec.PartialResult()).get("partial", "")
        items = self._merge_wake([(w, None) for w in partial.split() if w != "[unk]"])
        words = [w for w, _ in items]
        new = items[self._consumed:]
        text = " ".join(w for w, _ in new)
        kind, action = self._understand(new) if new else (None, None)
        if kind is None:
            self._last, self._streak = None, 0
            return None
        self._streak = self._streak + 1 if text == self._last else 1
        self._last = text
        if self._streak < self.stable_chunks():
            return None
        self._reset_partial(consumed=len(words))
        if kind == "wake":
            self._armed_until = self._clock + ARM_WINDOW
            return None
        self._armed_until = None
        word = self._request_word(text)
        if word and action == PHRASES[word]:
            # Could be "resume" or "play <song>": record what follows to find out.
            self._start_request(word)
            return None
        return {"text": text, "conf": None, "action": action, "early": True}

    def _request_word(self, text):
        """"play" or "shuffle" if the command starts with it (not "resume"): what to
        play may be named after it. Vosk may force the song's words into command
        words ("play hey am..."), so anything after it still counts; Whisper sorts
        it out."""
        words = text.split()
        if self.wake in words:
            words = words[len(words) - words[::-1].index(self.wake):]
        return words[0] if words[:1] and words[0] in REQUEST_WORDS else None

    def _remember(self, data):
        """Keep the last few seconds of audio."""
        self._history.append(data)
        self._history_s += len(data) / 2 / SAMPLE_RATE
        while self._history_s > REQUEST_MAX + PREROLL and len(self._history) > 1:
            self._history_s -= len(self._history.popleft()) / 2 / SAMPLE_RATE

    def _start_request(self, word):
        """Begin recording after a bare "play" (or "shuffle": `word`), with the audio
        just before it.

        Whether more words follow, and when they stop, is judged by loudness:
        Vosk only knows the command words, so it's slow to report anything else.
        The threshold sits between the background level (the quieter part of the
        last few seconds) and the voice level (the word "play" itself)."""
        chunks = list(self._history)
        preroll, length = [], 0
        for chunk in reversed(chunks):
            preroll.insert(0, chunk)
            length += len(chunk)
            if length >= PREROLL * SAMPLE_RATE * 2:
                break
        levels = sorted(_db(c) for c in chunks)
        background = levels[int(len(levels) * BG_PERCENTILE)]
        voice = max(_db(c) for c in chunks[-15:])  # the last ~1.5s holds "play", even if caught late
        threshold = background + max(3.0, VOICE_FRACTION * (voice - background))
        # The last few seconds can be mostly speech ("Amphion ... play ..."), which
        # puts the "background" near the voice level; keep the threshold below it.
        threshold = max(background + 3.0, min(threshold, voice - VOICE_HEADROOM))
        self._request = {"word": word, "audio": preroll, "elapsed": 0.0, "more": False,
                         "quiet": 0.0, "loud": 0, "threshold": threshold}

    def _record(self, data):
        """After a bare "play": keep recording until the request ends, then decide."""
        r = self._request
        r["audio"].append(data)
        step = len(data) / 2 / SAMPLE_RATE
        r["elapsed"] += step
        ended = self.rec.AcceptWaveform(data)  # keep Vosk's utterance in step
        if _db(data) >= r["threshold"]:
            r["quiet"] = 0.0
            r["loud"] += 1
            # Speech, not just the tail of "play" or a loud moment in the music.
            if r["elapsed"] > 0.15 and r["loud"] >= LOUD_RUN:
                r["more"] = True
        else:
            r["quiet"] += step
            r["loud"] = 0

        if not r["more"]:
            if r["quiet"] >= PLAY_WAIT:
                return self._finish_request(ended, {"text": r["word"], "conf": None,
                                                    "action": PHRASES[r["word"]], "early": True})
            return None
        if r["quiet"] >= REQUEST_PAUSE or r["elapsed"] >= REQUEST_MAX:
            return self._finish_request(ended, self._heard_search(b"".join(r["audio"]), r["word"]))
        return None

    def _finish_request(self, ended, heard):
        # Drop the rest of this utterance: Vosk would otherwise finish it later
        # and report the same "play" again.
        self._request = None
        self.rec.Reset()
        self._reset_partial(consumed=0)
        return heard

    def _heard_search(self, audio, word):
        return {"text": f"{word} …", "conf": None, "action": "search", "early": True, "audio": audio}

    def _reset_partial(self, consumed):
        self._consumed, self._last, self._streak = consumed, None, 0


def _db(data):
    """Loudness of an int16 block in dBFS (-90 for silence)."""
    samples = array("h", data)
    if not samples:
        return -90.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return 20 * math.log10(max(rms, 1) / 32768)


def _level(data):
    """Rough loudness of an int16 block, scaled 0-1 for a meter."""
    # Map roughly -60 dBFS..0 dBFS onto 0..1.
    return max(0.0, min(1.0, (_db(data) + 60) / 60))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-conf", type=float, default=0.6,
                    help="minimum average word confidence (0-1), default 0.6")
    ap.add_argument("--device", type=int, help="input device index (see --list-mics)")
    ap.add_argument("--list-mics", action="store_true", help="list microphones and exit")
    args = ap.parse_args()

    if args.list_mics:
        for i, name in list_microphones():
            print(f"{i:3}  {name}")
        return

    def on_event(kind, data):
        if kind == "status":
            print(f"[{data}]")
        elif kind == "error":
            print(f"error: {data}")
        elif kind == "heard" and data["action"]:
            extra = f"  ({data['note']})" if data["note"] else ""
            conf = "early" if data["conf"] is None else f"{data['conf']:.2f}"
            print(f"heard '{data['text']}' ({conf}) -> {data['action']}{extra}")
        elif kind == "search":
            print(f"song request: {data}")
        elif kind == "wake":
            print("[Amphion is listening]" if data else "[stopped listening]")

    listener = Listener(on_event, args.device, args.min_conf)
    listener.start()
    try:
        while listener.is_alive():
            listener.join(0.5)
    except KeyboardInterrupt:
        listener.stop()
        listener.join(2)


if __name__ == "__main__":
    main()
