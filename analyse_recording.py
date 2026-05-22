#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyse_recording.py  –  Analyse a simplerec recording session

Reads the last-used output folder from ~/.simplerec_lastdir,
lists completed session subfolders, lets the user pick one,
then analyses every .m4a in that folder and shows:

  - File metadata (via afinfo)
  - Peak, RMS, dynamic range, clipping count, silence
  - Per-minute loudness map (ASCII bar chart)
  - Playlist (.txt) track list
  - Photos taken during the session

Requires: numpy  soundfile  (both installed by simplerec's own requirements)
macOS built-ins used: afconvert, afinfo
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

# ── ANSI colours ────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
AMBER  = "\033[38;5;214m"
GREEN  = "\033[38;5;46m"
YELLOW = "\033[38;5;226m"
RED    = "\033[38;5;196m"
GREY   = "\033[38;5;240m"
BLUE   = "\033[38;5;39m"

_LASTDIR_FILE = Path.home() / ".simplerec_lastdir"

# ── Helpers ──────────────────────────────────────────────────────────────────

def _linear_to_dbfs(v: float) -> float:
    return 20.0 * math.log10(max(v, 1e-12))


def _human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes //= 1024
    return f"{nbytes:.1f} TB"


def _get_last_dir() -> Path | None:
    try:
        saved = _LASTDIR_FILE.read_text(encoding="utf-8").strip()
        if saved:
            return Path(saved)
    except OSError:
        pass
    return None


def _list_session_folders(base: Path) -> list[Path]:
    """Return subfolders matching YYYYMMDD-HHMM, newest first."""
    folders = []
    try:
        for p in base.iterdir():
            if (
                p.is_dir()
                and len(p.name) == 13
                and p.name[8] == "-"
                and p.name[:8].isdigit()
                and p.name[9:].isdigit()
            ):
                folders.append(p)
    except OSError:
        pass
    folders.sort(reverse=True)
    return folders


def _find_m4a(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() == ".m4a")


def _afinfo(m4a: Path) -> str:
    try:
        r = subprocess.run(
            ["afinfo", str(m4a)],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _convert_to_wav(m4a: Path) -> Path:
    """Convert M4A → temporary 16-bit WAV (deleted by caller)."""
    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="simplerec_analyse_")
    os.close(fd)
    tmp_path = Path(tmp)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "I16", str(m4a), str(tmp_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return tmp_path


# ── Audio analysis ────────────────────────────────────────────────────────────

def _analyse_wav(wav_path: Path) -> dict:
    """
    Stream through the WAV in 1-minute chunks and return:
      samplerate, channels, duration, peak_db, peak_db_per_ch,
      rms_db, dynamic_range_db, clip_count, silence_seconds, level_map
    level_map = list of dicts {minute, peak_db, rms_db} (max of all channels)
    """
    CLIP_THRESHOLD = 0.9990  # ≈ −0.009 dBFS

    with sf.SoundFile(str(wav_path)) as f:
        sr       = f.samplerate
        ch       = f.channels
        n_frames = len(f)

        CHUNK = sr * 60  # process 1 minute at a time

        peak_per_ch  = np.zeros(ch)
        power_sum    = np.zeros(ch)
        total_frames = 0
        clip_count   = 0
        silence_secs = 0.0
        level_map    = []
        minute_idx   = 0

        f.seek(0)
        while True:
            data = f.read(CHUNK, dtype="float32", always_2d=True)
            if len(data) == 0:
                break

            n = len(data)
            abs_data     = np.abs(data)
            chunk_peak   = np.max(abs_data, axis=0)          # shape (ch,)
            chunk_power  = np.mean(np.square(data, dtype=np.float64), axis=0)

            peak_per_ch  = np.maximum(peak_per_ch, chunk_peak)
            power_sum   += chunk_power * n
            total_frames += n

            clip_count   += int(np.sum(abs_data >= CLIP_THRESHOLD))

            max_peak_ch = float(np.max(chunk_peak))
            if max_peak_ch < 10 ** (-60.0 / 20.0):
                silence_secs += n / sr

            level_map.append({
                "minute":   minute_idx,
                "peak_db":  _linear_to_dbfs(max_peak_ch),
                "rms_db":   _linear_to_dbfs(float(np.sqrt(np.mean(chunk_power)))),
            })
            minute_idx += 1

    overall_rms_per_ch = np.sqrt(power_sum / max(1, total_frames))
    overall_rms_db     = _linear_to_dbfs(float(np.mean(overall_rms_per_ch)))
    overall_peak_db    = _linear_to_dbfs(float(np.max(peak_per_ch)))

    return {
        "samplerate":     sr,
        "channels":       ch,
        "duration":       n_frames / sr,
        "peak_db":        overall_peak_db,
        "peak_db_per_ch": [_linear_to_dbfs(float(v)) for v in peak_per_ch],
        "rms_db":         overall_rms_db,
        "dynamic_db":     overall_peak_db - overall_rms_db,
        "clip_count":     clip_count,
        "silence_secs":   silence_secs,
        "level_map":      level_map,
    }


# ── Rendering ─────────────────────────────────────────────────────────────────

def _render_level_map(level_map: list[dict], bar_width: int = 52) -> None:
    if not level_map:
        return
    print(f"\n{AMBER}{BOLD}Loudness map  (one row = 1 minute):{RESET}")
    print(f"{GREY}  min   peak    rms   │{'−60dBFS':^20}{'0dBFS':>20}│{RESET}")
    print(f"{GREY}  ─────────────────────{'─' * bar_width}──{RESET}")
    for e in level_map:
        rms  = e["rms_db"]
        peak = e["peak_db"]
        frac = max(0.0, min(1.0, (rms + 60.0) / 60.0))
        filled = int(round(frac * bar_width))
        if rms > -6:
            color = RED
        elif rms > -18:
            color = AMBER
        elif rms > -35:
            color = GREEN
        else:
            color = GREY
        bar = color + "█" * filled + DIM + GREY + "░" * (bar_width - filled) + RESET
        print(f"  {e['minute']:>3}   {peak:>+5.1f}  {rms:>+6.1f}   {bar}")


def _print_playlist(folder: Path) -> None:
    playlists = sorted(folder.glob("*.txt"))
    if not playlists:
        return
    pl = playlists[0]
    print(f"\n{AMBER}{BOLD}Playlist: {pl.name}{RESET}")
    try:
        lines = pl.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            print(f"  {GREY}(empty){RESET}")
            return
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            parts = ln.split(";")
            if len(parts) >= 4 and parts[2].strip().upper() == "CLIP-ADJUST":
                print(f"  {YELLOW}{parts[0]}  ⚙ {parts[3].strip()}{RESET}")
            elif len(parts) >= 4 and parts[2].strip():
                artist = parts[2].strip()
                title  = parts[3].strip()
                genre  = parts[4].strip() if len(parts) > 4 else ""
                year   = parts[5].strip() if len(parts) > 5 else ""
                meta   = "  " + "  ·  ".join(p for p in [genre, year] if p) if (genre or year) else ""
                print(f"  {BLUE}{parts[0]}{RESET}  {BOLD}{artist}{RESET} – {title}{GREY}{meta}{RESET}")
            else:
                elapsed = parts[1].strip() if len(parts) > 1 else ""
                print(f"  {GREY}{parts[0]}  [{elapsed}]  –no match–{RESET}")
    except OSError as e:
        print(f"  {RED}(could not read playlist: {e}){RESET}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Step 1: resolve base folder ─────────────────────────────────────────
    base = _get_last_dir()
    if base is None or not base.exists():
        if base:
            print(f"{YELLOW}Last folder not found: {base}{RESET}")
        else:
            print(f"{YELLOW}~/.simplerec_lastdir not found.{RESET}")
        raw = input("Enter recording output folder path: ").strip()
        if not raw:
            sys.exit(1)
        base = Path(raw).expanduser().resolve()

    if not base.exists():
        print(f"{RED}Folder does not exist: {base}{RESET}")
        sys.exit(1)

    # ── Step 2: list sessions ────────────────────────────────────────────────
    folders = _list_session_folders(base)
    if not folders:
        print(f"{RED}No recording sessions found in: {base}{RESET}")
        sys.exit(1)

    print(f"\n{AMBER}{BOLD}Recording sessions in:{RESET} {base}\n")
    for i, folder in enumerate(folders):
        m4a_files = _find_m4a(folder)
        photos    = list(folder.glob("*.jpg"))
        pls       = list(folder.glob("*.txt"))

        parts: list[str] = []
        if m4a_files:
            total_bytes = sum(p.stat().st_size for p in m4a_files)
            parts.append(f"{len(m4a_files)} M4A  {_human_size(total_bytes)}")
        else:
            parts.append(f"{DIM}no M4A{RESET}")
        if photos:
            parts.append(f"{len(photos)} photo{'s' if len(photos) != 1 else ''}")
        if pls:
            parts.append("playlist")

        info = "   ".join(parts)
        print(f"  [{i:02d}]  {BOLD}{folder.name}{RESET}   {info}")

    print()

    # ── Step 3: choose session ───────────────────────────────────────────────
    while True:
        raw = input("Choose session number (or Q to quit): ").strip().lower()
        if raw in ("q", "quit", "exit"):
            sys.exit(0)
        if raw.isdigit() and 0 <= int(raw) < len(folders):
            chosen = folders[int(raw)]
            break
        print("  Invalid – please enter a number from the list above.")

    print()

    # ── Step 4: analyse each M4A ────────────────────────────────────────────
    m4a_files = _find_m4a(chosen)

    if not m4a_files:
        print(f"{YELLOW}No .m4a files in {chosen}{RESET}")
    else:
        for m4a in m4a_files:
            sep = AMBER + "═" * 72 + RESET
            print(sep)
            print(f"{AMBER}{BOLD}File : {m4a.name}{RESET}")
            print(f"  Size : {_human_size(m4a.stat().st_size)}")

            # afinfo metadata
            info_txt = _afinfo(m4a)
            for line in info_txt.splitlines():
                line = line.strip()
                if any(k in line.lower() for k in (
                    "duration", "sample rate", "channels", "bit rate",
                    "data format", "packet", "estimated"
                )):
                    print(f"  {line}")

            # convert + analyse
            print(f"\n  {DIM}Converting to WAV …{RESET}", end="", flush=True)
            try:
                wav_tmp = _convert_to_wav(m4a)
            except subprocess.CalledProcessError as exc:
                print(f"\n  {RED}afconvert failed: {exc}{RESET}")
                continue

            print(f"\r  {DIM}Analysing audio …         {RESET}", end="", flush=True)
            try:
                s = _analyse_wav(wav_tmp)
            finally:
                try:
                    wav_tmp.unlink()
                except OSError:
                    pass

            # clear progress line
            print(f"\r{' ' * 40}\r", end="")

            print(f"  Duration  : {BOLD}{_human_duration(s['duration'])}{RESET}"
                  f"  ({s['duration']:.1f} s)")
            print(f"  Rate      : {s['samplerate']} Hz  ·  {s['channels']} ch")

            ch_detail = "  ".join(
                f"Ch{i+1} {v:+.1f}" for i, v in enumerate(s["peak_db_per_ch"])
            )
            print(f"  Peak      : {s['peak_db']:+.1f} dBFS   ({ch_detail})")
            print(f"  RMS       : {s['rms_db']:+.1f} dBFS")
            print(f"  Dynamic   : {s['dynamic_db']:.1f} dB  (peak − rms)")

            if s["clip_count"] > 0:
                print(f"  {RED}{BOLD}Clipping  : {s['clip_count']} samples ≥ −0.009 dBFS{RESET}")
            else:
                print(f"  {GREEN}Clipping  : none{RESET}")

            if s["silence_secs"] > 1.0:
                print(f"  Silence   : {s['silence_secs']:.1f} s  (below −60 dBFS)")
            else:
                print(f"  Silence   : {GREEN}none significant{RESET}")

            _render_level_map(s["level_map"])

    # ── Step 5: playlist & photos ────────────────────────────────────────────
    _print_playlist(chosen)

    photos = sorted(chosen.glob("*.jpg"))
    if photos:
        print(f"\n{AMBER}{BOLD}Photos ({len(photos)}):{RESET}")
        for p in photos:
            print(f"  {p.name}")

    print(f"\n{AMBER}{'═' * 72}{RESET}\n")


if __name__ == "__main__":
    main()
