#!/usr/bin/env python3
"""Durable household library intelligence: files, people, collections, watches, local answers.

Reads Radarr/Sonarr SQLite and Plex watch XML. Never moves or deletes media files.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

from cineswarm_sync import RADARR_DB, SONARR_DB, json_value

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_DB = os.environ.get("CINESWARM_CATALOG_DB", os.path.join(BASE_DIR, "cineswarm_catalog.db"))
CONTROL_DB = os.environ.get("CINESWARM_CONTROL_DB", os.path.join(BASE_DIR, "cineswarm_control.db"))
PORTRAIT_PATH = os.environ.get("CINESWARM_LIBRARY_PORTRAIT_PATH", os.path.join(BASE_DIR, "cineswarm_library_portrait.json"))

CREDIT_CAST = 0
CREDIT_CREW = 1
CREW_JOBS = {"director", "writer", "screenplay", "novel"}
MAX_CAST_ORDER = 8

CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_files (
    catalog_item_id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    relative_path TEXT,
    size_bytes INTEGER,
    video_codec TEXT,
    audio_codec TEXT,
    width INTEGER,
    height INTEGER,
    hdr TEXT,
    audio_channels INTEGER,
    audio_languages_json TEXT NOT NULL DEFAULT '[]',
    subtitle_languages_json TEXT NOT NULL DEFAULT '[]',
    edition TEXT,
    quality_label TEXT,
    bitrate_kbps INTEGER,
    fps REAL,
    duration_mins REAL,
    episode_file_count INTEGER NOT NULL DEFAULT 0,
    media_info_source TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalog_files_codec ON catalog_files(video_codec);
CREATE INDEX IF NOT EXISTS idx_catalog_files_hdr ON catalog_files(hdr);
CREATE TABLE IF NOT EXISTS catalog_people (
    catalog_item_id INTEGER NOT NULL,
    person_name TEXT NOT NULL,
    credit_type TEXT NOT NULL,
    character_name TEXT,
    person_tmdb_id INTEGER,
    billing_order INTEGER,
    PRIMARY KEY (catalog_item_id, person_name, credit_type)
);
CREATE INDEX IF NOT EXISTS idx_catalog_people_name ON catalog_people(person_name, credit_type);
CREATE INDEX IF NOT EXISTS idx_catalog_people_item ON catalog_people(catalog_item_id);
CREATE TABLE IF NOT EXISTS catalog_ratings (
    catalog_item_id INTEGER NOT NULL,
    rating_source TEXT NOT NULL,
    value REAL,
    votes INTEGER,
    PRIMARY KEY (catalog_item_id, rating_source)
);
CREATE TABLE IF NOT EXISTS catalog_collections (
    collection_tmdb_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    owned_count INTEGER NOT NULL DEFAULT 0,
    owned_titles_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
"""

CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_watch_ledger (
    rating_key TEXT NOT NULL,
    plex_account TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    media_type TEXT,
    view_count INTEGER NOT NULL DEFAULT 1,
    last_viewed_at TEXT,
    view_offset_ms INTEGER,
    duration_ms INTEGER,
    completion_pct REAL,
    tmdb_id TEXT,
    genres_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (rating_key, plex_account)
);
CREATE INDEX IF NOT EXISTS idx_watch_ledger_title ON library_watch_ledger(title);
CREATE INDEX IF NOT EXISTS idx_watch_ledger_account ON library_watch_ledger(plex_account, last_viewed_at);
"""

ITEM_COLUMNS = (
    ("video_codec", "TEXT"),
    ("audio_codec", "TEXT"),
    ("duration_mins", "REAL"),
    ("size_mb", "REAL"),
    ("studio", "TEXT"),
    ("certification", "TEXT"),
    ("collection_title", "TEXT"),
    ("collection_tmdb_id", "INTEGER"),
    ("imdb_rating", "REAL"),
    ("width", "INTEGER"),
    ("height", "INTEGER"),
    ("hdr", "TEXT"),
    ("audio_channels", "INTEGER"),
    ("edition", "TEXT"),
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_text(value: Any, default: Any) -> str:
    return json.dumps(value if value is not None else default, ensure_ascii=False, sort_keys=True)


def parse_runtime_mins(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 1)
    text = str(value).strip()
    match = re.match(r"^(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
        return round(hours * 60 + minutes + seconds / 60.0, 1)
    try:
        return round(float(text), 1)
    except ValueError:
        return None


def parse_media_info(raw: Any) -> dict[str, Any]:
    """Normalize Radarr/Sonarr MediaInfo JSON into a slim file record."""
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            data = {}
    if not isinstance(data, dict):
        data = {}

    hdr = str(data.get("videoHdrFormat") or "").strip()
    primaries = str(data.get("videoColourPrimaries") or "").lower()
    transfer = str(data.get("videoTransferCharacteristics") or "").lower()
    if not hdr and (primaries in {"bt2020", "bt.2020"} or "smpte2084" in transfer or "hlg" in transfer):
        hdr = "hdr"
    elif hdr.lower() in {"", "none", "unknown"}:
        hdr = ""

    video_codec = str(data.get("videoFormat") or data.get("videoCodecID") or "").strip().lower()
    audio_codec = str(data.get("audioFormat") or data.get("audioCodecID") or "").strip().lower()
    if video_codec and not re.fullmatch(r"[a-z0-9][a-z0-9.+_-]{0,31}", video_codec):
        video_codec = ""
    if audio_codec and not re.fullmatch(r"[a-z0-9][a-z0-9.+_-]{0,31}", audio_codec):
        audio_codec = ""
    width = data.get("width")
    height = data.get("height")
    try:
        width = int(width) if width not in (None, "") else None
    except (TypeError, ValueError):
        width = None
    try:
        height = int(height) if height not in (None, "") else None
    except (TypeError, ValueError):
        height = None

    bitrate = data.get("videoBitrate") or 0
    try:
        bitrate_kbps = int(round(float(bitrate) / 1000.0)) if bitrate else None
    except (TypeError, ValueError):
        bitrate_kbps = None

    channels = data.get("audioChannels")
    try:
        channels = int(channels) if channels not in (None, "") else None
    except (TypeError, ValueError):
        channels = None

    languages = data.get("audioLanguages") or []
    if isinstance(languages, str):
        languages = [part.strip() for part in languages.split("/") if part.strip()]
    subtitles = data.get("subtitles") or []
    if isinstance(subtitles, str):
        subtitles = [part.strip() for part in subtitles.split("/") if part.strip()]

    fps = data.get("videoFps")
    try:
        fps = round(float(fps), 3) if fps not in (None, "") else None
    except (TypeError, ValueError):
        fps = None

    return {
        "video_codec": video_codec or None,
        "audio_codec": audio_codec or None,
        "width": width,
        "height": height,
        "hdr": hdr or None,
        "audio_channels": channels,
        "audio_languages": [str(item) for item in languages][:12],
        "subtitle_languages": [str(item) for item in subtitles][:12],
        "bitrate_kbps": bitrate_kbps,
        "fps": fps,
        "duration_mins": parse_runtime_mins(data.get("runTime")),
    }


def quality_label(relative_path: str | None, quality_json: Any = None) -> str | None:
    name = str(relative_path or "")
    match = re.search(r"(?:^|[ (._-])((?:Bluray|BluRay|WEBDL|WEBRip|HDTV|Remux|DVDRip|BRRip)[-_. ]?\d{3,4}p)", name, re.I)
    if match:
        return re.sub(r"[._]", "-", match.group(1))
    if isinstance(quality_json, str):
        try:
            quality_json = json.loads(quality_json)
        except (TypeError, json.JSONDecodeError):
            quality_json = None
    if isinstance(quality_json, dict):
        quality = quality_json.get("quality")
        if isinstance(quality, dict) and quality.get("name"):
            return str(quality["name"])
    return None


def resolution_bucket(width: int | None, height: int | None) -> str:
    long_edge = max(width or 0, height or 0)
    if long_edge >= 3800:
        return "4K"
    if long_edge >= 2500:
        return "1440p"
    if long_edge >= 1800:
        return "1080p"
    if long_edge >= 1200:
        return "720p"
    if long_edge > 0:
        return "SD"
    return "unknown"


def credit_kind(credit_type: int, job: str | None) -> str | None:
    if int(credit_type or 0) == CREDIT_CAST:
        return "actor"
    job_name = str(job or "").strip().lower()
    if job_name in CREW_JOBS:
        return job_name
    return None


def watched_items_from_xml(root: ET.Element) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for node in root:
        node_type = node.get("type")
        if node.tag not in ("Video", "Directory") or node_type not in ("movie", "show", "episode"):
            continue
        last_viewed = int(node.get("lastViewedAt") or 0)
        duration_ms = int(node.get("duration") or 0)
        offset_ms = int(node.get("viewOffset") or 0)
        completion = None
        if duration_ms > 0:
            viewed = offset_ms if offset_ms > 0 else (duration_ms if int(node.get("viewCount") or 0) else 0)
            completion = round(min(1.0, max(0.0, viewed / duration_ms)), 3)
        tmdb_id = None
        for guid in node.findall("Guid"):
            guid_id = str(guid.get("id") or "")
            if "tmdb://" in guid_id:
                tmdb_id = guid_id.split("tmdb://", 1)[-1].split("?")[0]
                break
        if not tmdb_id:
            guid_id = str(node.get("guid") or "")
            if "themoviedb://" in guid_id:
                tmdb_id = guid_id.split("themoviedb://", 1)[-1].split("?")[0]
        genres = [genre.get("tag") for genre in node.findall("Genre") if genre.get("tag")]
        if node_type == "episode":
            title = node.get("grandparentTitle")
            if not title:
                continue
            rating_key = node.get("grandparentRatingKey") or node.get("ratingKey") or title
            media_type = "series"
            year = int(node.get("parentYear") or node.get("year")) if str(node.get("parentYear") or node.get("year") or "").isdigit() else None
        else:
            title = node.get("title") or "Unknown"
            rating_key = node.get("ratingKey") or title
            media_type = "movie" if node_type == "movie" else "series"
            year = int(node.get("year")) if str(node.get("year") or "").isdigit() else None
        items.append({
            "rating_key": rating_key,
            "title": title,
            "year": year,
            "media_type": media_type,
            "view_count": int(node.get("viewCount") or 1),
            "last_viewed_at": datetime.fromtimestamp(last_viewed, timezone.utc).isoformat(timespec="seconds") if last_viewed else None,
            "view_offset_ms": offset_ms or None,
            "duration_ms": duration_ms or None,
            "completion_pct": completion,
            "tmdb_id": tmdb_id,
            "genres": genres,
        })
    return items


class LibraryBrain:
    def __init__(self, catalog_db: str = CATALOG_DB, control_db: str = CONTROL_DB, radarr_db: str = RADARR_DB, sonarr_db: str = SONARR_DB):
        self.catalog_db = catalog_db
        self.control_db = control_db
        self.radarr_db = radarr_db
        self.sonarr_db = sonarr_db
        self.ensure_schema()

    def ensure_schema(self) -> None:
        os.makedirs(os.path.dirname(self.catalog_db) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(self.control_db) or ".", exist_ok=True)
        with sqlite3.connect(self.catalog_db) as conn:
            conn.executescript(CATALOG_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(catalog_items)")}
            for name, decl in ITEM_COLUMNS:
                if columns and name not in columns:
                    conn.execute(f"ALTER TABLE catalog_items ADD COLUMN {name} {decl}")
        with sqlite3.connect(self.control_db) as conn:
            conn.executescript(CONTROL_SCHEMA)

    def refresh(self, playback_history: Any | None = None, write_portrait: bool = True) -> dict[str, Any]:
        result = {
            "radarr": self.enrich_from_radarr(),
            "sonarr": self.enrich_from_sonarr(),
        }
        if playback_history is not None:
            result["watch"] = self.sync_watch_ledger(playback_history)
        if write_portrait:
            result["portrait"] = self.write_portrait()
        return result

    def enrich_from_radarr(self) -> dict[str, Any]:
        if not os.path.exists(self.radarr_db) or not os.path.exists(self.catalog_db):
            return {"status": "skipped", "reason": "missing_database"}
        timestamp = now()
        files_written = 0
        people_written = 0
        ratings_written = 0
        collections_written = 0
        with sqlite3.connect(self.radarr_db) as source, sqlite3.connect(self.catalog_db) as dest:
            source.row_factory = sqlite3.Row
            dest.row_factory = sqlite3.Row
            self.ensure_schema()
            dest.execute("DELETE FROM catalog_files WHERE source='radarr'")
            dest.execute(
                "DELETE FROM catalog_people WHERE catalog_item_id IN (SELECT id FROM catalog_items WHERE source='radarr')"
            )
            dest.execute(
                "DELETE FROM catalog_ratings WHERE catalog_item_id IN (SELECT id FROM catalog_items WHERE source='radarr')"
            )
            dest.execute("DELETE FROM catalog_collections")

            native_to_catalog = {
                int(row["source_native_id"]): int(row["id"])
                for row in dest.execute(
                    "SELECT id, source_native_id FROM catalog_items WHERE source='radarr' AND present=1 AND source_native_id IS NOT NULL"
                )
            }
            metadata_to_native = {
                int(row["MovieMetadataId"]): int(row["Id"])
                for row in source.execute("SELECT Id, MovieMetadataId FROM Movies")
                if row["MovieMetadataId"] is not None
            }

            file_rows = source.execute(
                """
                SELECT m.Id AS movie_id, m.MovieMetadataId, mf.RelativePath, mf.Size, mf.MediaInfo, mf.Edition, mf.Quality
                FROM Movies m
                JOIN MovieFiles mf ON mf.Id = m.MovieFileId
                WHERE m.MovieFileId IS NOT NULL AND m.MovieFileId > 0
                """
            )
            for row in file_rows:
                catalog_id = native_to_catalog.get(int(row["movie_id"]))
                if not catalog_id:
                    continue
                info = parse_media_info(row["MediaInfo"])
                size_bytes = int(row["Size"] or 0)
                dest.execute(
                    """
                    INSERT OR REPLACE INTO catalog_files (
                        catalog_item_id, source, relative_path, size_bytes, video_codec, audio_codec,
                        width, height, hdr, audio_channels, audio_languages_json, subtitle_languages_json,
                        edition, quality_label, bitrate_kbps, fps, duration_mins, episode_file_count,
                        media_info_source, updated_at
                    ) VALUES (?, 'radarr', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'radarr', ?)
                    """,
                    (
                        catalog_id,
                        row["RelativePath"],
                        size_bytes or None,
                        info.get("video_codec"),
                        info.get("audio_codec"),
                        info.get("width"),
                        info.get("height"),
                        info.get("hdr"),
                        info.get("audio_channels"),
                        _json_text(info.get("audio_languages"), []),
                        _json_text(info.get("subtitle_languages"), []),
                        (row["Edition"] or "").strip() or None,
                        quality_label(row["RelativePath"], row["Quality"]),
                        info.get("bitrate_kbps"),
                        info.get("fps"),
                        info.get("duration_mins"),
                        timestamp,
                    ),
                )
                dest.execute(
                    """
                    UPDATE catalog_items SET
                        video_codec=COALESCE(?, video_codec),
                        audio_codec=COALESCE(?, audio_codec),
                        duration_mins=COALESCE(?, duration_mins),
                        size_mb=CASE WHEN ? > 0 THEN ? ELSE size_mb END,
                        width=COALESCE(?, width),
                        height=COALESCE(?, height),
                        hdr=COALESCE(?, hdr),
                        audio_channels=COALESCE(?, audio_channels),
                        edition=COALESCE(?, edition)
                    WHERE id=?
                    """,
                    (
                        info.get("video_codec"),
                        info.get("audio_codec"),
                        info.get("duration_mins"),
                        round(size_bytes / (1024 * 1024), 1) if size_bytes else 0,
                        round(size_bytes / (1024 * 1024), 1) if size_bytes else 0,
                        info.get("width"),
                        info.get("height"),
                        info.get("hdr"),
                        info.get("audio_channels"),
                        (row["Edition"] or "").strip() or None,
                        catalog_id,
                    ),
                )
                files_written += 1

            credits = source.execute(
                """
                SELECT MovieMetadataId, Name, Character, Job, Type, "Order", PersonTmdbId
                FROM Credits
                WHERE (Type = ? AND COALESCE("Order", 0) <= ?)
                   OR (Type = ? AND lower(COALESCE(Job, '')) IN ('director', 'writer', 'screenplay', 'novel'))
                """,
                (CREDIT_CAST, MAX_CAST_ORDER, CREDIT_CREW),
            )
            for row in credits:
                kind = credit_kind(int(row["Type"] or 0), row["Job"])
                if not kind:
                    continue
                native_id = metadata_to_native.get(int(row["MovieMetadataId"]))
                catalog_id = native_to_catalog.get(native_id) if native_id is not None else None
                if not catalog_id or not row["Name"]:
                    continue
                dest.execute(
                    """
                    INSERT OR REPLACE INTO catalog_people (
                        catalog_item_id, person_name, credit_type, character_name, person_tmdb_id, billing_order
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        catalog_id,
                        str(row["Name"]).strip(),
                        kind,
                        (row["Character"] or None),
                        row["PersonTmdbId"],
                        row["Order"],
                    ),
                )
                people_written += 1

            collection_owned: dict[int, dict[str, Any]] = {}
            for row in source.execute(
                "SELECT Id, Title, Studio, Certification, CollectionTitle, CollectionTmdbId, Ratings FROM MovieMetadata"
            ):
                native_id = metadata_to_native.get(int(row["Id"]))
                catalog_id = native_to_catalog.get(native_id) if native_id is not None else None
                if not catalog_id:
                    continue
                ratings = json_value(row["Ratings"], {})
                dest.execute(
                    """
                    UPDATE catalog_items SET studio=?, certification=?, collection_title=?, collection_tmdb_id=?, imdb_rating=?
                    WHERE id=?
                    """,
                    (
                        row["Studio"],
                        row["Certification"],
                        row["CollectionTitle"],
                        row["CollectionTmdbId"] or None,
                        ((ratings.get("imdb") or {}).get("value") if isinstance(ratings, dict) else None),
                        catalog_id,
                    ),
                )
                if isinstance(ratings, dict):
                    for source_name, payload in ratings.items():
                        if not isinstance(payload, dict):
                            continue
                        dest.execute(
                            "INSERT OR REPLACE INTO catalog_ratings (catalog_item_id, rating_source, value, votes) VALUES (?, ?, ?, ?)",
                            (catalog_id, str(source_name), payload.get("value"), payload.get("votes") or 0),
                        )
                        ratings_written += 1
                collection_id = row["CollectionTmdbId"]
                collection_title = (row["CollectionTitle"] or "").strip()
                if collection_id and collection_title:
                    bucket = collection_owned.setdefault(
                        int(collection_id),
                        {"title": collection_title, "titles": []},
                    )
                    title_row = dest.execute("SELECT title, year FROM catalog_items WHERE id=?", (catalog_id,)).fetchone()
                    if title_row:
                        label = f"{title_row['title']} ({title_row['year']})" if title_row["year"] else title_row["title"]
                        if label not in bucket["titles"]:
                            bucket["titles"].append(label)

            for collection_id, payload in collection_owned.items():
                titles = sorted(payload["titles"])
                dest.execute(
                    """
                    INSERT OR REPLACE INTO catalog_collections (collection_tmdb_id, title, owned_count, owned_titles_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (collection_id, payload["title"], len(titles), _json_text(titles, []), timestamp),
                )
                collections_written += 1
            dest.commit()
        return {
            "status": "ok",
            "files": files_written,
            "people": people_written,
            "ratings": ratings_written,
            "collections": collections_written,
        }

    def enrich_from_sonarr(self) -> dict[str, Any]:
        if not os.path.exists(self.sonarr_db) or not os.path.exists(self.catalog_db):
            return {"status": "skipped", "reason": "missing_database"}
        timestamp = now()
        files_written = 0
        with sqlite3.connect(self.sonarr_db) as source, sqlite3.connect(self.catalog_db) as dest:
            source.row_factory = sqlite3.Row
            dest.row_factory = sqlite3.Row
            dest.execute("DELETE FROM catalog_files WHERE source='sonarr'")
            native_to_catalog = {
                int(row["source_native_id"]): int(row["id"])
                for row in dest.execute(
                    "SELECT id, source_native_id FROM catalog_items WHERE source='sonarr' AND present=1 AND source_native_id IS NOT NULL"
                )
            }
            grouped: dict[int, dict[str, Any]] = {}
            for row in source.execute("SELECT SeriesId, Size, MediaInfo, RelativePath, Quality FROM EpisodeFiles"):
                series_id = int(row["SeriesId"])
                bucket = grouped.setdefault(
                    series_id,
                    {"count": 0, "size": 0, "codecs": Counter(), "info": None, "path": None, "quality": None},
                )
                bucket["count"] += 1
                bucket["size"] += int(row["Size"] or 0)
                info = parse_media_info(row["MediaInfo"])
                if info.get("video_codec"):
                    bucket["codecs"][info["video_codec"]] += 1
                if bucket["info"] is None:
                    bucket["info"] = info
                    bucket["path"] = row["RelativePath"]
                    bucket["quality"] = row["Quality"]
            for series_id, bucket in grouped.items():
                catalog_id = native_to_catalog.get(series_id)
                if not catalog_id:
                    continue
                info = bucket["info"] or {}
                codec = bucket["codecs"].most_common(1)[0][0] if bucket["codecs"] else info.get("video_codec")
                dest.execute(
                    """
                    INSERT OR REPLACE INTO catalog_files (
                        catalog_item_id, source, relative_path, size_bytes, video_codec, audio_codec,
                        width, height, hdr, audio_channels, audio_languages_json, subtitle_languages_json,
                        edition, quality_label, bitrate_kbps, fps, duration_mins, episode_file_count,
                        media_info_source, updated_at
                    ) VALUES (?, 'sonarr', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 'sonarr', ?)
                    """,
                    (
                        catalog_id,
                        bucket["path"],
                        bucket["size"] or None,
                        codec,
                        info.get("audio_codec"),
                        info.get("width"),
                        info.get("height"),
                        info.get("hdr"),
                        info.get("audio_channels"),
                        _json_text(info.get("audio_languages"), []),
                        _json_text(info.get("subtitle_languages"), []),
                        quality_label(bucket["path"], bucket["quality"]),
                        info.get("bitrate_kbps"),
                        info.get("fps"),
                        info.get("duration_mins"),
                        bucket["count"],
                        timestamp,
                    ),
                )
                dest.execute(
                    """
                    UPDATE catalog_items SET
                        video_codec=COALESCE(?, video_codec),
                        audio_codec=COALESCE(?, audio_codec),
                        size_mb=CASE WHEN ? > 0 THEN ? ELSE size_mb END,
                        width=COALESCE(?, width),
                        height=COALESCE(?, height),
                        hdr=COALESCE(?, hdr),
                        audio_channels=COALESCE(?, audio_channels)
                    WHERE id=?
                    """,
                    (
                        codec,
                        info.get("audio_codec"),
                        round(bucket["size"] / (1024 * 1024), 1) if bucket["size"] else 0,
                        round(bucket["size"] / (1024 * 1024), 1) if bucket["size"] else 0,
                        info.get("width"),
                        info.get("height"),
                        info.get("hdr"),
                        info.get("audio_channels"),
                        catalog_id,
                    ),
                )
                files_written += 1
            dest.commit()
        return {"status": "ok", "files": files_written}

    def sync_watch_ledger(self, playback_history: Any, plex_account: str = "household") -> dict[str, Any]:
        limit = max(100, min(int(os.environ.get("CINESWARM_WATCH_LEDGER_LIMIT", "2000") or 2000), 5000))
        try:
            root = playback_history.get_watched_items(limit=limit)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        items = watched_items_from_xml(root)
        upserted = 0
        with sqlite3.connect(self.control_db) as conn:
            for item in items:
                if not item["rating_key"]:
                    continue
                self._upsert_watch(conn, plex_account=plex_account, item=item)
                upserted += 1
            conn.commit()
        return {"status": "ok", "upserted": upserted, "limit": limit}

    def watched_series_titles(self, limit: int = 40) -> list[str]:
        """Distinct series titles from the durable Plex watch ledger, most-watched first."""
        if not os.path.exists(self.control_db):
            return []
        titles: list[str] = []
        seen: set[str] = set()
        with sqlite3.connect(self.control_db) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "library_watch_ledger" not in tables:
                return []
            rows = conn.execute(
                """
                SELECT title, SUM(view_count) AS plays
                FROM library_watch_ledger
                WHERE media_type='series' AND title IS NOT NULL AND title!=''
                GROUP BY title
                ORDER BY plays DESC, MAX(last_viewed_at) DESC
                """
            ).fetchall()
        for title, _plays in rows:
            clean = str(title or "").strip()
            key = clean.casefold()
            if not clean or key in seen:
                continue
            seen.add(key)
            titles.append(clean)
            if len(titles) >= max(1, min(int(limit), 80)):
                break
        return titles

    def record_watch_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = payload.get("Metadata") or {}
        account = ((payload.get("Account") or {}).get("title") or (payload.get("Account") or {}).get("name") or "household")
        duration_ms = int(metadata.get("duration") or payload.get("duration") or 0)
        offset_ms = int(metadata.get("viewOffset") or payload.get("viewOffset") or 0)
        completion = round(min(1.0, max(0.0, offset_ms / duration_ms)), 3) if duration_ms else None
        if str(payload.get("event") or "").lower() in {"media.scrobble", "scrobble"}:
            completion = 1.0
        tmdb_id = None
        for guid in metadata.get("Guid") or []:
            guid_id = str(guid.get("id") if isinstance(guid, dict) else guid)
            if "tmdb://" in guid_id:
                tmdb_id = guid_id.split("tmdb://", 1)[-1].split("?")[0]
                break
        genres = []
        for genre in metadata.get("Genre") or []:
            if isinstance(genre, dict) and genre.get("tag"):
                genres.append(genre["tag"])
            elif isinstance(genre, str):
                genres.append(genre)
        item = {
            "rating_key": str(metadata.get("ratingKey") or metadata.get("title") or "unknown"),
            "title": metadata.get("title") or payload.get("title") or "Unknown",
            "year": metadata.get("year"),
            "media_type": "movie" if metadata.get("type") == "movie" else ("series" if metadata.get("type") in {"show", "episode"} else metadata.get("type")),
            "view_count": 1,
            "last_viewed_at": now(),
            "view_offset_ms": offset_ms or None,
            "duration_ms": duration_ms or None,
            "completion_pct": completion,
            "tmdb_id": tmdb_id,
            "genres": genres,
        }
        with sqlite3.connect(self.control_db) as conn:
            self._upsert_watch(conn, plex_account=str(account), item=item, increment=True)
            conn.commit()
        return {"status": "ok", "title": item["title"], "plex_account": account, "completion_pct": completion}

    def _upsert_watch(self, conn: sqlite3.Connection, plex_account: str, item: dict[str, Any], increment: bool = False) -> None:
        existing = conn.execute(
            "SELECT view_count FROM library_watch_ledger WHERE rating_key=? AND plex_account=?",
            (item["rating_key"], plex_account),
        ).fetchone()
        view_count = int(item.get("view_count") or 1)
        if increment and existing:
            view_count = int(existing[0] or 0) + 1
        elif existing and not increment:
            view_count = max(view_count, int(existing[0] or 0))
        conn.execute(
            """
            INSERT INTO library_watch_ledger (
                rating_key, plex_account, title, year, media_type, view_count, last_viewed_at,
                view_offset_ms, duration_ms, completion_pct, tmdb_id, genres_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rating_key, plex_account) DO UPDATE SET
                title=excluded.title,
                year=excluded.year,
                media_type=excluded.media_type,
                view_count=excluded.view_count,
                last_viewed_at=COALESCE(excluded.last_viewed_at, library_watch_ledger.last_viewed_at),
                view_offset_ms=COALESCE(excluded.view_offset_ms, library_watch_ledger.view_offset_ms),
                duration_ms=COALESCE(excluded.duration_ms, library_watch_ledger.duration_ms),
                completion_pct=COALESCE(excluded.completion_pct, library_watch_ledger.completion_pct),
                tmdb_id=COALESCE(excluded.tmdb_id, library_watch_ledger.tmdb_id),
                genres_json=excluded.genres_json,
                updated_at=excluded.updated_at
            """,
            (
                item["rating_key"],
                plex_account,
                item["title"],
                item.get("year"),
                item.get("media_type"),
                view_count,
                item.get("last_viewed_at"),
                item.get("view_offset_ms"),
                item.get("duration_ms"),
                item.get("completion_pct"),
                item.get("tmdb_id"),
                _json_text(item.get("genres"), []),
                now(),
            ),
        )

    def coverage(self) -> dict[str, Any]:
        stats = {
            "catalog_present": 0,
            "files": 0,
            "people": 0,
            "ratings": 0,
            "collections": 0,
            "watch_titles": 0,
            "codec_known": 0,
            "resolution_known": 0,
        }
        if os.path.exists(self.catalog_db):
            with sqlite3.connect(self.catalog_db) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                stats["catalog_present"] = int(conn.execute("SELECT COUNT(*) FROM catalog_items WHERE present=1").fetchone()[0])
                if "catalog_files" in tables:
                    stats["files"] = int(conn.execute("SELECT COUNT(*) FROM catalog_files").fetchone()[0])
                    stats["codec_known"] = int(conn.execute("SELECT COUNT(*) FROM catalog_files WHERE video_codec IS NOT NULL AND video_codec!=''").fetchone()[0])
                    stats["resolution_known"] = int(conn.execute("SELECT COUNT(*) FROM catalog_files WHERE width IS NOT NULL AND width>0").fetchone()[0])
                if "catalog_people" in tables:
                    stats["people"] = int(conn.execute("SELECT COUNT(*) FROM catalog_people").fetchone()[0])
                if "catalog_ratings" in tables:
                    stats["ratings"] = int(conn.execute("SELECT COUNT(*) FROM catalog_ratings").fetchone()[0])
                if "catalog_collections" in tables:
                    stats["collections"] = int(conn.execute("SELECT COUNT(*) FROM catalog_collections").fetchone()[0])
        if os.path.exists(self.control_db):
            with sqlite3.connect(self.control_db) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "library_watch_ledger" in tables:
                    stats["watch_titles"] = int(conn.execute("SELECT COUNT(*) FROM library_watch_ledger").fetchone()[0])
        return stats

    def build_portrait(self) -> dict[str, Any]:
        portrait: dict[str, Any] = {
            "generated_at": now(),
            "coverage": self.coverage(),
            "shelf": {},
            "files": {},
            "taste_gap": {},
            "collections": [],
            "people": {},
            "recent_watches": [],
        }
        if not os.path.exists(self.catalog_db):
            return portrait
        with sqlite3.connect(self.catalog_db) as conn:
            conn.row_factory = sqlite3.Row
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            genres: Counter[str] = Counter()
            decades: Counter[str] = Counter()
            for row in conn.execute("SELECT genres_json, year FROM catalog_items WHERE present=1 AND media_type='movie'"):
                for genre in json_value(row["genres_json"], []):
                    genres[str(genre)] += 1
                if row["year"]:
                    decades[f"{(int(row['year']) // 10) * 10}s"] += 1
            movie_count = int(conn.execute("SELECT COUNT(*) FROM catalog_items WHERE present=1 AND media_type='movie'").fetchone()[0])
            series_count = int(conn.execute("SELECT COUNT(*) FROM catalog_items WHERE present=1 AND media_type='series'").fetchone()[0])
            portrait["shelf"] = {
                "movies": movie_count,
                "series": series_count,
                "top_genres": genres.most_common(12),
                "top_decades": decades.most_common(8),
            }
            if "catalog_files" in tables:
                codecs = conn.execute(
                    "SELECT COALESCE(video_codec,'unknown'), COUNT(*) FROM catalog_files GROUP BY 1 ORDER BY 2 DESC LIMIT 12"
                ).fetchall()
                hdr_count = int(conn.execute("SELECT COUNT(*) FROM catalog_files WHERE hdr IS NOT NULL AND hdr!=''").fetchone()[0])
                av1_count = int(conn.execute("SELECT COUNT(*) FROM catalog_files WHERE lower(COALESCE(video_codec,'')) LIKE '%av1%'").fetchone()[0])
                buckets: Counter[str] = Counter()
                for row in conn.execute("SELECT width, height FROM catalog_files"):
                    buckets[resolution_bucket(row["width"], row["height"])] += 1
                portrait["files"] = {
                    "codecs": [(row[0], row[1]) for row in codecs],
                    "resolution": buckets.most_common(),
                    "hdr_count": hdr_count,
                    "av1_count": av1_count,
                }
            if "catalog_collections" in tables:
                rows = conn.execute(
                    "SELECT title, owned_count, owned_titles_json FROM catalog_collections ORDER BY owned_count DESC LIMIT 40"
                ).fetchall()
                portrait["collections"] = [
                    {
                        "title": row["title"],
                        "owned_count": row["owned_count"],
                        "sample": json_value(row["owned_titles_json"], [])[:8],
                        "likely_incomplete": row["owned_count"] <= 2,
                    }
                    for row in rows
                ]
            if "catalog_people" in tables:
                directors = conn.execute(
                    "SELECT person_name, COUNT(*) AS n FROM catalog_people WHERE credit_type='director' GROUP BY 1 ORDER BY n DESC LIMIT 12"
                ).fetchall()
                actors = conn.execute(
                    "SELECT person_name, COUNT(*) AS n FROM catalog_people WHERE credit_type='actor' GROUP BY 1 ORDER BY n DESC LIMIT 12"
                ).fetchall()
                portrait["people"] = {
                    "top_directors_on_shelf": [(row[0], row[1]) for row in directors],
                    "top_actors_on_shelf": [(row[0], row[1]) for row in actors],
                }

        watched_titles: set[str] = set()
        watch_genres: Counter[str] = Counter()
        if os.path.exists(self.control_db):
            with sqlite3.connect(self.control_db) as conn:
                conn.row_factory = sqlite3.Row
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "library_watch_ledger" in tables:
                    for row in conn.execute(
                        "SELECT title, year, view_count, last_viewed_at, genres_json FROM library_watch_ledger ORDER BY (last_viewed_at IS NULL), last_viewed_at DESC, view_count DESC LIMIT 20"
                    ):
                        portrait["recent_watches"].append(
                            {
                                "title": row["title"],
                                "year": row["year"],
                                "view_count": row["view_count"],
                                "last_viewed_at": row["last_viewed_at"],
                            }
                        )
                    for row in conn.execute("SELECT title, genres_json FROM library_watch_ledger"):
                        watched_titles.add(str(row["title"] or "").strip().lower())
                        for genre in json_value(row["genres_json"], []):
                            watch_genres[str(genre)] += 1
        shelf_genre_share = {name: count for name, count in portrait["shelf"].get("top_genres") or []}
        portrait["taste_gap"] = {
            "watch_genres": watch_genres.most_common(10),
            "shelf_genres": list(shelf_genre_share.items())[:10],
            "note": "Plex watches vs Radarr shelf. Discovery already prefers watch genres; this is the durable picture.",
        }
        return portrait

    def write_portrait(self) -> dict[str, Any]:
        portrait = self.build_portrait()
        with open(PORTRAIT_PATH, "w", encoding="utf-8") as handle:
            json.dump(portrait, handle, indent=2, ensure_ascii=False)
        return {"path": PORTRAIT_PATH, "coverage": portrait.get("coverage") or {}}

    def search_person(self, name: str, limit: int = 12) -> dict[str, Any]:
        needle = name.strip()
        if not needle or not os.path.exists(self.catalog_db):
            return {"person": needle, "credits": []}
        with sqlite3.connect(self.catalog_db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT p.person_name, p.credit_type, p.character_name, c.title, c.year, c.media_type
                FROM catalog_people p
                JOIN catalog_items c ON c.id = p.catalog_item_id
                WHERE lower(p.person_name) LIKE ? AND c.present=1
                ORDER BY p.credit_type, c.year DESC, c.title
                LIMIT ?
                """,
                (f"%{needle.lower()}%", limit),
            ).fetchall()
        return {
            "person": needle,
            "credits": [dict(row) for row in rows],
        }

    def collection_map(self, query: str = "", limit: int = 15) -> list[dict[str, Any]]:
        if not os.path.exists(self.catalog_db):
            return []
        clauses = []
        params: list[Any] = []
        if query.strip():
            clauses.append("lower(title) LIKE ?")
            params.append(f"%{query.strip().lower()}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with sqlite3.connect(self.catalog_db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT title, owned_count, owned_titles_json FROM catalog_collections {where} ORDER BY owned_count DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        results = []
        for row in rows:
            titles = json_value(row["owned_titles_json"], [])
            results.append(
                {
                    "title": row["title"],
                    "owned_count": row["owned_count"],
                    "titles": titles,
                    "likely_incomplete": row["owned_count"] <= 2,
                }
            )
        return results

    def answer(self, prompt: str, extra_context: dict[str, Any] | None = None) -> str:
        text = (prompt or "").strip()
        if not text:
            return "Ask about the shelf, a person, a collection, codecs, or what you've actually watched."
        lowered = text.lower()
        portrait = self.build_portrait()
        coverage = portrait.get("coverage") or {}

        person_match = re.search(r"(?:director|directed by|actor|starring|filmography|movies with)\s+(.+)$", text, re.I)
        if person_match or any(word in lowered for word in ("filmography", "directed by", "starring")):
            name = person_match.group(1).strip(" ?.") if person_match else re.sub(r".*(director|actor|starring|filmography)\s+", "", text, flags=re.I)
            found = self.search_person(name, limit=15)
            credits = found.get("credits") or []
            if not credits:
                return f"No local credits stored yet for {found['person']}. After the next catalog sync the people index should fill from Radarr."
            lines = [f"{row['credit_type']}: {row['title']} ({row['year'] or 'n.d.'})" for row in credits]
            return f"On the shelf for {found['person']}:\n" + "\n".join(f"- {line}" for line in lines)

        if any(word in lowered for word in ("collection", "franchise", "sequel", "box set", "boxset")):
            query = re.sub(r".*(collection|franchise|sequel[s]?)\s*", "", text, flags=re.I).strip(" ?.")
            collections = self.collection_map(query, limit=8)
            if not collections:
                return "No Radarr collections indexed yet. They land on the next catalog sync."
            lines = []
            for item in collections:
                flag = " — likely incomplete" if item["likely_incomplete"] else ""
                sample = ", ".join(item["titles"][:5])
                lines.append(f"{item['title']}: {item['owned_count']} owned{flag}. {sample}")
            return "Collections on the shelf:\n" + "\n".join(f"- {line}" for line in lines)

        if any(word in lowered for word in ("codec", "hdr", "4k", "1080", "720", "av1", "hevc", "h264", "resolution", "quality")):
            files = portrait.get("files") or {}
            codecs = ", ".join(f"{name} ({count})" for name, count in files.get("codecs") or [])
            resolutions = ", ".join(f"{name} ({count})" for name, count in files.get("resolution") or [])
            return (
                f"File-level picture: {coverage.get('files', 0)} files indexed, "
                f"{coverage.get('resolution_known', 0)} with resolution, {files.get('hdr_count', 0)} HDR, "
                f"{files.get('av1_count', 0)} AV1 (tablet-unfriendly). "
                f"Codecs: {codecs or 'n/a'}. Resolutions: {resolutions or 'n/a'}."
            )

        if any(word in lowered for word in ("watched", "watch history", "never watched", "unwatched", "taste", "actually watch")):
            watches = portrait.get("recent_watches") or []
            watch_genres = portrait.get("taste_gap", {}).get("watch_genres") or []
            shelf_genres = portrait.get("taste_gap", {}).get("shelf_genres") or []
            recent = ", ".join(f"{row['title']}" for row in watches[:8]) or "none stored yet"
            return (
                f"Durable watch ledger has {coverage.get('watch_titles', 0)} titles. "
                f"Recent plays: {recent}. "
                f"Watch genres: {watch_genres[:6]}. Shelf genres: {shelf_genres[:6]}. "
                f"The shelf skews drama-heavy; playback is what discovery should chase."
            )

        catalog_hits = []
        extra = extra_context or {}
        for item in extra.get("relevant_catalog_matches") or []:
            title = item.get("title")
            if title:
                catalog_hits.append(f"{title} ({item.get('year') or 'n.d.'})")
        shelf = portrait.get("shelf") or {}
        people = portrait.get("people") or {}
        directors = ", ".join(name for name, _count in (people.get("top_directors_on_shelf") or [])[:6])
        lines = [
            f"Local librarian (no hosted model). Catalog: {shelf.get('movies', 0)} movies, {shelf.get('series', 0)} series; "
            f"{coverage.get('files', 0)} files, {coverage.get('people', 0)} credits, {coverage.get('collections', 0)} collections, "
            f"{coverage.get('watch_titles', 0)} watched titles.",
            f"Shelf genres: {shelf.get('top_genres', [])[:8]}.",
            f"Directors most present on disk: {directors or 'n/a'}.",
        ]
        if catalog_hits:
            lines.append("Closest catalog matches: " + "; ".join(catalog_hits[:8]) + ".")
        recon = extra.get("reconciliation") or {}
        if recon:
            lines.append(f"Integrity snapshot (counts only): {recon}.")
        lines.append("Ask about a director, a franchise, codecs/HDR, or what you've actually watched for a sharper cut.")
        return " ".join(lines)


def refresh_library_brain(playback_history: Any | None = None) -> dict[str, Any]:
    return LibraryBrain().refresh(playback_history=playback_history)
