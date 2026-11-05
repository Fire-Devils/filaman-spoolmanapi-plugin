# Spoolman API Plugin for FilaMan

A FilaMan plugin that exposes a fully Spoolman-compatible REST API, allowing external tools like **Moonraker**, **OctoPrint** and others to use FilaMan as a drop-in replacement for Spoolman. Unlike Spoolman itself, this plugin includes IP-based access control — letting you restrict which devices are allowed to reach the API.

## Features

- Full Spoolman API v1 compatibility (all endpoints)
- Vendor, Filament and Spool CRUD operations
- Query filtering, sorting and pagination
- NFC/RFID tag linking for spools, lookup and scan relay for readers
- CSV and JSON export
- IP-based access control (a security layer missing in Spoolman)
- Admin UI for managing the IP allowlist

## Installation

Copy the `spoolmanapi/` folder into your FilaMan plugins directory and restart FilaMan.

## Configuration

### Moonraker

```ini
[spoolman]
server: http://<filaman-host>:8000/spoolman
```

### IP Access Control

By default, all IPs are allowed. To restrict access, open the plugin settings page in the FilaMan admin panel under **Spoolman API** and configure the IP allowlist.

The plugin accepts `extra.card_uids` as a comma-separated string and translates it to/from the
spool tag API without retaining it as a custom field. Native spool RFID tags take precedence.

## API

### Color compatibility

FilaMan stores colors as `#RRGGBB` or `#RRGGBBAA`. This plugin returns six-digit
RGB values for AFC and other consumers that require opaque colors, including
colors nested in spool and event payloads. Spoolman itself supports trailing
alpha; this RGB projection is a consumer compatibility choice.

An RGB filter matches stored colors with any alpha value. An eight-digit filter
matches the exact RGBA value. Updates match the returned color slots rather than
assuming native positions start at one or are contiguous. An unchanged RGB value
preserves its native color, alpha, position and assignment label. Distinct colors
can be reordered without losing alpha or labels. Duplicate RGB values keep their
matching slots. Moved copies with the same RGBA retain that alpha and any common
label; conflicting labels are not guessed. A moved RGB value with multiple
possible alpha values is treated as a new opaque color. Send eight digits to
select an exact alpha, including a trailing `FF` for opaque. When both requested color
values are null or empty, assignments are cleared.

For native single-mode filaments with secondary colors, sending both color fields
updates the primary and secondary colors while retaining single mode. To switch
to multi mode, send `multi_color_hexes` without a nonempty `color_hex`. A fallback
primary can also appear in the secondary list; an edit replaces its unchanged
echo without duplicating the native assignment. If both fields change that same
assignment to different values, the secondary-list value takes precedence.
Omitting the secondary field preserves it and the color mode during a primary edit; explicitly
sending an empty or null secondary field removes those secondary assignments.
Clearing only the primary field removes its assignment while retaining the other
native assignments. Partial clears retain the native color mode. In multi mode,
the secondary list includes every visible assignment, including the primary.
Empty comma-separated tokens are ignored; a list with no colors acts as an empty list.

All Spoolman endpoints are available under:

```
http://<filaman-host>:8000/spoolman/api/v1/
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/info` | API info |
| GET | `/health` | Health check |
| GET/POST | `/vendor` | List / create vendors |
| GET/PATCH/DELETE | `/vendor/{id}` | Get / update / delete vendor |
| GET/POST | `/filament` | List / create filaments |
| GET/PATCH/DELETE | `/filament/{id}` | Get / update / delete filament |
| GET/POST | `/spool` | List / create spools |
| GET/PATCH/DELETE | `/spool/{id}` | Get / update / delete spool |
| POST | `/spool/{id}/tag` | Link an NFC/RFID tag to a spool |
| DELETE | `/spool/{id}/tag/{uid}` | Unlink a tag from a spool |
| PUT | `/spool/{id}/use` | Use filament from spool |
| PUT | `/spool/{id}/measure` | Measure spool weight |
| POST | `/tag/scan` | Resolve a scanned tag and relay it to WebSocket listeners |
| GET | `/tag/reader` | List recently active tag readers |
| GET | `/material` | List materials |
| GET | `/location` | List locations |
| PATCH | `/location/{name}` | Rename location |
| GET/POST | `/setting/{key}` | Get / set settings |
| GET | `/export/spools` | Export spools (CSV/JSON) |
| GET | `/export/filaments` | Export filaments (CSV/JSON) |
| GET | `/export/vendors` | Export vendors (CSV/JSON) |
| POST | `/backup` | Create backup |

## License

See the [FilaMan](https://github.com/Fire-Devils/FilaMan) project for license information.
