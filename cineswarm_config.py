#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import os
import sys
import urllib.parse
from collections.abc import Mapping

REQUIRED_SERVICE_SETTINGS = (
    "PLEX_URL",
    "PLEX_TOKEN",
    "RADARR_URL",
    "RADARR_API_KEY",
    "SONARR_URL",
    "SONARR_API_KEY",
)
URL_SETTINGS = ("PLEX_URL", "RADARR_URL", "SONARR_URL", "SABNZBD_URL", "CINESWARM_MODEL_BASE_URL")
DIRECTORY_SETTINGS = (
    "CINESWARM_BACKUP_DIR",
    "CINESWARM_PRESERVATION_ROOTS",
    "CINESWARM_PRESERVATION_UNION_ROOTS",
    "CINESWARM_PRESERVATION_BRANCHES",
    "CINESWARM_PRESERVATION_MOUNTS",
)
FILE_SETTINGS = ("CINESWARM_CONTROL_DB", "CINESWARM_CATALOG_DB", "CINESWARM_OUTPUT_MD")
PAIRED_SETTINGS = (
    ("CINESWARM_DASHBOARD_USERNAME", "CINESWARM_DASHBOARD_PASSWORD"),
    ("SABNZBD_URL", "SABNZBD_API_KEY"),
)


def read_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.isfile(path):
        return values
    with open(path, encoding="utf-8") as source:
        for raw_line in source:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _validate_url(name: str, value: str, errors: list[str]) -> None:
    try:
        parsed = urllib.parse.urlparse(value)
        port = parsed.port
    except ValueError:
        errors.append(f"{name}: invalid port")
        return
    if parsed.scheme not in {"http", "https"}:
        errors.append(f"{name}: scheme must be http or https")
    if not parsed.hostname:
        errors.append(f"{name}: hostname is required")
    if port is not None and not 1 <= port <= 65535:
        errors.append(f"{name}: port must be between 1 and 65535")
    if parsed.username or parsed.password:
        errors.append(f"{name}: credentials must not be embedded in URLs")


def _configured_paths(value: str) -> list[str]:
    paths: list[str] = []
    for item in value.split(","):
        item = os.path.expanduser(item.strip())
        if not item:
            continue
        matches = glob.glob(item)
        paths.extend(matches or [item])
    return paths


def validate_config(environ: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    for name in REQUIRED_SERVICE_SETTINGS:
        if not environ.get(name, "").strip():
            errors.append(f"{name}: required setting is missing")
    for name in URL_SETTINGS:
        value = environ.get(name, "").strip()
        if value:
            _validate_url(name, value, errors)
    control_port = environ.get("CINESWARM_CONTROL_PORT", "8787").strip()
    try:
        port = int(control_port)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        errors.append("CINESWARM_CONTROL_PORT: port must be an integer between 1 and 65535")
    for first, second in PAIRED_SETTINGS:
        if bool(environ.get(first, "").strip()) != bool(environ.get(second, "").strip()):
            errors.append(f"{first}/{second}: both settings must be configured together")
    mapping = environ.get("CINESWARM_PATH_MAP", "").strip()
    if mapping:
        for index, item in enumerate(mapping.split(","), 1):
            if item.count("=") != 1:
                errors.append(f"CINESWARM_PATH_MAP: mapping {index} must use source=target")
                continue
            source, target = (part.strip() for part in item.split("=", 1))
            if not source.startswith("/") or not target.startswith("/"):
                errors.append(f"CINESWARM_PATH_MAP: mapping {index} paths must be absolute")
            elif not os.path.isdir(os.path.expanduser(target)):
                errors.append(f"CINESWARM_PATH_MAP: mapping {index} target directory is unavailable")
    for name in DIRECTORY_SETTINGS:
        value = environ.get(name, "").strip()
        for path in _configured_paths(value):
            if not os.path.isdir(path):
                errors.append(f"{name}: configured directory is unavailable")
                break
    for name in FILE_SETTINGS:
        value = environ.get(name, "").strip()
        if value:
            parent = os.path.dirname(os.path.abspath(os.path.expanduser(value)))
            if not os.path.isdir(parent):
                errors.append(f"{name}: parent directory is unavailable")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CineSwarm configuration without displaying secrets.")
    parser.add_argument("--env-file", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    args = parser.parse_args()
    config = read_env_file(args.env_file)
    config.update(os.environ)
    errors = validate_config(config)
    if errors:
        print(f"CineSwarm configuration invalid ({len(errors)} error(s)):")
        for error in errors:
            print(f"- {error}")
        return 1
    print("CineSwarm configuration valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
