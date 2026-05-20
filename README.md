# simplerec

A macOS audio recorder that captures stereo sound in M4A format with integrated song recognition via Shazam and optional DJ webcam photos. Set how long you want to record, give your files a prefix, and hit go — simplerec handles the rest.

**What simplerec does:**
- Records stereo audio as high-quality M4A files
- Recognises songs automatically via Shazam in the background
- Saves each recording segment in its own time-stamped subfolder
- Takes an optional webcam photo every 15 minutes to identify DJs
- Controls the macOS input gain automatically (auto-gain)
- Never stops on its own — cycles automatically into new segments
- Updates itself in one click (press `U` while recording)

---

## Installation — Step by Step (no Terminal required)

### Step 1 — Download the project

1. Open this page in your browser: **https://github.com/joergkuehnelt/simplerec**
2. Click the green **Code** button near the top right
3. Click **Download ZIP**
4. A file called `simplerec-main.zip` will appear in your **Downloads** folder

### Step 2 — Unpack the ZIP

1. Open your **Downloads** folder in Finder
2. Double-click `simplerec-main.zip`
3. A folder called `simplerec-main` appears next to it
4. Move this folder to wherever you want to keep it (e.g. your Desktop or Documents)

### Step 3 — Run the Installer

1. Open the `simplerec-main` folder in Finder
2. Double-click **`Install simplerec.command`**

**If macOS shows a "could not be verified" warning** — this is expected for files downloaded from the internet. To allow it:

> #### How to bypass Gatekeeper via System Settings (recommended — no Terminal needed)
>
> 1. Double-click `Install simplerec.command` in Finder
> 2. macOS shows a warning dialog — click **OK** (or **Done**) to dismiss it
> 3. Open the **Apple menu ()** → **System Settings**
> 4. Click **Privacy & Security** in the left sidebar
> 5. Scroll down to the **Security** section
> 6. You will see a message like *"Install simplerec.command was blocked…"* — click **Open Anyway**
> 7. Enter your Mac password when prompted
> 8. Double-click the file again — it will now open normally
>
> This only needs to be done once per file.

> #### Alternative: Right-click in Finder (may work on older macOS)
> Right-click (or hold **Ctrl** and click) the file → choose **Open** → click **Open** in the dialog.

> #### Alternative: Terminal (always works)
> Open Terminal and run:
> ```
> xattr -cr ~/Downloads/simplerec-main
> ```
> Then double-click the file again.

3. A Terminal window opens and the installer starts
4. Follow the on-screen prompts — you may be asked for your Mac password once (for Homebrew)
5. The installer will automatically:
   - Detect whether you have an Intel or Apple Silicon Mac
   - Install Homebrew (macOS package manager) if not already present
   - Install Python 3 via Homebrew
   - Install the required Python packages (sounddevice, soundfile, numpy, shazamio, psutil)
   - Install **imagesnap** via Homebrew (for optional DJ webcam photos)
   - Create the **`Start simplerec.command`** file in the same folder
6. When done, press **ENTER** to close the Terminal window

> Installation takes 5–15 minutes the first time (mostly Homebrew). On subsequent runs it only updates packages and completes in seconds.

### Step 4 — Start Recording

1. Open the `simplerec-main` folder in Finder
2. Double-click **`Start simplerec.command`**
3. If macOS blocks it the first time: follow the same **System Settings → Privacy & Security → Open Anyway** steps described in Step 3 above
4. A Terminal window opens with the simplerec interface
5. Answer the startup questions:
   - **How many minutes to record?** — enter a number between 1 and 120
   - **Filename prefix** — enter a label for your files (e.g. `Party2026_`) or press ENTER to skip
   - **Output folder** — confirm the suggested folder or enter your own path
   - **DJ webcam photos** — choose whether to take a photo every 15 min to identify DJs
   - **Input device** — choose your microphone or audio interface from the list
6. A 5-second preview runs so you can check your audio levels
7. Recording starts automatically after the preview

---

## Usage

Once recording is running, you control it with these keys in the Terminal window:

| Key | Action |
|-----|---------|
| `S` | Stop the current segment, save it, switch to PAUSE |
| `R` | Restart — save current segment and begin a new one |
| `Q` | Save the current segment and quit |
| `P` | Toggle PLAYLIST-ONLY mode (song log only, no M4A file) |
| `U` | Save the current segment, quit, and launch the updater |
| `A` | Toggle AUTOGAIN (AUTO ↔ MANUAL) |
| `2` `4` `6` `8` `0` | In MANUAL mode: set input gain to 20 / 40 / 60 / 80 / 100 % |
| `Ctrl+C` | Emergency stop (segment is saved where possible) |

When the set recording duration is reached, simplerec **automatically saves the file and starts a new recording** with the same settings — it never stops on its own.

### Auto-gain

simplerec controls the macOS system input gain automatically while recording:

- **Very weak signal** (< −50 dBFS for a few seconds) → input gain raised to **100 %**
- **Weak signal** (< −35 dBFS for a few seconds) → input gain raised to **80 %**
- **Clipping danger** (peak ≥ −2 dBFS or active clipping) → input gain reduced in 20 %-steps (minimum 20 %)
- Every gain change is recorded as a `CLIP-ADJUST` line in the playlist file with timestamp
- A 5×50 dot-grid in the UI shows the last 10 minutes of gain history (rows = 20 % gain buckets; colours: 100 % red, 80 % bright red, 60 % yellow, 40 / 20 % green)
- Press `A` to switch to **MANUAL** mode and pick a fixed gain with the number keys; press `A` again to return to AUTO

### Updating simplerec

There are two ways to update to the latest version:

**From within the app** — press `U` while recording. simplerec saves the current segment, quits, and automatically opens `Update simplerec.command`, which downloads the latest code from GitHub and re-runs the installer.

**Manually** — double-click **`Update simplerec.command`** in Finder at any time. It downloads the latest ZIP from GitHub, overwrites the local files, and launches the installer in the same Terminal window.

---

## Output Files

All files are saved to `~/simplerec - recordings` by default. You can choose a different root folder at startup.

**Each recording segment gets its own subfolder** named `YYYYMMDD-HHMM` (the segment start time). All files belonging to that segment are saved inside it:

| File | Description |
|------|-------------|
| `YYYYMMDD-HHMM/[prefix]YYYYMMDD-startHHMM-endHHMM.m4a` | Audio recording |
| `YYYYMMDD-HHMM/[prefix]YYYYMMDD-startHHMM-endHHMM.txt` | Playlist (CSV: time;elapsed;artist;title;genre;year) |
| `YYYYMMDD-HHMM/[prefix]photo_YYYYMMDD-HHMMSS.jpg` | DJ webcam photo (if enabled) |

While a segment is in progress, a temporary `[prefix]current_song_*.txt` live-status file is also written to the segment folder. It is **removed automatically** when the segment is saved.

**Example** with prefix `Party2026_`, root folder `~/simplerec - recordings`, recording started at 22:30:
```
~/simplerec - recordings/
└── 20260520-2230/
    ├── Party2026_20260520-start2230-end2330.m4a
    ├── Party2026_20260520-start2230-end2330.txt
    └── Party2026_photo_20260520-223000.jpg
```

When the segment duration is reached, a new subfolder is created for the next segment:
```
└── 20260520-2330/
    ├── Party2026_20260520-start2330-end0030.m4a
    └── ...
```

In **PLAYLIST-ONLY** mode (`P`) no audio file is written, only the playlist `.txt`.

---

## Functionality

simplerec is a command-line audio recorder for macOS with the following built-in features:

**Audio capture**
- Lists all available audio input devices with a short live level test at startup
- Records in stereo (falls back to mono if the device does not support stereo)
- Saves recordings as high-quality M4A (AAC) files using macOS's built-in `afconvert` tool — no ffmpeg needed
- Splits long recordings into segments (up to 120 minutes per segment)
- Conversion from raw WAV to M4A happens in a background thread so recording is never interrupted
- Prevents display and idle sleep automatically via macOS `caffeinate` for the entire recording session

**User interface**
- Full-screen terminal UI that refreshes in real time
- Stereo VU meter with peak-hold indicators and colour coding (green → yellow → red)
- Clipping warning with event counter
- Dedicated **Auto-gain** box with a 5×50 dot-grid showing the last 10 minutes of input-gain history
- Live display of elapsed recording time, channel count, device name, and output folder
- Status line shows `● REC` (red) or `‖ PAUSE` (amber) at a glance
- Three key-bars at the bottom: transport (S/R/Q), utility (P/U), and gain controls (A/2/4/6/8/0)

**Auto-gain (macOS system input volume)**
- Continuously monitors the input level while recording
- Boosts the system input gain to 100 % when the signal is very weak, 80 % when weak
- Reduces gain in 20 %-steps when clipping danger is detected (min 20 %)
- Logs every adjustment as a `CLIP-ADJUST` entry in the playlist file (with timestamp)
- Can be toggled off with `A`; in MANUAL mode the keys `2 4 6 8 0` set the gain to 20–100 %

**Song recognition**
- Integrated Shazam-based song recognition via ShazamIO
- Runs continuously in the background — every few seconds a short audio snippet is analysed
- Displays the currently playing song (artist, title, genre, year, album) in the terminal
- Maintains a deduplicated per-segment playlist file (skips repeats of the same song)
- Requires an active internet connection; if the connection is unavailable the recognition retries silently without interrupting recording

**Session management**
- Asks for recording duration (1–120 min), filename prefix, DJ photo preference, and input device at startup
- Automatically saves the current segment and starts a new one when the duration is reached
- **Each segment gets its own `YYYYMMDD-HHMM` subfolder** — all files (`.m4a`, `.txt`, photos) are saved inside it
- The live `current_song_*.txt` status file is auto-deleted after each segment is saved

**DJ webcam photos**
- Optional feature: asked at startup whether to enable
- Takes a photo every 15 minutes using the built-in webcam via `imagesnap`
- Saved as `[prefix]photo_YYYYMMDD-HHMMSS.jpg` in the current segment subfolder
- 5-second countdown shown in the UI (blinking red “SMILE IN X seconds!”)
- Status shown in the UI: `DJ PIC: ON / OFF`
- Requires `imagesnap` (installed automatically by the installer via Homebrew)

**Notes**
- Requires macOS (uses the built-in `afconvert` tool for M4A encoding)
- Keyboard controls require a real macOS Terminal window (not an IDE console)
- Run `python3 simplerec.py --help-messages` for extended in-app help

---

## Requirements

- macOS (Intel or Apple Silicon, macOS 10.15 Catalina or later)
- Python 3.8 or later (installed automatically by the installer)
- Internet connection (for song recognition)
- `psutil` — optional, enables CPU/RAM display in the UI (installed automatically)
- `shazamio` — optional, enables song recognition (installed automatically)
- `imagesnap` — optional, enables DJ webcam photos (installed automatically via Homebrew)

