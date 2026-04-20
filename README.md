# HTPC Media Center — Fedora bootc Image

A production-ready, fully reproducible HTPC (Home Theater PC) OS image built with
[Fedora bootc](https://containers.github.io/bootc/) (image-mode). A single
`bootc switch` command turns any Fedora system into a dedicated media center.

## Architecture

| Component | Technology |
|-----------|-----------|
| Base OS | `quay.io/fedora/fedora-bootc:41` (OCI image, atomic updates) |
| Media player | Kodi (RPM Fusion) — kiosk via `cage` Wayland compositor |
| DRM streaming | Chromium (Fedora repos) + Widevine (auto-installed on first boot) |
| Spotify receiver | Raspotify (Podman Quadlet) |
| Now Playing overlay | Kodi Python service addon + LRCLIB synced lyrics |
| Services | Podman Quadlets (Vikunja, Vaultwarden, Headscale, ntfy, Caddy) |
| Remote access | Headscale (self-hosted Tailscale) — services on VPN only |
| CI/CD | GitHub Actions + buildah → GHCR |

---

## Prerequisites

- A machine with UEFI firmware and a compatible GPU (AMD, Intel, or NVIDIA)
- A Fedora (or any compatible) Linux installation as the starting point
- For NVIDIA: **Secure Boot must be disabled** (akmod-nvidia compiles kernel modules at first boot)
- Network access during first boot (for Widevine download)

---

## Deployment

### 1. Switch to the HTPC image

```bash
# Default (AMD GPU):
sudo bootc switch ghcr.io/<OWNER>/htpc-image/os:latest

# Intel GPU:
sudo bootc switch ghcr.io/<OWNER>/htpc-image/os:latest-intel

# NVIDIA GPU (Secure Boot must be off):
sudo bootc switch ghcr.io/<OWNER>/htpc-image/os:latest-nvidia

sudo reboot
```

After reboot, Kodi starts fullscreen automatically on TTY1.

### 2. OS updates

```bash
sudo bootc upgrade
sudo reboot
```

Container services auto-update via `podman-auto-update.timer` (daily).

---

## First Boot Checklist

### Widevine CDM (automatic)

The `widevine-install.service` runs on first boot and downloads Widevine from
Google Chrome. Monitor progress:

```bash
journalctl -fu widevine-install.service
```

Widevine is installed to `/var/lib/chromium-widevine/` and survives OS upgrades.
Once installed, the service never runs again (guarded by `/var/lib/widevine-installed`).

Verify in Chromium: open `chrome://components` → look for **Widevine Content Decryption Module**.

### Spotify credentials (manual, required for Now Playing overlay)

Create the secrets file (not committed to git, not baked into the image):

```bash
sudo mkdir -p /etc/htpc
sudo tee /etc/htpc/secrets.env > /dev/null <<'EOF'
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
SPOTIFY_REFRESH_TOKEN=your_refresh_token_here
EOF
sudo chmod 600 /etc/htpc/secrets.env
sudo systemctl restart spotify-sidecar
```

See **§ Spotify OAuth2 Setup** below for how to obtain these values.

### Headscale VPN config (manual, required for remote access)

Create the config file:

```bash
sudo mkdir -p /etc/headscale
# Download and edit the official template:
curl -o /etc/headscale/config.yaml \
  https://raw.githubusercontent.com/juanfont/headscale/main/config-example.yaml
sudo nano /etc/headscale/config.yaml   # set server_url, etc.
sudo systemctl restart headscale
```

Register this machine with your Headscale server or use headscale as the server itself:

```bash
# If running headscale locally (this machine is the server):
podman exec headscale headscale users create htpc
podman exec headscale headscale nodes register --user htpc --key <preauth-key>
```

### Enable Kodi Now Playing addon (one-time)

After first boot, open Kodi and navigate to:
**Add-ons → My add-ons → Services → Now Playing → Enable**

This only needs to be done once; the setting persists across updates.

### NFS media sources (optional)

Edit `/var/home/kodi/.kodi/userdata/sources.xml` to point to your NAS:

```bash
nano /var/home/kodi/.kodi/userdata/sources.xml
# Replace nfs://nas.local/... with your actual NFS/SMB paths
```

Or use the Kodi UI: **Settings → Media → Library → Add video source**.

---

## Spotify OAuth2 Setup

The `/now-playing` endpoint requires the `user-read-currently-playing` OAuth2 scope.
You need a **refresh token** — obtained once, stored in secrets.env.

### Step 1: Create a Spotify app

1. Go to [https://developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Create an app, add `http://localhost:8888/callback` as a Redirect URI
3. Note your **Client ID** and **Client Secret**

### Step 2: Get the authorization code

Open this URL in a browser (replace `CLIENT_ID`):

```
https://accounts.spotify.com/authorize?client_id=CLIENT_ID&response_type=code&redirect_uri=http%3A%2F%2Flocalhost%3A8888%2Fcallback&scope=user-read-currently-playing
```

After authorizing, you'll be redirected to `http://localhost:8888/callback?code=<CODE>`.
Copy the `<CODE>` from the URL.

### Step 3: Exchange the code for a refresh token

```bash
CLIENT_ID="your_client_id"
CLIENT_SECRET="your_client_secret"
CODE="the_code_from_step_2"

curl -s -X POST https://accounts.spotify.com/api/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -u "${CLIENT_ID}:${CLIENT_SECRET}" \
  -d "grant_type=authorization_code" \
  -d "code=${CODE}" \
  -d "redirect_uri=http://localhost:8888/callback" \
  | python3 -m json.tool
```

Copy the `refresh_token` from the response. Set it in `/etc/htpc/secrets.env`.

---

## IPTV Channels

### Automatic updates

Chilean channels from [iptv-org](https://github.com/iptv-org/iptv) are downloaded
daily by `iptv-update.timer` to `/var/lib/iptv/channels.m3u`.

Check status: `journalctl -u iptv-update.service`

### Adding local overrides

When an iptv-org stream is broken, add your own URL:

```bash
sudo nano /var/lib/iptv/local-overrides.m3u
```

Format (standard M3U):

```m3u
#EXTM3U
#EXTINF:-1 tvg-id="TVN.cl" tvg-name="TVN" group-title="Chile",TVN
http://your-working-stream-url/stream.m3u8
```

Run `sudo /usr/local/bin/update-playlist.sh` to rebuild immediately.

---

## Adding Services

To add a new containerized service:

1. Create a Quadlet file:
   ```bash
   sudo tee /etc/containers/systemd/myservice.container > /dev/null <<'EOF'
   [Unit]
   Description=My new service

   [Container]
   Image=docker.io/example/myservice:1.2.3
   PublishPort=127.0.0.1:9000:9000
   Volume=/var/lib/myservice:/data

   [Service]
   Restart=always

   [Install]
   WantedBy=multi-user.target
   EOF
   ```

2. Reload and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl start myservice
   ```

3. Add to the Caddyfile if you want VPN-gated web access:
   ```bash
   sudo nano /etc/caddy/Caddyfile
   sudo systemctl restart caddy
   ```

To persist a service across `bootc upgrade`, add its `.container` file to the
`quadlets/` directory in this repo and rebuild the image.

---

## Remote Access

All services are exposed only on the Headscale/WireGuard interface (`100.64.0.x`).
They are unreachable from the LAN without VPN.

Add DNS entries for `*.htpc.internal` on your tailnet clients, or use
`/etc/hosts` entries pointing to the HTPC's Headscale IP:

```
100.64.x.y  tasks.htpc.internal vault.htpc.internal ntfy.htpc.internal
```

| Service | URL |
|---------|-----|
| Vikunja (tasks) | https://tasks.htpc.internal |
| Vaultwarden | https://vault.htpc.internal |
| ntfy | https://ntfy.htpc.internal |

TLS uses self-signed certs by default (`tls internal` in Caddyfile). Accept the
browser warning once and it will be remembered.

---

## Build Locally

```bash
# Clone the repo
git clone https://github.com/<OWNER>/htpc-image && cd htpc-image

# Build the OS image (AMD GPU default)
podman build --build-arg GPU_VENDOR=amd -t htpc-image-local .

# Inspect the result
podman run --rm -it htpc-image-local bash
podman run --rm htpc-image-local systemctl list-unit-files | grep enabled

# Build just the spotify-sidecar
podman build -t spotify-sidecar services/spotify-sidecar/

# Test the IPTV script (dry-run, no files written)
bash config/iptv/update-playlist.sh --dry-run
```

---

## Troubleshooting

### Kodi doesn't start

```bash
journalctl -fu kodi-session.service
# Common causes: TTY1 occupied by getty (masked in image), XDG_RUNTIME_DIR not set
```

### Widevine not working

```bash
journalctl -u widevine-install.service
ls -la /var/lib/chromium-widevine/
# In Chromium: chrome://components → Widevine Content Decryption Module
```

### PipeWire / audio issues

```bash
# Check PipeWire is running as kodi user
systemctl --user -M kodi@ status pipewire
# Check raspotify can reach PipeWire
podman logs raspotify
PULSE_SERVER=unix:/run/user/1000/pulse/native paplay /usr/share/sounds/alsa/Front_Left.wav
```

### Container service not starting

```bash
systemctl status <service>       # e.g., raspotify.service
podman logs <container-name>     # e.g., podman logs raspotify
journalctl -u <service> -n 50
```

### Check all Quadlet services

```bash
systemctl list-units --type=service | grep -E 'raspotify|sidecar|vikunja|vaultwarden|headscale|ntfy|caddy'
```

---

## Secrets Reference

| Variable | File | Used by |
|----------|------|---------|
| `SPOTIFY_CLIENT_ID` | `/etc/htpc/secrets.env` | spotify-sidecar |
| `SPOTIFY_CLIENT_SECRET` | `/etc/htpc/secrets.env` | spotify-sidecar |
| `SPOTIFY_REFRESH_TOKEN` | `/etc/htpc/secrets.env` | spotify-sidecar |
| Headscale config (server URL, etc.) | `/etc/headscale/config.yaml` | headscale |
| Caddy DNS API key (optional) | `/etc/htpc/secrets.env` | caddy |

**None of these files are committed to git or baked into the image.**
