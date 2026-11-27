# Deployment

The API and relay both bind loopback by default. For remote access, terminate
TLS at a reverse proxy (see `nginx/daythread.conf`) and set:

```bash
dt config set api.allowed_hosts "daythread.example.com,127.0.0.1,localhost"
```

## Docker Compose

```bash
export DAYTHREAD_RELAY_TOKEN="$(openssl rand -base64 32)"
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml exec api dt serve --print-token
```

The API is published on `127.0.0.1:8765`; the relay on `127.0.0.1:8788`;
Prometheus on `127.0.0.1:9090`. Volumes hold the SQLite database, master key,
backups, and relay bundles. Import `grafana/daythread.json` for a starter
dashboard.

## systemd

Install `dt` into `/usr/local/bin`, then:

```bash
cp deploy/systemd/daythread-api.service ~/.config/systemd/user/
systemctl --user enable --now daythread-api.service
```

For the relay, create `~/.config/daythread/relay.env` with
`DAYTHREAD_RELAY_TOKEN=...` and enable `daythread-relay.service`.

## Backups

Use `dt backup` on a schedule, or snapshot the `/data` volume. Keep
`master.key` with the database: secrets and signed backups depend on it.
