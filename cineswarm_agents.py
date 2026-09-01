#!/usr/bin/env python3
"""Hosted-model orchestration and safe read-only CineSwarm agent tools."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Callable


class AgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentRole:
    name: str
    purpose: str
    system_prompt: str


ROLES = {
    "librarian": AgentRole(
        "librarian",
        "Reconcile and explain the state of Plex, Radarr, Sonarr, and the durable catalog.",
        "You are the CineSwarm Chief Librarian. You maintain absolute precision across Plex playback history, Radarr movies, Sonarr series, and local SQLite catalogs. Answer with exact numbers, provider IDs (TMDB/TVDB/IMDB), and edition statuses."
    ),
    "projectionist": AgentRole(
        "projectionist",
        "Analyze collection taste, recommend media, and design collections or playlists.",
        "You are CineSwarm's Master Projectionist & Cult Curator. You combine deep knowledge of midnight cinema, genre classics, director filmographies, and user taste history to curate stunning Plex collections, playlists, and movie recommendations."
    ),
    "sentinel": AgentRole(
        "sentinel",
        "Identify service health, video corruption, storage limits, and mount integrity problems.",
        "You are the CineSwarm Sentinel. You inspect video stream headers (ffprobe/ffmpeg), verify disk storage space, monitor storage pool mount points, and trigger auto-healing repair tasks for unreadable files."
    ),
    "scout": AgentRole(
        "scout",
        "Scan filmography gaps, missing franchise entries, and candidate recommendations.",
        "You are the CineSwarm Discovery Scout. You analyze director and actor filmographies, track missing franchise sequels/prequels, and score candidate media using taste affinity metrics."
    ),
    "upgrader": AgentRole(
        "upgrader",
        "Analyze media codecs, resolution profiles, and missing subtitle/audio tracks.",
        "You are the CineSwarm Quality Upgrader. You identify low-resolution or outdated video codecs (x264 720p), locate missing English subtitle/audio tracks, and propose replacement upgrades."
    ),
    "archivist": AgentRole(
        "archivist",
        "Maintain metadata integrity, edition tags, poster art, and catalog consistency.",
        "You are the CineSwarm Archivist. You verify edition labels (Director's Cut, Extended, Unrated), poster artwork, release years, and external IDs across all catalog items."
    ),
    "annotator": AgentRole(
        "annotator",
        "Generate AI director trivia commentary and chapter marker overlays.",
        "You are the CineSwarm Master Annotator. You generate timed trivia subtitle overlays (.en.trivia.srt) and scene chapter markers (.en.chapters.vtt) to transform vault movies into interactive criterion-edition experiences."
    ),
    "marshal": AgentRole(
        "marshal",
        "Enforce security policy, download budgets, emergency stop, and rate limiting.",
        "You are the CineSwarm Security Marshal. You strictly enforce emergency-stop status, weekly download bandwidth limits, storage safety floors (500 GB free), and Basic Auth identity verification."
    ),
}



class HostedModelClient:
    def __init__(self) -> None:
        self.provider = os.environ.get("CINESWARM_MODEL_PROVIDER", "gemini").lower()
        self.api_key = (
            os.environ.get("CINESWARM_MODEL_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or ""
        )
        self.model = os.environ.get("CINESWARM_MODEL_NAME") or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
        self.base_url = os.environ.get("CINESWARM_MODEL_BASE_URL", "").rstrip("/")
        if not self.base_url and self.provider == "gemini":
            self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        if not self.base_url:
            self.base_url = "https://api.openai.com/v1"
        self.timeout = float(os.environ.get("CINESWARM_MODEL_TIMEOUT", "180"))


    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def complete(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        if not self.configured:
            raise AgentError("Hosted model is not configured; set CINESWARM_MODEL_API_KEY, GEMINI_API_KEY, or GOOGLE_API_KEY in .env")
        body = json.dumps({"model": self.model, "messages": messages, "temperature": temperature}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AgentError(f"Hosted model returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AgentError(f"Hosted model request failed: {exc.__class__.__name__}") from exc
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentError("Hosted model returned an invalid completion") from exc


class PlaybackHistory:
    def __init__(self, plex_url: str, token: str, timeout: float = 30.0):
        self.url = plex_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> ET.Element:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = self.url + "/" + path.lstrip("/")
        if query:
            url += "?" + query
        request = urllib.request.Request(url, method="GET")
        if self.token:
            request.add_header("X-Plex-Token", self.token)
        request.add_header("Accept", "application/xml")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return ET.fromstring(response.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
            raise AgentError(f"Plex playback history request failed: {exc.__class__.__name__}") from exc

    def get_watched_items(self, limit: int = 200) -> ET.Element:
        return self._get("library/all", {"sort": "lastViewedAt:desc", "viewCount>>": 0, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": limit, "includeGuids": 1})

    def build_taste_profile(self) -> dict[str, Any]:
        # Get watched items
        watched = self.get_watched_items(limit=500)
        genre_weights = Counter()
        decade_weights = Counter()
        director_weights = Counter()
        actor_weights = Counter()
        studio_weights = Counter()
        year_distribution = Counter()
        recent_activity = []

        for item in watched:
            if item.tag not in ("Video", "Directory") or item.get("type") not in ("movie", "show"):
                continue
            play_count = int(item.get("viewCount") or 1)

            # Weight by play count and recency
            weight = play_count
            last_viewed_at = int(item.get("lastViewedAt") or 0)
            last_played = datetime.fromtimestamp(last_viewed_at, timezone.utc).isoformat() if last_viewed_at else None
            if last_viewed_at:
                days_ago = (datetime.now(timezone.utc) - datetime.fromtimestamp(last_viewed_at, timezone.utc)).days
                if days_ago < 30:
                    weight *= 2.0  # Recent watches weigh more
                elif days_ago < 90:
                    weight *= 1.5

            # Genres
            for genre in item.findall("Genre"):
                if genre.get("tag"):
                    genre_weights[genre.get("tag")] += weight

            # Decades
            year = int(item.get("year")) if str(item.get("year", "")).isdigit() else None
            if year:
                decade = f"{(year // 10) * 10}s"
                decade_weights[decade] += weight

            # Directors
            for director in item.findall("Director"):
                if director.get("tag"):
                    director_weights[director.get("tag")] += weight

            # Actors (from People field if available)
            for person in item.findall("Role"):
                if person.get("tag"):
                    actor_weights[person.get("tag")] += weight

            # Studios
            if item.get("studio"):
                studio_weights[item.get("studio")] += weight

            # Year
            if year:
                year_distribution[year] += weight

            recent_activity.append({
                "title": item.get("title"),
                "type": "Movie" if item.get("type") == "movie" else "Series",
                "year": year,
                "play_count": play_count,
                "last_played": last_played,
                "rating": item.get("userRating"),
                "weight": weight,
            })

        return {
            "source": "plex",
            "top_genres": genre_weights.most_common(20),
            "top_decades": decade_weights.most_common(10),
            "top_directors": director_weights.most_common(10),
            "top_actors": actor_weights.most_common(15),
            "top_studios": studio_weights.most_common(10),
            "year_distribution": year_distribution.most_common(15),
            "total_watched": len(recent_activity),
            "total_plays": sum(r["play_count"] for r in recent_activity),
            "recent_activity": sorted(recent_activity, key=lambda x: x["weight"], reverse=True)[:20],
        }


class ReadOnlyTools:
    def __init__(self, store: Any, catalog_db: str) -> None:
        self.store = store
        self.catalog_db = catalog_db

    @staticmethod
    def _path_exists(path: str | None) -> bool:
        if not path:
            return False
        candidates = [path]
        mappings = os.environ.get("CINESWARM_PATH_MAP", "/media=/mnt/media,/data=/mnt/media")
        for mapping in mappings.split(","):
            if "=" not in mapping:
                continue
            source, target = mapping.split("=", 1)
            source, target = source.rstrip("/"), target.rstrip("/")
            if path == source or path.startswith(source + "/"):
                candidates.append(target + path[len(source):])
        return any(os.path.exists(candidate) for candidate in candidates)

    def status(self) -> dict[str, Any]:
        return self.store.status()

    def search_catalog(self, query: str = "", media_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 50))
        clauses = ["present=1"]
        params: list[Any] = []
        if media_type in ("movie", "series"):
            clauses.append("media_type=?")
            params.append(media_type)
        if query.strip():
            clauses.append("(lower(title) LIKE ? OR lower(genres_json) LIKE ? OR CAST(year AS TEXT) LIKE ?)")
            needle = f"%{query.strip().lower()}%"
            params.extend([needle, needle, needle])
        if not os.path.exists(self.catalog_db):
            return []
        try:
            with sqlite3.connect(f"file:{self.catalog_db}?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    f"SELECT media_type, source, source_id, source_native_id, title, year, genres_json, monitored, status FROM catalog_items WHERE {' AND '.join(clauses)} ORDER BY title LIMIT ?",
                    (*params, limit),
                ).fetchall()
        except sqlite3.Error:
            return []
        results = []
        for row in rows:
            item = dict(row)
            item["genres"] = json.loads(item.pop("genres_json") or "[]")
            results.append(item)
        return results

    def cached_reconciliation(self) -> dict[str, Any]:
        details = self.store.latest_audit_details("reconcile_library", "plex-radarr-sonarr", "complete")
        if not details:
            return {"status": "not_available", "message": "Run Reconcile library to create a current report."}
        return {"status": "cached", **details}

    def snapshot_summary(self) -> dict[str, Any]:
        status = self.store.status()
        return {
            "catalog": status.get("catalog", {}),
            "services": [
                {
                    "service": service["service"],
                    "status": service["status"],
                    "item_count": service["item_count"],
                    "fetched_at": service["fetched_at"],
                    "error": service["error"],
                }
                for service in status.get("services", [])
            ],
        }

    @staticmethod
    def _ids(item: dict[str, Any], media_type: str) -> set[tuple[str, str]]:
        if media_type == "movie":
            values = {"tmdb": item.get("tmdbId")}
        else:
            values = {"tvdb": item.get("tvdbId"), "tmdb": item.get("tmdbId")}
        return {(key, str(value)) for key, value in values.items() if value not in (None, 0, "0", "")}

    @staticmethod
    def _edition_key(item: dict[str, Any], media_type: str) -> set[tuple[str, str]]:
        """Generate a composite key that includes edition/quality information for duplicate detection."""
        base_ids = ReadOnlyTools._ids(item, media_type)
        edition = ReadOnlyTools._extract_edition(item)
        quality = ReadOnlyTools._extract_quality(item)
        if edition or quality:
            # Add edition/quality to the key to differentiate cuts/editions
            edition_str = f"edition:{edition}|quality:{quality}" if edition and quality else f"edition:{edition or quality}"
            return base_ids | {("edition_key", edition_str)}
        return base_ids

    @staticmethod
    def _extract_edition(item: dict[str, Any]) -> str | None:
        """Extract edition/cut information from title or edition field."""
        title = item.get("title", "") or item.get("Title", "") or ""
        edition_field = item.get("edition", "") or item.get("Edition", "") or item.get("EditionName", "") or ""
        
        # Common edition patterns
        edition_patterns = [
            r"\b(director['\s]?s?\s+cut)\b",
            r"\b(extended\s+(?:edition|cut))\b",
            r"\b(theatrical\s+(?:edition|cut))\b",
            r"\b(remastered)\b",
            r"\b(anniversary\s+edition)\b",
            r"\b(collector['\s]?s?\s+edition)\b",
            r"\b(special\s+edition)\b",
            r"\b(ultimate\s+edition)\b",
            r"\b(uncut)\b",
            r"\b(unrated)\b",
            r"\b(4k|uhd)\b",
            r"\b(1080p)\b",
            r"\b(720p)\b",
            r"\b(hdr)\b",
            r"\b(dolby\s+vision)\b",
        ]
        
        import re
        found = []
        for pattern in edition_patterns:
            matches = re.findall(pattern, title, re.IGNORECASE)
            if matches:
                found.extend(matches)
        if edition_field:
            found.append(edition_field)
        
        if found:
            # Return first unique match
            for f in found:
                if f:
                    return f.strip()
        return None

    @staticmethod
    def _extract_quality(item: dict[str, Any]) -> str | None:
        """Extract quality/resolution information from the item."""
        quality = item.get("Quality", {}) or {}
        quality_name = quality.get("quality", {}).get("name", "") if isinstance(quality.get("quality"), dict) else ""
        
        # Also check movieFile quality
        movie_file = item.get("movieFile", {}) or {}
        mf_quality = movie_file.get("quality", {}) or {}
        mf_quality_name = mf_quality.get("quality", {}).get("name", "") if isinstance(mf_quality.get("quality"), dict) else ""
        
        return quality_name or mf_quality_name or None


    @staticmethod
    def _plex_ids(item: dict[str, Any], media_type: str) -> set[tuple[str, str]]:
        provider_ids = item.get("ProviderIds") or {}
        normalized = {str(key).lower().replace("-", ""): value for key, value in provider_ids.items()}
        values = {"tmdb": normalized.get("tmdb"), "tvdb": normalized.get("tvdb")}
        if media_type == "movie":
            values.pop("tvdb", None)
        return {(key, str(value)) for key, value in values.items() if value not in (None, 0, "0", "")}

    def reconcile(self) -> dict[str, Any]:
        snapshots = {name: self.store.snapshot_payload(name) for name in ("plex", "radarr", "sonarr")}
        unavailable = [name for name, payload in snapshots.items() if payload is None]
        if unavailable:
            return {"status": "not_ready", "unavailable_services": unavailable, "message": "Refresh all services before reconciling."}

        plex_items = snapshots["plex"].get("items", [])
        plex_index = {
            media_type: {
                identifier
                for item in plex_items
                if item.get("Type") == plex_type
                for identifier in self._plex_ids(item, media_type)
            }
            for media_type, plex_type in (("movie", "Movie"), ("series", "Series"))
        }
        report: dict[str, Any] = {
            "status": "complete",
            "snapshots": {name: snapshots[name].get("_fetched_at") for name in snapshots},
            "movies": {"managed": 0, "indexed": 0, "without_file": 0, "file_not_indexed": 0, "path_missing": 0, "unknown": 0, "examples": []},
            "series": {"managed": 0, "indexed": 0, "not_indexed": 0, "path_missing": 0, "unknown": 0, "examples": []},
            "plex_unmanaged": {"movies": 0, "series": 0, "without_provider_ids": {"movies": 0, "series": 0}},
        }
        matched = {"movie": set(), "series": set()}

        for item in snapshots["radarr"].get("items", []):
            report["movies"]["managed"] += 1
            identifiers = self._edition_key(item, "movie")
            matched["movie"].update(identifiers & plex_index["movie"])
            title = item.get("title") or "Untitled movie"
            has_file = bool(item.get("hasFile"))
            movie_file = item.get("movieFile") or {}
            path = movie_file.get("path") or item.get("path")
            path_exists = self._path_exists(path)
            if identifiers & plex_index["movie"]:
                report["movies"]["indexed"] += 1
                continue
            if not has_file:
                category = "without_file"
            elif path and not path_exists:
                category = "path_missing"
            elif has_file and path_exists:
                category = "file_not_indexed"
            else:
                category = "unknown"
            report["movies"][category] += 1
            if len(report["movies"]["examples"]) < 25:
                report["movies"]["examples"].append({"category": category, "title": title, "year": item.get("year"), "path": path})

        for item in snapshots["sonarr"].get("items", []):
            report["series"]["managed"] += 1
            identifiers = self._edition_key(item, "series")
            matched["series"].update(identifiers & plex_index["series"])
            title = item.get("title") or "Untitled series"
            path = item.get("path")
            path_exists = self._path_exists(path)
            statistics = item.get("statistics") or {}
            if identifiers & plex_index["series"]:
                report["series"]["indexed"] += 1
            elif path and not path_exists:
                report["series"]["path_missing"] += 1
                if len(report["series"]["examples"]) < 25:
                    report["series"]["examples"].append({"category": "path_missing", "title": title, "year": item.get("year"), "path": path})
            elif identifiers:
                report["series"]["not_indexed"] += 1
                if len(report["series"]["examples"]) < 25:
                    report["series"]["examples"].append({"category": "not_indexed", "title": title, "year": item.get("year"), "path": path, "episode_files": statistics.get("episodeFileCount"), "episodes": statistics.get("episodeCount")})
            else:
                report["series"]["unknown"] += 1

        report["plex_unmanaged"]["movies"] = sum(1 for item in plex_items if item.get("Type") == "Movie" and not (self._plex_ids(item, "movie") & matched["movie"]))
        report["plex_unmanaged"]["series"] = sum(1 for item in plex_items if item.get("Type") == "Series" and not (self._plex_ids(item, "series") & matched["series"]))
        report["plex_unmanaged"]["without_provider_ids"]["movies"] = sum(1 for item in plex_items if item.get("Type") == "Movie" and not self._plex_ids(item, "movie"))
        report["plex_unmanaged"]["without_provider_ids"]["series"] = sum(1 for item in plex_items if item.get("Type") == "Series" and not self._plex_ids(item, "series"))
        return report


class AcquisitionPlanner:
    def __init__(self, radarr: Any, sonarr: Any, tools: ReadOnlyTools):
        self.radarr = radarr
        self.sonarr = sonarr
        self.tools = tools

    def plan(self, media_type: str, term: str) -> dict[str, Any]:
        if media_type not in ("movie", "series"):
            raise AgentError("media_type must be movie or series")
        if not term.strip():
            raise AgentError("A title or search term is required")
        client = self.radarr if media_type == "movie" else self.sonarr
        lookup_path = "api/v3/movie/lookup" if media_type == "movie" else "api/v3/series/lookup"
        results = client.get(lookup_path, {"term": term.strip()})
        candidates = results if isinstance(results, list) else []
        existing = self.tools.search_catalog(term, media_type, 10)
        roots = client.get("api/v3/rootfolder")
        profiles = client.get("api/v3/qualityprofile")
        return {
            "media_type": media_type,
            "term": term.strip(),
            "existing_matches": existing,
            "candidates": [self._candidate(item, media_type) for item in candidates[:20]],
            "root_folders": [{"id": item.get("id"), "path": item.get("path"), "free_space": item.get("freeSpace")} for item in (roots if isinstance(roots, list) else [])],
            "quality_profiles": [{"id": item.get("id"), "name": item.get("name")} for item in (profiles if isinstance(profiles, list) else [])],
            "write_action": "radarr_add_request" if media_type == "movie" else "sonarr_add_request",
            "warning": "Planning is read-only. Selecting a candidate creates an approval task; no request or download occurs yet.",
        }

    @staticmethod
    def _candidate(item: dict[str, Any], media_type: str) -> dict[str, Any]:
        fields = ("tmdbId", "imdbId", "tvdbId", "title", "year", "overview", "genres", "status", "titleSlug", "seriesType", "seasons")
        return {key: item.get(key) for key in fields if key in item}

    def monitor_search(self, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        media_type = payload.get("media_type")
        service_id = payload.get("service_id")
        command_id = result.get("id")
        if media_type not in ("movie", "series") or not service_id or not command_id:
            return {"state": "invalid_search_result", "media_type": media_type, "service_id": service_id, "command_id": command_id}
        client = self.radarr if media_type == "movie" else self.sonarr
        command = client.get(f"api/v3/command/{int(command_id)}")
        item_path = f"api/v3/movie/{int(service_id)}" if media_type == "movie" else f"api/v3/series/{int(service_id)}"
        item = client.get(item_path)
        include_unknown_key = "includeUnknownMovieItems" if media_type == "movie" else "includeUnknownSeriesItems"
        queue = client.get("api/v3/queue", {"page": 1, "pageSize": 100, include_unknown_key: "true"})
        service_id_key = "movieId" if media_type == "movie" else "seriesId"
        queue_item = next((record for record in queue.get("records", []) if record.get(service_id_key) == service_id), None)
        failed_statuses = {"failed", "warning", "importFailed", "importFailedPathDoesNotExist"}
        if command.get("status") not in ("completed", "failed"):
            state = "search_running"
        elif command.get("status") == "failed":
            state = "search_failed"
        elif media_type == "movie" and item.get("hasFile"):
            state = "imported"
        elif queue_item and queue_item.get("status") in failed_statuses:
            state = "download_failed"
        elif queue_item:
            state = "download_active"
        elif media_type == "movie":
            state = "search_completed_no_file"
        else:
            stats = item.get("statistics") or {}
            if stats.get("episodeFileCount", 0) > 0:
                state = "episodes_imported_partial" if stats.get("episodeFileCount") < stats.get("episodeCount", 0) else "imported"
            else:
                state = "search_completed_no_episodes"
        return {"state": state, "media_type": media_type, "service_id": service_id, "command_id": command_id, "command_status": command.get("status"), "command_result": command.get("result"), "title": item.get("title"), "has_file": item.get("hasFile"), "statistics": item.get("statistics"), "queue_status": queue_item.get("status") if queue_item else None, "tracked_download_state": queue_item.get("trackedDownloadState") if queue_item else None}

    def get_failed_downloads(self, media_type: str = "movie") -> list[dict[str, Any]]:
        """Get list of items with failed downloads from queue."""
        if media_type not in ("movie", "series"):
            raise AgentError("media_type must be movie or series")
        client = self.radarr if media_type == "movie" else self.sonarr
        include_unknown_key = "includeUnknownMovieItems" if media_type == "movie" else "includeUnknownSeriesItems"
        result = client.get("api/v3/queue", {"page": 1, "pageSize": 100, include_unknown_key: "true"})
        records = result.get("records", []) if isinstance(result, dict) else []
        failed = []
        service_id_key = "movieId" if media_type == "movie" else "seriesId"
        for item in records:
            if item.get("status") in ("failed", "warning", "importFailed", "importFailedPathDoesNotExist"):
                service_id = item.get(service_id_key)
                if not service_id:
                    continue
                failed.append({
                    "media_type": media_type,
                    "service_id": service_id,
                    "queue_id": item.get("id"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "tracked_download_state": item.get("trackedDownloadState"),
                    "error_message": item.get("errorMessage"),
                    "quality_profile_id": item.get("qualityProfileId"),
                    "quality": item.get("quality", {}).get("quality", {}).get("name"),
                })
        return failed

    def retry_with_quality_fallback(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Retry a failed download with a fallback quality profile."""
        media_type = payload.get("media_type")
        service_id = payload.get("service_id")
        current_profile_id = payload.get("quality_profile_id")
        fallback_order = payload.get("fallback_order", ["HD-1080p", "HD-720p", "Any", "SD"])
        
        if media_type not in ("movie", "series") or not service_id or not current_profile_id:
            raise AgentError("Retry payload requires media_type, service_id, and quality_profile_id")
        
        client = self.radarr if media_type == "movie" else self.sonarr
        
        # Get available quality profiles
        profiles = client.get("api/v3/qualityprofile")
        profile_map = {p.get("name"): p.get("id") for p in profiles if isinstance(p, dict) and p.get("name")}
        
        # Find next fallback profile
        next_profile_id = None
        for fallback_name in fallback_order:
            if fallback_name in profile_map and profile_map[fallback_name] != current_profile_id:
                next_profile_id = profile_map[fallback_name]
                break
        
        if not next_profile_id:
            return {"status": "no_fallback_available", "current_profile": current_profile_id}
        
        # Update the item with new quality profile
        item_path = f"api/v3/movie/{int(service_id)}" if media_type == "movie" else f"api/v3/series/{int(service_id)}"
        item = client.get(item_path)
        if not isinstance(item, dict) or not item.get("id"):
            raise AgentError("Could not retrieve item for quality profile update")
        
        # Update quality profile
        item["qualityProfileId"] = next_profile_id
        client.put_json(item_path, item)
        
        # Trigger search with new quality profile
        command = {"name": "MoviesSearch", "movieIds": [int(service_id)]} if media_type == "movie" else {"name": "SeriesSearch", "seriesId": int(service_id)}
        search_result = client.post_json("api/v3/command", command)
        
        return {
            "status": "retry_triggered",
            "previous_profile_id": current_profile_id,
            "new_profile_id": next_profile_id,
            "new_profile_name": [k for k, v in profile_map.items() if v == next_profile_id][0],
            "search_command": search_result
        }

    def quality_analysis(self, media_type: str = "movie") -> dict[str, Any]:
        if media_type not in ("movie", "series"):
            raise AgentError("media_type must be movie or series")
        service = "radarr" if media_type == "movie" else "sonarr"
        snapshot = self.tools.store.snapshot_payload(service)
        if not snapshot:
            raise AgentError(f"No current {service} snapshot; refresh services first")
        items = snapshot.get("items", [])
        client = self.radarr if media_type == "movie" else self.sonarr
        profiles = client.get("api/v3/qualityprofile")
        profile_map = {item.get("id"): item for item in profiles if isinstance(item, dict)}
        if media_type == "series":
            incomplete = []
            complete = 0
            for item in items:
                stats = item.get("statistics") or {}
                if stats.get("episodeCount", 0) and stats.get("episodeFileCount", 0) >= stats.get("episodeCount", 0):
                    complete += 1
                else:
                    incomplete.append({"service_id": item.get("id"), "title": item.get("title"), "episode_files": stats.get("episodeFileCount"), "episodes": stats.get("episodeCount"), "percent": stats.get("percentOfEpisodes")})
            return {"media_type": media_type, "managed": len(items), "complete": complete, "incomplete": len(incomplete), "examples": incomplete[:50], "note": "Series quality is represented through Sonarr episode statistics; no files are changed."}
        quality_counts = {}
        upgrade_candidates = []
        missing = 0
        for item in items:
            if not item.get("hasFile"):
                missing += 1
                continue
            movie_file = item.get("movieFile") or {}
            quality = movie_file.get("quality", {}).get("quality", {})
            name = quality.get("name") or "unknown"
            quality_counts[name] = quality_counts.get(name, 0) + 1
            profile = profile_map.get(item.get("qualityProfileId"), {})
            cutoff_id = profile.get("cutoff")
            cutoff_name = next((entry.get("quality", {}).get("name") for entry in (profile.get("items", []) or []) if entry.get("quality", {}).get("id") == cutoff_id), None)
            if cutoff_id and quality.get("id") and quality.get("id") != cutoff_id and profile.get("upgradeAllowed", True) and str(profile.get("name", "")).lower() != "any":
                upgrade_candidates.append({"service_id": item.get("id"), "title": item.get("title"), "year": item.get("year"), "current_quality": name, "cutoff_quality": cutoff_name, "quality_profile": profile.get("name")})
        return {"media_type": media_type, "snapshot_fetched_at": snapshot.get("_fetched_at"), "managed": len(items), "with_files": len(items) - missing, "without_files": missing, "quality_distribution": quality_counts, "upgrade_candidate_count": len(upgrade_candidates), "upgrade_candidates": upgrade_candidates[:100], "note": "Upgrade candidates are advisory. Profiles named Any are not treated as upgrade policies. No searches, replacements, or deletions occur during analysis."}

    def queue(self, media_type: str) -> dict[str, Any]:
        if media_type not in ("movie", "series"):
            raise AgentError("media_type must be movie or series")
        client = self.radarr if media_type == "movie" else self.sonarr
        include_unknown_key = "includeUnknownMovieItems" if media_type == "movie" else "includeUnknownSeriesItems"
        result = client.get("api/v3/queue", {"page": 1, "pageSize": 1000, include_unknown_key: "true"})
        records = result.get("records", []) if isinstance(result, dict) else []
        return {
            "media_type": media_type,
            "total_records": result.get("totalRecords", len(records)) if isinstance(result, dict) else len(records),
            "records": [{"id": item.get("id"), "download_id": item.get("downloadId"), "title": item.get("title"), "status": item.get("status"), "tracked_download_state": item.get("trackedDownloadState"), "error_message": item.get("errorMessage")} for item in records],
        }

    def global_queue_pressure(self, limit: int) -> dict[str, Any]:
        failed_statuses = {"failed", "warning", "importfailed", "importfailedpathdoesnotexist"}
        sources = {"radarr": 0, "sonarr": 0, "sabnzbd": 0}
        unique: dict[str, dict[str, Any]] = {}
        for media_type, source in (("movie", "radarr"), ("series", "sonarr")):
            for record in self.queue(media_type)["records"]:
                if str(record.get("status") or "").lower() in failed_statuses:
                    continue
                download_id = record.get("download_id")
                key = f"download:{download_id}" if download_id else f"{source}:{record.get('id')}"
                sources[source] += 1
                unique.setdefault(key, {"source": source, **record})
        sab_url = os.environ.get("SABNZBD_URL", "").rstrip("/")
        sab_key = os.environ.get("SABNZBD_API_KEY", "")
        sab_configured = bool(sab_url and sab_key)
        sab_error = None
        if sab_configured:
            query = urllib.parse.urlencode({"mode": "queue", "output": "json", "apikey": sab_key})
            try:
                request = urllib.request.Request(f"{sab_url}/api?{query}", headers={"Accept": "application/json"}, method="GET")
                with urllib.request.urlopen(request, timeout=float(os.environ.get("CINESWARM_API_TIMEOUT", "60"))) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                slots = payload.get("queue", {}).get("slots", []) if isinstance(payload, dict) else []
                for slot in slots:
                    download_id = slot.get("nzo_id")
                    key = f"download:{download_id}" if download_id else f"sabnzbd:{slot.get('filename')}"
                    sources["sabnzbd"] += 1
                    unique.setdefault(key, {"source": "sabnzbd", "download_id": download_id, "title": slot.get("filename"), "status": slot.get("status")})
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                sab_error = exc.__class__.__name__
        active = len(unique)
        return {"status": "pressured" if active >= limit else "available", "pressured": active >= limit, "active": active, "limit": limit, "sources": sources, "records": list(unique.values()), "sabnzbd": {"configured": sab_configured, "error": sab_error}}

    def grab_release(self, payload: dict[str, Any]) -> dict[str, Any]:
        movie_id = payload.get("movie_id")
        guid = payload.get("guid")
        indexer_id = payload.get("indexer_id")
        expected_title = payload.get("title")
        if not movie_id or not guid or not indexer_id or not expected_title:
            raise AgentError("Release grab requires movie_id, guid, indexer_id, and title")
        movie = self.radarr.get(f"api/v3/movie/{int(movie_id)}")
        if not isinstance(movie, dict) or movie.get("id") != int(movie_id):
            raise AgentError("The selected Radarr movie could not be revalidated")
        queue = self.radarr.get("api/v3/queue", {"page": 1, "pageSize": 100, "includeUnknownMovieItems": "true"})
        if any(item.get("movieId") == int(movie_id) and item.get("title", "").casefold() == expected_title.casefold() for item in queue.get("records", [])):
            raise AgentError("That exact release is already in the Radarr queue")
        releases = self.radarr.get("api/v3/release", {"movieId": int(movie_id)})
        release = next((item for item in releases if item.get("guid") == guid and item.get("indexerId") == int(indexer_id) and item.get("title", "").casefold() == expected_title.casefold()), None)
        if not release:
            raise AgentError("The selected release is no longer available from the expected indexer")
        result = self.radarr.post_json("api/v3/release", {"guid": guid, "indexerId": int(indexer_id)})
        return {"movie_id": int(movie_id), "title": release.get("title"), "indexer": release.get("indexer"), "size": release.get("size"), "rejections": release.get("rejections") or [], "radarr_result": result}

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        media_type = payload.get("media_type")
        item_id = payload.get("service_id")
        if media_type not in ("movie", "series") or not item_id:
            raise AgentError("Search payload requires media_type and service_id")
        client = self.radarr if media_type == "movie" else self.sonarr
        item_path = f"api/v3/movie/{int(item_id)}" if media_type == "movie" else f"api/v3/series/{int(item_id)}"
        item = client.get(item_path)
        if not isinstance(item, dict) or not item.get("id"):
            raise AgentError("Selected service item could not be revalidated")
        command = {"name": "MoviesSearch", "movieIds": [int(item_id)]} if media_type == "movie" else {"name": "SeriesSearch", "seriesId": int(item_id)}
        return client.post_json("api/v3/command", command)

    def add(self, payload: dict[str, Any]) -> dict[str, Any]:
        media_type = payload.get("media_type")
        candidate = payload.get("candidate") or {}
        root_path = payload.get("root_folder_path")
        quality_profile_id = payload.get("quality_profile_id")
        if media_type not in ("movie", "series") or not candidate or not root_path or not quality_profile_id:
            raise AgentError("Acquisition payload requires media_type, candidate, root_folder_path, and quality_profile_id")
        existing = self.tools.search_catalog(candidate.get("title", ""), media_type, 50)
        candidate_ids = {f"{key}:{value}" for key, value in self.tools._edition_key(candidate, media_type) if key in ("tmdb", "tvdb")}
        extract_edition = getattr(self.tools, "_extract_edition", ReadOnlyTools._extract_edition)
        extract_quality = getattr(self.tools, "_extract_quality", ReadOnlyTools._extract_quality)
        has_distinct_edition = bool(extract_edition(candidate) or extract_quality(candidate))
        if not has_distinct_edition and any(item.get("source_id") in candidate_ids for item in existing):
            raise AgentError(f"{candidate.get('title', 'This title')} is already in the current {media_type} collection")
        client = self.radarr if media_type == "movie" else self.sonarr
        roots = client.get("api/v3/rootfolder")
        profiles = client.get("api/v3/qualityprofile")
        allowed_roots = {item.get("path") for item in roots if isinstance(item, dict)}
        allowed_profiles = {item.get("id") for item in profiles if isinstance(item, dict)}
        if root_path not in allowed_roots or int(quality_profile_id) not in allowed_profiles:
            raise AgentError("Acquisition settings are not present in the current service configuration")
        lookup_path = "api/v3/movie/lookup" if media_type == "movie" else "api/v3/series/lookup"
        lookup_term = candidate.get("title") or candidate.get("titleSlug")
        matches = client.get(lookup_path, {"term": lookup_term})
        candidate_id = candidate.get("tmdbId") if media_type == "movie" else candidate.get("tvdbId")
        id_key = "tmdbId" if media_type == "movie" else "tvdbId"
        if not any(item.get(id_key) == candidate_id for item in matches if isinstance(item, dict)):
            raise AgentError("Selected acquisition candidate was not confirmed by the service lookup")
        if media_type == "movie":
            body = {**candidate, "rootFolderPath": root_path, "qualityProfileId": int(quality_profile_id), "monitored": True, "addOptions": {"searchForMovie": False}}
            return self.radarr.post_json("api/v3/movie", body)
        body = {**candidate, "rootFolderPath": root_path, "qualityProfileId": int(quality_profile_id), "monitored": True, "addOptions": {"searchForMissingEpisodes": False}}
        return self.sonarr.post_json("api/v3/series", body)


class AgentOrchestrator:
    def __init__(self, store: Any, catalog_db: str) -> None:
        self.store = store
        self.tools = ReadOnlyTools(store, catalog_db)
        self.model = HostedModelClient()

    def select_roles(self, prompt: str) -> list[AgentRole]:
        text = prompt.lower()
        selected: list[AgentRole] = []
        if any(word in text for word in ("health", "broken", "corrupt", "unreadable", "repair", "bitrot")):
            selected.append(ROLES["sentinel"])
        if any(word in text for word in ("request", "download", "add", "get me", "wanted", "available")):
            selected.append(ROLES["scout"])
        if any(word in text for word in ("recommend", "watch", "playlist", "collection", "taste", "similar", "mood", "curate")):
            selected.append(ROLES["projectionist"])
        if any(word in text for word in ("library", "catalog", "plex", "radarr", "sonarr", "reconcile")):
            selected.append(ROLES["librarian"])
        if any(word in text for word in ("codec", "resolution", "upgrade", "1080p", "4k", "remux", "subtitle", "audio")):
            selected.append(ROLES["upgrader"])
        if any(word in text for word in ("poster", "edition", "tag", "year", "tmdb", "metadata")):
            selected.append(ROLES["archivist"])
        if any(word in text for word in ("trivia", "commentary", "chapter", "srt", "vtt")):
            selected.append(ROLES["annotator"])
        if any(word in text for word in ("security", "emergency", "stop", "budget", "auth", "limit")):
            selected.append(ROLES["marshal"])
        return selected or [ROLES["projectionist"]]


    def ask(self, prompt: str, actor: str = "dashboard") -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise AgentError("A prompt is required")
        roles = self.select_roles(prompt)
        context = self.tools.snapshot_summary()
        context["reconciliation"] = self.tools.cached_reconciliation()
        search_results = self.tools.search_catalog(prompt)
        context["relevant_catalog_matches"] = search_results
        try:
            if hasattr(self, "writers") and self.writers and "plex" in self.writers:
                tree = self.writers["plex"].get("status/sessions")
                sessions = []
                for video in tree.findall("Video") or tree.findall("Track"):
                    user_elem = video.find("User")
                    transcode = video.find("TranscodeSession")
                    sessions.append({
                        "title": video.get("title"),
                        "grandparent_title": video.get("grandparentTitle"),
                        "type": video.get("type"),
                        "user": user_elem.get("title") if user_elem is not None else "Unknown",
                        "device": video.find("Player").get("title") if video.find("Player") is not None else "Unknown",
                        "transcode": {
                            "video_decision": transcode.get("videoDecision") if transcode is not None else "direct",
                            "hw_decoding": transcode.get("transcodeHwRequested") if transcode is not None else None
                        } if transcode is not None else None
                    })
                context["live_plex_sessions"] = sessions
            else:
                context["live_plex_sessions"] = []
        except Exception:
            context["live_plex_sessions"] = []
        try:
            context["radarr_active_queue"] = self.tools.radarr.get("api/v3/queue", {"page": 1, "pageSize": 10}).get("records", [])
        except Exception:
            context["radarr_active_queue"] = []
        try:
            context["sonarr_active_queue"] = self.tools.sonarr.get("api/v3/queue", {"page": 1, "pageSize": 10}).get("records", [])
        except Exception:
            context["sonarr_active_queue"] = []

        self.store.audit(actor, "agent_request", ",".join(role.name for role in roles), "read-only", "started", {"prompt_length": len(prompt)})
        if not self.model.configured:
            message = "The agent swarm is connected to the local catalog, but the hosted model is not configured yet. Add a Gemini key as CINESWARM_MODEL_API_KEY, GEMINI_API_KEY, or GOOGLE_API_KEY in .env."
            self.store.audit(actor, "agent_request", ",".join(role.name for role in roles), "read-only", "not_configured", {})
            return {"answer": message, "roles": [role.name for role in roles], "context": context, "model_configured": False}
        role_text = "\n".join(f"- {role.name} ({role.purpose}):\n  SYSTEM PROMPT: \"{role.system_prompt}\"" for role in roles)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the CineSwarm supervisor coordinating a team of specialized AI agents. "
                    "Incorporate the precise perspectives, guidelines, and expertise of the active specialists listed below. "
                    "Answer the user's request directly using live real-time telemetry context. "
                    "You have full access to live active Plex playback streams, Radarr & Sonarr download queues, and the local catalog. "
                    "If the user asks what they are currently watching or what is downloading, report the live session telemetry. "
                    "For recommendations, provide actual titles and concise reasons. Keep movies and series distinct.\n\n"
                    f"Active Specialists & Instructions:\n{role_text}"
                ),
            },
            {"role": "user", "content": f"User request:\n{prompt}\n\nCurrent local context:\n{json.dumps(context, ensure_ascii=False)}"},
        ]

        try:
            answer = self.model.complete(messages)
        except AgentError as exc:
            self.store.audit(actor, "agent_request", ",".join(role.name for role in roles), "read-only", "error", {"prompt_length": len(prompt), "error": str(exc)})
            raise
        self.store.audit(actor, "agent_request", ",".join(role.name for role in roles), "read-only", "success", {"prompt_length": len(prompt)})
        return {"answer": answer, "roles": [role.name for role in roles], "context": context, "model_configured": True}


class TriviaCommentaryAgent:
    def __init__(self, model: HostedModelClient | None = None):
        self.model = model or HostedModelClient()

    def generate(self, title: str, year: int | None = None, runtime_mins: int = 120) -> dict[str, Any]:
        if not self.model.configured:
            raise AgentError("Gemini model is not configured")

        prompt = {
            "role": "You are a film historian, director, and master cinematographer providing a timestamped director commentary and trivia track for a film.",
            "film_title": title,
            "release_year": year,
            "approx_runtime_minutes": runtime_mins,
            "instruction": "Generate 10 to 15 timestamped director commentary and behind-the-scenes trivia markers across the film's runtime. Include filming secrets, camera techniques, director decisions, cast easter eggs, and visual lore.",
            "schema": {
                "movie_title": title,
                "director": "string",
                "tagline": "string",
                "markers": [
                    {
                        "start_seconds": 120,
                        "end_seconds": 135,
                        "timestamp": "00:02:00",
                        "speaker": "Director / Film Historian",
                        "category": "Cinematography | Trivia | Director Note | Easter Egg",
                        "commentary": "string explanation"
                    }
                ]
            }
        }

        messages = [
            {"role": "system", "content": prompt["role"]},
            {"role": "user", "content": json.dumps(prompt)}
        ]
        response = self.model.complete(messages, temperature=0.7)
        
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

        markers = parsed.get("markers", [])
        srt_content = self.export_srt(markers)
        return {
            "movie_title": parsed.get("movie_title", title),
            "director": parsed.get("director", "Unknown"),
            "tagline": parsed.get("tagline", ""),
            "marker_count": len(markers),
            "markers": markers,
            "srt": srt_content
        }

    @staticmethod
    def export_srt(markers: list[dict[str, Any]]) -> str:
        blocks = []
        for idx, m in enumerate(markers, start=1):
            start_sec = m.get("start_seconds", (idx - 1) * 300 + 30)
            end_sec = m.get("end_seconds", start_sec + 15)
            
            def fmt(seconds: int) -> str:
                hrs = seconds // 3600
                mins = (seconds % 3600) // 60
                secs = seconds % 60
                return f"{hrs:02d}:{mins:02d}:{secs:02d},000"

            speaker = m.get("speaker", "Director")
            category = m.get("category", "Trivia")
            text = m.get("commentary", "")
            blocks.append(f"{idx}\n{fmt(start_sec)} --> {fmt(end_sec)}\n[{category.upper()}] {speaker}: {text}\n")
        return "\n".join(blocks)
