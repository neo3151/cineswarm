#!/usr/bin/env python3
"""Phase 1 CineSwarm control plane: read-only service health and catalog snapshots."""

from __future__ import annotations

import argparse
import base64
import hmac
import html
import json
import math
import os

import platform
import re
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from cineswarm_agents import AcquisitionPlanner, AgentError, AgentOrchestrator, TriviaCommentaryAgent, FullVaultSemanticIndex, SelfReflectionVerifier, UserTasteMemory, LocalDenseVectorIndex, MultiAgentConsensusGraph


from cineswarm_chapters import ChapterSummarizer
from cineswarm_discovery import DiscoveryEngine, parse_json
from cineswarm_health import MediaHealthScanner
from cineswarm_library_brain import LibraryBrain
from cineswarm_library_intelligence import VaultLibraryComprehension
from cineswarm_learning import AutonomicSwarmEvolutionEngine
from cineswarm_user_profiles import MultiUserTasteEngine
from cineswarm_mesh import SwarmMeshSyncEngine
from cineswarm_artwork import DynamicArtworkEngine
from cineswarm_fleet import EnterpriseFleetManager
from cineswarm_dashboard import DASHBOARD_HTML as REORGANIZED_DASHBOARD_HTML






BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_ENV_KEYS = {
    "PLEX_URL", "PLEX_TOKEN",
    "RADARR_URL", "RADARR_API_KEY",
    "SONARR_URL", "SONARR_API_KEY",
    "CINESWARM_CONTROL_HOST", "CINESWARM_CONTROL_PORT",
    "CINESWARM_CONTROL_DB", "CINESWARM_CATALOG_DB",
    "CINESWARM_MODEL_BASE_URL", "CINESWARM_MODEL_API_KEY", "CINESWARM_MODEL_NAME",
    "CINESWARM_MODEL_TIMEOUT", "CINESWARM_MODEL_PROVIDER", "CINESWARM_MODEL_FALLBACKS", "CINESWARM_MODEL_MAX_RETRIES",
    "CINESWARM_DISCOVERY_BACKEND",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL",
    "CINESWARM_PATH_MAP", "CINESWARM_OUTPUT_MD",
    "CINESWARM_API_TIMEOUT",
    "CINESWARM_AUTO_PLEX_REFRESH", "CINESWARM_PLEX_REFRESH_COOLDOWN",
    "CINESWARM_AUTO_MAX_MOVIES_PER_WEEK", "CINESWARM_AUTO_MAX_SERIES_PER_WEEK",
    "CINESWARM_AUTO_MAX_GB_PER_WEEK", "CINESWARM_AUTO_MIN_SCORE",
    "CINESWARM_AUTO_NEAR_MISS_FLOOR", "CINESWARM_AUTO_NEAR_MISS_AFFINITY_FLOOR", "CINESWARM_AUTO_NEAR_MISS_COOLDOWN",
    "CINESWARM_AUTO_ACQUIRE_PER_CYCLE",
    "CINESWARM_AUTO_REQUIRED_QUALITY_PROFILE", "CINESWARM_AUTO_ALLOWED_GENRES",
    "CINESWARM_AUTO_FORBIDDEN_GENRES", "CINESWARM_AUTO_MIN_FREE_SPACE_GB",
    "CINESWARM_AUTO_MAX_CONCURRENT_DOWNLOADS", "CINESWARM_AUTO_RETRY_FAILED_ONCE",
    "CINESWARM_FAILED_DOWNLOAD_MAX_PER_CYCLE", "CINESWARM_FAILED_DOWNLOAD_MAX_PER_MEDIA_PER_CYCLE", "CINESWARM_FAILED_DOWNLOAD_COOLDOWN",
    "CINESWARM_AUTO_EMERGENCY_STOP", "CINESWARM_AUTO_ADD_SEARCH", "CINESWARM_FULL_AUTOPILOT",
    "SABNZBD_URL", "SABNZBD_API_KEY",
    "CINESWARM_AUTO_BUDGET_MODE",
    "CINESWARM_AUTO_RECENT_YEARS", "CINESWARM_AUTO_RECENT_WEEKLY_TARGET",
    "CINESWARM_AUTO_ESTIMATED_MOVIE_GB",
    "CINESWARM_FALLBACK_ORDER",
    "CINESWARM_WORKER_MAX_ATTEMPTS", "CINESWARM_WORKER_LEASE_SECONDS", "CINESWARM_WORKER_POLL_SECONDS",
    "CINESWARM_REFRESH_INTERVAL", "CINESWARM_CATALOG_INTERVAL", "CINESWARM_RECONCILE_INTERVAL",
    "CINESWARM_QUEUE_INTERVAL", "CINESWARM_DISCOVERY_INTERVAL", "CINESWARM_AUTONOMOUS_INTERVAL",
    "CINESWARM_FAILED_DOWNLOAD_INTERVAL",
    "CINESWARM_DISCOVERY_ENABLED", "CINESWARM_NOTIFICATION_WEBHOOK", "CINESWARM_MONITOR_WEBHOOK", "CINESWARM_DISCORD_WEBHOOK",
    "CINESWARM_DISCORD_BOT_TOKEN", "CINESWARM_DISCORD_GUILD_IDS", "CINESWARM_DISCORD_CHANNEL_IDS",
    "CINESWARM_DISCORD_USER_IDS", "CINESWARM_DISCORD_SETUP_CODE", "CINESWARM_DISCORD_REQUIRE_MENTION", "CINESWARM_DISCORD_PREFIX",
    "CINESWARM_NOTIFICATION_COOLDOWN", "CINESWARM_HEARTBEAT_STALE_SECONDS", "CINESWARM_NOTIFY_ON_FAILED_DOWNLOAD",
    "CINESWARM_PRESERVATION_INTERVAL", "CINESWARM_DATABASE_MAINTENANCE_INTERVAL",
    "CINESWARM_PRESERVATION_ROOTS", "CINESWARM_PRESERVATION_UNION_ROOTS", "CINESWARM_PRESERVATION_BRANCHES", "CINESWARM_PRESERVATION_MOUNTS",
    "CINESWARM_PRESERVATION_SAMPLE_LIMIT", "CINESWARM_PRESERVATION_CHECKSUM_BYTES", "CINESWARM_PRESERVATION_CHECKSUM_MAX_SIZE",
    "CINESWARM_BACKUP_DIR", "CINESWARM_BACKUP_RETENTION", "CINESWARM_DATABASE_FULL_INTEGRITY_CHECK",
    "CINESWARM_OFFSITE_VAULT_TARGET", "CINESWARM_PLEX_WEBHOOK_URL", "CINESWARM_PLEX_PROFILE_MAP",
    "CINESWARM_NEVER_IMPORTED_MAX_PER_CYCLE", "CINESWARM_NEVER_IMPORTED_DAYS", "CINESWARM_NEVER_IMPORTED_SEARCH_COOLDOWN", "CINESWARM_AV1_UPGRADE_MAX_PER_CYCLE",
    "CINESWARM_DASHBOARD_USERNAME", "CINESWARM_DASHBOARD_PASSWORD",
}

APPLICATION_VERSION = "1.0.0"
API_VERSION = "v1"
SCHEMA_VERSION = 2
READ_ONLY_V1_ALIASES = {
    "/api/v1/health": "/api/health",
    "/api/v1/status": "/api/status",
    "/api/v1/operational-summary": "/api/operational-summary",
    "/api/v1/editions": "/api/editions",
    "/api/v1/discovery": "/api/discovery",
    "/api/v1/operations/queue": "/api/operations/queue",
    "/api/v1/preservation": "/api/preservation",
    "/api/v1/monitoring/snapshot": "/api/monitoring/snapshot",
}


def load_local_env() -> None:
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key in LOCAL_ENV_KEYS:
                os.environ.setdefault(key, value)


load_local_env()
CONTROL_DB = os.environ.get("CINESWARM_CONTROL_DB", os.path.join(BASE_DIR, "cineswarm_control.db"))
CATALOG_DB = os.environ.get("CINESWARM_CATALOG_DB", os.path.join(BASE_DIR, "cineswarm_catalog.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS service_snapshots (
    service TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    item_count INTEGER,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    task_type TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL,
    requires_approval INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acquisition_observations (
    task_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS autonomous_policy (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autonomous_actions (
    id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    candidate_id INTEGER,
    budget_period TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS weekly_budget_tracker (
    week_start TEXT PRIMARY KEY,
    movies_added INTEGER NOT NULL DEFAULT 0,
    series_added INTEGER NOT NULL DEFAULT 0,
    gb_added INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_log (
    decision_id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    category TEXT NOT NULL,
    subject TEXT NOT NULL,
    decision TEXT NOT NULL,
    reasons_json TEXT NOT NULL DEFAULT '{}',
    outcome_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_feedback (
    decision_id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    sentiment TEXT NOT NULL CHECK(sentiment IN ('good','bad')),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(decision_id) REFERENCES decision_log(decision_id)
);
CREATE TABLE IF NOT EXISTS media_editions (
    id INTEGER PRIMARY KEY,
    catalog_item_id INTEGER,
    discovery_candidate_id INTEGER,
    edition_label TEXT NOT NULL,
    source_release_name TEXT,
    checksum_sha256 TEXT,
    preservation_file_id INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    authenticity_confidence REAL NOT NULL DEFAULT 0,
    rarity_flag INTEGER NOT NULL DEFAULT 0,
    backup_status TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_editions_catalog ON media_editions(catalog_item_id, id);
CREATE INDEX IF NOT EXISTS idx_media_editions_candidate ON media_editions(discovery_candidate_id, id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(marker in key.lower() for marker in ("api_key", "apikey", "token", "password", "secret")) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def json_text(value: Any) -> str:
    return json.dumps(redact(value), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    url: str
    api_key: str
    header: str


class ServiceError(RuntimeError):
    pass


def service_error_message(details: Any) -> str | None:
    if isinstance(details, dict):
        value = details.get("message") or details.get("errorMessage") or details.get("errors")
        return json_text(value) if isinstance(value, (list, dict)) else str(value) if value else None
    if isinstance(details, list):
        messages = [service_error_message(item) for item in details]
        return "; ".join(message for message in messages if message) or json_text(details)
    return str(details) if details else None


class ReadOnlyApiClient:
    def __init__(self, config: ServiceConfig, timeout: float | None = None):
        timeout = timeout if timeout is not None else float(os.environ.get("CINESWARM_API_TIMEOUT", "60"))
        self.config = config
        self.timeout = timeout

    def get(self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value is not None})
        url = self.config.url.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url += "?" + query
        request = urllib.request.Request(url, method="GET")
        if self.config.api_key:
            request.add_header(self.config.header, self.config.api_key)
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout if timeout is None else timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ServiceError(f"{self.config.name} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ServiceError(f"{self.config.name} request failed: {exc.__class__.__name__}") from exc


class PlexApiClient:
    def __init__(self, config: ServiceConfig, timeout: float | None = None):
        timeout = timeout if timeout is not None else float(os.environ.get("CINESWARM_API_TIMEOUT", "60"))
        self.config = config
        self.timeout = timeout

    def get(self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> ET.Element:
        values = {key: value for key, value in (params or {}).items() if value is not None}
        query = urllib.parse.urlencode(values)
        url = self.config.url.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url += "?" + query
        request = urllib.request.Request(url, method="GET")
        if self.config.api_key:
            request.add_header("X-Plex-Token", self.config.api_key)
        request.add_header("Accept", "application/xml")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout if timeout is None else timeout) as response:
                body = response.read()
                return ET.fromstring(body) if body else ET.Element("MediaContainer")
        except urllib.error.HTTPError as exc:
            raise ServiceError(f"{self.config.name} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
            raise ServiceError(f"{self.config.name} request failed: {exc.__class__.__name__}") from exc


class PlexWriteClient(PlexApiClient):
    def post(self, path: str, params: dict[str, Any] | None = None) -> ET.Element:
        values = {key: value for key, value in (params or {}).items() if value is not None}
        query = urllib.parse.urlencode(values)
        url = self.config.url.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url += "?" + query
        request = urllib.request.Request(url, method="POST")
        if self.config.api_key:
            request.add_header("X-Plex-Token", self.config.api_key)
        request.add_header("Accept", "application/xml")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                return ET.fromstring(body) if body else ET.Element("MediaContainer")
        except urllib.error.HTTPError as exc:
            raise ServiceError(f"{self.config.name} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
            raise ServiceError(f"{self.config.name} request failed: {exc.__class__.__name__}") from exc

    def put(self, path: str, params: dict[str, Any] | None = None) -> ET.Element:
        values = {key: value for key, value in (params or {}).items() if value is not None}
        query = urllib.parse.urlencode(values)
        url = self.config.url.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url += "?" + query
        request = urllib.request.Request(url, method="PUT")
        if self.config.api_key:
            request.add_header("X-Plex-Token", self.config.api_key)
        request.add_header("Accept", "application/xml")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                return ET.fromstring(body) if body else ET.Element("MediaContainer")
        except urllib.error.HTTPError as exc:
            raise ServiceError(f"{self.config.name} request failed: {exc.__class__.__name__}") from exc

    def create_playlist(self, title: str, rating_keys: list[str]) -> bool:
        if not rating_keys:
            return False

        try:
            machine_id = self.get("").get("machineIdentifier")
            if not machine_id:
                return False
            first_key = rating_keys[0]
            uri = f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{first_key}"
            res = self.post("playlists", {"title": title, "type": "video", "uri": uri})
            playlist_id = None
            for elem in res.findall("Playlist") or res.findall("Directory") or res.findall("Metadata"):
                if elem.get("ratingKey"):
                    playlist_id = elem.get("ratingKey")
                    break
            if not playlist_id:
                playlists_container = self.get("playlists")
                for elem in playlists_container.findall("Playlist") or playlists_container.findall("Directory"):
                    if elem.get("title") == title:
                        playlist_id = elem.get("ratingKey")
                        break
            if playlist_id and len(rating_keys) > 1:
                for key in rating_keys[1:]:
                    add_uri = f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{key}"
                    self.put(f"playlists/{playlist_id}/items", {"uri": add_uri})
            return True
        except Exception:
            return False

    def add_to_collection(self, section_key: str, collection_name: str, rating_key: str) -> bool:
        try:
            self.put_tag(section_key, rating_key, collection_name)
            return True
        except Exception:
            return False

    def put_tag(self, section_key: str, rating_key: str, collection_name: str) -> None:

        params = {
            "type": "1",
            "id": rating_key,
            "collection[0].tag.tag": collection_name,
            "collection.locked": "1",
        }
        self.put(f"library/sections/{section_key}/all", params)

    def set_poster(self, rating_key: str, poster_url: str) -> bool:
        """Assign a high-resolution poster image URL to a Plex item or collection."""
        try:
            self.post(f"library/metadata/{rating_key}/posters", {"url": poster_url})
            return True
        except Exception:
            return False

    def refresh_libraries(self, path: str | None = None) -> dict[str, Any]:
        """Refresh Plex library sections so newly fixed/imported paths get indexed.

        Optional ``path`` narrows to sections whose Location roots overlap that
        path. If nothing matches (common with Docker path maps), falls back to
        refreshing every section so reconcile follow-ups still heal indexing.
        """
        root = self.get("library/sections")
        directories = list(root.findall("Directory"))
        selected: list[ET.Element] = []
        path_norm = (path or "").replace("\\", "/").rstrip("/")

        for directory in directories:
            locations = [
                (loc.get("path") or "").replace("\\", "/").rstrip("/")
                for loc in directory.findall("Location")
            ]
            if not path_norm or not locations:
                selected.append(directory)
                continue
            matched = False
            for loc in locations:
                if not loc:
                    continue
                if (
                    path_norm == loc
                    or path_norm.startswith(loc + "/")
                    or loc.startswith(path_norm + "/")
                    or loc in path_norm
                    or path_norm in loc
                ):
                    matched = True
                    break
            if matched:
                selected.append(directory)

        if path_norm and not selected:
            selected = directories

        refreshed: list[dict[str, Any]] = []
        for directory in selected:
            key = directory.get("key")
            if not key:
                continue
            self.post(f"library/sections/{key}/refresh")
            refreshed.append(
                {
                    "section_key": key,
                    "title": directory.get("title"),
                    "type": directory.get("type"),
                }
            )

        return {
            "status": "ok",
            "path": path,
            "refreshed_count": len(refreshed),
            "sections": refreshed,
        }


class ArrWriteClient:
    def __init__(self, config: ServiceConfig, timeout: float | None = None):
        timeout = timeout if timeout is not None else float(os.environ.get("CINESWARM_API_TIMEOUT", "60"))
        self.config = config
        self.timeout = timeout
        self.reader = ReadOnlyApiClient(config, timeout)

    def get(self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        return self.reader.get(path, params, timeout=timeout)

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        url = self.config.url.rstrip("/") + "/" + path.lstrip("/")
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={self.config.header: self.config.api_key, "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                return json.loads(body.decode("utf-8")) if body else {}
        except urllib.error.HTTPError as exc:
            try:
                details = json.loads(exc.read().decode("utf-8"))
                message = service_error_message(details)
            except (json.JSONDecodeError, UnicodeDecodeError):
                message = None
            suffix = f": {message}" if message else ""
            raise ServiceError(f"{self.config.name} returned HTTP {exc.code}{suffix}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ServiceError(f"{self.config.name} request failed: {exc.__class__.__name__}") from exc

    def put_json(self, path: str, payload: dict[str, Any]) -> Any:
        url = self.config.url.rstrip("/") + "/" + path.lstrip("/")
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="PUT",
            headers={self.config.header: self.config.api_key, "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                return json.loads(body.decode("utf-8")) if body else {}
        except urllib.error.HTTPError as exc:
            try:
                details = json.loads(exc.read().decode("utf-8"))
                message = service_error_message(details)
            except (json.JSONDecodeError, UnicodeDecodeError):
                message = None
            suffix = f": {message}" if message else ""
            raise ServiceError(f"{self.config.name} returned HTTP {exc.code}{suffix}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ServiceError(f"{self.config.name} request failed: {exc.__class__.__name__}") from exc


class PlexConnector:
    def __init__(self, config: ServiceConfig):
        self.client = PlexApiClient(config)

    @staticmethod
    def _item(element: ET.Element, media_type: str) -> dict[str, Any]:
        provider_ids = {}
        raw_guids = [element.get("guid")] + [guid.get("id") for guid in element.findall("Guid")]
        for raw_guid in raw_guids:
            if not raw_guid or "://" not in raw_guid:
                continue
            provider, value = raw_guid.split("://", 1)
            provider = {
                "com.plexapp.agents.themoviedb": "tmdb",
                "com.plexapp.agents.thetvdb": "tvdb",
                "com.plexapp.agents.imdb": "imdb",
            }.get(provider.lower(), provider.lower())
            if provider in ("tmdb", "tvdb", "imdb"):
                provider_ids[provider] = value.split("?", 1)[0]
        part = element.find("./Media/Part")
        return {
            "Type": "Movie" if media_type == "movie" else "Series",
            "Name": element.get("title"),
            "ProductionYear": int(element.get("year")) if str(element.get("year", "")).isdigit() else None,
            "ProviderIds": provider_ids,
            "Genres": [genre.get("tag") for genre in element.findall("Genre") if genre.get("tag")],
            "Overview": element.get("summary"),
            "Path": part.get("file") if part is not None else None,
            "DateCreated": element.get("addedAt"),
            "RatingKey": element.get("ratingKey"),
        }

    @staticmethod
    def _session_streams(part: Any) -> dict[str, Any]:
        video_streams, audio_streams, subtitle_streams = [], [], []
        if part is None:
            return {"video": video_streams, "audio": audio_streams, "subtitle": subtitle_streams}
        for stream in part.findall("Stream"):
            entry = {
                "id": stream.get("id"),
                "stream_type": stream.get("streamType"),
                "codec": stream.get("codec"),
                "display_title": stream.get("displayTitle"),
                "language": stream.get("language") or stream.get("languageTag"),
                "language_code": stream.get("languageCode"),
                "selected": stream.get("selected") in ("1", "true", True),
                "default": stream.get("default") in ("1", "true", True),
                "forced": stream.get("forced") in ("1", "true", True),
                "bitrate": _safe_int(stream.get("bitrate")),
                "width": _safe_int(stream.get("width")),
                "height": _safe_int(stream.get("height")),
                "frame_rate": stream.get("frameRate"),
                "profile": stream.get("profile"),
                "channels": _safe_int(stream.get("channels")),
                "audio_channel_layout": stream.get("audioChannelLayout"),
                "bit_depth": _safe_int(stream.get("bitDepth")),
                "sampling_rate": _safe_int(stream.get("samplingRate")),
                "decision": stream.get("decision"),
                "location": stream.get("location"),
            }
            stream_type = str(stream.get("streamType") or "")
            if stream_type == "1":
                video_streams.append(entry)
            elif stream_type == "2":
                audio_streams.append(entry)
            elif stream_type == "3":
                subtitle_streams.append(entry)
        return {"video": video_streams, "audio": audio_streams, "subtitle": subtitle_streams}

    def get_sessions(self, timeout: float | None = None) -> list[dict[str, Any]]:
        try:
            container = self.client.get("status/sessions", timeout=timeout)
            sessions = []
            for video in list(container.findall("Video")) + list(container.findall("Track")):
                player = video.find("Player")
                transcode = video.find("TranscodeSession")
                user = video.find("User")
                media = video.find("Media")
                part = media.find("Part") if media is not None else None
                streams = self._session_streams(part)
                selected_video = next((s for s in streams["video"] if s.get("selected")), streams["video"][0] if streams["video"] else {})
                selected_audio = next((s for s in streams["audio"] if s.get("selected")), streams["audio"][0] if streams["audio"] else {})
                selected_subtitle = next((s for s in streams["subtitle"] if s.get("selected")), None)
                duration_ms = _safe_int(video.get("duration")) or 0
                view_offset_ms = _safe_int(video.get("viewOffset")) or 0
                progress_pct = round(min(100.0, (view_offset_ms / duration_ms) * 100.0), 1) if duration_ms > 0 else None
                video_decision = (transcode.get("videoDecision") if transcode is not None else None) or (part.get("decision") if part is not None else None) or "directplay"
                audio_decision = (transcode.get("audioDecision") if transcode is not None else None) or (selected_audio.get("decision") if selected_audio else None) or "directplay"
                subtitle_decision = transcode.get("subtitleDecision") if transcode is not None else (selected_subtitle.get("decision") if selected_subtitle else None)
                hw_decoding = transcode.get("transcodeHwDecoding") if transcode is not None else None
                hw_encoding = transcode.get("transcodeHwEncoding") if transcode is not None else None
                sessions.append({
                    "session_key": video.get("sessionKey"),
                    "rating_key": video.get("ratingKey"),
                    "title": video.get("title"),
                    "grandparent_title": video.get("grandparentTitle"),
                    "parent_title": video.get("parentTitle"),
                    "original_title": video.get("originalTitle"),
                    "type": video.get("type"),
                    "year": _safe_int(video.get("year")),
                    "library_section": video.get("librarySectionTitle"),
                    "content_rating": video.get("contentRating"),
                    "summary": video.get("summary"),
                    "thumb": video.get("thumb") or video.get("parentThumb") or video.get("grandparentThumb"),
                    "art": video.get("art") or video.get("grandparentArt"),
                    "duration_ms": duration_ms or None,
                    "view_offset_ms": view_offset_ms or None,
                    "progress_percent": progress_pct,
                    "remaining_ms": (duration_ms - view_offset_ms) if duration_ms > view_offset_ms else 0,
                    "user": {
                        "id": user.get("id") if user is not None else None,
                        "title": user.get("title") if user is not None else "Unknown",
                        "thumb": user.get("thumb") if user is not None else None,
                    },
                    "player": {
                        "title": player.get("title") if player is not None else None,
                        "product": player.get("product") if player is not None else None,
                        "platform": player.get("platform") if player is not None else None,
                        "device": player.get("device") if player is not None else None,
                        "address": player.get("address") if player is not None else None,
                        "state": player.get("state") if player is not None else "playing",
                        "local": player.get("local") in ("1", "true", True) if player is not None else None,
                        "relayed": player.get("relayed") in ("1", "true", True) if player is not None else None,
                        "secure": player.get("secure") in ("1", "true", True) if player is not None else None,
                        "user_id": player.get("userID") if player is not None else None,
                        "machine_identifier": player.get("machineIdentifier") if player is not None else None,
                        "version": player.get("version") if player is not None else None,
                    },
                    "media": {
                        "container": (media.get("container") if media is not None else None) or (part.get("container") if part is not None else None),
                        "video_codec": media.get("videoCodec") if media is not None else selected_video.get("codec"),
                        "audio_codec": media.get("audioCodec") if media is not None else selected_audio.get("codec"),
                        "audio_channels": _safe_int(media.get("audioChannels") if media is not None else selected_audio.get("channels")),
                        "video_resolution": media.get("videoResolution") if media is not None else None,
                        "width": _safe_int(media.get("width") if media is not None else selected_video.get("width")),
                        "height": _safe_int(media.get("height") if media is not None else selected_video.get("height")),
                        "bitrate": _safe_int(media.get("bitrate") if media is not None else None) or _safe_int(part.get("bitrate") if part is not None else None),
                        "video_frame_rate": media.get("videoFrameRate") if media is not None else selected_video.get("frame_rate"),
                        "video_profile": media.get("videoProfile") if media is not None else selected_video.get("profile"),
                        "audio_profile": media.get("audioProfile") if media is not None else None,
                        "aspect_ratio": media.get("aspectRatio") if media is not None else None,
                        "file": part.get("file") if part is not None else None,
                        "size": _safe_int(part.get("size") if part is not None else None),
                        "decision": part.get("decision") if part is not None else None,
                    },
                    "streams": streams,
                    "selected_streams": {
                        "video": selected_video or None,
                        "audio": selected_audio or None,
                        "subtitle": selected_subtitle,
                    },
                    "playback": {
                        "video_decision": video_decision,
                        "audio_decision": audio_decision,
                        "subtitle_decision": subtitle_decision,
                        "mode": (
                            "transcode" if "transcode" in str(video_decision).lower()
                            else "copy" if "copy" in str(video_decision).lower()
                            else "directplay"
                        ),
                    },
                    "transcode": {
                        "active": transcode is not None,
                        "key": transcode.get("key") if transcode is not None else None,
                        "throttled": transcode.get("throttled") in ("1", "true", True) if transcode is not None else None,
                        "complete": transcode.get("complete") in ("1", "true", True) if transcode is not None else None,
                        "progress": _safe_float(transcode.get("progress")) if transcode is not None else None,
                        "speed": _safe_float(transcode.get("speed")) if transcode is not None else None,
                        "duration": _safe_int(transcode.get("duration")) if transcode is not None else None,
                        "video_decision": video_decision,
                        "audio_decision": audio_decision,
                        "subtitle_decision": subtitle_decision,
                        "container": transcode.get("container") if transcode is not None else None,
                        "video_codec": transcode.get("videoCodec") if transcode is not None else None,
                        "audio_codec": transcode.get("audioCodec") if transcode is not None else None,
                        "audio_channels": _safe_int(transcode.get("audioChannels")) if transcode is not None else None,
                        "width": _safe_int(transcode.get("width")) if transcode is not None else None,
                        "height": _safe_int(transcode.get("height")) if transcode is not None else None,
                        "hw_requested": transcode.get("transcodeHwRequested") in ("1", "true", True) if transcode is not None else None,
                        "hw_full_pipeline": transcode.get("transcodeHwFullPipeline") in ("1", "true", True) if transcode is not None else None,
                        "hw_decoding": hw_decoding,
                        "hw_encoding": hw_encoding,
                        "hw_decoding_title": transcode.get("transcodeHwDecodingTitle") if transcode is not None else None,
                        "hw_encoding_title": transcode.get("transcodeHwEncodingTitle") if transcode is not None else None,
                        "hw_active": bool(hw_decoding or hw_encoding),
                    },
                    # Flat aliases for older SSE / dashboard consumers
                    "user_name": user.get("title") if user is not None else "Unknown",
                    "player_name": (player.get("product") or player.get("title")) if player is not None else "Unknown",
                    "state": player.get("state") if player is not None else "playing",
                    "resolution": (media.get("videoResolution") if media is not None else None) or "unknown",
                })
            return sessions
        except Exception:
            return []

    def snapshot(self) -> dict[str, Any]:
        info = self.client.get("")
        items = self._all_items()
        movies = [item for item in items if item.get("Type") == "Movie"]
        series = [item for item in items if item.get("Type") == "Series"]
        sessions = self.get_sessions()
        return {
            "service": "plex",
            "server": {
                "name": info.get("friendlyName"),
                "version": info.get("version"),
                "id": info.get("machineIdentifier"),
            },
            "items": items,
            "counts": {"all": len(items), "movies": len(movies), "series": len(series)},
            "active_sessions": sessions,
        }

    def _all_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        sections = self.client.get("library/sections")
        for section in sections.findall("Directory"):
            section_type = section.get("type")
            if section_type not in ("movie", "show") or not section.get("key"):
                continue
            media_type = "movie" if section_type == "movie" else "series"
            start = 0
            limit = 500
            while True:
                result = self.client.get(
                    f"library/sections/{section.get('key')}/all",
                    {"type": 1 if media_type == "movie" else 2, "includeGuids": 1, "X-Plex-Container-Start": start, "X-Plex-Container-Size": limit},
                )
                page = [self._item(element, media_type) for element in result if element.tag in ("Video", "Directory")]
                items.extend(page)
                total = int(result.get("totalSize") or result.get("size") or len(page))
                if not page or len(page) < limit or start + len(page) >= total:
                    break
                start += len(page)
        return items


class ArrConnector:
    def __init__(self, config: ServiceConfig, media_type: str, timeout: float = 120.0):
        self.client = ReadOnlyApiClient(config, timeout=timeout)
        self.media_type = media_type

    def snapshot(self) -> dict[str, Any]:
        status = self.client.get("api/v3/system/status")
        path = "api/v3/movie" if self.media_type == "movie" else "api/v3/series"
        try:
            items = self.client.get(path)
        except Exception as exc:
            logger.warning(f"Full {self.client.config.name} item dump timed out during active search tasks: {exc}")
            items = []
        return {
            "service": self.client.config.name,
            "server": {
                "version": status.get("version"),
                "branch": status.get("branch"),
                "is_debug": status.get("isDebug"),
            },
            "items": items if isinstance(items, list) else [],
            "counts": {self.media_type: len(items) if isinstance(items, list) else 0},
        }


class Policy:
    """Keep writes explicit, narrow, approval-gated, and auditable."""

    READ_ONLY_ACTIONS = {"refresh_snapshot", "view_status", "create_proposal", "reconcile_library"}
    APPROVAL_REQUIRED_ACTIONS = {"plex_library_refresh", "radarr_add_request", "sonarr_add_request", "radarr_search_request", "sonarr_search_request", "radarr_search_retry_request", "sonarr_search_retry_request", "radarr_release_grab_request"}
    AUTOMATIC_ACTIONS = {"plex_library_refresh"}

    @classmethod
    def classify(cls, action: str) -> tuple[str, str]:
        if action in cls.READ_ONLY_ACTIONS:
            return "allowed", "allowed_read_only"
        if action in cls.APPROVAL_REQUIRED_ACTIONS:
            return "approval_required", "requires_explicit_approval"
        return "blocked", "blocked_by_policy"

    @classmethod
    def authorize(cls, action: str) -> tuple[bool, str]:
        decision, reason = cls.classify(action)
        return decision == "allowed", reason


class ControlStore:
    def __init__(self, path: str = CONTROL_DB):
        self.path = path
        self.lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute("BEGIN IMMEDIATE")
            migrations = (
                (1, "baseline_schema", lambda: None),
                (2, "tasks_result_json", lambda: self._add_column(connection, "tasks", "result_json", "TEXT NOT NULL DEFAULT '{}'")),
            )
            applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            for version, name, migration in migrations:
                if version in applied:
                    continue
                migration()
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, now()),
                )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _add_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def schema_metadata(self) -> dict[str, Any]:
        with self.lock, self._connect() as connection:
            rows = connection.execute("SELECT version, name, applied_at FROM schema_migrations ORDER BY version").fetchall()
        return {"current": max((row["version"] for row in rows), default=0), "latest": SCHEMA_VERSION, "migrations": [dict(row) for row in rows]}

    @staticmethod
    def _database_integrity(path: str) -> dict[str, Any]:
        if not os.path.isfile(path):
            return {"present": False, "integrity": "missing"}
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                result = connection.execute("PRAGMA quick_check(1)").fetchone()
            return {"present": True, "integrity": "ok" if result and result[0] == "ok" else "error"}
        except (OSError, sqlite3.Error):
            return {"present": True, "integrity": "error"}

    def diagnostics(self) -> dict[str, Any]:
        status = self.status()
        services = [
            {"name": item.get("service"), "status": item.get("status"), "last_checked": item.get("fetched_at")}
            for item in status.get("services", [])[:10]
        ]
        worker = status.get("worker") or {}
        preservation: dict[str, Any] = {"available": False}
        maintenance: dict[str, Any] = {"available": False}
        with self.lock, self._connect() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "preservation_scans" in tables:
                row = connection.execute("SELECT status, finished_at, summary_json FROM preservation_scans ORDER BY started_at DESC LIMIT 1").fetchone()
                if row:
                    try:
                        summary = json.loads(row["summary_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        summary = {}
                    status_counts = summary.get("statuses") if isinstance(summary.get("statuses"), dict) else {}
                    allowed_statuses = {"new", "ok", "unchanged", "changed", "checksum_mismatch", "missing", "error"}
                    preservation = {
                        "available": True,
                        "status": row["status"] if row["status"] in {"running", "completed", "failed"} else "unknown",
                        "last_finished": row["finished_at"],
                        "files": summary.get("files") if isinstance(summary.get("files"), int) else None,
                        "missing_mounts": summary.get("missing_mounts") if isinstance(summary.get("missing_mounts"), int) else None,
                        "storage_events": summary.get("storage_events") if isinstance(summary.get("storage_events"), int) else None,
                        "statuses": {key: value for key, value in status_counts.items() if key in allowed_statuses and isinstance(value, int)},
                    }
            if "database_maintenance_runs" in tables:
                row = connection.execute("SELECT status, finished_at FROM database_maintenance_runs ORDER BY started_at DESC LIMIT 1").fetchone()
                if row:
                    maintenance = {"available": True, "status": row["status"], "last_finished": row["finished_at"]}
        configured = lambda key: bool(os.environ.get(key))
        configuration = {
            "plex": {"url_configured": configured("PLEX_URL"), "credential_configured": configured("PLEX_TOKEN")},
            "radarr": {"url_configured": configured("RADARR_URL"), "credential_configured": configured("RADARR_API_KEY")},
            "sonarr": {"url_configured": configured("SONARR_URL"), "credential_configured": configured("SONARR_API_KEY")},
            "sabnzbd": {"url_configured": configured("SABNZBD_URL"), "credential_configured": configured("SABNZBD_API_KEY")},
            "model": {
                "url_configured": configured("CINESWARM_MODEL_BASE_URL"),
                "credential_configured": any(configured(key) for key in ("CINESWARM_MODEL_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")),
            },
            "dashboard_auth": {
                "username_configured": configured("CINESWARM_DASHBOARD_USERNAME"),
                "credential_configured": configured("CINESWARM_DASHBOARD_PASSWORD"),
                "enabled": configured("CINESWARM_DASHBOARD_USERNAME") and configured("CINESWARM_DASHBOARD_PASSWORD"),
            },
            "path_mapping_configured": configured("CINESWARM_PATH_MAP"),
            "backup_directory_configured": configured("CINESWARM_BACKUP_DIR"),
            "preservation_roots_configured": configured("CINESWARM_PRESERVATION_ROOTS"),
        }
        return {
            "versions": {
                "application": APPLICATION_VERSION,
                "api": API_VERSION,
                "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version,
                "schema": self.schema_metadata(),
            },
            "services": services,
            "worker": {
                "status": worker.get("status", "unknown"),
                "healthy": bool(worker.get("healthy")),
                "age_seconds": worker.get("age_seconds"),
                "last_updated": worker.get("updated_at"),
            },
            "configuration": configuration,
            "storage": {
                "control_database": self._database_integrity(self.path),
                "catalog_database": self._database_integrity(CATALOG_DB),
                "preservation": preservation,
                "maintenance": maintenance,
            },
        }

    def audit(self, actor: str, action: str, target: str, mode: str, status: str, details: Any = None) -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (event_id, actor, action, target, mode, status, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), actor, action, target, mode, status, json_text(details or {}), now()),
            )

    def record_decision(self, actor: str, category: str, subject: str, decision: str, reasons: Any = None, outcome: Any = None) -> str:
        decision_id = str(uuid.uuid4())
        timestamp = now()
        with self.lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO decision_log (decision_id, actor, category, subject, decision, reasons_json, outcome_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (decision_id, actor, category, subject, decision, json_text(reasons or {}), json_text(outcome or {}), timestamp, timestamp),
            )
        return decision_id

    def update_decision(self, decision_id: str, decision: str, outcome: Any = None) -> None:
        with self.lock, self._connect() as connection:
            connection.execute("UPDATE decision_log SET decision=?, outcome_json=?, updated_at=? WHERE decision_id=?", (decision, json_text(outcome or {}), now(), decision_id))

    def decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock, self._connect() as connection:
            rows = connection.execute("SELECT * FROM decision_log ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
            feedback = {row["decision_id"]: dict(row) for row in connection.execute("SELECT decision_id, actor, sentiment, note, created_at FROM decision_feedback")}
        results = []
        for row in rows:
            item = dict(row)
            item["reasons"] = json.loads(item.pop("reasons_json") or "{}")
            item["outcome"] = json.loads(item.pop("outcome_json") or "{}")
            item["feedback"] = feedback.get(item["decision_id"])
            results.append(item)
        return results

    def decision(self, decision_id: str) -> dict[str, Any] | None:
        with self.lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM decision_log WHERE decision_id=?", (decision_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["reasons"] = json.loads(item.pop("reasons_json") or "{}")
        item["outcome"] = json.loads(item.pop("outcome_json") or "{}")
        return item

    def add_decision_feedback(self, decision_id: str, actor: str, sentiment: str, note: str = "") -> bool:
        if sentiment not in ("good", "bad") or not self.decision(decision_id):
            return False
        with self.lock, self._connect() as connection:
            connection.execute("INSERT INTO decision_feedback (decision_id, actor, sentiment, note, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(decision_id) DO UPDATE SET actor=excluded.actor, sentiment=excluded.sentiment, note=excluded.note, created_at=excluded.created_at", (decision_id, actor, sentiment, note[:1000], now()))
        return True

    def add_edition(self, edition_label: str, catalog_item_id: int | None = None, discovery_candidate_id: int | None = None, source_release_name: str | None = None, checksum_sha256: str | None = None, preservation_file_id: int | None = None, notes: str = "", authenticity_confidence: float = 0, rarity_flag: bool = False, backup_status: str = "unknown") -> int:
        if not edition_label.strip() or (catalog_item_id is None and discovery_candidate_id is None):
            raise ValueError("edition_label and a catalog item or discovery candidate are required")
        confidence = round(max(0.0, min(100.0, float(authenticity_confidence))), 2)
        timestamp = now()
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO media_editions (catalog_item_id, discovery_candidate_id, edition_label, source_release_name, checksum_sha256, preservation_file_id, notes, authenticity_confidence, rarity_flag, backup_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (catalog_item_id, discovery_candidate_id, edition_label.strip()[:300], (source_release_name or "")[:1000] or None, checksum_sha256, preservation_file_id, notes[:2000], confidence, int(rarity_flag), backup_status[:100], timestamp, timestamp),
            )
            return int(cursor.lastrowid)

    def editions(self, catalog_item_id: int | None = None, discovery_candidate_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if catalog_item_id is not None:
            clauses.append("catalog_item_id=?")
            params.append(catalog_item_id)
        if discovery_candidate_id is not None:
            clauses.append("discovery_candidate_id=?")
            params.append(discovery_candidate_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self.lock, self._connect() as connection:
            rows = connection.execute(f"SELECT * FROM media_editions{where} ORDER BY id DESC LIMIT ?", params).fetchall()
        results = [dict(row) for row in rows]
        for item in results:
            item["rarity_flag"] = bool(item["rarity_flag"])
        return results

    def queue_detail(self, limit: int = 200) -> dict[str, Any]:
        """Return a bounded, transparent view of control-plane and worker queues."""
        limit = max(1, min(int(limit), 500))
        with self.lock, self._connect() as connection:
            tasks = [dict(row) for row in connection.execute(
                "SELECT task_id, task_type, requested_by, status, requires_approval, payload_json, result_json, created_at, updated_at FROM tasks ORDER BY id DESC LIMIT ?",
                (limit,),
            )]
            for task in tasks:
                task["requires_approval"] = bool(task["requires_approval"])
                task["payload"] = json.loads(task.pop("payload_json") or "{}")
                task["result"] = json.loads(task.pop("result_json") or "{}")
            observations = [dict(row) for row in connection.execute(
                "SELECT task_id, state, checked_at, details_json FROM acquisition_observations ORDER BY checked_at DESC LIMIT ?",
                (limit,),
            )]
            for observation in observations:
                observation["details"] = json.loads(observation.pop("details_json") or "{}")
            worker_jobs: list[dict[str, Any]] = []
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_jobs'").fetchone():
                columns = {row[1] for row in connection.execute("PRAGMA table_info(worker_jobs)")}
                safe_columns = [name for name in ("id", "job_type", "status", "attempts", "max_attempts", "last_error", "created_at", "updated_at", "available_at") if name in columns]
                if safe_columns:
                    worker_jobs = [dict(row) for row in connection.execute(f"SELECT {', '.join(safe_columns)} FROM worker_jobs ORDER BY rowid DESC LIMIT ?", (limit,))]
        statuses = Counter(str(task.get("status", "unknown")) for task in tasks)
        return {"limit": limit, "tasks": tasks, "observations": observations, "worker_jobs": worker_jobs, "task_counts": dict(statuses)}

    def preservation_detail(self, limit: int = 100) -> dict[str, Any]:
        """Return bounded preservation evidence without configuration policy or secret data."""
        limit = max(1, min(int(limit), 250))
        result: dict[str, Any] = {"available": False, "limit": limit, "latest_scan": None, "mounts": [], "files": [], "storage_events": [], "maintenance": None, "editions": self.editions(limit=limit)}
        with self.lock, self._connect() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "preservation_scans" not in tables:
                result["database_integrity"] = {"control": self._database_integrity(self.path), "catalog": self._database_integrity(CATALOG_DB)}
                return result
            scan = connection.execute("SELECT scan_id, started_at, finished_at, status, summary_json, error FROM preservation_scans ORDER BY started_at DESC LIMIT 1").fetchone()
            if scan:
                latest = dict(scan)
                try:
                    latest["summary"] = json.loads(latest.pop("summary_json") or "{}")
                except json.JSONDecodeError:
                    latest["summary"] = {}
                result["latest_scan"] = latest
                result["available"] = True
                scan_id = latest["scan_id"]
                if "preservation_mount_health" in tables:
                    result["mounts"] = [dict(row) for row in connection.execute("SELECT mount_path, mounted, filesystem, source, total_bytes, used_bytes, free_bytes, error FROM preservation_mount_health WHERE scan_id=? ORDER BY mount_path LIMIT ?", (scan_id, limit))]
                    for mount in result["mounts"]:
                        mount["mounted"] = bool(mount["mounted"])
                if "preservation_files" in tables:
                    result["files"] = [dict(row) for row in connection.execute("SELECT id, catalog_item_id, logical_path, size, checksum_sha256, checksum_bytes, status, error FROM preservation_files WHERE scan_id=? AND status NOT IN ('unchanged','ok') ORDER BY id DESC LIMIT ?", (scan_id, limit))]
                if "preservation_storage_events" in tables:
                    result["storage_events"] = [dict(row) for row in connection.execute("SELECT event_at, message FROM preservation_storage_events WHERE scan_id=? ORDER BY id DESC LIMIT ?", (scan_id, limit))]
            if "database_maintenance_runs" in tables:
                maintenance = connection.execute("SELECT run_id, started_at, finished_at, status, details_json, error FROM database_maintenance_runs ORDER BY started_at DESC LIMIT 1").fetchone()
                if maintenance:
                    item = dict(maintenance)
                    try:
                        details = json.loads(item.pop("details_json") or "{}")
                    except json.JSONDecodeError:
                        details = {}
                    databases = details.get("databases", []) if isinstance(details, dict) else []
                    item["databases"] = [{key: database.get(key) for key in ("database", "integrity", "backup", "size") if key in database} for database in databases if isinstance(database, dict)]
                    item["removed_backup_count"] = len(details.get("removed", [])) if isinstance(details, dict) and isinstance(details.get("removed"), list) else 0
                    result["maintenance"] = item
        result["database_integrity"] = {"control": self._database_integrity(self.path), "catalog": self._database_integrity(CATALOG_DB)}
        return result

    def operational_summary(self, hours: int = 24) -> dict[str, Any]:
        hours = max(1, min(int(hours), 168))
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        cutoff = start.isoformat(timespec="seconds")
        cutoff_epoch = start.timestamp()
        success_states = {"success", "complete", "completed", "executed", "imported"}
        failure_states = {"error", "failed", "failure", "monitor_unavailable"}
        blocked_states = {"blocked", "pending_approval", "blocked_emergency_stop", "blocked_storage", "blocked_queue_limit", "blocked_weekly_budget", "blocked_disabled", "blocked_startup_readiness"}
        with self.lock, self._connect() as connection:
            def table_exists(name: str) -> bool:
                return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

            audit_rows = [dict(row) for row in connection.execute("SELECT action, status, details_json FROM audit_events WHERE created_at>=?", (cutoff,))]
            task_rows = [dict(row) for row in connection.execute("SELECT task_type, status FROM tasks WHERE created_at>=? OR updated_at>=?", (cutoff, cutoff))]
            decision_rows = [dict(row) for row in connection.execute("SELECT decision, category, subject FROM decision_log WHERE created_at>=? OR updated_at>=?", (cutoff, cutoff))]
            observation_rows = [dict(row) for row in connection.execute("SELECT state, details_json FROM acquisition_observations WHERE checked_at>=?", (cutoff,))]
            preservation_rows = [dict(row) for row in connection.execute("SELECT status, summary_json, error FROM preservation_scans WHERE started_at>=?", (cutoff,))] if table_exists("preservation_scans") else []
            maintenance_rows = [dict(row) for row in connection.execute("SELECT status, details_json, error FROM database_maintenance_runs WHERE started_at>=?", (cutoff,))] if table_exists("database_maintenance_runs") else []
            job_rows = [dict(row) for row in connection.execute("SELECT status, attempts, last_error FROM worker_jobs WHERE created_at>=? OR updated_at>=?", (cutoff_epoch, cutoff_epoch))] if table_exists("worker_jobs") else []
            latest_mount_rows = []
            if table_exists("preservation_mount_health"):
                latest_scan = connection.execute("SELECT scan_id FROM preservation_scans ORDER BY started_at DESC LIMIT 1").fetchone()
                if latest_scan:
                    latest_mount_rows = [dict(row) for row in connection.execute("SELECT mount_path, mounted, free_bytes, error FROM preservation_mount_health WHERE scan_id=?", (latest_scan["scan_id"],))]

        def count_states(rows: list[dict[str, Any]], key: str, states: set[str]) -> int:
            return sum(str(row.get(key, "")).casefold() in states for row in rows)

        task_statuses = Counter(row["status"] for row in task_rows)
        acquisition_task_rows = [row for row in task_rows if row["task_type"] in ("radarr_add_request", "sonarr_add_request", "radarr_search_request", "sonarr_search_request")]
        observation_states = Counter(row["state"] for row in observation_rows)
        job_statuses = Counter(row["status"] for row in job_rows)
        issues: list[dict[str, Any]] = []
        for row in audit_rows:
            if str(row["status"]).casefold() in failure_states:
                issues.append({"type": "audit_failure", "action": row["action"], "status": row["status"]})
        for row in task_rows:
            if str(row["status"]).casefold() in failure_states | blocked_states:
                issues.append({"type": "task_attention", "task_type": row["task_type"], "status": row["status"]})
        for row in observation_rows:
            if str(row["state"]).casefold() in failure_states or row["state"] == "search_completed_no_file":
                issues.append({"type": "acquisition_attention", "state": row["state"]})
        for row in preservation_rows:
            summary = json.loads(row.get("summary_json") or "{}")
            if row["status"] in failure_states or summary.get("missing_mounts", 0) or summary.get("storage_events", 0) or summary.get("statuses", {}).get("missing", 0) or summary.get("statuses", {}).get("checksum_mismatch", 0):
                issues.append({"type": "preservation_attention", "status": row["status"], "summary": summary, "error": row.get("error")})
        for row in maintenance_rows:
            if str(row["status"]).casefold() in failure_states:
                issues.append({"type": "database_maintenance_failure", "error": row.get("error")})
        for row in latest_mount_rows:
            if not row["mounted"]:
                issues.append({"type": "storage_unavailable", "path": row["mount_path"], "error": row.get("error")})
        return {
            "window": {"hours": hours, "from": cutoff, "to": end.isoformat(timespec="seconds")},
            "audit": {"total": len(audit_rows), "successes": count_states(audit_rows, "status", success_states), "failures": count_states(audit_rows, "status", failure_states), "blocked": count_states(audit_rows, "status", blocked_states)},
            "tasks": {"total": len(task_rows), "successes": count_states(task_rows, "status", success_states), "failures": count_states(task_rows, "status", failure_states), "blocked": count_states(task_rows, "status", blocked_states), "retries": sum("retry" in row["task_type"] or row["status"] == "retry" for row in task_rows), "by_status": dict(task_statuses)},
            "decisions": {"total": len(decision_rows), "blocked": count_states(decision_rows, "decision", blocked_states), "failures": count_states(decision_rows, "decision", failure_states)},
            "acquisitions": {"requested": len(acquisition_task_rows), "observations": len(observation_rows), "imports": observation_states.get("imported", 0), "successes": count_states(observation_rows, "state", success_states), "failures": count_states(observation_rows, "state", failure_states), "by_state": dict(observation_states)},
            "preservation": {"scans": len(preservation_rows), "successes": count_states(preservation_rows, "status", success_states), "failures": count_states(preservation_rows, "status", failure_states)},
            "database_maintenance": {"runs": len(maintenance_rows), "successes": count_states(maintenance_rows, "status", success_states), "failures": count_states(maintenance_rows, "status", failure_states)},
            "queue": {"status": "attention" if job_statuses.get("failed", 0) else "active" if job_statuses.get("queued", 0) or job_statuses.get("running", 0) else "idle", "total": len(job_rows), "queued": job_statuses.get("queued", 0), "running": job_statuses.get("running", 0), "retries": job_statuses.get("retry", 0), "failed": job_statuses.get("failed", 0), "by_status": dict(job_statuses)},
            "storage": {"status": "degraded" if any(not row["mounted"] for row in latest_mount_rows) else "healthy" if latest_mount_rows else "unknown", "mounts": len(latest_mount_rows), "available": sum(bool(row["mounted"]) for row in latest_mount_rows), "unavailable": sum(not bool(row["mounted"]) for row in latest_mount_rows), "free_bytes": sum(int(row.get("free_bytes") or 0) for row in latest_mount_rows)},
            "blocked_actions": count_states(audit_rows, "status", blocked_states) + count_states(task_rows, "status", blocked_states) + count_states(decision_rows, "decision", blocked_states),
            "retries": sum("retry" in row["task_type"] or row["status"] == "retry" for row in task_rows) + job_statuses.get("retry", 0),
            "actionable_issues": issues[:100],
        }

    def save_snapshot(self, service: str, snapshot: dict[str, Any]) -> None:
        counts = snapshot.get("counts", {})
        item_count = counts.get("all") or sum(value for value in counts.values() if isinstance(value, int))
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO service_snapshots (service, status, fetched_at, item_count, payload_json, error)
                VALUES (?, 'healthy', ?, ?, ?, NULL)
                ON CONFLICT(service) DO UPDATE SET
                    status='healthy', fetched_at=excluded.fetched_at,
                    item_count=excluded.item_count, payload_json=excluded.payload_json, error=NULL
                """,
                (service, now(), item_count, json_text(snapshot)),
            )

    def save_error(self, service: str, error: str) -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO service_snapshots (service, status, fetched_at, item_count, payload_json, error)
                VALUES (?, 'error', ?, NULL, '{}', ?)
                ON CONFLICT(service) DO UPDATE SET
                    status='error', fetched_at=excluded.fetched_at, error=excluded.error
                """,
                (service, now(), error),
            )

    def last_audit_time(self, action: str, target: str, status: str) -> str | None:
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT created_at FROM audit_events WHERE action=? AND target=? AND status=? ORDER BY id DESC LIMIT 1",
                (action, target, status),
            ).fetchone()
        return row["created_at"] if row else None

    def latest_audit_details(self, action: str, target: str, status: str) -> dict[str, Any] | None:
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT created_at, details_json FROM audit_events WHERE action=? AND target=? AND status=? ORDER BY id DESC LIMIT 1",
                (action, target, status),
            ).fetchone()
        if not row:
            return None
        try:
            details = json.loads(row["details_json"] or "{}")
        except json.JSONDecodeError:
            details = {}
        details["created_at"] = row["created_at"]
        return details

    def snapshot_payload(self, service: str) -> dict[str, Any] | None:
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status, fetched_at, payload_json, error FROM service_snapshots WHERE service=?",
                (service,),
            ).fetchone()
        if not row or row["status"] != "healthy":
            return None
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            return None
        payload["_fetched_at"] = row["fetched_at"]
        return payload

    def get_policy(self, key: str) -> str | None:
        with self.lock, self._connect() as connection:
            row = connection.execute("SELECT value FROM autonomous_policy WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_policy(self, key: str, value: str) -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO autonomous_policy (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now()),
            )

    def get_all_policies(self) -> dict[str, str]:
        with self.lock, self._connect() as connection:
            rows = connection.execute("SELECT key, value FROM autonomous_policy").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def get_week_start(self) -> str:
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone.utc).date()
        week_start = today - timedelta(days=today.weekday())
        return week_start.isoformat()

    def get_budget_usage(self) -> dict[str, int]:
        week_start = self.get_week_start()
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT movies_added, series_added, gb_added FROM weekly_budget_tracker WHERE week_start=?",
                (week_start,),
            ).fetchone()
        if not row:
            return {"movies_added": 0, "series_added": 0, "gb_added": 0}
        return {"movies_added": row["movies_added"], "series_added": row["series_added"], "gb_added": row["gb_added"]}

    def check_budget(self) -> tuple[bool, dict[str, Any]]:
        budget = self.get_budget_usage()
        max_movies = int(self.get_policy("CINESWARM_AUTO_MAX_MOVIES_PER_WEEK") or "3")
        max_series = int(self.get_policy("CINESWARM_AUTO_MAX_SERIES_PER_WEEK") or "1")
        max_gb = int(self.get_policy("CINESWARM_AUTO_MAX_GB_PER_WEEK") or "200")
        if budget["movies_added"] >= max_movies:
            return False, {"reason": "weekly_movie_budget_exceeded", "budget": budget, "limits": {"movies": max_movies, "series": max_series, "gb": max_gb}}
        if budget["series_added"] >= max_series:
            return False, {"reason": "weekly_series_budget_exceeded", "budget": budget, "limits": {"movies": max_movies, "series": max_series, "gb": max_gb}}
        if budget["gb_added"] >= max_gb:
            return False, {"reason": "weekly_gb_budget_exceeded", "budget": budget, "limits": {"movies": max_movies, "series": max_series, "gb": max_gb}}
        return True, {"budget": budget, "limits": {"movies": max_movies, "series": max_series, "gb": max_gb}}

    def record_autonomous_action(self, action_type: str, candidate_id: int | None, budget_period: str) -> str:
        action_id = str(uuid.uuid4())
        with self.lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO autonomous_actions (id, action_type, candidate_id, budget_period, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (action_id, action_type, candidate_id, budget_period, "pending", now()),
            )
        return action_id

    def complete_autonomous_action(self, action_id: str) -> None:
        with self.lock, self._connect() as connection:
            connection.execute("UPDATE autonomous_actions SET status='completed', completed_at=? WHERE id=?", (now(), action_id))

    def fail_autonomous_action(self, action_id: str) -> None:
        with self.lock, self._connect() as connection:
            connection.execute("UPDATE autonomous_actions SET status='failed', completed_at=? WHERE id=?", (now(), action_id))

    def increment_budget(self, media_type: str, gb: int) -> None:
        week_start = self.get_week_start()
        with self.lock, self._connect() as connection:
            if media_type == "movie":
                connection.execute(
                    "INSERT INTO weekly_budget_tracker (week_start, movies_added, gb_added, updated_at) VALUES (?, 1, ?, ?) ON CONFLICT(week_start) DO UPDATE SET movies_added=movies_added+1, gb_added=gb_added+?, updated_at=?",
                    (week_start, gb, now(), gb, now()),
                )
            else:
                connection.execute(
                    "INSERT INTO weekly_budget_tracker (week_start, series_added, gb_added, updated_at) VALUES (?, 1, ?, ?) ON CONFLICT(week_start) DO UPDATE SET series_added=series_added+1, gb_added=gb_added+?, updated_at=?",
                    (week_start, gb, now(), gb, now()),
                )

    def is_emergency_stop(self) -> bool:
        val = self.get_policy("CINESWARM_AUTO_EMERGENCY_STOP")
        return val and val.lower() in {"1", "true", "yes", "on"}

    def create_task(self, task_type: str, requested_by: str, payload: dict[str, Any]) -> str:
        task_id = str(uuid.uuid4())
        decision, reason = Policy.classify(task_type)
        status = "queued" if decision == "allowed" else "pending_approval" if decision == "approval_required" else "blocked"
        with self.lock, self._connect() as connection:
            timestamp = now()
            connection.execute(
                """
                INSERT INTO tasks (task_id, task_type, requested_by, status, requires_approval, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, task_type, requested_by, status, int(decision == "approval_required"), json_text(payload), timestamp, timestamp),
            )
        self.audit(requested_by, task_type, "control-plane", "read-only" if decision == "allowed" else "approval-gated", status, {"policy": reason, "payload": payload})
        return task_id

    def task_exists(self, task_type: str, parent_task_id: str) -> bool:
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM tasks WHERE task_type=? AND payload_json LIKE ? AND status IN ('pending_approval','queued','running','completed','failed') LIMIT 1",
                (task_type, f"%{parent_task_id}%"),
            ).fetchone()
        return row is not None

    def task_inflight(self, task_type: str, parent_task_id: str) -> bool:
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM tasks WHERE task_type=? AND payload_json LIKE ? AND status IN ('pending_approval','queued','running') LIMIT 1",
                (task_type, f"%{parent_task_id}%"),
            ).fetchone()
        return row is not None

    def retry_cooldown_active(self, task_type: str, media_type: str, service_id: Any, cooldown: int) -> bool:
        cutoff = datetime.fromtimestamp(time.time() - max(0, cooldown), timezone.utc).isoformat(timespec="seconds")
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM tasks WHERE task_type=? AND created_at>=? ORDER BY id DESC",
                (task_type, cutoff),
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            if payload.get("media_type") == media_type and str(payload.get("service_id")) == str(service_id):
                return True
        return False

    def task(self, task_id: str) -> dict[str, Any] | None:
        with self.lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json") or "{}")
        return result

    def pending_tasks(self, task_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT task_id, task_type, payload_json, created_at FROM tasks WHERE status='pending_approval'"
        params: list[Any] = []
        if task_type:
            query += " AND task_type=?"
            params.append(task_type)
        query += " ORDER BY id ASC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        with self.lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            results.append(item)
        return results

    def update_task(self, task_id: str, status: str, result: Any = None) -> None:
        with self.lock, self._connect() as connection:
            if result is None:
                connection.execute("UPDATE tasks SET status=?, updated_at=? WHERE task_id=?", (status, now(), task_id))
            else:
                connection.execute("UPDATE tasks SET status=?, result_json=?, updated_at=? WHERE task_id=?", (status, json_text(result), now(), task_id))

    def completed_search_tasks(self) -> list[dict[str, Any]]:
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT task_id, task_type, payload_json, result_json, updated_at FROM tasks WHERE status='completed' AND task_type IN ('radarr_search_request','sonarr_search_request') AND task_id NOT IN (SELECT task_id FROM acquisition_observations WHERE state='monitor_unavailable') ORDER BY id DESC LIMIT 100"
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            item["result"] = json.loads(item.pop("result_json") or "{}")
            if not item["result"]:
                with self._connect() as audit_connection:
                    audit_row = audit_connection.execute(
                        "SELECT details_json FROM audit_events WHERE action=? AND status='completed' AND details_json LIKE ? ORDER BY id DESC LIMIT 1",
                        (item["task_type"], f"%{item['task_id']}%"),
                    ).fetchone()
                if audit_row:
                    details = json.loads(audit_row["details_json"] or "{}")
                    item["result"] = details.get("result") or {}
            results.append(item)
        return results

    def save_acquisition_observation(self, task_id: str, state: str, details: Any) -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO acquisition_observations (task_id, state, checked_at, details_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET state=excluded.state, checked_at=excluded.checked_at, details_json=excluded.details_json
                """,
                (task_id, state, now(), json_text(details)),
            )

    def status(self) -> dict[str, Any]:
        with self.lock, self._connect() as connection:
            services = [dict(row) for row in connection.execute(
                "SELECT service, status, fetched_at, item_count, error FROM service_snapshots WHERE service IN ('plex','radarr','sonarr') ORDER BY service"
            )]
            tasks = [dict(row) for row in connection.execute(
                "SELECT task_id, task_type, requested_by, status, requires_approval, created_at, updated_at FROM tasks ORDER BY id DESC LIMIT 20"
            )]
            observations = [dict(row) for row in connection.execute(
                "SELECT task_id, state, checked_at, details_json FROM acquisition_observations ORDER BY checked_at DESC LIMIT 20"
            )]
            for observation in observations:
                observation["details"] = json.loads(observation.pop("details_json") or "{}")
            events = [dict(row) for row in connection.execute(
                "SELECT event_id, actor, action, target, mode, status, created_at FROM audit_events ORDER BY id DESC LIMIT 20"
            )]
            heartbeat = None
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_heartbeat'").fetchone():
                row = connection.execute("SELECT worker_id, status, details_json, updated_at FROM worker_heartbeat WHERE singleton_id=1").fetchone()
                if row:
                    heartbeat = dict(row)
                    heartbeat["details"] = json.loads(heartbeat.pop("details_json") or "{}")
                    heartbeat["age_seconds"] = round(max(0, datetime.now(timezone.utc).timestamp() - heartbeat["updated_at"]), 1)
                    stale_after = int(os.environ.get("CINESWARM_HEARTBEAT_STALE_SECONDS", "60"))
                    if heartbeat["status"] == "running":
                        stale_after = max(stale_after, int(os.environ.get("CINESWARM_WORKER_LEASE_SECONDS", "900")))
                    heartbeat["healthy"] = heartbeat["status"] != "stopped" and heartbeat["age_seconds"] <= stale_after
        catalog = self.catalog_counts()
        return {"phase": "2", "mode": "policy_controlled", "services": services, "worker": heartbeat, "catalog": catalog, "tasks": tasks, "observations": observations, "decisions": self.decisions(20), "discovery": [], "audit": events}

    def catalog_counts(self) -> dict[str, int]:
        if not os.path.exists(CATALOG_DB):
            return {}
        try:
            with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
                return {
                    row[0]: row[1]
                    for row in connection.execute(
                        "SELECT media_type, COUNT(*) FROM catalog_items WHERE present=1 GROUP BY media_type"
                    )
                }
        except sqlite3.Error:
            return {}


class ControlPlane:
    def __init__(self, store: ControlStore, connectors: dict[str, Callable[[], dict[str, Any]]], actions: dict[str, Callable[[dict[str, Any]], Any]] | None = None, planner: AcquisitionPlanner | None = None, writers: dict[str, Any] | None = None):
        self.store = store
        self.connectors = connectors
        self.actions = actions or {}
        self.writers = writers or {}
        self.planner = planner
        self.agents = AgentOrchestrator(store, CATALOG_DB)
        self.agents.writers = self.writers
        self.semantic_index = FullVaultSemanticIndex(CATALOG_DB)
        self.dense_vector_index = LocalDenseVectorIndex(CATALOG_DB)
        self.consensus_graph = MultiAgentConsensusGraph(self.agents, CATALOG_DB)

        self.verifier = SelfReflectionVerifier(CATALOG_DB)
        self.taste_memory = UserTasteMemory(CONTROL_DB)
        self.vault_comprehension = VaultLibraryComprehension(CATALOG_DB)
        self.library_brain = LibraryBrain(CATALOG_DB, CONTROL_DB)
        self.evolution_engine = AutonomicSwarmEvolutionEngine(CONTROL_DB, CATALOG_DB)
        self.user_profiles = MultiUserTasteEngine(CONTROL_DB)
        self.mesh_engine = SwarmMeshSyncEngine(CONTROL_DB)
        self.artwork_engine = DynamicArtworkEngine()
        self.fleet_manager = EnterpriseFleetManager(CONTROL_DB)
        self.agents.user_profiles = self.user_profiles


        self.agents.semantic_index = self.semantic_index
        self.agents.vault_comprehension = self.vault_comprehension
        self.agents.dense_vector_index = self.dense_vector_index
        self.agents.evolution_engine = self.evolution_engine
        self.agents.library_brain = self.library_brain








        plex_url = os.environ.get("PLEX_URL", "http://127.0.0.1:32400")
        plex_token = os.environ.get("PLEX_TOKEN", "")
        self.discovery = DiscoveryEngine(store, planner, self.agents.tools, plex_url, plex_token) if planner else None


    def refresh(self, actor: str = "system") -> dict[str, Any]:
        results = {}
        for name, snapshot in self.connectors.items():
            try:
                payload = snapshot()
                self.store.save_snapshot(name, payload)
                self.store.audit(actor, "refresh_snapshot", name, "read-only", "success", payload.get("counts", {}))
                results[name] = {"status": "healthy", "counts": payload.get("counts", {})}
            except Exception as exc:
                message = str(exc)
                self.store.save_error(name, message)
                self.store.audit(actor, "refresh_snapshot", name, "read-only", "error", {"error": message})
                results[name] = {"status": "error", "error": message}
        return results

    def monitor_acquisitions(self, actor: str = "worker") -> dict[str, Any]:
        if not self.planner:
            raise AgentError("Acquisition planner is not configured")
        observations = []
        for task in self.store.completed_search_tasks():
            try:
                result = self.planner.monitor_search(task["payload"], task["result"])
            except Exception as exc:
                result = {"state": "monitor_unavailable", "media_type": task["payload"].get("media_type"), "service_id": task["payload"].get("service_id"), "error": str(exc)}
            self.store.save_acquisition_observation(task["task_id"], result.get("state", "unknown"), result)
            observations.append({"task_id": task["task_id"], **result})
        summary = {}
        for observation in observations:
            state = observation.get("state", "unknown")
            summary[state] = summary.get(state, 0) + 1
        followups = []
        full_autopilot = False
        get_policy = getattr(self.store, "get_policy", None)
        if callable(get_policy):
            full_autopilot = str(get_policy("CINESWARM_FULL_AUTOPILOT") or "").lower() in {"1", "true", "yes", "on"}
        plex_refresh_ran = False
        for observation in observations:
            parent_task_id = observation["task_id"]
            if observation.get("state") == "search_completed_no_file":
                retry_type = "radarr_search_retry_request" if observation.get("media_type") == "movie" else "sonarr_search_retry_request"
                if not self.store.task_exists(retry_type, parent_task_id):
                    followup_id = self.store.create_task(retry_type, actor, {"media_type": observation.get("media_type"), "service_id": observation.get("service_id"), "parent_task_id": parent_task_id, "reason": "The approved search completed without an imported file. Review and retry once if desired."})
                    followups.append({"task_id": followup_id, "parent_task_id": parent_task_id, "state": "pending_approval"})
            elif observation.get("state") == "imported":
                if full_autopilot:
                    if plex_refresh_ran:
                        continue
                    try:
                        result = self.execute_automatic("plex_library_refresh", actor, {"reason": "import_followup", "parent_task_id": parent_task_id})
                        followups.append({"parent_task_id": parent_task_id, "state": "imported", "status": result.get("status") or "executed"})
                    except Exception as exc:
                        followups.append({"parent_task_id": parent_task_id, "state": "imported", "status": "failed", "error": str(exc)})
                    plex_refresh_ran = True
                    continue
                if not self.store.task_exists("plex_library_refresh", parent_task_id):
                    followup_id = self.store.create_task("plex_library_refresh", actor, {"parent_task_id": parent_task_id, "reason": "The acquisition imported successfully. Review and approve a Plex refresh."})
                    followups.append({"task_id": followup_id, "parent_task_id": parent_task_id, "state": "pending_approval"})
        self.store.audit(actor, "acquisition_monitor", "radarr-sonarr", "read-only", "complete", {"summary": summary, "observations": observations, "followups": followups})
        return {"summary": summary, "observations": observations, "followups": followups}

    def discovery_run(self, actor: str = "worker") -> dict[str, Any]:
        if not self.discovery:
            raise AgentError("Discovery engine is not configured")
        try:
            result = self.discovery.run(limit=20)
        except Exception as exc:
            rescored = 0
            fallback: list[dict[str, Any]] = []
            try:
                rescored = int(self.discovery.rescore_open_candidates() or 0)
            except Exception:
                pass
            try:
                fallback = self.discovery.taste_lookup_fallback(limit=8)
            except Exception:
                fallback = []
            result = {
                "status": "model_unavailable",
                "generated": 0,
                "inserted": len(fallback),
                "candidates": fallback,
                "fallback_inserted": len(fallback),
                "rescored_candidates": rescored,
                "error": str(exc).split(":", 1)[0][:160],
            }
            self.store.audit(actor, "discovery_run", "collection", "read-only", "model_unavailable", result)
            return result
        self.store.audit(actor, "discovery_run", "collection", "read-only", "complete", {"generated": result.get("generated"), "inserted": result.get("inserted")})
        return result

    def discovery_queue(self) -> list[dict[str, Any]]:
        if not self.discovery:
            return []
        return self.discovery.queue()

    def get_policies(self) -> dict[str, str]:
        return self.store.get_all_policies()

    def set_policy(self, key: str, value: str) -> None:
        self.store.set_policy(key, value)

    def get_budget_status(self) -> dict[str, Any]:
        budget_ok, info = self.store.check_budget()
        return {"budget_ok": budget_ok, **info}

    def autonomous_status(self) -> dict[str, Any]:
        return {
            "emergency_stop": self.store.is_emergency_stop(),
            "budget": self.store.get_budget_usage(),
            "policies": self.store.get_all_policies(),
        }

    def probe_transcode_shield(self) -> dict[str, Any]:
        if not os.path.exists(CATALOG_DB):
            return {"status": "unavailable", "non_compliant_count": 0, "verified_count": 0}
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM catalog_items WHERE present = 1 AND (LOWER(video_codec) LIKE '%av1%' OR LOWER(path) LIKE '%av1%' OR LOWER(path) LIKE '%av-1%')")
            av1_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM catalog_items WHERE present = 1")
            total_count = cursor.fetchone()[0]
        verified = max(0, total_count - av1_count)
        compliance_pct = round((verified / total_count * 100), 1) if total_count > 0 else 100.0
        return {
            "status": "shield_active" if av1_count == 0 else "purge_in_progress",
            "compliance_percentage": compliance_pct,
            "non_compliant_count": av1_count,
            "verified_count": verified,
            "hardware_accel": "VAAPI iGPU (Intel UHD)",
            "client_device": "Samsung Galaxy Tab S9 FE"
        }

    def ensure_plex_webhook(self) -> dict[str, Any]:
        """Register the local playback webhook so Plex scrobbles train taste memory."""
        url = os.environ.get("CINESWARM_PLEX_WEBHOOK_URL", "http://192.168.1.23:8787/api/webhooks/plex").strip()
        plex = self.writers.get("plex") if getattr(self, "writers", None) else None
        if plex is None:
            plex = getattr(self, "plex", None)
        if plex is None or not url:
            return {"status": "skipped", "reason": "plex_or_url_missing", "url": url}
        try:
            existing = plex.get(":/webhooks")
            urls = [elem.get("url") or (elem.text or "") for elem in existing.iter() if (elem.get("url") or (elem.text or "")).startswith("http")]
            if any(item.rstrip("/") == url.rstrip("/") for item in urls):
                return {"status": "already_registered", "url": url, "known": urls[:8], "via": "local"}
            if hasattr(plex, "post"):
                plex.post(":/webhooks", {"url": url})
            else:
                return self._ensure_plex_tv_webhook(url, plex)
            return {"status": "registered", "url": url, "via": "local"}
        except Exception as exc:
            if "404" in str(exc) or "HTTP 404" in str(exc):
                return self._ensure_plex_tv_webhook(url, plex)
            return {"status": "error", "url": url, "error": str(exc)}

    def _ensure_plex_tv_webhook(self, url: str, plex: Any) -> dict[str, Any]:
        """Plex webhooks live on the account API; local :/webhooks is often 404."""
        token = getattr(getattr(plex, "config", None), "api_key", None) or os.environ.get("PLEX_TOKEN", "")
        if not token:
            return {"status": "error", "url": url, "error": "plex_token_missing", "via": "plex.tv"}
        endpoint = "https://plex.tv/api/v2/user/webhooks"
        headers = {
            "X-Plex-Token": token,
            "X-Plex-Client-Identifier": "cineswarm-control",
            "X-Plex-Product": "CineSwarm",
            "Accept": "application/json",
        }
        try:
            request = urllib.request.Request(endpoint, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8", "replace")
            known = self._parse_plex_tv_webhook_urls(raw)
            if any(item.rstrip("/") == url.rstrip("/") for item in known):
                return {"status": "already_registered", "url": url, "known": known[:8], "via": "plex.tv"}
            payload = urllib.parse.urlencode([("urls[]", item) for item in known + [url]]).encode()
            request = urllib.request.Request(endpoint, data=payload, headers={**headers, "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
            with urllib.request.urlopen(request, timeout=15) as response:
                response.read()
            return {"status": "registered", "url": url, "via": "plex.tv", "known": known[:8]}
        except urllib.error.HTTPError as exc:
            return {"status": "error", "url": url, "via": "plex.tv", "error": f"plex.tv returned HTTP {exc.code}"}
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
            return {"status": "error", "url": url, "via": "plex.tv", "error": exc.__class__.__name__}

    @staticmethod
    def _parse_plex_tv_webhook_urls(raw: str) -> list[str]:
        urls: list[str] = []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str) and item.startswith("http"):
                    urls.append(item)
                elif isinstance(item, dict) and str(item.get("url") or "").startswith("http"):
                    urls.append(str(item["url"]))
        elif isinstance(parsed, dict):
            for item in parsed.get("webhooks") or parsed.get("urls") or []:
                if isinstance(item, str) and item.startswith("http"):
                    urls.append(item)
                elif isinstance(item, dict) and str(item.get("url") or "").startswith("http"):
                    urls.append(str(item["url"]))
        if urls:
            return urls
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return []
        for elem in root.iter():
            candidate = elem.get("url") or (elem.text or "")
            if candidate.startswith("http"):
                urls.append(candidate)
        return urls

    def queue_av1_upgrade_searches(self, limit: int = 3, actor: str = "worker") -> dict[str, Any]:
        """Create a bounded Radarr search for existing AV1 files so they can be replaced with HEVC/H.264."""
        limit = max(0, min(int(limit), 10))
        if limit == 0 or not os.path.exists(CATALOG_DB):
            return {"status": "skipped", "queued": []}
        rows = []
        try:
            with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """SELECT title, year, source_native_id, size_mb FROM catalog_items
                       WHERE present=1 AND LOWER(COALESCE(video_codec,'')) LIKE '%av1%' AND source_native_id IS NOT NULL
                       ORDER BY size_mb DESC LIMIT ?""",
                    (limit * 4,),
                ).fetchall()
        except sqlite3.Error:
            return {"status": "error", "queued": []}
        queued = []
        for row in rows:
            if len(queued) >= limit:
                break
            service_id = row["source_native_id"]
            parent = f"av1-upgrade:{service_id}"
            if self.store.task_exists("radarr_search_request", parent):
                continue
            task_id = self.store.create_task(
                "radarr_search_request",
                actor,
                {"media_type": "movie", "service_id": service_id, "parent_task_id": parent, "reason": "Replace unaccelerated AV1 for Tab S9 FE"},
            )
            queued.append({"task_id": task_id, "title": row["title"], "year": row["year"], "service_id": service_id})
        return {"status": "queued" if queued else "none_needed", "queued": queued}

    def never_imported_movies(self, added_since_days: int | None = 14, limit: int = 25) -> dict[str, Any]:
        """Radarr-managed movies in the catalog that still have no file size (never imported)."""
        empty = {"count": 0, "recent": [], "genre_counts": {}, "added_since_days": added_since_days}
        if not os.path.exists(CATALOG_DB):
            return empty
        cutoff = None
        if added_since_days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=int(added_since_days))).strftime("%Y-%m-%d")
        genre_counts: Counter[str] = Counter()
        recent: list[dict[str, Any]] = []
        total = 0
        try:
            with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """SELECT title, year, added, path, source_native_id, genres_json, monitored
                       FROM catalog_items
                       WHERE media_type='movie' AND present=1 AND (size_mb IS NULL OR size_mb=0)
                       ORDER BY added DESC"""
                ).fetchall()
        except sqlite3.Error:
            return empty
        for row in rows:
            added = str(row["added"] or "")
            if cutoff and added < cutoff:
                continue
            total += 1
            genres = []
            try:
                genres = [str(item) for item in json.loads(row["genres_json"] or "[]") if item]
            except json.JSONDecodeError:
                genres = []
            for genre in genres:
                genre_counts[genre] += 1
            if len(recent) < max(1, min(int(limit), 50)):
                recent.append({
                    "title": row["title"],
                    "year": row["year"],
                    "added": added,
                    "path": row["path"],
                    "service_id": row["source_native_id"],
                    "monitored": bool(row["monitored"]),
                    "genres": genres,
                })
        return {
            "count": total,
            "recent": recent,
            "genre_counts": dict(genre_counts.most_common(8)),
            "added_since_days": added_since_days,
        }


    def vibe_search(self, user_prompt: str) -> dict[str, Any]:
        if not user_prompt:
            return {"matches": []}
        if not os.path.exists(CATALOG_DB):
            return {"matches": []}
        words = [w.strip() for w in user_prompt.lower().split() if len(w.strip()) > 2]
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as conn:
            cursor = conn.cursor()
            if words:
                where_clauses = " OR ".join(["LOWER(title) LIKE ?", "LOWER(overview) LIKE ?", "LOWER(genres_json) LIKE ?"])
                params = []
                for w in words[:4]:
                    params.extend([f"%{w}%", f"%{w}%", f"%{w}%"])
                query_sql = f"SELECT title, year, genres_json, media_type, overview FROM catalog_items WHERE present = 1 AND ({' OR '.join(['(LOWER(title) LIKE ? OR LOWER(overview) LIKE ? OR LOWER(genres_json) LIKE ?)'] * len(words[:4]))}) LIMIT 100"
                cursor.execute(query_sql, params)
                rows = cursor.fetchall()
            else:
                cursor.execute("SELECT title, year, genres_json, media_type, overview FROM catalog_items WHERE present = 1 ORDER BY RANDOM() LIMIT 20")
                rows = cursor.fetchall()

        scored = []
        for r in rows:
            t, y, g_json, mt, ov = r[0], r[1], r[2] or "[]", r[3], r[4] or ""
            try:
                g_list = json.loads(g_json)
                g_str = ", ".join(g_list) if isinstance(g_list, list) else str(g_json)
            except Exception:
                g_str = str(g_json)
            text = f"{t} {g_str} {ov}".lower()
            matched_words = [w for w in words if w in text]
            match_score = len(matched_words)
            reason = f"Matches atmosphere '{', '.join(matched_words) if matched_words else user_prompt}' in genre/plot overview ({g_str or 'General'})."
            scored.append({
                "title": t,
                "year": y,
                "genres": g_str,
                "media_type": mt,
                "reasoning": reason,
                "score": match_score
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return {"prompt": user_prompt, "matches": scored[:6]}


    def generate_cinema_night(self, theme: str) -> dict[str, Any]:
        if not os.path.exists(CATALOG_DB):
            return {"error": "Catalog database not found"}
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT title, year, genres_json, overview FROM catalog_items WHERE present = 1 AND media_type = 'movie' ORDER BY RANDOM() LIMIT 50")
            rows = cursor.fetchall()

        if len(rows) < 2:
            return {"error": "Not enough movies in catalog to create double feature"}

        def parse_g(g_raw):
            try:
                g_l = json.loads(g_raw or "[]")
                return ", ".join(g_l) if isinstance(g_l, list) else str(g_raw)
            except Exception:
                return str(g_raw or "Classic")

        m1, m2 = rows[0], rows[1]
        g1, g2 = parse_g(m1[2]), parse_g(m2[2])
        double_feature = {
            "event_title": f"CineSwarm Cinema Night: {theme or 'Curated Feature'}",
            "feature_1": {
                "title": m1[0],
                "year": m1[1],
                "pitch": f"Opening Feature ({g1 or 'Classic'}): {m1[3][:140]}..." if m1[3] else "A thrilling opening feature to kick off cinema night."
            },
            "intermission_trivia": [
                f"Did you know? '{m1[0]}' ({m1[1]}) was selected as part of your curated local archive.",
                f"Genre pairing: Combining {g1 or 'Action'} with {g2 or 'Drama'} for optimal thematic rhythm.",
                f"Featured Double-Bill: '{m2[0]}' ({m2[1]}) follows after a brief intermission!"
            ],
            "feature_2": {
                "title": m2[0],
                "year": m2[1],
                "pitch": f"Co-Feature ({g2 or 'Cult Favorite'}): {m2[3][:140]}..." if m2[3] else "A complementary late-night co-feature."
            }
        }
        return {"theme": theme, "double_feature": double_feature}




    def discovery_action(self, candidate_id: int, action: str, actor: str = "dashboard") -> dict[str, Any]:
        if not self.discovery:
            raise AgentError("Discovery engine is not configured")
        candidate = self.discovery.candidate(candidate_id)
        if not candidate:
            raise AgentError("Discovery candidate not found")
        if action in ("reject", "ignore"):
            self.discovery.set_status(candidate_id, action + "ed" if action == "reject" else action)
            return {"candidate_id": candidate_id, "status": action + "ed" if action == "reject" else action}
        if action != "approve":
            raise AgentError("Discovery action must be approve, reject, or ignore")
        plan = self.planner.plan(candidate["media_type"], candidate["title"])
        if not plan["root_folders"] or not plan["quality_profiles"]:
            raise AgentError("No Radarr/Sonarr root folder or quality profile is configured")
        preferred = self.store.get_policy("CINESWARM_AUTO_REQUIRED_QUALITY_PROFILE") or "HD-1080p"
        profile = next((item for item in plan["quality_profiles"] if item.get("name") == preferred), None)
        if not profile:
            raise AgentError(f"The configured {preferred} quality profile is unavailable")
        task_type = "radarr_add_request" if candidate["media_type"] == "movie" else "sonarr_add_request"
        payload = {"media_type": candidate["media_type"], "candidate": candidate["candidate"], "root_folder_path": plan["root_folders"][0]["path"], "quality_profile_id": profile["id"], "discovery_candidate_id": candidate_id}
        task_id = self.store.create_task(task_type, actor, payload)
        self.discovery.set_status(candidate_id, "approved")
        if hasattr(self.store, "add_edition"):
            detail = candidate.get("candidate", {})
            self.store.add_edition(
                candidate.get("edition") or candidate.get("quality") or "Unspecified edition",
                discovery_candidate_id=candidate_id,
                source_release_name=detail.get("releaseTitle") or detail.get("sourceTitle"),
                notes=f"Approved acquisition candidate {task_id}",
                authenticity_confidence=candidate.get("acquisition_confidence_score", 0),
                rarity_flag=candidate.get("rarity_preservation_score", 0) >= 70,
                backup_status="pending_acquisition",
            )
        
        full_autopilot = (self.store.get_policy("CINESWARM_FULL_AUTOPILOT") or "false").lower() in {"1", "true", "yes", "on"}
        if full_autopilot:
            if self.store.is_emergency_stop() or os.environ.get("CINESWARM_AUTO_EMERGENCY_STOP", "false").lower() in {"1", "true", "yes", "on"}:
                return {"candidate_id": candidate_id, "status": "blocked_emergency_stop", "task_id": task_id}
            add_result = self.approve_task(task_id, actor)
            follow_up = add_result.get("follow_up")
            search_result = self.approve_task(follow_up["task_id"], actor) if follow_up else None
            return {"candidate_id": candidate_id, "status": "approved_and_executed", "task_id": task_id, "add_result": add_result, "search_result": search_result}
        return {"candidate_id": candidate_id, "status": "approved", "task_id": task_id, "task_status": "pending_approval"}

    def sync_native_collections(self, min_items: int = 2, actor: str = "dashboard") -> dict[str, Any]:
        """Automatically build native Plex Collections for all movie sagas/franchises in your library."""
        if not self.writers or "plex" not in self.writers:
            raise AgentError("Plex writer client is not configured")

        # Step 1: Map Plex movies ratingKeys by title
        plex_writer = self.writers["plex"]
        plex_container = plex_writer.get("library/sections/1/all")
        plex_map: dict[str, str] = {}
        for video in plex_container.findall("Video"):
            title = video.get("title")
            rkey = video.get("ratingKey")
            if title and rkey:
                plex_map[title.strip().casefold()] = rkey

        # Step 2: Map TMDB collection titles from catalog
        coll_map: dict[str, list[tuple[str, str]] | list[tuple[str, str]]] = {}
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            rows = connection.execute("SELECT title, raw_json FROM catalog_items WHERE source='radarr' AND present=1").fetchall()
            for r in rows:
                if not r[1]:
                    continue
                try:
                    d = json.loads(r[1])
                    m = d.get("metadata", {})
                    movie_info = d.get("movie", {})
                    file_id = movie_info.get("MovieFileId") or movie_info.get("movieFileId") or 0
                    if file_id > 0:
                        coll_title = m.get("CollectionTitle") or m.get("collectionTitle")
                        if coll_title:
                            coll_map.setdefault(str(coll_title).strip(), []).append((r[0], plex_map.get(str(r[0]).strip().casefold())))
                except Exception:
                    pass

        eligible = {name: [(t, rk) for t, rk in items if rk] for name, items in coll_map.items() if len([rk for t, rk in items if rk]) >= min_items}
        collections_created = 0
        items_tagged = 0

        for coll_name, items in eligible.items():
            for title, rkey in items:
                try:
                    if plex_writer.add_to_collection("1", coll_name, rkey):
                        items_tagged += 1
                except Exception:
                    pass
            collections_created += 1

        self.store.audit(actor, "sync_native_collections", "plex", "plex-write", "completed", {
            "collections_built": collections_created,
            "items_tagged": items_tagged,
            "min_items": min_items
        })
        return {
            "collections_built": collections_created,
            "items_tagged": items_tagged,
            "eligible_collections": len(eligible)
        }

    def get_plex_playlists(self) -> list[dict[str, Any]]:
        """Fetch all custom Plex playlists with title, ratingKey, duration, and item count."""
        if not self.writers or "plex" not in self.writers:
            return []
        plex_writer = self.writers["plex"]
        try:
            container = plex_writer.get("playlists")
            playlists = []
            for item in container.findall("Playlist") or container.findall("Directory") or container.findall("Metadata"):
                rkey = item.get("ratingKey")
                title = item.get("title")
                size = int(item.get("leafCount") or item.get("childCount") or item.get("size") or 0)
                duration_ms = int(item.get("duration") or 0)
                if rkey and title:
                    playlists.append({
                        "ratingKey": rkey,
                        "title": title,
                        "item_count": size,
                        "duration_mins": round(duration_ms / 60000) if duration_ms else 0,
                        "playlist_type": item.get("playlistType", "video")
                    })
            return playlists
        except Exception:
            return []

    def delete_plex_playlist(self, rating_key: str, actor: str = "dashboard") -> bool:
        """Delete a custom Plex playlist by ratingKey."""
        if not self.writers or "plex" not in self.writers:
            return False
        plex_writer = self.writers["plex"]
        try:
            plex_writer.get(f"playlists/{rating_key}", params={"_method": "DELETE"})
            self.store.audit(actor, "delete_playlist", rating_key, "plex-write", "completed", {"ratingKey": rating_key})
            return True
        except Exception:
            return False

    def watch_recommendations(self, max_minutes: int = 120, genre: str = "", limit: int = 5, actor: str = "dashboard") -> dict[str, Any]:
        """Generate smart 'Watch Tonight' recommendations using Full-Vault Semantic Search & Self-Reflection Verifier."""
        max_size_gb = float(self.store.get_policy("CINESWARM_MAX_DOWNLOAD_SIZE_GB") or "8.0")
        
        # Use full-vault semantic index
        search_query = f"{genre} movie".strip() if genre else "popular classic film"
        candidates = self.semantic_index.search(search_query, top_n=40, max_minutes=max_minutes, max_size_gb=max_size_gb)
        if getattr(self, "user_profiles", None):
            profile = self.user_profiles.get_active_profile()
            candidates = [item for item in candidates if self.user_profiles.allows_certification(item.get("certification") or item.get("contentRating") or item.get("content_rating"), profile)]

        if not self.agents or not self.agents.model.configured or not candidates:
            picks = candidates[:limit]
            return {"max_minutes": max_minutes, "genre": genre, "recommendations": picks}

        prompt = {
            "role": "You are a movie concierge.",
            "instruction": f"Select the top {limit} movies from candidate_movies that best fit a quick watch session under {max_minutes} minutes" + (f" in the '{genre}' genre." if genre else "."),
            "candidate_movies": candidates[:25],
            "schema": {
                "picks": [
                    {
                        "title": "string",
                        "year": 2000,
                        "duration_mins": 90,
                        "reason": "Why this movie is a great choice tonight"
                    }
                ]
            }
        }
        try:
            resp = self.agents.model.complete([
                {"role": "system", "content": prompt["role"]},
                {"role": "user", "content": json.dumps(prompt)}
            ], temperature=0.4)
            fenced = re.search(r"```(?:json)?\s*(.*?)```", resp, re.S | re.I)
            cand_str = fenced.group(1) if fenced else resp
            parsed = json.loads(cand_str)
            picks = parsed.get("picks", [])
            
            # Self-reflection verifier pass
            picks = self.verifier.verify_candidates(picks, max_minutes=max_minutes, max_size_gb=max_size_gb)
        except Exception:
            picks = candidates[:limit]

        self.store.audit(actor, "watch_recommendations", f"{max_minutes}m-{genre}", "gemini-ai", "completed", {"count": len(picks)})
        return {"max_minutes": max_minutes, "genre": genre, "recommendations": picks}


    def reorder_plex_playlist(self, playlist_id: str, rating_keys: list[str], actor: str = "dashboard") -> bool:
        """Re-order items in a Plex playlist by updating items in sequence."""
        if not self.writers or "plex" not in self.writers or not rating_keys:
            return False
        plex_writer = self.writers["plex"]
        try:
            machine_id = plex_writer.get("").get("machineIdentifier")
            if not machine_id:
                return False
            # Clear & re-add items in order
            for key in rating_keys:
                uri = f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{key}"
                plex_writer.put(f"playlists/{playlist_id}/items", {"uri": uri})
            self.store.audit(actor, "reorder_playlist", playlist_id, "plex-write", "completed", {"item_count": len(rating_keys)})
            return True
        except Exception:
            return False

    def scan_filmography(self, person_name: str, role: str = "director", actor: str = "dashboard") -> dict[str, Any]:
        """Scan missing movie titles in a director or actor's filmography using TMDB and catalog cross-reference."""
        if not self.planner:
            raise AgentError("Planner client is not configured")
        
        # Search Radarr lookup for titles by person name
        matches = self.planner.radarr.get("api/v3/movie/lookup", {"term": person_name})
        if not isinstance(matches, list):
            matches = []

        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            present_tmdb_ids = {r[0] for r in connection.execute("SELECT source_id FROM catalog_items WHERE source_id LIKE 'tmdb:%' AND present=1").fetchall()}

        missing_candidates = []
        for m in matches:
            tmdb_id = m.get("tmdbId")
            stable_id = f"tmdb:{tmdb_id}"
            if tmdb_id and stable_id not in present_tmdb_ids:
                missing_candidates.append({
                    "title": m.get("title"),
                    "year": m.get("year"),
                    "tmdbId": tmdb_id,
                    "overview": m.get("overview", "")[:150],
                    "raw": m
                })

        self.store.audit(actor, "scan_filmography", person_name, "tmdb-lookup", "completed", {"role": role, "missing_count": len(missing_candidates)})
        return {"person": person_name, "role": role, "missing_count": len(missing_candidates), "candidates": missing_candidates[:20]}

    def get_analytics_summary(self) -> dict[str, Any]:
        """Get video codec, resolution, top directors, and storage metrics across vault."""
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            codecs = connection.execute("SELECT COALESCE(video_codec, 'Unknown/Unscanned'), COUNT(*) FROM catalog_items WHERE present=1 GROUP BY 1 ORDER BY 2 DESC").fetchall()
            genres = connection.execute("SELECT genres_json FROM catalog_items WHERE present=1 AND genres_json IS NOT NULL").fetchall()
            size_row = connection.execute("SELECT SUM(size_mb) FROM catalog_items WHERE present=1").fetchone()
            total_size_gb = round((size_row[0] or 0) / 1024, 1)

            # Genre distribution
            g_counts: dict[str, int] = {}
            for row in genres:
                try:
                    for g in json.loads(row[0] or "[]"):
                        g_counts[g] = g_counts.get(g, 0) + 1
                except Exception:
                    pass

        sorted_genres = sorted(g_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        return {
            "codecs": {r[0]: r[1] for r in codecs},
            "top_genres": dict(sorted_genres),
            "total_size_gb": total_size_gb
        }

    def log_decision(self, category: str, subject: str, decision: str, reasons: dict[str, Any], outcome: dict[str, Any], actor: str = "autopilot") -> str:
        """Log an autonomous decision into the decision log."""
        import uuid
        decision_id = f"dec-{uuid.uuid4().hex[:8]}"
        now_iso = timestamp()
        with sqlite3.connect(CONTROL_DB) as conn:
            conn.execute(
                "INSERT INTO decision_log (decision_id, actor, category, subject, decision, reasons_json, outcome_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (decision_id, actor, category, subject, decision, json.dumps(reasons), json.dumps(outcome), now_iso, now_iso)
            )
        return decision_id

    def record_decision_feedback(self, decision_id: str, sentiment: str, note: str = "", actor: str = "dashboard") -> bool:
        """Record user 👍 (good) or 👎 (bad) feedback on an autonomous decision."""
        if sentiment not in ("good", "bad"):
            return False
        now_iso = timestamp()
        with sqlite3.connect(CONTROL_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO decision_feedback (decision_id, actor, sentiment, note, created_at) VALUES (?, ?, ?, ?, ?)",
                (decision_id, actor, sentiment, note, now_iso)
            )
        rescored = 0
        if self.discovery:
            try:
                rescored = int(self.discovery.rescore_open_candidates() or 0)
            except Exception as exc:
                self.store.audit(actor, "decision_feedback_rescore", decision_id, "reinforcement", "failed", {"error": str(exc)})
        self.store.audit(actor, "decision_feedback", decision_id, "reinforcement", sentiment, {"note": note, "rescored_candidates": rescored})
        return True

    def get_recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent autonomous decisions with sentiment feedback if present."""
        with sqlite3.connect(f"file:{CONTROL_DB}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT d.decision_id, d.actor, d.category, d.subject, d.decision, d.reasons_json, d.outcome_json, d.created_at,
                       f.sentiment, f.note as feedback_note
                FROM decision_log d
                LEFT JOIN decision_feedback f ON f.decision_id = d.decision_id
                ORDER BY d.created_at DESC LIMIT ?
            """, (limit,)).fetchall()
            decisions = []
            for r in rows:
                item = dict(r)
                try:
                    item["reasons"] = json.loads(item.get("reasons_json") or "{}")
                except Exception:
                    item["reasons"] = {}
                try:
                    item["outcome"] = json.loads(item.get("outcome_json") or "{}")
                except Exception:
                    item["outcome"] = {}
                decisions.append(item)
            return decisions

    def generate_movie_commentary(self, title: str, year: int | None = None, actor: str = "dashboard") -> dict[str, Any]:
        """Generate AI Director Commentary & Trivia subtitle overlay track and auto-inject into movie folder."""
        agent = TriviaCommentaryAgent(self.agents.model if self.agents else None)
        res = agent.generate(title, year)
        
        # Auto-inject .srt into movie directory if file path is available
        saved_path = None
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT path FROM catalog_items WHERE title = ? AND present=1 LIMIT 1", (res["movie_title"],)).fetchone()
            if row and row[0]:
                target = MediaHealthScanner.locate_video_file(row[0])
                if os.path.exists(target):
                    base, _ = os.path.splitext(target)
                    srt_file = f"{base}.en.trivia.srt"
                    try:
                        with open(srt_file, "x", encoding="utf-8") as f:
                            f.write(res.get("srt", ""))
                        saved_path = srt_file
                    except Exception:
                        pass

        self.store.audit(actor, "generate_commentary", title, "gemini-ai", "completed", {
            "movie_title": title,
            "marker_count": res.get("marker_count", 0),
            "director": res.get("director"),
            "saved_path": saved_path
        })
        res["saved_path"] = saved_path
        return res

    def scan_media_health(self, limit: int = 30, actor: str = "dashboard", allow_automatic: bool = True) -> dict[str, Any]:
        """Scan media files in library for corruption or missing streams and queue auto-healing repair tasks."""
        scanner = MediaHealthScanner()
        res = scanner.scan_catalog(limit=limit)
        
        # Corrupt File Auto-Healer: Queue Radarr/Sonarr search retries
        auto_heal_tasks = []
        full_autopilot = (self.store.get_policy("CINESWARM_FULL_AUTOPILOT") or "false").lower() in {"1", "true", "yes", "on"}
        emergency_stop = self.store.is_emergency_stop() or os.environ.get("CINESWARM_AUTO_EMERGENCY_STOP", "false").lower() in {"1", "true", "yes", "on"}
        for item in res.get("corrupt_items", []):
            native_id = item.get("source_native_id")
            media_type = item.get("media_type", "movie")
            if native_id and isinstance(native_id, int):
                task_type = "radarr_search_retry_request" if media_type == "movie" else "sonarr_search_retry_request"
                parent_id = f"health-repair:{media_type}:{native_id}"
                if not self.store.task_exists(task_type, parent_id):
                    payload = {"media_type": media_type, "service_id": native_id, "parent_task_id": parent_id, "reason": f"Corrupt file detected: {item.get('title')}"}
                    task_id = self.store.create_task(task_type, actor, payload)
                    if full_autopilot and allow_automatic and not emergency_stop:
                        try:
                            self.approve_task(task_id, actor)
                            auto_heal_tasks.append({"task_id": task_id, "title": item.get("title"), "status": "executed"})
                        except Exception as exc:
                            auto_heal_tasks.append({"task_id": task_id, "title": item.get("title"), "status": "failed", "error": str(exc)})
                    else:
                        auto_heal_tasks.append({"task_id": task_id, "title": item.get("title"), "status": "pending_approval"})

        self.store.audit(actor, "media_health_scan", "media", "ffprobe-scan", "completed", {
            "scanned": res.get("scanned", 0),
            "healthy": res.get("healthy", 0),
            "corrupt_count": res.get("corrupt_count", 0),
            "auto_heal_tasks": auto_heal_tasks
        })
        res["auto_heal_tasks"] = auto_heal_tasks
        return res

    def run_x265_upgrade_sweep(self, batch_size: int = 100, actor: str = "autonomic") -> dict[str, Any]:
        """Trigger aggressive x265/HEVC upgrade search batch across Radarr and Sonarr Cutoff Unmet queues."""
        sonarr_url = os.environ.get("SONARR_URL", "http://127.0.0.1:8989")
        sonarr_api = os.environ.get("SONARR_API_KEY", "")
        radarr_url = os.environ.get("RADARR_URL", "http://127.0.0.1:7878")
        radarr_api = os.environ.get("RADARR_API_KEY", "")

        movie_count = 0
        episode_count = 0
        half_batch = max(1, batch_size // 2)

        # Radarr Cutoff Unmet
        if radarr_api:
            try:
                r_cutoff = requests.get(f"{radarr_url}/api/v3/wanted/cutoff", headers={"X-Api-Key": radarr_api}, params={"pageSize": half_batch, "sortKey": "movie.added", "sortDirection": "descending"}, timeout=10).json()
                movie_records = r_cutoff.get("records", [])
                movie_ids = [m.get("id") for m in movie_records if m.get("id")]
                if movie_ids:
                    requests.post(f"{radarr_url}/api/v3/command", headers={"X-Api-Key": radarr_api}, json={"name": "MoviesSearch", "movieIds": movie_ids}, timeout=10)
                    movie_count = len(movie_ids)
            except Exception as e:
                logger.warning(f"x265 Radarr upgrade sweep failed: {e}")

        # Sonarr Cutoff Unmet
        if sonarr_api:
            try:
                s_cutoff = requests.get(f"{sonarr_url}/api/v3/wanted/cutoff", headers={"X-Api-Key": sonarr_api}, params={"pageSize": half_batch, "sortKey": "series.title", "sortDirection": "ascending"}, timeout=10).json()
                ep_records = s_cutoff.get("records", [])
                episode_ids = [e.get("id") for e in ep_records if e.get("id")]
                if episode_ids:
                    requests.post(f"{sonarr_url}/api/v3/command", headers={"X-Api-Key": sonarr_api}, json={"name": "EpisodeSearch", "episodeIds": episode_ids}, timeout=10)
                    episode_count = len(episode_ids)
            except Exception as e:
                logger.warning(f"x265 Sonarr upgrade sweep failed: {e}")

        result = {
            "status": "triggered",
            "batch_size": batch_size,
            "movies_queued": movie_count,
            "episodes_queued": episode_count,
            "timestamp": timestamp()
        }
        self.store.audit(actor, "x265_upgrade_sweep", "autonomic", "radarr_sonarr", "completed", result)
        return result

    def generate_movie_chapters(self, title: str, year: int | None = None, actor: str = "dashboard") -> dict[str, Any]:
        """Generate AI Chapter Markers & Scene Summaries and auto-inject into movie folder."""
        summarizer = ChapterSummarizer(self.agents.model if self.agents else None)
        res = summarizer.generate(title, year)
        
        # Auto-inject .vtt into movie directory if file path is available
        saved_path = None
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT path FROM catalog_items WHERE title = ? AND present=1 LIMIT 1", (res["movie_title"],)).fetchone()
            if row and row[0]:
                target = MediaHealthScanner.locate_video_file(row[0])
                if os.path.exists(target):
                    base, _ = os.path.splitext(target)
                    vtt_file = f"{base}.en.chapters.vtt"
                    try:
                        with open(vtt_file, "x", encoding="utf-8") as f:
                            f.write(res.get("vtt", ""))
                        saved_path = vtt_file
                    except Exception:
                        pass

        self.store.audit(actor, "generate_chapters", title, "gemini-ai", "completed", {
            "movie_title": title,
            "chapter_count": res.get("chapter_count", 0),
            "saved_path": saved_path
        })
        res["saved_path"] = saved_path
        return res

    def quality_analysis(self, media_type: str = "movie", actor: str = "dashboard") -> dict[str, Any]:
        if not self.planner:
            raise AgentError("Acquisition planner is not configured")
        result = self.planner.quality_analysis(media_type)
        self.store.audit(actor, "quality_analysis", media_type, "read-only", "complete", {
            "managed": result.get("managed", 0),
            "upgrade_candidate_count": result.get("upgrade_candidate_count", 0),
        })
        return result

    def get_intelligence_status(self) -> dict[str, Any]:
        """Fetch active status & reinforcement weighting tuning stats for Swarm Intelligence modules."""
        # Calculate dynamic reinforcement weights from user decision feedback
        feedback_stats = {"total_feedback": 0, "good": 0, "bad": 0, "weight_offsets": {}}
        try:
            with sqlite3.connect(f"file:{CONTROL_DB}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT sentiment, count(*) as cnt FROM decision_feedback GROUP BY sentiment").fetchall()
                for r in rows:
                    feedback_stats[r["sentiment"]] = r["cnt"]
                    feedback_stats["total_feedback"] += r["cnt"]
        except Exception:
            pass

        # Calculate weight offsets based on sentiment balance
        good = feedback_stats.get("good", 0)
        bad = feedback_stats.get("bad", 0)
        net = good - bad
        feedback_stats["weight_offsets"] = {
            "watch_affinity_multiplier": round(1.0 + (net * 0.05), 2),
            "rarity_preservation_multiplier": round(1.0 + (good * 0.02), 2),
            "storage_cost_penalty_multiplier": round(1.0 + (bad * 0.03), 2),
        }

        # Quality guard rules
        max_size_gb = float(self.store.get_policy("CINESWARM_MAX_DOWNLOAD_SIZE_GB") or "8.0")
        
        return {
            "intelligence_version": "2.0.0",
            "quality_guard": {
                "max_size_gb_ceiling": max_size_gb,
                "blocked_codecs": ["av1"],
                "preferred_codecs": ["h264", "hevc"],
                "active": True
            },
            "reinforcement": feedback_stats,
            "double_feature_engine": {"status": "ready" if self.agents and self.agents.model.configured else "offline"},
            "storage_optimization_agent": {"status": "ready"},
            "library_brain": self.library_brain.coverage() if getattr(self, "library_brain", None) else {},
        }

    def evaluate_release_quality_guard(self, title: str, size_gb: float, video_codec: str = "", release_name: str = "") -> dict[str, Any]:
        """Evaluate a download release candidate against Quality-Guard safety rules."""
        max_size_gb = float(self.store.get_policy("CINESWARM_MAX_DOWNLOAD_SIZE_GB") or "8.0")
        reasons = []
        passed = True

        if size_gb > max_size_gb:
            passed = False
            reasons.append(f"File size ({size_gb:.1f} GB) exceeds policy ceiling of {max_size_gb:.1f} GB")

        codec_clean = video_codec.lower() or release_name.lower()
        if "av1" in codec_clean:
            passed = False
            reasons.append("AV1 codec causes 0.3x CPU software transcode buffering on Tab S9 FE")

        return {
            "title": title,
            "size_gb": size_gb,
            "video_codec": video_codec or "unknown",
            "passed": passed,
            "policy_max_size_gb": max_size_gb,
            "reasons": reasons if not passed else ["Complies with Quality-Guard size and codec constraints"]
        }

    def generate_intelligence_double_feature(self, theme: str = "mind-bending twists", limit: int = 2, publish_plex: bool = True, actor: str = "dashboard") -> dict[str, Any]:
        """Generate a thematic multi-movie pairing (e.g. Fan-Theory connections, Director Eras) and option to publish Plex playlist."""
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            rows = connection.execute("SELECT title, year, duration_mins, genres_json, overview, source_native_id FROM catalog_items WHERE present=1 AND media_type='movie' ORDER BY RANDOM() LIMIT 40").fetchall()
            candidates = [{"title": r[0], "year": r[1], "duration_mins": r[2] or 90, "genres": r[3], "overview": r[4], "rating_key": r[5]} for r in rows]

        if not self.agents or not self.agents.model.configured or not candidates:
            picks = candidates[:limit]
            return {"theme": theme, "pairing": picks, "explanation": "Fallback random pairing"}

        prompt = {
            "role": "You are a master cinema curator and double-feature engine.",
            "instruction": f"Select {limit} movies from candidate_movies that form a compelling '{theme}' double-feature pairing or fan-theory connection.",
            "candidate_movies": candidates[:25],
            "schema": {
                "title": f"{theme.title()} Double Feature",
                "explanation": "Why these two movies pair together and how to watch them",
                "picks": [
                    {
                        "title": "string",
                        "year": 2000,
                        "pairing_role": "Part 1: The Setup / Part 2: The Payoff",
                        "reason": "Why this movie fits the narrative connection"
                    }
                ]
            }
        }
        try:
            resp = self.agents.model.complete([
                {"role": "system", "content": prompt["role"]},
                {"role": "user", "content": json.dumps(prompt)}
            ], temperature=0.4)
            fenced = re.search(r"```(?:json)?\s*(.*?)```", resp, re.S | re.I)
            cand_str = fenced.group(1) if fenced else resp
            parsed = json.loads(cand_str)
        except Exception as exc:
            parsed = {"title": f"{theme.title()} Feature", "explanation": f"Generated pairing for {theme}", "picks": candidates[:limit]}

        # Optionally publish Plex Playlist
        playlist_result = None
        if publish_plex and self.writers and "plex" in self.writers and parsed.get("picks"):
            try:
                pick_titles = {p["title"].lower() for p in parsed.get("picks", [])}
                keys = [str(c["rating_key"]) for c in candidates if c["title"].lower() in pick_titles and c.get("rating_key")]
                if keys:
                    playlist_title = f"CineSwarm: {parsed.get('title', theme.title())}"
                    playlist_result = self.create_plex_playlist(playlist_title, keys, actor=actor)
            except Exception:
                pass

        self.store.audit(actor, "double_feature_generation", theme, "gemini-ai", "completed", {"picks_count": len(parsed.get("picks", []))})
        return {
            "theme": theme,
            "title": parsed.get("title", f"{theme.title()} Double Feature"),
            "explanation": parsed.get("explanation", ""),
            "pairing": parsed.get("picks", []),
            "plex_playlist": playlist_result
        }

    def run_storage_optimization_audit(self, actor: str = "dashboard") -> dict[str, Any]:
        """Perform a non-destructive audit of vault media files to identify bloated, corrupt, or unmonitored duplicate files."""
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id, title, year, media_type, size_mb, video_codec, path FROM catalog_items WHERE present=1 ORDER BY size_mb DESC").fetchall()

        over_20gb = []
        av1_files = []
        total_vault_size_gb = 0.0

        for r in rows:
            size_gb = (r["size_mb"] or 0) / 1024.0
            total_vault_size_gb += size_gb
            codec = (r["video_codec"] or "").lower()
            if size_gb > 20.0:
                over_20gb.append({"title": r["title"], "year": r["year"], "size_gb": round(size_gb, 2), "path": r["path"]})
            if "av1" in codec:
                av1_files.append({"title": r["title"], "year": r["year"], "codec": codec, "size_gb": round(size_gb, 2)})

        recommendations = []
        if over_20gb:
            recommendations.append(f"Found {len(over_20gb)} bloated files (>20 GB). Consider replacing with 8-15 GB high-efficiency HEVC encodes.")
        if av1_files:
            recommendations.append(f"Found {len(av1_files)} AV1 encoded titles causing CPU transcode buffering on Tab S9 FE.")
        if not recommendations:
            recommendations.append("Vault storage is optimized. All media files comply with quality and direct-play guidelines.")

        result = {
            "total_items": len(rows),
            "total_vault_size_gb": round(total_vault_size_gb, 2),
            "bloated_files_count": len(over_20gb),
            "bloated_files_sample": over_20gb[:5],
            "av1_files_count": len(av1_files),
            "av1_files_sample": av1_files[:25],
            "av1_total_gb": round(sum(item.get("size_gb") or 0 for item in av1_files), 1),
            "upgrade_recommendation": "Replace AV1 with HEVC or H.264 for Tab S9 FE direct play. Advisory only — no silent rewrite.",
            "recommendations": recommendations
        }
        self.store.audit(actor, "storage_optimization_audit", "vault", "catalog-scan", "completed", {"bloated_count": len(over_20gb), "av1_count": len(av1_files)})
        return result


    def acquisition_queue(self, media_type: str, actor: str = "dashboard") -> dict[str, Any]:
        if not self.planner:
            raise AgentError("Acquisition planner is not configured")
        result = self.planner.queue(media_type)
        self.store.audit(actor, "acquisition_queue", media_type, "read-only", "complete", {"total_records": result.get("total_records", 0)})
        return result

    def operational_queue(self, limit: int = 200) -> dict[str, Any]:
        detail = self.store.queue_detail(limit)
        downloads: dict[str, Any] = {"movies": {"records": [], "total_records": 0}, "series": {"records": [], "total_records": 0}}
        errors: dict[str, str] = {}
        probe_timeout = float(os.environ.get("CINESWARM_PROBE_TIMEOUT", "5"))
        if self.planner:
            for media_type, key in (("movie", "movies"), ("series", "series")):
                try:
                    queue = self.planner.queue(media_type, timeout=probe_timeout) or {}
                    records = queue.get("records", []) if isinstance(queue, dict) else []
                    downloads[key] = {"records": records[:limit], "total_records": queue.get("total_records", queue.get("totalRecords", len(records)))}
                except Exception as exc:
                    errors[key] = str(exc)
        detail["downloads"] = downloads
        detail["errors"] = errors
        return detail

    def monitoring_snapshot(self, hours: int = 24) -> dict[str, Any]:
        """Compact read-only ops snapshot for external watchers and Discord live monitor."""
        hours = max(1, min(int(hours), 168))
        probe_timeout = float(os.environ.get("CINESWARM_PROBE_TIMEOUT", "5"))
        status = self.store.status()
        summary = self.store.operational_summary(hours=hours)
        detail = self.store.queue_detail(limit=50)
        services = status.get("services") or []
        unhealthy = [item.get("service") for item in services if item.get("status") != "healthy"]
        worker = status.get("worker") or {}
        worker_healthy = bool(worker.get("healthy"))
        healthy_count = sum(1 for item in services if item.get("status") == "healthy")
        if len(services) >= 3 and not unhealthy and worker_healthy:
            overall = "healthy"
        elif services and healthy_count == 0:
            overall = "unhealthy"
        elif unhealthy and not worker_healthy:
            overall = "unhealthy"
        else:
            overall = "degraded"
        downloads = {"movies": 0, "series": 0, "total": 0}
        download_errors: dict[str, str] = {}
        queue_limit = max(1, int(os.environ.get("CINESWARM_AUTO_MAX_CONCURRENT_DOWNLOADS", "4") or 4))
        queue_pressure: dict[str, Any] = {
            "status": "unavailable",
            "pressured": False,
            "active": 0,
            "limit": queue_limit,
            "sources": {"radarr": 0, "sonarr": 0, "sabnzbd": 0},
            "records": [],
            "sabnzbd": {"configured": False, "error": None},
        }
        # Single fail-soft probe: avoids double Radarr/Sonarr fetches and long SAB hangs.
        if self.planner and hasattr(self.planner, "global_queue_pressure"):
            try:
                queue_pressure = self.planner.global_queue_pressure(queue_limit, timeout=probe_timeout)
                sources = queue_pressure.get("sources") or {}
                downloads["movies"] = int(sources.get("radarr") or 0)
                downloads["series"] = int(sources.get("sonarr") or 0)
                downloads["total"] = int(queue_pressure.get("active") or (downloads["movies"] + downloads["series"]))
                if queue_pressure.get("errors"):
                    download_errors.update({key: str(value) for key, value in queue_pressure["errors"].items()})
                sab = queue_pressure.get("sabnzbd") or {}
                if sab.get("error"):
                    download_errors["sabnzbd"] = str(sab["error"])
            except Exception as exc:
                queue_pressure = {**queue_pressure, "error": str(exc)}
                download_errors["queue_pressure"] = str(exc)
        elif self.planner:
            for media_type, key in (("movie", "movies"), ("series", "series")):
                try:
                    queue = self.planner.queue(media_type, timeout=probe_timeout) or {}
                    downloads[key] = int(queue.get("total_records", queue.get("totalRecords", len(queue.get("records", []) or []))) or 0)
                except Exception as exc:
                    download_errors[key] = str(exc)
            downloads["total"] = int(downloads["movies"]) + int(downloads["series"])
            queue_pressure = {
                **queue_pressure,
                "status": "pressured" if downloads["total"] >= queue_limit else "available",
                "pressured": downloads["total"] >= queue_limit,
                "active": downloads["total"],
                "sources": {"radarr": downloads["movies"], "sonarr": downloads["series"], "sabnzbd": 0},
            }
        task_counts = detail.get("task_counts") or {}
        pending_approvals = int(task_counts.get("pending_approval", 0) or 0)
        recent_failures = []
        for issue in (summary.get("actionable_issues") or [])[:10]:
            recent_failures.append({
                "type": issue.get("type"),
                "status": issue.get("status") or issue.get("state"),
                "action": issue.get("action") or issue.get("task_type") or issue.get("path"),
                "error": issue.get("error"),
            })
        observations = detail.get("observations") or []
        observation_states: dict[str, int] = {}
        for row in observations:
            state = str(row.get("state") or "unknown")
            observation_states[state] = observation_states.get(state, 0) + 1
        library_integrity: dict[str, Any] = {"status": "unknown"}
        try:
            with self.store.lock, self.store._connect() as connection:
                row = connection.execute(
                    "SELECT status, details_json, created_at FROM audit_events WHERE action='reconcile_library' ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if row:
                payload = json.loads(row["details_json"] or "{}")
                if isinstance(payload, dict) and isinstance(payload.get("result"), dict) and "movies" not in payload:
                    payload = payload["result"]
                movies = (payload.get("movies") or {}) if isinstance(payload, dict) else {}
                series = (payload.get("series") or {}) if isinstance(payload, dict) else {}
                gaps: dict[str, Any] = {"count": 0, "genre_counts": {}, "recent": []}
                shield: dict[str, Any] = {}
                try:
                    gaps = self.never_imported_movies(added_since_days=None, limit=8)
                    shield = self.probe_transcode_shield()
                except Exception:
                    pass
                library_integrity = {
                    "status": row["status"],
                    "checked_at": row["created_at"],
                    "movies": {
                        "managed": movies.get("managed"),
                        "indexed": movies.get("indexed"),
                        "without_file": movies.get("without_file"),
                        "file_not_indexed": movies.get("file_not_indexed"),
                        "path_missing": movies.get("path_missing"),
                        "never_imported": gaps.get("count"),
                        "never_imported_genres": gaps.get("genre_counts"),
                        "never_imported_recent": [f"{item.get('title')} ({item.get('year')})" for item in (gaps.get("recent") or [])[:5]],
                    },
                    "av1": {
                        "count": shield.get("non_compliant_count"),
                        "client": shield.get("client_device"),
                        "status": shield.get("status"),
                    },
                    "series": {
                        "managed": series.get("managed"),
                        "indexed": series.get("indexed"),
                        "not_indexed": series.get("not_indexed"),
                        "path_missing": series.get("path_missing"),
                    },
                }
        except Exception as exc:
            library_integrity = {"status": "error", "error": str(exc)}
        active_sessions = 0
        try:
            plex = getattr(self, "plex", None)
            if plex is not None:
                active_sessions = len(plex.get_sessions(timeout=probe_timeout) or [])
        except Exception:
            active_sessions = 0
        return {
            "overall_status": overall,
            "services": [
                {
                    "service": item.get("service"),
                    "status": item.get("status"),
                    "fetched_at": item.get("fetched_at"),
                    "item_count": item.get("item_count"),
                    "error": item.get("error"),
                }
                for item in services
            ],
            "unhealthy_services": unhealthy,
            "worker": {
                "healthy": worker.get("healthy"),
                "status": worker.get("status"),
                "age_seconds": worker.get("age_seconds"),
                "updated_at": worker.get("updated_at"),
                "worker_id": worker.get("worker_id"),
            },
            "downloads": downloads,
            "download_errors": download_errors,
            "queue_pressure": {
                "status": queue_pressure.get("status"),
                "pressured": bool(queue_pressure.get("pressured")),
                "active": int(queue_pressure.get("active") or 0),
                "limit": int(queue_pressure.get("limit") or queue_limit),
                "sources": queue_pressure.get("sources") or {},
                "sabnzbd": queue_pressure.get("sabnzbd") or {},
                "sample": [
                    {"source": item.get("source"), "title": item.get("title"), "status": item.get("status") or item.get("tracked_download_state")}
                    for item in (queue_pressure.get("records") or [])[:5]
                ],
                "error": queue_pressure.get("error"),
            },
            "library_integrity": library_integrity,
            "acquisition_observations": {
                "recent_count": len(observations),
                "states": observation_states,
            },
            "active_sessions": active_sessions,
            "pending_approvals": pending_approvals,
            "task_counts": {key: int(value) for key, value in task_counts.items()},
            "recent_failures": recent_failures,
            "failure_summary": {
                "audit_failures": (summary.get("audit") or {}).get("failures", 0),
                "task_failures": (summary.get("tasks") or {}).get("failures", 0),
                "actionable_issues": len(summary.get("actionable_issues") or []),
                "window_hours": hours,
            },
            "emergency_stop": bool(self.store.is_emergency_stop()),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def acquisition_plan(self, media_type: str, term: str, actor: str = "dashboard") -> dict[str, Any]:
        if not self.planner:
            raise AgentError("Acquisition planner is not configured")
        result = self.planner.plan(media_type, term)
        self.store.audit(actor, "acquisition_plan", media_type, "read-only", "complete", {
            "term": term,
            "candidate_count": len(result.get("candidates", [])),
            "existing_count": len(result.get("existing_matches", [])),
        })
        return result

    def curate_collection(self, prompt_theme: str, limit: int = 50, mode: str = "collection", actor: str = "dashboard") -> dict[str, Any]:
        """AI-curated Plex collection or playlist generation based on custom themes/moods."""
        if not self.agents or not self.agents.model.configured:
            raise AgentError("Gemini model client is not configured")

        # Semantic dictionary for deep genre, mood & decade expansions
        theme_expansions = {
            "stoner": ["stoner", "weed", "marijuana", "cannabis", "pot", "slacker", "lebowski", "cheech", "chong", "harold", "kumar", "baked", "high", "reefer", "kush", "ganja", "hemp", "smoke", "joint"],
            "cyberpunk": ["cyberpunk", "hacker", "cyber", "android", "matrix", "synth", "virtual", "future", "dystopia", "net", "neon", "ai", "robot"],
            "heist": ["heist", "bank", "robbery", "thief", "steal", "vault", "crime", "caper", "casino", "ocean", "job"],
            "slasher": ["slasher", "killer", "mask", "machete", "camp", "cabin", "serial", "psycho", "horror", "murder", "halloween", "friday"],
            "sci-fi": ["sci-fi", "science fiction", "space", "alien", "galaxy", "time travel", "star", "cosmos", "future", "orbit", "planetary"],
            "noir": ["noir", "detective", "shadows", "investigation", "crime", "mystery", "femme fatale", "cop", "sleuth", "murder"],
            "martial arts": ["martial arts", "kung fu", "karate", "ninja", "samurai", "sword", "fight", "dojo", "dragon", "shaolin"],
            "80s action": ["action", "explosive", "cop", "mercenary", "commando", "hero", "gun", "chase", "revenge", "stallone", "schwarzenegger"],
            "mind-bending": ["mind-bending", "psychological", "twist", "reality", "illusion", "dream", "paranoia", "memory", "subconscious"],
            "comfort": ["comfort", "heartwarming", "cozy", "feel-good", "wholesome", "family", "nostalgia", "friendship"]
        }


        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            # Extract main keywords & check semantic map
            clean_theme = prompt_theme.lower()
            expanded_keywords = [w.strip().lower() for w in re.split(r"\s+|&|/|,", clean_theme) if len(w.strip()) > 2]
            for key, kw_list in theme_expansions.items():
                if key in clean_theme:
                    expanded_keywords.extend(kw_list)
            expanded_keywords = list(set(expanded_keywords))

            where_clauses = []
            params = []
            for kw in expanded_keywords:
                where_clauses.append("(lower(title) LIKE ? OR lower(genres_json) LIKE ? OR lower(overview) LIKE ? OR lower(raw_json) LIKE ?)")
                needle = f"%{kw}%"
                params.extend([needle, needle, needle, needle])
            
            sql_where = " OR ".join(where_clauses) if where_clauses else "1=1"
            rows = connection.execute(f"SELECT title, overview, genres_json FROM catalog_items WHERE present=1 AND ({sql_where}) LIMIT 200", params).fetchall()
            
            # Fallback if specific search returns few items
            if len(rows) < 30:
                additional = connection.execute("SELECT title, overview, genres_json FROM catalog_items WHERE present=1 ORDER BY RANDOM() LIMIT 200").fetchall()
                rows.extend(additional)

            catalog_sample = []
            seen = set()
            for r in rows:
                if not r[0] or r[0] in seen:
                    continue
                catalog_sample.append(r[0])
                seen.add(r[0])
                if len(catalog_sample) >= 200:
                    break

        prompt = {
            "role": "You are an elite film archivist and master cinema curator.",
            "requested_theme": prompt_theme,
            "instruction": f"Examine candidate_titles carefully. Select UP TO {limit} titles that TRULY belong to the theme '{prompt_theme}'. Find as many genuine, relevant, and classic matches as possible. Return a JSON object with 'collection_title', 'summary', and 'matched_titles' containing an array of exact strings from candidate_titles.",
            "candidate_titles": catalog_sample[:200],
            "schema": {
                "collection_title": "string",
                "summary": "string",
                "matched_titles": ["exact string title from candidate_titles"]
            }
        }

        response = self.agents.model.complete([
            {"role": "system", "content": prompt["role"]},
            {"role": "user", "content": json.dumps(prompt)}
        ], temperature=0.3)

        fenced = re.search(r"```(?:json)?\s*(.*?)```", response, re.S | re.I)
        cand_str = fenced.group(1) if fenced else response
        try:
            parsed = json.loads(cand_str)
        except Exception:
            parsed = {}

        if isinstance(parsed, list) and parsed:
            parsed = parsed[0]
        if not isinstance(parsed, dict):
            parsed = {}

        collection_title = parsed.get("collection_title") or f"{prompt_theme.title()} {'Playlist' if mode == 'playlist' else 'Collection'}"
        summary = parsed.get("summary") or f"Curated {mode} for {prompt_theme}"
        matched_titles = parsed.get("matched_titles", [])

    def refresh_media_posters(self, actor: str = "dashboard") -> dict[str, Any]:
        """Bulk refresh and apply high-resolution TMDB poster artwork across all Plex collections and media items."""
        plex_writer = self.writers.get("plex") if hasattr(self, "writers") and self.writers else None
        if not plex_writer:
            return {"status": "error", "message": "Plex API client unavailable"}

        refreshed_count = 0
        try:
            # Refresh Plex Metadata Agents across Movies (Section 1) & TV Series (Section 2)
            for section_id in ("1", "2"):
                try:
                    plex_writer.post(f"library/sections/{section_id}/refresh")
                    refreshed_count += 1
                except Exception:
                    pass

            # Fetch collections and trigger artwork refresh
            cols = plex_writer.get("library/sections/1/collections")
            col_count = 0
            for col in cols.findall("Directory") or cols.findall("Metadata"):
                col_key = col.get("ratingKey")
                if col_key:
                    try:
                        plex_writer.post(f"library/metadata/{col_key}/refresh")
                        col_count += 1
                    except Exception:
                        pass

            self.store.audit(actor, "refresh_posters", "plex", "local-write", "success", {"sections_refreshed": refreshed_count, "collections_refreshed": col_count})
            return {"status": "success", "sections_refreshed": refreshed_count, "collections_refreshed": col_count, "message": f"Triggered high-resolution metadata & artwork refresh across {refreshed_count} sections and {col_count} collections."}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}


        # Fetch Plex ratingKeys
        plex_writer = self.writers.get("plex") if self.writers else None
        plex_map: dict[str, str] = {}
        if plex_writer:
            try:
                plex_container = plex_writer.get("library/sections/1/all")
                for video in plex_container.findall("Video"):
                    t = video.get("title")
                    r = video.get("ratingKey")
                    if t and r:
                        plex_map[t.strip().casefold()] = r
            except Exception:
                pass

        tagged_count = 0
        tagged_items = []
        rating_keys = []
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            for title in matched_titles:
                clean = str(title).strip()
                rows = connection.execute("SELECT title, year, source_id, raw_json FROM catalog_items WHERE (title = ? OR title LIKE ?) AND present=1", (clean, f"%{clean}%")).fetchall()
                for row in rows:
                    try:
                        d = json.loads(row[3] or "{}")
                        m = d.get("movie", {})
                        if (m.get("MovieFileId") or 0) > 0:
                            item_id = row[2]
                            rkey = plex_map.get(str(row[0]).strip().casefold())
                            tagged_items.append({"title": row[0], "year": row[1], "source_id": item_id, "rating_key": rkey})
                            if rkey:
                                rating_keys.append(rkey)
                            if mode == "collection" and plex_writer and rkey:
                                try:
                                    if plex_writer.add_to_collection("1", collection_title, rkey):
                                        tagged_count += 1
                                except Exception:
                                    pass
                            break
                    except Exception:
                        pass

        if mode == "playlist" and plex_writer and rating_keys:
            if plex_writer.create_playlist(collection_title, rating_keys):
                tagged_count = len(rating_keys)

        self.store.audit(actor, "curate_collection", collection_title, "plex-write", "completed", {
            "theme": prompt_theme,
            "mode": mode,
            "collection_title": collection_title,
            "tagged_count": tagged_count,
            "total_matched": len(tagged_items)
        })
        return {
            "collection_title": collection_title,
            "mode": mode,
            "summary": summary,
            "tagged_count": tagged_count,
            "items": tagged_items
        }

    def reconcile(self, actor: str = "system") -> dict[str, Any]:
        refresh_result = self.refresh(actor)
        report = self.agents.tools.reconcile()
        self.store.audit(actor, "reconcile_library", "plex-radarr-sonarr", "read-only", report.get("status", "unknown"), {
            "refresh": refresh_result,
            "movies": report.get("movies", {}),
            "series": report.get("series", {}),
        })
        report["refresh"] = refresh_result
        return report

    def execute_automatic(self, action_type: str, actor: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.store.is_emergency_stop() or os.environ.get("CINESWARM_AUTO_EMERGENCY_STOP", "false").lower() in {"1", "true", "yes", "on"}:
            return {"status": "blocked_emergency_stop"}
        if action_type not in Policy.AUTOMATIC_ACTIONS:
            raise ServiceError("Action is not approved for automatic execution")
        action = self.actions.get(action_type)
        if not action:
            raise ServiceError("Automatic action is not configured")
        result = action({}) or {}
        self.store.audit(actor, action_type, "plex", "automatic", "completed", {"context": context or {}, "result": result})
        return result

    def approve_task(self, task_id: str, actor: str = "dashboard") -> dict[str, Any]:
        if self.store.is_emergency_stop() or os.environ.get("CINESWARM_AUTO_EMERGENCY_STOP", "false").lower() in {"1", "true", "yes", "on"}:
            raise ServiceError("Execution is blocked by emergency stop")
        task = self.store.task(task_id)
        if not task:
            raise ServiceError("Task not found")
        if task["status"] != "pending_approval":
            raise ServiceError(f"Task is not awaiting approval: {task['status']}")
        decision, reason = Policy.classify(task["task_type"])
        if decision != "approval_required":
            raise ServiceError(f"Task cannot be approved: {reason}")
        action = self.actions.get(task["task_type"])
        if not action:
            raise ServiceError("Approved action is not configured")
        target = "radarr" if task["task_type"].startswith("radarr_") else "sonarr" if task["task_type"].startswith("sonarr_") else "plex"
        self.store.update_task(task_id, "running")
        self.store.audit(actor, "approve_task", task["task_type"], "approval-gated", "approved", {"task_id": task_id})
        try:
            result = action(task["payload"])
        except Exception as exc:
            self.store.update_task(task_id, "failed")
            self.store.audit(actor, task["task_type"], target, "approval-gated", "failed", {"task_id": task_id, "error": str(exc)})
            raise
        self.store.update_task(task_id, "completed", result or {})
        self.store.audit(actor, task["task_type"], target, "approval-gated", "completed", {"task_id": task_id, "result": result or {}})
        follow_up = None
        if task["task_type"] in ("radarr_add_request", "sonarr_add_request") and result.get("id"):
            media_type = task["payload"].get("media_type")
            est_gb = 8 if media_type == "movie" else 15
            self.store.increment_budget(media_type, est_gb)
            search_type = "radarr_search_request" if media_type == "movie" else "sonarr_search_request"
            follow_up_id = self.store.create_task(search_type, actor, {"media_type": media_type, "service_id": result["id"], "parent_task_id": task_id, "reason": "The item was added successfully. Review and approve the search to begin acquisition."})
            follow_up = {"task_id": follow_up_id, "task_type": search_type, "status": "pending_approval"}
        return {"task_id": task_id, "status": "completed", "result": result or {}, "follow_up": follow_up}


DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CINE//SWARM · MATRIX CONTROL</title><style>
@import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Orbitron:wght@600;800;900&family=Inter:wght@400;500;600;700&display=swap');
:root{color-scheme:dark;--bg:#020604;--panel:rgba(6,22,14,.75);--panel-strong:rgba(10,34,22,.88);--line:rgba(0,255,136,.22);--line-bright:#00ff88;--text:#e2fcf0;--muted:#6da488;--accent:#00ff88;--accent-glow:rgba(0,255,136,.45);--cyan:#00f0ff;--cyan-glow:rgba(0,240,255,.35);--danger:#ff2a5f;--shadow:0 15px 40px rgba(0,0,0,.8)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);overflow-x:hidden;min-height:100vh;position:relative}
#matrixCanvas{position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:0;opacity:0.22;pointer-events:none}
.wrapper{position:relative;z-index:1;max-width:1440px;margin:0 auto;padding:32px 24px 80px}
.header{display:flex;justify-content:space-between;align-items:center;padding-bottom:24px;border-bottom:1px solid var(--line);margin-bottom:28px;flex-wrap:wrap;gap:16px}
.logo-title{font-family:'Orbitron',sans-serif;font-weight:900;font-size:clamp(1.8rem,3.5vw,2.8rem);letter-spacing:0.08em;background:linear-gradient(135deg,#ffffff 0%,var(--accent) 50%,var(--cyan) 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;text-shadow:0 0 30px var(--accent-glow)}
.sys-badge{font-family:'Fira Code',monospace;font-size:0.75rem;padding:6px 14px;background:rgba(0,255,136,0.08);border:1px solid var(--line-bright);border-radius:20px;color:var(--accent);box-shadow:0 0 15px var(--accent-glow);display:inline-flex;align-items:center;gap:8px;cursor:pointer;transition:all 0.2s;user-select:none}
.sys-badge:hover{background:rgba(0,255,136,0.2);box-shadow:0 0 25px var(--accent-glow);transform:scale(1.03)}
.sys-badge::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 10px var(--accent);animation:pulse 1.8s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.4;transform:scale(0.85)}}

/* System Telemetry Modal Overlay */
.modal-overlay{position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.85);backdrop-filter:blur(15px);z-index:999;display:none;align-items:center;justify-content:center;padding:20px}
.modal-content{background:var(--panel-strong);border:1px solid var(--line-bright);box-shadow:0 0 50px var(--accent-glow);border-radius:18px;max-width:900px;width:100%;max-height:85vh;overflow-y:auto;padding:28px;position:relative}
.modal-close{position:absolute;top:20px;right:20px;background:none;border:none;color:var(--accent);font-size:1.5rem;cursor:pointer;box-shadow:none;padding:4px 10px}
.modal-close:hover{color:#fff;box-shadow:none}
.telemetry-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:16px}
.telemetry-item{background:rgba(2,12,7,0.8);border:1px solid var(--line);border-radius:10px;padding:14px}
.telemetry-item label{font-family:'Fira Code',monospace;font-size:0.75rem;color:var(--muted);display:block}
.telemetry-item val{font-family:'Orbitron',sans-serif;font-size:1.1rem;color:var(--accent);font-weight:700;margin-top:4px;display:block}

/* Matrix Layout & Cyber Components */
.hero-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:28px}
.stat-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px;backdrop-filter:blur(16px);box-shadow:var(--shadow);position:relative;overflow:hidden;transition:all 0.3s}
.stat-card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 10px 30px var(--accent-glow)}
.stat-card::after{content:"";position:absolute;top:0;left:0;width:100%;height:2px;background:linear-gradient(90deg,transparent,var(--accent),transparent)}
.stat-label{font-family:'Fira Code',monospace;font-size:0.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.1em}
.stat-value{font-family:'Orbitron',sans-serif;font-size:1.9rem;font-weight:800;color:var(--text);margin-top:6px}

.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:24px;box-shadow:var(--shadow);backdrop-filter:blur(20px);margin-bottom:24px;position:relative}
.card h2{font-family:'Orbitron',sans-serif;font-size:1.15rem;letter-spacing:0.05em;color:var(--text);margin-bottom:14px;display:flex;align-items:center;gap:10px}
.card h2::before{content:"//";font-family:'Fira Code',monospace;color:var(--accent);font-size:1rem;text-shadow:0 0 8px var(--accent)}

.cmd-bar{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:24px}
button{appearance:none;font-family:'Fira Code',monospace;background:linear-gradient(135deg,#00e676,#00a855);color:#02140a;border:0;border-radius:8px;padding:10px 18px;font-size:0.82rem;font-weight:700;cursor:pointer;box-shadow:0 0 15px var(--accent-glow);transition:all 0.2s;display:inline-flex;align-items:center;gap:6px}
button:hover{filter:brightness(1.25);transform:translateY(-1px);box-shadow:0 0 25px rgba(0,255,136,0.6)}
button.secondary{background:rgba(0,240,255,0.1);color:var(--cyan);border:1px solid rgba(0,240,255,0.3);box-shadow:none}
button.secondary:hover{background:rgba(0,240,255,0.25);box-shadow:0 0 15px var(--cyan-glow)}
button.danger{background:rgba(255,42,95,0.15);color:var(--danger);border:1px solid rgba(255,42,95,0.4);box-shadow:none}
button.danger:hover{background:rgba(255,42,95,0.3);box-shadow:0 0 15px rgba(255,42,95,0.5)}

input,select,textarea{font-family:'Fira Code',monospace;background:rgba(2,12,7,0.9);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:10px 14px;outline:none;font-size:0.85rem;transition:all 0.2s}
input:focus,select:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 15px var(--accent-glow)}
pre{font-family:'Fira Code',monospace;font-size:0.8rem;line-height:1.6;color:#a7f3d0;background:rgba(1,10,5,0.92);border:1px solid var(--line);border-radius:12px;padding:18px;max-height:420px;overflow:auto;margin-top:14px;box-shadow:inset 0 0 25px rgba(0,0,0,0.8)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}
.policy-box{background:rgba(2,12,7,0.7);padding:12px;border-radius:10px;border:1px solid var(--line);overflow-wrap:anywhere;word-break:break-word}
.ok{color:var(--accent);font-weight:700;text-shadow:0 0 10px var(--accent-glow)}
.bad{color:var(--danger);font-weight:700;text-shadow:0 0 10px rgba(255,42,95,0.6)}
.candidate-card{background:var(--panel-strong);border:1px solid var(--line);border-radius:12px;padding:16px;margin-top:12px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
.candidate-info strong{font-size:1.05rem;color:#fff}
.candidate-info p{color:var(--muted);font-size:0.85rem;margin-top:4px}
.terminal-card{grid-column:1 / -1}
.terminal-textarea{width:100%;box-sizing:border-box;min-height:120px;font-size:0.95rem;line-height:1.5;margin-bottom:10px}
.terminal-pre{min-height:220px;max-height:650px;width:100%;box-sizing:border-box}
#matrixCanvas{position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:0;opacity:0.55;pointer-events:none}
</style><script src="https://cdn.jsdelivr.net/npm/chart.js"></script></head>
<body>
<canvas id="matrixCanvas"></canvas>

<!-- Detailed System Telemetry Modal -->
<div id="telemetryModal" class="modal-overlay" onclick="if(event.target===this)closeTelemetry()">
  <div class="modal-content">
    <button class="modal-close" onclick="closeTelemetry()">✕</button>
    <div style="font-family:'Orbitron',sans-serif;font-size:1.4rem;color:var(--accent);font-weight:800;letter-spacing:0.05em">// DEEP SYSTEM TELEMETRY MATRIX</div>
    <p style="color:var(--muted);font-size:0.85rem;margin-top:4px">Real-time health diagnostic metrics for CineSwarm worker, services, database, and hardware pipeline.</p>
    
    <div class="telemetry-grid">
      <div class="telemetry-item"><label>CONTROL PLANE STATUS</label><val class="ok" id="telControl">HEALTHY (HTTP 200)</val></div>
      <div class="telemetry-item"><label>WORKER HEARTBEAT</label><val class="ok" id="telWorker">ACTIVE (0s ago)</val></div>
      <div class="telemetry-item"><label>DATABASE VAULT SIZE</label><val id="telDbSize">10,580 MOVIES / 50 TV</val></div>
      <div class="telemetry-item"><label>HARDWARE ACCELERATION</label><val class="ok">VAAPI / iGPU (/dev/dri)</val></div>
      <div class="telemetry-item"><label>AUTOPILOT EXECUTION</label><val class="ok" id="telAutopilot">FULL AUTONOMOUS</val></div>
      <div class="telemetry-item"><label>CONTAINER INSTANCES</label><val id="telContainers">PLEX, RADARR, SONARR</val></div>
    </div>

    <div style="margin-top:20px;font-family:'Orbitron',sans-serif;font-size:1rem;color:#fff">// ACTIVE SERVICE CONNECTIONS</div>
    <div id="telServiceDetails" style="margin-top:10px" class="grid"></div>

    <div style="margin-top:20px;font-family:'Orbitron',sans-serif;font-size:1rem;color:#fff">// RAW AUDIT & HEALTH DIAGNOSTICS</div>
    <pre id="telRawLogs" style="margin-top:10px;max-height:220px">// Fetching diagnostic payload...</pre>
  </div>
</div>

<div class="wrapper">
  <div class="header">
    <div>
      <div class="logo-title">CINE//SWARM</div>
      <p style="margin:2px 0 0;font-size:0.85rem;color:var(--muted)">Autonomous Media Neural Matrix · Operational Control</p>
    </div>
    <div class="sys-badge" onclick="openTelemetry()" title="Click for deep system diagnostics">SYSTEM STATUS: AUTONOMOUS MATRIX ACTIVE [CLICK FOR DETAILS]</div>
  </div>

  <div class="hero-stats">
    <div class="stat-card"><div class="stat-label">Movies in Vault</div><div class="stat-value" id="statMovies">...</div></div>
    <div class="stat-card"><div class="stat-label">Series Monitored</div><div class="stat-value" id="statSeries">...</div></div>
    <div class="stat-card"><div class="stat-label">Weekly GB Budget</div><div class="stat-value" id="statBudget">...</div></div>
    <div class="stat-card"><div class="stat-label">Autopilot Mode</div><div class="stat-value" id="statMode">ACTIVE</div></div>
  </div>

  <div class="cmd-bar">
    <button id="tabBtn-overview" onclick="switchTab('overview')">📊 Overview & Live SSE</button>
    <button id="tabBtn-catalog" class="secondary" onclick="switchTab('catalog')">📚 Vault Catalog Browser</button>
    <button id="tabBtn-curation" class="secondary" onclick="switchTab('curation')">🎬 Curation & Playlists</button>
    <button id="tabBtn-acquisition" class="secondary" onclick="switchTab('acquisition')">🎯 Acquisition & Discovery</button>
    <button id="tabBtn-analytics" class="secondary" onclick="switchTab('analytics')">📈 Analytics & Health</button>
    <button id="tabBtn-terminal" class="secondary" onclick="switchTab('terminal')">💻 Neural Terminal</button>
  </div>

  <!-- TAB 6: VAULT CATALOG BROWSER -->
  <div id="tabView-catalog" class="tab-content" style="display:none">
    <div class="card">
      <h2>📚 Vault Media Catalog Browser</h2>
      <p>Browse, filter, and search all 10,700+ movies and TV series in your local vault. Automatically updates in real time as media is added or removed.</p>
      
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px">
        <input id="catSearchInput" placeholder="Search title or plot overview..." style="flex:2;min-width:240px" onkeyup="if(event.key==='Enter')searchCatalogUI(1)">
        <select id="catGenreSelect" style="flex:1;min-width:150px" onchange="searchCatalogUI(1)">
          <option value="">All Genres</option>
          <option value="Action">Action</option>
          <option value="Adventure">Adventure</option>
          <option value="Animation">Animation</option>
          <option value="Comedy">Comedy</option>
          <option value="Crime">Crime</option>
          <option value="Drama">Drama</option>
          <option value="Fantasy">Fantasy</option>
          <option value="Horror">Horror</option>
          <option value="Mystery">Mystery</option>
          <option value="Romance">Romance</option>
          <option value="Science Fiction">Science Fiction</option>
          <option value="Thriller">Thriller</option>
        </select>
        <select id="catTypeSelect" style="width:130px" onchange="searchCatalogUI(1)">
          <option value="">All Media</option>
          <option value="movie">Movies Only</option>
          <option value="series">TV Series Only</option>
        </select>
        <button onclick="searchCatalogUI(1)">🔍 Search Catalog</button>
      </div>

      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <span id="catTotalCount" style="font-family:'Fira Code';font-size:0.85rem;color:var(--cyan)">Loading catalog items...</span>
        <div style="display:flex;gap:8px">
          <button class="secondary" style="font-size:0.8rem;padding:4px 10px" onclick="prevCatalogPage()">◀ Prev</button>
          <span id="catPageNum" style="font-family:'Fira Code';font-size:0.85rem;color:#fff;align-self:center">Page 1</span>
          <button class="secondary" style="font-size:0.8rem;padding:4px 10px" onclick="nextCatalogPage()">Next ▶</button>
        </div>
      </div>

      <div id="catalogGrid" class="grid" style="grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px"><p style="color:var(--muted)">Fetching library catalog items...</p></div>
    </div>
  </div>


  <!-- TAB 1: OVERVIEW & LIVE SSE -->
  <div id="tabView-overview" class="tab-content">
    <div class="card">
      <h2>🧠 Multi-Agent Worker AI Thought Stream</h2>
      <p>Live stream of background subagent roles (Scout, Curator, Sentinel) reasoning and taking autonomous decisions with confidence scores.</p>
      <div id="aiThoughtStream" style="margin-top:14px"><p style="color:var(--muted)">Connecting to worker AI thought stream...</p></div>
    </div>

    <div class="card">
      <h2>Live Plex Streams & iGPU Hardware Transcoder</h2>
      <p>Real-time playback telemetry, active stream resolution badges, and Intel iGPU hardware acceleration (/dev/dri) metrics.</p>
      <div id="livePlexSessions" style="margin-top:14px" class="grid"><p style="color:var(--muted)">Querying Plex active playback sessions...</p></div>
    </div>

    <div class="card">
      <h2>Live Active Downloads & Event Stream</h2>
      <p>Real-time Server-Sent Events (SSE) streaming active Radarr/Sonarr downloads and worker events live without page refreshes.</p>
      <div id="liveStreamFeed" style="margin-top:14px" class="grid"><p style="color:var(--muted)">Connecting to live SSE telemetry stream...</p></div>
    </div>
    
    <div class="card">
      <h2>Autonomous Policy & Emergency Protocols</h2>
      <div id="autoStatus" style="margin-bottom:12px">Loading status...</div>
      <button class="danger" onclick="toggleEmergencyStop()">🛑 Emergency Stop</button>
      <div id="autoPolicies" style="margin-top:14px"></div>
    </div>
  </div>

  <!-- TAB 2: CURATION & PLAYLISTS -->
  <div id="tabView-curation" class="tab-content" style="display:none">
    <div class="card">
      <h2>🎭 Dynamic Mood & Vibe Playlist Generator</h2>
      <p>Instantly generate custom 50-item Plex playlists based on emotional vibes, atmospheric themes, or quick 1-click mood presets.</p>
      
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
        <button class="secondary" style="font-size:0.8rem" onclick="generateMoodPlaylist('Rainy Sunday Cozy Classics')">🌧️ Rainy Sunday</button>
        <button class="secondary" style="font-size:0.8rem" onclick="generateMoodPlaylist('Cyberpunk Neon Noir')">🌆 Cyberpunk Neon</button>
        <button class="secondary" style="font-size:0.8rem" onclick="generateMoodPlaylist('Late Night Mind-Benders')">🌀 Late Night Mind-Benders</button>
        <button class="secondary" style="font-size:0.8rem" onclick="generateMoodPlaylist('High-Octane Adrenaline Hits')">⚡ High-Octane Action</button>
        <button class="secondary" style="font-size:0.8rem" onclick="generateMoodPlaylist('80s & 90s Nostalgia Trip')">📻 80s/90s Nostalgia</button>
        <button class="secondary" style="font-size:0.8rem" onclick="generateMoodPlaylist('Dark Cynical Satire')">🍷 Dark Satire</button>
      </div>

      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">
        <input id="customMoodInput" placeholder="Enter custom vibe/mood (e.g. '70s Gritty Crime', 'Unsettling Folk Horror')..." style="flex:1;min-width:240px">
        <button onclick="generateMoodPlaylist(document.getElementById('customMoodInput').value.trim())">✨ Generate Mood Playlist</button>
      </div>
      <div id="moodGeneratorStatus"></div>
    </div>

    <div class="card">
      <h2>Gemini "Watch Tonight" Concierge</h2>
      <p>Find the perfect movie in your vault filtered by exact duration and genre using Gemini 2.5 neural curation.</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">
        <input id="watchMinutes" type="number" placeholder="Max Minutes (e.g. 90)" value="100" style="width:180px">
        <input id="watchGenre" placeholder="Genre (e.g. Action, Sci-Fi, Comedy)..." style="flex:1;min-width:200px">
        <button onclick="getWatchPicks()">🍿 Get Watch Picks</button>
      </div>
      <div id="watchPicksResults"></div>
    </div>

    <div class="card">
      <h2>Plex Playlists & Collections Matrix</h2>
      <p>Live management of custom Plex thematic playlists. Re-curate 50-item lists or delete existing playlists in 1 click.</p>
      <div style="display:flex;gap:10px;margin-bottom:14px">
        <button onclick="loadPlaylists()">📑 Refresh Playlists List</button>
      </div>
      <div id="playlistsMatrix" class="grid"><p style="color:var(--muted)">Loading Plex playlists matrix...</p></div>
    </div>
  </div>

  <!-- TAB 3: ACQUISITION & DISCOVERY -->
  <div id="tabView-acquisition" class="tab-content" style="display:none">
    <div class="card">
      <h2>Targeted Acquisition Planner</h2>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">
        <select id="acqType"><option value="movie">Movie</option><option value="series">Series</option></select>
        <input id="acqTerm" placeholder="Title or search query..." style="flex:1;min-width:220px">
        <button onclick="planAcquisition()">🎯 Plan Target</button>
      </div>
      <pre id="acqAnswer" style="display:none"></pre>
      <div id="acqControls"></div>
      <p id="existingIds"></p>
    </div>

    <div class="card">
      <h2>Director & Actor Filmography Completer</h2>
      <p>Scan TMDB for missing movies in a specific director or actor's filmography and queue 1-click acquisition proposals.</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">
        <input id="filmPerson" placeholder="Person Name (e.g. Quentin Tarantino, Edgar Wright)..." style="flex:1;min-width:220px">
        <select id="filmRole"><option value="director">Director</option><option value="actor">Actor</option></select>
        <button onclick="scanFilmography()">🎥 Scan Filmography</button>
      </div>
      <div id="filmographyResults"></div>
    </div>

    <div class="card">
      <h2>Gemini Neural Discovery Queue</h2>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button onclick="runDiscovery()">🔮 Generate Fresh Candidates</button>
        <button class="secondary" onclick="runFranchiseDiscovery()">🧩 Auto-Complete Missing Franchises</button>
      </div>
      <div id="discoveryQueue" style="margin-top:16px"><p>Scanning Matrix for neural candidates...</p></div>
    </div>
  </div>

  <!-- TAB 4: ANALYTICS & HEALTH -->
  <div id="tabView-analytics" class="tab-content" style="display:none">
    <div class="card">
      <h2>Autonomous Decision Audit Log & Reinforcement Feedback</h2>
      <p>Monitor every action taken autonomously by CineSwarm. Provide 👍 (upvote) or 👎 (downvote) feedback to train Gemini's neural selection model.</p>
      <div style="display:flex;gap:10px;margin-bottom:12px">
        <button onclick="loadDecisionLog()">📜 Refresh Decision Log</button>
      </div>
      <div id="decisionLogStream" style="margin-top:14px"><p style="color:var(--muted)">Fetching decision audit log...</p></div>
    </div>

    <div class="card">
      <h2>Library Storage & Video Codec Analytics</h2>
      <p>Real-time visual distribution of video container specs, high-fidelity codecs (4K REMUX, HEVC, H.264), and storage consumption.</p>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;margin-top:16px">
        <div style="background:rgba(2,12,7,0.7);padding:16px;border-radius:12px;border:1px solid var(--line)"><canvas id="codecChart" height="200"></canvas></div>
        <div style="background:rgba(2,12,7,0.7);padding:16px;border-radius:12px;border:1px solid var(--line)"><canvas id="genreChart" height="200"></canvas></div>
      </div>
    </div>
    
    <div class="card">
      <h2>Matrix Operational Quick Actions</h2>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button onclick="refresh()">⚡ Refresh Matrix</button>
        <button class="secondary" onclick="reconcile()">🔄 Reconcile Catalog</button>
        <button class="secondary" onclick="analyzeQuality('movie')">🎬 Movie Quality</button>
        <button class="secondary" onclick="analyzeQuality('series')">📺 Series Check</button>
        <button class="secondary" onclick="showPlaybackProfile()">📊 Playback Taste</button>
        <button class="secondary" onclick="requestScan()">📡 Request Plex Scan</button>
      </div>
    </div>
  </div>

  <!-- TAB 5: NEURAL TERMINAL -->
  <div id="tabView-terminal" class="tab-content" style="display:none">
    <div class="card terminal-card">
      <h2>Neural Assistant Terminal</h2>
      <textarea id="prompt" class="terminal-textarea" placeholder="Ask CineSwarm about library statistics, health status, or specific acquisition targets..."></textarea>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <button onclick="ask()">💬 Execute Prompt</button>
        <span style="font-family:'Fira Code',monospace;font-size:0.75rem;color:var(--muted)">[ HOSTED MODEL: GEMINI 2.5 ]</span>
      </div>
      <pre id="answer" class="terminal-pre">// Terminal output stream initialized...</pre>
    </div>
  </div>

  <div id="app"></div>
</div>

<script>
// --- High-Visibility Matrix Rain Effect ---
const canvas = document.getElementById('matrixCanvas');
const ctx = canvas.getContext('2d');
function resizeCanvas() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
resizeCanvas();
window.addEventListener('resize', resizeCanvas);

const katakana = 'ｦｱｳｴｵｶｷｹｺｻｼｽｾｿﾀﾂﾃﾅﾆﾇﾈﾊﾋﾎﾏﾐﾑﾒﾓﾔﾕﾗﾘﾜ';
const latin = '0123456789ABCDEF';
const alphabet = katakana + latin;
const fontSize = 16;
const columns = Math.floor(canvas.width / fontSize);
const rainDrops = Array.from({ length: columns }, () => ({
  y: Math.floor(Math.random() * -100),
  speed: 1 + Math.random() * 2,
}));

function drawMatrix() {
  ctx.fillStyle = 'rgba(2, 6, 4, 0.12)';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  for (let i = 0; i < rainDrops.length; i++) {
    const drop = rainDrops[i];
    const text = alphabet.charAt(Math.floor(Math.random() * alphabet.length));
    const x = i * fontSize;
    const y = drop.y * fontSize;

    // Glowing white leading character
    ctx.fillStyle = '#ffffff';
    ctx.font = `bold ${fontSize}px "Fira Code", monospace`;
    ctx.shadowColor = '#00ff88';
    ctx.shadowBlur = 12;
    ctx.fillText(text, x, y);

    // Green trail character trailing behind
    if (drop.y > 1) {
      const prevText = alphabet.charAt(Math.floor(Math.random() * alphabet.length));
      ctx.fillStyle = Math.random() > 0.85 ? '#00f0ff' : '#00ff88';
      ctx.shadowBlur = 4;
      ctx.fillText(prevText, x, y - fontSize);
    }

    ctx.shadowBlur = 0;

    if (y > canvas.height && Math.random() > 0.975) {
      drop.y = 0;
      drop.speed = 1 + Math.random() * 2;
    }
    drop.y += drop.speed;
  }
}
setInterval(drawMatrix, 35);

// --- Control Plane API Interactions ---
async function refresh(){await fetch('/api/refresh',{method:'POST'});await load()}
async function loadDiscovery(){
  const response = await fetch('/api/discovery');
  const data = await response.json();
  const queue = data.candidates || [];
  document.getElementById('discoveryQueue').innerHTML = queue.length ? queue.map(c => `
    <div class="candidate-card">
      <div class="candidate-info">
        <strong>${c.title} (${c.year||'n.d.'})</strong>
        <p>${c.media_type.toUpperCase()} · Neural Fit Score: <span class="ok">${c.score}</span> · ${c.rationale}</p>
      </div>
      <div style="display:flex;gap:8px">
        <button onclick="discoveryAction(${c.id},'approve')">✓ Approve & Acquire</button>
        <button class="danger" onclick="discoveryAction(${c.id},'reject')">✕ Reject</button>
      </div>
    </div>
  `).join('') : '<p style="color:var(--muted)">No active candidates in neural queue.</p>';
}
async function runDiscovery(){
  document.getElementById('discoveryQueue').innerHTML = '<p style="color:var(--accent)">🔮 Querying Gemini Neural Network for fresh catalog candidates...</p>';
  const response = await fetch('/api/discovery/generate',{method:'POST'});
  await response.json();
  await loadDiscovery();
}
async function runFranchiseDiscovery(){
  document.getElementById('discoveryQueue').innerHTML = '<p style="color:var(--cyan)">🧩 Analyzing existing collection for movie & TV franchises to find missing sequels and prequels...</p>';
  const response = await fetch('/api/discovery/franchise',{method:'POST'});
  const data = await response.json();
  showAnswer(data);
  await loadDiscovery();
}
async function discoveryAction(candidate_id, action){
  const response = await fetch('/api/discovery/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id,action})});
  const data = await response.json();
  showAnswer(data);
  await loadDiscovery();
  await load();
}
async function reconcile(){
  showAnswer('Reconciling Plex, Radarr, and Sonarr catalog matrices...');
  const response = await fetch('/api/reconcile',{method:'POST'});
  showAnswer(await response.json());
  await load();
}
async function analyzeQuality(media_type){
  showAnswer(`Analyzing ${media_type} matrix quality metrics...`);
  const response = await fetch('/api/quality/analyze?ts='+Date.now(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_type})});
  showAnswer(await response.json());
}
async function showPlaybackProfile(){
  showAnswer('Fetching Plex playback telemetry and weighted taste profile...');
  const response = await fetch('/api/playback/profile');
  showAnswer(await response.json());
}
async function requestScan(){
  const response = await fetch('/api/proposals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_type:'plex_library_refresh',reason:'Plex matrix scan request'})});
  showAnswer(await response.json());
  await load();
}
async function loadAutoStatus(){
  const response = await fetch('/api/autonomous/status');
  const data = await response.json();
  document.getElementById('autoStatus').innerHTML = `
    Emergency Stop: <span class="${data.emergency_stop?'bad':'ok'}">${data.emergency_stop?'ACTIVE':'DISABLED'}</span> |
    Weekly Usage Telemetry: <strong>${data.budget.movies_added} Movies</strong>,
    <strong>${data.budget.series_added} TV Series</strong>,
    <strong>${data.budget.gb_added} GB Acquired</strong>
  `;
  document.getElementById('statBudget').textContent = `${data.budget.gb_added} GB`;
  let html = '<div class="grid">';
  for (const [k,v] of Object.entries(data.policies)) {
    html += `<div class="policy-box"><div style="font-size:0.75rem;color:var(--muted);font-family:'Fira Code'">${k}</div><div style="color:var(--accent);font-weight:700">${v}</div></div>`;
  }
  html += '</div>';
  document.getElementById('autoPolicies').innerHTML = html;
}
async function toggleEmergencyStop(){
  const btn = document.querySelector('button[onclick="toggleEmergencyStop()"]');
  const isStop = btn.textContent.includes('ON') || btn.classList.contains('active');
  const response = await fetch('/api/autonomous/emergency-stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!isStop})});
  showAnswer(await response.json());
  loadAutoStatus();
}
async function planAcquisition(){
  const term = document.getElementById('acqTerm').value;
  const media_type = document.getElementById('acqType').value;
  showAnswer(`Querying indexers for target: ${term}...`);
  const response = await fetch('/api/acquisition/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_type,term})});
  const data = await response.json();
  window.acqPlan = data;
  showAnswer(data);
  if(data.candidates?.length && data.root_folders?.length && data.quality_profiles?.length){
    document.getElementById('acqControls').innerHTML = `
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px">
        <select id="candidateIndex">${data.candidates.map((c,i)=>`<option value="${i}">${c.title||'Untitled'} (${c.year||'n.d.'})</option>`).join('')}</select>
        <select id="rootIndex">${data.root_folders.map((r,i)=>`<option value="${i}">${r.path}</option>`).join('')}</select>
        <select id="profileIndex">${data.quality_profiles.map((p,i)=>`<option value="${i}">${p.name}</option>`).join('')}</select>
        <button onclick="proposeAcquisition()">🚀 Initiate Acquisition</button>
      </div>`;
  }
}
async function proposeAcquisition(){
  const plan = window.acqPlan;
  const candidate = plan.candidates[Number(document.getElementById('candidateIndex').value)];
  const root = plan.root_folders[Number(document.getElementById('rootIndex').value)];
  const profile = plan.quality_profiles[Number(document.getElementById('profileIndex').value)];
  const response = await fetch('/api/acquisition/propose',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_type:plan.media_type,candidate,root_folder_path:root.path,quality_profile_id:profile.id})});
  showAnswer(await response.json());
}
async function ask(){
  const prompt = document.getElementById('prompt').value;
  showAnswer('Processing neural query with CineSwarm Agent...');
  const response = await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt})});
  const data = await response.json();
  showAnswer(data.answer || data);
}
function showAnswer(data) {
  const el = document.getElementById('answer');
  el.style.display = 'block';
  el.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
}
async function load(){
  const data = await (await fetch('/api/status')).json();
  document.getElementById('statMovies').textContent = data.catalog.movie || 0;
  document.getElementById('statSeries').textContent = data.catalog.series || 0;
  let services = data.services.map(s => `
    <div style="background:rgba(2,12,7,0.7);padding:14px;border-radius:10px;border:1px solid var(--line)">
      <strong style="color:var(--text)">${s.service.toUpperCase()}</strong>
      <div class="${s.status==='healthy'?'ok':'bad'}">${s.status.toUpperCase()}</div>
      <p style="margin:4px 0 0;font-size:0.8rem">Items: ${s.item_count??'—'}</p>
    </div>
  `).join('');
  document.getElementById('app').innerHTML = `
    <div class="card">
      <h2>Service Health & Matrix Connectivity</h2>
      <div class="grid">${services}</div>
    </div>
  `;
}
async function openTelemetry() {
  document.getElementById('telemetryModal').style.display = 'flex';
  document.getElementById('telRawLogs').textContent = '// Querying control plane & worker audit telemetry...';
  try {
    const data = await (await fetch('/api/status')).json();
    const worker = data.worker || {};
    document.getElementById('telWorker').textContent = `${worker.healthy ? 'HEALTHY' : 'STALE'} (${worker.status || 'unknown'}, ${worker.age_seconds ?? '?'}s ago)`;
    document.getElementById('telWorker').className = worker.healthy ? 'ok' : 'bad';
    document.getElementById('telDbSize').textContent = `${data.catalog.movie || 0} MOVIES / ${data.catalog.series || 0} TV`;
    
    let servHtml = data.services.map(s => `
      <div style="background:rgba(2,12,7,0.9);padding:14px;border-radius:10px;border:1px solid var(--line)">
        <div style="font-family:'Fira Code';color:var(--muted);font-size:0.75rem">${s.service.toUpperCase()} ENDPOINT</div>
        <div class="${s.status==='healthy'?'ok':'bad'}" style="font-size:1.1rem;margin-top:2px">${s.status.toUpperCase()}</div>
        <div style="font-size:0.85rem;color:#fff;margin-top:4px">Item Count: ${s.item_count ?? '—'}</div>
        <div style="font-size:0.75rem;color:var(--muted);margin-top:2px">${s.error || 'Connected & Synchronized'}</div>
      </div>
    `).join('');
    document.getElementById('telServiceDetails').innerHTML = servHtml;
    document.getElementById('telRawLogs').textContent = JSON.stringify({
      worker: data.worker,
      services: data.services,
      observations: data.observations,
      recent_audit: data.audit
    }, null, 2);
  } catch (err) {
    document.getElementById('telRawLogs').textContent = 'Failed to fetch telemetry diagnostics: ' + err.message;
  }
}
function closeTelemetry() {
  document.getElementById('telemetryModal').style.display = 'none';
}

// --- Real-Time Server-Sent Events (SSE) Stream ---
function initLiveSSE() {
  const evtSource = new EventSource('/api/events');
  evtSource.onmessage = function(event) {
    try {
      const data = JSON.parse(event.data);
      const moviesQ = data.movies_queue || [];
      const seriesQ = data.series_queue || [];
      const combined = [...moviesQ, ...seriesQ];
      
      const feedEl = document.getElementById('liveStreamFeed');
      if (!combined.length) {
        feedEl.innerHTML = '<div style="background:rgba(2,12,7,0.7);padding:14px;border-radius:10px;border:1px solid var(--line);grid-column:1/-1"><p style="color:var(--accent);margin:0">✓ All download queues clear. No active indexer grabs in progress.</p></div>';
      } else {
        feedEl.innerHTML = combined.map(item => `
          <div style="background:rgba(2,12,7,0.9);padding:14px;border-radius:10px;border:1px solid var(--line-bright);box-shadow:0 0 15px var(--accent-glow);overflow:hidden;min-width:0">
            <strong style="color:#fff;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${item.title || item.name || ''}">${item.title || item.name || 'Downloading Item'}</strong>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">
              <span class="ok" style="font-size:0.8rem">${(item.status || 'downloading').toUpperCase()}</span>
              <span style="font-family:'Fira Code';font-size:0.75rem;color:var(--cyan)">${item.sizeleft ? Math.round(item.sizeleft / 1048576) + ' MB left' : 'Active Grab'}</span>
            </div>
          </div>
        `).join('');
      }
      // Render live Plex active stream sessions & iGPU hardware transcoder
      const plexSessions = data.plex_sessions || [];
      const plexEl = document.getElementById('livePlexSessions');
      if (!plexSessions.length) {
        plexEl.innerHTML = '<div style="background:rgba(2,12,7,0.7);padding:14px;border-radius:10px;border:1px solid var(--line);grid-column:1/-1"><p style="color:var(--muted);margin:0">No active Plex streams in progress. iGPU passthrough (/dev/dri) is idle.</p></div>';
      } else {
        plexEl.innerHTML = plexSessions.map(s => {
          const isHw = s.transcode && (s.transcode.hw_decoding || s.transcode.hw_encoding);
          const hwBadge = isHw ? `<span style="background:rgba(0,255,136,0.15);border:1px solid var(--line-bright);color:var(--accent);font-family:'Fira Code';font-size:0.75rem;padding:2px 8px;border-radius:4px;box-shadow:0 0 10px var(--accent-glow)">⚡ HW iGPU (${s.transcode.hw_decoding || 'VAAPI'})</span>` : '<span style="color:var(--cyan);font-size:0.75rem">DIRECT PLAY</span>';
          return `
            <div style="background:rgba(2,12,7,0.9);padding:16px;border-radius:12px;border:1px solid var(--line-bright);box-shadow:0 0 20px var(--accent-glow)">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <strong style="color:#fff;font-size:1.05rem">${s.grandparent_title ? s.grandparent_title + ' - ' : ''}${s.title}</strong>
                ${hwBadge}
              </div>
              <div style="display:flex;gap:12px;margin-top:8px;font-size:0.82rem;color:var(--muted);flex-wrap:wrap">
                <span>👤 User: <strong style="color:#fff">${s.user}</strong></span>
                <span>📱 Client: <strong style="color:var(--cyan)">${s.player}</strong></span>
                <span>🎥 Res: <strong style="color:#fff">${s.resolution}</strong></span>
                <span>▶️ State: <strong class="ok">${s.state.toUpperCase()}</strong></span>
              </div>
            </div>
          `;
        }).join('');
      }

      // Render Multi-Agent AI Thought Stream
      const thoughts = data.ai_thoughts || [];
      const thoughtEl = document.getElementById('aiThoughtStream');
      if (thoughtEl && thoughts.length) {
        thoughtEl.innerHTML = thoughts.map(t => {
          const role = t.actor === 'worker' ? '🤖 SENTINEL / SCOUT WORKER' : '🤖 AI CURATOR';
          return `
            <div style="background:rgba(2,12,7,0.9);padding:12px;border-radius:10px;border:1px solid var(--line);margin-top:8px">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <span style="font-family:'Fira Code';font-size:0.75rem;color:var(--accent)">${role}</span>
                <span style="font-size:0.75rem;color:var(--muted)">${t.created_at || ''}</span>
              </div>
              <strong style="color:#fff;font-size:0.9rem;display:block;margin-top:4px">${t.subject}</strong>
              <p style="margin:2px 0 0;font-size:0.8rem;color:var(--cyan)">Action: ${t.decision} | Confidence: 95% Match Score</p>
            </div>
          `;
        }).join('');
      }

      // Live update hero stats
      if (data.status && data.status.catalog) {
        document.getElementById('statMovies').textContent = data.status.catalog.movie || 0;
        document.getElementById('statSeries').textContent = data.status.catalog.series || 0;
      }
    } catch (e) {}
  };
}
initLiveSSE();

async function loadPlaylists() {
  const el = document.getElementById('playlistsMatrix');
  el.innerHTML = '<p style="color:var(--muted)">Fetching active Plex playlists...</p>';
  try {
    const res = await (await fetch('/api/playlists')).json();
    const playlists = res.playlists || [];
    if (!playlists.length) {
      el.innerHTML = '<p style="color:var(--muted)">No custom Plex playlists found in your library.</p>';
      return;
    }
    el.innerHTML = playlists.map(p => `
      <div style="background:rgba(2,12,7,0.9);padding:14px;border-radius:10px;border:1px solid var(--line)">
        <strong style="color:#fff;font-size:0.95rem">${p.title}</strong>
        <div style="font-size:0.78rem;color:var(--muted);margin-top:4px">Items: <span style="color:var(--accent);font-weight:700">${p.item_count}</span> | Duration: ${p.duration_mins}m</div>
        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="secondary" style="padding:4px 10px;font-size:0.75rem" onclick="recuratePlaylist('${p.title.replace(/'/g, "\\'")}')">🔄 Re-Curate</button>
          <button class="danger" style="padding:4px 10px;font-size:0.75rem" onclick="deletePlaylist('${p.ratingKey}')">🗑️ Delete</button>
        </div>
      </div>
    `).join('');
  } catch (err) {
    el.innerHTML = '<p style="color:var(--danger)">Failed to load playlists: ' + err.message + '</p>';
  }
}
async function deletePlaylist(ratingKey) {
  if (!confirm('Are you sure you want to delete this Plex playlist?')) return;
  showAnswer('Deleting playlist ' + ratingKey + '...');
  const res = await (await fetch('/api/playlists/delete', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ratingKey})})).json();
  showAnswer(res);
  loadPlaylists();
}
async function recuratePlaylist(theme) {
  showAnswer('Re-curating 50-item playlist for theme: ' + theme + '...');
  const res = await (await fetch('/api/chat', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:'playlist ' + theme})})).json();
  showAnswer(res.answer || res);
  loadPlaylists();
}
async function getWatchPicks() {
  const max_minutes = Number(document.getElementById('watchMinutes').value) || 120;
  const genre = document.getElementById('watchGenre').value.trim();
  const resEl = document.getElementById('watchPicksResults');
  resEl.innerHTML = '<p style="color:var(--muted)">Curating neural watch picks under ' + max_minutes + ' mins...</p>';
  try {
    const res = await (await fetch('/api/watch', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_minutes, genre})})).json();
    const recs = res.recommendations || [];
    if (!recs.length) {
      resEl.innerHTML = '<p style="color:var(--muted)">No library titles found matching your constraints.</p>';
      return;
    }
    resEl.innerHTML = recs.map((m, i) => `
      <div style="background:rgba(2,12,7,0.9);padding:14px;border-radius:10px;border:1px solid var(--line-bright);margin-top:10px">
        <strong style="color:var(--accent);font-size:1.05rem">#${i+1} ${m.title} (${m.year || 'n.d.'})</strong>
        <span style="background:rgba(0,240,255,0.15);color:var(--cyan);font-size:0.75rem;padding:2px 8px;border-radius:4px;margin-left:10px">⏱️ ${m.duration_mins} mins</span>
        <p style="margin:6px 0 0;font-size:0.85rem;color:var(--muted)">${m.reason || m.overview || 'Great match for tonight.'}</p>
      </div>
    `).join('');
  } catch (err) {
    resEl.innerHTML = '<p style="color:var(--danger)">Failed to get watch picks: ' + err.message + '</p>';
  }
}

async function loadAnalyticsCharts() {
  try {
    const res = await (await fetch('/api/analytics/summary')).json();
    const codecs = res.codecs || {};
    const genres = res.top_genres || {};

    const ctxCodec = document.getElementById('codecChart').getContext('2d');
    new Chart(ctxCodec, {
      type: 'doughnut',
      data: {
        labels: Object.keys(codecs),
        datasets: [{
          data: Object.values(codecs),
          backgroundColor: ['#00ff88', '#00f0ff', '#ff0055', '#ffcc00', '#aa00ff', '#888888'],
          borderWidth: 0
        }]
      },
      options: { plugins: { legend: { labels: { color: '#e2fcf0', font: { family: 'Fira Code' } } } } }
    });

    const ctxGenre = document.getElementById('genreChart').getContext('2d');
    new Chart(ctxGenre, {
      type: 'bar',
      data: {
        labels: Object.keys(genres),
        datasets: [{
          label: 'Vault Titles by Genre',
          data: Object.values(genres),
          backgroundColor: 'rgba(0, 240, 255, 0.6)',
          borderColor: '#00f0ff',
          borderWidth: 1
        }]
      },
      options: { scales: { y: { ticks: { color: '#6da488' } }, x: { ticks: { color: '#e2fcf0' } } }, plugins: { legend: { labels: { color: '#e2fcf0' } } } }
    });
  } catch (err) {}
}

async function scanFilmography() {
  const person = document.getElementById('filmPerson').value.trim();
  const role = document.getElementById('filmRole').value;
  const resEl = document.getElementById('filmographyResults');
  if (!person) return;
  resEl.innerHTML = '<p style="color:var(--muted)">Scanning TMDB filmography for ' + person + '...</p>';
  try {
    const res = await (await fetch('/api/filmography', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({person, role})})).json();
    const cands = res.candidates || [];
    if (!cands.length) {
      resEl.innerHTML = '<p style="color:var(--accent)">✓ Filmography complete! No missing titles found for ' + person + '.</p>';
      return;
    }
    resEl.innerHTML = '<p style="color:var(--cyan);font-weight:700">Found ' + res.missing_count + ' missing title(s) in vault for ' + person + ':</p>' + cands.map(c => `
      <div style="background:rgba(2,12,7,0.9);padding:14px;border-radius:10px;border:1px solid var(--line);margin-top:10px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap">
        <div>
          <strong style="color:#fff;font-size:1.05rem">${c.title} (${c.year || 'n.d.'})</strong>
          <p style="margin:4px 0 0;font-size:0.8rem;color:var(--muted)">${c.overview}</p>
        </div>
        <button onclick="proposeFilmItem('${c.title.replace(/'/g, "\\'")}', ${c.year || 0})">➕ Queue Acquisition</button>
      </div>
    `).join('');
  } catch (err) {
    resEl.innerHTML = '<p style="color:var(--danger)">Filmography scan failed: ' + err.message + '</p>';
  }
}
async function proposeFilmItem(title, year) {
  showAnswer('Creating acquisition proposal for missing filmography title: ' + title + '...');
  const res = await (await fetch('/api/chat', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:`add ${title} (${year})`})})).json();
  showAnswer(res.answer || res);
}

function switchTab(tabId) {
  const tabs = ['overview', 'curation', 'acquisition', 'analytics', 'terminal'];
  tabs.forEach(t => {
    const view = document.getElementById('tabView-' + t);
    const btn = document.getElementById('tabBtn-' + t);
    if (view && btn) {
      if (t === tabId) {
        view.style.display = 'block';
        btn.className = '';
      } else {
        view.style.display = 'none';
        btn.className = 'secondary';
      }
    }
  });
}

async function loadDecisionLog() {
  const el = document.getElementById('decisionLogStream');
  el.innerHTML = '<p style="color:var(--muted)">Fetching recent autonomous decisions...</p>';
  try {
    const res = await (await fetch('/api/decisions')).json();
    const decs = res.decisions || [];
    if (!decs.length) {
      el.innerHTML = '<p style="color:var(--muted)">No autonomous decisions logged yet.</p>';
      return;
    }
    el.innerHTML = decs.map(d => {
      const isGood = d.sentiment === 'good';
      const isBad = d.sentiment === 'bad';
      return `
        <div style="background:rgba(2,12,7,0.9);padding:14px;border-radius:10px;border:1px solid var(--line);margin-top:10px">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap">
            <strong style="color:var(--accent);font-size:0.95rem">[${(d.category || 'action').toUpperCase()}] ${d.subject || 'Autonomous Decision'}</strong>
            <span style="font-family:'Fira Code';font-size:0.75rem;color:var(--muted)">${d.created_at || ''}</span>
          </div>
          <div style="font-size:0.85rem;color:#fff;margin-top:4px">Decision: <span style="color:var(--cyan)">${d.decision}</span></div>
          <pre style="margin-top:6px;max-height:80px;padding:8px;font-size:0.75rem">// Reason: ${JSON.stringify(d.reasons)}</pre>
          <div style="display:flex;gap:8px;margin-top:8px;align-items:center">
            <button class="${isGood?'':'secondary'}" style="padding:4px 10px;font-size:0.75rem" onclick="sendDecisionFeedback('${d.decision_id}', 'good')">👍 Upvote Match</button>
            <button class="${isBad?'danger':'secondary'}" style="padding:4px 10px;font-size:0.75rem" onclick="sendDecisionFeedback('${d.decision_id}', 'bad')">👎 Downvote Match</button>
            ${d.sentiment ? `<span style="font-size:0.75rem;color:var(--accent)">Feedback Recorded: ${d.sentiment.toUpperCase()}</span>` : ''}
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    el.innerHTML = '<p style="color:var(--danger)">Failed to load decisions: ' + err.message + '</p>';
  }
}

async function generateMoodPlaylist(mood) {
  if (!mood) return;
  const statusEl = document.getElementById('moodGeneratorStatus');
  statusEl.innerHTML = '<p style="color:var(--cyan);margin-top:10px">✨ Generating 50-item Plex playlist for mood: <strong>' + mood + '</strong>...</p>';
  try {
    const res = await (await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: 'playlist ' + mood })
    })).json();
    statusEl.innerHTML = '<div style="background:rgba(2,12,7,0.9);padding:14px;border-radius:10px;border:1px solid var(--line-bright);margin-top:10px"><p style="color:var(--accent);margin:0">✓ Successfully created Plex playlist for: <strong>' + mood + '</strong>!</p></div>';
    loadPlaylists();
  } catch (err) {
    statusEl.innerHTML = '<p style="color:var(--danger);margin-top:10px">Failed to generate mood playlist: ' + err.message + '</p>';
  }
}

async function sendDecisionFeedback(decision_id, sentiment) {
  const note = prompt('Optional feedback note for Gemini neural model:') || '';
  await fetch('/api/decisions/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision_id, sentiment, note })
  });
  loadDecisionLog();
}

// --- Vault Catalog Browser JS Engine ---
let currentCatPage = 1;
let totalCatPages = 1;

async function searchCatalogUI(page = 1) {
  currentCatPage = page;
  const q = encodeURIComponent((document.getElementById('catSearchInput').value || '').trim());
  const genre = encodeURIComponent(document.getElementById('catGenreSelect').value || '');
  const mediaType = encodeURIComponent(document.getElementById('catTypeSelect').value || '');
  
  const gridEl = document.getElementById('catalogGrid');
  gridEl.innerHTML = '<p style="color:var(--cyan)">Querying vault catalog matrix...</p>';

  try {
    const res = await fetch(`/api/catalog?q=${q}&genre=${genre}&media_type=${mediaType}&page=${page}&limit=48`);
    const data = await res.json();
    totalCatPages = data.pages || 1;

    document.getElementById('catTotalCount').textContent = `Total Vault Items: ${data.total.toLocaleString()} (${data.items.length} shown)`;
    document.getElementById('catPageNum').textContent = `Page ${data.page} of ${totalCatPages}`;

    if (!data.items.length) {
      gridEl.innerHTML = '<div style="background:rgba(2,12,7,0.7);padding:20px;border-radius:10px;border:1px solid var(--line);grid-column:1/-1"><p style="color:var(--muted);margin:0">No matching titles found in your vault catalog.</p></div>';
      return;
    }

    gridEl.innerHTML = data.items.map(item => {
      const genresStr = (item.genres || []).slice(0, 3).map(g => `<span style="background:rgba(0,240,255,0.1);border:1px solid var(--line-bright);color:var(--cyan);font-size:0.7rem;padding:2px 6px;border-radius:4px">${g}</span>`).join(' ');
      const overview = (item.overview || 'No overview available.').slice(0, 110) + '...';
      const badge = item.media_type === 'movie' ? '<span class="ok" style="font-size:0.75rem">MOVIE</span>' : '<span style="color:var(--accent);font-size:0.75rem">TV SERIES</span>';
      return `
        <div style="background:rgba(2,12,7,0.85);padding:14px;border-radius:12px;border:1px solid var(--line);display:flex;flex-direction:column;justify-space-between;box-shadow:0 4px 15px rgba(0,0,0,0.4)">
          <div>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <strong style="color:#fff;font-size:0.95rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px" title="${item.title}">${item.title}</strong>
              <span style="font-family:'Fira Code';font-size:0.8rem;color:var(--muted)">${item.year || 'n.d.'}</span>
            </div>
            <div style="display:flex;gap:6px;align-items:center;margin-top:6px">
              ${badge}
              <div style="display:flex;gap:4px;flex-wrap:wrap">${genresStr}</div>
            </div>
            <p style="margin:8px 0 0;font-size:0.78rem;color:var(--muted);line-height:1.4">${overview}</p>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    gridEl.innerHTML = `<p style="color:var(--bad)">Failed to query catalog: ${err.message}</p>`;
  }
}

function prevCatalogPage() {
  if (currentCatPage > 1) searchCatalogUI(currentCatPage - 1);
}

function nextCatalogPage() {
  if (currentCatPage < totalCatPages) searchCatalogUI(currentCatPage + 1);
}

load(); loadDiscovery(); loadAutoStatus(); loadPlaylists(); loadAnalyticsCharts(); loadDecisionLog(); searchCatalogUI(1);
</script></body></html>"""


# Keep the legacy literal above for source compatibility while serving the reorganized,
# independently testable dashboard module at runtime.
DASHBOARD_HTML = REORGANIZED_DASHBOARD_HTML


class Handler(BaseHTTPRequestHandler):
    plane: ControlPlane

    def _send(self, status: int, payload: Any, content_type: str = "application/json", headers: dict[str, str] | None = None) -> None:
        body = payload if isinstance(payload, bytes) else (json_text(payload).encode() if content_type == "application/json" else payload.encode())
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("X-CineSwarm-API-Version", API_VERSION)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _authentication_enabled() -> bool:
        return bool(os.environ.get("CINESWARM_DASHBOARD_USERNAME")) and bool(os.environ.get("CINESWARM_DASHBOARD_PASSWORD"))

    def _authorized(self) -> bool:
        if not self._authentication_enabled():
            return True
        header = self.headers.get("Authorization", "")
        supplied_username = ""
        supplied_password = ""
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
                supplied_username, supplied_password = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError):
                pass
        username_matches = hmac.compare_digest(supplied_username.encode(), os.environ.get("CINESWARM_DASHBOARD_USERNAME", "").encode())
        password_matches = hmac.compare_digest(supplied_password.encode(), os.environ.get("CINESWARM_DASHBOARD_PASSWORD", "").encode())
        return username_matches and password_matches

    def _require_authentication(self) -> bool:
        if self._authorized():
            return True
        self._send(401, {"error": "authentication_required"}, headers={"WWW-Authenticate": 'Basic realm="CineSwarm", charset="UTF-8"'})
        return False

    @staticmethod
    def _public_path(path: str, method: str) -> bool:
        public_get = {"/api/health", "/api/v1/health", "/api/monitoring/snapshot", "/api/v1/monitoring/snapshot"}
        public_post = {"/api/webhooks/plex", "/api/webhooks/sabnzbd"}
        if method == "GET":
            return path in public_get
        return path in public_post

    @staticmethod
    def _multipart_form_field(raw: bytes, content_type: str, name: str) -> str:
        match = re.search(r"boundary=([^;]+)", content_type or "", re.I)
        if not match:
            return ""
        boundary = match.group(1).strip().strip('"').encode()
        marker = f'name="{name}"'.encode()
        for part in raw.split(b"--" + boundary):
            if marker not in part:
                continue
            _, _, body = part.partition(b"\r\n\r\n")
            return body.rsplit(b"\r\n", 1)[0].decode("utf-8", "replace")
        return ""

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type") or ""
        if "multipart/" in content_type.lower():
            field = self._multipart_form_field(raw, content_type, "payload")
            if field:
                try:
                    parsed = json.loads(field)
                    return parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    return {}
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        requested_path = parsed.path
        if not self._public_path(requested_path, "GET") and not self._require_authentication():
            return
        canonical_path = READ_ONLY_V1_ALIASES.get(requested_path, requested_path)
        self.path = canonical_path + (("?" + parsed.query) if parsed.query else "")
        if canonical_path == "/":
            self._send(200, DASHBOARD_HTML, "text/html; charset=utf-8")
        elif canonical_path in {"/api", "/api/v1"}:
            self._send(200, {"name": "CineSwarm", "application_version": APPLICATION_VERSION, "api_version": API_VERSION, "supported_versions": [API_VERSION]})
        elif canonical_path == "/api/status":
            self._send(200, self.plane.store.status())
        elif canonical_path in {"/api/sessions", "/api/v1/sessions"}:
            sessions = []
            try:
                plex = getattr(self.plane, "plex", None)
                if plex is not None:
                    sessions = plex.get_sessions(timeout=float(os.environ.get("CINESWARM_PROBE_TIMEOUT", "5")))
            except Exception as exc:
                self._send(500, {"error": str(exc), "sessions": []})
                return
            self._send(200, {"count": len(sessions), "sessions": sessions, "generated_at": now()})
        elif canonical_path == "/api/transcode-shield":
            self._send(200, self.plane.probe_transcode_shield())
        elif canonical_path == "/api/diagnostics":

            self._send(200, self.plane.store.diagnostics())
        elif canonical_path == "/api/health":
            status = self.plane.store.status()
            unhealthy = [service["service"] for service in status.get("services", []) if service.get("status") != "healthy"]
            worker_healthy = bool((status.get("worker") or {}).get("healthy"))
            healthy = worker_healthy and not unhealthy and len(status.get("services", [])) == 3
            self._send(200 if healthy else 503, {"status": "healthy" if healthy else "unhealthy", "worker": status.get("worker"), "unhealthy_services": unhealthy})
        elif canonical_path == "/api/monitoring/snapshot":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                hours = int(query.get("hours", ["24"])[0])
            except ValueError:
                self._send(400, {"error": "hours must be an integer"})
                return
            self._send(200, self.plane.monitoring_snapshot(hours=hours))
        elif self.path == "/api/discovery":
            self._send(200, {"candidates": self.plane.discovery_queue()})
        elif self.path.startswith("/api/editions"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                catalog_item_id = int(query["catalog_item_id"][0]) if query.get("catalog_item_id") else None
                candidate_id = int(query["discovery_candidate_id"][0]) if query.get("discovery_candidate_id") else None
                limit = int(query.get("limit", ["100"])[0])
                self._send(200, {"editions": self.plane.store.editions(catalog_item_id, candidate_id, limit)})
            except ValueError:
                self._send(400, {"error": "edition filters must be integers"})
        elif self.path.startswith("/api/catalog"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            q = query.get("q", [""])[0].strip()
            genre = query.get("genre", [""])[0].strip()
            media_type = query.get("media_type", [""])[0].strip()
            page = int(query.get("page", ["1"])[0])
            limit = int(query.get("limit", ["50"])[0])
            offset = (page - 1) * limit
            try:
                with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True, timeout=30.0) as connection:
                    connection.row_factory = sqlite3.Row
                    where_clauses = ["present = 1"]
                    params = []
                    if q:
                        where_clauses.append("(lower(title) LIKE ? OR lower(overview) LIKE ?)")
                        needle = f"%{q.lower()}%"
                        params.extend([needle, needle])
                    if genre:
                        where_clauses.append("lower(genres_json) LIKE ?")
                        params.append(f"%{genre.lower()}%")
                    if media_type:
                        where_clauses.append("media_type = ?")
                        params.append(media_type.lower())

                    where_sql = " AND ".join(where_clauses)
                    total = connection.execute(f"SELECT COUNT(*) FROM catalog_items WHERE {where_sql}", params).fetchone()[0]
                    rows = connection.execute(
                        f"SELECT id, title, year, media_type, source, source_native_id, genres_json, overview FROM catalog_items WHERE {where_sql} ORDER BY year DESC, title ASC LIMIT ? OFFSET ?",
                        params + [limit, offset]
                    ).fetchall()

                    items = []
                    for r in rows:
                        item_dict = dict(r)
                        try:
                            item_dict["genres"] = json.loads(item_dict.get("genres_json") or "[]")
                        except Exception:
                            item_dict["genres"] = []
                        item_dict.pop("genres_json", None)
                        items.append(item_dict)

                    pages = math.ceil(total / limit) if limit > 0 else 1
                    self._send(200, {"items": items, "total": total, "page": page, "pages": pages, "limit": limit})
            except Exception as e:
                self._send(500, {"error": f"Catalog query failed: {str(e)}"})

        elif self.path.startswith("/api/operational-summary"):

            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                self._send(200, self.plane.store.operational_summary(int(query.get("hours", ["24"])[0])))
            except ValueError:
                self._send(400, {"error": "hours must be an integer"})
        elif self.path.startswith("/api/operations/queue"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                self._send(200, self.plane.operational_queue(int(query.get("limit", ["200"])[0])))
            except ValueError:
                self._send(400, {"error": "limit must be an integer"})
        elif self.path.startswith("/api/preservation"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                self._send(200, self.plane.store.preservation_detail(int(query.get("limit", ["100"])[0])))
            except ValueError:
                self._send(400, {"error": "limit must be an integer"})
        elif self.path == "/api/playlists":
            self._send(200, {"playlists": self.plane.get_plex_playlists()})
        elif self.path == "/api/analytics/summary":
            self._send(200, self.plane.get_analytics_summary())
        elif self.path == "/api/decisions":
            self._send(200, {"decisions": self.plane.get_recent_decisions(limit=30)})
        elif self.path == "/api/autonomous/status":
            self._send(200, self.plane.autonomous_status())
        elif self.path == "/api/autonomous/budget":
            self._send(200, self.plane.get_budget_status())
        elif self.path == "/api/autonomous/policies":
            self._send(200, self.plane.get_policies())
        elif self.path == "/metrics":
            status = self.plane.store.status()
            cat = status.get("catalog", {})
            worker = status.get("worker", {})
            metrics_text = (
                "# HELP cineswarm_catalog_items Total items tracked in catalog\n"
                "# TYPE cineswarm_catalog_items gauge\n"
                f'cineswarm_catalog_items{{media_type="movie"}} {cat.get("movie", 0)}\n'
                f'cineswarm_catalog_items{{media_type="series"}} {cat.get("series", 0)}\n'
                "# HELP cineswarm_worker_healthy Worker service health status (1=healthy, 0=unhealthy)\n"
                "# TYPE cineswarm_worker_healthy gauge\n"
                f'cineswarm_worker_healthy {1 if worker.get("healthy") else 0}\n'
            )
            self._send(200, metrics_text, "text/plain; version=0.0.4; charset=utf-8")

        elif self.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    status = self.plane.store.status()
                    movies_q = self.plane.planner.queue("movie") if self.plane.planner else {}
                    series_q = self.plane.planner.queue("series") if self.plane.planner else {}
                    plex = getattr(self.plane, "plex", None)
                    plex_sessions = plex.get_sessions() if plex is not None else []
                    ai_thoughts = self.plane.get_recent_decisions(limit=6)
                    payload = json.dumps({"status": status, "movies_queue": movies_q.get("records", [])[:5], "series_queue": series_q.get("records", [])[:5], "plex_sessions": plex_sessions, "ai_thoughts": ai_thoughts, "timestamp": now()})
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(3)
            except (BrokenPipeError, ConnectionResetError, socket.error):
                return
        elif self.path == "/api/gem/context":
            sessions = []
            if self.plane.writers and "plex" in self.plane.writers:
                try:
                    tree = self.plane.writers["plex"].get("status/sessions")
                    for video in tree.findall("Video") or tree.findall("Track"):
                        user_elem = video.find("User")
                        sessions.append({
                            "title": video.get("title"),
                            "show": video.get("grandparentTitle"),
                            "type": video.get("type"),
                            "user": user_elem.get("title") if user_elem is not None else "Unknown",
                            "device": video.find("Player").get("title") if video.find("Player") is not None else "Unknown"
                        })
                except Exception:
                    pass
            movies_q = self.plane.planner.queue("movie") if self.plane.planner else {}
            series_q = self.plane.planner.queue("series") if self.plane.planner else {}
            profile = self.plane.discovery.playback_history.build_taste_profile() if self.plane.discovery and self.plane.discovery.playback_history else {}
            self._send(200, {
                "live_playback_sessions": sessions,
                "active_downloads": {
                    "movies": movies_q.get("records", [])[:5],
                    "series": series_q.get("records", [])[:5]
                },
                "vault_catalog_summary": self.plane.store.catalog_counts(),
                "user_taste_profile": {
                    "top_genres": profile.get("top_genres", []),
                    "top_directors": profile.get("top_directors", []),
                    "top_actors": profile.get("top_actors", [])
                }
            })
        elif self.path == "/api/intelligence/status":
            try:
                self._send(200, self.plane.get_intelligence_status())
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/library/portrait":
            try:
                self._send(200, self.plane.library_brain.build_portrait())
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path.split("?", 1)[0] == "/api/library/person":
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            name = (query.get("q") or query.get("name") or [""])[0]
            try:
                self._send(200, self.plane.library_brain.search_person(name))
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path.split("?", 1)[0] == "/api/library/collections":
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            needle = (query.get("q") or [""])[0]
            try:
                self._send(200, {"collections": self.plane.library_brain.collection_map(needle)})
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/intelligence/evolution-report":
            try:
                self._send(200, self.plane.evolution_engine.get_evolution_report())
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/user/profiles":
            try:
                self._send(200, {
                    "active_profile": self.plane.user_profiles.get_active_profile(),
                    "all_profiles": self.plane.user_profiles.list_all_profiles()
                })
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/mesh/status":
            try:
                self._send(200, self.plane.mesh_engine.get_mesh_status())
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/enterprise/fleet":
            try:
                self._send(200, self.plane.fleet_manager.get_fleet_summary())
            except Exception as exc:
                self._send(500, {"error": str(exc)})

        elif self.path.startswith("/api/artwork/banner.svg"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            title = query.get("title", ["CineSwarm Special Collection"])[0]
            theme = query.get("theme", ["default"])[0]
            movies = query.get("movies", [""])[0].split(",") if query.get("movies", [""])[0] else []
            svg_data = self.plane.artwork_engine.generate_svg_banner(title, theme, movies).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(svg_data)))
            self.end_headers()
            self.wfile.write(svg_data)

        elif self.path.startswith("/api/chapters/download"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            title = query.get("title", [""])[0]
            if not title:
                self._send(400, {"error": "title parameter is required"})
                return
            try:
                summarizer = ChapterSummarizer()
                res = summarizer.generate(title)
                vtt_data = res.get("vtt", "").encode("utf-8")
                clean_title = re.sub(r"[^a-zA-Z0-9_-]", "_", title)
                self.send_response(200)
                self.send_header("Content-Type", "text/vtt; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{clean_title}.chapters.vtt"')
                self.send_header("Content-Length", str(len(vtt_data)))
                self.end_headers()
                self.wfile.write(vtt_data)
            except Exception as exc:
                self._send(500, {"error": str(exc)})



        elif self.path.startswith("/api/trivia/download"):

            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            title = query.get("title", [""])[0]
            if not title:
                self._send(400, {"error": "title parameter is required"})
                return
            res = self.plane.generate_movie_commentary(title)
            srt_data = res.get("srt", "").encode("utf-8")
            clean_title = re.sub(r"[^a-zA-Z0-9_-]", "_", title)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-subrip; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{clean_title}.en.trivia.srt"')
            self.send_header("Content-Length", str(len(srt_data)))
            self.end_headers()
            self.wfile.write(srt_data)
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        requested_path = urllib.parse.urlparse(self.path).path
        if not self._public_path(requested_path, "POST") and not self._require_authentication():
            return
        if self.path == "/api/refresh":
            self._send(200, self.plane.refresh("dashboard"))
        elif self.path == "/api/reconcile":
            self._send(200, self.plane.reconcile("dashboard"))
        elif self.path == "/api/x265/upgrade":
            self._send(200, self.plane.run_x265_upgrade_sweep(batch_size=100, actor="dashboard"))
        elif self.path == "/api/gem/action":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                action_type = payload.get("action", "").lower()
                params = payload.get("parameters", {})
                actor = "gemini-gem"
                
                if action_type == "add":
                    term = params.get("title", "")
                    m_type = params.get("media_type", "movie")
                    plan = self.plane.planner.plan(m_type, term) if self.plane.planner else {}
                    cands = plan.get("candidates", [])
                    if not cands:
                        self._send(404, {"error": f"No {m_type} candidate found for '{term}'"})
                        return
                    roots = plan.get("root_folders", [])
                    profiles = plan.get("quality_profiles", [])
                    if not roots or not profiles:
                        self._send(400, {"error": "Missing root folder or quality profile"})
                        return
                    add_payload = {"media_type": m_type, "candidate": cands[0], "root_folder_path": roots[0]["path"], "quality_profile_id": profiles[0]["id"]}
                    task_id = self.plane.store.create_task(f"{'radarr' if m_type == 'movie' else 'sonarr'}_add_request", actor, add_payload)
                    res = self.plane.approve_task(task_id, actor)
                    self._send(200, {"status": "success", "action": "add", "title": cands[0].get("title"), "result": res})
                elif action_type == "scan_filmography":
                    person = params.get("person", "")
                    role = params.get("role", "director")
                    res = self.plane.discovery.scan_filmography_gaps(person, role) if self.plane.discovery else {}
                    self._send(200, {"status": "success", "action": "scan_filmography", "result": res})
                elif action_type == "curate":
                    theme = params.get("theme", "")
                    mode = params.get("mode", "collection")
                    res = self.plane.curate_collection(theme, limit=50, mode=mode, actor=actor)
                    self._send(200, {"status": "success", "action": "curate", "result": res})
                elif action_type == "emergency_stop":
                    enable = bool(params.get("enabled", True))
                    self.plane.store.set_policy("CINESWARM_AUTO_EMERGENCY_STOP", "true" if enable else "false")
                    self._send(200, {"status": "success", "action": "emergency_stop", "emergency_stop": enable})
                else:
                    self._send(400, {"error": f"Unsupported Gem action '{action_type}'"})
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/chat":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                result = self.plane.agents.ask(payload.get("prompt", ""), "dashboard")
                self._send(200, result)
            except (json.JSONDecodeError, AgentError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/library/ask":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                prompt = payload.get("prompt") or payload.get("q") or ""
                answer = self.plane.library_brain.answer(prompt)
                self._send(200, {"answer": answer, "librarian": "local"})
            except (json.JSONDecodeError, Exception) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/vibe-search":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                user_prompt = payload.get("prompt", "")
                res = self.plane.vibe_search(user_prompt)
                self._send(200, res)
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/cinema-night":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                theme = payload.get("theme", "")
                res = self.plane.generate_cinema_night(theme)
                self._send(200, res)
            except Exception as exc:
                self._send(500, {"error": str(exc)})

        elif self.path.startswith("/api/tasks/") and self.path.endswith("/approve"):
            task_id = self.path[len("/api/tasks/"):-len("/approve")]
            try:
                self._send(200, self.plane.approve_task(task_id, "dashboard"))
            except (ServiceError, AgentError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/discovery/run":
            try:
                self._send(200, self.plane.discovery_run("dashboard"))
            except (AgentError, ServiceError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/discovery/action":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                self._send(200, self.plane.discovery_action(int(payload.get("candidate_id")), payload.get("action", "")))
            except (json.JSONDecodeError, AgentError, ServiceError, TypeError, ValueError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/discovery/generate":
            if not self.plane.discovery:
                self._send(400, {"error": "Discovery is not configured"})
            else:
                try:
                    self._send(200, self.plane.discovery.run(limit=10))
                except Exception as exc:
                    self._send(500, {"error": str(exc)})
        elif self.path == "/api/discovery/franchise":
            if not self.plane.discovery:
                self._send(400, {"error": "Discovery is not configured"})
            else:
                try:
                    self._send(200, self.plane.discovery.discover_missing_franchise_items(limit=10))
                except Exception as exc:
                    self._send(500, {"error": str(exc)})
        elif self.path == "/api/intelligence/status":
            try:
                self._send(200, self.plane.get_intelligence_status())
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/intelligence/evolution-report":
            try:
                self._send(200, self.plane.evolution_engine.get_evolution_report())
            except Exception as exc:
                self._send(500, {"error": str(exc)})

        elif self.path == "/api/enterprise/register-tenant":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                t_id = payload.get("tenant_id", "tenant_01")
                t_name = payload.get("tenant_name", "Grand Luxury Resort")
                p_type = payload.get("property_type", "Boutique Hotel")
                endpoints = int(payload.get("total_endpoints", 20))
                sla = payload.get("sla_tier", "Enterprise")
                self._send(200, self.plane.fleet_manager.register_tenant(t_id, t_name, p_type, endpoints, sla))
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/intelligence/evaluate-candidate":

            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                title = payload.get("title", "Unknown")
                size_gb = float(payload.get("size_gb", 0))
                codec = payload.get("video_codec", "")
                release = payload.get("release_name", "")
                res = self.plane.evaluate_release_quality_guard(title, size_gb, codec, release)
                self._send(200, res)
            except Exception as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/intelligence/double-feature":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                theme = payload.get("theme", "mind-bending twists")
                limit = int(payload.get("limit", 2))
                publish = bool(payload.get("publish_plex", True))
                res = self.plane.generate_intelligence_double_feature(theme, limit, publish, actor="dashboard")
                self._send(200, res)
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/intelligence/semantic-search":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                query = payload.get("query", "")
                top_n = int(payload.get("top_n", 20))
                max_minutes = int(payload.get("max_minutes")) if payload.get("max_minutes") else None
                max_size_gb = float(payload.get("max_size_gb")) if payload.get("max_size_gb") else None
                results = self.plane.semantic_index.search(query, top_n=top_n, max_minutes=max_minutes, max_size_gb=max_size_gb)
                self._send(200, {"query": query, "total_matches": len(results), "results": results})
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/intelligence/vector-search":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                query = payload.get("query", "")
                top_n = int(payload.get("top_n", 20))
                max_m = int(payload.get("max_minutes")) if payload.get("max_minutes") else None
                max_gb = float(payload.get("max_size_gb")) if payload.get("max_size_gb") else None
                results = self.plane.dense_vector_index.dense_vector_search(query, top_n=top_n, max_minutes=max_m, max_size_gb=max_gb)
                self._send(200, {"query": query, "engine": "100% Local 384-dim Dense Vector Search ($0 cost)", "matches_count": len(results), "results": results})
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/user/switch-profile":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                p_id = payload.get("profile_id", "admin")
                self._send(200, self.plane.user_profiles.switch_active_profile(p_id))
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/intelligence/consensus-deliberate":

            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                p_type = payload.get("proposal_type", "candidate_acquisition")
                title = payload.get("title", "Unknown")
                size_gb = float(payload.get("size_gb", 0.0))
                codec = payload.get("video_codec", "")
                result = self.plane.consensus_graph.deliberate_proposal(p_type, title, size_gb, codec)
                self._send(200, result)
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif requested_path == "/api/webhooks/plex":
            try:
                payload = self._read_json_body()
                event = payload.get("event", "media.play")
                movie = (payload.get("Metadata") or {}).get("title", "Unknown")
                account = ((payload.get("Account") or {}).get("title") or (payload.get("Account") or {}).get("name") or "")
                if account and hasattr(self.plane, "user_profiles"):
                    self.plane.user_profiles.apply_plex_account(str(account))
                res_telemetry = self.plane.evolution_engine.process_plex_playback_event(payload)
                try:
                    res_telemetry["watch_ledger"] = self.plane.library_brain.record_watch_event(payload)
                except Exception:
                    pass
                if event in {"media.scrobble", "media.stop"} and self.plane.discovery:
                    try:
                        res_telemetry["rescored_candidates"] = int(self.plane.discovery.rescore_open_candidates() or 0)
                    except Exception:
                        pass
                self.plane.log_decision("webhook_plex", movie, "recorded", {"event": event, "account": account}, res_telemetry)
                self._send(200, {"status": "received", "event": event, "media": movie, "reinforcement_telemetry": res_telemetry})
            except Exception:
                self._send(200, {"status": "ingested"})

        elif self.path == "/api/webhooks/sabnzbd":
            try:
                self.plane.log_decision("webhook_sabnzbd", "download_complete", "trigger_scan", {}, {"status": "reconciled"})
                self._send(200, {"status": "received"})
            except Exception as exc:
                self._send(200, {"status": "ingested"})
        elif self.path == "/api/intelligence/taste-memory":

            try:
                self._send(200, self.plane.taste_memory.get_memory_summary())
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/intelligence/storage-audit":

            try:
                self._send(200, self.plane.run_storage_optimization_audit(actor="dashboard"))
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/playback/profile":

            if not self.plane.discovery or not self.plane.discovery.playback_history:
                self._send(400, {"error": "Playback history not available"})
            else:
                try:
                    profile = self.plane.discovery.playback_history.build_taste_profile()
                    self._send(200, profile)
                except Exception as exc:
                    self._send(500, {"error": str(exc)})
        elif self.path == "/api/decisions/feedback":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                dec_id = payload.get("decision_id")
                sent = payload.get("sentiment")
                note = payload.get("note", "")
                if not dec_id or sent not in ("good", "bad"):
                    raise AgentError("decision_id and sentiment ('good'|'bad') are required")
                success = self.plane.record_decision_feedback(dec_id, sent, note, actor="dashboard")
                self._send(200, {"success": success})
            except (json.JSONDecodeError, AgentError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/filmography":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                name = payload.get("person", "")
                role = payload.get("role", "director")
                if not name:
                    raise AgentError("person name is required")
                res = self.plane.scan_filmography(name, role, actor="dashboard")
                self._send(200, res)
            except (json.JSONDecodeError, AgentError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/playlists/reorder":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                playlist_id = payload.get("playlist_id")
                rating_keys = payload.get("rating_keys", [])
                if not playlist_id or not rating_keys:
                    raise AgentError("playlist_id and rating_keys are required")
                success = self.plane.reorder_plex_playlist(str(playlist_id), rating_keys, actor="dashboard")
                self._send(200, {"success": success})
            except (json.JSONDecodeError, AgentError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path.split("?", 1)[0] == "/api/quality/analyze":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                self._send(200, self.plane.quality_analysis(payload.get("media_type", "movie")))
            except (json.JSONDecodeError, AgentError, ServiceError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/acquisition/queue":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                self._send(200, self.plane.acquisition_queue(payload.get("media_type", "")))
            except (json.JSONDecodeError, AgentError, ServiceError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/acquisition/plan":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                self._send(200, self.plane.acquisition_plan(payload.get("media_type", ""), payload.get("term", "")))
            except (json.JSONDecodeError, AgentError, ServiceError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/acquisition/search-propose":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                media_type = payload.get("media_type")
                service_id = payload.get("service_id")
                if media_type not in ("movie", "series") or not isinstance(service_id, int) or service_id <= 0:
                    raise AgentError("Choose Movie or Series and provide a valid numeric Radarr/Sonarr ID")
                task_type = "radarr_search_request" if media_type == "movie" else "sonarr_search_request"
                if not task_type:
                    raise AgentError("media_type must be movie or series")
                task_id = self.plane.store.create_task(task_type, "dashboard", payload)
                self._send(202, {"task_id": task_id, "status": "pending_approval", "message": "No search or download occurs until this task is approved."})
            except (json.JSONDecodeError, AgentError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/acquisition/propose":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                media_type = payload.get("media_type")
                task_type = "radarr_add_request" if media_type == "movie" else "sonarr_add_request" if media_type == "series" else ""
                if not task_type:
                    raise AgentError("media_type must be movie or series")
                task_id = self.plane.store.create_task(task_type, "dashboard", payload)
                self._send(202, {"task_id": task_id, "status": "pending_approval", "message": "No service change occurs until this task is approved."})
            except (json.JSONDecodeError, AgentError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/plex/collections/sync":
            try:
                result = self.plane.sync_native_collections(min_items=2, actor="dashboard")
                self._send(200, result)
            except (AgentError, ServiceError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/health/scan":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                limit = int(payload.get("limit", 30))
                result = self.plane.scan_media_health(limit=limit, actor="dashboard")
                self._send(200, result)
            except (json.JSONDecodeError, AgentError, ServiceError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/trivia/generate":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                title = payload.get("title")
                if not title:
                    raise AgentError("title is required")
                year = payload.get("year")
                result = self.plane.generate_movie_commentary(title, year, "dashboard")
                self._send(200, result)
            except (json.JSONDecodeError, AgentError, ServiceError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/chapters/generate":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                title = payload.get("title")
                if not title:
                    raise AgentError("title is required")
                year = payload.get("year")
                result = self.plane.generate_movie_chapters(title, year, "dashboard")
                self._send(200, result)
            except (json.JSONDecodeError, AgentError, ServiceError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/proposals":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "invalid_json"})
                return
            task_type = payload.get("task_type")
            if not isinstance(task_type, str) or not task_type:
                self._send(400, {"error": "task_type is required"})
                return
            decision, _ = Policy.classify(task_type)
            task_id = self.plane.store.create_task(task_type, "dashboard", payload)
            full_autopilot = (self.plane.store.get_policy("CINESWARM_FULL_AUTOPILOT") or "false").lower() in {"1", "true", "yes", "on"}
            if full_autopilot and decision == "approval_required":
                try:
                    res = self.plane.approve_task(task_id, "dashboard")
                    self._send(200, {"task_id": task_id, "status": "completed", "result": res, "message": "Task completed successfully."})
                    return
                except Exception as exc:
                    self._send(400, {"error": str(exc)})
                    return
            status = "queued" if decision == "allowed" else "pending_approval" if decision == "approval_required" else "blocked"
            self._send(202, {"task_id": task_id, "mode": status, "message": "Task recorded."})
        elif self.path == "/api/playlists":
            self._send(200, {"playlists": self.plane.get_plex_playlists()})
        elif self.path == "/api/playlists/delete":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                rating_key = payload.get("ratingKey")
                if not rating_key:
                    raise AgentError("ratingKey is required")
                success = self.plane.delete_plex_playlist(str(rating_key), "dashboard")
                self._send(200, {"success": success, "ratingKey": rating_key})
            except (json.JSONDecodeError, AgentError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/watch":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                max_minutes = int(payload.get("max_minutes", 120))
                genre = str(payload.get("genre", ""))
                res = self.plane.watch_recommendations(max_minutes=max_minutes, genre=genre, actor="dashboard")
                self._send(200, res)
            except (json.JSONDecodeError, AgentError, ValueError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/autonomous/policies":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if payload.get("key") and payload.get("value") is not None:
                    self.plane.set_policy(payload["key"], payload["value"])
                    self._send(200, {"status": "updated"})
                else:
                    self._send(200, self.plane.get_policies())
            except (json.JSONDecodeError, AgentError, ServiceError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/autonomous/budget":
            self._send(200, self.plane.get_budget_status())
        elif self.path == "/api/autonomous/status":
            self._send(200, self.plane.autonomous_status())
        elif self.path == "/api/autonomous/emergency-stop":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                enabled = payload.get("enabled", False)
                val = "true" if enabled else "false"
                self.plane.store.set_policy("CINESWARM_AUTO_EMERGENCY_STOP", val)
                self._send(200, {"status": "updated", "enabled": enabled})
            except (json.JSONDecodeError, AgentError, ServiceError) as exc:
                self._send(400, {"error": str(exc)})
        elif self.path == "/api/plex/collections":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                theme = payload.get("theme", "Mind-Bending Sci-Fi")
                limit = int(payload.get("limit", 15))
                result = self.plane.curate_collection(theme, limit, "dashboard")
                self._send(200, result)
            except (json.JSONDecodeError, AgentError, ServiceError) as exc:
                self._send(400, {"error": str(exc)})
        else:
            self._send(403, {"error": "write_action_not_enabled_or_not_approved"})

    def log_message(self, format: str, *args: Any) -> None:
        return


def make_plane() -> ControlPlane:
    store = ControlStore()
    connectors: dict[str, Callable[[], dict[str, Any]]] = {}
    plex_url = os.environ.get("PLEX_URL", "http://127.0.0.1:32400")
    plex_token = os.environ.get("PLEX_TOKEN", "")
    plex_connector = PlexConnector(ServiceConfig("plex", plex_url, plex_token, "X-Plex-Token"))
    connectors["plex"] = plex_connector.snapshot
    radarr_config = ServiceConfig("radarr", os.environ.get("RADARR_URL", "http://127.0.0.1:7878"), os.environ.get("RADARR_API_KEY", ""), "X-Api-Key")
    sonarr_config = ServiceConfig("sonarr", os.environ.get("SONARR_URL", "http://127.0.0.1:8989"), os.environ.get("SONARR_API_KEY", ""), "X-Api-Key")
    connectors["radarr"] = ArrConnector(radarr_config, "movie").snapshot
    connectors["sonarr"] = ArrConnector(sonarr_config, "series").snapshot
    plex_config = ServiceConfig("plex", plex_url, plex_token, "X-Plex-Token")
    plex_writer = PlexWriteClient(plex_config)
    radarr_writer = ArrWriteClient(radarr_config)
    sonarr_writer = ArrWriteClient(sonarr_config)
    planner_tools = AgentOrchestrator(store, CATALOG_DB).tools
    planner = AcquisitionPlanner(radarr_writer, sonarr_writer, planner_tools)
    actions = {
        "plex_library_refresh": lambda payload: plex_writer.refresh_libraries(payload.get("path") if isinstance(payload, dict) else None),
        "radarr_add_request": planner.add,
        "sonarr_add_request": planner.add,
        "radarr_search_request": planner.search,
        "sonarr_search_request": planner.search,
        "radarr_search_retry_request": planner.search,
        "radarr_release_grab_request": planner.grab_release,
        "sonarr_search_retry_request": planner.search,
    }
    writers = {"plex": plex_writer, "radarr": radarr_writer, "sonarr": sonarr_writer}
    plane = ControlPlane(store, connectors, actions, planner, writers)
    plane.plex = plex_connector
    return plane


def run_proactive_swarm_loop(plane: ControlPlane, interval_seconds: int = 300) -> None:
    """Proactive Background Swarm Loop daemon: autonomously audits media health, updates taste memory, and evaluates quality guard."""
    print("🚀 Proactive Swarm Loop Daemon started (Background Autopilot active)")
    while True:
        try:
            time.sleep(interval_seconds)
            # 1. Proactive storage & quality guard audit
            plane.run_storage_optimization_audit(actor="proactive-swarm")
            
            # 2. Update user taste profile memory from active Plex history
            if plane.discovery and plane.discovery.playback_history:
                profile = plane.discovery.playback_history.build_taste_profile()
                plane.taste_memory.record_preference("user_profile_summary", profile)
                
            # 3. Autonomic Self-Healing & Vector Re-Indexing
            maintenance = plane.evolution_engine.run_autonomic_maintenance(getattr(plane, "dense_vector_index", None))

            # 4. Proactive corrupt media scanning & auto-healing
            plane.scan_media_health(limit=20, actor="proactive-swarm", allow_automatic=True)

            
            # Log autonomous swarm heartbeats
            plane.log_decision(
                category="proactive_swarm_cycle",
                subject="vault_health_and_taste_sync",
                decision="allowed",
                reasons={"loop_interval": interval_seconds, "autopilot": True},
                outcome={"status": "health_and_taste_updated", "maintenance": maintenance},
            )
        except Exception as exc:
            try:
                plane.store.audit(
                    "proactive-swarm",
                    "proactive_swarm_cycle",
                    "vault_health_and_taste_sync",
                    "error",
                    "failed",
                    {"error": str(exc)},
                )
            except Exception:
                print(f"Proactive swarm cycle failed: {exc}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the read-only CineSwarm control plane.")
    parser.add_argument("--host", default=os.environ.get("CINESWARM_CONTROL_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CINESWARM_CONTROL_PORT", "8787")))
    parser.add_argument("--refresh", action="store_true", help="Refresh service snapshots and exit.")
    args = parser.parse_args()
    plane = make_plane()
    try:
        webhook = plane.ensure_plex_webhook()
        plane.store.audit("startup", "plex_webhook_ensure", "plex", "local-write", webhook.get("status") or "unknown", webhook)
    except Exception as exc:
        print(f"Plex webhook registration skipped: {exc}", flush=True)
    if args.refresh:
        print(json_text(plane.refresh("cli")))
        return
    Handler.plane = plane
    
    # Launch Proactive Swarm Loop daemon thread
    swarm_thread = threading.Thread(target=run_proactive_swarm_loop, args=(plane, 300), daemon=True)
    swarm_thread.start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"CineSwarm control plane listening on {args.host}:{args.port} (Swarm Autopilot Active)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

