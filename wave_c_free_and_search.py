#!/usr/bin/env python3
"""Wave C follow-up: free short folders as own movies, then search selected longs.
Never print secrets.
"""
from __future__ import annotations
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta

ENV_PATH = "/home/neo/workspace/cineswarm/.env"
REPORT = "/tmp/cs-recon/WAVE_C_FREE_AND_SEARCH.md"
QUALITY_PROFILE_ID = 4  # HD-1080p
ROOT = "/media/Movies"

# Long titles: unlink file, retarget to correct expected folder
LONGS = [
    {
        "id": 16,
        "title": "Harry Potter and the Deathly Hallows: Part 1",
        "year": 2010,
        "wrong_folder": "1 (2013)",
        "expected_path": f"{ROOT}/Harry Potter and the Deathly Hallows - Part 1 (2010)",
        "search": True,
        "short": {"label": "1 (2013)", "title": "1", "year": 2013, "tmdbId": 217316, "folder": f"{ROOT}/1 (2013)"},
    },
    {
        "id": 572,
        "title": "Harry Potter and the Half-Blood Prince",
        "year": 2009,
        "wrong_folder": "Blood (2012)",
        "expected_path": f"{ROOT}/Harry Potter and the Half-Blood Prince (2009)",
        "search": True,
        "short": {"label": "Blood (2012)", "title": "Blood", "year": 2012, "tmdbId": 136278, "folder": f"{ROOT}/Blood (2012)"},
    },
    {
        "id": 200,
        "title": "Alice in Wonderland",
        "year": 1951,
        "wrong_folder": "Alice (1990)",
        "expected_path": f"{ROOT}/Alice in Wonderland (1951)",
        "search": False,
        "short": {"label": "Alice (1990)", "title": "Alice", "year": 1990, "tmdbId": 8217, "folder": f"{ROOT}/Alice (1990)"},
    },
    {
        "id": 573,
        "title": "First Blood",
        "year": 1982,
        "wrong_folder": "Blood (2023)",
        "expected_path": f"{ROOT}/First Blood (1982)",
        "search": True,
        "short": {"label": "Blood (2023)", "title": "Blood", "year": 2023, "tmdbId": 746524, "folder": f"{ROOT}/Blood (2023)"},
    },
    {
        "id": 2094,
        "title": "The Lion King",
        "year": 1994,
        "wrong_folder": "Lion (2016)",
        "expected_path": f"{ROOT}/The Lion King (1994)",
        "search": True,
        "short": {"label": "Lion (2016)", "title": "Lion", "year": 2016, "tmdbId": 334543, "folder": f"{ROOT}/Lion (2016)"},
    },
    {
        "id": 623,
        "title": "Sunset Boulevard",
        "year": 1950,
        "wrong_folder": "Boulevard (2014)",
        "expected_path": f"{ROOT}/Sunset Boulevard (1950)",
        "search": False,
        "short": {"label": "Boulevard (2014)", "title": "Boulevard", "year": 2014, "tmdbId": 259963, "folder": f"{ROOT}/Boulevard (2014)"},
    },
    {
        "id": 480,
        "title": "What Lies Beneath",
        "year": 2000,
        "wrong_folder": "Beneath (2013)",
        "expected_path": f"{ROOT}/What Lies Beneath (2000)",
        "search": False,
        # duration ~89.9m; prefer fish/Chiller (191619, runtime 90) over miners (257874)
        "short": {"label": "Beneath (2013)", "title": "Beneath", "year": 2013, "tmdbId": 191619, "folder": f"{ROOT}/Beneath (2013)"},
    },
    {
        "id": 998,
        "title": "Death Race",
        "year": 2008,
        "wrong_folder": "Death Race 2050 (2017)",
        "expected_path": f"{ROOT}/Death Race (2008)",
        "search": False,
        "short": {"label": "Death Race 2050 (2017)", "title": "Death Race 2050", "year": 2017, "tmdbId": 401544, "folder": f"{ROOT}/Death Race 2050 (2017)"},
    },
    {
        "id": 1049,
        "title": "Dirty Dancing",
        "year": 1987,
        "wrong_folder": "Dirty Dancing (2017)",
        "expected_path": f"{ROOT}/Dirty Dancing (1987)",
        "search": False,
        "short": {"label": "Dirty Dancing (2017)", "title": "Dirty Dancing", "year": 2017, "tmdbId": 444902, "folder": f"{ROOT}/Dirty Dancing (2017)"},
    },
]


def load_env(p=ENV_PATH):
    env = {}
    for line in Path(p).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()
RADARR_BASE = ENV["RADARR_URL"].rstrip("/")
RADARR_KEY = ENV["RADARR_API_KEY"]
PLEX_BASE = ENV["PLEX_URL"].rstrip("/")
PLEX_TOKEN = ENV["PLEX_TOKEN"]


def radarr(method, path, data=None, query=""):
    url = f"{RADARR_BASE}{path}"
    if query:
        url += ("&" if "?" in url else "?") + query
    body = None
    headers = {"X-Api-Key": RADARR_KEY, "Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")
        try:
            parsed = json.loads(err)
        except Exception:
            parsed = err
        return e.code, parsed


def log(msg):
    print(msg, flush=True)


def ct_now():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M CT")


def get_movie(mid):
    code, m = radarr("GET", f"/api/v3/movie/{mid}")
    if code != 200:
        raise RuntimeError(f"GET movie {mid}: {code} {m}")
    return m


def delete_moviefile(mfid):
    return radarr("DELETE", f"/api/v3/moviefile/{mfid}", query="deleteFiles=false")


def put_movie(m, move_files=False):
    q = f"moveFiles={'true' if move_files else 'false'}"
    return radarr("PUT", f"/api/v3/movie/{m['id']}", data=m, query=q)


def lookup_tmdb(tmdb_id):
    code, hits = radarr("GET", "/api/v3/movie/lookup", query=urllib.parse.urlencode({"term": f"tmdb:{tmdb_id}"}))
    if code != 200 or not hits:
        return code, None
    return code, hits[0]


def wait_has_file(mid, attempts=20, delay=3):
    last = None
    for _ in range(attempts):
        m = get_movie(mid)
        last = m
        if m.get("hasFile"):
            return m
        time.sleep(delay)
    return last


def rescan(mid):
    return radarr("POST", "/api/v3/command", data={"name": "RescanMovie", "movieId": mid})


def manual_import(folder, movie_id):
    q = urllib.parse.urlencode({"folder": folder, "filterExistingFiles": "false"})
    code, items = radarr("GET", "/api/v3/manualimport", query=q)
    if code != 200 or not items:
        return code, items, "no candidates"
    vids = []
    for it in items:
        path = it.get("path") or ""
        if Path(path).suffix.lower() not in {".mkv", ".mp4", ".avi", ".m4v", ".m2ts", ".ts"}:
            continue
        if not (path.startswith(folder.rstrip("/") + "/") or str(Path(path).parent) == folder.rstrip("/")):
            continue
        vids.append(it)
    if not vids:
        return code, items, "no video candidates in folder"
    files = []
    for it in vids:
        files.append({
            "path": it["path"],
            "folderName": it.get("folderName") or folder,
            "movieId": movie_id,
            "quality": it.get("quality") or {
                "quality": {"id": 7, "name": "Bluray-1080p"},
                "revision": {"version": 1, "real": 0, "isRepack": False},
            },
            "languages": it.get("languages") or [{"id": 1, "name": "English"}],
            "releaseGroup": it.get("releaseGroup") or "",
            "indexerFlags": it.get("indexerFlags") or 0,
        })
    # Prefer copy/move that keeps files in place — use 'auto' or 'copy'; Radarr often wants 'move' or 'copy'
    code2, resp = radarr("POST", "/api/v3/command", data={
        "name": "ManualImport",
        "files": files,
        "importMode": "copy",  # same volume; avoid deleting source unexpectedly
    })
    if code2 not in (200, 201, 202):
        # fallback move
        code2, resp = radarr("POST", "/api/v3/command", data={
            "name": "ManualImport",
            "files": files,
            "importMode": "move",
        })
    return code2, resp, f"imported {len(files)} file(s)"


def add_short(short):
    """Lookup + POST if needed + import until hasFile."""
    result = {
        "label": short["label"],
        "title": short["title"],
        "year": short["year"],
        "tmdbId": short["tmdbId"],
        "folder": short["folder"],
        "status": "?",
        "radarr_id": None,
        "hasFile": False,
        "notes": "",
    }
    code, lookup = lookup_tmdb(short["tmdbId"])
    if code != 200 or not lookup:
        result["status"] = "lookup_failed"
        result["notes"] = f"lookup code={code}"
        return result

    # Already in library?
    existing_id = lookup.get("id")
    if existing_id:
        m = get_movie(existing_id)
        result["radarr_id"] = existing_id
        # ensure path correct
        if m.get("path") != short["folder"]:
            m["path"] = short["folder"]
            m["folderName"] = short["folder"]
            m["monitored"] = True
            put_movie(m, move_files=False)
            m = get_movie(existing_id)
        if m.get("hasFile"):
            result["status"] = "already_in_library"
            result["hasFile"] = True
            result["notes"] = f"path={m.get('path')}"
            return result
        # need import
        mid = existing_id
    else:
        # Build add payload from lookup
        payload = dict(lookup)
        payload["path"] = short["folder"]
        payload["folderName"] = short["folder"]
        payload["qualityProfileId"] = QUALITY_PROFILE_ID
        payload["monitored"] = True
        payload["rootFolderPath"] = ROOT
        payload["minimumAvailability"] = payload.get("minimumAvailability") or "released"
        payload["addOptions"] = {"searchForMovie": False}
        # Clear id if present as None
        if "id" in payload and not payload["id"]:
            del payload["id"]
        code_add, added = radarr("POST", "/api/v3/movie", data=payload)
        if code_add not in (200, 201):
            result["status"] = "add_failed"
            result["notes"] = f"POST {code_add}: {str(added)[:300]}"
            return result
        mid = added["id"]
        result["radarr_id"] = mid
        result["notes"] = "added"

    # Rescan then manual import
    rescan(mid)
    time.sleep(4)
    m = wait_has_file(mid, attempts=8, delay=2)
    if m and m.get("hasFile"):
        result["status"] = "ok"
        result["hasFile"] = True
        result["radarr_id"] = mid
        result["notes"] = (result["notes"] + "; rescan imported").strip("; ")
        return result

    code_mi, resp_mi, note_mi = manual_import(short["folder"], mid)
    result["notes"] = (result["notes"] + f"; MI {code_mi} {note_mi}").strip("; ")
    time.sleep(4)
    rescan(mid)
    m = wait_has_file(mid, attempts=15, delay=3)
    if m and m.get("hasFile"):
        result["status"] = "ok"
        result["hasFile"] = True
        result["radarr_id"] = mid
    else:
        result["status"] = "added_but_no_file"
        result["hasFile"] = False
        result["radarr_id"] = mid
        if m:
            result["notes"] += f"; path={m.get('path')} hasFile=false"
    return result


def unlink_and_retarget(long_item):
    """Unlink movieFile, PUT expected long path, confirm hasFile=false."""
    mid = long_item["id"]
    out = {
        "id": mid,
        "title": long_item["title"],
        "year": long_item["year"],
        "wrong_folder": long_item["wrong_folder"],
        "expected_path": long_item["expected_path"],
        "search": long_item["search"],
        "status": "?",
        "hasFile": None,
        "path": None,
        "monitored": None,
        "notes": "",
        "mf_deleted": None,
    }
    m = get_movie(mid)
    out["notes"] = f"before path={m.get('path')} hasFile={m.get('hasFile')}"
    mf = m.get("movieFile") or {}
    mfid = mf.get("id")
    if mfid:
        code_d, resp_d = delete_moviefile(mfid)
        out["mf_deleted"] = mfid
        out["notes"] += f"; DELETE movieFile {mfid} -> {code_d}"
        if code_d not in (200, 201, 202, 204):
            out["status"] = "unlink_failed"
            out["notes"] += f" {str(resp_d)[:200]}"
            return out
    else:
        out["notes"] += "; no movieFile to delete"

    # Refresh and PUT path
    m = get_movie(mid)
    m["path"] = long_item["expected_path"]
    m["folderName"] = long_item["expected_path"]
    m["monitored"] = True
    # Keep quality as-is or bump? User said HD-1080p for adds; longs ready for search — leave QP or set 4
    # leave qualityProfileId unchanged unless missing
    code_p, resp_p = put_movie(m, move_files=False)
    out["notes"] += f"; PUT path -> {code_p}"
    if code_p not in (200, 202):
        out["status"] = "put_failed"
        out["notes"] += f" {str(resp_p)[:300]}"
        return out

    m = get_movie(mid)
    out["path"] = m.get("path")
    out["hasFile"] = m.get("hasFile")
    out["monitored"] = m.get("monitored")
    if m.get("hasFile"):
        out["status"] = "still_has_file"
    elif m.get("path") != long_item["expected_path"]:
        out["status"] = "path_mismatch"
    else:
        out["status"] = "ok"
    return out


def movies_search(mid):
    return radarr("POST", "/api/v3/command", data={"name": "MoviesSearch", "movieIds": [mid]})


def plex_refresh():
    try:
        url = f"{PLEX_BASE}/library/sections?X-Plex-Token={PLEX_TOKEN}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        dirs = data.get("MediaContainer", {}).get("Directory", [])
        refreshed = []
        for d in dirs:
            if d.get("type") != "movie":
                continue
            # Prefer Flix / movie libs that map to movies
            key = d.get("key")
            title = d.get("title")
            refresh_url = f"{PLEX_BASE}/library/sections/{key}/refresh?X-Plex-Token={PLEX_TOKEN}"
            req2 = urllib.request.Request(refresh_url, method="GET")
            with urllib.request.urlopen(req2, timeout=60) as r2:
                refreshed.append({"section_key": key, "title": title, "status": r2.status})
        return {"status": "ok", "refreshed": refreshed}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    log(f"=== Wave C free+search start {ct_now()} ===")
    long_results = []
    short_results = []
    search_results = []

    # Process each long: unlink+retarget, then add its short
    for item in LONGS:
        log(f"\n--- LONG id={item['id']} {item['title']} ({item['year']}) ---")
        lr = unlink_and_retarget(item)
        long_results.append(lr)
        log(f"  long status={lr['status']} hasFile={lr['hasFile']} path={lr['path']}")

        log(f"  SHORT add {item['short']['label']} tmdb={item['short']['tmdbId']}")
        sr = add_short(item["short"])
        short_results.append(sr)
        log(f"  short status={sr['status']} id={sr['radarr_id']} hasFile={sr['hasFile']} notes={sr['notes']}")

    # Search only approved longs
    search_ids = [16, 572, 2094, 573]
    log("\n=== MoviesSearch ===")
    for mid in search_ids:
        code, resp = movies_search(mid)
        cmd_id = (resp or {}).get("id") if isinstance(resp, dict) else None
        search_results.append({
            "movieId": mid,
            "http": code,
            "commandId": cmd_id,
            "status": (resp or {}).get("status") if isinstance(resp, dict) else str(resp)[:100],
            "title": next((x["title"] for x in LONGS if x["id"] == mid), "?"),
        })
        log(f"  search id={mid} http={code} cmd={cmd_id} status={(resp or {}).get('status') if isinstance(resp, dict) else resp}")

    # Confirm finals
    log("\n=== Final confirm ===")
    for item in LONGS:
        m = get_movie(item["id"])
        for lr in long_results:
            if lr["id"] == item["id"]:
                lr["final_hasFile"] = m.get("hasFile")
                lr["final_path"] = m.get("path")
                lr["final_monitored"] = m.get("monitored")
        log(f"  long {item['id']}: hasFile={m.get('hasFile')} monitored={m.get('monitored')} path={m.get('path')}")

    for sr in short_results:
        if sr.get("radarr_id"):
            m = get_movie(sr["radarr_id"])
            sr["final_hasFile"] = m.get("hasFile")
            sr["final_path"] = m.get("path")
            log(f"  short {sr['label']}: id={sr['radarr_id']} hasFile={m.get('hasFile')} path={m.get('path')}")

    plex = plex_refresh()
    log(f"Plex refresh: {plex}")

    # Write report
    lines = []
    lines.append("# Wave C — Free Shorts + Search Longs")
    lines.append("")
    lines.append(f"**Host:** devtop  ")
    lines.append(f"**When:** {ct_now()}  ")
    lines.append("**Rules:** unlink deleteFiles=false; PUT expected long path moveFiles=false; add shorts searchForMovie=false QP=4; MoviesSearch only 16,572,2094,573.")
    lines.append("")
    lines.append("## Shorts added (own Radarr movies)")
    lines.append("")
    lines.append("| Folder / short | TMDB | Radarr ID | hasFile | Path | Status | Notes |")
    lines.append("|---|---:|---:|---|---|---|---|")
    for sr in short_results:
        lines.append(
            f"| {sr['label']} | {sr['tmdbId']} | {sr.get('radarr_id') or '-'} | {sr.get('final_hasFile', sr.get('hasFile'))} | `{sr.get('final_path') or sr['folder']}` | {sr['status']} | {sr.get('notes','')} |"
        )
    lines.append("")
    lines.append("## Longs unlinked + retargeted")
    lines.append("")
    lines.append("| ID | Title | Wrong folder | Expected path | hasFile | Monitored | Status | Notes |")
    lines.append("|---:|---|---|---|---|---|---|---|")
    for lr in long_results:
        lines.append(
            f"| {lr['id']} | {lr['title']} ({lr['year']}) | `{lr['wrong_folder']}` | `{lr.get('final_path') or lr['expected_path']}` | {lr.get('final_hasFile', lr.get('hasFile'))} | {lr.get('final_monitored', lr.get('monitored'))} | {lr['status']} | {lr.get('notes','')[:120]} |"
        )
    lines.append("")
    lines.append("## MoviesSearch started (only 16, 572, 2094, 573)")
    lines.append("")
    lines.append("| Movie ID | Title | HTTP | Command ID | Status |")
    lines.append("|---:|---|---:|---:|---|")
    for s in search_results:
        lines.append(f"| {s['movieId']} | {s['title']} | {s['http']} | {s.get('commandId')} | {s.get('status')} |")
    lines.append("")
    lines.append("## NOT searched (left missing/monitored)")
    lines.append("")
    lines.append("- Alice in Wonderland (1951) id=200")
    lines.append("- Sunset Boulevard (1950) id=623")
    lines.append("- What Lies Beneath (2000) id=480")
    lines.append("- Death Race (2008) id=998")
    lines.append("- Dirty Dancing (1987) id=1049")
    lines.append("")
    lines.append("## Plex Flix refresh")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(plex, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## Caution / disambiguation")
    lines.append("")
    lines.append("- Blood (2012) tmdb 136278 vs Blood (2023) tmdb 746524 — kept distinct.")
    lines.append("- Dirty Dancing (2017) remake tmdb 444902 vs 1987 original id 1049.")
    lines.append("- Lion (2016) tmdb 334543 vs The Lion King (1994) id 2094.")
    lines.append("- Beneath (2013): chose tmdb **191619** (Chiller fish horror, runtime 90; file ~89.9m). Alternate miners film is tmdb 257874.")
    lines.append("- 1 (2013): tmdb **217316**.")
    lines.append("")

    Path(REPORT).write_text("\n".join(lines) + "\n")
    log(f"Wrote {REPORT}")

    # Also dump JSON summary
    summary = {
        "shorts": short_results,
        "longs": long_results,
        "searches": search_results,
        "plex": plex,
        "when": ct_now(),
    }
    Path("/tmp/cs-recon/WAVE_C_FREE_AND_SEARCH.json").write_text(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
