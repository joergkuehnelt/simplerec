
#!/usr/bin/env python3
# -*- coding: utf-8 -*-


from __future__ import annotations

import os
import sys
import math
import time
import queue
import shutil
import signal
import termios
import threading
import subprocess
import datetime as dt
import asyncio
import re
import argparse
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

try:
    from shazamio import Shazam
    SONGREC_AVAILABLE = True
except Exception:
    Shazam = None
    SONGREC_AVAILABLE = False

try:
    import psutil as _psutil
except ImportError:
    _psutil = None  # type: ignore

VERSION = "0.1"

SAMPLE_RATE_FALLBACK = 48000
BLOCKSIZE = 2048
DTYPE = "float32"
PREVIEW_SECONDS = 5
SEGMENT_SECONDS = 60 * 60
DEVICE_PROBE_SECONDS = 0.18
UI_REFRESH_SECONDS = 0.08
METER_WIDTH = 38
PEAK_HOLD_SECONDS = 1.2
CLIP_HOLD_SECONDS = 2.0
CLIP_THRESHOLD = 0.995
# Level-meter warning threshold (must match bar_color zones)
LEVEL_CLIP_DB   = -3.0   # dBFS – effectively clipping (→ red)
LEVEL_CLIP_LINEAR = 0.7079  # 10**(-3/20) – linear peak threshold for banner
AUTO_GAIN_TARGET        = 80    # % – default target when signal is weak
AUTO_GAIN_BOOST         = 100   # % – target when signal is very weak
AUTO_GAIN_STEP_DOWN     = 20    # % – step subtracted when clipping danger
AUTO_GAIN_MIN           = 20    # % – never reduce below this
AUTO_GAIN_COOLDOWN      = 6.0   # seconds between consecutive adjustments
AUTO_GAIN_WEAK_DB       = -35.0 # dBFS – signal considered weak below this
AUTO_GAIN_VERY_WEAK_DB  = -50.0 # dBFS – signal considered very weak below this
AUTO_GAIN_DANGER_DB     = -2.0  # dBFS – peak above this = clipping danger
AUTO_GAIN_WEAK_HOLD     = 4.0   # seconds of weak signal before raising gain
AUTO_GAIN_MSG_TTL       = 20.0  # seconds the status message stays highlighted red
GAIN_POLL_SECONDS       = 5.0   # how often to read current input gain
GAIN_HISTORY_SECONDS    = 600   # 10 min window for gain history graph
GAIN_HISTORY_MAX        = 256   # max samples kept in history deque
SONGREC_WINDOW_SECONDS = 5
SONGREC_INTERVAL_SECONDS = 15
# Unique per-process to avoid collisions when two instances share an output dir.
SONGREC_TEMP_SNIPPET = f".songrec_snippet_{os.getpid()}.wav"
PHOTO_FIRST_DELAY_SECONDS = 300  # 5 minutes before first webcam snapshot
PHOTO_INTERVAL_SECONDS = 900    # 15 minutes between subsequent snapshots

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
BLINK = "\033[5m"
CLEAR = "\033[2J\033[H"
AMBER = "\033[38;5;214m"
GREEN = "\033[38;5;46m"
YELLOW = "\033[38;5;226m"
RED = "\033[38;5;196m"
RED_BRIGHT = "\033[38;5;203m"  # brighter / lighter red for 80% row
GREY = "\033[38;5;240m"
BLUE      = "\033[38;5;39m"
BG_AMBER  = "\033[48;5;214m"   # amber background (for key bar)
BG_WHITE  = "\033[107m"        # bright-white background (for highlighted keys)
BG_RED    = "\033[48;5;196m"   # red background (for clipping banner)
FG_BLACK  = "\033[30m"         # black foreground (readable on both amber/white bg)
FG_WHITE  = "\033[97m"         # bright-white foreground


class KeyReader:
    def __enter__(self):
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        new = termios.tcgetattr(self.fd)
        # Disable canonical mode and echo; keep OPOST so \n → \r\n still works.
        # TCSAFLUSH flushes any buffered Enter presses from the setup prompts.
        new[3] &= ~(termios.ICANON | termios.ECHO | termios.IEXTEN)
        new[6][termios.VMIN] = 0   # non-blocking read
        new[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, new)
        return self

    def __exit__(self, exc_type, exc, tb):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
        return False

    def get_key(self):
        ch = os.read(self.fd, 1)
        return ch.decode("utf-8", errors="ignore").lower() if ch else None


def print_help_messages() -> None:
    print("""
English Help
============

What this script does
- Lists all available audio input devices and shows a short live level test.
- Suggests a user-specific output folder and asks for confirmation.
- Runs a 5-second preview before recording starts.
- Records fixed 60-minute segments and saves them as .m4a files.
- Shows a stereo VU meter, clipping warning, song recognition status, and current song in the CLI.
- Continuously updates a status file called current_song.txt in the selected output folder.

Controls
- S : Stop the current segment, save it, and switch to PAUSE
- R : Restart recording with a new segment
- Q : Save the current segment and quit
- Ctrl+C : Emergency stop (the current segment is still saved when possible)

Output files
- Recording segments:
    YYYYMMDD-startHHMM-endHHMM.m4a
- Continuously updated song status:
    current_song.txt

Song recognition
- Uses ShazamIO in the background on a rolling audio snippet.
- Requires an active internet connection.
- If ShazamIO is not installed, recording still works and song recognition is disabled.

Install dependencies
    python3 -m pip install sounddevice soundfile numpy shazamio

Run
    python3 audio_recorder_macos_m4a_stereo_songrec_en_help.py

Show help
    python3 audio_recorder_macos_m4a_stereo_songrec_en_help.py --help
    python3 audio_recorder_macos_m4a_stereo_songrec_en_help.py --help-messages

Notes
- This script is intended for macOS and requires the built-in 'afconvert' tool.
- For raw keyboard handling, use a real macOS terminal rather than some IDE consoles.
""")


def clear_screen():
    sys.stdout.write(CLEAR)
    sys.stdout.flush()


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def linear_to_dbfs(v: float) -> float:
    if v <= 1e-12:
        return -120.0
    return 20.0 * math.log10(v)


def classify_level(rms: float) -> str:
    db = linear_to_dbfs(rms)
    if db < -35:
        return "Level low – increase input/gain"
    if db > -6:
        return "Level high – reduce input/gain"
    return "Level ok"


def bar_color(index: int, width: int) -> str:
    # Map cell index back to dBFS (meter spans -40..0 dBFS, right edge of cell).
    db = -40.0 + (index + 1) * (40.0 / max(1, width))
    if db < -18.0:
        return GREEN        # ok / safe
    if db < -6.0:
        return AMBER        # on spot / nominal
    if db < -3.0:
        return RED_BRIGHT   # clipping danger
    return RED              # clipping


def colored_meter(dbfs: float, peak_hold_db: float, width: int = METER_WIDTH) -> str:
    db = max(-40.0, min(0.0, dbfs))
    frac = (db + 40.0) / 40.0
    filled = int(round(frac * width))
    peak_frac = (max(-40.0, min(0.0, peak_hold_db)) + 40.0) / 40.0
    peak_pos = max(0, min(width - 1, int(round(peak_frac * (width - 1)))))
    out = []
    for i in range(width):
        if i == peak_pos:
            out.append(RED + "│")
        elif i < filled:
            out.append(bar_color(i, width) + "●")
        else:
            out.append(GREY + "·")
    return "".join(out) + RESET


def _set_input_gain(pct: int) -> None:
    """Set macOS system microphone input gain via osascript (0–100)."""
    # Clamp and force int to prevent injection if caller ever passes a non-int.
    safe_pct = max(0, min(100, int(pct)))
    try:
        subprocess.run(
            ["osascript", "-e", f"set volume input volume {safe_pct}"],
            check=False, capture_output=True, timeout=1.0
        )
    except Exception:
        pass


def _get_input_gain() -> Optional[int]:
    """Read current macOS system microphone input gain (0–100), or None on error."""
    try:
        r = subprocess.run(
            ["osascript", "-e", "input volume of (get volume settings)"],
            capture_output=True, text=True, timeout=1.0
        )
        return int(r.stdout.strip())
    except Exception:
        return None


def _probe_gain_control() -> bool:
    """Detect whether the currently selected macOS input device actually responds
    to the system input-gain slider. Some class-compliant USB interfaces
    (e.g. Behringer UCA202) have a fixed line-level input with no software
    gain control — every set call is a no-op. We probe by writing two distinct
    values and checking whether the read-back follows; the original value is
    restored afterwards.

    Returns True only if both probes were reflected by the OS, False otherwise.
    """
    orig = _get_input_gain()
    if orig is None:
        return False
    # Pick two probe values that are distinct from `orig` and from each other.
    candidates = [v for v in (40, 70) if abs(v - orig) >= 10]
    if len(candidates) < 2:
        candidates = [30, 80]
    ok = True
    for v in candidates:
        _set_input_gain(v)
        time.sleep(0.12)  # give CoreAudio a moment to apply
        read_back = _get_input_gain()
        if read_back is None or abs(read_back - v) > 2:
            ok = False
            break
    # Restore original setting regardless of result.
    _set_input_gain(orig)
    return ok


def require_macos_tools():
    if sys.platform != "darwin":
        raise RuntimeError("This version is intended for macOS (sys.platform != 'darwin').")
    if shutil.which("afconvert") is None:
        raise RuntimeError("'afconvert' was not found. Please run this in a macOS terminal.")


_LASTDIR_FILE = Path.home() / ".simplerec_lastdir"

def default_output_dir() -> Path:
    try:
        saved = _LASTDIR_FILE.read_text(encoding="utf-8").strip()
        if saved:
            return Path(saved)
    except OSError:
        pass
    return Path.home() / "simplerec - recordings"

def _save_last_output_dir(path: Path) -> None:
    try:
        _LASTDIR_FILE.write_text(str(path), encoding="utf-8")
    except OSError:
        pass


DEFAULT_DURATION_MINUTES = 60

def ask_recording_minutes() -> int:
    print("\nRecording Duration")
    print("------------------")
    while True:
        raw = input(f"How many minutes to record? (1–120) [default: {DEFAULT_DURATION_MINUTES}]: ").strip()
        if raw == "":
            return DEFAULT_DURATION_MINUTES
        if raw.isdigit():
            val = int(raw)
            if 1 <= val <= 120:
                return val
        print("Please enter a number between 1 and 120.")


def ask_filename_prefix() -> str:
    print("\nFilename Prefix")
    print("---------------")
    print("This prefix will be added to all recording files and the song-log.")
    print("Leave empty to use no prefix.")
    raw = input("Prefix (e.g. 'Party2026_'): ").strip()
    # sanitise: keep only safe characters for filenames
    safe = "".join(c for c in raw if c.isalnum() or c in "_-. ")
    safe = safe.replace(" ", "_")
    return safe


def ask_dj_photos() -> bool:
    print("\nDJ Webcam Photos")
    print("----------------")
    print("simplerec can take a photo every 15 minutes using your webcam.")
    print("This helps you match DJ sets to the recorded playlists later.")
    print("Photos are saved as .jpg files in the same output folder.")
    print("Note: the green camera LED will light up briefly for each shot.")
    if shutil.which("imagesnap") is None:
        print("Note: 'imagesnap' is not installed — photos will be skipped.")
        print("      Install with: brew install imagesnap")
        return False
    raw = input("Take webcam photos every 15 min? [Y/n]: ").strip().lower()
    return raw in ("", "y", "yes")


def choose_output_dir() -> Path:
    suggested = default_output_dir().expanduser()
    print("\nOutput Folder")
    print("-------------")
    print(f"Suggested output folder: {suggested}")
    print("[Enter]/Y = confirm, N = enter a different folder, Q = quit")
    while True:
        choice = input("Use this folder? ").strip().lower()
        if choice in ("", "y", "yes"):
            outdir = suggested
            break
        if choice in ("n", "no"):
            custom = input("Please enter the target folder: ").strip()
            if not custom:
                print("Empty input – please enter the path again or press [Enter] to confirm.")
                continue
            outdir = Path(custom).expanduser()
            break
        if choice in ("q", "quit"):
            raise SystemExit("Aborted by user.")
        print("Please enter [Enter]/Y, N, or Q.")
    outdir = outdir.resolve()
    if not outdir.exists():
        print(f"Folder does not exist yet: {outdir}")
        create = input("Create it? [Y/n] ").strip().lower()
        if create not in ("", "y", "yes"):
            raise SystemExit("Aborted – target folder was not created.")
        outdir.mkdir(parents=True, exist_ok=True)
    _save_last_output_dir(outdir)
    return outdir


def temp_wav_name(output_dir: Path, start_wall: dt.datetime) -> Path:
    return output_dir / f".{start_wall:%Y%m%d}-start{start_wall:%H%M%S}.part.wav"


def probe_input_level(device_index: int, samplerate: int, channels: int, seconds: float) -> tuple[float | None, float | None]:
    values = []
    done = threading.Event()
    def cb(indata, frames, time_info, status):
        if indata.size:
            arr = indata if indata.ndim == 2 else indata.reshape(-1, 1)
            rms = np.sqrt(np.mean(np.square(arr, dtype=np.float64), axis=0))
            values.append(rms)
        if len(values) >= max(1, int(seconds * samplerate / max(frames, 1))):
            done.set()
            raise sd.CallbackStop
    try:
        with sd.InputStream(device=device_index, channels=channels, samplerate=samplerate, dtype=DTYPE, blocksize=BLOCKSIZE, callback=cb):
            t0 = time.time()
            while not done.is_set() and time.time() - t0 < seconds + 0.4:
                time.sleep(0.01)
        if values:
            data = np.vstack(values)
            max_rms = np.max(data, axis=0)
            left_db = linear_to_dbfs(float(max_rms[0]))
            right_db = linear_to_dbfs(float(max_rms[1])) if len(max_rms) > 1 else None
            return left_db, right_db
    except Exception:
        return None, None
    return None, None  # values was empty


def _extract_track_meta(track: dict) -> dict:
    """Pull genre, album and release year from a Shazam track dict."""
    genre = ""
    album = ""
    year  = ""
    genres = track.get("genres")
    if isinstance(genres, dict):
        genre = genres.get("primary") or ""
    for section in track.get("sections", []):
        if isinstance(section, dict) and section.get("type") == "SONG":
            for meta in section.get("metadata", []):
                if isinstance(meta, dict):
                    key = (meta.get("title") or "").lower()
                    val =  meta.get("text")  or ""
                    if key == "album":
                        album = val
                    elif key == "released":
                        year = val[:4]  # first four chars = year
            break
    return {"genre": genre, "album": album, "year": year}


@dataclass
class RecorderState:
    device_index: int
    samplerate: int
    channels: int
    output_dir: Path
    lock: threading.Lock = field(default_factory=threading.Lock)
    mode: str = "preview"
    latest_rms_lr: tuple[float, float] = (0.0, 0.0)
    latest_peak_lr: tuple[float, float] = (0.0, 0.0)
    preview_peak_lr: tuple[float, float] = (0.0, 0.0)
    peak_hold_db_lr: tuple[float, float] = (-120.0, -120.0)
    peak_hold_until_lr: tuple[float, float] = (0.0, 0.0)
    clip_hold_until: float = 0.0
    clip_count: int = 0
    simulate_clip_until: float = 0.0  # [T] key – force clipping banner for a few seconds (test)
    gain_last_adjust: float = 0.0
    gain_weak_since: Optional[float] = None
    gain_last_action: str = ""  # for UI display
    gain_last_action_at: float = 0.0  # monotonic time of last action message
    auto_gain_enabled: bool = True  # toggle via [A]
    gain_control_supported: bool = True  # False for devices like UCA202 (fixed line-in)
    gain_history: deque = field(default_factory=lambda: deque(maxlen=GAIN_HISTORY_MAX))
    gain_current_pct: Optional[int] = None
    gain_last_poll: float = 0.0
    stream: Optional[sd.InputStream] = None
    writer_q: queue.Queue = field(default_factory=queue.Queue)
    writer_stop: threading.Event = field(default_factory=threading.Event)
    writer_thread: Optional[threading.Thread] = None
    convert_q: queue.Queue = field(default_factory=queue.Queue)
    convert_stop: threading.Event = field(default_factory=threading.Event)
    convert_thread: Optional[threading.Thread] = None
    current_file: Optional[sf.SoundFile] = None
    current_temp_name: Optional[Path] = None
    segment_start_wall: Optional[dt.datetime] = None
    segment_start_monotonic: Optional[float] = None
    songrec_enabled: bool = SONGREC_AVAILABLE
    songrec_stop: threading.Event = field(default_factory=threading.Event)
    songrec_thread: Optional[threading.Thread] = None
    photo_enabled: bool = False
    photo_stop: threading.Event = field(default_factory=threading.Event)
    photo_thread: Optional[threading.Thread] = None
    photo_countdown: Optional[int] = None
    recent_blocks: deque = field(default_factory=deque)
    songrec_last_check: Optional[dt.datetime] = None
    songrec_last_match: Optional[dt.datetime] = None
    songrec_current_title: str = "-"
    songrec_current_artist: str = "-"
    songrec_current_genre: str = ""
    songrec_current_album: str = ""
    songrec_current_year: str = ""
    songrec_status: str = "not started"
    songrec_total_checks: int = 0
    songrec_next_check_at: float = 0.0
    songrec_last_tagid: str = ""
    playlist_path: Optional[Path] = None
    playlist_last_tagid: str = ""
    playlist_last_key: tuple = ("", "")
    playlist_last_was_empty: bool = False
    filename_prefix: str = ""
    max_record_seconds: Optional[float] = None
    playlist_only: bool = False
    session_start_monotonic: Optional[float] = None
    session_start_wall: Optional[dt.datetime] = None
    segment_dir: Optional[Path] = None

    def __post_init__(self):
        blocks = int(math.ceil((SONGREC_WINDOW_SECONDS * self.samplerate) / BLOCKSIZE)) + 2
        self.recent_blocks = deque(maxlen=max(4, blocks))

    def _song_status_fname(self) -> str:
        ts = self.session_start_wall.strftime("%Y%m%d-%H%M") if self.session_start_wall else "session"
        base = f"current_song_{ts}.txt"
        return (self.filename_prefix + base) if self.filename_prefix else base

    def write_song_status_file(self):
        with self.lock:
            fname = self._song_status_fname()  # reads session_start_wall – must be inside lock
            last_check = self.songrec_last_check.strftime("%Y-%m-%d %H:%M:%S") if self.songrec_last_check else "-"
            last_match = self.songrec_last_match.strftime("%Y-%m-%d %H:%M:%S") if self.songrec_last_match else "-"
            content = (
                f"Last check   : {last_check}\n"
                f"Last match   : {last_match}\n"
                f"Current song : {self.songrec_current_artist} - {self.songrec_current_title}\n"
                f"Genre        : {self.songrec_current_genre}\n"
                f"Album        : {self.songrec_current_album}\n"
                f"Year         : {self.songrec_current_year}\n"
                f"Status       : {self.songrec_status}\n"
                f"Checks       : {self.songrec_total_checks}\n"
            )
        try:
            base_dir = self.segment_dir if self.segment_dir else self.output_dir
            (base_dir / fname).write_text(content, encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _norm_key(artist: Optional[str], title: Optional[str]) -> tuple:
        return ((artist or "").strip().casefold(), (title or "").strip().casefold())

    @staticmethod
    def _last_song_key_from_file(path: Path) -> Optional[tuple]:
        """Read the file tail and return the normalized (artist, title) of the
        last actual song row (ignoring CLIP-ADJUST and no-match lines). None on
        error / no song row found."""
        try:
            if not path.exists():
                return None
            with open(path, "rb") as f:
                try:
                    f.seek(-4096, 2)
                except OSError:
                    f.seek(0)
                tail = f.read().decode("utf-8", errors="ignore")
        except OSError:
            return None
        for ln in reversed(tail.splitlines()):
            ln = ln.strip()
            if not ln:
                continue
            parts = ln.split(";")
            if len(parts) < 4:
                continue
            # CLIP-ADJUST rows have "CLIP-ADJUST" in the artist column
            if parts[2].strip() == "CLIP-ADJUST":
                continue
            artist = parts[2]
            title = parts[3]
            if not (artist or title):
                continue
            return (artist.strip().casefold(), title.strip().casefold())
        return None

    def append_to_playlist(self, title, artist, tagid: str, check_time: dt.datetime,
                            elapsed: float, genre: str = "", year: str = ""):
        """Append one entry to the segment playlist .txt file, skipping duplicates."""
        line: Optional[str] = None
        with self.lock:
            path = self.playlist_path
            if path is None:
                return
            if title and artist:
                new_key = self._norm_key(artist, title)
                # In-memory dedup: tagid or normalized artist/title match
                same_tag = bool(tagid) and tagid == self.playlist_last_tagid
                same_key = new_key == self.playlist_last_key and new_key != ("", "")
                if same_tag or same_key:
                    if tagid and not self.playlist_last_tagid:
                        self.playlist_last_tagid = tagid
                    self.playlist_last_key = new_key
                    return
                line = f"{check_time.strftime('%H:%M:%S')};{human_duration(elapsed)};{artist};{title};{genre};{year}\n"
                self.playlist_last_tagid = tagid
                self.playlist_last_key = new_key
                self.playlist_last_was_empty = False
            else:
                if self.playlist_last_was_empty:
                    return  # consecutive no-match – skip
                line = f"{check_time.strftime('%H:%M:%S')};{human_duration(elapsed)};;;;\n"
                self.playlist_last_was_empty = True
        if line is None:
            return
        # Second-level guard: compare against the LAST actual song row in the
        # file. This catches any in-memory dedup state loss / races.
        if title and artist:
            file_key = self._last_song_key_from_file(path)
            new_key = self._norm_key(artist, title)
            if file_key is not None and file_key == new_key:
                with self.lock:
                    self.playlist_last_key = new_key
                return
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    async def _recognize_file(self, path: Path):
        if not self.songrec_enabled or Shazam is None:
            return None, None, "ShazamIO not installed", "", {}
        try:
            shazam = Shazam()
            result = await shazam.recognize(str(path))
            tagid = result.get("tagid", "") if isinstance(result, dict) else ""
            track = result.get("track") if isinstance(result, dict) else None
            if track:
                meta = _extract_track_meta(track)
                return (track.get("title") or "-", track.get("subtitle") or "-", "Match", tagid, meta)
            return None, None, "No match", tagid, {}
        except Exception as exc:
            return None, None, f"Error: {type(exc).__name__}", "", {}

    def start_songrec(self):
        if not self.songrec_enabled:
            with self.lock:
                self.songrec_status = "disabled (shazamio missing)"
            self.write_song_status_file()
            return
        self.songrec_stop.clear()
        def _runner():
            snippet_path = self.output_dir / SONGREC_TEMP_SNIPPET
            while not self.songrec_stop.is_set():
                next_at = time.monotonic() + SONGREC_INTERVAL_SECONDS
                with self.lock:
                    self.songrec_next_check_at = next_at
                # sleep in small steps so the countdown stays accurate
                while time.monotonic() < next_at:
                    if self.songrec_stop.is_set():
                        return
                    time.sleep(0.5)
                if self.songrec_stop.is_set():
                    break
                with self.lock:
                    blocks = list(self.recent_blocks)
                    mode = self.mode
                if mode == "quitting" or not blocks:
                    continue
                try:
                    audio = np.concatenate(blocks, axis=0)
                    max_frames = int(SONGREC_WINDOW_SECONDS * self.samplerate)
                    if len(audio) > max_frames:
                        audio = audio[-max_frames:]
                    if audio.size == 0:
                        continue
                    sf.write(str(snippet_path), audio, self.samplerate, subtype="PCM_16")
                    now = dt.datetime.now()
                    with self.lock:
                        seg_start_mono = self.segment_start_monotonic
                    elapsed_at_check = max(0.0, time.monotonic() - seg_start_mono) if seg_start_mono is not None else 0.0
                    title, artist, status, tagid, meta = asyncio.run(self._recognize_file(snippet_path))
                    with self.lock:
                        self.songrec_last_check = now
                        self.songrec_total_checks += 1
                        self.songrec_status = status
                        self.songrec_last_tagid = tagid or ""
                        if title and artist:
                            self.songrec_current_title = title
                            self.songrec_current_artist = artist
                            self.songrec_current_genre = meta.get("genre", "")
                            self.songrec_current_album = meta.get("album", "")
                            self.songrec_current_year  = meta.get("year", "")
                            self.songrec_last_match = now
                        else:
                            self.songrec_current_title = "-"
                            self.songrec_current_artist = "-"
                            self.songrec_current_genre = ""
                            self.songrec_current_album = ""
                            self.songrec_current_year  = ""
                    self.write_song_status_file()
                    self.append_to_playlist(title, artist, tagid or "", now, elapsed_at_check,
                                            meta.get("genre", ""), meta.get("year", ""))
                except Exception as exc:
                    with self.lock:
                        self.songrec_last_check = dt.datetime.now()
                        self.songrec_total_checks += 1
                        self.songrec_status = f"Error: {type(exc).__name__}"
                    try:
                        self.write_song_status_file()
                    except Exception:
                        pass
                finally:
                    try:
                        if snippet_path.exists():
                            snippet_path.unlink()
                    except OSError:
                        pass
        self.songrec_thread = threading.Thread(target=_runner, daemon=True)
        self.songrec_thread.start()
        with self.lock:
            self.songrec_status = f"active – every {SONGREC_INTERVAL_SECONDS}s"
        self.write_song_status_file()

    def stop_songrec(self):
        self.songrec_stop.set()
        if self.songrec_thread is not None:
            self.songrec_thread.join(timeout=3)
            self.songrec_thread = None

    def start_photo(self) -> None:
        """Start background thread that takes a webcam photo every PHOTO_INTERVAL_SECONDS."""
        if not self.photo_enabled:
            return
        if shutil.which("imagesnap") is None:
            return  # imagesnap not installed – silently skip
        self.photo_stop.clear()
        def _runner():
            next_photo = time.monotonic() + PHOTO_FIRST_DELAY_SECONDS
            while not self.photo_stop.is_set():
                secs_until = next_photo - time.monotonic()
                if secs_until <= 0:
                    with self.lock:
                        self.photo_countdown = None
                        out_dir = self.output_dir
                        prefix  = self.filename_prefix or ""
                    if out_dir is not None:
                        ts   = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                        save_dir = self.segment_dir if self.segment_dir else out_dir
                        dest = save_dir / f"{prefix}photo_{ts}.jpg"
                        try:
                            subprocess.run(
                                ["imagesnap", "-w", "1", str(dest)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                timeout=15,
                            )
                        except Exception:
                            pass
                    next_photo = time.monotonic() + PHOTO_INTERVAL_SECONDS
                elif secs_until <= 5.0:
                    with self.lock:
                        self.photo_countdown = max(1, math.ceil(secs_until))
                else:
                    with self.lock:
                        self.photo_countdown = None
                self.photo_stop.wait(0.4)
            with self.lock:
                self.photo_countdown = None
        self.photo_thread = threading.Thread(target=_runner, daemon=True)
        self.photo_thread.start()

    def stop_photo(self) -> None:
        self.photo_stop.set()
        if self.photo_thread is not None:
            self.photo_thread.join(timeout=5)
            self.photo_thread = None

    def audio_callback(self, indata, frames, time_info, status):
        arr = indata if indata.ndim == 2 else indata.reshape(-1, 1)
        rms = np.sqrt(np.mean(np.square(arr, dtype=np.float64), axis=0)) if arr.size else np.zeros((self.channels,), dtype=np.float64)
        peak = np.max(np.abs(arr), axis=0) if arr.size else np.zeros((self.channels,), dtype=np.float64)
        if len(rms) == 1:
            rms_lr = (float(rms[0]), float(rms[0]))
            peak_lr = (float(peak[0]), float(peak[0]))
        else:
            rms_lr = (float(rms[0]), float(rms[1]))
            peak_lr = (float(peak[0]), float(peak[1]))
        now = time.monotonic()
        db_lr = (linear_to_dbfs(rms_lr[0]), linear_to_dbfs(rms_lr[1]))
        # Make copies OUTSIDE the lock to minimise lock-hold time in the audio thread.
        arr_copy = arr.copy()
        with self.lock:
            self.latest_rms_lr = rms_lr
            self.latest_peak_lr = peak_lr
            self.recent_blocks.append(arr_copy)
            hold_db = list(self.peak_hold_db_lr)
            hold_until = list(self.peak_hold_until_lr)
            for i in range(2):
                if db_lr[i] >= hold_db[i] or now >= hold_until[i]:
                    hold_db[i] = db_lr[i]
                    hold_until[i] = now + PEAK_HOLD_SECONDS
            self.peak_hold_db_lr = (hold_db[0], hold_db[1])
            self.peak_hold_until_lr = (hold_until[0], hold_until[1])
            if self.mode == "preview":
                self.preview_peak_lr = (max(self.preview_peak_lr[0], rms_lr[0]), max(self.preview_peak_lr[1], rms_lr[1]))
            if max(peak_lr) >= CLIP_THRESHOLD:
                self.clip_hold_until = now + CLIP_HOLD_SECONDS
                self.clip_count += 1
            need_write = self.mode == "recording" and self.current_file is not None
        if need_write:
            self.writer_q.put(indata.copy())

    def poll_gain(self) -> None:
        """Read current macOS input gain every GAIN_POLL_SECONDS and append to history."""
        now = time.monotonic()
        if now - self.gain_last_poll < GAIN_POLL_SECONDS:
            return
        self.gain_last_poll = now
        pct = _get_input_gain()
        if pct is None:
            return
        self.gain_current_pct = pct
        with self.lock:
            self.gain_history.append((now, pct))

    def _apply_gain(self, now: float, new_pct: int, msg: str) -> None:
        _set_input_gain(new_pct)
        self.gain_last_adjust = now
        self.gain_weak_since = None
        self.gain_current_pct = new_pct
        with self.lock:
            self.gain_last_action = msg
            self.gain_last_action_at = now
            self.gain_history.append((now, new_pct))

    def append_gain_event_to_playlist(self, msg: str) -> None:
        """Append a timestamped clip/adjust event line to the playlist file."""
        with self.lock:
            path = self.playlist_path
            seg_start = self.segment_start_monotonic
        if path is None:
            return
        now_wall = dt.datetime.now()
        elapsed = max(0.0, time.monotonic() - seg_start) if seg_start is not None else 0.0
        line = f"{now_wall.strftime('%H:%M:%S')};{human_duration(elapsed)};CLIP-ADJUST;{msg};;\n"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def manual_set_gain(self, pct: int) -> None:
        """Manually set input gain (when autogain is disabled)."""
        if not self.gain_control_supported:
            return
        now = time.monotonic()
        msg = f"manual → {pct}%"
        self._apply_gain(now, pct, msg)
        self.append_gain_event_to_playlist(msg)

    def _reset_gain_for_new_segment(self) -> None:
        """Reset input gain to AUTO_GAIN_TARGET (80%) on every (re)start of a
        segment so monitoring begins from a known baseline. Also clears the
        weak-signal timer and the autogain cooldown so the algorithm can react
        immediately to the new segment's signal level.

        On devices that don't honour software gain (e.g. UCA202) the OS call
        is skipped and a marker line is written to the playlist instead."""
        now = time.monotonic()
        if self.gain_control_supported:
            _set_input_gain(AUTO_GAIN_TARGET)
            self.gain_current_pct = AUTO_GAIN_TARGET
            msg = f"reset → {AUTO_GAIN_TARGET}% (segment start)"
        else:
            msg = "reset skipped (device has no software gain)"
        self.gain_weak_since = None
        self.gain_last_adjust = 0.0
        with self.lock:
            self.gain_last_action = msg
            self.gain_last_action_at = now
            if self.gain_control_supported:
                self.gain_history.append((now, AUTO_GAIN_TARGET))
        self.append_gain_event_to_playlist(msg)

    def check_auto_gain(self) -> None:
        """Raise or lower macOS input gain depending on signal level."""
        if not self.auto_gain_enabled or not self.gain_control_supported:
            return
        now = time.monotonic()
        if now - self.gain_last_adjust < AUTO_GAIN_COOLDOWN:
            return
        with self.lock:
            mode = self.mode
            peak_l, peak_r = self.latest_peak_lr
            rms_l, rms_r = self.latest_rms_lr
            clip_now = now < self.clip_hold_until
        if mode not in ("recording", "playlist"):
            return
        peak_db = linear_to_dbfs(max(peak_l, peak_r))
        rms_db  = linear_to_dbfs(max(rms_l, rms_r))
        cur = self.gain_current_pct if self.gain_current_pct is not None else _get_input_gain()
        # Clipping danger → step down (use instantaneous peak)
        if clip_now or peak_db >= AUTO_GAIN_DANGER_DB:
            base = cur if cur is not None else AUTO_GAIN_TARGET
            new_pct = max(AUTO_GAIN_MIN, base - AUTO_GAIN_STEP_DOWN)
            if cur is None or new_pct < cur:
                msg = f"↓ clipping danger → reduced to {new_pct}%"
                self._apply_gain(now, new_pct, msg)
                self.append_gain_event_to_playlist(msg)
            return
        # Weak signal handling (use RMS – less sensitive to brief transients)
        if rms_db < AUTO_GAIN_VERY_WEAK_DB:
            if self.gain_weak_since is None:
                self.gain_weak_since = now
            elif now - self.gain_weak_since >= AUTO_GAIN_WEAK_HOLD:
                if cur is None or cur < AUTO_GAIN_BOOST:
                    msg = f"↑ very weak → set to {AUTO_GAIN_BOOST}%"
                    self._apply_gain(now, AUTO_GAIN_BOOST, msg)
                    self.append_gain_event_to_playlist(msg)
                else:
                    self.gain_weak_since = None
        elif rms_db < AUTO_GAIN_WEAK_DB:
            if self.gain_weak_since is None:
                self.gain_weak_since = now
            elif now - self.gain_weak_since >= AUTO_GAIN_WEAK_HOLD:
                if cur is None or cur < AUTO_GAIN_TARGET:
                    msg = f"↑ too weak → set to {AUTO_GAIN_TARGET}%"
                    self._apply_gain(now, AUTO_GAIN_TARGET, msg)
                    self.append_gain_event_to_playlist(msg)
                elif cur < AUTO_GAIN_BOOST:
                    # Already at target but still weak → boost to max
                    msg = f"↑ still weak at {cur}% → set to {AUTO_GAIN_BOOST}%"
                    self._apply_gain(now, AUTO_GAIN_BOOST, msg)
                    self.append_gain_event_to_playlist(msg)
                else:
                    self.gain_weak_since = None
        else:
            self.gain_weak_since = None

    def start_stream(self):
        self.stream = sd.InputStream(device=self.device_index, channels=self.channels, samplerate=self.samplerate, dtype=DTYPE, blocksize=BLOCKSIZE, callback=self.audio_callback)
        self.stream.start()

    def stop_stream(self):
        if self.stream is not None:
            try: self.stream.stop()
            except Exception: pass
            try: self.stream.close()
            except Exception: pass
            self.stream = None

    def start_writer(self):
        self.writer_stop.clear()
        def _writer():
            while not self.writer_stop.is_set() or not self.writer_q.empty():
                try:
                    block = self.writer_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                with self.lock:
                    f = self.current_file
                if f is not None:
                    try: f.write(block)
                    except Exception as e: print(f"\nWrite error: {e}")
                self.writer_q.task_done()
        self.writer_thread = threading.Thread(target=_writer, daemon=True)
        self.writer_thread.start()

    def stop_writer(self):
        self.writer_stop.set()
        if self.writer_thread is not None:
            self.writer_thread.join(timeout=3)
            self.writer_thread = None

    def start_converter(self):
        self.convert_stop.clear()
        def _converter():
            while not self.convert_stop.is_set() or not self.convert_q.empty():
                try:
                    wav_path, final_m4a = self.convert_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    cmd = ["afconvert", "-f", "m4af", "-d", "aac", "-u", "vbrq", "127", str(wav_path), str(final_m4a)]
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    try: os.remove(wav_path)
                    except OSError: pass
                except subprocess.CalledProcessError:
                    fallback = Path(final_m4a).with_suffix(".wav")
                    try: os.replace(wav_path, fallback)
                    except OSError: pass
                    print(f"\nWarning: M4A conversion failed, keeping WAV: {fallback}")
                except Exception as exc:
                    print(f"\nWarning: Unexpected conversion error ({type(exc).__name__}): {exc}")
                finally:
                    self.convert_q.task_done()
        self.convert_thread = threading.Thread(target=_converter, daemon=True)
        self.convert_thread.start()

    def stop_converter(self):
        self.convert_stop.set()
        if self.convert_thread is not None:
            self.convert_thread.join(timeout=5)
            self.convert_thread = None

    def start_segment(self):
        # Prepare state fields under the lock, but open the file outside it
        # to avoid blocking the audio callback during disk I/O.
        with self.lock:
            if self.current_file is not None:
                return
            start_wall = dt.datetime.now()
            if self.session_start_monotonic is None:
                self.session_start_monotonic = time.monotonic()
                self.session_start_wall = start_wall
            self.segment_start_wall = start_wall
            self.segment_start_monotonic = time.monotonic()
            seg_dir = self.output_dir / f"{start_wall:%Y%m%d-%H%M}"
            self.segment_dir = seg_dir
            self.playlist_path = seg_dir / f"{self.filename_prefix}{start_wall:%Y%m%d}-start{start_wall:%H%M%S}.playlist.txt"
            self.playlist_last_tagid = ""
            self.playlist_last_key = ("", "")
            self.playlist_last_was_empty = False
        seg_dir.mkdir(parents=True, exist_ok=True)
        with self.lock:
            if self.playlist_only:
                self.mode = "playlist"
                # also reset gain for playlist-only restarts
                playlist_only_reset = True
            else:
                playlist_only_reset = False
                tmp_name = temp_wav_name(seg_dir, start_wall)
                self.current_temp_name = tmp_name
        if playlist_only_reset:
            self._reset_gain_for_new_segment()
            return
        try:
            new_file = sf.SoundFile(str(tmp_name), mode="w", samplerate=self.samplerate, channels=self.channels, subtype="PCM_16")
        except Exception:
            with self.lock:  # roll back on file-open failure
                self.current_temp_name = None
                self.segment_start_wall = None
                self.segment_start_monotonic = None
                self.playlist_path = None
                self.segment_dir = None
            raise
        with self.lock:
            self.current_file = new_file
            self.mode = "recording"
        # Reset gain to AUTO_GAIN_TARGET on every (re)start so each segment
        # begins from a known baseline, regardless of autogain state.
        self._reset_gain_for_new_segment()

    def _remove_song_status_file(self) -> None:
        """Delete the live current_song_*.txt status file (kept only while recording)."""
        try:
            with self.lock:
                fname = self._song_status_fname()
                base_dir = self.segment_dir or self.output_dir
            f = base_dir / fname
            if f.exists():
                f.unlink()
        except OSError:
            pass

    @staticmethod
    def _write_audacity_labels(playlist_path: Path) -> Optional[Path]:
        """Read a finalized playlist .txt and write an Audacity point-label track
        next to it ("<basename>.audacity.txt"). Returns the path written, or None.

        Audacity label-track format is tab-separated: ``start\tend\tlabel``.
        Point labels have start == end. Times are seconds from segment start
        (the 'elapsed' HH:MM:SS column in the playlist).
        """
        if playlist_path is None or not playlist_path.exists():
            return None
        try:
            text = playlist_path.read_text(encoding="utf-8")
        except OSError:
            return None
        labels: list[str] = []
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            parts = ln.split(";")
            if len(parts) < 4:
                continue
            elapsed = parts[1].strip()
            try:
                hh, mm, ss = elapsed.split(":")
                secs = int(hh) * 3600 + int(mm) * 60 + int(ss)
            except (ValueError, AttributeError):
                continue
            artist = parts[2].strip()
            title = parts[3].strip()
            if artist == "CLIP-ADJUST":
                label = f"CLIP-ADJUST: {title}" if title else "CLIP-ADJUST"
            elif artist and title:
                label = f"{artist} - {title}"
            elif artist or title:
                label = artist or title
            else:
                continue  # no-match row
            # Audacity reads label text verbatim until newline; strip tabs/newlines.
            label = label.replace("\t", " ").replace("\n", " ").replace("\r", " ")
            labels.append(f"{secs:.6f}\t{secs:.6f}\t{label}")
        out_path = playlist_path.with_suffix(".audacity.txt")
        try:
            # Always write the file (even if empty) so the folder is consistent.
            out_path.write_text(("\n".join(labels) + "\n") if labels else "", encoding="utf-8")
            return out_path
        except OSError:
            return None

    def stop_and_save(self) -> Optional[Path]:
        playlist_branch = False
        start_wall_pb: Optional[dt.datetime] = None
        end_wall_pb: Optional[dt.datetime] = None
        playlist_temp_pb: Optional[Path] = None
        with self.lock:
            if self.mode == "playlist":
                playlist_branch = True
                self.mode = "paused"
                self.segment_start_monotonic = None
                end_wall_pb = dt.datetime.now()
                start_wall_pb = self.segment_start_wall
                self.segment_start_wall = None
                playlist_temp_pb = self.playlist_path
                self.playlist_path = None
            elif self.current_file is None or self.segment_start_wall is None:
                self.mode = "paused"
                self.segment_start_monotonic = None
                return None
            else:
                self.mode = "paused"
                self.segment_start_monotonic = None
        if playlist_branch:
            if (playlist_temp_pb is not None and start_wall_pb is not None
                    and playlist_temp_pb.exists()):
                prefix = self.filename_prefix
                with self.lock:
                    seg_dir = self.segment_dir or self.output_dir
                playlist_final = seg_dir / (
                    f"{prefix}{start_wall_pb:%Y%m%d}"
                    f"-start{start_wall_pb:%H%M}-end{end_wall_pb:%H%M}.txt"
                )
                try:
                    playlist_temp_pb.rename(playlist_final)
                except OSError:
                    playlist_final = playlist_temp_pb
                self._write_audacity_labels(playlist_final)
            with self.lock:
                self.segment_dir = None
            self._remove_song_status_file()
            return None
        self.writer_q.join()
        with self.lock:
            end_wall = dt.datetime.now()
            tmp_name = self.current_temp_name
            start_wall = self.segment_start_wall
            if self.current_file is not None:
                try:
                    self.current_file.flush()
                    self.current_file.close()
                except Exception:
                    pass
            self.current_file = None
            self.current_temp_name = None
            self.segment_start_wall = None
        if tmp_name is None or start_wall is None:
            return None
        with self.lock:
            seg_dir = self.segment_dir or self.output_dir
            self.segment_dir = None
        prefix = self.filename_prefix
        final_name = seg_dir / f"{prefix}{start_wall:%Y%m%d}-start{start_wall:%H%M}-end{end_wall:%H%M}.m4a"
        if final_name.exists():
            final_name = final_name.with_name(final_name.stem + dt.datetime.now().strftime("-%H%M%S") + final_name.suffix)
        with self.lock:
            playlist_temp = self.playlist_path
            self.playlist_path = None
        if playlist_temp is not None and playlist_temp.exists():
            playlist_final = final_name.with_suffix(".txt")
            try:
                playlist_temp.rename(playlist_final)
            except OSError:
                playlist_final = playlist_temp
            self._write_audacity_labels(playlist_final)
        self.convert_q.put((tmp_name, final_name))
        self._remove_song_status_file()
        return final_name

    def elapsed_segment_seconds(self) -> float:
        with self.lock:
            if self.mode in ("recording", "playlist") and self.segment_start_monotonic is not None:
                return max(0.0, time.monotonic() - self.segment_start_monotonic)
        return 0.0


def select_input_device() -> tuple[int, int, int, str]:
    devices = sd.query_devices()
    input_devices = []
    print("\nInput devices  (brief level test – feed a signal now):\n")
    for idx, dev in enumerate(devices):
        max_input = int(dev.get("max_input_channels", 0))
        if max_input > 0:
            channels = 2 if max_input >= 2 else 1
            samplerate = int(dev.get("default_samplerate", SAMPLE_RATE_FALLBACK) or SAMPLE_RATE_FALLBACK)
            left_db, right_db = probe_input_level(idx, samplerate, channels, DEVICE_PROBE_SECONDS)
            input_devices.append((idx, samplerate, channels, dev["name"], left_db, right_db, max_input))
    if not input_devices:
        raise RuntimeError("No input devices found.")
    # columns (all visible chars, ANSI codes excluded from count):
    # [##](4) name(28) Stereo/Mono  (8) kHz(5) [bar8](10) L±xx.x R±xx.xdB(18) = 79
    NAME_W = 28
    BAR_W  = 8
    print(f"{'':4} {'Name':<28}  {'Ch':<6}  {'Rate':>5}  {'Signal':^10}  {'Level'}")
    print(f"{'':4} {'-'*28}  {'-'*6}  {'-'*5}  {'-'*10}  {'-'*17}")
    for i, (sd_idx, samplerate, channels, name, left_db, right_db, maxch) in enumerate(input_devices):
        ch_txt  = "Stereo" if channels >= 2 else "Mono  "
        khz_txt = f"{samplerate // 1000}kHz"
        name_t  = (name[:NAME_W - 1] + "…") if len(name) > NAME_W else name.ljust(NAME_W)
        if left_db is None:
            bar     = GREY + "·" * BAR_W + RESET
            lvl_txt = f"{GREY}n/a{RESET}"
        else:
            max_db  = max(left_db, right_db) if right_db is not None else left_db
            bar     = colored_meter(max_db, max_db, width=BAR_W)
            if right_db is None:
                lvl_txt = f"L{left_db:+6.1f}dB"
            else:
                lvl_txt = f"L{left_db:+6.1f} R{right_db:+6.1f}dB"
        print(f"[{i:02d}] {name_t}  {ch_txt}  {khz_txt:>5}  [{bar}] {lvl_txt}")
    while True:
        raw = input("\nDevice number: ").strip()
        if raw.isdigit() and 0 <= int(raw) < len(input_devices):
            sd_idx, samplerate, channels, name, _, _, _ = input_devices[int(raw)]
            if channels < 2:
                print("Note: mono fallback (device has no stereo).")
            return sd_idx, samplerate, channels, name
        print("Invalid – please choose one of the listed numbers.")


def _trunc(s: str, width: int = 80) -> str:
    """Truncate a plain-text string to `width` visible chars, adding … if needed."""
    s = str(s)
    if len(s) <= width:
        return s
    return s[:width - 1] + "…"


_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

def _visible_len(s: str) -> int:
    """Return the visible column-width of a string, ignoring ANSI escape codes."""
    return len(_ANSI_RE.sub('', s))

def _box_top(width: int = 80) -> str:
    return f"{AMBER}╔{'═' * (width - 2)}╗{RESET}"

def _box_bot(width: int = 80) -> str:
    return f"{AMBER}╚{'═' * (width - 2)}╝{RESET}"

def _box_row(content: str, width: int = 80) -> str:
    inner = width - 4   # ║ <inner> ║
    pad = max(0, inner - _visible_len(content))
    return f"{AMBER}║{RESET} {content}{' ' * pad} {AMBER}║{RESET}"


def _render_gain_grid(history, now: float, cols: int = 50, rows: int = 5) -> list[str]:
    """Render a `rows`x`cols` dot grid of the last GAIN_HISTORY_SECONDS of gain.

    Each row represents a 20%% gain bucket (top = 80-100%, bottom = 0-19%).
    Each column represents GAIN_HISTORY_SECONDS/cols seconds (12s for 10 min / 50).
    Active samples are drawn as a thick red dot, empty cells as grey dots.
    """
    total = GAIN_HISTORY_SECONDS
    slot = total / cols
    start_t = now - total
    samples = sorted(history, key=lambda e: e[0])
    si = 0
    n_samples = len(samples)
    values: list[Optional[int]] = []
    latest: Optional[int] = None
    for c in range(cols):
        t_end = start_t + (c + 1) * slot
        while si < n_samples and samples[si][0] <= t_end:
            latest = samples[si][1]
            si += 1
        values.append(latest)
    out: list[str] = []
    # Row 0 = 100% (top), Row 1 = 80%, Row 2 = 60%, Row 3 = 40%, Row 4 = 20%
    row_colors = [RED, RED_BRIGHT, YELLOW, GREEN, GREEN]
    for r in range(rows):
        color = row_colors[r]
        row_chars: list[str] = []
        for c in range(cols):
            v = values[c]
            if v is None:
                row_chars.append(f"{DIM}{color}·{RESET}")
                continue
            bucket = min(rows - 1, max(0, (max(0, v - 1) * rows) // 100))
            gain_row = rows - 1 - bucket  # row 0 = top = highest gain
            if r == gain_row:
                row_chars.append(f"{color}{BOLD}●{RESET}")
            else:
                row_chars.append(f"{DIM}{color}·{RESET}")
        out.append("".join(row_chars))
    return out


def render_ui(state: RecorderState, device_name: str, preview_end: Optional[float]):
    with state.lock:
        mode = state.mode
        rms_l, rms_r = state.latest_rms_lr
        peak_l, peak_r = state.latest_peak_lr
        prev_l, prev_r = state.preview_peak_lr
        hold_l, hold_r = state.peak_hold_db_lr
        start_wall = state.segment_start_wall
        pending_conversions = state.convert_q.qsize()
        clip_active = time.monotonic() < state.clip_hold_until
        clip_count = state.clip_count
        gain_action = state.gain_last_action
        gain_action_at = state.gain_last_action_at
        gain_history = list(state.gain_history)
        gain_current = state.gain_current_pct
        auto_gain_on = state.auto_gain_enabled
        outdir = state.output_dir
        seg_dir = state.segment_dir
        channels = state.channels
        song_title = state.songrec_current_title
        song_artist = state.songrec_current_artist
        song_last_check = state.songrec_last_check.strftime("%H:%M:%S") if state.songrec_last_check else "-"
        song_last_match = state.songrec_last_match.strftime("%H:%M:%S") if state.songrec_last_match else "-"
        song_next_at = state.songrec_next_check_at
        song_tagid = state.songrec_last_tagid
        song_genre = state.songrec_current_genre
        song_album = state.songrec_current_album
        song_year  = state.songrec_current_year
        playlist_only = state.playlist_only
        max_record_seconds = state.max_record_seconds
        session_start_mono = state.session_start_monotonic
        photo_enabled  = state.photo_enabled
        photo_countdown = state.photo_countdown
        gain_supported = state.gain_control_supported
        simulate_clip_active = time.monotonic() < state.simulate_clip_until
    elapsed = state.elapsed_segment_seconds() if mode in ("recording", "playlist") else 0.0
    db_l = linear_to_dbfs(rms_l)
    db_r = linear_to_dbfs(rms_r)
    ch_label = "Stereo" if channels >= 2 else "Mono"
    sys_txt = ""
    if _psutil is not None:
        # Cache CPU/RAM readings for ~1s so the UI (12 fps) doesn't poll psutil
        # on every frame; cpu_percent() without interval also gets noisy that fast.
        now_mono = time.monotonic()
        cache = getattr(render_ui, "_sys_cache", None)
        if cache is None or now_mono - cache[0] > 1.0:
            cpu_pct = _psutil.cpu_percent()
            ram_pct = _psutil.virtual_memory().percent
            render_ui._sys_cache = (now_mono, cpu_pct, ram_pct)
        else:
            _, cpu_pct, ram_pct = cache
        sys_txt = f"    CPU:{cpu_pct:5.1f}%  RAM:{ram_pct:5.1f}%"
    # remaining time countdown (red)
    if max_record_seconds is not None and session_start_mono is not None:
        secs_left = max(0.0, max_record_seconds - (time.monotonic() - session_start_mono))
        remaining_txt = f"  {RED}{BOLD}{human_duration(secs_left)} left{RESET}{AMBER}"
    else:
        remaining_txt = ""
    if mode == "recording":
        status_label = f"{RED}{BOLD}● REC{RESET}"
    elif mode == "playlist":
        status_label = f"{AMBER}{BOLD}♪ PLAYLIST{RESET}"
    else:
        status_label = f"{AMBER}{BOLD}‖ PAUSE{RESET}"
    W = 80
    clear_screen()

    # ── Logo ────────────────────────────────────────────────────────────────
    print()
    print(f"{AMBER}{BOLD}███████ ██ ███    ███ ██████  ██      ███████ ██████  ███████  ██████ {RESET}")
    print(f"{AMBER}{BOLD}██      ██ ████  ████ ██   ██ ██      ██      ██   ██ ██      ██      {RESET}")
    print(f"{AMBER}{BOLD}███████ ██ ██ ████ ██ ██████  ██      █████   ██████  █████   ██      {RESET}")
    print(f"{AMBER}{BOLD}     ██ ██ ██  ██  ██ ██      ██      ██      ██   ██ ██      ██      {RESET}")
    print(f"{AMBER}{BOLD}███████ ██ ██      ██ ██      ███████ ███████ ██   ██ ███████  ██████ {DIM} v{VERSION}{RESET}")
    print()

    # ── Box 1 · Device & Status ─────────────────────────────────────────────
    print(_box_top(W))
    print(_box_row(f"{AMBER}Device : {_trunc(device_name, 56)}  {ch_label}{RESET}", W))
    folder_display = seg_dir if seg_dir is not None else outdir
    print(_box_row(f"{AMBER}Folder : {_trunc(str(folder_display), 67)}{RESET}", W))
    print(_box_row(
        f"{AMBER}Status : {status_label}{AMBER}    Length : {BLUE}{BOLD}{human_duration(elapsed)}{RESET}{AMBER}{remaining_txt}{RESET}", W))
    print(_box_row(
        f"{AMBER}Ch: {channels}{sys_txt}{RESET}", W))
    if clip_active:
        if gain_supported:
            print(_box_row(
                f"{RED}{BOLD}⚠ CLIPPING detected! Reduce input gain.  (Events: {clip_count}){RESET}", W))
        else:
            print(_box_row(
                f"{RED}{BOLD}{BLINK}⚠ CLIPPING! Reduce level at SOURCE (mixer/pad).{RESET}"
                f"{RED}{BOLD}  (Events: {clip_count}){RESET}", W))
    if start_wall and mode in ("recording", "playlist"):
        print(_box_row(f"{AMBER}Start  : {start_wall:%Y-%m-%d %H:%M:%S}{RESET}", W))
    elif mode == "preview" and preview_end is not None:
        preview_rms = max(prev_l, prev_r)
        remaining = max(0, int(round(preview_end - time.monotonic())))
        print(_box_row(
            f"{AMBER}Preview: {remaining}s left  ·  {classify_level(preview_rms)}{RESET}", W))
    else:
        print(_box_row(f"{AMBER}Preview: completed / Pause{RESET}", W))
    # DJ picture status
    if photo_enabled:
        dj_status = f"{GREEN}{BOLD}ON{RESET}"
    else:
        dj_status = f"{DIM}OFF{RESET}"
    print(_box_row(f"{AMBER}DJ PIC : {dj_status}{RESET}", W))
    if photo_countdown is not None:
        s = "s" if photo_countdown != 1 else ""
        smile_msg = f"{RED}{BLINK}{BOLD}  \U0001f4f7 SMILE IN {photo_countdown} second{s}!{RESET}"
        print(_box_row(smile_msg, W))
    if not gain_supported:
        print(_box_row(
            f"{YELLOW}{BOLD}⚠ Device has no software gain control – adjust level at source.{RESET}", W))
    print(_box_bot(W))
    print()

    # ── Box · Auto-gain history (hidden when device has no software gain) ──
    if gain_supported:
        cur_txt = f"  (now: {gain_current}%)" if gain_current is not None else ""
        if auto_gain_on:
            mode_tag = f"{GREEN}[AUTO]{RESET}"
        else:
            mode_tag = f"{YELLOW}[MANUAL]{RESET}"
        recent_adjust = (
            gain_action != ""
            and gain_action_at > 0.0
            and (time.monotonic() - gain_action_at) <= AUTO_GAIN_MSG_TTL
        )
        if recent_adjust:
            action_txt = gain_action
            msg_color = f"{RED}{BOLD}"
        else:
            action_txt = gain_action if gain_action else "(idle)"
            msg_color = AMBER
        status_line = f"{AMBER}Auto-gain:{RESET} {mode_tag} {msg_color}{action_txt}{cur_txt}{RESET}"
        grid_rows = _render_gain_grid(gain_history, time.monotonic())
        row_labels = ["100%", " 80%", " 60%", " 40%", " 20%"]
        print(_box_top(W))
        print(_box_row(status_line, W))
        print(_box_row("", W))
        for i, row in enumerate(grid_rows):
            print(_box_row(f"{AMBER}{row_labels[i]} │{RESET}{row}", W))
        tick = "     " + "".join("┴" if (c % 5 == 0) else "─" for c in range(50))
        print(_box_row(f"{AMBER}{tick}{RESET}", W))
        print(_box_row(f"{AMBER}      ←10 min" + " " * 35 + f"now→{RESET}", W))
        print(_box_bot(W))
        print()

    # ── Box 2 · Level Meter ─────────────────────────────────────────────────
    print(_box_top(W))
    print(_box_row(
        f"{AMBER}L: {AMBER}[{colored_meter(db_l, hold_l)}{AMBER}] {db_l:6.1f} dBFS"
        f"   peak={peak_l:.3f}{RESET}", W))
    print(_box_row(
        f"{AMBER}R: {AMBER}[{colored_meter(db_r, hold_r)}{AMBER}] {db_r:6.1f} dBFS"
        f"   peak={peak_r:.3f}{RESET}", W))
    print(_box_row(
        f"{AMBER}Peak-Hold L/R: {hold_l:6.1f} / {hold_r:6.1f} dBFS"
        f"   Pending: {pending_conversions}{RESET}", W))
    # Blinking clipping banner (white on red), shown only while peak is clipping.
    if max(peak_l, peak_r) >= LEVEL_CLIP_LINEAR or simulate_clip_active:
        print(_box_row(
            f"{BG_RED}{FG_WHITE}{BOLD}{BLINK} ⚠ CLIPPING – REDUCE GAIN {RESET}", W))
    print(_box_bot(W))
    print()

    # ── Box 3 · Song Info ───────────────────────────────────────────────────
    countdown = max(0, int(song_next_at - time.monotonic()))
    shazam_ok_txt = "  Shazam ok" if song_tagid else ""
    meta_parts = [p for p in [song_genre, song_year, song_album] if p]
    meta_txt = "  ·  ".join(meta_parts)
    print(_box_top(W))
    print(_box_row(
        f"{AMBER}Song : {BLUE}{BOLD}{_trunc(song_artist + ' - ' + song_title, 69)}{RESET}", W))
    print(_box_row(
        f"{AMBER}Info : {BLUE}{BOLD}{_trunc(meta_txt, 69) if meta_txt else '-'}{RESET}", W))
    print(_box_row(
        f"{AMBER}Check: {song_last_check}  Match: {song_last_match}"
        f"  Next: {countdown:2d}s{shazam_ok_txt}{RESET}", W))
    print(_box_bot(W))
    print()

    # ── Key bar ─────────────────────────────────────────────────────────────
    def _key_btn(k: str) -> str:
        return f"{BG_WHITE}{FG_BLACK}{BOLD} {k} {RESET}{BG_AMBER}{FG_BLACK}"

    bar1 = (
        f"  {_key_btn('S')}=STOP (pause)  "
        f"{_key_btn('R')}=RESTART (new file)  "
        f"{_key_btn('Q')}=SAVE & QUIT"
    )
    pad1 = " " * max(0, W - _visible_len(bar1))
    print(f"{BG_AMBER}{FG_BLACK}{bar1}{pad1}{RESET}")

    bar2 = (
        f"  {_key_btn('P')}=PLAYLIST ONLY  "
        f"{_key_btn('U')}=UPDATE"
    )
    pad2 = " " * max(0, W - _visible_len(bar2))
    print(f"{BG_AMBER}{FG_BLACK}{bar2}{pad2}{RESET}")

    if auto_gain_on:
        bar3 = f"  {_key_btn('A')}=AUTOGAIN: ON   (press [A] to switch off and set manually)"
    elif not gain_supported:
        bar3 = f"  {_key_btn('A')}=AUTOGAIN: N/A  (device has no software gain – adjust at source)"
    else:
        bar3 = (
            f"  {_key_btn('A')}=AUTOGAIN: OFF  "
            f"{_key_btn('2')}=20%  {_key_btn('4')}=40%  {_key_btn('6')}=60%  "
            f"{_key_btn('8')}=80%  {_key_btn('0')}=100%"
        )
    pad3 = " " * max(0, W - _visible_len(bar3))
    print(f"{BG_AMBER}{FG_BLACK}{bar3}{pad3}{RESET}")


def main():
    parser = argparse.ArgumentParser(add_help=True, description="macOS CLI Audio Recorder with stereo capture, M4A output, and song recognition.")
    parser.add_argument("--help-messages", action="store_true", help="Show extended English help messages and exit.")
    args = parser.parse_args()
    if args.help_messages:
        print_help_messages()
        return

    require_macos_tools()
    signal.signal(signal.SIGINT, signal.default_int_handler)
    print("macOS CLI Audio Recorder (.m4a, Stereo, Song Recognition)\n")
    print("Tip: run with --help or --help-messages for usage information.\n")
    output_dir = choose_output_dir()
    max_minutes = ask_recording_minutes()
    filename_prefix = ask_filename_prefix()
    dj_photos = ask_dj_photos()
    device_index, samplerate, channels, device_name = select_input_device()
    print(f"\nSelected: {device_name} (sd-index={device_index})")
    print(f"Output folder: {output_dir}")
    if not SONGREC_AVAILABLE:
        print("Note: 'shazamio' is not installed – song recognition stays disabled.")
        print("Install with: python3 -m pip install shazamio")
    print("Starting stream …")
    time.sleep(0.4)

    state = RecorderState(
        device_index=device_index,
        samplerate=samplerate,
        channels=channels,
        output_dir=output_dir,
        filename_prefix=filename_prefix,
        max_record_seconds=max_minutes * 60.0,
    )
    state.photo_enabled = dj_photos
    # Prevent display & idle sleep for the duration of the recording.
    _caffeinate = None
    try:
        if shutil.which("caffeinate"):
            _caffeinate = subprocess.Popen(["caffeinate", "-d", "-i"])

        state.start_writer()
        state.start_converter()
        state.start_stream()
        # Probe whether this device actually honours the macOS input-gain slider
        # (class-compliant interfaces like Behringer UCA202 have a fixed line-in).
        print("Probing input-gain control …")
        gain_ok = _probe_gain_control()
        state.gain_control_supported = gain_ok
        if gain_ok:
            _set_input_gain(AUTO_GAIN_TARGET)
            state.gain_current_pct = AUTO_GAIN_TARGET
            with state.lock:
                state.gain_history.append((time.monotonic(), AUTO_GAIN_TARGET))
        else:
            state.auto_gain_enabled = False  # nothing the algorithm could do
            state.gain_current_pct = None
            print(
                f"{YELLOW}Note: this device does not respond to the macOS"
                f" input-gain slider.{RESET}\n"
                f"{YELLOW}      Autogain has been disabled. Adjust the level"
                f" at the source (mixer / pad).{RESET}"
            )
            time.sleep(1.5)
        state.start_songrec()
        state.start_photo()
        preview_end = time.monotonic() + PREVIEW_SECONDS

        with KeyReader() as keys:
            while True:
                render_ui(state, device_name, preview_end if state.mode == "preview" else None)
                if state.mode == "preview" and time.monotonic() >= preview_end:
                    with state.lock:
                        msg = classify_level(max(state.preview_peak_lr))
                    print(f"\n{AMBER}Preview finished: {msg}{RESET}")
                    time.sleep(0.5)
                    state.start_segment()
                if state.mode in ("recording", "playlist") and state.elapsed_segment_seconds() >= SEGMENT_SECONDS:
                    saved = state.stop_and_save()
                    if saved:
                        print(f"\n{AMBER}Segment saved (conversion in background): {saved.name}{RESET}")
                    state.start_segment()
                # auto-restart when user-defined duration is reached
                if (
                    state.max_record_seconds is not None
                    and state.session_start_monotonic is not None
                    and time.monotonic() - state.session_start_monotonic >= state.max_record_seconds
                ):
                    if state.mode in ("recording", "playlist"):
                        saved = state.stop_and_save()
                        if saved:
                            print(f"\n{AMBER}Recording limit reached – saving: {saved.name}{RESET}")
                    # reset session counters so next segment starts a fresh session
                    with state.lock:
                        state.session_start_monotonic = None
                        state.session_start_wall = None
                    state.start_segment()
                key = keys.get_key()
                if key == "s":
                    if state.mode in ("recording", "playlist"):
                        saved = state.stop_and_save()
                        if saved:
                            print(f"\n{AMBER}Segment saved (conversion in background): {saved.name}{RESET}")
                            time.sleep(0.5)
                elif key == "r":
                    if state.mode in ("recording", "playlist"):
                        saved = state.stop_and_save()
                        if saved:
                            print(f"\n{AMBER}Segment saved (conversion in background): {saved.name}{RESET}")
                    state.start_segment()
                    time.sleep(0.2)
                elif key == "p":
                    if state.mode in ("recording", "playlist"):
                        saved = state.stop_and_save()
                        if saved:
                            print(f"\n{AMBER}Segment saved: {saved.name}{RESET}")
                    with state.lock:
                        state.playlist_only = not state.playlist_only
                    state.start_segment()
                    time.sleep(0.2)
                elif key == "a":
                    with state.lock:
                        state.auto_gain_enabled = not state.auto_gain_enabled
                        state.gain_last_action = (
                            "AUTOGAIN ON" if state.auto_gain_enabled else "AUTOGAIN OFF (manual)"
                        )
                        state.gain_last_action_at = time.monotonic()
                        if state.auto_gain_enabled:
                            # Fresh start: clear weak-signal timer & cooldown so autogain
                            # can react immediately to the current signal level.
                            state.gain_weak_since = None
                            state.gain_last_adjust = 0.0
                elif key in ("0", "2", "4", "6", "8") and not state.auto_gain_enabled:
                    pct = {"0": 100, "2": 20, "4": 40, "6": 60, "8": 80}[key]
                    state.manual_set_gain(pct)
                elif key == "t":
                    # Simulate clipping for 3s so the banner styling can be tested without signal.
                    with state.lock:
                        state.simulate_clip_until = time.monotonic() + 3.0
                elif key == "u":
                    # Save current segment, then launch the updater script
                    if state.mode in ("recording", "playlist"):
                        saved = state.stop_and_save()
                        if saved:
                            print(f"\n{AMBER}Segment saved: {saved.name}{RESET}")
                    with state.lock:
                        state.mode = "quitting"
                    updater = Path(__file__).parent / "Update simplerec.command"
                    if updater.exists():
                        print(f"\nLaunching updater …")
                        subprocess.Popen(["open", str(updater)])
                    else:
                        print(f"\n{AMBER}Update simplerec.command not found — please download manually.{RESET}")
                    break
                elif key == "q":
                    if state.mode in ("recording", "playlist"):
                        saved = state.stop_and_save()
                        if saved:
                            print(f"\n{AMBER}Saving segment: {saved.name}{RESET}")
                    with state.lock:
                        state.mode = "quitting"
                    break
                time.sleep(UI_REFRESH_SECONDS)
                state.check_auto_gain()
                state.poll_gain()
        if state.mode in ("recording", "playlist"):
            saved = state.stop_and_save()
            if saved:
                print(f"Segment saved: {saved.name}")
    finally:
        state.stop_songrec()
        state.stop_photo()
        state.stop_stream()
        state.stop_writer()
        print("Waiting for pending M4A conversions …")
        state.convert_q.join()
        state.stop_converter()
        # remove the live-status txt file — only the playlist is kept
        try:
            base_dir = state.segment_dir if state.segment_dir else state.output_dir
            status_file = base_dir / state._song_status_fname()
            if status_file.exists():
                status_file.unlink()
        except OSError:
            pass
        print("Done.")
        if _caffeinate is not None:
            _caffeinate.terminate()
            _caffeinate.wait()


if __name__ == "__main__":
    main()
