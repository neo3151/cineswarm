#!/usr/bin/env python3
"""Media Health & Video Corruption Scanner for CineSwarm using ffprobe & ffmpeg."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from typing import Any

CATALOG_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cineswarm_catalog.db")


class MediaHealthScanner:
    def __init__(self, db_path: str = CATALOG_DB):
        self.db_path = db_path

    def inspect_file(self, filepath: str) -> dict[str, Any]:
        """Perform fast ffprobe header & stream integrity inspection."""
        if not os.path.exists(filepath):
            return {"status": "missing", "error": "File does not exist on disk"}

        if os.path.getsize(filepath) == 0:
            return {"status": "corrupt", "error": "Zero-byte empty file"}

        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            filepath
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode != 0:
                return {"status": "corrupt", "error": f"ffprobe error: {res.stderr.strip()[:150]}"}

            probe = json.loads(res.stdout or "{}")
            fmt = probe.get("format", {})
            streams = probe.get("streams", [])

            duration = float(fmt.get("duration", 0))
            has_video = any(s.get("codec_type") == "video" for s in streams)
            has_audio = any(s.get("codec_type") == "audio" for s in streams)

            if not has_video:
                return {"status": "corrupt", "error": "No video stream found in container"}
            if not has_audio:
                return {"status": "warning", "error": "No audio stream detected"}
            if duration <= 0:
                return {"status": "corrupt", "error": "Invalid or missing video duration"}

            video_codec = next((s.get("codec_name") for s in streams if s.get("codec_type") == "video"), "unknown")
            audio_codec = next((s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), "unknown")

            return {
                "status": "healthy",
                "duration_mins": round(duration / 60, 1),
                "video_codec": video_codec,
                "audio_codec": audio_codec,
                "size_mb": round(os.path.getsize(filepath) / (1024 * 1024), 1)
            }
        except subprocess.TimeoutExpired:
            return {"status": "corrupt", "error": "ffprobe inspection timed out (unreadable header)"}
        except Exception as exc:
            return {"status": "corrupt", "error": str(exc)}

    def deep_decode_check(self, filepath: str, sample_seconds: int = 10) -> dict[str, Any]:
        """Perform ffmpeg sample decode check to detect corrupted frames."""
        cmd = [
            "ffmpeg",
            "-v", "error",
            "-ss", "00:05:00",
            "-i", filepath,
            "-t", str(sample_seconds),
            "-f", "null",
            "-"
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if res.stderr.strip():
                return {"status": "corrupt", "error": f"ffmpeg decode error: {res.stderr.strip()[:200]}"}
            return {"status": "healthy"}
        except subprocess.TimeoutExpired:
            return {"status": "warning", "error": "ffmpeg decode sample timed out"}
        except Exception as exc:
            return {"status": "corrupt", "error": str(exc)}

    @staticmethod
    def resolve_path(path: str) -> str:
        if not path:
            return path
        path_map = os.environ.get("CINESWARM_PATH_MAP", "/media=/mnt/media,/data=/mnt/media")
        for mapping in path_map.split(","):
            if "=" in mapping:
                src, dst = mapping.split("=", 1)
                if path.startswith(src):
                    target = path.replace(src, dst, 1)
                    if os.path.exists(target):
                        return target
        return path

    @classmethod
    def locate_video_file(cls, path: str) -> str:
        real_path = cls.resolve_path(path)
        if not os.path.exists(real_path):
            return real_path
        if os.path.isfile(real_path):
            return real_path
        if os.path.isdir(real_path):
            for root, _, files in os.walk(real_path):
                for f in files:
                    if f.endswith((".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts")):
                        return os.path.join(root, f)
        return real_path

    def scan_catalog(self, limit: int = 50) -> dict[str, Any]:
        """Scan catalog files for corruption and write video spec metadata into DB."""
        scanned = 0
        healthy_count = 0
        corrupt_items = []

        with sqlite3.connect(self.db_path) as conn:
            # Ensure metadata columns exist
            cols = {row[1] for row in conn.execute("PRAGMA table_info(catalog_items)")}
            if "video_codec" not in cols:
                conn.execute("ALTER TABLE catalog_items ADD COLUMN video_codec TEXT")
            if "audio_codec" not in cols:
                conn.execute("ALTER TABLE catalog_items ADD COLUMN audio_codec TEXT")
            if "duration_mins" not in cols:
                conn.execute("ALTER TABLE catalog_items ADD COLUMN duration_mins REAL")
            if "size_mb" not in cols:
                conn.execute("ALTER TABLE catalog_items ADD COLUMN size_mb REAL")

            rows = conn.execute("SELECT id, title, path, raw_json, source_native_id, media_type FROM catalog_items WHERE present=1 AND path IS NOT NULL ORDER BY RANDOM() LIMIT ?", (limit,)).fetchall()

            for item_id, title, path, raw_json, native_id, m_type in rows:
                target_file = self.locate_video_file(path)
                scanned += 1
                res = self.inspect_file(target_file)
                if res["status"] in ("corrupt", "missing"):
                    corrupt_items.append({
                        "catalog_id": item_id,
                        "title": title,
                        "path": path,
                        "resolved_path": target_file,
                        "source_native_id": native_id,
                        "media_type": m_type or "movie",
                        "error": res.get("error", "Unknown corruption")
                    })
                else:
                    healthy_count += 1
                    conn.execute(
                        "UPDATE catalog_items SET video_codec=?, audio_codec=?, duration_mins=?, size_mb=? WHERE id=?",
                        (res.get("video_codec"), res.get("audio_codec"), res.get("duration_mins"), res.get("size_mb"), item_id)
                    )

        return {
            "scanned": scanned,
            "healthy": healthy_count,
            "corrupt_count": len(corrupt_items),
            "corrupt_items": corrupt_items
        }
