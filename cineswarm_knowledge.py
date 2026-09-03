#!/usr/bin/env python3
"""CineSwarm Master Domain Knowledge Base (High-Density Concise Edition).

Contains complete, high-density domain intelligence across 4 core disciplines:
1. Cinema History & Boutique Restorations
2. Media Engineering & Codec Hardware Constraints
3. The -arr Suite & Usenet NNTP Ecosystem
4. Docker, Linux Server Administration & Storage Health
"""

CINEMA_EXPERT_KNOWLEDGE = """
[DOMAIN KNOWLEDGE 1: CINEMA & FILM HISTORY]
- Eras & Movements: German Expressionism (Murnau/Nosferatu, Lang/Metropolis - shadow chiaroscuro), Soviet Montage (Eisenstein/Potemkin, Kuleshov effect), Golden Age Hollywood (Welles/Kane, Hitchcock/Vertigo, Noir/Double Indemnity), Italian Neorealism (De Sica/Bicycle Thieves - location non-actors), French New Wave (Godard/Breathless, Truffaut/400 Blows, Resnais/Hiroshima), Japanese Golden Age (Kurosawa/Seven Samurai, Ozu/Tokyo Story tatami shots), 70s New Hollywood (Scorsese, Coppola, Kubrick, De Palma), 90s Indie (Tarantino, Coen Bros, PTA), Asian Extreme & Modern Masters (Bong Joon-ho, Park Chan-wook, Wong Kar-wai, Edward Yang, Tarkovsky).
- Boutique Labels & Restorations: Criterion, Arrow, Eureka, Vinegar Syndrome, Radiance, Second Sight, Kino Lorber. Restorations require OCN (Original Camera Negative) 4K scans, ACES color grading, natural grain retention without destructive DNR (Digital Noise Reduction) or artificial sharpening.
- Technical Aesthetics: Aspect ratios (1.33:1 Academy, 1.66:1 European, 1.85:1 US Flat, 2.39:1 Anamorphic, 1.43:1 IMAX 70mm). Film stocks (Kodak 35mm 5219) vs Digital (Arri Alexa LF).
"""

MEDIA_ENGINEERING_KNOWLEDGE = """
[DOMAIN KNOWLEDGE 2: MEDIA ENGINEERING & TRANSCODING]
- Hardware Constraints (Samsung Galaxy Tab S9 FE): Exynos 1380 SoC + Mali-G68 iGPU. Native HW Decoders: H.264/AVC (High@L4.1), H.265/HEVC (Main 10 up to 4K60), VP9. Lacks AV1 hardware block; playing AV1 forces server CPU software decoding (0.3x speed bottleneck, dropped frames). Quality-Guard strictly BLOCKS AV1.
- Server Acceleration: Intel QuickSync (QSV / VAAPI via /dev/dri/renderD128), Nvidia NVENC (/dev/nvidia0).
- Containers & Bitrates: Matroska (.mkv) preferred. 1080p SDR (3-8 Mbps), 4K HDR (15-35 Mbps). Max release size ceiling: 8.0 GB (~67 MB/min).
- Dynamic Range: SDR (BT.709 8-bit), HDR10 (BT.2020 10-bit ST 2086), HDR10+, Dolby Vision (Profile 5 native streaming, Profile 7 MEL/FEL dual-layer Blu-ray, Profile 8.1 HDR10 fallback).
- Audio & Subtitles: Dolby TrueHD Atmos, DTS-HD MA, FLAC, AC3 5.1, AAC 2.0. Subtitles: SubRip (.srt) and WebVTT (.vtt) text format allow direct client overlay without triggering server video transcode (unlike PGS .sup image subtitles).
"""

ARR_USENET_ECOSYSTEM_KNOWLEDGE = """
=== DOMAIN KNOWLEDGE 3: THE -ARR SUITE & USENET ECOSYSTEM ===
- Usenet NNTP Stack: Binary newsgroups via SSL (Port 563/119), NZB XML files, newsreaders (SABnzbd / NZBGet), backbones (Omicron, Eweka, UsenetExpress), 5000+ days retention. Zero seeding, full pipe speed. PAR2 checksum repair. Indexers: Drunkenslug, NZBGeek, SimplyNZBs, AbNZB.
- Servarr Suite: Radarr (Movies), Sonarr (Series), Prowlarr (Indexer sync), Bazarr (Subtitles), Overseerr/Jellyseerr (Requests).
- Trash Custom Formats: Tier 1 Groups (FraMeSToR, EPSiLON, Don, PlayBD, NTb, FLUX, Silence). Positive score for TrueHD Atmos (+10000), Remux (+5000), WEB-DL (+3000). Hard block (-10000) for CAM, HDTS, Telecine, SDR 4K, Low-Grade Upscales, AV1 releases.
"""

DOCKER_SERVER_MAINTENANCE_KNOWLEDGE = """
[DOMAIN KNOWLEDGE 4: DOCKER & LINUX SERVER ADMINISTRATION]
- Process Management: Systemd user unit `cineswarm-control.service` (location `~/.config/systemd/user/`). Status check `systemctl --user status cineswarm-control`. Logs via `journalctl --user-unit cineswarm-control -f --no-pager`. Configured with `Restart=always`.
- Storage & Permissions: Media mounts `/media/Movies` and `/media/TV` from `/mnt/media`. User `PUID=1000`, Group `PGID=1000`, `umask 022` (files 644, dir 755). Storage safety floor: 500 GB free floor required.
- Storage Pools: ZFS (datasets, ARC RAM cache, ZFS Scrub), Unraid (Array + Parity + Cache Pools), mergerfs + SnapRAID.
- SQLite Integrity: `cineswarm_catalog.db` and `cineswarm_control.db`. Read-only URI mode (`file:catalog.db?mode=ro`) used by HTTP workers to eliminate lock contention. WAL mode (`PRAGMA journal_mode=WAL;`). Corrupt media scanning via `ffprobe`.
"""


def get_full_domain_knowledge() -> str:
    """Return complete synthesized master domain knowledge string."""
    return f"{CINEMA_EXPERT_KNOWLEDGE}\n\n{MEDIA_ENGINEERING_KNOWLEDGE}\n\n{ARR_USENET_ECOSYSTEM_KNOWLEDGE}\n\n{DOCKER_SERVER_MAINTENANCE_KNOWLEDGE}"
