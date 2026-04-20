FROM quay.io/fedora/fedora-bootc:41

ARG GPU_VENDOR=amd

# --- RPM Fusion repos (free + nonfree) ---
RUN dnf install -y \
    https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-41.noarch.rpm \
    https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-41.noarch.rpm \
    && dnf clean all

# --- Core packages ---
RUN dnf install -y \
    kodi \
    kodi-inputstream-adaptive \
    kodi-pvr-iptvsimple \
    cage \
    chromium \
    podman \
    pipewire \
    wireplumber \
    pipewire-pulseaudio \
    python3 \
    binutils \
    tar \
    curl \
    jq \
    sqlite \
    libva \
    libva-utils \
    && dnf clean all

# --- GPU-specific VA-API / video acceleration drivers ---
# Valid GPU_VENDOR values: amd | intel | nvidia
# NVIDIA WARNING: akmod-nvidia compiles kernel modules at first boot into /var/.
#                 This requires Secure Boot to be DISABLED. First boot will be slow.
RUN case "${GPU_VENDOR}" in \
      amd) \
        dnf install -y --skip-unavailable \
          mesa-va-drivers \
          mesa-vdpau-drivers \
        ;; \
      intel) \
        dnf install -y --skip-unavailable \
          intel-media-driver \
          libva-intel-driver \
        ;; \
      nvidia) \
        dnf install -y --skip-unavailable \
          akmod-nvidia \
          kernel-devel \
          xorg-x11-drv-nvidia-cuda \
        ;; \
      *) \
        echo "ERROR: GPU_VENDOR must be amd, intel, or nvidia (got: ${GPU_VENDOR})" >&2; \
        exit 1 \
        ;; \
    esac \
    && dnf clean all

# --- Kodi system user ---
# Explicit UID 1000 so the PipeWire socket path /run/user/1000/ is predictable
# and can be hardcoded in Quadlet volume mounts.
#
# video/audio/input/render are defined in /usr/lib/group via systemd-sysusers,
# NOT in /etc/group. groupadd -f exits 0 without writing to /etc/group (it sees
# them via NSS), so useradd/usermod fail when they try to modify /etc/group.
# Fix: copy each group entry from NSS into /etc/group if absent, then use
# gpasswd -a which modifies /etc/group directly.
RUN useradd \
        --uid 1000 \
        --system \
        --create-home \
        --home-dir /var/home/kodi \
        --shell /bin/bash \
        kodi \
    && for grp in video audio input render; do \
         grep -q "^${grp}:" /etc/group \
           || getent group "${grp}" >> /etc/group \
           || groupadd "${grp}"; \
         gpasswd -a kodi "${grp}"; \
       done

# Enable linger so kodi's user services start without an active login session.
# loginctl enable-linger does not work in a container build; write the marker file directly.
RUN mkdir -p /var/lib/systemd/linger && touch /var/lib/systemd/linger/kodi

# Mask getty on TTY1 — prevents a race between agetty and the Kodi kiosk session.
RUN systemctl mask getty@tty1.service

# --- Systemd units ---
COPY systemd/system/ /etc/systemd/system/

# --- Quadlet container files (auto-discovered by systemd-generator — no enable needed) ---
COPY quadlets/ /etc/containers/systemd/

# --- tmpfiles.d: copies skeleton Kodi config to /var/home/kodi on first boot only ---
COPY config/tmpfiles.d/ /etc/tmpfiles.d/

# --- Kodi addon (baked into system addons path) ---
# NOTE: After first boot, enable via Kodi UI: Add-ons → My add-ons → Services → Now Playing
COPY kodi-addon/plugin.audio.nowplaying/ /usr/share/kodi/addons/plugin.audio.nowplaying/

# --- Kodi skeleton configs (tmpfiles.d copies these on first boot if not already present) ---
RUN mkdir -p /usr/share/htpc-kodi-config/
COPY config/kodi/ /usr/share/htpc-kodi-config/

# --- Chromium managed policies ---
RUN mkdir -p /etc/chromium/policies/managed/
COPY config/chromium/policies.json /etc/chromium/policies/managed/policies.json

# --- Scripts ---
COPY config/iptv/update-playlist.sh /usr/local/bin/update-playlist.sh
COPY services/widevine-install/widevine-install.sh /usr/local/bin/widevine-install.sh
COPY services/kodi-utils/launch-chromium.sh /usr/local/bin/launch-chromium.sh
RUN chmod +x \
    /usr/local/bin/update-playlist.sh \
    /usr/local/bin/widevine-install.sh \
    /usr/local/bin/launch-chromium.sh

# --- Caddyfile ---
RUN mkdir -p /etc/caddy/
COPY Caddyfile /etc/caddy/Caddyfile

# --- Enable systemd units ---
# Quadlets in /etc/containers/systemd/ are auto-discovered — no enable needed for them.
RUN systemctl enable \
    kodi-session.service \
    widevine-install.service \
    iptv-update.timer \
    podman-auto-update.timer
