# Spoolman API Plugin for FilaMan

A FilaMan plugin that exposes a fully Spoolman-compatible REST API, allowing external tools like **Moonraker**, **OctoPrint** and others to use FilaMan as a drop-in replacement for Spoolman. Unlike Spoolman itself, this plugin includes IP-based access control — letting you restrict which devices are allowed to reach the API.

## Features

- Full Spoolman API v1 compatibility (all endpoints)
- Vendor, Filament and Spool CRUD operations
- Query filtering, sorting and pagination
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

## API

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
| PUT | `/spool/{id}/use` | Use filament from spool |
| PUT | `/spool/{id}/measure` | Measure spool weight |
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
