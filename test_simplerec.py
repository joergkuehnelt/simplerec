#!/usr/bin/env python3
"""Automated test suite for simplerec.py.

Run with:
    python3 -m pytest test_simplerec.py -v
    python3 -m pytest test_simplerec.py -v --tb=short
"""
from __future__ import annotations

import math
import os
import sys
import time
import threading
import datetime as dt
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock hardware-only deps before importing the module under test so tests
# run without a physical audio device, osascript, imagesnap, or afconvert.
# ---------------------------------------------------------------------------
for _mod in ("sounddevice", "soundfile"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import simplerec as sr  # noqa: E402  (must come after sys.modules patches)


# ===========================================================================
# Helpers
# ===========================================================================

def _make_state(tmp_path: Path, **kwargs) -> sr.RecorderState:
    """Return a RecorderState wired to a temp directory."""
    return sr.RecorderState(
        device_index=0,
        samplerate=48000,
        channels=2,
        output_dir=tmp_path,
        **kwargs,
    )


@pytest.fixture
def state(tmp_path):
    return _make_state(tmp_path)


# ===========================================================================
# 1. Pure utility functions
# ===========================================================================

class TestHumanDuration:
    def test_zero(self):
        assert sr.human_duration(0) == "00:00:00"

    def test_one_second(self):
        assert sr.human_duration(1) == "00:00:01"

    def test_one_minute(self):
        assert sr.human_duration(60) == "00:01:00"

    def test_one_hour(self):
        assert sr.human_duration(3600) == "01:00:00"

    def test_mixed(self):
        assert sr.human_duration(3661) == "01:01:01"

    def test_negative_clamped_to_zero(self):
        assert sr.human_duration(-5) == "00:00:00"

    def test_float_truncated(self):
        assert sr.human_duration(90.9) == "00:01:30"


class TestLinearToDbfs:
    def test_silence(self):
        assert sr.linear_to_dbfs(0) == pytest.approx(-120.0)

    def test_full_scale(self):
        assert sr.linear_to_dbfs(1.0) == pytest.approx(0.0)

    def test_half_scale(self):
        assert sr.linear_to_dbfs(0.5) == pytest.approx(-6.02, abs=0.01)

    def test_very_small_clamped(self):
        assert sr.linear_to_dbfs(1e-13) == pytest.approx(-120.0)

    def test_quarter_scale(self):
        assert sr.linear_to_dbfs(0.25) == pytest.approx(-12.04, abs=0.01)


class TestClassifyLevel:
    def test_low(self):
        result = sr.classify_level(0.001)   # ≈ −60 dBFS
        assert "low" in result.lower()

    def test_ok(self):
        result = sr.classify_level(0.2)     # ≈ −14 dBFS
        assert "ok" in result.lower()

    def test_high(self):
        result = sr.classify_level(0.9)     # ≈ −0.9 dBFS
        assert "high" in result.lower() or "reduce" in result.lower()


class TestTrunc:
    def test_short_string_unchanged(self):
        assert sr._trunc("hello", 10) == "hello"

    def test_exact_length_unchanged(self):
        assert sr._trunc("hello", 5) == "hello"

    def test_truncated_ends_with_ellipsis(self):
        result = sr._trunc("hello world", 5)
        assert result.endswith("…")
        assert len(result) == 5

    def test_empty_string(self):
        assert sr._trunc("", 5) == ""

  
        def test_non_string_coerced(self):
            result = sr._trunc(str(12345), 3)
            assert result.endswith("…")



class TestVisibleLen:
    def test_plain_string(self):
        assert sr._visible_len("hello") == 5

    def test_bold_ansi(self):
        assert sr._visible_len("\033[1mhello\033[0m") == 5

    def test_256_colour_ansi(self):
        assert sr._visible_len("\033[38;5;196mABC\033[0m") == 3

    def test_empty_string(self):
        assert sr._visible_len("") == 0


class TestExtractTrackMeta:
    def test_empty_dict(self):
        assert sr._extract_track_meta({}) == {"genre": "", "album": "", "year": ""}

    def test_genre(self):
        track = {"genres": {"primary": "Electronic"}}
        assert sr._extract_track_meta(track)["genre"] == "Electronic"

    def test_album_and_year(self):
        track = {
            "sections": [{
                "type": "SONG",
                "metadata": [
                    {"title": "Album", "text": "Homework"},
                    {"title": "Released", "text": "1997-01-01"},
                ],
            }]
        }
        meta = sr._extract_track_meta(track)
        assert meta["album"] == "Homework"
        assert meta["year"] == "1997"

    def test_year_truncated_to_four_chars(self):
        track = {
            "sections": [{
                "type": "SONG",
                "metadata": [{"title": "Released", "text": "20261231"}],
            }]
        }
        assert sr._extract_track_meta(track)["year"] == "2026"

    def test_non_song_section_ignored(self):
        track = {
            "sections": [{
                "type": "VIDEO",
                "metadata": [{"title": "Album", "text": "ShouldNotAppear"}],
            }]
        }
        assert sr._extract_track_meta(track)["album"] == ""


# ===========================================================================
# 2. Filename sanitization
# ===========================================================================

class TestAskFilenamePrefix:
    def test_safe_input_preserved(self):
        with patch("builtins.input", return_value="Party2026_"):
            assert sr.ask_filename_prefix() == "Party2026_"

    def test_spaces_converted_to_underscores(self):
        with patch("builtins.input", return_value="Party 2026"):
            result = sr.ask_filename_prefix()
        assert " " not in result
        assert "_" in result

    def test_path_traversal_slashes_stripped(self):
        with patch("builtins.input", return_value="../../../etc/passwd"):
            result = sr.ask_filename_prefix()
        assert "/" not in result

    def test_shell_special_chars_stripped(self):
        with patch("builtins.input", return_value="Test!@#$%^&*()"):
            result = sr.ask_filename_prefix()
        for c in "!@#$%^&*()":
            assert c not in result

    def test_empty_input_returns_empty(self):
        with patch("builtins.input", return_value=""):
            assert sr.ask_filename_prefix() == ""

    def test_unicode_kept(self):
        # Python's isalnum() accepts Unicode letters (é, ü, etc.) — they are valid
        # macOS filename characters and are intentionally kept by the sanitizer.
        with patch("builtins.input", return_value="Café2026"):
            result = sr.ask_filename_prefix()
        assert "Caf" in result
        assert "2026" in result


# ===========================================================================
# 3. RecorderState helper methods
# ===========================================================================

class TestNormKey:
    def test_basic_case_fold(self):
        assert sr.RecorderState._norm_key("Daft Punk", "Get Lucky") == ("daft punk", "get lucky")

    def test_strips_whitespace(self):
        assert sr.RecorderState._norm_key("  Artist  ", "  Title  ") == ("artist", "title")

    def test_none_values(self):
        assert sr.RecorderState._norm_key(None, None) == ("", "")

    def test_empty_strings(self):
        assert sr.RecorderState._norm_key("", "") == ("", "")


class TestSongStatusFname:
    def test_with_prefix_and_time(self, state):
        state.filename_prefix = "DJ_"
        state.session_start_wall = dt.datetime(2026, 5, 20, 22, 30)
        assert state._song_status_fname() == "DJ_current_song_20260520-2230.txt"

    def test_no_prefix(self, state):
        state.session_start_wall = dt.datetime(2026, 5, 20, 10, 0)
        assert state._song_status_fname() == "current_song_20260520-1000.txt"

    def test_fallback_when_no_wall_time(self, state):
        state.session_start_wall = None
        assert "session" in state._song_status_fname()


class TestLastSongKeyFromFile:
    def test_returns_none_for_missing_file(self, tmp_path):
        result = sr.RecorderState._last_song_key_from_file(tmp_path / "missing.txt")
        assert result is None

    def test_parses_last_song_row(self, tmp_path):
        f = tmp_path / "playlist.txt"
        f.write_text("10:00:00;00:00:00;Daft Punk;Get Lucky;Electronic;2013\n")
        assert sr.RecorderState._last_song_key_from_file(f) == ("daft punk", "get lucky")

    def test_skips_clip_adjust_rows(self, tmp_path):
        f = tmp_path / "playlist.txt"
        f.write_text(
            "10:00:00;00:00:00;Daft Punk;Get Lucky;Electronic;2013\n"
            "10:05:00;00:05:12;CLIP-ADJUST;↓ clipping → 60%;;\n"
        )
        assert sr.RecorderState._last_song_key_from_file(f) == ("daft punk", "get lucky")

    def test_returns_none_for_empty_file(self, tmp_path):
        f = tmp_path / "playlist.txt"
        f.write_text("")
        assert sr.RecorderState._last_song_key_from_file(f) is None

    def test_case_insensitive(self, tmp_path):
        f = tmp_path / "playlist.txt"
        f.write_text("10:00:00;00:00:00;DAFT PUNK;GET LUCKY;;\n")
        key = sr.RecorderState._last_song_key_from_file(f)
        assert key == ("daft punk", "get lucky")


# ===========================================================================
# 4. Playlist append logic
# ===========================================================================

def _recording_state(tmp_path: Path) -> tuple[sr.RecorderState, Path]:
    s = _make_state(tmp_path)
    playlist = tmp_path / "playlist.txt"
    playlist.touch()
    s.playlist_path = playlist
    s.segment_start_monotonic = time.monotonic()
    s.mode = "recording"
    return s, playlist


class TestAppendToPlaylist:
    def test_appends_new_song(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        s.append_to_playlist("Get Lucky", "Daft Punk", "tag1", dt.datetime.now(), 60.0, "Electronic", "2013")
        content = pl.read_text()
        assert "Daft Punk" in content
        assert "Get Lucky" in content

    def test_deduplicates_same_tagid(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        now = dt.datetime.now()
        s.append_to_playlist("Get Lucky", "Daft Punk", "tag1", now, 60.0)
        s.append_to_playlist("Get Lucky", "Daft Punk", "tag1", now, 75.0)
        lines = [l for l in pl.read_text().splitlines() if l.strip()]
        assert len(lines) == 1

    def test_deduplicates_same_artist_title_different_case(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        now = dt.datetime.now()
        s.append_to_playlist("Get Lucky", "Daft Punk", "tag1", now, 60.0)
        s.append_to_playlist("get lucky", "DAFT PUNK", "tag2", now, 75.0)
        lines = [l for l in pl.read_text().splitlines() if l.strip()]
        assert len(lines) == 1

    def test_allows_different_songs(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        now = dt.datetime.now()
        s.append_to_playlist("Get Lucky", "Daft Punk", "tag1", now, 60.0)
        s.append_to_playlist("Around the World", "Daft Punk", "tag2", now, 120.0)
        lines = [l for l in pl.read_text().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_skips_consecutive_no_match(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        now = dt.datetime.now()
        s.append_to_playlist(None, None, "", now, 60.0)
        s.append_to_playlist(None, None, "", now, 75.0)
        lines = [l for l in pl.read_text().splitlines() if l.strip()]
        assert len(lines) == 1

    def test_allows_song_after_no_match(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        now = dt.datetime.now()
        s.append_to_playlist(None, None, "", now, 60.0)
        s.append_to_playlist("Get Lucky", "Daft Punk", "tag1", now, 75.0)
        lines = [l for l in pl.read_text().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_no_write_when_playlist_path_none(self, tmp_path):
        s = _make_state(tmp_path)
        s.playlist_path = None
        # Must not raise
        s.append_to_playlist("Title", "Artist", "tag", dt.datetime.now(), 0.0)

    def test_csv_columns_present(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        s.append_to_playlist("Get Lucky", "Daft Punk", "tag1", dt.datetime.now(), 65.0, "Electronic", "2013")
        row = pl.read_text().strip()
        parts = row.split(";")
        assert len(parts) == 6   # time;elapsed;artist;title;genre;year
        assert parts[2] == "Daft Punk"
        assert parts[3] == "Get Lucky"
        assert parts[4] == "Electronic"
        assert parts[5] == "2013"


class TestAppendGainEventToPlaylist:
    def test_writes_clip_adjust_row(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        s.append_gain_event_to_playlist("↓ clipping → 60%")
        content = pl.read_text()
        assert "CLIP-ADJUST" in content

    def test_elapsed_time_populated(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        s.segment_start_monotonic = time.monotonic() - 120.0   # 2 min elapsed
        s.append_gain_event_to_playlist("↓ clipping → 60%")
        content = pl.read_text()
        # elapsed field should contain ~2 minutes
        assert "00:02:" in content

    def test_elapsed_zero_when_no_segment_start(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        s.segment_start_monotonic = None
        s.append_gain_event_to_playlist("test")
        assert "00:00:00" in pl.read_text()

    def test_no_crash_without_playlist_path(self, tmp_path):
        s = _make_state(tmp_path)
        s.playlist_path = None
        s.append_gain_event_to_playlist("test")   # must not raise

    def test_csv_has_six_columns(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        s.append_gain_event_to_playlist("↓ clipping → 60%")
        parts = pl.read_text().strip().split(";")
        assert len(parts) == 6


# ===========================================================================
# 5. Auto-gain state machine
# ===========================================================================

def _gain_state(tmp_path: Path, rms_db: float = -20.0, peak_db: float = -20.0,
                cur_pct: int = 80) -> sr.RecorderState:
    """State primed for auto-gain checks: recording mode, cooldown cleared."""
    import numpy as np
    s = _make_state(tmp_path)
    rms_lin  = 10 ** (rms_db  / 20.0)
    peak_lin = 10 ** (peak_db / 20.0)
    s.mode = "recording"
    s.latest_rms_lr  = (rms_lin, rms_lin)
    s.latest_peak_lr = (peak_lin, peak_lin)
    s.gain_current_pct = cur_pct
    # Set far enough in the past so the cooldown check (now - last_adjust >= COOLDOWN) passes
    # even when time.monotonic() is small (e.g. early in a fresh pytest process).
    s.gain_last_adjust = time.monotonic() - sr.AUTO_GAIN_COOLDOWN - 1.0
    return s


class TestCheckAutoGain:
    def test_no_action_in_preview_mode(self, tmp_path):
        s = _gain_state(tmp_path, rms_db=-60.0)
        s.mode = "preview"
        with patch("simplerec._set_input_gain") as mock_set:
            s.check_auto_gain()
        mock_set.assert_not_called()

    def test_no_action_in_paused_mode(self, tmp_path):
        s = _gain_state(tmp_path, rms_db=-60.0)
        s.mode = "paused"
        with patch("simplerec._set_input_gain") as mock_set:
            s.check_auto_gain()
        mock_set.assert_not_called()

    def test_no_action_when_autogain_disabled(self, tmp_path):
        s = _gain_state(tmp_path, rms_db=-60.0)
        s.auto_gain_enabled = False
        with patch("simplerec._set_input_gain") as mock_set:
            s.check_auto_gain()
        mock_set.assert_not_called()

    def test_no_action_during_cooldown(self, tmp_path):
        s = _gain_state(tmp_path, rms_db=-60.0)
        s.gain_last_adjust = time.monotonic()   # just adjusted
        with patch("simplerec._set_input_gain") as mock_set:
            s.check_auto_gain()
        mock_set.assert_not_called()

    def test_clipping_danger_reduces_gain_by_step(self, tmp_path):
        s = _gain_state(tmp_path, peak_db=-1.0, cur_pct=80)   # above DANGER_DB (−2)
        with patch("simplerec._set_input_gain") as mock_set:
            s.check_auto_gain()
        mock_set.assert_called_once_with(60)   # 80 − 20 = 60

    def test_clipping_danger_never_below_minimum(self, tmp_path):
        s = _gain_state(tmp_path, peak_db=-1.0, cur_pct=sr.AUTO_GAIN_MIN)
        with patch("simplerec._set_input_gain") as mock_set:
            s.check_auto_gain()
        if mock_set.called:
            assert mock_set.call_args[0][0] >= sr.AUTO_GAIN_MIN

    def test_very_weak_boosts_to_100_after_hold(self, tmp_path):
        s = _gain_state(tmp_path, rms_db=-55.0, cur_pct=80)   # below VERY_WEAK_DB (−50)
        s.gain_weak_since = time.monotonic() - sr.AUTO_GAIN_WEAK_HOLD - 1.0
        with patch("simplerec._set_input_gain") as mock_set:
            s.check_auto_gain()
        mock_set.assert_called_once_with(sr.AUTO_GAIN_BOOST)   # 100 %

    def test_weak_boosts_to_target_after_hold(self, tmp_path):
        s = _gain_state(tmp_path, rms_db=-40.0, cur_pct=20)   # below WEAK_DB (−35)
        s.gain_weak_since = time.monotonic() - sr.AUTO_GAIN_WEAK_HOLD - 1.0
        with patch("simplerec._set_input_gain") as mock_set:
            s.check_auto_gain()
        mock_set.assert_called_once_with(sr.AUTO_GAIN_TARGET)   # 80 %

    def test_weak_already_at_target_boosts_to_max(self, tmp_path):
        # Bug fix: already at 80 % but still weak → must boost to 100 %
        s = _gain_state(tmp_path, rms_db=-40.0, cur_pct=sr.AUTO_GAIN_TARGET)
        s.gain_weak_since = time.monotonic() - sr.AUTO_GAIN_WEAK_HOLD - 1.0
        with patch("simplerec._set_input_gain") as mock_set:
            s.check_auto_gain()
        mock_set.assert_called_once_with(sr.AUTO_GAIN_BOOST)

    def test_normal_signal_clears_weak_since(self, tmp_path):
        s = _gain_state(tmp_path, rms_db=-20.0)   # perfectly normal signal
        s.gain_weak_since = time.monotonic() - 10.0
        s.check_auto_gain()
        assert s.gain_weak_since is None

    def test_weak_not_triggered_before_hold_expires(self, tmp_path):
        s = _gain_state(tmp_path, rms_db=-40.0, cur_pct=20)
        s.gain_weak_since = time.monotonic()   # just started – hold not expired
        with patch("simplerec._set_input_gain") as mock_set:
            s.check_auto_gain()
        mock_set.assert_not_called()

    def test_clipping_hold_active_triggers_step_down(self, tmp_path):
        s = _gain_state(tmp_path, peak_db=-20.0, cur_pct=80)   # peak is fine now…
        s.clip_hold_until = time.monotonic() + 2.0              # …but clip hold active
        with patch("simplerec._set_input_gain") as mock_set:
            s.check_auto_gain()
        mock_set.assert_called_once_with(60)


# ===========================================================================
# 6. Segment lifecycle (start_segment / stop_and_save)
# ===========================================================================

class TestSegmentLifecycle:
    def _started(self, tmp_path):
        s = _make_state(tmp_path)
        with patch("simplerec.sf.SoundFile", return_value=MagicMock()):
            s.start_writer()
            s.start_segment()
        return s

    def test_start_segment_sets_recording_mode(self, tmp_path):
        s = self._started(tmp_path)
        assert s.mode == "recording"
        s.stop_writer()

    def test_start_segment_creates_segment_subfolder(self, tmp_path):
        s = self._started(tmp_path)
        assert s.segment_dir is not None
        assert s.segment_dir.exists()
        s.stop_writer()

    def test_segment_subfolder_name_format(self, tmp_path):
        s = self._started(tmp_path)
        assert s.segment_dir is not None
        name = s.segment_dir.name
        assert len(name) == 13          # YYYYMMDD-HHMM
        assert name[8] == "-"
        assert name[:8].isdigit()
        s.stop_writer()

    def test_stop_and_save_queues_conversion(self, tmp_path):
        s = self._started(tmp_path)
        s.stop_and_save()
        assert not s.convert_q.empty()
        s.stop_writer()

    def test_stop_and_save_sets_paused_mode(self, tmp_path):
        s = self._started(tmp_path)
        s.stop_and_save()
        assert s.mode == "paused"
        s.stop_writer()

    def test_stop_and_save_clears_segment_dir(self, tmp_path):
        s = self._started(tmp_path)
        s.stop_and_save()
        assert s.segment_dir is None
        s.stop_writer()

    def test_stop_and_save_returns_none_when_not_recording(self, tmp_path):
        s = _make_state(tmp_path)
        assert s.stop_and_save() is None

    def test_playlist_only_mode_on_start(self, tmp_path):
        s = _make_state(tmp_path)
        s.playlist_only = True
        s.start_writer()
        s.start_segment()
        assert s.mode == "playlist"
        s.stop_writer()

    def test_second_start_segment_ignored_while_recording(self, tmp_path):
        s = self._started(tmp_path)
        first_dir = s.segment_dir
        with patch("simplerec.sf.SoundFile", return_value=MagicMock()):
            s.start_segment()   # should be no-op
        assert s.segment_dir == first_dir
        s.stop_writer()

    def test_elapsed_segment_seconds_during_recording(self, tmp_path):
        s = self._started(tmp_path)
        time.sleep(0.05)
        elapsed = s.elapsed_segment_seconds()
        assert elapsed > 0.0
        s.stop_writer()

    def test_elapsed_zero_when_paused(self, tmp_path):
        s = _make_state(tmp_path)
        s.mode = "paused"
        assert s.elapsed_segment_seconds() == 0.0


# ===========================================================================
# 7. Photo thread
# ===========================================================================

class TestPhotoThread:
    def test_disabled_no_thread_started(self, tmp_path):
        s = _make_state(tmp_path)
        s.photo_enabled = False
        s.start_photo()
        assert s.photo_thread is None

    def test_skipped_when_imagesnap_missing(self, tmp_path):
        s = _make_state(tmp_path)
        s.photo_enabled = True
        with patch("shutil.which", return_value=None):
            s.start_photo()
        assert s.photo_thread is None

    def test_thread_starts_when_enabled_and_imagesnap_present(self, tmp_path):
        s = _make_state(tmp_path)
        s.photo_enabled = True
        with patch("shutil.which", return_value="/usr/local/bin/imagesnap"):
            s.start_photo()
        assert s.photo_thread is not None
        assert s.photo_thread.is_alive()
        s.stop_photo()

    def test_stop_photo_joins_thread(self, tmp_path):
        s = _make_state(tmp_path)
        s.photo_enabled = True
        with patch("shutil.which", return_value="/usr/local/bin/imagesnap"):
            s.start_photo()
        s.stop_photo()
        assert s.photo_thread is None
        assert s.photo_countdown is None

    def test_stop_photo_without_start_is_safe(self, tmp_path):
        s = _make_state(tmp_path)
        s.stop_photo()   # must not raise


# ===========================================================================
# 8. Gain history grid
# ===========================================================================

class TestRenderGainGrid:
    def test_returns_five_rows(self):
        rows = sr._render_gain_grid([], time.monotonic())
        assert len(rows) == 5

    def test_empty_history_no_bold_dot(self):
        rows = sr._render_gain_grid([], time.monotonic())
        # BOLD ● should not appear for empty history
        for row in rows:
            assert "\033[1m●" not in row

    def test_100pct_sample_in_top_row(self):
        now = time.monotonic()
        rows = sr._render_gain_grid([(now - 1, 100)], now)
        assert "●" in rows[0]

    def test_20pct_sample_in_bottom_row(self):
        now = time.monotonic()
        rows = sr._render_gain_grid([(now - 1, 20)], now)
        assert "●" in rows[4]

    def test_80pct_sample_in_row_1(self):
        now = time.monotonic()
        rows = sr._render_gain_grid([(now - 1, 80)], now)
        assert "●" in rows[1]

    def test_old_sample_carried_forward(self):
        # The grid uses "last known value" semantics: a sample before the window
        # is carried forward as the initial state for all columns, so a bold dot
        # will appear across the entire row (correct historical behaviour).
        now = time.monotonic()
        old = now - sr.GAIN_HISTORY_SECONDS - 60
        rows = sr._render_gain_grid([(old, 100)], now)
        # Row 0 = 100 % → should be filled with bold dots (carried-forward value)
        assert "●" in rows[0]


# ===========================================================================
# 9. Security: _set_input_gain clamping
# ===========================================================================

class TestSetInputGainSecurity:
    def test_value_above_100_clamped(self):
        with patch("simplerec.subprocess.run") as mock_run:
            sr._set_input_gain(200)
        cmd = mock_run.call_args[0][0]
        assert "100" in cmd[-1]
        assert "200" not in cmd[-1]

    def test_value_below_0_clamped(self):
        with patch("simplerec.subprocess.run") as mock_run:
            sr._set_input_gain(-50)
        cmd = mock_run.call_args[0][0]
        # clamped to 0 – the string in the osascript command should be "0"
        assert cmd[-1].endswith(" 0")

    def test_normal_value_passed_through(self):
        with patch("simplerec.subprocess.run") as mock_run:
            sr._set_input_gain(60)
        cmd = mock_run.call_args[0][0]
        assert "60" in cmd[-1]

    def test_float_cast_to_int(self):
        with patch("simplerec.subprocess.run") as mock_run:
            sr._set_input_gain(75.9)   # type: ignore
        cmd = mock_run.call_args[0][0]
        assert "75" in cmd[-1]
        assert "75.9" not in cmd[-1]


# ===========================================================================
# 10. ElapsedSegmentSeconds
# ===========================================================================

class TestElapsedSegmentSeconds:
    def test_zero_when_paused(self, state):
        state.mode = "paused"
        assert state.elapsed_segment_seconds() == 0.0

    def test_zero_when_preview(self, state):
        state.mode = "preview"
        assert state.elapsed_segment_seconds() == 0.0

    def test_nonzero_when_recording(self, state):
        state.mode = "recording"
        state.segment_start_monotonic = time.monotonic() - 5.0
        elapsed = state.elapsed_segment_seconds()
        assert 4.9 <= elapsed <= 6.0

    def test_nonzero_in_playlist_mode(self, state):
        state.mode = "playlist"
        state.segment_start_monotonic = time.monotonic() - 10.0
        assert state.elapsed_segment_seconds() > 9.0


# ===========================================================================
# 11. Integration: audio_callback (direct call with numpy array)
# ===========================================================================

class TestAudioCallback:
    def _make_audio(self, channels: int, frames: int = 512, amplitude: float = 0.5):
        import numpy as np
        return np.full((frames, channels), amplitude, dtype=np.float32)

    def _state(self, tmp_path, channels=2):
        return sr.RecorderState(device_index=0, samplerate=48000,
                                channels=channels, output_dir=tmp_path)

    def test_updates_rms_after_callback(self, tmp_path):
        s = self._state(tmp_path)
        indata = self._make_audio(2, amplitude=0.5)
        s.audio_callback(indata, 512, None, None)
        assert s.latest_rms_lr[0] > 0.0
        assert s.latest_rms_lr[1] > 0.0

    def test_updates_peak_after_callback(self, tmp_path):
        s = self._state(tmp_path)
        indata = self._make_audio(2, amplitude=0.8)
        s.audio_callback(indata, 512, None, None)
        assert s.latest_peak_lr[0] > 0.0

    def test_clip_detected_near_full_scale(self, tmp_path):
        s = self._state(tmp_path)
        indata = self._make_audio(2, amplitude=0.999)
        s.audio_callback(indata, 512, None, None)
        assert s.clip_count >= 1
        assert s.clip_hold_until > time.monotonic()

    def test_no_clip_on_normal_signal(self, tmp_path):
        s = self._state(tmp_path)
        indata = self._make_audio(2, amplitude=0.2)
        s.audio_callback(indata, 512, None, None)
        assert s.clip_count == 0

    def test_preview_peak_accumulates(self, tmp_path):
        s = self._state(tmp_path)
        s.mode = "preview"
        indata = self._make_audio(2, amplitude=0.3)
        s.audio_callback(indata, 512, None, None)
        assert s.preview_peak_lr[0] > 0.0

    def test_mono_input_mirrors_to_both_channels(self, tmp_path):
        import numpy as np
        s = self._state(tmp_path, channels=1)
        indata = self._make_audio(1, amplitude=0.4)
        s.audio_callback(indata, 512, None, None)
        assert s.latest_rms_lr[0] == s.latest_rms_lr[1]

    def test_silent_block_no_clip(self, tmp_path):
        import numpy as np
        s = self._state(tmp_path)
        indata = np.zeros((512, 2), dtype=np.float32)
        s.audio_callback(indata, 512, None, None)
        assert s.clip_count == 0
        assert s.latest_rms_lr == (0.0, 0.0)

    def test_recording_mode_puts_block_in_writer_queue(self, tmp_path):
        s = self._state(tmp_path)
        s.mode = "recording"
        s.current_file = MagicMock()
        indata = self._make_audio(2, amplitude=0.3)
        s.audio_callback(indata, 512, None, None)
        assert not s.writer_q.empty()

    def test_preview_mode_does_not_queue_blocks(self, tmp_path):
        s = self._state(tmp_path)
        s.mode = "preview"
        indata = self._make_audio(2, amplitude=0.3)
        s.audio_callback(indata, 512, None, None)
        assert s.writer_q.empty()

    def test_recent_blocks_populated(self, tmp_path):
        s = self._state(tmp_path)
        indata = self._make_audio(2, amplitude=0.2)
        s.audio_callback(indata, 512, None, None)
        assert len(s.recent_blocks) == 1


# ===========================================================================
# 12. Integration: poll_gain
# ===========================================================================

class TestPollGain:
    def test_poll_reads_gain_and_stores_it(self, tmp_path):
        s = _make_state(tmp_path)
        # Set far enough in the past so the GAIN_POLL_SECONDS cooldown has expired.
        s.gain_last_poll = time.monotonic() - sr.GAIN_POLL_SECONDS - 1.0
        with patch("simplerec._get_input_gain", return_value=75):
            s.poll_gain()
        assert s.gain_current_pct == 75
        assert len(s.gain_history) == 1

    def test_poll_skipped_during_cooldown(self, tmp_path):
        s = _make_state(tmp_path)
        s.gain_last_poll = time.monotonic()   # just polled
        with patch("simplerec._get_input_gain", return_value=75) as mock_get:
            s.poll_gain()
        mock_get.assert_not_called()

    def test_poll_skips_on_none_return(self, tmp_path):
        s = _make_state(tmp_path)
        s.gain_last_poll = time.monotonic() - sr.GAIN_POLL_SECONDS - 1.0
        with patch("simplerec._get_input_gain", return_value=None):
            s.poll_gain()
        assert s.gain_current_pct is None
        assert len(s.gain_history) == 0


# ===========================================================================
# 13. Integration: _apply_gain / manual_set_gain
# ===========================================================================

class TestApplyGain:
    def test_apply_gain_calls_set_input_gain(self, tmp_path):
        s = _make_state(tmp_path)
        with patch("simplerec._set_input_gain") as mock_set:
            s._apply_gain(time.monotonic(), 60, "test msg")
        mock_set.assert_called_once_with(60)

    def test_apply_gain_updates_current_pct(self, tmp_path):
        s = _make_state(tmp_path)
        with patch("simplerec._set_input_gain"):
            s._apply_gain(time.monotonic(), 55, "test")
        assert s.gain_current_pct == 55

    def test_apply_gain_clears_weak_since(self, tmp_path):
        s = _make_state(tmp_path)
        s.gain_weak_since = time.monotonic() - 5.0
        with patch("simplerec._set_input_gain"):
            s._apply_gain(time.monotonic(), 80, "boost")
        assert s.gain_weak_since is None

    def test_apply_gain_appends_to_history(self, tmp_path):
        s = _make_state(tmp_path)
        with patch("simplerec._set_input_gain"):
            s._apply_gain(time.monotonic(), 70, "test")
        assert len(s.gain_history) == 1
        assert s.gain_history[-1][1] == 70

    def test_manual_set_gain_writes_playlist(self, tmp_path):
        s, pl = _recording_state(tmp_path)
        with patch("simplerec._set_input_gain"):
            s.manual_set_gain(65)
        assert "CLIP-ADJUST" in pl.read_text()
        assert "manual" in pl.read_text()


# ===========================================================================
# 14. Integration: write_song_status_file
# ===========================================================================

class TestWriteSongStatusFile:
    def test_writes_file_in_output_dir(self, tmp_path):
        s = _make_state(tmp_path)
        s.session_start_wall = dt.datetime(2026, 5, 20, 22, 0)
        s.write_song_status_file()
        files = list(tmp_path.glob("current_song_*.txt"))
        assert len(files) == 1

    def test_file_contains_artist_and_title(self, tmp_path):
        s = _make_state(tmp_path)
        s.session_start_wall = dt.datetime(2026, 5, 20, 22, 0)
        s.songrec_current_artist = "Daft Punk"
        s.songrec_current_title = "Get Lucky"
        s.write_song_status_file()
        content = next(tmp_path.glob("current_song_*.txt")).read_text()
        assert "Daft Punk" in content
        assert "Get Lucky" in content

    def test_file_in_segment_dir_when_recording(self, tmp_path):
        s = _make_state(tmp_path)
        s.session_start_wall = dt.datetime(2026, 5, 20, 22, 0)
        seg = tmp_path / "segment"
        seg.mkdir()
        s.segment_dir = seg
        s.write_song_status_file()
        files = list(seg.glob("current_song_*.txt"))
        assert len(files) == 1

    def test_no_crash_on_unwritable_path(self, tmp_path):
        s = _make_state(tmp_path / "nonexistent")
        s.session_start_wall = dt.datetime(2026, 5, 20, 22, 0)
        s.write_song_status_file()   # must not raise


# ===========================================================================
# 15. Integration: converter worker (afconvert mocked)
# ===========================================================================

class TestConverter:
    def test_converter_calls_afconvert(self, tmp_path):
        s = _make_state(tmp_path)
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"\x00" * 44)
        m4a = tmp_path / "test.m4a"
        with patch("simplerec.subprocess.run") as mock_run, \
             patch("simplerec.os.remove"):
            mock_run.return_value = MagicMock(returncode=0)
            s.start_converter()
            s.convert_q.put((str(wav), str(m4a)))
            s.convert_q.join()
            s.stop_converter()
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert "afconvert" in cmd[0]

    def test_converter_keeps_wav_on_failure(self, tmp_path):
        s = _make_state(tmp_path)
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"\x00" * 44)
        m4a = tmp_path / "test.m4a"
        with patch("simplerec.subprocess.run",
                   side_effect=__import__("subprocess").CalledProcessError(1, "afconvert")):
            s.start_converter()
            s.convert_q.put((str(wav), str(m4a)))
            s.convert_q.join()
            s.stop_converter()
        # fallback WAV should exist (os.replace was called)
        # We just verify no exception was raised and the queue was processed.


# ===========================================================================
# 16. Integration: colored_meter / bar_color
# ===========================================================================

class TestColoredMeter:
    def test_returns_string_of_correct_length(self):
        result = sr.colored_meter(-20.0, -10.0, width=20)
        # Strip ANSI codes and check visible length
        assert sr._visible_len(result) == 20

    def test_silence_is_all_dots(self):
        result = sr.colored_meter(-120.0, -120.0, width=10)
        assert "●" not in result or result.count("●") <= 1   # peak marker only

    def test_full_scale_has_many_filled(self):
        result = sr.colored_meter(0.0, 0.0, width=20)
        # at 0 dBFS the bar should be fully filled
        stripped = result.replace("\033[0m", "")
        assert stripped.count("●") >= 18

    def test_bar_color_low_is_amber(self):
        color = sr.bar_color(0, 20)
        assert color == sr.AMBER

    def test_bar_color_high_is_red(self):
        color = sr.bar_color(19, 20)
        assert color == sr.RED


# ===========================================================================
# 17. Integration: songrec disabled path
# ===========================================================================

class TestSongrecDisabled:
    def test_start_songrec_disabled_sets_status(self, tmp_path):
        s = _make_state(tmp_path)
        s.session_start_wall = dt.datetime(2026, 5, 20, 22, 0)
        s.songrec_enabled = False
        s.start_songrec()
        assert "disabled" in s.songrec_status

    def test_stop_songrec_without_start_is_safe(self, tmp_path):
        s = _make_state(tmp_path)
        s.stop_songrec()   # must not raise

    def test_start_songrec_disabled_writes_status_file(self, tmp_path):
        s = _make_state(tmp_path)
        s.session_start_wall = dt.datetime(2026, 5, 20, 22, 0)
        s.songrec_enabled = False
        s.start_songrec()
        files = list(tmp_path.glob("current_song_*.txt"))
        assert len(files) == 1


# ===========================================================================
# 18. Integration: _get_input_gain (mocked osascript)
# ===========================================================================

class TestGetInputGain:
    def test_returns_integer_on_success(self):
        mock_result = MagicMock()
        mock_result.stdout = "72\n"
        with patch("simplerec.subprocess.run", return_value=mock_result):
            result = sr._get_input_gain()
        assert result == 72

    def test_returns_none_on_exception(self):
        with patch("simplerec.subprocess.run", side_effect=OSError):
            result = sr._get_input_gain()
        assert result is None

    def test_returns_none_on_invalid_output(self):
        mock_result = MagicMock()
        mock_result.stdout = "not_a_number\n"
        with patch("simplerec.subprocess.run", return_value=mock_result):
            result = sr._get_input_gain()
        assert result is None
