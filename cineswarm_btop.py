#!/usr/bin/env python3
"""Full-screen Mac + CineSwarm operations monitor.

Run this locally in a large Terminal window on the secondary display:
    python3 cineswarm_btop.py
"""

from __future__ import annotations

import curses
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime
from typing import Any

from cineswarm_system import system_metrics, top_processes

CONTROL_URL = os.environ.get("CINESWARM_MONITOR_URL", "http://192.168.1.23:8787").rstrip("/")
REFRESH_SECONDS = float(os.environ.get("CINESWARM_MONITOR_REFRESH", "2"))
REQUEST_TIMEOUT = float(os.environ.get("CINESWARM_MONITOR_TIMEOUT", "8"))


def command(args: list[str]) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=2).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def json_get(path: str, timeout: float = REQUEST_TIMEOUT) -> dict[str, Any]:
    try:
        request = urllib.request.Request(f"{CONTROL_URL}{path}", headers={"Accept": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return {}


def mac_metrics() -> dict[str, Any]:
    top = command(["top", "-l", "1", "-n", "0"])
    cpu = 0.0
    for line in top.splitlines():
        if "CPU usage:" in line:
            try:
                user = float(line.split("user", 1)[0].split(":")[-1].strip().strip("%"))
                system = float(line.split("sys", 1)[0].split(",")[-1].strip().strip("%"))
                cpu = user + system
            except (ValueError, IndexError):
                pass
            break

    vm = command(["vm_stat"])
    page_size = 4096
    pages: dict[str, int] = {}
    for line in vm.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        try:
            pages[key.strip()] = int(value.strip().rstrip("."))
        except ValueError:
            continue
    active = pages.get("Pages active", 0) + pages.get("Pages wired down", 0) + pages.get("Pages occupied by compressor", 0)
    free = pages.get("Pages free", 0) + pages.get("Pages speculative", 0)
    memory = command(["sysctl", "-n", "hw.memsize"])
    total_memory = int(memory or 0)
    used_memory = active * page_size
    memory_pct = (used_memory / total_memory * 100) if total_memory else 0

    disk = command(["df", "-h", "/"]).splitlines()
    disk_values = disk[-1].split() if disk else []
    processes = []
    for line in command(["ps", "-Ao", "pid=,comm=,%cpu=,%mem=", "-r"]).splitlines()[:8]:
        parts = line.split()
        if len(parts) >= 4:
            processes.append({"pid": parts[0], "name": parts[1], "cpu": parts[2], "mem": parts[3]})

    return {
        "cpu": cpu,
        "memory_pct": memory_pct,
        "memory_used_gb": used_memory / 1024**3,
        "memory_total_gb": total_memory / 1024**3,
        "disk_used": disk_values[4] if len(disk_values) > 4 else "?",
        "disk_free": disk_values[3] if len(disk_values) > 3 else "?",
        "processes": processes,
        "load": command(["sysctl", "-n", "vm.loadavg"]).strip("{} "),
    }


def bar(value: float, width: int = 24) -> str:
    value = max(0.0, min(100.0, value))
    filled = int(width * value / 100)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def safe_add(window: Any, y: int, x: int, text: str, attr: int = 0, max_width: int | None = None) -> None:
    if max_width is not None:
        text = text[:max_width]
    try:
        window.addstr(y, x, text, attr)
    except curses.error:
        pass


def panel(window: Any, y: int, x: int, height: int, width: int, title: str, lines: list[str], color: int) -> None:
    try:
        window.attron(curses.color_pair(color))
        window.addstr(y, x, "+" + "-" * max(0, width - 2) + "+")
        for row in range(1, height - 1):
            window.addstr(y + row, x, "|" + " " * max(0, width - 2) + "|")
        window.addstr(y + height - 1, x, "+" + "-" * max(0, width - 2) + "+")
        window.attroff(curses.color_pair(color))
    except curses.error:
        return
    safe_add(window, y, x + 2, f"[ {title} ]", curses.color_pair(color) | curses.A_BOLD, width - 4)
    for index, line in enumerate(lines[: max(0, height - 2)]):
        safe_add(window, y + 1 + index, x + 2, line, 0, width - 4)


def draw(window: Any, snapshot: dict[str, Any], progress: dict[str, Any], incoming: dict[str, Any], mac: dict[str, Any], devtop: dict[str, Any], history: deque[float]) -> None:
    window.erase()
    height, width = window.getmaxyx()
    if height < 20 or width < 90:
        safe_add(window, 1, 2, "Resize the terminal to at least 90x20.", curses.A_BOLD)
        window.refresh()
        return

    healthy = snapshot.get("overall_status") == "healthy"
    severity = incoming.get("severity", "unknown").upper()
    header_color = 2 if healthy else 3
    safe_add(window, 0, 2, f"CINE//SWARM  |  MAC + DEVTOP  |  {severity}", curses.color_pair(header_color) | curses.A_BOLD, width - 4)
    safe_add(window, 1, 2, f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  refresh {REFRESH_SECONDS:.1f}s  [q] quit [r] refresh", curses.color_pair(4), width - 4)

    gap = 2
    left = max(34, (width - gap) // 2)
    right = width - gap - left
    def resource_lines(item: dict[str, Any]) -> list[str]:
        if not item:
            return ["unavailable", "Check CINESWARM_MONITOR_URL"]
        return [
            f"CPU   {bar(float(item.get('cpu', 0)))} {float(item.get('cpu', 0)):5.1f}%  ({item.get('cores', '?')} cores)",
            f"MEM   {bar(float(item.get('memory_pct', 0)))} {item.get('memory_used_gb', '?'):>4}/{item.get('memory_total_gb', '?'):>4} GB",
            f"SWAP  {item.get('swap_used_gb', '?'):>5} GB",
            f"DISK  {item.get('disk_used_gb', '?'):>4}/{item.get('disk_total_gb', '?'):>4} GB  free {item.get('disk_free_gb', '?'):>4}",
            f"LOAD  {item.get('load', '?')}",
            f"UP    {item.get('uptime', '?')}",
        ]
    panel(window, 3, 1, 9, left, f"MAC [{mac.get('host', 'local')}]", resource_lines(mac), 4)
    worker = snapshot.get("worker") or {}
    queue = snapshot.get("queue_pressure") or {}
    progress_queue = progress.get("queue") or {}
    movie_queue = progress_queue.get("movies") or {}
    series_queue = progress_queue.get("series") or {}
    queue_records = int(movie_queue.get("total_records") or 0) + int(series_queue.get("total_records") or 0)
    task_counts = progress.get("tasks", {}).get("by_status") or {}
    panel(window, 3, 1 + left + gap, 9, right, f"DEVTOP [{devtop.get('host', 'remote')}]", resource_lines(devtop) if devtop else [
        "unavailable", "API endpoint did not respond",
    ], header_color)
    panel(window, 13, 1, 6, left, "CINESWARM OPS", [
        f"WORKER {str(worker.get('status', 'live')).upper():<10} age {worker.get('age_seconds', '?')}s",
        f"QUEUE  {queue.get('active', queue_records)}/{queue.get('limit', 4)} active",
        f"RADARR {movie_queue.get('total_records', 0)}  SONARR {series_queue.get('total_records', 0)}",
        f"TASKS  pending {task_counts.get('pending_approval', 0)}",
        f"PLEX  {snapshot.get('library_integrity', {}).get('movies', {}).get('indexed', '?')} indexed",
    ], header_color)

    issue_lines = []
    for issue in (incoming.get("issues") or [])[:5]:
        issue_lines.append(f"{issue.get('code', '?')}: {issue.get('summary', '')}")
    if not issue_lines:
        issue_lines = ["No ranked household issues."]
    panel(window, 13, 1 + left + gap, 6, right, "INCOMING ISSUES", issue_lines, 3 if severity in {"ATTENTION", "CRITICAL"} else 4)

    def process_lines(item: dict[str, Any], label: str) -> list[str]:
        return [
            f"{label:<6} {p['pid']:>7} {p['cpu']:>5}% {p['mem']:>5}% {p['name']}"
            for p in item.get("processes", [])
        ]
    processes = process_lines(mac, "MAC") + process_lines(devtop, "DEVTOP")
    panel(window, 20, 1, max(3, height - 21), width - 2, "TOP PROCESSES", processes or ["No process data"], 4)

    window.refresh()


def main() -> None:
    curses.wrapper(run)


def run(window: Any) -> None:
    curses.curs_set(0)
    window.nodelay(True)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    last_refresh = 0.0
    snapshot: dict[str, Any] = {}
    incoming: dict[str, Any] = {}
    while True:
        now = time.monotonic()
        key = window.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key in (ord("r"), ord("R")) or now - last_refresh >= REFRESH_SECONDS:
            with ThreadPoolExecutor(max_workers=3) as pool:
                snapshot_future = pool.submit(json_get, "/api/monitoring/snapshot", 5)
                progress_future = pool.submit(json_get, "/api/acquisition/progress", 10)
                incoming_future = pool.submit(json_get, "/api/monitoring/incoming", 5)
                system_future = pool.submit(json_get, "/api/monitoring/system", 8)
                snapshot = snapshot_future.result()
                progress = progress_future.result()
                incoming = incoming_future.result()
                devtop = system_future.result()
            mac = system_metrics()
            mac["processes"] = top_processes()
            draw(window, snapshot, progress, incoming, mac, devtop, deque())
            last_refresh = now
        time.sleep(0.05)


if __name__ == "__main__":
    main()
