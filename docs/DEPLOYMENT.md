# Deployment

The defaults are for a single person on their own machine: bound to `127.0.0.1`, SQLite,
HTTP. Before making the app reachable from any network:

1. **Use Docker Compose (PostgreSQL)** or run the API + worker under a process manager.
2. **Set secrets** in `.env`: `SECRET_KEY`, `ENCRYPTION_KEY`, `ADMIN_PASSWORD` (≥ 10 chars,
   unique), `POSTGRES_PASSWORD`. Set `APP_ENV=production` (hides `/api/docs`).
3. **Terminate HTTPS** in a reverse proxy (Caddy, nginx, Traefik) in front of `127.0.0.1:8000`,
   and set `COOKIE_SECURE=true`. Cookies are HttpOnly, SameSite=Strict; CSRF uses a
   session-bound double-submit token.
4. **Restrict access**: ideally only expose `/u/*` (unsubscribe) publicly and keep the app itself
   behind a VPN, Tailscale, or an IP allow-list. The app always requires the admin password.
5. **Rate limit** at the proxy as well (the built-in limiter is per process), e.g. 10 req/min/IP for
   `/u/` and `/api/auth/login`.
6. Set `PUBLIC_BASE_URL=https://your-host` to enable signed one-click unsubscribe links
   (`List-Unsubscribe` + `List-Unsubscribe-Post`, RFC 8058).

Example Caddyfile exposing only unsubscribe publicly while the UI is reachable over a VPN:

```
outreach.example.com {
    @public path /u/*
    handle @public {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        @vpn remote_ip 100.64.0.0/10
        reverse_proxy @vpn 127.0.0.1:8000
        respond 403
    }
}
```

## Docker Compose

```bash
docker compose up -d --build        # db + migrate (one-shot) + app + worker
docker compose logs -f worker
docker compose down                 # data persists in the pgdata volume
```

The API container listens on 0.0.0.0 *inside* the container but is published only on the host's
loopback (`127.0.0.1:8000`). PostgreSQL is not published at all.

## Upgrades

Pull the new code, rebuild, and run migrations (`docker compose up -d --build` runs the `migrate`
service first). Take a backup beforehand.
