# Madden League Tracker

A web app where a group of friends who each play one player on the same Madden franchise team sign in, enter their own per-game stats, and share one record of their seasons, games, and careers. Other invited groups can run their own leagues on the same site.

It runs as one Docker container on the Unraid server and is reached only over HTTPS through SWAG. The data is one SQLite database in `/data`. A nightly job writes a checked, gzipped snapshot of it to `/backups` and keeps the 14 newest.

## 1. Prepare the folders on Unraid

The app needs two folders:

| Folder | Holds | Where |
|---|---|---|
| data | `db.sqlite3` (plus its `-wal` and `-shm` files) and `secret_key` | A cache/pool path, e.g. `/mnt/cache/appdata/madden-tracker/data` |
| backups | `madden-YYYYMMDD-HHMMSSZ.sqlite3.gz` snapshots | An array share, e.g. `/mnt/user/backups/madden-tracker` |

- **Data goes on the pool path, not `/mnt/user/...`.** SQLite's WAL mode relies on shared memory and file locking that Unraid's FUSE user shares (`/mnt/user`) don't handle reliably. Use `/mnt/cache/...` (or `/mnt/<pool name>/...`). Set the `appdata` share's storage so it stays on the pool (primary storage the pool, no secondary storage / mover to the array). Otherwise the mover can take the database off the pool path.
- **Keep the backups share private.** Backups contain password hashes and 2FA secrets. In **Shares → backups**, set SMB export and NFS export to **No**.

In the Unraid terminal:

```
mkdir -p /mnt/cache/appdata/madden-tracker/data /mnt/user/backups/madden-tracker
chown -R 99:100 /mnt/cache/appdata/madden-tracker/data /mnt/user/backups/madden-tracker
```

The container runs as Unraid's `nobody:users` (99:100), so there are no PUID or PGID variables. Every `docker exec` also runs as 99:100, so commands like the backup never create root-owned files.

## 2. Build the image on Unraid

Clone the repo onto the server (or copy this folder there), then build:

```
git clone <repo URL> /mnt/user/appdata/madden-tracker/src
docker build -t madden-tracker /mnt/user/appdata/madden-tracker/src
```

## 3. Add the container

In the Unraid **Docker** tab → **Add Container**:

| Field | Value |
|---|---|
| Name | `madden-tracker` |
| Repository | `madden-tracker` |
| Network Type | the same custom network SWAG uses (e.g. `proxynet`) |
| Extra Parameters | `--restart unless-stopped` |
| Path: container `/data` | host `/mnt/cache/appdata/madden-tracker/data` |
| Path: container `/backups` | host `/mnt/user/backups/madden-tracker` |
| Variable `DJANGO_ALLOWED_HOSTS` | `madden.example.com` (your subdomain) |
| Variable `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://madden.example.com` |
| Variable `DJANGO_TIME_ZONE` (optional) | e.g. `America/New_York`. Defaults to UTC. |
| Variable `DJANGO_SECRET_KEY` (optional) | Leave unset. On first start the container writes a random key to `/data/secret_key` and keeps using it. |
| Variable `GUNICORN_WORKERS` (optional) | Defaults to `2`, which is plenty. |

Don't add a port mapping. SWAG reaches the app on the Docker network at `madden-tracker:8000`, and keeping 8000 off the LAN means the only way in is HTTPS through SWAG.

Click **Apply**, then make sure **Autostart** is on for the container in the Docker tab. Autostart brings the app back after an Unraid reboot; the restart parameter covers crashes. Unraid may show the image's update status as "not available". That's expected for a locally built image.

On each start the container applies any database migrations, then starts the web server. The container's health check calls `/health` inside the container.

The template values live on the Unraid flash drive, not in this folder.

## 4. Add the SWAG proxy conf

1. Copy `docker/swag/madden.subdomain.conf.sample` to SWAG's `/config/nginx/proxy-confs/madden.subdomain.conf` (on the host, typically `/mnt/user/appdata/swag/nginx/proxy-confs/`).
2. Add `madden` to SWAG's `SUBDOMAINS` variable (skip this if you use a wildcard certificate) and make sure DNS has a record for the subdomain.
3. Restart SWAG.

The conf is SWAG's standard subdomain sample pointed at `madden-tracker:8000`. SWAG's `proxy.conf` sends `X-Forwarded-For` and `X-Forwarded-Proto`. The app takes the client IP for sign-in lockout from the rightmost `X-Forwarded-For` entry (the one SWAG adds) and trusts `X-Forwarded-Proto` for HTTPS, so it must only ever be reached through SWAG.

The app sends HSTS for one year by default. Browsers then refuse plain HTTP for the subdomain for that long. To be cautious on the first day, add the variable `DJANGO_HSTS_SECONDS` = `300`, and remove it once the smoke checklist below passes.

## 5. Create the site admin

The site admin is a Django superuser, created from the command line only:

```
docker exec -it madden-tracker python manage.py createsuperuser
```

Then sign in at `https://madden.example.com`, open your account page, and turn on two-factor authentication. The site-admin pages (`/site-admin/`: users, password reset links, 2FA resets, backup status) only open for a superuser who has 2FA on and signed in with it. Without it they return "not found".

## 6. Schedule the nightly backup

Install the **User Scripts** plugin if it isn't there. In **Settings → User Scripts → Add New Script**, name it `madden-tracker-backup` and set its script to:

```
#!/bin/bash
docker exec madden-tracker python manage.py backup_db
```

Set the schedule to **Custom** with `30 3 * * *` (3:30 AM daily), then click **Run Script** once to check it. It should print `Backup written: /backups/madden-....sqlite3.gz`.

What the command does:

1. Copies the live database with SQLite's online backup API into a temporary file in `/backups`. This is safe while people are using the app. Never back up by copying `db.sqlite3` directly, which can capture a half-written change.
2. Runs `PRAGMA integrity_check` on the copy.
3. Gzips it and renames it into place as `madden-YYYYMMDD-HHMMSSZ.sqlite3.gz` (UTC time), readable only by its owner (mode 0600).
4. Deletes all but the 14 newest snapshots.

If anything fails, the command exits non-zero, leaves no partial file, and leaves the existing snapshots alone. It also writes the error to `/data/backup_failure.json`. The next successful run removes that file.

The **Backups** card on the site-admin page shows the newest snapshot's time. It shows a warning when there is no snapshot, when the newest is more than 26 hours old, or when the last attempt failed. The time comes from the snapshot files themselves, so a schedule that silently stopped running shows up there.

## 7. Restore drill

Do this once after setup, and again whenever you need a real restore. In the Unraid terminal:

```
docker exec madden-tracker python manage.py backup_db      # optional: a fresh snapshot to restore
docker stop madden-tracker
cd /mnt/cache/appdata/madden-tracker/data
cp db.sqlite3 db.sqlite3.before-restore                    # optional safety copy
rm -f db.sqlite3-wal db.sqlite3-shm
gunzip -c /mnt/user/backups/madden-tracker/madden-YYYYMMDD-HHMMSSZ.sqlite3.gz > db.sqlite3
chown 99:100 db.sqlite3 && chmod 600 db.sqlite3
docker start madden-tracker
```

Pick the snapshot with `ls /mnt/user/backups/madden-tracker`. Deleting `db.sqlite3-wal` and `db.sqlite3-shm` matters: they belong to the old database, and SQLite would try to apply them to the restored one.

For the drill, before stopping note a few things you can check afterwards: the number of games in a season and one player's season totals. After the restart, sign in and confirm they match. Delete `db.sqlite3.before-restore` once you're satisfied.

If the snapshot is from an older version of the app, the container applies the newer migrations on start.

## 8. On-server smoke checklist

Run through this after the first deployment and after any change to SWAG or the container template:

- [ ] `https://madden.example.com` loads with a valid certificate, and the sign-in page appears.
- [ ] `curl -I http://madden.example.com` returns a redirect to `https://`.
- [ ] Lockout uses the real client IP. From a phone on mobile data (not your LAN), sign in with a wrong password once, then check the recorded IP:

  ```
  docker exec madden-tracker python manage.py shell -c "from axes.models import AccessAttempt; print(list(AccessAttempt.objects.values_list('username', 'ip_address')))"
  ```

  It should show the phone's public IP, not SWAG's Docker address (`172.x.x.x`). If Cloudflare proxies the subdomain in front of SWAG, it shows a Cloudflare address instead, and lockout would then group everyone behind the same Cloudflare edge. Password reset links also record the IP they were used from, shown in the reset log on the site-admin page.
- [ ] The backup runs and appears: **Run Script** in User Scripts, then `ls -l /mnt/user/backups/madden-tracker` shows a new file owned by `nobody` with mode `-rw-------`, and the site-admin **Backups** card shows its time without a warning.
- [ ] Data survives a restart: make a small change (e.g. a team name), `docker restart madden-tracker`, and confirm it's still there.
- [ ] The database is in WAL mode:

  ```
  docker exec madden-tracker python -c "import sqlite3; print(sqlite3.connect('/data/db.sqlite3').execute('PRAGMA journal_mode').fetchone()[0])"
  ```

  This should print `wal`.
- [ ] The restore drill above gives back the same data.

## Logs and troubleshooting

Open the container's **Logs** from the Docker tab. Each request is logged by gunicorn, and errors include a traceback.

- **`DJANGO_ALLOWED_HOSTS must be set when DEBUG is off`:** the variable is blank in the template.
- **`No secret key`:** `/data` isn't writable, so the container couldn't create `/data/secret_key`. Run the `chown -R 99:100` command from step 1 again.
- **Browser shows "Bad Request (400)":** the address you used isn't in `DJANGO_ALLOWED_HOSTS`.
- **"CSRF verification failed" on sign-in:** `DJANGO_CSRF_TRUSTED_ORIGINS` is missing or doesn't exactly match `https://` plus the subdomain.
- **`attempt to write a readonly database` or `unable to open database file`:** run the `chown` from step 1 on the data folder.
- **`database is locked` errors, or a damaged database:** check that `/data` is mapped to a `/mnt/cache/...` (pool) path, not `/mnt/user/...`.
- **Backup fails with `Permission denied`:** run the `chown` from step 1 on the backups folder. The failure also appears on the site-admin page.

## Keeping it updated

The image is built locally, so Unraid never shows an update for it. Security fixes only arrive when you rebuild it.

**Monthly:** take a backup, pull any merged changes, and rebuild with the latest `python:3.12-slim` base:

```
docker exec madden-tracker python manage.py backup_db
cd /mnt/user/appdata/madden-tracker/src && git pull
docker build --pull -t madden-tracker /mnt/user/appdata/madden-tracker/src
```

In the Docker tab, edit `madden-tracker` and click **Apply** without changing anything. The `/data` and `/backups` folders and the template variables are kept, and the container applies any new migrations on start.

**Package updates:** Dependabot (`.github/dependabot.yml`) opens a pull request on GitHub when the pinned packages in `requirements.txt` or the Python base image have new versions. GitHub Actions runs the tests on Python 3.12 for every pull request. To take an update, merge the pull request, then do the monthly rebuild above.

To check by hand instead:

```
.venv/bin/pip install pip-audit
.venv/bin/pip-audit -r requirements.txt
.venv/bin/pip list --outdated
```

**Python version:** 3.12 gets security fixes until October 2028. Move `FROM` in the `Dockerfile` to a newer `python:3.x-slim` before then, run the tests, and rebuild.

## Development

```
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

To run it locally (the database goes in `./data`):

```
mkdir -p data
DJANGO_DEBUG=1 .venv/bin/python manage.py migrate
DJANGO_DEBUG=1 .venv/bin/python manage.py createsuperuser
DJANGO_DEBUG=1 .venv/bin/python manage.py runserver
```

To try a backup locally, point `BACKUP_DIR` at an existing folder:

```
mkdir -p backups
BACKUP_DIR=./backups DJANGO_DEBUG=1 .venv/bin/python manage.py backup_db
```

To check the production security settings:

```
DJANGO_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')" DJANGO_ALLOWED_HOSTS=madden.example.com .venv/bin/python manage.py check --deploy
```

Keep any virtual environment out of the Nextcloud folder if you don't want it synced. Never create a `.env` or other secrets file here, and never commit a legacy backup (`madden-player-tracker*.json`): it contains real names.

## License

Madden League Tracker is free software, released under the [GNU General Public License v3.0](LICENSE). To report a security problem, see [SECURITY.md](SECURITY.md).
