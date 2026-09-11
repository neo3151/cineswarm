#!/usr/bin/env python3
"""Proactive household ops pulse: rank incoming issues from existing telemetry.

Read-only. Never moves or deletes media. No hosted model.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

SEVERITY_RANK = {"ok": 0, "watch": 1, "attention": 2, "critical": 3}

PULSE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_pulse_history (
    id INTEGER PRIMARY KEY,
    generated_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    issues_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ops_pulse_generated ON ops_pulse_history(generated_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def overall_severity(issues: list[dict[str, Any]]) -> str:
    rank = 0
    for issue in issues:
        rank = max(rank, SEVERITY_RANK.get(str(issue.get("severity") or "ok"), 0))
    for name, value in SEVERITY_RANK.items():
        if value == rank:
            return name
    return "ok"


def classify_issues(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn a point-in-time metrics dict into ranked incoming issues."""
    issues: list[dict[str, Any]] = []
    if metrics.get("emergency_stop"):
        issues.append({
            "code": "emergency_stop",
            "severity": "critical",
            "summary": "Emergency stop is on; autopilot will not add or search.",
        })
    if not metrics.get("worker_healthy", True):
        issues.append({
            "code": "worker_stale",
            "severity": "critical",
            "summary": "Worker heartbeat is stale. Scheduled acquire, recovery, and pulses are not running.",
        })
    unhealthy = [str(name) for name in (metrics.get("unhealthy_services") or []) if name]
    if unhealthy:
        issues.append({
            "code": "services_unhealthy",
            "severity": "critical" if len(unhealthy) >= 2 else "attention",
            "summary": "Unhealthy services: " + ", ".join(unhealthy) + ".",
            "services": unhealthy,
        })

    sab_error = str(metrics.get("sab_error") or "").strip()
    if sab_error:
        issues.append({
            "code": "sab_probe_failed",
            "severity": "attention",
            "summary": f"SABnzbd probe failed ({sab_error.split('(')[0].strip()}). Queue pressure may be under-counted.",
            "error": sab_error[:160],
        })

    without_file = _int(metrics.get("without_file"))
    file_not_indexed = _int(metrics.get("file_not_indexed"))
    never_imported = _int(metrics.get("never_imported"))
    delta_without = metrics.get("delta_without_file")
    delta_not_indexed = metrics.get("delta_file_not_indexed")
    delta_never = metrics.get("delta_never_imported")

    if delta_without is not None and _int(delta_without) >= 3:
        issues.append({
            "code": "library_debt_growing",
            "severity": "attention",
            "summary": f"Movies without a file rose by {delta_without} to {without_file}. Radarr is adding faster than files land.",
            "without_file": without_file,
            "delta": _int(delta_without),
        })
    elif without_file >= 450:
        issues.append({
            "code": "library_debt",
            "severity": "watch",
            "summary": f"{without_file} Radarr movies still have no file (never-imported overlap is expected).",
            "without_file": without_file,
        })

    if file_not_indexed >= 50 and (delta_not_indexed is None or _int(delta_not_indexed) == 0):
        issues.append({
            "code": "plex_index_stuck",
            "severity": "attention",
            "summary": f"{file_not_indexed} files sit on disk but Plex has not indexed them. This count has not moved.",
            "file_not_indexed": file_not_indexed,
        })
    elif file_not_indexed >= 50:
        issues.append({
            "code": "plex_index_lag",
            "severity": "watch",
            "summary": f"{file_not_indexed} files are present but not in Plex yet.",
            "file_not_indexed": file_not_indexed,
            "delta": _int(delta_not_indexed),
        })

    if never_imported >= 400 and delta_never is not None and _int(delta_never) > 0:
        issues.append({
            "code": "never_imported_growing",
            "severity": "attention",
            "summary": f"Never-imported gap grew by {delta_never} to {never_imported}. Recovery searches are not catching up.",
            "never_imported": never_imported,
            "delta": _int(delta_never),
        })
    elif never_imported >= 400:
        issues.append({
            "code": "never_imported",
            "severity": "watch",
            "summary": f"{never_imported} recent Radarr adds still have no file. Bounded recovery should keep picking at this.",
            "never_imported": never_imported,
        })

    skip_streak = _int(metrics.get("acquire_skip_streak"))
    active = _int(metrics.get("downloads_active"))
    last_decision = str(metrics.get("last_acquire_decision") or "")
    skip_counts = metrics.get("skip_counts") or {}
    if skip_streak >= 3 and active == 0 and last_decision.startswith("skipped"):
        top_skip = max(skip_counts.items(), key=lambda item: _int(item[1]), default=("unknown", 0))
        issues.append({
            "code": "acquire_idle",
            "severity": "attention",
            "summary": f"Acquire skipped {skip_streak} cycles in a row with an empty download queue. Dominant skip: {top_skip[0]}={top_skip[1]}.",
            "skip_streak": skip_streak,
            "skip_counts": skip_counts,
            "last_decision": last_decision,
        })

    plex_refresh = _int(metrics.get("plex_refresh_pending"))
    if plex_refresh >= 5:
        issues.append({
            "code": "plex_refresh_backlog",
            "severity": "attention",
            "summary": f"{plex_refresh} Plex library refreshes are queued. Indexing will lag until this drains.",
            "pending": plex_refresh,
        })

    retries = _int(metrics.get("pending_search_retries"))
    if retries >= 3:
        issues.append({
            "code": "search_retry_pile",
            "severity": "watch",
            "summary": f"{retries} search retries are waiting. A stuck title can occupy recovery slots.",
            "pending": retries,
        })

    watch_titles = metrics.get("watch_titles")
    if watch_titles is not None and _int(watch_titles) < 50:
        issues.append({
            "code": "watch_ledger_thin",
            "severity": "watch",
            "summary": f"Watch ledger only has {watch_titles} titles. Taste scoring is still incomplete until Plex history finishes syncing.",
            "watch_titles": _int(watch_titles),
        })

    av1 = _int(metrics.get("av1_count"))
    if av1 > 0:
        issues.append({
            "code": "av1_unaccelerated",
            "severity": "watch",
            "summary": f"{av1} AV1 files are advisory upgrades for the Tab S9 FE. Do not treat this as corruption.",
            "av1_count": av1,
        })

    series_missing = _int(metrics.get("series_path_missing"))
    if series_missing:
        issues.append({
            "code": "series_path_missing",
            "severity": "watch",
            "summary": f"{series_missing} series paths are missing on disk.",
            "series_path_missing": series_missing,
        })

    return issues


def _reconcile_payload(details_json: str) -> dict[str, Any]:
    payload = _json(details_json)
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict) and "movies" not in payload:
        payload = payload["result"]
    return payload if isinstance(payload, dict) else {}


def collect_ops_metrics(plane: Any) -> dict[str, Any]:
    store = plane.store
    status = store.status()
    services = status.get("services") or []
    unhealthy = [item.get("service") for item in services if item.get("status") != "healthy"]
    worker = status.get("worker") or {}
    metrics: dict[str, Any] = {
        "worker_healthy": bool(worker.get("healthy")),
        "unhealthy_services": [name for name in unhealthy if name],
        "emergency_stop": bool(store.is_emergency_stop()) if hasattr(store, "is_emergency_stop") else False,
        "downloads_active": 0,
        "sab_error": None,
        "without_file": 0,
        "file_not_indexed": 0,
        "never_imported": 0,
        "series_path_missing": 0,
        "av1_count": 0,
        "plex_refresh_pending": 0,
        "pending_search_retries": 0,
        "acquire_skip_streak": 0,
        "last_acquire_decision": "",
        "skip_counts": {},
        "watch_titles": None,
    }

    probe_timeout = float(__import__("os").environ.get("CINESWARM_PROBE_TIMEOUT", "5"))
    planner = getattr(plane, "planner", None)
    if planner and hasattr(planner, "global_queue_pressure"):
        try:
            limit = max(1, int(__import__("os").environ.get("CINESWARM_AUTO_MAX_CONCURRENT_DOWNLOADS", "4") or 4))
            pressure = planner.global_queue_pressure(limit, timeout=probe_timeout)
            metrics["downloads_active"] = _int(pressure.get("active"))
            sab = pressure.get("sabnzbd") or {}
            metrics["sab_error"] = sab.get("error")
        except Exception as exc:
            metrics["sab_error"] = str(exc)

    try:
        with store.lock, store._connect() as connection:
            rows = connection.execute(
                "SELECT details_json, created_at FROM audit_events WHERE action='reconcile_library' ORDER BY id DESC LIMIT 2"
            ).fetchall()
    except Exception:
        rows = []
    current: dict[str, Any] = {}
    previous: dict[str, Any] = {}
    if rows:
        current = _reconcile_payload(rows[0]["details_json"] if not isinstance(rows[0], tuple) else rows[0][0])
        if len(rows) > 1:
            previous = _reconcile_payload(rows[1]["details_json"] if not isinstance(rows[1], tuple) else rows[1][0])
    movies = current.get("movies") or {}
    prev_movies = previous.get("movies") or {}
    series = current.get("series") or {}
    metrics["without_file"] = _int(movies.get("without_file"))
    metrics["file_not_indexed"] = _int(movies.get("file_not_indexed"))
    metrics["series_path_missing"] = _int(series.get("path_missing"))
    if prev_movies:
        metrics["delta_without_file"] = metrics["without_file"] - _int(prev_movies.get("without_file"))
        metrics["delta_file_not_indexed"] = metrics["file_not_indexed"] - _int(prev_movies.get("file_not_indexed"))

    try:
        gaps = plane.never_imported_movies(added_since_days=None, limit=5)
        metrics["never_imported"] = _int(gaps.get("count"))
        prev_never = _int((prev_movies.get("never_imported") if prev_movies else None) or 0)
        if prev_movies and prev_movies.get("never_imported") is not None:
            metrics["delta_never_imported"] = metrics["never_imported"] - prev_never
    except Exception:
        pass
    try:
        shield = plane.probe_transcode_shield()
        metrics["av1_count"] = _int(shield.get("non_compliant_count"))
    except Exception:
        pass

    try:
        metrics["plex_refresh_pending"] = len(store.pending_tasks("plex_library_refresh", limit=50))
        metrics["pending_search_retries"] = len(store.pending_tasks("radarr_search_retry_request", limit=50)) + len(
            store.pending_tasks("sonarr_search_retry_request", limit=50)
        )
    except Exception:
        pass

    try:
        with store.lock, store._connect() as connection:
            decisions = connection.execute(
                "SELECT decision, reasons_json FROM decision_log WHERE category='autonomous_acquisition' ORDER BY created_at DESC LIMIT 12"
            ).fetchall()
        streak = 0
        for row in decisions:
            decision = row["decision"] if not isinstance(row, tuple) else row[0]
            if str(decision).startswith("skipped"):
                streak += 1
                if streak == 1:
                    reasons = _json(row["reasons_json"] if not isinstance(row, tuple) else row[1])
                    metrics["skip_counts"] = reasons.get("skip_counts") or reasons.get("skips") or {}
                    metrics["last_acquire_decision"] = str(decision)
            else:
                if streak == 0:
                    metrics["last_acquire_decision"] = str(decision)
                break
        metrics["acquire_skip_streak"] = streak
    except Exception:
        pass

    brain = getattr(plane, "library_brain", None)
    if brain is not None:
        try:
            coverage = brain.coverage()
            metrics["watch_titles"] = _int(coverage.get("watch_titles"))
        except Exception:
            pass
    return metrics


def persist_pulse(control_db: str, report: dict[str, Any]) -> None:
    with sqlite3.connect(control_db) as connection:
        connection.executescript(PULSE_SCHEMA)
        connection.execute(
            "INSERT INTO ops_pulse_history (generated_at, severity, issues_json, metrics_json) VALUES (?, ?, ?, ?)",
            (
                report.get("generated_at") or now(),
                report.get("severity") or "ok",
                json.dumps(report.get("issues") or [], ensure_ascii=False),
                json.dumps(report.get("metrics") or {}, ensure_ascii=False),
            ),
        )
        connection.execute(
            "DELETE FROM ops_pulse_history WHERE generated_at < ?",
            ((datetime.now(timezone.utc) - timedelta(days=14)).isoformat(timespec="seconds"),),
        )


def run_ops_pulse(plane: Any, persist: bool = True) -> dict[str, Any]:
    metrics = collect_ops_metrics(plane)
    issues = classify_issues(metrics)
    report = {
        "severity": overall_severity(issues),
        "issues": issues,
        "metrics": {
            key: metrics.get(key)
            for key in (
                "without_file",
                "file_not_indexed",
                "never_imported",
                "delta_without_file",
                "delta_file_not_indexed",
                "delta_never_imported",
                "downloads_active",
                "acquire_skip_streak",
                "last_acquire_decision",
                "plex_refresh_pending",
                "watch_titles",
                "av1_count",
                "sab_error",
            )
        },
        "headline": (issues[0]["summary"] if issues else "No incoming household issues ranked above ok."),
        "generated_at": now(),
    }
    if persist:
        try:
            persist_pulse(plane.store.path, report)
        except Exception:
            report["persist_error"] = True
    return report
