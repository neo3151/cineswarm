#!/usr/bin/env python3
"""Wave C safe free+search. Never print secrets. Never Radarr DELETE moviefile."""
import os, json, urllib.request, urllib.error, shutil, time, urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

ENV_PATH = "/home/neo/workspace/cineswarm/.env"
REPORT = "/tmp/cs-recon/WAVE_C_FREE_AND_SEARCH.md"
ROOT = "/media/Movies"
HOST_ROOT = "/mnt/media/Movies"
HOLD = f"{HOST_ROOT}/.wave_c_hold"
QP = 4

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

def ct_now():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M CT")

def log(msg):
    print(msg, flush=True)

def get_movie(mid):
    c, m = radarr("GET", f"/api/v3/movie/{mid}")
    if c != 200:
        raise RuntimeError(f"GET {mid} {c} {m}")
    return m

def wait_has_file(mid, want=True, attempts=25, delay=2):
    last = None
    for _ in range(attempts):
        last = get_movie(mid)
        if bool(last.get("hasFile")) == want:
            return last
        time.sleep(delay)
    return last

def host_videos(folder_name):
    p = Path(HOST_ROOT) / folder_name
    vids = []
    if p.is_dir():
        for n in sorted(p.iterdir()):
            if n.is_file() and n.suffix.lower() in {".mkv", ".mp4", ".avi", ".m4v", ".m2ts", ".ts"}:
                vids.append(n)
    return vids

LONGS = [
    dict(id=572, title="Harry Potter and the Half-Blood Prince", year=2009,
         wrong="Blood (2012)", expected=f"{ROOT}/Harry Potter and the Half-Blood Prince (2009)",
         search=True, short=dict(title="Blood", year=2012, tmdbId=136278, folder="Blood (2012)")),
    dict(id=200, title="Alice in Wonderland", year=1951,
         wrong="Alice (1990)", expected=f"{ROOT}/Alice in Wonderland (1951)",
         search=False, short=dict(title="Alice", year=1990, tmdbId=8217, folder="Alice (1990)")),
    dict(id=573, title="First Blood", year=1982,
         wrong="Blood (2023)", expected=f"{ROOT}/First Blood (1982)",
         search=True, short=dict(title="Blood", year=2023, tmdbId=746524, folder="Blood (2023)")),
    dict(id=2094, title="The Lion King", year=1994,
         wrong="Lion (2016)", expected=f"{ROOT}/The Lion King (1994)",
         search=True, short=dict(title="Lion", year=2016, tmdbId=334543, folder="Lion (2016)")),
    dict(id=623, title="Sunset Boulevard", year=1950,
         wrong="Boulevard (2014)", expected=f"{ROOT}/Sunset Boulevard (1950)",
         search=False, short=dict(title="Boulevard", year=2014, tmdbId=259963, folder="Boulevard (2014)")),
    dict(id=480, title="What Lies Beneath", year=2000,
         wrong="Beneath (2013)", expected=f"{ROOT}/What Lies Beneath (2000)",
         search=False, short=dict(title="Beneath", year=2013, tmdbId=191619, folder="Beneath (2013)")),
    dict(id=998, title="Death Race", year=2008,
         wrong="Death Race 2050 (2017)", expected=f"{ROOT}/Death Race (2008)",
         search=False, short=dict(title="Death Race 2050", year=2017, tmdbId=401544, folder="Death Race 2050 (2017)")),
    dict(id=1049, title="Dirty Dancing", year=1987,
         wrong="Dirty Dancing (2017)", expected=f"{ROOT}/Dirty Dancing (1987)",
         search=False, short=dict(title="Dirty Dancing", year=2017, tmdbId=444902, folder="Dirty Dancing (2017)")),
]

def add_or_get_short(short):
    sr = {"label": short["folder"], "title": short["title"], "year": short["year"],
          "tmdbId": short["tmdbId"], "status": "?", "notes": "", "radarr_id": None, "hasFile": False}
    c, lookup = radarr("GET", "/api/v3/movie/lookup",
                       query=urllib.parse.urlencode({"term": "tmdb:%s" % short["tmdbId"]}))
    if c != 200 or not lookup:
        sr["status"] = "lookup_failed"
        return sr
    hit = lookup[0]
    folder_path = "%s/%s" % (ROOT, short["folder"])
    if hit.get("id"):
        sid = hit["id"]
        sr["radarr_id"] = sid
        sr["notes"] = "already_in_library"
        sm = get_movie(sid)
        if sm.get("path") != folder_path:
            sm["path"] = folder_path
            sm["folderName"] = folder_path
            sm["monitored"] = True
            radarr("PUT", "/api/v3/movie/%s" % sid, data=sm, query="moveFiles=false")
    else:
        payload = dict(hit)
        payload["path"] = folder_path
        payload["folderName"] = folder_path
        payload["qualityProfileId"] = QP
        payload["monitored"] = True
        payload["rootFolderPath"] = ROOT
        payload["addOptions"] = {"searchForMovie": False}
        payload.pop("id", None)
        cadd, added = radarr("POST", "/api/v3/movie", data=payload)
        if cadd not in (200, 201):
            sr["status"] = "add_failed"
            sr["notes"] = str(added)[:300]
            return sr
        sid = added["id"]
        sr["radarr_id"] = sid
        sr["notes"] = "added"
    return sr

def manual_import(folder_path, sid):
    q = urllib.parse.urlencode({"folder": folder_path, "filterExistingFiles": "false"})
    cmi, items = radarr("GET", "/api/v3/manualimport", query=q)
    files = []
    for it in (items or []):
        p = it.get("path") or ""
        if Path(p).suffix.lower() in {".mkv", ".mp4", ".avi", ".m4v", ".m2ts", ".ts"}:
            files.append({
                "path": p,
                "folderName": it.get("folderName") or folder_path,
                "movieId": sid,
                "quality": it.get("quality") or {
                    "quality": {"id": 7, "name": "Bluray-1080p"},
                    "revision": {"version": 1, "real": 0, "isRepack": False},
                },
                "languages": it.get("languages") or [{"id": 1, "name": "English"}],
                "releaseGroup": it.get("releaseGroup") or "",
                "indexerFlags": it.get("indexerFlags") or 0,
            })
    if not files:
        return False
    radarr("POST", "/api/v3/command", data={"name": "ManualImport", "files": files, "importMode": "copy"})
    return True

def process_one(item):
    mid = item["id"]
    folder = item["wrong"]
    log("===== LONG %s %s <- %s =====" % (mid, item["title"], folder))
    lr = {"id": mid, "title": item["title"], "year": item["year"], "wrong": folder,
          "expected": item["expected"], "status": "?", "notes": ""}
    m = get_movie(mid)
    lr["notes"] = "before path=%s hasFile=%s" % (m.get("path"), m.get("hasFile"))
    vids = host_videos(folder)
    if not vids:
        lr["status"] = "no_video_on_disk"
        return lr, None

    held = []
    for v in vids:
        dest = Path(HOLD) / ("%s__%s" % (mid, v.name))
        log("  HOLD mv %s -> %s" % (v, dest))
        shutil.move(str(v), str(dest))
        if not dest.is_file():
            raise RuntimeError("hold move failed for %s" % v)
        held.append(dest)
    nfo = Path(HOST_ROOT) / folder / "movie.nfo"
    if nfo.is_file():
        ndest = Path(HOLD) / ("%s__movie.nfo" % mid)
        shutil.move(str(nfo), str(ndest))
        held.append(ndest)

    def restore_held():
        for h in held:
            if not h.exists():
                continue
            name = h.name.split("__", 1)[1]
            shutil.move(str(h), str(Path(HOST_ROOT) / folder / name))

    radarr("POST", "/api/v3/command", data={"name": "RescanMovie", "movieId": mid})
    m = wait_has_file(mid, want=False)
    if m.get("hasFile"):
        time.sleep(3)
        radarr("POST", "/api/v3/command", data={"name": "RescanMovie", "movieId": mid})
        m = wait_has_file(mid, want=False)
    log("  after rescan hasFile=%s" % m.get("hasFile"))
    if m.get("hasFile"):
        lr["status"] = "rescan_still_hasFile"
        restore_held()
        return lr, None

    m = get_movie(mid)
    m["path"] = item["expected"]
    m["folderName"] = item["expected"]
    m["monitored"] = True
    c, _ = radarr("PUT", "/api/v3/movie/%s" % mid, data=m, query="moveFiles=false")
    m = get_movie(mid)
    lr["path"] = m.get("path")
    lr["hasFile"] = m.get("hasFile")
    lr["monitored"] = m.get("monitored")
    if (not m.get("hasFile")) and m.get("path") == item["expected"]:
        lr["status"] = "ok"
    else:
        lr["status"] = "partial"
    lr["notes"] += "; PUT=%s path=%s hasFile=%s" % (c, m.get("path"), m.get("hasFile"))
    log("  long status=%s path=%s hasFile=%s" % (lr["status"], lr["path"], lr["hasFile"]))

    short = item["short"]
    sr = add_or_get_short(short)
    if sr["status"] == "add_failed" or sr["status"] == "lookup_failed":
        restore_held()
        return lr, sr
    sid = sr["radarr_id"]
    folder_path = "%s/%s" % (ROOT, short["folder"])

    for h in list(held):
        if not h.exists():
            continue
        name = h.name.split("__", 1)[1]
        dest = Path(HOST_ROOT) / short["folder"] / name
        log("  RESTORE %s -> %s" % (h.name, dest))
        shutil.move(str(h), str(dest))
        held.remove(h)

    radarr("POST", "/api/v3/command", data={"name": "RescanMovie", "movieId": sid})
    sm = wait_has_file(sid, want=True, attempts=12, delay=2)
    if not sm.get("hasFile"):
        manual_import(folder_path, sid)
        time.sleep(4)
        radarr("POST", "/api/v3/command", data={"name": "RescanMovie", "movieId": sid})
        sm = wait_has_file(sid, want=True, attempts=15, delay=2)
    sr["hasFile"] = bool(sm.get("hasFile"))
    sr["path"] = sm.get("path")
    sr["status"] = "ok" if sm.get("hasFile") else "added_no_file"
    log("  short status=%s id=%s hasFile=%s" % (sr["status"], sr["radarr_id"], sr["hasFile"]))
    return lr, sr

def handle_id16_short():
    """id 16 already retargeted; file was permanently deleted by Radarr 6.3 movieFile DELETE."""
    lr = {
        "id": 16,
        "title": "Harry Potter and the Deathly Hallows: Part 1",
        "year": 2010,
        "wrong": "1 (2013)",
        "expected": f"{ROOT}/Harry Potter and the Deathly Hallows - Part 1 (2010)",
        "status": "ok_file_lost",
        "notes": "Unlinked via DELETE moviefile; Radarr 6.3 permanently deleted file (no recycle bin then). Path retargeted.",
    }
    m = get_movie(16)
    lr["path"] = m.get("path")
    lr["hasFile"] = m.get("hasFile")
    lr["monitored"] = m.get("monitored")
    short = dict(title="1", year=2013, tmdbId=217316, folder="1 (2013)")
    sr = add_or_get_short(short)
    if sr.get("radarr_id"):
        sm = get_movie(sr["radarr_id"])
        sr["hasFile"] = bool(sm.get("hasFile"))
        sr["path"] = sm.get("path")
        if not sm.get("hasFile"):
            sr["status"] = "added_file_missing"
            sr["notes"] = (sr.get("notes") or "") + "; SOURCE FILE PERMANENTLY DELETED by earlier movieFile DELETE"
    return lr, sr

def movies_search(mid):
    return radarr("POST", "/api/v3/command", data={"name": "MoviesSearch", "movieIds": [mid]})

def plex_refresh():
    try:
        url = "%s/library/sections?X-Plex-Token=%s" % (PLEX_BASE, PLEX_TOKEN)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        dirs = data.get("MediaContainer", {}).get("Directory", [])
        refreshed = []
        for d in dirs:
            if d.get("type") != "movie":
                continue
            key = d.get("key")
            title = d.get("title")
            refresh_url = "%s/library/sections/%s/refresh?X-Plex-Token=%s" % (PLEX_BASE, key, PLEX_TOKEN)
            req2 = urllib.request.Request(refresh_url, method="GET")
            with urllib.request.urlopen(req2, timeout=60) as r2:
                refreshed.append({"section_key": key, "title": title, "status": r2.status})
        return {"status": "ok", "refreshed": refreshed}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def main():
    log("=== Wave C safe start %s ===" % ct_now())
    Path(HOLD).mkdir(parents=True, exist_ok=True)
    # ensure recycle bin
    c, mm = radarr("GET", "/api/v3/config/mediamanagement")
    if c == 200 and not mm.get("recycleBin"):
        Path("/mnt/media/.radarr-recycle").mkdir(parents=True, exist_ok=True)
        mm["recycleBin"] = "/media/.radarr-recycle"
        radarr("PUT", "/api/v3/config/mediamanagement", data=mm)

    long_results = []
    short_results = []

    lr16, sr16 = handle_id16_short()
    long_results.append(lr16)
    short_results.append(sr16)
    log("id16 long=%s short=%s" % (lr16["status"], sr16.get("status")))

    for item in LONGS:
        try:
            lr, sr = process_one(item)
            long_results.append(lr)
            if sr:
                short_results.append(sr)
        except Exception as e:
            log("ERROR %s: %s" % (item["id"], e))
            long_results.append({"id": item["id"], "status": "exception", "notes": str(e)})

    search_results = []
    for mid in [16, 572, 2094, 573]:
        code, resp = movies_search(mid)
        search_results.append({
            "movieId": mid,
            "http": code,
            "commandId": (resp or {}).get("id") if isinstance(resp, dict) else None,
            "status": (resp or {}).get("status") if isinstance(resp, dict) else str(resp)[:100],
            "title": get_movie(mid).get("title"),
        })
        log("search %s http=%s cmd=%s" % (mid, code, search_results[-1]["commandId"]))

    # finals
    for lr in long_results:
        if "id" in lr:
            m = get_movie(lr["id"])
            lr["final_hasFile"] = m.get("hasFile")
            lr["final_path"] = m.get("path")
            lr["final_monitored"] = m.get("monitored")
    for sr in short_results:
        if sr.get("radarr_id"):
            m = get_movie(sr["radarr_id"])
            sr["final_hasFile"] = m.get("hasFile")
            sr["final_path"] = m.get("path")

    plex = plex_refresh()
    log("plex=%s" % plex)

    lines = []
    lines.append("# Wave C — Free Shorts + Search Longs")
    lines.append("")
    lines.append("**Host:** devtop  ")
    lines.append("**When:** %s  " % ct_now())
    lines.append("**Rules:** free shorts as own movies (searchForMovie=false, QP HD-1080p id=4); MoviesSearch only 16,572,2094,573.")
    lines.append("")
    lines.append("## CRITICAL: Radarr 6.3 movieFile DELETE")
    lines.append("")
    lines.append("On Radarr **6.3.0.10514**, `DELETE /api/v3/moviefile/{id}?deleteFiles=false` still **permanently deleted** the file")
    lines.append("(`/media/Movies/1 (2013)/1 (2013) Bluray-1080p.mkv`, 8202947546 bytes) because no recycle bin was configured.")
    lines.append("History reason: `Manual`. Remaining 8 used **filesystem hold + RescanMovie** (no movieFile DELETE).")
    lines.append("Recycle bin now set to `/media/.radarr-recycle`.")
    lines.append("")
    lines.append("## Shorts added")
    lines.append("")
    lines.append("| Folder / short | TMDB | Radarr ID | hasFile | Path | Status | Notes |")
    lines.append("|---|---:|---:|---|---|---|---|")
    for sr in short_results:
        lines.append("| %s | %s | %s | %s | `%s` | %s | %s |" % (
            sr.get("label"), sr.get("tmdbId"), sr.get("radarr_id") or "-",
            sr.get("final_hasFile", sr.get("hasFile")),
            sr.get("final_path") or sr.get("path") or "",
            sr.get("status"), (sr.get("notes") or "")[:120]))
    lines.append("")
    lines.append("## Longs unlinked + retargeted")
    lines.append("")
    lines.append("| ID | Title | Wrong folder | Path now | hasFile | Monitored | Status | Notes |")
    lines.append("|---:|---|---|---|---|---|---|---|")
    for lr in long_results:
        lines.append("| %s | %s (%s) | `%s` | `%s` | %s | %s | %s | %s |" % (
            lr.get("id"), lr.get("title"), lr.get("year"), lr.get("wrong"),
            lr.get("final_path") or lr.get("path") or lr.get("expected"),
            lr.get("final_hasFile", lr.get("hasFile")),
            lr.get("final_monitored", lr.get("monitored")),
            lr.get("status"), (lr.get("notes") or "")[:120]))
    lines.append("")
    lines.append("## MoviesSearch started (only 16, 572, 2094, 573)")
    lines.append("")
    lines.append("| Movie ID | Title | HTTP | Command ID | Status |")
    lines.append("|---:|---|---:|---:|---|")
    for s in search_results:
        lines.append("| %s | %s | %s | %s | %s |" % (
            s["movieId"], s["title"], s["http"], s.get("commandId"), s.get("status")))
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
    lines.append("## Disambiguation")
    lines.append("")
    lines.append("- Blood (2012) tmdb **136278** vs Blood (2023) tmdb **746524**")
    lines.append("- Dirty Dancing (2017) remake tmdb **444902** vs 1987 id 1049")
    lines.append("- Lion (2016) tmdb **334543** vs The Lion King (1994) id 2094")
    lines.append("- Beneath (2013) tmdb **191619** (Chiller fish; file ~89.9m)")
    lines.append("- 1 (2013) tmdb **217316** — **video file lost**")
    lines.append("")
    Path(REPORT).write_text("\n".join(lines) + "\n")
    Path("/tmp/cs-recon/WAVE_C_FREE_AND_SEARCH.json").write_text(
        json.dumps({"longs": long_results, "shorts": short_results, "searches": search_results, "plex": plex, "when": ct_now()}, indent=2, default=str)
    )
    log("Wrote %s" % REPORT)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
