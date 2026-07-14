# Usage

Day-to-day operation of the photo frame, plus Raspberry Pi deployment.

## Setting Up the Virtual Environment

The app's dependencies must be installed into a Python virtual environment (venv), not the system Python. On Raspberry Pi OS (Debian Bookworm and later) the system Python is managed by apt, so `pip install` there fails with an `externally-managed-environment` error ([PEP 668](https://peps.python.org/pep-0668/)). A venv keeps the app's packages isolated from the system.

```sh
cd /home/pi/photo-frame
python3 -m venv venv
source venv/bin/activate            # activate for interactive use
pip install -r requirements.txt
```

If `python3 -m venv` reports that the module is missing, install it first:

```sh
sudo apt install python3-venv
```

For systemd and cron you do **not** need to activate the venv -- invoke its Python directly by full path (e.g. `/home/pi/photo-frame/venv/bin/python indexer.py`) and it uses the venv automatically. The service examples below already do this.

## Running the Indexer

The indexer scans the photo directory, reads metadata with exiftool, and populates (or updates) the local SQLite database.

```sh
python3 indexer.py                  # uses config.yaml in current directory
python3 indexer.py /path/to/config.yaml   # explicit config path
```

The indexer is idempotent -- run it as often as you like. It upserts photos that changed and removes photos that were deleted from disk.

### Automatic Indexing with a Systemd Timer

Create `/etc/systemd/system/photo-indexer.service`:

```ini
[Unit]
Description=Photo Frame Indexer
After=network.target

[Service]
Type=oneshot
User=pi
WorkingDirectory=/home/pi/photo-frame
ExecStart=/home/pi/photo-frame/venv/bin/python indexer.py
```

Create `/etc/systemd/system/photo-indexer.timer`:

```ini
[Unit]
Description=Run photo indexer every 30 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=30min

[Install]
WantedBy=timers.target
```

Enable and start the timer:

```sh
sudo systemctl enable --now photo-indexer.timer
```

Check status and logs:

```sh
systemctl status photo-indexer.timer    # next run time
systemctl list-timers                   # all active timers
journalctl -u photo-indexer             # indexer output
```

### Alternative: Cron

If you prefer cron:

```
*/30 * * * * cd /home/pi/photo-frame && /home/pi/photo-frame/venv/bin/python indexer.py >> /tmp/indexer.log 2>&1
```

Adjust paths to match your setup.

## The Web App (Flask)

The frame is a small [Flask](https://flask.palletsprojects.com/) web application (`app.py`). Flask is a lightweight Python web framework -- it maps URLs to Python functions and renders HTML templates. We use it because the frame is naturally a client/server split:

- The **server** (Flask) owns the slideshow state, reads photo metadata from SQLite, serves image files from the NAS mount, and exposes a small JSON API.
- The **clients** are just web pages: the fullscreen slideshow (`/`) shown on the frame's own display, and the phone remote (`/remote`). Because they're web pages, any device on the LAN can act as a remote with no app to install.

Flask keeps this simple -- the whole app is a few hundred lines with only `flask` and `pyyaml` as dependencies, which suits a Raspberry Pi.

`app.py` runs Flask's built-in development server (`app.run(...)`, bound to `0.0.0.0:5000`). That server is not hardened for the public internet, but it's perfectly fine here: the frame lives on your home LAN and serves a handful of trusted devices. If you ever exposed it more widely you'd put a production WSGI server (e.g. gunicorn) in front, but for a picture frame that's unnecessary.

## Slideshow Display

Open `http://localhost:5000` in any browser. The slideshow:

- Advances automatically based on `display.interval_secs` (default 30s)
- Applies the configured fit mode, transition, and background style
- Responds to schedule changes (night dimming, power save blanking)
- Pauses/resumes and changes photos in response to remote control actions

### Fit Modes

- **fit** -- scales the photo to fit within the screen, with letterboxing or pillarboxing as needed
- **fill** -- crops the photo to fill the screen entirely
- **ken_burns** -- fills the screen with a slow pan/zoom animation

### Transitions

- **fade** -- crossfade between photos
- **slide** -- the new photo slides in from the right
- **none** -- instant switch (set `transition_duration_secs: 0`)

### Background

When `background` is set to `"blur"`, a blurred and dimmed version of the current photo fills the letterbox/pillarbox area. Set to `"black"` for a plain black background.

## Remote Control

Open `http://<frame-ip>:5000/remote` on your phone. The remote provides:

- **Playlist selector** -- switch between playlists defined in `config.yaml`
- **Play/pause** -- pause or resume the slideshow timer
- **Prev/next** -- skip to the previous or next photo
- **Rating editor** -- tap the current photo to rate it 1-5 stars. The rating is written back to the JPEG file on disk, so Lightroom can pick it up via "Read Metadata from File"
- **History strip** -- scrollable row of recently displayed photos. Tap to view full size

The remote polls the frame every 3 seconds to stay in sync.

## Rating Writeback

When you change a photo's rating from the remote:

1. The SQLite database is updated immediately
2. exiftool writes the new rating to the JPEG's XMP metadata on disk
3. In Lightroom, select the photo and choose **Metadata > Read Metadata from File** to import the change

## Night Mode and Power Save

Configured under `schedule` in `config.yaml`:

- **Night mode** dims the display during the configured window (e.g., 21:00-06:00) by fading in a translucent black overlay. The slideshow keeps running at reduced brightness.
- **Power save** blanks the display entirely during its window (e.g., 23:00-07:00). On a Raspberry Pi, you can extend this with HDMI CEC (`cec-client`) or DPMS (`xset dpms force off`) to actually power down the monitor.

The slideshow checks the schedule every 60 seconds.

## Raspberry Pi Deployment

### Prerequisites

```sh
sudo apt install libimage-exiftool-perl chromium-browser unclutter
```

- **unclutter** - Hides the mouse cursor after some inactivity
- **chromium-browser** - The frame's display is a Chromium window in kiosk mode pointed at the slideshow page (see [Chromium Kiosk Service](#chromium-kiosk-service))
- **libimage-exiftool-perl** - Read/write image file EXIF information

### Web App Service

Run the Flask app under systemd so it starts on boot and restarts if it ever crashes. Create `/etc/systemd/system/photo-frame.service`:

```ini
[Unit]
Description=Photo Frame Web App
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/photo-frame
ExecStart=/home/pi/photo-frame/venv/bin/python app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Key points:

- `ExecStart` calls the venv's Python by full path, so no activation is needed (see [Setting Up the Virtual Environment](#setting-up-the-virtual-environment)).
- `Type=simple` -- the app runs in the foreground and systemd tracks it directly.
- `Restart=always` with `RestartSec=5` restarts the app 5 seconds after any exit, so a transient error self-heals.
- `WantedBy=multi-user.target` starts the service at boot once networking is up.

Enable and start it:

```sh
sudo systemctl daemon-reload             # after editing the unit file
sudo systemctl enable --now photo-frame
```

Check status and follow logs:

```sh
systemctl status photo-frame             # running state, recent output
journalctl -u photo-frame -f             # live log (Flask request/error output)
```

### Chromium Kiosk Service

Ready-to-copy unit files are in [`deploy/`](deploy/). The frame's display is a
fullscreen Chromium window pointed at the slideshow. Current Raspberry Pi OS
(Bookworm/Trixie) runs a **Wayland** session by default on a Pi 5, so that's the
primary setup below; see [X11 (older setups)](#x11-older-setups) if you're on X11.

Confirm which session you're in first:

```sh
echo $XDG_SESSION_TYPE          # prints "wayland" or "x11"
```

#### Wayland (default on Pi 5)

Copy [`deploy/photo-frame-kiosk.service`](deploy/photo-frame-kiosk.service) to
`/etc/systemd/system/`:

```ini
[Unit]
Description=Photo Frame Kiosk (Chromium, Wayland)
After=photo-frame.service graphical.target
Wants=photo-frame.service

[Service]
Type=simple
User=pi
# The kiosk must reach the running compositor. XDG_RUNTIME_DIR uses the user's
# UID (1000 for the first user); check the socket name with:
#   ls /run/user/1000/wayland-*      (often wayland-0 or wayland-1)
Environment=XDG_RUNTIME_DIR=/run/user/1000
Environment=WAYLAND_DISPLAY=wayland-0
Environment=XDG_SESSION_TYPE=wayland
ExecStart=/usr/bin/chromium \
  --kiosk --noerrdialogs --disable-infobars \
  --disable-session-crashed-bubble --disable-component-update \
  --enable-gpu-rasterization --ignore-gpu-blocklist --enable-zero-copy --use-gl=egl \
  --ozone-platform=wayland --enable-features=UseOzonePlatform \
  http://localhost:5000
Restart=always
RestartSec=10

[Install]
WantedBy=graphical.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now photo-frame-kiosk
```

Notes:

- The package is **`chromium`** on current Pi OS (it was `chromium-browser` on
  older releases).
- `--ozone-platform=wayland --enable-features=UseOzonePlatform` tells Chromium to
  render natively on Wayland instead of going through XWayland.
- Adjust `User`, the UID in `XDG_RUNTIME_DIR`, and `WAYLAND_DISPLAY` to match your
  system. If the service can't reach the display, the compositor may name the
  socket `wayland-1`; check with the `ls` command in the comment.
- Cursor hiding: `unclutter` is X11-only and does nothing on Wayland. On Wayland
  the pointer generally stays out of the way in kiosk mode; if it lingers, hide it
  through your compositor (labwc/Wayfire) settings.
- **Testing without the service:** run [`deploy/run-kiosk.sh`](deploy/run-kiosk.sh)
  from inside your desktop session. It starts `app.py` (unless something is already
  on port 5000), launches Chromium with the same flags (auto-detecting Wayland vs
  X11), and shuts the app back down when you quit — so you can iterate without
  `systemctl`. Exit with **Alt+F4** or **Ctrl+C**.

#### Exiting the kiosk

Chromium's `--kiosk` mode disables `F11`; use **Alt+F4** to close the window (or
**Ctrl+C** in the terminal if you launched it with `run-kiosk.sh`).

Watch out for the auto-restart: the service sets `Restart=always`, which is right
for a frame that should always be showing photos, but it means closing the browser
while the *service* is running just makes systemd relaunch it after `RestartSec`
(10s). To get out and stay out:

- From SSH or a text console (**Ctrl+Alt+F2**), stop the service — a manual `stop`
  is not undone by `Restart=always`:

  ```sh
  sudo systemctl stop photo-frame-kiosk
  ```

- While actively developing on the Pi, change `Restart=always` to
  `Restart=on-failure` in the unit. A clean exit (Alt+F4 → exit 0) then leaves the
  kiosk closed, while genuine crashes still restart it. Switch back to `always` for
  normal use.
- Keep SSH enabled (`raspi-config` → *Interface Options*) so you always have a way
  in when the kiosk owns the screen.

#### GPU Acceleration (important for 4K)

The GPU flags on the `ExecStart` line force Chromium to use the Pi's GPU for
compositing and rasterization:

- `--enable-gpu-rasterization` — rasterize layers on the GPU, not the CPU
- `--ignore-gpu-blocklist` — don't silently fall back to software rendering
- `--enable-zero-copy` — upload textures without an extra CPU copy
- `--use-gl=egl` — use the native EGL/GLES driver

Without these, Chromium often lands on software rasterization on Pi OS, which
pegs the GPU (or CPU) and makes fades, slides, and Ken Burns at 4K stutter.

Verify acceleration is actually on: open `chrome://gpu` in the kiosk (or a
regular Chromium window on the Pi) and confirm **Graphics Feature Status** shows
*Rasterization: Hardware accelerated* and *Compositing: Hardware accelerated*.

#### X11 (older setups)

On an X11 session, use the `chromium-browser` binary, drop the Wayland flags, and
hide the cursor with `unclutter`. The rest of the unit is the same:

```ini
[Service]
Type=simple
User=pi
Environment=DISPLAY=:0
ExecStartPre=/usr/bin/unclutter -idle 0.1 -root &
ExecStart=/usr/bin/chromium-browser --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --disable-component-update --enable-gpu-rasterization --ignore-gpu-blocklist --enable-zero-copy --use-gl=egl http://localhost:5000
Restart=always
RestartSec=10
```

### Disable Screen Blanking

Prevent the Pi from turning off the display on its own.

**Wayland (labwc — default on Pi 5):** disable screen blanking and DPMS in
`~/.config/labwc/rc.xml` (or the equivalent Wayfire `[idle]` settings), for
example by setting the idle timeouts to `0`. The simplest option on Raspberry Pi
OS is the built-in tool: **Screen Blanking** can be turned off under
`raspi-config` → *Display Options*, or in the Pi's Screen Configuration app.

**X11:** add to `/etc/xdg/lxsession/LXDE-pi/autostart`:

```sh
@xset s off
@xset -dpms
@xset s noblank
```

### Database Location

The SQLite database defaults to `frame.db` in the working directory. Override with the `FRAME_DB` environment variable:

```sh
FRAME_DB=/home/pi/photo-frame/frame.db python3 app.py
```

The database is a derived cache of photo metadata -- it can be safely deleted and rebuilt at any time:

```sh
rm frame.db
python3 indexer.py
```

Keep the database on the Pi's local filesystem (SD card or USB drive), not on the NAS mount -- SQLite's file locking is unreliable over network filesystems.
