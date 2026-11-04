"""Small dependency-free system telemetry helpers for the terminal monitor."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from typing import Any


def _command(args: list[str]) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=2).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _gb(value: int | float) -> float:
    return round(float(value) / 1024**3, 1)


def _mac_metrics() -> dict[str, Any]:
    top = _command(["top", "-l", "1", "-n", "0"])
    cpu = 0.0
    for line in top.splitlines():
        match = re.search(r"CPU usage:\s*([\d.]+)% user,\s*([\d.]+)% sys", line)
        if match:
            cpu = float(match.group(1)) + float(match.group(2))
            break

    vm = _command(["vm_stat"])
    pages: dict[str, int] = {}
    for line in vm.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            try:
                pages[key.strip()] = int(value.strip().rstrip("."))
            except ValueError:
                pass
    page_size_match = re.search(r"page size of (\d+) bytes", vm)
    page_size = int(page_size_match.group(1)) if page_size_match else 4096
    total = int(_command(["sysctl", "-n", "hw.memsize"]) or 0)
    used = sum(pages.get(name, 0) for name in (
        "Pages active", "Pages wired down", "Pages occupied by compressor", "Pages purgeable",
    )) * page_size
    swap = _command(["sysctl", "-n", "vm.swapusage"])
    swap_match = re.search(r"used = ([\d.]+)M", swap)
    swap_used = float(swap_match.group(1)) / 1024 if swap_match else 0.0
    disk = shutil.disk_usage("/")
    return {
        "host": platform.node() or "mac",
        "os": f"macOS {platform.mac_ver()[0]}".strip(),
        "cpu": round(cpu, 1),
        "cores": os.cpu_count() or 0,
        "memory_used_gb": _gb(used),
        "memory_total_gb": _gb(total),
        "memory_pct": round(used / total * 100, 1) if total else 0.0,
        "swap_used_gb": round(swap_used, 1),
        "disk_used_gb": _gb(disk.total - disk.free),
        "disk_total_gb": _gb(disk.total),
        "disk_free_gb": _gb(disk.free),
        "load": _command(["sysctl", "-n", "vm.loadavg"]).strip("{} "),
        "uptime": _command(["uptime"]).split("up ", 1)[-1].split(",", 1)[0] if "up " in _command(["uptime"]) else "?",
    }


def _linux_metrics() -> dict[str, Any]:
    stat = open("/proc/stat", encoding="utf-8").readline().split()
    values = [int(value) for value in stat[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    time.sleep(0.08)
    stat2 = open("/proc/stat", encoding="utf-8").readline().split()
    values2 = [int(value) for value in stat2[1:]]
    idle2 = values2[3] + (values2[4] if len(values2) > 4 else 0)
    total2 = sum(values2)
    cpu = (1 - (idle2 - idle) / max(1, total2 - total)) * 100
    memory: dict[str, int] = {}
    for line in open("/proc/meminfo", encoding="utf-8"):
        key, value = line.split(":", 1)
        memory[key] = int(value.split()[0]) * 1024
    total_memory = memory.get("MemTotal", 0)
    available = memory.get("MemAvailable", memory.get("MemFree", 0))
    disk = shutil.disk_usage("/")
    uptime_seconds = float(open("/proc/uptime", encoding="utf-8").read().split()[0])
    mount_rows = []
    for path in ["/mnt/media", *[f"/mnt/pool/disk{index}" for index in range(1, 8)]]:
        mount_info = _command(["findmnt", "-n", "-o", "FSTYPE,SOURCE,TARGET", "--target", path])
        fields = mount_info.split()
        mount_rows.append({
            "path": path,
            "mounted": bool(fields),
            "filesystem": fields[0] if fields else None,
            "source": fields[1] if len(fields) > 1 else None,
        })
    containers = []
    docker_ps = _command(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"])
    for line in docker_ps.splitlines():
        name, _, status = line.partition("\t")
        if name in {"radarr", "sonarr", "sabnzbd", "plex", "samba"}:
            containers.append({"name": name, "status": status})
    return {
        "host": platform.node() or "devtop",
        "os": f"{platform.system()} {platform.release()}",
        "cpu": round(max(0.0, cpu), 1),
        "cores": os.cpu_count() or 0,
        "memory_used_gb": _gb(total_memory - available),
        "memory_total_gb": _gb(total_memory),
        "memory_pct": round((total_memory - available) / total_memory * 100, 1) if total_memory else 0.0,
        "swap_used_gb": _gb(memory.get("SwapTotal", 0) - memory.get("SwapFree", 0)),
        "disk_used_gb": _gb(disk.total - disk.free),
        "disk_total_gb": _gb(disk.total),
        "disk_free_gb": _gb(disk.free),
        "load": " ".join(open("/proc/loadavg", encoding="utf-8").read().split()[:3]),
        "uptime": f"{int(uptime_seconds // 86400)}d {int(uptime_seconds // 3600 % 24)}h",
        "storage": {
            "media_mount": next((item for item in mount_rows if item["path"] == "/mnt/media"), {}),
            "pool_mounts": [item for item in mount_rows if item["path"] != "/mnt/media"],
        },
        "containers": containers,
    }


def system_metrics() -> dict[str, Any]:
    try:
        metrics = _mac_metrics() if platform.system() == "Darwin" else _linux_metrics()
    except (OSError, ValueError, IndexError):
        metrics = {"host": platform.node() or "unknown", "os": platform.system(), "cpu": 0.0, "memory_pct": 0.0}
    metrics["generated_at"] = time.time()
    return metrics


def top_processes(limit: int = 8) -> list[dict[str, str]]:
    output = _command(["ps", "-Ao", "pid=,comm=,%cpu=,%mem=", "-r"])
    rows = []
    for line in output.splitlines()[:limit]:
        parts = line.split()
        if len(parts) >= 4:
            rows.append({"pid": parts[0], "name": parts[1], "cpu": parts[2], "mem": parts[3]})
    return rows
