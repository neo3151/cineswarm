#!/usr/bin/env python3
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RADARR_DB = os.environ.get("RADARR_DB", "/home/neo/docker/radarr/config/radarr.db")
SONARR_DB = os.environ.get("SONARR_DB", "/home/neo/docker/sonarr/config/sonarr.db")
CATALOG_DB = os.environ.get("CINESWARM_CATALOG_DB", os.path.join(BASE_DIR, "cineswarm_catalog.db"))
OUTPUT_MD = os.environ.get("CINESWARM_OUTPUT_MD", os.path.join(BASE_DIR, "LATEST_VAULT_ADDITIONS.md"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_items (
    id INTEGER PRIMARY KEY,
    media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'series')),
    source TEXT NOT NULL CHECK (source IN ('radarr', 'sonarr')),
    source_id TEXT NOT NULL,
    source_native_id INTEGER NOT NULL,
    external_ids_json TEXT NOT NULL DEFAULT '{}',
    title TEXT NOT NULL,
    year INTEGER,
    genres_json TEXT NOT NULL DEFAULT '[]',
    monitored INTEGER NOT NULL DEFAULT 0,
    status TEXT,
    added TEXT,
    path TEXT,
    runtime INTEGER,
    overview TEXT,
    raw_json TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    present INTEGER NOT NULL DEFAULT 1,
    UNIQUE (source, media_type, source_id)
);
CREATE INDEX IF NOT EXISTS idx_catalog_items_current ON catalog_items (media_type, present);
CREATE INDEX IF NOT EXISTS idx_catalog_items_title ON catalog_items (title);
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    radarr_count INTEGER NOT NULL DEFAULT 0,
    sonarr_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_value(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, (list, dict)) else default
    except (TypeError, json.JSONDecodeError):
        return default


def json_text(value, default):
    return json.dumps(value if value is not None else default, ensure_ascii=False, sort_keys=True)


def read_database(path):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def stable_key(prefix, external_id, native_id):
    return f"{prefix}:{external_id}" if external_id not in (None, 0, "0", "") else f"native:{native_id}"


def load_radarr():
    with read_database(RADARR_DB) as conn:
        conn.row_factory = sqlite3.Row
        metadata = {row["Id"]: dict(row) for row in conn.execute("SELECT * FROM MovieMetadata")}
        records = []
        for movie_row in conn.execute("SELECT * FROM Movies ORDER BY Id"):
            movie = dict(movie_row)
            movie_metadata = metadata.get(movie["MovieMetadataId"], {})
            genres = json_value(movie_metadata.get("Genres"), [])
            records.append({
                "media_type": "movie",
                "source": "radarr",
                "source_id": stable_key("tmdb", movie_metadata.get("TmdbId"), movie["Id"]),
                "source_native_id": movie["Id"],
                "external_ids": {"tmdb": movie_metadata.get("TmdbId"), "imdb": movie_metadata.get("ImdbId")},
                "title": movie_metadata.get("Title") or f"Radarr movie {movie['Id']}",
                "year": movie_metadata.get("Year"),
                "genres": genres,
                "monitored": int(bool(movie.get("Monitored"))),
                "status": movie_metadata.get("Status"),
                "added": movie.get("Added"),
                "path": movie.get("Path"),
                "runtime": movie_metadata.get("Runtime"),
                "overview": movie_metadata.get("Overview"),
                "raw": {"movie": movie, "metadata": movie_metadata},
            })
        return records


def load_sonarr():
    with read_database(SONARR_DB) as conn:
        conn.row_factory = sqlite3.Row
        records = []
        for series_row in conn.execute("SELECT * FROM Series ORDER BY Id"):
            series = dict(series_row)
            records.append({
                "media_type": "series",
                "source": "sonarr",
                "source_id": stable_key("tvdb", series.get("TvdbId"), series["Id"]),
                "source_native_id": series["Id"],
                "external_ids": {
                    "tvdb": series.get("TvdbId"),
                    "tmdb": series.get("TmdbId"),
                    "imdb": series.get("ImdbId"),
                    "tvmaze": series.get("TvMazeId"),
                },
                "title": series["Title"],
                "year": series.get("Year"),
                "genres": json_value(series.get("Genres"), []),
                "monitored": int(bool(series.get("Monitored"))),
                "status": series.get("Status"),
                "added": series.get("Added"),
                "path": series.get("Path"),
                "runtime": series.get("Runtime"),
                "overview": series.get("Overview"),
                "raw": {"series": series},
            })
        return records


def upsert_records(conn, records, seen_at):
    for record in records:
        conn.execute(
            """
            INSERT INTO catalog_items (
                media_type, source, source_id, source_native_id, external_ids_json, title, year,
                genres_json, monitored, status, added, path, runtime, overview,
                raw_json, first_seen, last_seen, present
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(source, media_type, source_id) DO UPDATE SET
                source_native_id=excluded.source_native_id,
                external_ids_json=excluded.external_ids_json,
                title=excluded.title, year=excluded.year,
                genres_json=excluded.genres_json, monitored=excluded.monitored,
                status=excluded.status, added=excluded.added, path=excluded.path,
                runtime=excluded.runtime, overview=excluded.overview,
                raw_json=excluded.raw_json, last_seen=excluded.last_seen,
                present=1
            """,
            (
                record["media_type"], record["source"], record["source_id"], record["source_native_id"],
                json_text(record["external_ids"], {}), record["title"], record["year"],
                json_text(record["genres"], []), record["monitored"], record["status"],
                record["added"], record["path"], record["runtime"], record["overview"],
                json_text(record["raw"], {}), seen_at, seen_at,
            ),
        )


def mark_source_absent(conn, source, media_type, source_ids):
    if not source_ids:
        conn.execute(
            "UPDATE catalog_items SET present=0 WHERE source=? AND media_type=?",
            (source, media_type),
        )
        return
    placeholders = ",".join("?" for _ in source_ids)
    conn.execute(
        f"UPDATE catalog_items SET present=0 WHERE source=? AND media_type=? AND source_id NOT IN ({placeholders})",
        (source, media_type, *source_ids),
    )


def summarize(rows):
    genres = Counter()
    decades = Counter()
    for row in rows:
        for genre in json_value(row["genres_json"], []):
            genres[genre] += 1
        if row["year"]:
            decades[f"{(row['year'] // 10) * 10}s"] += 1
    recent = sorted(rows, key=lambda row: (row["added"] or "", row["title"]), reverse=True)[:15]
    return {
        "count": len(rows),
        "monitored": sum(row["monitored"] for row in rows),
        "genres": genres.most_common(8),
        "decades": decades.most_common(6),
        "recent": [f"{row['title']} ({row['year'] or 'n.d.'})" for row in recent],
    }


def format_summary(label, summary):
    genres = ", ".join(f"{name} ({count})" for name, count in summary["genres"]) or "none"
    decades = ", ".join(f"{name} ({count})" for name, count in summary["decades"]) or "none"
    recent = ", ".join(summary["recent"]) or "none"
    return f"""## {label}
- **Complete records currently present:** {summary['count']}
- **Monitored:** {summary['monitored']}
- **Genre distribution:** {genres}
- **Release eras:** {decades}
- **Most recently added:** {recent}
"""


def write_snapshot(rows_by_type, run_errors):
    summaries = {media_type: summarize(rows) for media_type, rows in rows_by_type.items()}
    total = sum(summary["count"] for summary in summaries.values())
    error_text = "\n- **Source warnings:** " + "; ".join(run_errors) if run_errors else ""
    prompt_snippet = f"""```text
[CINESWARM FULL COLLECTION UPDATE]
- Complete current catalog: {total} records
- Movies tracked separately: {summaries['movie']['count']}
- TV series tracked separately: {summaries['series']['count']}
- Instruction: Use the movie section for movie requests and the series section for show requests. Never treat a TV series as a film.
```"""
    full_md = f"""# CineSwarm Complete Collection Snapshot

This file summarizes the durable catalog in `{os.path.basename(CATALOG_DB)}`. The SQLite catalog is the complete source of truth; this markdown is a human-readable overview.

### Copy & Paste Context

{prompt_snippet}

---
{format_summary('Movies (Radarr)', summaries['movie'])}
---
{format_summary('TV Series (Sonarr)', summaries['series'])}
{error_text}
"""
    os.makedirs(os.path.dirname(OUTPUT_MD), exist_ok=True)
    with open(OUTPUT_MD, "w", encoding="utf-8") as output:
        output.write(full_md)


def migrate_catalog(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(catalog_items)")}
    if "source_native_id" not in columns:
        conn.execute("ALTER TABLE catalog_items ADD COLUMN source_native_id INTEGER")
    rows = conn.execute(
        "SELECT id, source, source_id, raw_json FROM catalog_items WHERE source_native_id IS NULL"
    ).fetchall()
    for row in rows:
        raw = json.loads(row[3])
        if row[1] == "radarr":
            native_id = raw["movie"]["Id"]
            external_id = raw.get("metadata", {}).get("TmdbId")
            source_id = stable_key("tmdb", external_id, native_id)
        else:
            native_id = raw["series"]["Id"]
            external_id = raw["series"].get("TvdbId")
            source_id = stable_key("tvdb", external_id, native_id)
        conn.execute(
            "UPDATE catalog_items SET source_id=?, source_native_id=? WHERE id=?",
            (source_id, native_id, row[0]),
        )


def sync_catalog():
    started_at = now()
    errors = []
    radarr_records = []
    sonarr_records = []
    radarr_available = False
    sonarr_available = False
    try:
        radarr_records = load_radarr()
        radarr_available = True
    except Exception as exc:
        errors.append(f"Radarr unavailable: {exc}")
    try:
        sonarr_records = load_sonarr()
        sonarr_available = True
    except Exception as exc:
        errors.append(f"Sonarr unavailable: {exc}")

    os.makedirs(os.path.dirname(CATALOG_DB) or ".", exist_ok=True)
    with sqlite3.connect(CATALOG_DB) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        migrate_catalog(conn)
        run_id = conn.execute(
            "INSERT INTO sync_runs (started_at, status) VALUES (?, ?)",
            (started_at, "running"),
        ).lastrowid
        try:
            seen_at = now()
            if radarr_available:
                upsert_records(conn, radarr_records, seen_at)
                mark_source_absent(conn, "radarr", "movie", [record["source_id"] for record in radarr_records])
            if sonarr_available:
                upsert_records(conn, sonarr_records, seen_at)
                mark_source_absent(conn, "sonarr", "series", [record["source_id"] for record in sonarr_records])
            conn.execute(
                "UPDATE sync_runs SET finished_at=?, status=?, radarr_count=?, sonarr_count=?, error=? WHERE id=?",
                (now(), "warning" if errors else "success", len(radarr_records), len(sonarr_records), "; ".join(errors) or None, run_id),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.execute(
                "UPDATE sync_runs SET finished_at=?, status=?, error=? WHERE id=?",
                (now(), "failed", str(exc), run_id),
            )
            conn.commit()
            raise
        current_rows = {
            "movie": conn.execute("SELECT * FROM catalog_items WHERE media_type='movie' AND present=1 ORDER BY title").fetchall(),
            "series": conn.execute("SELECT * FROM catalog_items WHERE media_type='series' AND present=1 ORDER BY title").fetchall(),
        }
    write_snapshot(current_rows, errors)
    print(f"Synced {len(current_rows['movie'])} movies and {len(current_rows['series'])} series into {CATALOG_DB}")
    print(f"Snapshot written to {OUTPUT_MD}")
    if errors:
        print("Warnings: " + "; ".join(errors), file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fully sync Radarr movies and Sonarr series into CineSwarm.")
    parser.parse_args()
    sync_catalog()
