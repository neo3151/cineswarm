#!/usr/bin/env python3
"""Persistent CineSwarm maintenance worker with durable jobs and retry-safe leases."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any
import urllib.request

from cineswarm_control import CATALOG_DB, ControlStore, json_text, make_plane, redact
from cineswarm_discovery import TV_GROWTH_TASTE_SEEDS, TV_GROWTH_PREFERRED_GENRES, is_boxset_title, is_junk_series_title, series_matches_tv_taste, series_titles_align
from cineswarm_preservation import configured_branches, csv_paths, database_maintenance, preservation_scan
from cineswarm_sync import sync_catalog

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTROL_DB = os.environ.get("CINESWARM_CONTROL_DB", os.path.join(BASE_DIR, "cineswarm_control.db"))
WORKER_ID = f"worker-{uuid.uuid4()}"
MAX_ATTEMPTS = int(os.environ.get("CINESWARM_WORKER_MAX_ATTEMPTS", "5"))
LEASE_SECONDS = int(os.environ.get("CINESWARM_WORKER_LEASE_SECONDS", "900"))
POLL_SECONDS = float(os.environ.get("CINESWARM_WORKER_POLL_SECONDS", "5"))

WORKER_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_jobs (
    id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    job_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    locked_by TEXT,
    locked_until REAL,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worker_jobs_due ON worker_jobs (status, available_at, locked_until);
CREATE TABLE IF NOT EXISTS worker_schedules (
    job_type TEXT PRIMARY KEY,
    interval_seconds INTEGER NOT NULL,
    next_run_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS worker_heartbeat (
    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
    worker_id TEXT NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_state (
    event_key TEXT PRIMARY KEY,
    last_sent_at REAL NOT NULL
);
"""


def utc_epoch() -> float:
    return time.time()


def sd_notify(message: str) -> bool:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
            notifier.connect(address)
            notifier.sendall(message.encode())
        return True
    except OSError:
        return False


class WorkerStore:
    def __init__(self, path: str = CONTROL_DB):
        self.path = path
        with self.connect() as connection:
            connection.executescript(WORKER_SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def ensure_schedules(self, schedules: dict[str, int]) -> None:
        timestamp = utc_epoch()
        with self.connect() as connection:
            for job_type, interval in schedules.items():
                connection.execute(
                    "INSERT OR IGNORE INTO worker_schedules (job_type, interval_seconds, next_run_at) VALUES (?, ?, ?)",
                    (job_type, interval, timestamp),
                )
                connection.execute(
                    "UPDATE worker_schedules SET interval_seconds=? WHERE job_type=?",
                    (interval, job_type),
                )

    def enqueue_due_schedules(self) -> int:
        timestamp = utc_epoch()
        created = 0
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT job_type, interval_seconds, next_run_at FROM worker_schedules WHERE enabled=1 AND next_run_at<=?",
                (timestamp,),
            ).fetchall()
            for row in rows:
                job_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO worker_jobs (job_id, job_type, status, available_at, created_at, updated_at)
                    VALUES (?, ?, 'queued', ?, ?, ?)
                    """,
                    (job_id, row["job_type"], timestamp, timestamp, timestamp),
                )
                next_run = timestamp + row["interval_seconds"]
                connection.execute(
                    "UPDATE worker_schedules SET next_run_at=? WHERE job_type=?",
                    (next_run, row["job_type"]),
                )
                created += 1
        return created

    def claim(self) -> sqlite3.Row | None:
        timestamp = utc_epoch()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM worker_jobs
                WHERE (status IN ('queued', 'retry') AND available_at<=?)
                   OR (status='running' AND locked_until<?)
                ORDER BY available_at, id LIMIT 1
                """,
                (timestamp, timestamp),
            ).fetchone()
            if not row:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE worker_jobs
                SET status='running', attempts=attempts+1, locked_by=?, locked_until=?, updated_at=?
                WHERE job_id=?
                """,
                (WORKER_ID, timestamp + LEASE_SECONDS, timestamp, row["job_id"]),
            )
            connection.commit()
            return connection.execute("SELECT * FROM worker_jobs WHERE job_id=?", (row["job_id"],)).fetchone()

    def complete(self, job_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE worker_jobs SET status='completed', locked_by=NULL, locked_until=NULL, last_error=NULL, updated_at=? WHERE job_id=?",
                (utc_epoch(), job_id),
            )

    def fail(self, job_id: str, error: str, attempts: int) -> str:
        timestamp = utc_epoch()
        if attempts >= MAX_ATTEMPTS:
            status = "failed"
            available_at = timestamp
        else:
            status = "retry"
            available_at = timestamp + min(3600, 30 * (2 ** max(0, attempts - 1)))
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE worker_jobs
                SET status=?, available_at=?, locked_by=NULL, locked_until=NULL,
                    last_error=?, updated_at=? WHERE job_id=?
                """,
                (status, available_at, error[:1000], timestamp, job_id),
            )
        return status

    def heartbeat(self, status: str, details: dict[str, Any] | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO worker_heartbeat (singleton_id, worker_id, status, details_json, updated_at) VALUES (1, ?, ?, ?, ?) ON CONFLICT(singleton_id) DO UPDATE SET worker_id=excluded.worker_id, status=excluded.status, details_json=excluded.details_json, updated_at=excluded.updated_at",
                (WORKER_ID, status, json.dumps(details or {}, ensure_ascii=False), utc_epoch()),
            )

    def notification_allowed(self, event_key: str, cooldown: int) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT last_sent_at FROM notification_state WHERE event_key=?", (event_key,)).fetchone()
        return not row or utc_epoch() - row["last_sent_at"] >= cooldown

    def mark_notification(self, event_key: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO notification_state (event_key, last_sent_at) VALUES (?, ?) ON CONFLICT(event_key) DO UPDATE SET last_sent_at=excluded.last_sent_at",
                (event_key, utc_epoch()),
            )


class Worker:
    def __init__(self, store: WorkerStore | None = None):
        self.store = store or WorkerStore()
        self.control_store = ControlStore(CONTROL_DB)
        self.plane = make_plane()
        self._startup_ready = False
        self._resume_recorded = False
        self.schedules = {
            "service_refresh": int(os.environ.get("CINESWARM_REFRESH_INTERVAL", "900")),
            "catalog_sync": int(os.environ.get("CINESWARM_CATALOG_INTERVAL", "1800")),
            "reconcile_library": int(os.environ.get("CINESWARM_RECONCILE_INTERVAL", "3600")),
            "queue_monitor": int(os.environ.get("CINESWARM_QUEUE_INTERVAL", "300")),
            "discovery_refresh": int(os.environ.get("CINESWARM_DISCOVERY_INTERVAL", "3600")),
            "franchise_refresh": int(os.environ.get("CINESWARM_FRANCHISE_INTERVAL", "14400")),
            "autonomous_acquisition": int(os.environ.get("CINESWARM_AUTONOMOUS_INTERVAL", "900")),
            "failed_download_recovery": int(os.environ.get("CINESWARM_FAILED_DOWNLOAD_INTERVAL", "900")),
            "media_health_scan": int(os.environ.get("CINESWARM_HEALTH_SCAN_INTERVAL", "43200")),
            "preservation_scan": int(os.environ.get("CINESWARM_PRESERVATION_INTERVAL", "604800")),
            "database_maintenance": int(os.environ.get("CINESWARM_DATABASE_MAINTENANCE_INTERVAL", "86400")),
            "daily_digest": int(os.environ.get("CINESWARM_DIGEST_INTERVAL", "86400")),
            "ops_pulse": int(os.environ.get("CINESWARM_OPS_PULSE_INTERVAL", "3600")),
        }

    def _policy(self, key: str, default: str = "") -> str:
        return self.control_store.get_policy(key) or os.environ.get(key, default)

    def _policy_bool(self, key: str, default: str = "false") -> bool:
        return self._policy(key, default).lower() in {"1", "true", "yes", "on"}

    def _policy_int(self, key: str, default: int) -> int:
        return int(self._policy(key, str(default)))

    @staticmethod
    def genres_blocked(genres: set[str], allowed: set[str], forbidden: set[str], soft_forbidden: set[str] | None = None) -> bool:
        """Block hard-forbidden genres; Music only blocks when no allowed genre is also present."""
        names = {str(genre).strip().lower() for genre in genres if genre}
        allowed_names = {str(genre).strip().lower() for genre in allowed if genre}
        forbidden_names = {str(genre).strip().lower() for genre in forbidden if genre}
        soft = {str(genre).strip().lower() for genre in (soft_forbidden or {"music"}) if genre}
        hard = forbidden_names - soft
        if names & hard:
            return True
        if allowed_names and not (names & allowed_names):
            return True
        if (names & forbidden_names & soft) and not (names & allowed_names):
            return True
        return False

    @staticmethod
    def runtime_minutes(raw: dict[str, Any] | None) -> float:
        value = (raw or {}).get("runtime") or (raw or {}).get("runtime_minutes") or 0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _movie_title(self, service_id: Any) -> str:
        if service_id in (None, ""):
            return ""
        try:
            with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    "SELECT title FROM catalog_items WHERE media_type='movie' AND source_native_id=? LIMIT 1",
                    (int(service_id),),
                ).fetchone()
            return str(row[0] or "") if row else ""
        except Exception:
            return ""

    def _emergency_stop(self) -> bool:
        stored = self.control_store.is_emergency_stop() if hasattr(self.control_store, "is_emergency_stop") else False
        return self._policy_bool("CINESWARM_AUTO_EMERGENCY_STOP") or stored

    def _queue_pressure(self) -> dict[str, Any]:
        limit = self._policy_int("CINESWARM_AUTO_MAX_CONCURRENT_DOWNLOADS", 4)
        if hasattr(self.plane.planner, "global_queue_pressure"):
            return self.plane.planner.global_queue_pressure(limit)
        queues = [self.plane.planner.queue(media_type) for media_type in ("movie", "series")]
        active = sum(queue.get("total_records", len(queue.get("records", []))) for queue in queues)
        return {"status": "pressured" if active >= limit else "available", "pressured": active >= limit, "active": active, "limit": limit, "sources": {"radarr": queues[0].get("total_records", 0), "sonarr": queues[1].get("total_records", 0), "sabnzbd": 0}, "records": [], "sabnzbd": {"configured": False, "error": None}}

    def _startup_readiness(self) -> dict[str, Any]:
        if getattr(self, "_startup_ready", True):
            return {"status": "ready", "ready": True}
        details: dict[str, Any] = {"services": {}, "storage": {}}
        try:
            details["services"] = self.plane.refresh("worker-readiness")
            details["storage"] = self._check_storage()
            services_ready = all(details["services"].get(name, {}).get("status") == "healthy" for name in ("plex", "radarr", "sonarr"))
            storage_ready = details["storage"].get("status") == "ready"
            ready = services_ready and storage_ready
        except Exception as exc:
            details["error"] = str(exc)
            ready = False
        details.update({"status": "ready" if ready else "waiting", "ready": ready})
        if ready:
            self._startup_ready = True
            if not getattr(self, "_resume_recorded", False):
                decision_id = self.control_store.record_decision("worker", "startup_readiness", "autonomous writes", "resumed", details, {})
                self._send_notification("startup_ready", {"decision_id": decision_id, **details}, "startup_ready", force=True)
                self._resume_recorded = True
                details["decision_id"] = decision_id
        return details

    def _record_autopilot_decision(self, decision: str, details: dict[str, Any]) -> dict[str, Any]:
        subject = str(details.get("title") or details.get("candidate_id") or "autonomous acquisition cycle")
        decision_id = self.control_store.record_decision("worker", "autonomous_acquisition", subject, decision, details, {})
        result = {"status": decision, "decision_id": decision_id, **details}
        self._send_notification("autopilot_decision", result, f"autopilot_decision:{decision_id}", force=True)
        return result

    def _near_miss_promote_recent(self, within_seconds: int | None = None) -> bool:
        """True if a near-miss promote already ran inside the cooldown window."""
        if within_seconds is None:
            within_seconds = self._policy_int("CINESWARM_AUTO_NEAR_MISS_COOLDOWN", 900)
        try:
            with sqlite3.connect(f"file:{CONTROL_DB}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    "SELECT created_at FROM decision_log WHERE category='autonomous_acquisition' AND decision='executed_near_miss_promote' ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            if not row or not row[0]:
                return False
            previous = datetime.fromisoformat(str(row[0]))
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - previous).total_seconds() < within_seconds
        except Exception:
            return False

    def _recover_never_imported(self, limit: int | None = None) -> dict[str, Any]:
        """Search a bounded number of recent Radarr movies that were added but never imported."""
        preferred = {"comedy", "animation", "action", "science fiction", "family"}
        lookback = max(14, min(self._policy_int("CINESWARM_NEVER_IMPORTED_DAYS", 180), 730))
        gaps = self.plane.never_imported_movies(added_since_days=lookback, limit=80)
        candidates = list(gaps.get("recent") or [])
        candidates.sort(
            key=lambda item: (
                bool(preferred & {str(genre).casefold() for genre in (item.get("genres") or [])}),
                str(item.get("added") or ""),
            ),
            reverse=True,
        )
        recovered = []
        exhausted = []
        max_recover = max(0, min(self._policy_int("CINESWARM_NEVER_IMPORTED_MAX_PER_CYCLE", 4), 8))
        if limit is not None:
            max_recover = max(0, min(max_recover, int(limit)))
        if max_recover == 0:
            return {"count": int(gaps.get("count") or 0), "recovered": [], "exhausted": []}
        search_cooldown = max(0, self._policy_int("CINESWARM_NEVER_IMPORTED_SEARCH_COOLDOWN", 21600))
        max_attempts = max(1, min(self._policy_int("CINESWARM_NEVER_IMPORTED_MAX_ATTEMPTS", 3), 12))
        attempt_counts = {}
        if hasattr(self.control_store, "search_attempt_counts"):
            attempt_counts = self.control_store.search_attempt_counts("radarr_search_request", "movie")
        for item in candidates:
            if is_boxset_title(item.get("title")):
                continue
            service_id = item.get("service_id")
            if not service_id or not item.get("monitored"):
                continue
            prior_attempts = int(attempt_counts.get(str(service_id), 0))
            if prior_attempts >= max_attempts:
                exhausted.append({"service_id": service_id, "title": item.get("title"), "attempts": prior_attempts})
                continue
            parent_task_id = f"never-imported:{service_id}"
            inflight = getattr(self.control_store, "task_inflight", None)
            if callable(inflight) and inflight("radarr_search_request", parent_task_id):
                continue
            if not callable(inflight) and self.control_store.task_exists("radarr_search_request", parent_task_id):
                continue
            if hasattr(self.control_store, "retry_cooldown_active") and self.control_store.retry_cooldown_active("radarr_search_request", "movie", service_id, search_cooldown):
                continue
            payload = {
                "media_type": "movie",
                "service_id": service_id,
                "parent_task_id": parent_task_id,
                "reason": "Recent Radarr add still has no imported file.",
            }
            task_id = self.control_store.create_task("radarr_search_request", "worker", payload)
            task_result = {"task_id": task_id, "task_type": "radarr_search_request", "service_id": service_id, "title": item.get("title"), "status": "pending_approval"}
            decision_id = self.control_store.record_decision(
                "worker",
                "never_imported_recovery",
                item.get("title") or str(service_id),
                "pending",
                {"task_id": task_id, "service_id": service_id},
                {},
            )
            try:
                result = self.plane.approve_task(task_id, "autonomous-recovery")
                self.control_store.update_decision(decision_id, "executed", {"task_id": task_id, "result": result.get("result") or {}})
                task_result.update({"status": "executed", "decision_id": decision_id})
            except Exception as exc:
                if hasattr(self.control_store, "update_decision"):
                    self.control_store.update_decision(decision_id, "failed", {"task_id": task_id, "error": str(exc)})
                task_result.update({"status": "failed", "decision_id": decision_id, "error": str(exc)})
            recovered.append(task_result)
            if len(recovered) >= max_recover:
                break
        return {"count": int(gaps.get("count") or 0), "recovered": recovered, "exhausted": exhausted}

    def _tv_growth_enabled(self) -> bool:
        return self._policy_bool("CINESWARM_TV_GROWTH")

    def _tv_growth_terms(self, owned_titles: set[str]) -> list[str]:
        terms: list[str] = []
        seen: set[str] = set()
        brain = getattr(self.plane, "library_brain", None)
        watched = []
        if brain is not None and hasattr(brain, "watched_series_titles"):
            try:
                watched = list(brain.watched_series_titles(limit=40) or [])
            except Exception:
                watched = []
        queue_titles = []
        if hasattr(self.plane, "discovery_queue"):
            try:
                for candidate in self.plane.discovery_queue() or []:
                    if candidate.get("media_type") == "series" and candidate.get("title"):
                        queue_titles.append(str(candidate.get("title")))
            except Exception:
                queue_titles = []
        for title in [*TV_GROWTH_TASTE_SEEDS, *watched, *queue_titles]:
            clean = str(title or "").strip()
            key = clean.casefold()
            if not clean or key in seen or key in owned_titles or is_junk_series_title(clean):
                continue
            seen.add(key)
            terms.append(clean)
        return terms

    def _tv_growth_lookup(self, sonarr: Any, term: str, owned_tvdb: set[Any], owned_titles: set[str]) -> dict[str, Any] | None:
        try:
            matches = sonarr.get("api/v3/series/lookup", {"term": term})
        except Exception:
            return None
        if not isinstance(matches, list):
            return None
        for match in matches:
            if not isinstance(match, dict) or not match.get("tvdbId"):
                continue
            title = str(match.get("title") or "").strip()
            if not title or is_junk_series_title(title) or not series_titles_align(term, title):
                continue
            if match.get("tvdbId") in owned_tvdb or title.casefold() in owned_titles:
                continue
            if str(match.get("seriesType") or "standard").lower() == "daily":
                continue
            language = str(match.get("originalLanguage") or match.get("language") or "eng").lower()
            if language not in {"", "eng", "en", "english"}:
                continue
            genres = match.get("genres") or []
            network = match.get("network") or match.get("studio")
            if not series_matches_tv_taste(genres, network=network, studio=match.get("studio")):
                continue
            year = match.get("year")
            try:
                if year and int(year) < 1987:
                    continue
            except (TypeError, ValueError):
                pass
            candidate = {
                key: match.get(key)
                for key in (
                    "tvdbId", "tmdbId", "imdbId", "title", "year", "overview", "genres",
                    "status", "titleSlug", "seriesType", "seasons", "runtime", "studio", "network",
                )
                if key in match
            }
            return candidate
        return None

    def _tv_growth_pass(self, limit: int | None = None) -> dict[str, Any]:
        """Temporary, bounded Sonarr growth toward CINESWARM_TV_GROWTH_TARGET. Does not invert movie-first acquire."""
        target = max(1, min(self._policy_int("CINESWARM_TV_GROWTH_TARGET", 200), 500))
        max_actions = max(0, min(self._policy_int("CINESWARM_TV_GROWTH_MAX_PER_CYCLE", 1), 2))
        if limit is not None:
            max_actions = max(0, min(max_actions, int(limit)))
        empty = {"status": "disabled", "managed": 0, "target": target, "added": [], "searched": [], "skipped": []}
        if not self._tv_growth_enabled():
            return empty
        if self._emergency_stop():
            return {**empty, "status": "blocked_emergency_stop"}
        if max_actions <= 0:
            return {**empty, "status": "capped"}
        sonarr = getattr(getattr(self.plane, "planner", None), "sonarr", None)
        if sonarr is None:
            return {**empty, "status": "sonarr_unavailable"}
        try:
            storage = self._check_storage()
        except Exception as exc:
            storage = {"alerts": [{"type": "storage_check_failed", "service": "sonarr", "error": str(exc)}]}
        if any(
            alert.get("service") == "sonarr" and alert.get("type") in {"low_space", "root_folder_unavailable", "storage_check_failed"}
            for alert in (storage.get("alerts") or [])
        ):
            return {**empty, "status": "blocked_storage", "storage": storage}
        try:
            series = sonarr.get("api/v3/series")
        except Exception as exc:
            return {**empty, "status": "sonarr_error", "error": str(exc)}
        if not isinstance(series, list):
            series = []
        managed = len(series)
        owned_tvdb = {item.get("tvdbId") for item in series if item.get("tvdbId")}
        owned_titles = {str(item.get("title") or "").strip().casefold() for item in series}
        if managed >= target:
            return {**empty, "status": "target_reached", "managed": managed}
        required_profile = self._policy("CINESWARM_AUTO_REQUIRED_QUALITY_PROFILE", "HD-1080p")
        try:
            roots = sonarr.get("api/v3/rootfolder") or []
            profiles = sonarr.get("api/v3/qualityprofile") or []
        except Exception as exc:
            return {**empty, "status": "sonarr_error", "error": str(exc), "managed": managed}
        profile = next((item for item in profiles if isinstance(item, dict) and item.get("name") == required_profile), None)
        root_path = next((item.get("path") for item in roots if isinstance(item, dict) and item.get("path")), None)
        if not profile or not root_path:
            return {**empty, "status": "missing_profile_or_root", "managed": managed}

        added: list[dict[str, Any]] = []
        searched: list[dict[str, Any]] = []
        skipped: list[str] = []
        remaining = max_actions

        incomplete: list[tuple[bool, int, dict[str, Any]]] = []
        for item in series:
            if not item.get("monitored"):
                continue
            stats = item.get("statistics") or {}
            try:
                episode_count = int(stats.get("episodeCount") or 0)
                file_count = int(stats.get("episodeFileCount") or 0)
            except (TypeError, ValueError):
                continue
            if episode_count and file_count < episode_count:
                genres = {str(genre).lower() for genre in (item.get("genres") or [])}
                incomplete.append((bool(genres & TV_GROWTH_PREFERRED_GENRES), episode_count - file_count, item))
        incomplete.sort(key=lambda row: (not row[0], -row[1]))

        if remaining >= 2 and incomplete:
            item = incomplete[0][2]
            try:
                search_task_id = self.control_store.create_task(
                    "sonarr_search_request",
                    "autonomous",
                    {
                        "media_type": "series",
                        "service_id": item.get("id"),
                        "reason": "Temporary TV growth: fill missing episodes on an already-managed series.",
                    },
                )
                search_result = self.plane.approve_task(search_task_id, "autonomous-worker")
                searched.append({
                    "service_id": item.get("id"),
                    "title": item.get("title"),
                    "task_id": search_task_id,
                    "command_id": (search_result.get("result") or {}).get("id"),
                    "missing_episodes": incomplete[0][1],
                })
                remaining -= 1
            except Exception as exc:
                skipped.append(f"search:{item.get('title')}:{exc}")

        for term in self._tv_growth_terms(owned_titles):
            if remaining <= 0 or managed + len(added) >= target:
                break
            candidate = self._tv_growth_lookup(sonarr, term, owned_tvdb, owned_titles)
            if not candidate:
                skipped.append(f"lookup:{term}")
                continue
            add_payload = {
                "media_type": "series",
                "candidate": candidate,
                "root_folder_path": root_path,
                "quality_profile_id": profile["id"],
            }
            try:
                add_task_id = self.control_store.create_task("sonarr_add_request", "autonomous", add_payload)
                add_result = self.plane.approve_task(add_task_id, "autonomous-worker")
                service_id = (add_result.get("result") or {}).get("id")
                follow_up = add_result.get("follow_up") or {}
                search_task_id = follow_up.get("task_id")
                command_id = None
                if search_task_id:
                    search_result = self.plane.approve_task(search_task_id, "autonomous-worker")
                    command_id = (search_result.get("result") or {}).get("id")
                added.append({
                    "title": candidate.get("title"),
                    "tvdbId": candidate.get("tvdbId"),
                    "service_id": service_id,
                    "add_task_id": add_task_id,
                    "search_task_id": search_task_id,
                    "command_id": command_id,
                    "term": term,
                })
                owned_tvdb.add(candidate.get("tvdbId"))
                owned_titles.add(str(candidate.get("title") or "").casefold())
                remaining -= 1
            except Exception as exc:
                skipped.append(f"add:{candidate.get('title')}:{exc}")

        if remaining > 0 and not added and incomplete and not searched:
            item = incomplete[0][2]
            try:
                search_task_id = self.control_store.create_task(
                    "sonarr_search_request",
                    "autonomous",
                    {
                        "media_type": "series",
                        "service_id": item.get("id"),
                        "reason": "Temporary TV growth: no new taste series this cycle; search missing episodes instead.",
                    },
                )
                search_result = self.plane.approve_task(search_task_id, "autonomous-worker")
                searched.append({
                    "service_id": item.get("id"),
                    "title": item.get("title"),
                    "task_id": search_task_id,
                    "command_id": (search_result.get("result") or {}).get("id"),
                    "missing_episodes": incomplete[0][1],
                })
            except Exception as exc:
                skipped.append(f"search:{item.get('title')}:{exc}")

        status = "executed" if added or searched else "no_eligible_series"
        return {
            "status": status,
            "managed": managed + len(added),
            "started_managed": managed,
            "target": target,
            "progress": f"{managed + len(added)} → {target}",
            "added": added,
            "searched": searched,
            "skipped": skipped[:12],
        }

    def _auto_refresh_allowed(self) -> bool:
        return self._policy_bool("CINESWARM_AUTO_PLEX_REFRESH")

    def _refresh_cooldown_elapsed(self) -> bool:
        last_run = self.control_store.last_audit_time("plex_library_refresh", "plex", "completed")
        if not last_run:
            return True
        previous = datetime.fromisoformat(last_run)
        cooldown = self._policy_int("CINESWARM_PLEX_REFRESH_COOLDOWN", 21600)
        return (datetime.now(timezone.utc) - previous).total_seconds() >= cooldown

    def _get_free_space_gb(self, path: str) -> float:
        """Get free space in GB for a given path, with path mapping."""
        # Try the mapped path first, then the original
        candidates = [path]
        mappings = os.environ.get("CINESWARM_PATH_MAP", "/media=/mnt/media,/data=/mnt/media")
        for mapping in mappings.split(","):
            if "=" not in mapping:
                continue
            source, target = mapping.split("=", 1)
            source, target = source.rstrip("/"), target.rstrip("/")
            if path == source or path.startswith(source + "/"):
                candidates.append(target + path[len(source):])
        for candidate in candidates:
            try:
                usage = shutil.disk_usage(candidate)
                return usage.free / (1024**3)
            except Exception:
                continue
        return 0.0

    def _check_storage(self) -> dict[str, Any]:
        """Check storage status for all configured root folders."""
        if not self.plane.planner:
            return {"status": "planner_not_configured", "alerts": [], "root_folders": {}}
        alerts = []
        root_folders = {}
        for client_name, client in [("radarr", self.plane.planner.radarr), ("sonarr", self.plane.planner.sonarr)]:
            roots = client.get("api/v3/rootfolder")
            roots = roots if isinstance(roots, list) else []
            root_folders[client_name] = len(roots)
            if not roots:
                alerts.append({"type": "root_folder_unavailable", "service": client_name})
            for root in roots:
                path = root.get("path", "")
                free_gb = self._get_free_space_gb(path)
                min_free = self._policy_int("CINESWARM_AUTO_MIN_FREE_SPACE_GB", 500)
                if free_gb < min_free:
                    alerts.append({
                        "type": "low_space",
                        "service": client_name,
                        "path": path,
                        "free_gb": round(free_gb, 2),
                        "min_free_gb": min_free,
                    })
        return {"status": "ready" if not alerts else "waiting", "alerts": alerts, "root_folders": root_folders, "checked_at": utc_epoch()}

    def _send_notification(self, event_type: str, payload: dict[str, Any], event_key: str | None = None, force: bool = False) -> bool:
        """Send a notification through the paired Discord channel or configured webhook."""
        bot_token = os.environ.get("CINESWARM_DISCORD_BOT_TOKEN", "")
        channel_id = self.control_store.get_policy("CINESWARM_DISCORD_CHANNEL_IDS") or ""
        bot_url = f"https://discord.com/api/v10/channels/{channel_id}/messages" if bot_token and channel_id else ""
        discord_webhook = os.environ.get("CINESWARM_DISCORD_WEBHOOK", "")
        target_url = bot_url or discord_webhook or os.environ.get("CINESWARM_NOTIFICATION_WEBHOOK", "")
        if not target_url:
            return False
        event_key = event_key or event_type
        cooldown = self._policy_int("CINESWARM_NOTIFICATION_COOLDOWN", 1800)
        if not force and not self.store.notification_allowed(event_key, cooldown):
            return False
        timestamp = datetime.now(timezone.utc).isoformat()
        details = redact({"event_type": event_type, "timestamp": timestamp, "worker_id": WORKER_ID, **payload})
        discord_delivery = bool(bot_url or discord_webhook or "discord.com/api/webhooks/" in target_url)
        if discord_delivery:
            color = 15158332 if any(marker in event_type for marker in ("failed", "error", "alert", "unhealthy")) else 3066993
            title = f"CineSwarm: {event_type.replace('_', ' ').title()}"
            fields = []

            if event_type == "daily_digest":
                title = "📰 CineSwarm Daily Library Digest"
                counts = payload.get("counts", {})
                fields.append({"name": "Vault Status", "value": f"🎬 `{counts.get('movie', 0)} Movies` | 📺 `{counts.get('series', 0)} TV Series`", "inline": False})
                recs = payload.get("recommendations", [])
                if recs:
                    rec_lines = []
                    for i, r in enumerate(recs[:3], 1):
                        rec_lines.append(f"**#{i} {r.get('title')} ({r.get('year') or 'n.d.'})** ({r.get('duration_mins', 90)}m)\n*{r.get('reason') or r.get('overview') or 'Vault Spotlight.'}*")
                    fields.append({"name": "🍿 Top Watch Recommendations for Today", "value": "\n\n".join(rec_lines), "inline": False})
                op_sum = payload.get("operational_summary", {})
                if op_sum:
                    aud = op_sum.get("audit", {})
                    fields.append({"name": "⚡ Operational Telemetry (Last 24h)", "value": f"Audits: `{aud.get('successes', 0)} Success` | `{aud.get('failures', 0)} Failures`", "inline": False})
                embed = {"title": title, "color": color, "fields": fields, "timestamp": timestamp}
            elif event_type in ("autopilot_decision", "startup_ready"):
                decision_id = payload.get("decision_id", "n/a")
                status = str(payload.get("status", "completed")).upper()
                title = f"🤖 Autopilot Decision: {payload.get('title') or payload.get('candidate_id') or 'System Action'}"
                fields.append({"name": "Status / Decision", "value": f"`{status}` (ID: `{decision_id}`)", "inline": True})
                if payload.get("service_id"):
                    fields.append({"name": "Service ID", "value": f"`{payload['service_id']}`", "inline": True})
                if payload.get("error"):
                    fields.append({"name": "Error Details", "value": f"```{payload['error']}```", "inline": False})
                embed = {"title": title, "color": color, "fields": fields, "timestamp": timestamp}
            elif event_type == "incoming_issues":
                severity = str(payload.get("severity") or "watch").upper()
                color = 15105570 if payload.get("severity") == "attention" else 15158332
                title = f"Incoming household issues — {severity}"
                fields.append({"name": "Headline", "value": str(payload.get("headline") or "See issues")[:1000], "inline": False})
                lines = []
                for issue in (payload.get("issues") or [])[:6]:
                    lines.append(f"• `{issue.get('code')}` {issue.get('summary')}")
                if lines:
                    fields.append({"name": "Ranked", "value": "\n".join(lines)[:1000], "inline": False})
                embed = {"title": title, "color": color, "fields": fields, "timestamp": timestamp}
            else:
                embed = {"title": title, "description": f"```json\n{json_text(details)[:3500]}\n```", "color": color, "timestamp": timestamp}

            body = {"embeds": [embed]} if bot_url else {"username": "CineSwarm", "embeds": [embed]}

        else:
            body = details
        try:
            data = json.dumps(body).encode("utf-8")
            headers = {"Content-Type": "application/json", "User-Agent": "CineSwarm/1.0"}
            if bot_url:
                headers["Authorization"] = f"Bot {bot_token}"
            request = urllib.request.Request(target_url, data=data, method="POST", headers=headers)
            with urllib.request.urlopen(request, timeout=10):
                pass
            self.store.mark_notification(event_key)
            return True
        except Exception as exc:
            print(f"Failed to send notification: {exc}", flush=True)
            return False

    def _overall_monitor_status(self, refresh_result: dict[str, Any] | None = None) -> dict[str, Any]:
        """Derive compact overall status from refresh results and stored worker heartbeat."""
        status = self.control_store.status()
        services = status.get("services") or []
        if refresh_result:
            # Prefer just-refreshed statuses when available
            merged = {item.get("service"): item for item in services if item.get("service")}
            for name, details in refresh_result.items():
                merged[name] = {"service": name, "status": details.get("status", "error"), "error": details.get("error")}
            services = list(merged.values())
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
        return {
            "overall_status": overall,
            "unhealthy_services": unhealthy,
            "worker_healthy": worker_healthy,
            "emergency_stop": bool(self.control_store.is_emergency_stop()),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def _post_monitor_webhook_on_transition(self, refresh_result: dict[str, Any] | None = None) -> bool:
        """POST to CINESWARM_MONITOR_WEBHOOK only when overall status transitions."""
        url = os.environ.get("CINESWARM_MONITOR_WEBHOOK", "").strip()
        if not url:
            return False
        payload = self._overall_monitor_status(refresh_result)
        overall = payload["overall_status"]
        previous = getattr(self, "_last_monitor_overall_status", None)
        if previous is None:
            stored = self.control_store.get_policy("CINESWARM_MONITOR_LAST_STATUS")
            previous = stored
        self._last_monitor_overall_status = overall
        try:
            self.control_store.set_policy("CINESWARM_MONITOR_LAST_STATUS", overall)
        except Exception:
            pass
        if previous is None or previous == overall:
            return False
        should_notify = overall in {"unhealthy", "degraded"} or (
            overall == "healthy" and previous in {"unhealthy", "degraded"}
        )
        if not should_notify:
            return False
        body = {
            "event_type": "monitor_status_transition",
            "status": overall,
            "previous_status": previous,
            "unhealthy_services": payload.get("unhealthy_services") or [],
            "worker_healthy": payload.get("worker_healthy"),
            "emergency_stop": payload.get("emergency_stop"),
            "timestamp": payload.get("timestamp"),
        }
        try:
            data = json.dumps(body).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={"Content-Type": "application/json", "User-Agent": "CineSwarm/1.0"},
            )
            with urllib.request.urlopen(request, timeout=10):
                pass
            return True
        except Exception as exc:
            print(f"Failed to post monitor webhook: {exc}", flush=True)
            return False

    def execute(self, job: sqlite3.Row) -> dict[str, Any]:
        job_type = job["job_type"]
        readiness = self._startup_readiness() if job_type in ("autonomous_acquisition", "failed_download_recovery") else {"ready": True}
        if not readiness["ready"]:
            if job_type == "autonomous_acquisition":
                return self._record_autopilot_decision("blocked_startup_readiness", {"readiness": readiness})
            return {"status": "blocked_startup_readiness", "readiness": readiness, "approval_tasks_created": 0, "tasks": []}
        
        # Check storage before certain jobs
        if job_type in ("autonomous_acquisition", "catalog_sync", "discovery_refresh"):
            storage_check = self._check_storage()
            if storage_check["alerts"]:
                self._send_notification("storage_alert", {"alerts": storage_check["alerts"]})
                if job_type == "autonomous_acquisition":
                    return self._record_autopilot_decision("blocked_storage", {"alerts": storage_check["alerts"]})
                return {"status": "storage_alert", "alerts": storage_check["alerts"]}
        
        if job_type == "service_refresh":
            result = self.plane.refresh("worker")
            unhealthy = {service: details for service, details in result.items() if details.get("status") != "healthy"}
            if unhealthy:
                self._send_notification("service_unhealthy", {"services": unhealthy}, "service_unhealthy")
            self._post_monitor_webhook_on_transition(result)
            try:
                history = self.plane.discovery.playback_history if self.plane.discovery else None
                if history:
                    result["watch_ledger"] = self.plane.library_brain.sync_watch_ledger(history)
            except Exception as exc:
                result["watch_ledger"] = {"status": "error", "error": str(exc)}
            return result
        if job_type == "catalog_sync":
            sync_catalog()
            brain_result: dict[str, Any] = {"status": "skipped"}
            try:
                history = self.plane.discovery.playback_history if self.plane.discovery else None
                brain_result = self.plane.library_brain.refresh(playback_history=history)
            except Exception as exc:
                brain_result = {"status": "error", "error": str(exc)}
                print(f"Failed to refresh library brain: {exc}", flush=True)
            try:
                from export_gem_knowledge import generate_gem_knowledge
                generate_gem_knowledge()
            except Exception as exc:
                print(f"Failed to auto-update Gem knowledge: {exc}", flush=True)
            return {"status": "catalog_synced", "library_brain": brain_result}
        if job_type == "discovery_refresh":
            if not self._policy_bool("CINESWARM_DISCOVERY_ENABLED", "true"):
                return {"status": "disabled"}
            return self.plane.discovery_run("worker")
        if job_type == "franchise_refresh":
            if not self._policy_bool("CINESWARM_DISCOVERY_ENABLED", "true"):
                return {"status": "disabled"}
            if not self.plane.discovery:
                return {"status": "discovery_not_configured"}
            return self.plane.discovery.discover_missing_franchise_items(limit=10)
        if job_type == "queue_monitor":
            if not self.plane.planner:
                raise RuntimeError("Acquisition planner is not configured")
            return {"radarr": self.plane.planner.queue("movie"), "sonarr": self.plane.planner.queue("series"), "acquisitions": self.plane.monitor_acquisitions("worker")}
        if job_type == "reconcile_library":
            report = self.plane.reconcile("worker")
            needs_refresh = (
                report.get("movies", {}).get("file_not_indexed", 0)
                + report.get("series", {}).get("not_indexed", 0)
            ) > 0
            if getattr(self, "_startup_ready", True) and self._auto_refresh_allowed() and needs_refresh and self._refresh_cooldown_elapsed():
                report["automatic_action"] = self.plane.execute_automatic(
                    "plex_library_refresh",
                    "worker",
                    {"reason": "files_present_but_not_indexed", "movies": report.get("movies", {}), "series": report.get("series", {})},
                )
            else:
                report["automatic_action"] = {"status": "not_run", "enabled": self._auto_refresh_allowed(), "cooldown_elapsed": self._refresh_cooldown_elapsed(), "reason": "policy_or_cooldown"}
            try:
                report["incoming"] = self.plane.incoming_issues_report(persist=True)
            except Exception as exc:
                report["incoming"] = {"status": "error", "error": str(exc)}
            return report
        if job_type == "ops_pulse":
            report = self.plane.incoming_issues_report(persist=True)
            if report.get("severity") in {"attention", "critical"}:
                self._send_notification("incoming_issues", report, "incoming_issues")
            return report
        if job_type == "autonomous_acquisition":
            if self._emergency_stop():
                return self._record_autopilot_decision("blocked_emergency_stop", {})
            if not self._policy_bool("CINESWARM_AUTO_ADD_SEARCH"):
                return self._record_autopilot_decision("blocked_disabled", {})
            pressure = self._queue_pressure()
            if pressure["pressured"]:
                return self._record_autopilot_decision("blocked_queue_limit", {"active": pressure["active"], "limit": pressure["limit"], "pressure": pressure})
            estimated_gb = self._policy_int("CINESWARM_AUTO_ESTIMATED_MOVIE_GB", 20)
            budget_ok, budget_info = self.control_store.check_budget()
            budget_mode = self._policy("CINESWARM_AUTO_BUDGET_MODE", "weekly")
            if budget_mode != "queue_only" and (not budget_ok or budget_info["budget"]["gb_added"] + estimated_gb > budget_info["limits"]["gb"]):
                return self._record_autopilot_decision("blocked_weekly_budget", {"details": budget_info})
            if not self.plane.discovery:
                return self._record_autopilot_decision("blocked_discovery_unavailable", {})
            queue = self.plane.discovery_queue()
            min_score = self._policy_int("CINESWARM_AUTO_MIN_SCORE", 65)
            near_miss_floor = self._policy_int("CINESWARM_AUTO_NEAR_MISS_FLOOR", max(0, min_score - 5))
            affinity_floor = self._policy_int("CINESWARM_AUTO_NEAR_MISS_AFFINITY_FLOOR", 35)
            free_slots = max(0, int(pressure.get("limit") or 4) - int(pressure.get("active") or 0))
            acquire_cap = max(1, min(self._policy_int("CINESWARM_AUTO_ACQUIRE_PER_CYCLE", free_slots or 1), free_slots or 1, 8))
            tv_max = max(0, min(self._policy_int("CINESWARM_TV_GROWTH_MAX_PER_CYCLE", 1), 2)) if self._tv_growth_enabled() else 0
            tv_reserved = 1 if tv_max and free_slots >= 2 else 0
            if tv_reserved:
                acquire_cap = max(1, acquire_cap - tv_reserved)
            allowed_genres = {genre.strip().lower() for genre in self._policy("CINESWARM_AUTO_ALLOWED_GENRES").split(",") if genre.strip()}
            forbidden_genres = {genre.strip().lower() for genre in self._policy("CINESWARM_AUTO_FORBIDDEN_GENRES").split(",") if genre.strip()}
            required_profile = self._policy("CINESWARM_AUTO_REQUIRED_QUALITY_PROFILE", "HD-1080p")
            recent_years = self._policy_int("CINESWARM_AUTO_RECENT_YEARS", 2)
            recent_target = self._policy_int("CINESWARM_AUTO_RECENT_WEEKLY_TARGET", 3)
            desired_recent = budget_info["budget"]["movies_added"] < recent_target
            recent_cutoff = datetime.now(timezone.utc).year - recent_years
            queue.sort(key=lambda candidate: ((candidate.get("year", 0) >= recent_cutoff) != desired_recent, -candidate.get("score", 0)))
            managed_movies = self.plane.planner.radarr.get("api/v3/movie")
            skip_counts = {"low_score": 0, "non_movie_or_status": 0, "forbidden_genre": 0, "owned_or_missing_detail": 0, "missing_profile_or_root": 0, "boxset": 0, "short_runtime": 0}
            near_misses: list[dict[str, Any]] = []
            acquired: list[dict[str, Any]] = []

            def _execute_candidate(candidate: dict[str, Any], candidate_detail: dict[str, Any], decision_name: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
                plan = self.plane.planner.plan("movie", candidate["title"])
                profile = next((item for item in plan.get("quality_profiles", []) if item.get("name") == required_profile), None)
                if not plan.get("root_folders") or not profile:
                    skip_counts["missing_profile_or_root"] += 1
                    return {}
                add_payload = {
                    "media_type": "movie",
                    "candidate": candidate_detail["candidate"],
                    "root_folder_path": plan["root_folders"][0]["path"],
                    "quality_profile_id": profile["id"],
                    "discovery_candidate_id": candidate["id"],
                }
                action_id = self.control_store.record_autonomous_action("autonomous_add_search", candidate["id"], self.control_store.get_week_start())
                existing_movie = next((item for item in managed_movies if item.get("tmdbId") == candidate_detail["candidate"].get("tmdbId")), None)
                try:
                    if existing_movie:
                        add_task_id = None
                        service_id = existing_movie["id"]
                        search_task_id = self.control_store.create_task("radarr_search_request", "autonomous", {"media_type": "movie", "service_id": service_id, "reason": "Recover an autonomous add that completed before its search step."})
                    else:
                        add_task_id = self.control_store.create_task("radarr_add_request", "autonomous", add_payload)
                        add_result = self.plane.approve_task(add_task_id, "autonomous-worker")
                        service_id = add_result.get("result", {}).get("id")
                        follow_up = add_result.get("follow_up") or {}
                        search_task_id = follow_up.get("task_id")
                        if not search_task_id:
                            raise RuntimeError("Autonomous add did not create a search task")
                    self.control_store.increment_budget("movie", estimated_gb)
                    sync_catalog()
                    search_result = self.plane.approve_task(search_task_id, "autonomous-worker")
                except Exception as exc:
                    self.control_store.fail_autonomous_action(action_id)
                    self._record_autopilot_decision("failed_execution", {"candidate_id": candidate["id"], "title": candidate["title"], "error": str(exc)})
                    raise
                self.control_store.complete_autonomous_action(action_id)
                self.plane.discovery.set_status(candidate["id"], "approved")
                payload = {
                    "candidate_id": candidate["id"],
                    "title": candidate["title"],
                    "add_task_id": add_task_id,
                    "search_task_id": search_task_id,
                    "service_id": service_id,
                    "command_id": search_result.get("result", {}).get("id"),
                    "score": candidate.get("score"),
                }
                if existing_movie and decision_name == "executed_add_search":
                    decision_name = "executed_recovery_search"
                if extra:
                    payload.update(extra)
                return self._record_autopilot_decision(decision_name, payload)

            for candidate in queue:
                if candidate["media_type"] != "movie" or candidate["status"] not in ("new", "approved"):
                    skip_counts["non_movie_or_status"] += 1
                    continue
                if is_boxset_title(candidate.get("title")):
                    skip_counts["boxset"] += 1
                    try:
                        self.plane.discovery.set_status(candidate["id"], "rejected")
                    except Exception:
                        pass
                    continue
                if candidate["score"] < min_score:
                    skip_counts["low_score"] += 1
                    if len(near_misses) < 8:
                        near_misses.append({
                            "id": candidate.get("id"),
                            "title": candidate.get("title"),
                            "year": candidate.get("year"),
                            "score": candidate.get("score"),
                            "watch_affinity_score": candidate.get("watch_affinity_score"),
                            "reason": "low_score",
                        })
                    continue
                candidate_detail = self.plane.discovery.candidate(candidate["id"])
                if not candidate_detail:
                    skip_counts["owned_or_missing_detail"] += 1
                    if len(near_misses) < 8:
                        near_misses.append({"id": candidate.get("id"), "title": candidate.get("title"), "year": candidate.get("year"), "score": candidate.get("score"), "reason": "owned_or_missing_detail"})
                    continue
                raw = candidate_detail.get("candidate") or {}
                runtime_val = self.runtime_minutes(raw)
                if 0 < runtime_val < 70:
                    skip_counts["short_runtime"] += 1
                    continue
                genres = {str(genre).lower() for genre in (raw.get("genres") or [])}
                if self.genres_blocked(genres, allowed_genres, forbidden_genres):
                    skip_counts["forbidden_genre"] += 1
                    if len(near_misses) < 8:
                        near_misses.append({"id": candidate.get("id"), "title": candidate.get("title"), "year": candidate.get("year"), "score": candidate.get("score"), "reason": "forbidden_genre", "genres": sorted(genres & forbidden_genres) if forbidden_genres else sorted(genres)})
                    continue
                result = _execute_candidate(candidate, candidate_detail, "executed_add_search")
                if result:
                    acquired.append(result)
                    if len(acquired) >= acquire_cap:
                        break
            near_misses.sort(key=lambda item: -float(item.get("score") or 0))

            if len(acquired) < acquire_cap and near_misses and (acquired or not self._near_miss_promote_recent()):
                for miss in near_misses:
                    if len(acquired) >= acquire_cap:
                        break
                    score = float(miss.get("score") or 0)
                    if score < near_miss_floor:
                        continue
                    candidate = next((item for item in queue if item.get("id") == miss.get("id")), None)
                    if not candidate:
                        continue
                    candidate_detail = self.plane.discovery.candidate(candidate["id"])
                    if not candidate_detail:
                        continue
                    raw = candidate_detail.get("candidate") or {}
                    genres = {str(genre).lower() for genre in (raw.get("genres") or [])}
                    if self.genres_blocked(genres, allowed_genres, forbidden_genres):
                        continue
                    runtime_val = self.runtime_minutes(raw)
                    theatrical_ok = runtime_val >= 70 and not (raw.get("seriesType") or raw.get("seasons"))
                    affinity = float(candidate.get("watch_affinity_score") or candidate_detail.get("watch_affinity_score") or 0)
                    if not theatrical_ok or affinity < affinity_floor:
                        continue
                    result = _execute_candidate(
                        candidate,
                        candidate_detail,
                        "executed_near_miss_promote",
                        {
                            "near_miss_floor": near_miss_floor,
                            "affinity_floor": affinity_floor,
                            "watch_affinity_score": affinity,
                            "runtime": runtime_val,
                            "promote_reason": "score_within_near_miss_band_with_theatrical_affinity_gates",
                        },
                    )
                    if result:
                        acquired.append(result)

            leftover = max(0, acquire_cap + tv_reserved - len(acquired))
            tv_growth: dict[str, Any] = {"status": "disabled", "added": [], "searched": []}
            if leftover and tv_max:
                try:
                    tv_growth = self._tv_growth_pass(limit=min(tv_max, leftover))
                except Exception as exc:
                    tv_growth = {"status": "error", "error": str(exc), "added": [], "searched": []}
                leftover = max(0, leftover - len(tv_growth.get("added") or []) - len(tv_growth.get("searched") or []))

            never_imported: dict[str, Any] = {"count": 0, "recovered": []}
            if leftover and self._policy_bool("CINESWARM_FULL_AUTOPILOT") and hasattr(self.plane, "never_imported_movies"):
                never_imported = self._recover_never_imported(limit=leftover)
                leftover = max(0, leftover - len(never_imported.get("recovered") or []))

            if acquired:
                payload = {
                    **acquired[-1],
                    "acquisitions": acquired,
                    "acquired": len(acquired),
                    "never_imported": never_imported,
                    "tv_growth": tv_growth,
                    "skip_counts": skip_counts,
                }
                return payload

            if tv_growth.get("added") or tv_growth.get("searched"):
                title = None
                if tv_growth.get("added"):
                    title = tv_growth["added"][-1].get("title")
                elif tv_growth.get("searched"):
                    title = tv_growth["searched"][-1].get("title")
                return self._record_autopilot_decision("executed_tv_growth", {
                    "acquired": 0,
                    "title": title,
                    "tv_growth": tv_growth,
                    "never_imported": never_imported,
                    "skip_counts": skip_counts,
                })

            if never_imported.get("recovered"):
                return self._record_autopilot_decision("executed_never_imported_fill", {
                    "acquired": 0,
                    "never_imported": never_imported,
                    "tv_growth": tv_growth,
                    "skip_counts": skip_counts,
                })

            return self._record_autopilot_decision("skipped_no_eligible_candidate", {
                "desired_recent": desired_recent,
                "recent_cutoff": recent_cutoff,
                "candidate_count": len(queue),
                "min_score": min_score,
                "near_miss_floor": near_miss_floor,
                "skip_counts": skip_counts,
                "near_misses": near_misses[:5],
                "never_imported": never_imported,
                "tv_growth": tv_growth,
            })
        if job_type == "failed_download_recovery":
            if self._emergency_stop():
                return {"status": "blocked_emergency_stop", "approval_tasks_created": 0, "tasks": []}
            if not self.plane.planner:
                return {"status": "planner_not_configured"}
            pressure = self._queue_pressure()
            failed = self.plane.planner.get_failed_downloads("movie")
            failed += self.plane.planner.get_failed_downloads("series")
            failed.sort(key=lambda item: (str(item.get("media_type")), str(item.get("service_id")), str(item.get("queue_id") or ""), str(item.get("title") or "")))
            approval_tasks = []
            selected_per_media: dict[tuple[str, str], int] = {}
            max_per_cycle = max(0, self._policy_int("CINESWARM_FAILED_DOWNLOAD_MAX_PER_CYCLE", 5))
            max_per_media = max(0, self._policy_int("CINESWARM_FAILED_DOWNLOAD_MAX_PER_MEDIA_PER_CYCLE", 1))
            cooldown = max(0, self._policy_int("CINESWARM_FAILED_DOWNLOAD_COOLDOWN", 21600))
            for item in failed:
                if len(approval_tasks) >= max_per_cycle:
                    break
                media_type = item.get("media_type")
                service_id = item.get("service_id")
                if media_type not in ("movie", "series") or not service_id:
                    continue
                media_key = (media_type, str(service_id))
                if selected_per_media.get(media_key, 0) >= max_per_media:
                    continue
                task_type = "radarr_search_retry_request" if media_type == "movie" else "sonarr_search_retry_request"
                parent_task_id = f"failed-download:{media_type}:{item.get('queue_id') or service_id}"
                if self.control_store.task_exists(task_type, parent_task_id):
                    continue
                if hasattr(self.control_store, "retry_cooldown_active") and self.control_store.retry_cooldown_active(task_type, media_type, service_id, cooldown):
                    continue
                payload = {
                    "media_type": media_type,
                    "service_id": service_id,
                    "parent_task_id": parent_task_id,
                    "reason": "A download failed. Retry search once under the configured recovery policy.",
                }
                task_id = self.control_store.create_task(task_type, "worker", payload)
                selected_per_media[media_key] = selected_per_media.get(media_key, 0) + 1
                task_result = {"task_id": task_id, "task_type": task_type, "service_id": service_id, "status": "pending_approval"}
                if self._policy_bool("CINESWARM_FULL_AUTOPILOT") and not pressure["pressured"]:
                    decision_id = self.control_store.record_decision("worker", "failed_download_retry", item.get("title") or str(service_id), "pending", {"error": item.get("error_message"), "task_id": task_id}, {})
                    try:
                        result = self.plane.approve_task(task_id, "autonomous-recovery")
                        self.control_store.update_decision(decision_id, "executed", {"task_id": task_id, "result": result.get("result") or {}})
                        task_result.update({"status": "executed", "decision_id": decision_id})
                    except Exception as exc:
                        self.control_store.update_decision(decision_id, "failed", {"task_id": task_id, "error": str(exc)})
                        task_result.update({"status": "failed", "decision_id": decision_id, "error": str(exc)})
                    self._send_notification("autopilot_decision", task_result, f"autopilot_decision:{decision_id}", force=True)
                approval_tasks.append(task_result)
            notify = self.control_store.get_policy("CINESWARM_NOTIFY_ON_FAILED_DOWNLOAD") or os.environ.get("CINESWARM_NOTIFY_ON_FAILED_DOWNLOAD", "false")
            if notify.lower() in ("1", "true", "yes", "on") and failed:
                self._send_notification("failed_downloads", {"count": len(failed), "failed": failed[:5]}, "failed_downloads")
            never_imported: dict[str, Any] = {"count": 0, "recovered": []}
            pending_retries: list[dict[str, Any]] = []
            free_slots = max(0, int(pressure.get("limit") or 2) - int(pressure.get("active") or 0) - len(approval_tasks))
            if (
                free_slots
                and not pressure.get("pressured")
                and self._policy_bool("CINESWARM_FULL_AUTOPILOT")
                and hasattr(self.control_store, "pending_tasks")
            ):
                seen_ids: set[str] = set()
                for task in self.control_store.pending_tasks("radarr_search_retry_request", limit=30):
                    if free_slots <= 0:
                        break
                    payload = task.get("payload") or {}
                    service_id = payload.get("service_id")
                    title = self._movie_title(service_id)
                    if is_boxset_title(title):
                        self.control_store.update_task(task["task_id"], "completed", {"status": "skipped_boxset", "title": title})
                        continue
                    if service_id in (None, "") or str(service_id) in seen_ids:
                        self.control_store.update_task(task["task_id"], "completed", {"status": "skipped_duplicate", "service_id": service_id})
                        continue
                    seen_ids.add(str(service_id))
                    task_result = {"task_id": task["task_id"], "task_type": task["task_type"], "service_id": service_id, "title": title, "status": "pending_approval"}
                    try:
                        result = self.plane.approve_task(task["task_id"], "autonomous-recovery")
                        task_result.update({"status": "executed", "result": result.get("result") or {}})
                        free_slots -= 1
                    except Exception as exc:
                        task_result.update({"status": "failed", "error": str(exc)})
                    pending_retries.append(task_result)
                    approval_tasks.append(task_result)
            if (
                free_slots
                and not pressure.get("pressured")
                and hasattr(self.plane, "never_imported_movies")
                and self._policy_bool("CINESWARM_FULL_AUTOPILOT")
            ):
                never_imported = self._recover_never_imported(limit=free_slots)
                approval_tasks.extend(never_imported.get("recovered") or [])
                free_slots = max(0, free_slots - len(never_imported.get("recovered") or []))
            av1_upgrades = {"queued": []}
            if (
                free_slots
                and not pressure.get("pressured")
                and hasattr(self.plane, "queue_av1_upgrade_searches")
                and self._policy_bool("CINESWARM_FULL_AUTOPILOT")
            ):
                av1_limit = min(free_slots, self._policy_int("CINESWARM_AV1_UPGRADE_MAX_PER_CYCLE", 3))
                av1_upgrades = self.plane.queue_av1_upgrade_searches(av1_limit, actor="worker")
                for item in av1_upgrades.get("queued") or []:
                    task_id = item.get("task_id")
                    if not task_id:
                        continue
                    try:
                        self.plane.approve_task(task_id, "autonomous-recovery")
                        item["status"] = "executed"
                    except Exception as exc:
                        item["status"] = "failed"
                        item["error"] = str(exc)
                    approval_tasks.append(item)
            return {"status": "completed", "failed_count": len(failed), "approval_tasks_created": len(approval_tasks), "tasks": approval_tasks, "pressure": pressure, "limits": {"per_cycle": max_per_cycle, "per_media": max_per_media, "cooldown": cooldown}, "never_imported": never_imported, "pending_retries": pending_retries, "av1_upgrades": av1_upgrades}
        if job_type == "media_health_scan":
            scan_res = self.plane.scan_media_health(limit=50, actor="worker", allow_automatic=getattr(self, "_startup_ready", True))
            if scan_res.get("corrupt_count", 0) > 0:
                self._send_notification("media_corruption_detected", scan_res, force=True)
            return scan_res
        if job_type == "preservation_scan":
            branches = configured_branches(self._policy("CINESWARM_PRESERVATION_BRANCHES", "/mnt/pool/disk*"))
            return preservation_scan(
                control_db=self.store.path,
                catalog_db=CATALOG_DB,
                roots=csv_paths(self._policy("CINESWARM_PRESERVATION_ROOTS", "/mnt/media")),
                union_roots=csv_paths(self._policy("CINESWARM_PRESERVATION_UNION_ROOTS", "/mnt/media")),
                branches=branches,
                mounts=csv_paths(self._policy("CINESWARM_PRESERVATION_MOUNTS", ",".join(branches))),
                sample_limit=self._policy_int("CINESWARM_PRESERVATION_SAMPLE_LIMIT", 50),
                checksum_bytes=self._policy_int("CINESWARM_PRESERVATION_CHECKSUM_BYTES", 16777216),
                checksum_max_size=self._policy_int("CINESWARM_PRESERVATION_CHECKSUM_MAX_SIZE", 0),
            )
        if job_type == "database_maintenance":
            return database_maintenance(
                control_db=self.store.path,
                catalog_db=CATALOG_DB,
                backup_dir=self._policy("CINESWARM_BACKUP_DIR", os.path.join(BASE_DIR, "backups")),
                retention=self._policy_int("CINESWARM_BACKUP_RETENTION", 7),
                integrity_check=self._policy_bool("CINESWARM_DATABASE_FULL_INTEGRITY_CHECK"),
            )
        if job_type == "daily_digest":
            res = self.plane.watch_recommendations(max_minutes=150, limit=3, actor="worker")
            counts = self.control_store.catalog_counts()
            digest_payload = {"counts": counts, "operational_summary": self.control_store.operational_summary(24), "recommendations": res.get("recommendations", [])}
            try:
                from export_gem_knowledge import generate_gem_knowledge
                generate_gem_knowledge()
            except Exception as exc:
                print(f"Failed to update Gem knowledge during digest: {exc}", flush=True)
            self._send_notification("daily_digest", digest_payload, "daily_digest", force=True)
            return {"status": "completed", "digest": digest_payload}

        raise RuntimeError(f"Unknown worker job type: {job_type}")

    @staticmethod
    def _audit_mode(job_type: str, result: dict[str, Any] | None = None) -> str:
        if job_type == "ops_pulse":
            return "read-only"
        if job_type == "queue_monitor":
            return "approval-gated"
        if job_type == "failed_download_recovery":
            return "automatic" if any(task.get("status") in ("executed", "failed") for task in (result or {}).get("tasks", [])) else "approval-gated"
        if job_type == "autonomous_acquisition":
            return "automatic"
        automatic = (result or {}).get("automatic_action", {})
        if job_type == "reconcile_library" and automatic.get("status") not in (None, "not_run"):
            return "automatic"
        return "local-write"

    def run_once(self) -> dict[str, Any]:
        self.store.ensure_schedules(self.schedules)
        self.store.enqueue_due_schedules()
        job = self.store.claim()
        if not job:
            self.store.heartbeat("idle", {"schedules": self.schedules})
            return {"status": "idle"}
        self.store.heartbeat("running", {"job_id": job["job_id"], "job_type": job["job_type"]})
        try:
            result = self.execute(job)
            self.store.complete(job["job_id"])
            mode = self._audit_mode(job["job_type"], result)
            self.control_store.audit("worker", job["job_type"], "maintenance", mode, "completed", {"job_id": job["job_id"], "result": result})
            self.store.heartbeat("healthy", {"job_id": job["job_id"], "job_type": job["job_type"], "result_status": result.get("status")})
            return {"status": "completed", "job_id": job["job_id"], "job_type": job["job_type"], "result": result}
        except Exception as exc:
            status = self.store.fail(job["job_id"], str(exc), job["attempts"])
            mode = self._audit_mode(job["job_type"])
            self.control_store.audit("worker", job["job_type"], "maintenance", mode, status, {"job_id": job["job_id"], "error": str(exc)})
            self.store.heartbeat(status, {"job_id": job["job_id"], "job_type": job["job_type"], "error": str(exc)})
            if status == "failed":
                self._send_notification("job_failed", {"job_id": job["job_id"], "job_type": job["job_type"], "attempts": job["attempts"], "error": str(exc)}, f"job_failed:{job['job_type']}")
            return {"status": status, "job_id": job["job_id"], "job_type": job["job_type"], "error": str(exc)}

    def run_forever(self) -> None:
        self.store.ensure_schedules(self.schedules)
        self.store.heartbeat("starting", {"schedules": self.schedules})
        sd_notify("READY=1\nSTATUS=CineSwarm worker running")
        print(f"CineSwarm worker {WORKER_ID} running; schedules={self.schedules}", flush=True)
        try:
            while True:
                sd_notify("WATCHDOG=1")
                result = self.run_once()
                if result["status"] != "idle":
                    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
                sd_notify("WATCHDOG=1")
                time.sleep(POLL_SECONDS)
        finally:
            self.store.heartbeat("stopped", {})
            sd_notify("STOPPING=1")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run persistent CineSwarm maintenance jobs.")
    parser.add_argument("--once", action="store_true", help="Claim and execute at most one due job, then exit.")
    args = parser.parse_args()
    worker = Worker()
    if args.once:
        print(json.dumps(worker.run_once(), ensure_ascii=False, default=str))
    else:
        worker.run_forever()


if __name__ == "__main__":
    main()
