#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTROL_DB = os.environ.get("CINESWARM_CONTROL_DB", os.path.join(BASE_DIR, "cineswarm_control.db"))
CATALOG_DB = os.environ.get("CINESWARM_CATALOG_DB", os.path.join(BASE_DIR, "cineswarm_catalog.db"))
PRESERVATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS preservation_scans (
    scan_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    catalog_db TEXT NOT NULL,
    policy_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);
CREATE TABLE IF NOT EXISTS preservation_files (
    id INTEGER PRIMARY KEY,
    scan_id TEXT NOT NULL,
    catalog_item_id INTEGER,
    logical_path TEXT NOT NULL,
    physical_path TEXT,
    size INTEGER,
    mtime_ns INTEGER,
    device INTEGER,
    checksum_sha256 TEXT,
    checksum_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error TEXT,
    UNIQUE(scan_id, logical_path),
    FOREIGN KEY(scan_id) REFERENCES preservation_scans(scan_id)
);
CREATE INDEX IF NOT EXISTS idx_preservation_files_path ON preservation_files(logical_path, id);
CREATE TABLE IF NOT EXISTS preservation_mount_health (
    id INTEGER PRIMARY KEY,
    scan_id TEXT NOT NULL,
    mount_path TEXT NOT NULL,
    mounted INTEGER NOT NULL,
    filesystem TEXT,
    source TEXT,
    total_bytes INTEGER,
    used_bytes INTEGER,
    free_bytes INTEGER,
    error TEXT,
    UNIQUE(scan_id, mount_path),
    FOREIGN KEY(scan_id) REFERENCES preservation_scans(scan_id)
);
CREATE TABLE IF NOT EXISTS preservation_storage_events (
    id INTEGER PRIMARY KEY,
    scan_id TEXT NOT NULL,
    event_at TEXT,
    message TEXT NOT NULL,
    FOREIGN KEY(scan_id) REFERENCES preservation_scans(scan_id)
);
CREATE TABLE IF NOT EXISTS database_maintenance_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);
"""
BACKUP_RE = re.compile(r"^cineswarm-(control|catalog)-\d{8}T\d{6}Z-[0-9a-f]{8}\.sqlite3$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def csv_paths(value: str) -> list[str]:
    return [os.path.abspath(os.path.expanduser(item.strip())) for item in value.split(",") if item.strip()]


def configured_branches(value: str | None = None) -> list[str]:
    value = value if value is not None else os.environ.get("CINESWARM_PRESERVATION_BRANCHES", "/mnt/pool/disk*")
    paths: list[str] = []
    for pattern in csv_paths(value):
        matches = glob.glob(pattern)
        paths.extend(matches or [pattern])
    return sorted(dict.fromkeys(os.path.abspath(path) for path in paths))


def mapped_path(path: str, mappings: str | None = None) -> str:
    mappings = mappings if mappings is not None else os.environ.get("CINESWARM_PATH_MAP", "/media=/mnt/media,/data=/mnt/media")
    for mapping in mappings.split(","):
        if "=" not in mapping:
            continue
        source, target = (part.rstrip("/") for part in mapping.split("=", 1))
        if path == source or path.startswith(source + "/"):
            return target + path[len(source):]
    return path


def resolve_physical_path(path: str, union_roots: Iterable[str], branches: Iterable[str]) -> str | None:
    logical = os.path.abspath(path)
    candidates: list[str] = []
    for root in union_roots:
        root = os.path.abspath(root)
        try:
            relative = os.path.relpath(logical, root)
        except ValueError:
            continue
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            continue
        for branch in branches:
            candidate = os.path.join(os.path.abspath(branch), relative)
            if os.path.isfile(candidate):
                candidates.append(candidate)
    if candidates:
        logical_stat = None
        try:
            logical_stat = os.stat(logical)
        except OSError:
            pass
        if logical_stat:
            same = [item for item in candidates if os.stat(item).st_ino == logical_stat.st_ino and os.stat(item).st_dev == logical_stat.st_dev]
            if same:
                return same[0]
        return candidates[0]
    return logical if os.path.isfile(logical) else None


def streaming_sha256(path: str, byte_limit: int = 0, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    digest = hashlib.sha256()
    consumed = 0
    with open(path, "rb") as media:
        while byte_limit <= 0 or consumed < byte_limit:
            requested = chunk_size if byte_limit <= 0 else min(chunk_size, byte_limit - consumed)
            block = media.read(requested)
            if not block:
                break
            digest.update(block)
            consumed += len(block)
    return digest.hexdigest(), consumed


def mount_info(path: str) -> tuple[str | None, str | None]:
    target = os.path.abspath(path)
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as mountinfo:
            for line in mountinfo:
                left, right = line.rstrip().split(" - ", 1)
                fields = left.split()
                if fields[4].replace("\\040", " ") == target:
                    details = right.split()
                    return details[0], details[1].replace("\\040", " ")
    except (OSError, ValueError, IndexError):
        pass
    return None, None


def collect_mount_health(mounts: Iterable[str]) -> list[dict[str, Any]]:
    results = []
    for path in mounts:
        path = os.path.abspath(path)
        mounted = os.path.ismount(path)
        filesystem, source = mount_info(path) if mounted else (None, None)
        item: dict[str, Any] = {"mount_path": path, "mounted": mounted, "filesystem": filesystem, "source": source, "total_bytes": None, "used_bytes": None, "free_bytes": None, "error": None}
        if mounted:
            try:
                usage = shutil.disk_usage(path)
                item.update(total_bytes=usage.total, used_bytes=usage.used, free_bytes=usage.free)
            except OSError as exc:
                item["error"] = str(exc)
        else:
            item["error"] = "not mounted"
        results.append(item)
    return results


def collect_storage_events(since_minutes: int = 10080, limit: int = 100) -> list[dict[str, str]]:
    since_minutes = max(1, min(since_minutes, 43200))
    limit = max(1, min(limit, 500))
    try:
        result = subprocess.run(
            ["journalctl", "--kernel", "--since", f"-{since_minutes} minutes", "--no-pager", "--output", "short-iso"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    pattern = re.compile(r"(?:I/O error|Buffer I/O|blk_update_request|critical target error|USB disconnect|reset SuperSpeed|XFS.*(?:error|corrupt|shutdown))", re.IGNORECASE)
    events = []
    for line in result.stdout.splitlines():
        if not pattern.search(line):
            continue
        timestamp, _, message = line.partition(" ")
        events.append({"event_at": timestamp[:64], "message": message[-2000:]})
    return events[-limit:]


def _catalog_candidates(catalog_db: str, roots: list[str], limit: int) -> list[tuple[int | None, str]]:
    candidates: list[tuple[int | None, str]] = []
    if os.path.isfile(catalog_db):
        with sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "catalog_items" in tables:
                for item_id, path in connection.execute("SELECT id, path FROM catalog_items WHERE present=1 AND path IS NOT NULL AND path<>'' ORDER BY id"):
                    candidates.append((item_id, mapped_path(path)))
    candidates.extend((None, root) for root in roots)
    files: list[tuple[int | None, str]] = []
    seen: set[str] = set()
    for item_id, path in candidates:
        path = os.path.abspath(path)
        if os.path.isfile(path) or not os.path.exists(path):
            if path not in seen:
                files.append((item_id, path))
                seen.add(path)
        elif os.path.isdir(path):
            for directory, names, filenames in os.walk(path):
                names.sort()
                for filename in sorted(filenames):
                    candidate = os.path.join(directory, filename)
                    if candidate not in seen and os.path.isfile(candidate):
                        files.append((item_id, candidate))
                        seen.add(candidate)
                        if len(files) >= limit:
                            return files
        if len(files) >= limit:
            break
    return files[:limit]


def initialize_schema(control_db: str) -> None:
    with sqlite3.connect(control_db) as connection:
        connection.executescript(PRESERVATION_SCHEMA)


def preservation_scan(control_db: str = CONTROL_DB, catalog_db: str = CATALOG_DB, roots: list[str] | None = None, union_roots: list[str] | None = None, branches: list[str] | None = None, mounts: list[str] | None = None, sample_limit: int = 50, checksum_bytes: int = 0, checksum_max_size: int = 0) -> dict[str, Any]:
    roots = roots or []
    union_roots = union_roots or roots
    branches = branches or []
    mounts = mounts if mounts is not None else branches
    sample_limit = max(1, min(sample_limit, 1000))
    checksum_bytes = max(0, min(checksum_bytes, 1024 * 1024 * 1024))
    if checksum_bytes == 0 and checksum_max_size <= 0:
        checksum_bytes = 16 * 1024 * 1024
    initialize_schema(control_db)
    scan_id = str(uuid.uuid4())
    policy = {"roots": roots, "union_roots": union_roots, "branches": branches, "mounts": mounts, "sample_limit": sample_limit, "checksum_bytes": max(0, checksum_bytes), "checksum_max_size": max(0, checksum_max_size)}
    with sqlite3.connect(control_db) as connection:
        connection.execute("INSERT INTO preservation_scans(scan_id, started_at, status, catalog_db, policy_json) VALUES (?, ?, 'running', ?, ?)", (scan_id, now(), catalog_db, json.dumps(policy, sort_keys=True)))
        previous = {row[0]: row[1:] for row in connection.execute("SELECT logical_path, size, mtime_ns, checksum_sha256, checksum_bytes FROM preservation_files WHERE id IN (SELECT MAX(id) FROM preservation_files GROUP BY logical_path)")}
    records = []
    try:
        current = _catalog_candidates(catalog_db, roots, sample_limit)
        selected = {path for _, path in current}
        disappeared = [(None, path) for path in previous if path not in selected and not resolve_physical_path(path, union_roots, branches)]
        candidates = (disappeared + current)[:sample_limit]
        for item_id, logical_path in candidates:
            record: dict[str, Any] = {"catalog_item_id": item_id, "logical_path": logical_path, "physical_path": None, "size": None, "mtime_ns": None, "device": None, "checksum_sha256": None, "checksum_bytes": 0, "status": "missing", "error": None}
            physical = resolve_physical_path(logical_path, union_roots, branches)
            if physical:
                try:
                    stat = os.stat(physical)
                    record.update(physical_path=physical, size=stat.st_size, mtime_ns=stat.st_mtime_ns, device=stat.st_dev)
                    if checksum_max_size > 0 and stat.st_size > checksum_max_size:
                        record["status"] = "checksum_skipped"
                    else:
                        checksum, consumed = streaming_sha256(physical, max(0, checksum_bytes))
                        record.update(checksum_sha256=checksum, checksum_bytes=consumed)
                        old = previous.get(logical_path)
                        if not old:
                            record["status"] = "new"
                        elif old[0] != stat.st_size or old[1] != stat.st_mtime_ns:
                            record["status"] = "changed"
                        elif old[2] and old[3] == consumed and old[2] != checksum:
                            record["status"] = "checksum_mismatch"
                        else:
                            record["status"] = "ok"
                except OSError as exc:
                    record.update(status="error", error=str(exc))
            records.append(record)
        health = collect_mount_health(mounts)
        storage_events = collect_storage_events()
        counts: dict[str, int] = {}
        for record in records:
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        summary = {"files": len(records), "statuses": counts, "mounts": len(health), "missing_mounts": sum(not item["mounted"] for item in health), "storage_events": len(storage_events)}
        with sqlite3.connect(control_db) as connection:
            connection.executemany("INSERT INTO preservation_files(scan_id, catalog_item_id, logical_path, physical_path, size, mtime_ns, device, checksum_sha256, checksum_bytes, status, error) VALUES (:scan_id, :catalog_item_id, :logical_path, :physical_path, :size, :mtime_ns, :device, :checksum_sha256, :checksum_bytes, :status, :error)", ({"scan_id": scan_id, **record} for record in records))
            connection.executemany("INSERT INTO preservation_mount_health(scan_id, mount_path, mounted, filesystem, source, total_bytes, used_bytes, free_bytes, error) VALUES (:scan_id, :mount_path, :mounted, :filesystem, :source, :total_bytes, :used_bytes, :free_bytes, :error)", ({"scan_id": scan_id, **item} for item in health))
            connection.executemany("INSERT INTO preservation_storage_events(scan_id, event_at, message) VALUES (:scan_id, :event_at, :message)", ({"scan_id": scan_id, **item} for item in storage_events))
            connection.execute("UPDATE preservation_scans SET finished_at=?, status='completed', summary_json=? WHERE scan_id=?", (now(), json.dumps(summary, sort_keys=True), scan_id))
        return {"status": "completed", "scan_id": scan_id, **summary}
    except Exception as exc:
        with sqlite3.connect(control_db) as connection:
            connection.execute("UPDATE preservation_scans SET finished_at=?, status='failed', error=? WHERE scan_id=?", (now(), str(exc)[:1000], scan_id))
        raise


def check_database(path: str, pragma: str = "quick_check") -> list[str]:
    if pragma not in {"quick_check", "integrity_check"}:
        raise ValueError("Unsupported integrity check")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        return [str(row[0]) for row in connection.execute(f"PRAGMA {pragma}")]


def online_backup(source: str, destination: str) -> dict[str, Any]:
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    if os.path.exists(destination):
        raise FileExistsError(destination)
    try:
        with sqlite3.connect(source, timeout=30) as source_db:
            try:
                source_db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
            except sqlite3.DatabaseError:
                pass
            with sqlite3.connect(destination) as backup_db:
                source_db.backup(backup_db)
        checks = check_database(destination, "quick_check")
        if checks != ["ok"]:
            raise sqlite3.DatabaseError("Backup integrity check failed: " + "; ".join(checks))
        return {"source": source, "backup": destination, "quick_check": checks, "size": os.path.getsize(destination)}
    except Exception:
        if os.path.isfile(destination):
            os.unlink(destination)
        raise


def rotate_backups(backup_dir: str, retention: int) -> list[str]:
    retention = max(0, retention)
    if not os.path.isdir(backup_dir):
        return []
    generated = [entry for entry in os.scandir(backup_dir) if entry.is_file(follow_symlinks=False) and BACKUP_RE.fullmatch(entry.name)]
    generated.sort(key=lambda entry: (entry.stat().st_mtime_ns, entry.name), reverse=True)
    removed = []
    for entry in generated[retention:]:
        os.unlink(entry.path)
        removed.append(entry.path)
    return removed


def database_maintenance(control_db: str = CONTROL_DB, catalog_db: str = CATALOG_DB, backup_dir: str | None = None, retention: int = 7, integrity_check: bool = False) -> dict[str, Any]:
    backup_dir = os.path.abspath(backup_dir or os.path.join(BASE_DIR, "backups"))
    initialize_schema(control_db)
    run_id = str(uuid.uuid4())
    with sqlite3.connect(control_db) as connection:
        connection.execute("INSERT INTO database_maintenance_runs(run_id, started_at, status) VALUES (?, ?, 'running')", (run_id, now()))
    results = []
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for label, path in (("control", control_db), ("catalog", catalog_db)):
            if not os.path.isfile(path):
                results.append({"source": path, "status": "missing"})
                continue
            checks = check_database(path, "integrity_check" if integrity_check else "quick_check")
            if checks != ["ok"]:
                raise sqlite3.DatabaseError(f"{label} integrity check failed: {'; '.join(checks)}")
            destination = os.path.join(backup_dir, f"cineswarm-{label}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3")
            result = online_backup(path, destination)
            result["status"] = "backed_up"
            results.append(result)
        removed = rotate_backups(backup_dir, retention)
        offsite = sync_offsite_vault(backup_dir=backup_dir)
        details = {"databases": results, "removed": removed, "backup_dir": backup_dir, "offsite": offsite}
        with sqlite3.connect(control_db) as connection:
            connection.execute("UPDATE database_maintenance_runs SET finished_at=?, status='completed', details_json=? WHERE run_id=?", (now(), json.dumps(details, sort_keys=True), run_id))
        return {"status": "completed", "run_id": run_id, **details}
    except Exception as exc:
        with sqlite3.connect(control_db) as connection:
            connection.execute("UPDATE database_maintenance_runs SET finished_at=?, status='failed', error=? WHERE run_id=?", (now(), str(exc)[:1000], run_id))
        raise


def restore_database(backup: str, target: str, confirm_overwrite: bool = False) -> dict[str, Any]:
    checks = check_database(backup, "integrity_check")
    if checks != ["ok"]:
        raise sqlite3.DatabaseError("Backup integrity check failed: " + "; ".join(checks))
    target = os.path.abspath(target)
    if os.path.exists(target) and not confirm_overwrite:
        raise FileExistsError("Refusing to overwrite an existing database without --confirm-overwrite")
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        raise FileNotFoundError(parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".cineswarm-restore-", suffix=".sqlite3", dir=parent)
    os.close(descriptor)
    os.unlink(temporary)
    try:
        online_backup(backup, temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"status": "restored", "backup": os.path.abspath(backup), "target": target, "integrity_check": checks}


def sync_offsite_vault(backup_dir: str | None = None, vault_target: str | None = None) -> dict[str, Any]:
    """Sync database backups to offsite target (S3/GCS bucket, remote SSH, or secondary mount point)."""
    backup_dir = os.path.abspath(backup_dir or os.path.join(BASE_DIR, "backups"))
    vault_target = vault_target or os.environ.get("CINESWARM_OFFSITE_VAULT_TARGET", "")
    if not vault_target:
        return {"status": "skipped", "message": "CINESWARM_OFFSITE_VAULT_TARGET is not configured."}
    
    if not os.path.isdir(backup_dir):
        return {"status": "skipped", "message": f"Backup directory {backup_dir} does not exist."}

    if not vault_target.startswith(("s3://", "gs://")) and not os.path.isdir(vault_target):
        try:
            os.makedirs(vault_target, exist_ok=True)
        except OSError as exc:
            return {"status": "skipped", "vault_target": vault_target, "message": f"vault target missing: {exc}"}

    copied = []
    errors = []
    for entry in os.scandir(backup_dir):
        if entry.is_file() and BACKUP_RE.fullmatch(entry.name):
            try:
                if vault_target.startswith("s3://") or vault_target.startswith("gs://"):
                    cmd = ["gcloud", "storage", "cp", entry.path, f"{vault_target.rstrip('/')}/{entry.name}"]
                    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
                    copied.append(entry.name)
                elif os.path.isdir(vault_target):
                    dest = os.path.join(vault_target, entry.name)
                    shutil.copy2(entry.path, dest)
                    copied.append(entry.name)
            except Exception as exc:
                errors.append({"file": entry.name, "error": str(exc)})

    return {"status": "completed", "vault_target": vault_target, "copied_count": len(copied), "copied_files": copied, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="CineSwarm preservation and SQLite resilience tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    restore = subparsers.add_parser("restore")
    restore.add_argument("backup")
    restore.add_argument("target")
    restore.add_argument("--confirm-overwrite", action="store_true")
    offsite = subparsers.add_parser("offsite")
    offsite.add_argument("--target", default="")
    args = parser.parse_args()
    if args.command == "restore":
        print(json.dumps(restore_database(args.backup, args.target, args.confirm_overwrite), sort_keys=True))
    elif args.command == "offsite":
        print(json.dumps(sync_offsite_vault(vault_target=args.target), sort_keys=True))


if __name__ == "__main__":
    main()

