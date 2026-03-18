# Usage

Day-to-day operation of the photo frame, plus Raspberry Pi deployment.

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

- **Night mode** reduces display brightness via a CSS filter during the configured window (e.g., 21:00-06:00). The slideshow keeps running at reduced brightness.
- **Power save** blanks the display entirely during its window (e.g., 23:00-07:00). On a Raspberry Pi, you can extend this with HDMI CEC (`cec-client`) or DPMS (`xset dpms force off`) to actually power down the monitor.

The slideshow checks the schedule every 60 seconds.

## Raspberry Pi Deployment

### Prerequisites

```sh
sudo apt install libimage-exiftool-perl chromium-browser unclutter
```

### Web App Service

Create `/etc/systemd/system/photo-frame.service`:

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

```sh
sudo systemctl enable --now photo-frame
```

### Chromium Kiosk Service

Create `/etc/systemd/system/photo-frame-kiosk.service`:

```ini
[Unit]
Description=Photo Frame Kiosk
After=photo-frame.service graphical.target
Wants=photo-frame.service

[Service]
Type=simple
User=pi
Environment=DISPLAY=:0
ExecStartPre=/usr/bin/unclutter -idle 0.1 -root &
ExecStart=/usr/bin/chromium-browser --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --disable-component-update http://localhost:5000
Restart=always
RestartSec=10

[Install]
WantedBy=graphical.target
```

```sh
sudo systemctl enable --now photo-frame-kiosk
```

### Disable Screen Blanking

Prevent the Pi from turning off the display on its own:

```sh
# In /etc/xdg/lxsession/LXDE-pi/autostart, add:
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
