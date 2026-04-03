# Photo Frame

A DIY digital picture frame that displays photos from a Synology NAS on a Raspberry Pi (or any Linux machine). Photos are managed in Adobe Lightroom Classic -- the frame reads their embedded metadata (ratings, keywords, people tags) and uses it to drive configurable playlists. A phone-friendly remote control lets you switch playlists, skip photos, and change ratings from the couch.

```
Lightroom Classic --> Export JPEG w/ XMP --> Synology NAS (SMB share)
                                                  |
                                             SMB mount
                                                  |
                                         Raspberry Pi / Linux
                                           indexer (cron)
                                                |
                                             SQLite
                                                |
                                           Flask web app
                                                |
                                         Chromium (kiosk)
```

## Requirements

- Python 3.10+
- [exiftool](https://exiftool.org/) (reads/writes photo metadata)
- Flask and PyYAML (see `requirements.txt`)

On Debian/Ubuntu/Raspberry Pi OS:

```sh
sudo apt install libimage-exiftool-perl
pip install -r requirements.txt
```

## Quick Start

1. **Configure** -- edit `config.yaml` and set `nas.mount_point` to your photo directory:

   ```yaml
   nas:
     mount_point: "/mnt/photos"   # NAS mount, or any local folder
   ```

2. **Index photos** -- scan the directory and populate the SQLite database:

   ```sh
   python3 indexer.py
   ```

3. **Start the web app**:

   ```sh
   python3 app.py
   ```

4. **Open the slideshow** at `http://localhost:5000` and the **remote control** at`http://localhost:5000/remote` (from your phone on the same network).

## Configuration

All settings live in `config.yaml`.

### Display

```yaml
display:
  fit_mode: "fit"              # fit (letterbox) | fill (crop) | ken_burns (slow pan/zoom)
  background: "blur"           # black | blur (blurred version of current photo)
  transition: "fade"           # fade | slide | none
  transition_duration_secs: 1.5
  interval_secs: 30
  shuffle: true
  show_info_overlay: false     # show rating, people, keywords on screen
```

### Playlists

Each playlist defines a `filter` that selects photos from the index. Filters can match on `rating`, `people`, `keywords`, and `orientation`. Playlists can also override display settings like `fit_mode`.

```yaml
playlists:
  favorites:
    name: "Favorites (4+ stars)"
    filter:
      rating: { gte: 4 }
    fit_mode: "ken_burns"

  family:
    name: "Family Photos"
    filter:
      people: { any: ["Alice", "Bob", "Charlie"] }

  vacation:
    name: "Vacation"
    filter:
      keywords: { any: ["vacation", "travel"] }
```

**Filter operators:**
- `rating`: `gte`, `lte`, `eq`
- `people`: `any` (match if any listed person appears), `all` (must include every listed person)
- `keywords`: `any`, `all`
- `orientation`: `"landscape"`, `"portrait"`, `"square"`

Multiple filters in one playlist are combined with AND.

### Schedule

```yaml
schedule:
  night_mode:
    enabled: true
    start: "21:00"
    end: "06:00"
    brightness: 0.3       # CSS brightness filter, 0.0-1.0
  power_save:
    enabled: true
    start: "23:00"
    end: "07:00"           # blanks the display entirely
```

### History

```yaml
history:
  enabled: true
  max_entries: 500
  allow_rating_changes: true
```

## NAS Mount

The photo directory is typically a network share from a Synology NAS (or similar). Both NFS and SMB work. NFS has lower overhead and is a better fit for a dedicated Linux device like a Pi. SMB is the better choice if the share is also accessed from Windows or Mac and you need user-level authentication.

The mount needs read-write access so that rating changes can be written back to the JPEG files.

### NFS

Enable NFS on the Synology: **Control Panel > Shared Folder > (select folder) > Edit > NFS Permissions**. Add a rule for the Pi's IP with read/write access.

Install the NFS client on the Pi:

```sh
sudo apt install nfs-common
```

Add to `/etc/fstab`:

```
nas-ip:/volume1/photos /mnt/photos nfs rw,soft,intr,noatime 0 0
```

Then mount:

```sh
sudo mkdir -p /mnt/photos
sudo mount /mnt/photos
```

`soft,intr` lets operations fail gracefully if the NAS is unreachable, rather than hanging the Pi. `noatime` avoids unnecessary write traffic.

### SMB

Add to `/etc/fstab`:

```
//nas-ip/photos /mnt/photos cifs credentials=/etc/samba/creds,uid=1000,gid=1000,iocharset=utf8 0 0
```

Where `/etc/samba/creds` contains:

```
username=your_user
password=your_password
```

Protect the credentials file:

```sh
sudo chmod 600 /etc/samba/creds
```

## Lightroom Integration

When exporting from Lightroom Classic, enable **"Include All Metadata"** so that ratings, keywords, and people (face) tags are embedded as XMP in the JPEG. The indexer reads:

- `xmp:Rating` (1-5 stars)
- `dc:subject` (keywords)
- `mwg-rs:RegionList` / `RegionName` (people/face tags)

When you change a rating from the remote control, the app writes it back to the JPEG via exiftool. Lightroom can pick up the change with **Metadata > Read Metadata from File**.

## Raspberry Pi Deployment

See [USAGE.md](USAGE.md) for cron setup, systemd services, and Chromium kiosk configuration.

## API

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Fullscreen slideshow |
| `/remote` | GET | Phone remote control UI |
| `/api/playlists` | GET | List playlists |
| `/api/now-playing` | GET | Current photo + display state |
| `/api/control/next` | POST | Skip to next photo |
| `/api/control/prev` | POST | Go to previous photo |
| `/api/control/pause` | POST | Toggle pause |
| `/api/control/playlist/{id}` | POST | Switch playlist |
| `/api/history` | GET | Recent play history |
| `/api/photos/{id}/rating` | PUT | Update photo rating (JSON body: `{"rating": N}`) |
| `/api/schedule` | GET | Current night mode / power save state |
| `/photos/{path}` | GET | Serve photo file |
