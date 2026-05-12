# SnapStack

SnapStack is a local Docker web app for finding burst shots and visually similar
photos stored on a NAS. It groups similar images and recommends the best three
photos to keep from each group.

The first version is intentionally safe: it reads photos from mounted folders,
writes thumbnails and analysis cache to Docker storage, and does not delete
original files.

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

1. Edit `docker-compose.yml` and map your NAS folders into the container:

   ```yaml
   volumes:
     - /mnt/nas/photos/camera-roll:/photos/camera-roll:ro
     - /mnt/nas/photos/family:/photos/family:ro
     - /mnt/nas/photos/archive:/photos/archive:ro
   ```

2. Edit `config/snapstack.yml` so each configured path points at the container
   path from the right-hand side of the volume mapping:

   ```yaml
   photo_roots:
     - name: camera-roll
       path: /photos/camera-roll
     - name: family
       path: /photos/family
     - name: archive
       path: /photos/archive
   ```

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
  - name: camera-roll
    path: /photos/camera-roll
  - name: family
    path: /photos/family

recommendation_count: 3
hash_distance_threshold: 8
burst_time_window_seconds: 20
```

You can also skip the YAML file and pass comma-separated roots with
`SNAPSTACK_PHOTO_ROOTS`:

```bash
SNAPSTACK_PHOTO_ROOTS=/photos/camera-roll,/photos/family uvicorn app.main:app
```

## Scan and cache behavior

- Photos are rechecked when you press the scan button in the browser.
- Unchanged photos reuse cached analysis from `/data/snapstack.db` and cached
  thumbnails from `/data/thumbnails`.
- Cache rows for deleted photos are cleaned from the database for the selected
  roots during each scan.
- The UI shows the timestamp of the most recently completed scan.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
SNAPSTACK_CONFIG=config/snapstack.yml SNAPSTACK_DATA_DIR=.data uvicorn app.main:app --reload
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