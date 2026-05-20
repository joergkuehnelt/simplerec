
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
macOS CLI Audio Recorder (.m4a, Stereo, Song Recognition)

English-only output version with built-in help messages.

Examples:
    python3 audio_recorder_macos_m4a_stereo_songrec_en_help.py --help
    python3 audio_recorder_macos_m4a_stereo_songrec_en_help.py --help-messages
    python3 audio_recorder_macos_m4a_stereo_songrec_en_help.py
"""

from __future__ import annotations

import os
import sys
import math
import time
import queue
import shutil
import signal
import select
import termios
import tty
import threading
import subprocess
import datetime as dt
import asyncio
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
SONGREC_WINDOW_SECONDS = 12
SONGREC_INTERVAL_SECONDS = 25
SONGREC_STATUS_FILE = "current_song.txt"
SONGREC_TEMP_SNIPPET = ".songrec_snippet.wav"

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CLEAR = "\033[2J\033[H"
AMBER = "\033[38;5;214m"
GREEN = "\033[38;5;46m"
YELLOW = "\033[38;5;226m"
RED = "\033[38;5;196m"
GREY = "\033[38;5;240m"
CYAN = "\033[38;5;45m"
MAGENTA = "\033[38;5;201m"


class KeyReader:
    def __enter__(self):
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, exc_type, exc, tb):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
        return False

    def get_key(self):
        dr, _, _ = select.select([sys.stdin], [], [], 0)
        if dr:
            return sys.stdin.read(1).lower()
        return None


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
    if index < int(width * 0.68):
        return GREEN
    if index < int(width * 0.88):
        return YELLOW
    return RED


def colored_meter(dbfs: float, peak_hold_db: float, width: int = METER_WIDTH) -> str:
    db = max(-60.0, min(0.0, dbfs))
    frac = (db + 60.0) / 60.0
    filled = int(round(frac * width))
    peak_frac = (max(-60.0, min(0.0, peak_hold_db)) + 60.0) / 60.0
    peak_pos = max(0, min(width - 1, int(round(peak_frac * (width - 1)))))
    out = []
    for i in range(width):
        if i == peak_pos:
            out.append(RED + "│")
        elif i < filled:
            out.append(bar_color(i, width) + "█")
        else:
            out.append(GREY + "·")
    return "".join(out) + RESET


def require_macos_tools():
    if sys.platform != "darwin":
        raise RuntimeError("This version is intended for macOS (sys.platform != 'darwin').")
    if shutil.which("afconvert") is None:
        raise RuntimeError("'afconvert' was not found. Please run this in a macOS terminal.")


def default_output_dir() -> Path:
    return Path.home() / "Music" / "Recordings"


def ask_recording_minutes() -> int:
    print("\nRecording Duration")
    print("------------------")
    while True:
        raw = input("How many minutes to record? (1–120): ").strip()
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
    return outdir


def m4a_final_name(output_dir: Path, start_wall: dt.datetime, end_wall: dt.datetime) -> Path:
    return output_dir / f"{start_wall:%Y%m%d}-start{start_wall:%H%M}-end{end_wall:%H%M}.m4a"


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
    return None, None


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
    recent_blocks: deque = field(default_factory=deque)
    recent_blocks_maxlen: int = 0
    songrec_last_check: Optional[dt.datetime] = None
    songrec_last_match: Optional[dt.datetime] = None
    songrec_current_title: str = "-"
    songrec_current_artist: str = "-"
    songrec_status: str = "not started"
    songrec_total_checks: int = 0
    filename_prefix: str = ""
    max_record_seconds: Optional[float] = None
    session_start_monotonic: Optional[float] = None
    session_start_wall: Optional[dt.datetime] = None

    def __post_init__(self):
        blocks = int(math.ceil((SONGREC_WINDOW_SECONDS * self.samplerate) / BLOCKSIZE)) + 2
        self.recent_blocks = deque(maxlen=max(4, blocks))

    def _song_status_fname(self) -> str:
        ts = self.session_start_wall.strftime("%Y%m%d-%H%M") if self.session_start_wall else "session"
        base = f"current_song_{ts}.txt"
        return (self.filename_prefix + base) if self.filename_prefix else base

    def write_song_status_file(self):
        path = self.output_dir / self._song_status_fname()
        with self.lock:
            last_check = self.songrec_last_check.strftime("%Y-%m-%d %H:%M:%S") if self.songrec_last_check else "-"
            last_match = self.songrec_last_match.strftime("%Y-%m-%d %H:%M:%S") if self.songrec_last_match else "-"
            content = (
                f"Last check   : {last_check}\n"
                f"Last match   : {last_match}\n"
                f"Current song : {self.songrec_current_artist} - {self.songrec_current_title}\n"
                f"Status       : {self.songrec_status}\n"
                f"Checks       : {self.songrec_total_checks}\n"
            )
        path.write_text(content, encoding="utf-8")

    async def _recognize_file(self, path: Path):
        if not self.songrec_enabled or Shazam is None:
            return None, None, "ShazamIO not installed"
        try:
            shazam = Shazam()
            result = await shazam.recognize(str(path))
            track = result.get("track") if isinstance(result, dict) else None
            if track:
                return (track.get("title") or "-", track.get("subtitle") or "-", "Match")
            return None, None, "No match"
        except Exception as exc:
            return None, None, f"Error: {type(exc).__name__}"

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
                time.sleep(SONGREC_INTERVAL_SECONDS)
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
                    title, artist, status = asyncio.run(self._recognize_file(snippet_path))
                    with self.lock:
                        self.songrec_last_check = now
                        self.songrec_total_checks += 1
                        self.songrec_status = status
                        if title and artist:
                            self.songrec_current_title = title
                            self.songrec_current_artist = artist
                            self.songrec_last_match = now
                    self.write_song_status_file()
                except Exception as exc:
                    with self.lock:
                        self.songrec_last_check = dt.datetime.now()
                        self.songrec_total_checks += 1
                        self.songrec_status = f"Error: {type(exc).__name__}"
                    self.write_song_status_file()
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
        with self.lock:
            self.latest_rms_lr = rms_lr
            self.latest_peak_lr = peak_lr
            self.recent_blocks.append(arr.copy())
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
            if self.mode == "recording" and self.current_file is not None:
                self.writer_q.put(indata.copy())

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
        with self.lock:
            if self.current_file is not None:
                return
            start_wall = dt.datetime.now()
            if self.session_start_monotonic is None:
                self.session_start_monotonic = time.monotonic()
                self.session_start_wall = start_wall
            tmp_name = temp_wav_name(self.output_dir, start_wall)
            self.current_file = sf.SoundFile(str(tmp_name), mode="w", samplerate=self.samplerate, channels=self.channels, subtype="PCM_16")
            self.current_temp_name = tmp_name
            self.segment_start_wall = start_wall
            self.segment_start_monotonic = time.monotonic()
            self.mode = "recording"

    def stop_and_save(self) -> Optional[Path]:
        with self.lock:
            if self.current_file is None or self.segment_start_wall is None:
                self.mode = "paused"
                self.segment_start_monotonic = None
                return None
            self.mode = "paused"
            self.segment_start_monotonic = None
        self.writer_q.join()
        with self.lock:
            end_wall = dt.datetime.now()
            tmp_name = self.current_temp_name
            start_wall = self.segment_start_wall
            try: self.current_file.flush()
            except Exception: pass
            try: self.current_file.close()
            except Exception: pass
            self.current_file = None
            self.current_temp_name = None
            self.segment_start_wall = None
        if tmp_name is None or start_wall is None:
            return None
        prefix = self.filename_prefix
        final_name = self.output_dir / f"{prefix}{start_wall:%Y%m%d}-start{start_wall:%H%M}-end{end_wall:%H%M}.m4a"
        if final_name.exists():
            final_name = final_name.with_name(final_name.stem + dt.datetime.now().strftime("-%H%M%S") + final_name.suffix)
        self.convert_q.put((tmp_name, final_name))
        return final_name

    def elapsed_segment_seconds(self) -> float:
        with self.lock:
            if self.mode == "recording" and self.segment_start_monotonic is not None:
                return max(0.0, time.monotonic() - self.segment_start_monotonic)
        return 0.0


def select_input_device() -> tuple[int, int, int, str]:
    devices = sd.query_devices()
    input_devices = []
    print("\nAvailable input devices (short level test – please speak briefly / feed a signal):\n")
    for idx, dev in enumerate(devices):
        max_input = int(dev.get("max_input_channels", 0))
        if max_input > 0:
            channels = 2 if max_input >= 2 else 1
            samplerate = int(dev.get("default_samplerate", SAMPLE_RATE_FALLBACK) or SAMPLE_RATE_FALLBACK)
            left_db, right_db = probe_input_level(idx, samplerate, channels, DEVICE_PROBE_SECONDS)
            input_devices.append((idx, samplerate, channels, dev["name"], left_db, right_db, max_input))
    if not input_devices:
        raise RuntimeError("No input devices found.")
    for i, (sd_idx, samplerate, channels, name, left_db, right_db, maxch) in enumerate(input_devices):
        mode_txt = "Stereo" if channels >= 2 else "Mono fallback"
        if left_db is None:
            lvl_txt = f"{GREY}n/a{RESET}"
        elif right_db is None:
            lvl_txt = f"L {left_db:6.1f} dBFS"
        else:
            lvl_txt = f"L {left_db:6.1f} dBFS | R {right_db:6.1f} dBFS"
        print(f"[{i:02d}] sd-index={sd_idx:02d} | {name} | maxch={maxch} | use={channels} ({mode_txt}) | {samplerate} Hz | Level: {lvl_txt}")
    while True:
        raw = input("\nNumber of the desired device: ").strip()
        if raw.isdigit() and 0 <= int(raw) < len(input_devices):
            sd_idx, samplerate, channels, name, _, _, _ = input_devices[int(raw)]
            if channels < 2:
                print("Note: The selected device does not support stereo. Recording will use mono fallback.")
            return sd_idx, samplerate, channels, name
        print("Invalid input. Please choose one of the listed numbers.")


def _trunc(s: str, width: int = 80) -> str:
    """Truncate a plain-text string to `width` visible chars, adding … if needed."""
    s = str(s)
    if len(s) <= width:
        return s
    return s[:width - 1] + "…"


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
        outdir = state.output_dir
        channels = state.channels
        song_status = state.songrec_status
        song_title = state.songrec_current_title
        song_artist = state.songrec_current_artist
        song_last_check = state.songrec_last_check.strftime("%H:%M:%S") if state.songrec_last_check else "-"
        song_last_match = state.songrec_last_match.strftime("%H:%M:%S") if state.songrec_last_match else "-"
    elapsed = state.elapsed_segment_seconds() if mode == "recording" else 0.0
    db_l = linear_to_dbfs(rms_l)
    db_r = linear_to_dbfs(rms_r)
    if mode == "recording":
        status_label = f"{RED}{BOLD}● REC{RESET}"
    else:
        status_label = f"{AMBER}{BOLD}‖ PAUSE{RESET}"
    clear_screen()
    print(f"{AMBER}{BOLD} _____ ___ __  __ ___ _    ___ ___ ___  {RESET}")
    print(f"{AMBER}{BOLD}/ __| | |  \\/  | _ \\ |  | __| _ \\ __| {RESET}")
    print(f"{AMBER}{BOLD}\\__ \\ | || |\\/| |  _/ |__| _||   / _|  {RESET}")
    print(f"{AMBER}{BOLD}|___/___|_|  |_|_| |____|___|_|_\\___|  {DIM}-beta{RESET}")
    print()
    print(f"{AMBER}Device : {_trunc(device_name, 71)}{RESET}")
    print(f"{AMBER}Folder : {_trunc(str(outdir), 71)}{RESET}")
    print(f"Status : {status_label}    {AMBER}Length : {human_duration(elapsed)}{RESET}    {AMBER}Ch: {channels}{RESET}")
    if clip_active:
        print(f"{RED}{BOLD}WARNING: CLIPPING detected! Reduce input gain.  (Events: {clip_count}){RESET}")
    if start_wall and mode == "recording":
        print(f"{AMBER}Start  : {start_wall:%Y-%m-%d %H:%M:%S}{RESET}")
    elif mode == "preview" and preview_end is not None:
        preview_rms = max(prev_l, prev_r)
        remaining = max(0, int(round(preview_end - time.monotonic())))
        print(f"{AMBER}Preview: {remaining}s left  ·  {classify_level(preview_rms)}{RESET}")
    else:
        print(f"{AMBER}Preview: completed / Pause{RESET}")
    print()
    print(f"{AMBER}L:{RESET} [{colored_meter(db_l, hold_l)}] {GREEN}{db_l:6.1f} dBFS{RESET}   peak={peak_l:.3f}")
    print(f"{AMBER}R:{RESET} [{colored_meter(db_r, hold_r)}] {GREEN}{db_r:6.1f} dBFS{RESET}   peak={peak_r:.3f}")
    print(f"{DIM}Peak-Hold L/R: {hold_l:6.1f} / {hold_r:6.1f} dBFS   Pending Save Jobs: {pending_conversions}{RESET}")
    print()
    print(f"{AMBER}{BOLD}Song:{RESET} {GREEN}{BOLD}{_trunc(song_artist + ' - ' + song_title, 74)}{RESET}")
    print(f"{DIM}Check: {song_last_check}  Match: {song_last_match}  {_trunc('Status: ' + song_status, 55)}{RESET}")
    with state.lock:
        song_fname = state._song_status_fname()
    print(f"{DIM}List : {_trunc(str(outdir / song_fname), 73)}{RESET}")
    print()
    print(f"{AMBER}Keys   :{RESET} {BOLD}S{RESET}=STOP  {BOLD}R{RESET}=RESTART  {BOLD}Q{RESET}=Save and quit")
    print(f"{DIM}Help   : run with --help or --help-messages for usage details{RESET}")


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
    state.start_writer()
    state.start_converter()
    state.start_stream()
    state.start_songrec()
    preview_end = time.monotonic() + PREVIEW_SECONDS

    try:
        with KeyReader() as keys:
            while True:
                render_ui(state, device_name, preview_end if state.mode == "preview" else None)
                if state.mode == "preview" and time.monotonic() >= preview_end:
                    with state.lock:
                        msg = classify_level(max(state.preview_peak_lr))
                    print(f"\n{AMBER}Preview finished: {msg}{RESET}")
                    time.sleep(0.5)
                    state.start_segment()
                if state.mode == "recording" and state.elapsed_segment_seconds() >= SEGMENT_SECONDS:
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
                    if state.mode == "recording":
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
                    if state.mode == "recording":
                        saved = state.stop_and_save()
                        if saved:
                            print(f"\n{AMBER}Segment saved (conversion in background): {saved.name}{RESET}")
                            time.sleep(0.5)
                elif key == "r":
                    if state.mode == "recording":
                        saved = state.stop_and_save()
                        if saved:
                            print(f"\n{AMBER}Segment saved (conversion in background): {saved.name}{RESET}")
                    state.start_segment()
                    time.sleep(0.2)
                elif key == "q":
                    if state.mode == "recording":
                        saved = state.stop_and_save()
                        if saved:
                            print(f"\n{AMBER}Saving segment: {saved.name}{RESET}")
                    with state.lock:
                        state.mode = "quitting"
                    break
                time.sleep(UI_REFRESH_SECONDS)
    except KeyboardInterrupt:
        print("\nAborted by user.")
        if state.mode == "recording":
            saved = state.stop_and_save()
            if saved:
                print(f"Segment saved: {saved.name}")
    finally:
        state.stop_songrec()
        state.stop_stream()
        state.stop_writer()
        print("Waiting for pending M4A conversions …")
        state.convert_q.join()
        state.stop_converter()
        print("Done.")


if __name__ == "__main__":
    main()
