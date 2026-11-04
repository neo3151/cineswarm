#!/usr/bin/env bash
set -euo pipefail

# Deploy the music services without changing the existing movie/TV compose file.
#
# Run this on devtop:
#   bash music-setup.sh
#
# The script only creates service configuration/directories and starts containers.
# API keys and indexer settings remain a manual, UI-level configuration step.

BASE_DIR="${MUSIC_DOCKER_DIR:-$HOME/docker/music}"
MEDIA_DIR="${MUSIC_MEDIA_DIR:-/mnt/media}"
COMPOSE_FILE="$BASE_DIR/docker-compose.yml"

mkdir -p \
  "$BASE_DIR/lidarr/config" \
  "$BASE_DIR/navidrome/data" \
  "$MEDIA_DIR/Music"

cat > "$COMPOSE_FILE" <<EOF
services:
  lidarr:
    image: lscr.io/linuxserver/lidarr:latest
    container_name: lidarr
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/Chicago
    volumes:
      - $BASE_DIR/lidarr/config:/config
      - $MEDIA_DIR:/media
      - $MEDIA_DIR/downloads:/downloads
    ports:
      - "8686:8686"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped

  navidrome:
    image: deluan/navidrome:latest
    container_name: navidrome
    user: "1000:1000"
    environment:
      ND_MUSICFOLDER: /music
      ND_DATAFOLDER: /data
      ND_SCANNER_SCHEDULE: "@every 1h"
      ND_LOGLEVEL: info
      TZ: America/Chicago
    volumes:
      - $BASE_DIR/navidrome/data:/data
      - $MEDIA_DIR/Music:/music:ro
    ports:
      - "4533:4533"
    restart: unless-stopped
EOF

docker compose -f "$COMPOSE_FILE" config >/dev/null
docker compose -f "$COMPOSE_FILE" up -d

cat <<EOF

Music services are running:
  Lidarr:    http://$(hostname -I 2>/dev/null | awk '{print $1}'):8686
  Navidrome: http://$(hostname -I 2>/dev/null | awk '{print $1}'):4533

Next steps:
  1. In Lidarr, add root folder /media/Music.
  2. Add SABnzbd at http://host.docker.internal:8080.
  3. Connect Lidarr to Prowlarr at http://host.docker.internal:9696.
  4. Add the Lidarr root folder to Navidrome's music library is automatic.
EOF
