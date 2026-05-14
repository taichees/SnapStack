# SnapStack

SnapStack is a local Docker web app for finding burst shots and visually similar
photos stored on a NAS. It groups similar images and recommends the best three
photos to keep from each group.

The first version is intentionally safe: it reads photos from mounted folders,
writes thumbnails and analysis cache to Docker storage, and does not delete
original files.

For implementation notes and design decisions, see
[`docs/project-summary.md`](docs/project-summary.md).

## Features

- Multiple target folders can be configured and selected per scan.
- JPEG, PNG, WebP, HEIC, and HEIF inputs are supported.
- EXIF capture time and perceptual hashes are used to group burst/similar shots.
- Recommendations are scored using lightweight local quality signals:
  - sharpness
  - exposure balance
  - contrast
  - resolution
- A browser UI displays each group, the top recommendations, and all grouped
  photos.

## Quick start

1. Put photos on the host under **`$HOME/snapstack`** (Compose default mount). The
   container sees them as **`/photos/snapstack`**. To use a different host path,
   set `SNAPSTACK_PHOTOS_HOST` when running Compose (see `docker-compose.yml`).

2. `config/snapstack.yml` maps `photo_roots` to that container path (default:
   `snapstack` → `/photos/snapstack`). You normally do not need to change it.

3. Start the app:

   ```bash
   docker compose up --build
   ```

4. Open <http://localhost:8000>, select one or more target folders, and start a
   scan.

## Configuration

`config/snapstack.yml` is mounted into the container at `/config/snapstack.yml`.

```yaml
photo_roots:
  - name: snapstack
    path: /photos/snapstack

recommendation_count: 3
hash_distance_threshold: 8
burst_time_window_seconds: 20
```

Docker maps the host directory to `/photos/snapstack` (default host path
`$HOME/snapstack`, overridable with `SNAPSTACK_PHOTOS_HOST`).

For **local** `uvicorn`, YAML still points at `/photos/snapstack`, which does
not exist on the host—override roots with **`SNAPSTACK_PHOTO_ROOTS`** using the
same host folder:

```bash
SNAPSTACK_PHOTO_ROOTS="$HOME/snapstack" uvicorn app.main:app
```

## Scan and cache behavior

- Photos are rechecked when you press the scan button in the browser.
- Unchanged photos reuse cached analysis from `/data/snapstack.db` and cached
  thumbnails from `/data/thumbnails`.
- Cache rows for deleted photos are cleaned from the database for the selected
  roots during each scan.
- The UI shows the timestamp of the most recently completed scan.

## Development

Python **3.10+** is recommended (the Docker image uses 3.12). After pulling changes, run `pip install -r requirements.txt` again so new dependencies such as `requests` are installed.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export SNAPSTACK_CONFIG=config/snapstack.yml
export SNAPSTACK_DATA_DIR=.data
export SNAPSTACK_PHOTO_ROOTS="$HOME/snapstack"
export SNAPSTACK_UI_LOCAL_PREFIXES="$HOME/snapstack"

# Optional — use a different sandbox folder instead:
#   mkdir -p .data/dev-photo-roots
#   export SNAPSTACK_PHOTO_ROOTS="$(pwd)/.data/dev-photo-roots"
#   export SNAPSTACK_UI_LOCAL_PREFIXES="$(pwd)/.data/dev-photo-roots"

uvicorn app.main:app --reload
```

## Cursor Cloud Agent environment

This repository includes `.cursor/environment.json` and `.cursor/Dockerfile` so
future Cursor Cloud Agents can start with Docker CLI, Docker Compose, and
`python3-venv` available.

The environment starts Docker with:

```bash
sudo service docker start
```

Useful verification commands inside a new Cloud Agent:

```bash
docker --version
docker compose version
sudo service docker start
docker run --rm hello-world
python3 -m venv /tmp/venv-test
/tmp/venv-test/bin/python --version
```

If you use Cursor Web's environment setup flow instead of the repo-level
`.cursor` configuration, open <https://cursor.com/onboard> and ask the setup
agent to preserve these same Docker and Python requirements.

## Current limitations

- The grouping implementation is a lightweight local MVP. For very large NAS
  libraries, a vector index such as FAISS or hnswlib should replace the global
  representative pass.
- Face quality, eye-open detection, and aesthetic ML scoring are not included
  yet.
- Original files are never modified or deleted.