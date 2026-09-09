# CineSwarm Workspace

CineSwarm maintains a complete local catalog from Radarr and Sonarr for media-type-aware Gemini recommendations. The catalog is a local copy: the sync only reads the source databases and never modifies them.

## Workspace Files
- **`USER_MANUAL.md`**: Complete operator guide for Discord, dashboard, acquisitions, upgrades, autonomy, alerts, reliability, and troubleshooting.
- **`USER_MANUAL.pdf`**: Professionally typeset A4 edition of the complete operator guide.
- **`cineswarm_sync.py`**: Full, idempotent sync for every Radarr movie and Sonarr series.
- **`cineswarm_catalog.db`**: Durable SQLite source of truth created by the sync. It stores separate movie and series records, normalized metadata, source IDs, raw source rows, sync history, and current/stale state.
- **`cineswarm_gem_prompt.md`**: Copy-ready Gemini Gem instructions for the current CineSwarm command contract, full autopilot, decision learning, recommendations, queues, and release operations.
- **`LATEST_VAULT_ADDITIONS.md`**: Human-readable summary of the complete current catalog.

## Run a Full Sync
```bash
python3 cineswarm_sync.py
```

The default source paths are:
- Radarr: `/home/neo/docker/radarr/config/radarr.db`
- Sonarr: `/home/neo/docker/sonarr/config/sonarr.db`

Override paths or output locations with `RADARR_DB`, `SONARR_DB`, `CINESWARM_CATALOG_DB`, and `CINESWARM_OUTPUT_MD`. Each successful source import upserts every source record and marks records no longer present as stale. If a source is unavailable, its previous catalog records are retained and the snapshot reports a warning.

The SQLite catalog is the complete collection; the markdown file intentionally contains aggregate analytics and recent titles rather than duplicating every record. Do not commit the catalog database if it contains private library paths or metadata.

## Phase 1 Control Plane

`cineswarm_control.py` starts a local web dashboard and a read-only multi-service control plane. It observes Plex, Radarr, and Sonarr, stores service snapshots, records audit events, and exposes catalog counts. Product monitoring adds `GET /api/monitoring/snapshot` (and `/api/v1/...`), Discord `!cine monitor`, and optional `CINESWARM_MONITOR_WEBHOOK` transition alerts. No server write actions are enabled in this phase.

```bash
python3 cineswarm_control.py
```

The dashboard is bound to the LAN address `192.168.1.23:8787` by the installed user service. Open `http://192.168.1.23:8787` from another device on the same LAN. To refresh once and exit:

```bash
python3 cineswarm_control.py --refresh
```

Configure service access through environment variables or an untracked local `.env` file in this directory. The loader accepts only the documented keys:

- `PLEX_URL` and `PLEX_TOKEN`
- `RADARR_URL` and `RADARR_API_KEY`
- `SONARR_URL` and `SONARR_API_KEY`
- `CINESWARM_CONTROL_HOST` and `CINESWARM_CONTROL_PORT`
- `CINESWARM_CONTROL_DB` for the audit/task database
- `CINESWARM_API_TIMEOUT` for service request timeout seconds

```dotenv
PLEX_URL=http://127.0.0.1:32400
PLEX_TOKEN=
CINESWARM_AUTO_PLEX_REFRESH=true
CINESWARM_PLEX_REFRESH_COOLDOWN=21600
```

Service credentials are only read into process memory and are never written to snapshots, task payloads, audit details, or logs. Phase 1 proposal endpoints reject write actions; future agents must be added behind the policy layer rather than calling service APIs directly.

## Phase 2 Read-Only Agent Swarm

`cineswarm_agents.py` adds librarian, curator, health, and request specialists behind a supervisor. The dashboard now includes `POST /api/chat`, which uses the local catalog and service state as context. It can run without a model key and will report that the hosted model is not configured.

The model adapter supports Gemini’s OpenAI-compatible endpoint and is configured only through `.env` or the process environment:

```dotenv
CINESWARM_MODEL_PROVIDER=gemini
CINESWARM_MODEL_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
CINESWARM_MODEL_API_KEY=
CINESWARM_MODEL_NAME=gemini-2.5-flash
CINESWARM_MODEL_TIMEOUT=30
```

`GEMINI_API_KEY` or `GOOGLE_API_KEY` can be used instead of `CINESWARM_MODEL_API_KEY`, and `GEMINI_MODEL` can be used instead of `CINESWARM_MODEL_NAME`.

This phase can analyze, recommend, and propose plans. It cannot change Plex, Radarr, Sonarr, files, downloads, users, or settings. Write-capable agents must be added to `Policy` with explicit scopes, approval records, and audit coverage before they are enabled.

The dashboard’s **Reconcile library** action refreshes all three services and compares Radarr TMDB IDs and Sonarr TVDB/TMDB IDs against Plex provider IDs. It also checks Radarr file presence, Sonarr paths, and the configured container-to-host path map. The report distinguishes items without files, files not indexed by Plex, missing paths, unmatched Plex items, and Plex records without provider IDs. It does not modify anything.

The **Request Plex scan** button creates a pending approval task. Only clicking **Approve pending scan** refreshes every configured Plex movie and show library. No Radarr/Sonarr actions, file operations, metadata edits, or deletes are enabled.

The dashboard also provides **Plan an acquisition**. It performs read-only Radarr/Sonarr lookups, shows candidates, existing catalog matches, root folders, and quality profiles, and does not add or download anything. A selected plan can later be submitted to `/api/acquisition/propose`, which creates a pending approval task. Approval revalidates the candidate, root folder, and quality profile before adding the item with automatic searching disabled. Downloads are not started by this action. Queue state is available through `/api/acquisition/queue`; a separate `/api/acquisition/search-propose` endpoint creates an approval-gated search task that revalidates the selected Radarr/Sonarr item before submitting a search command. **Analyze quality** reports current movie-file quality, missing files, series completeness, and advisory upgrade candidates. It never searches, replaces, or deletes media.

The worker now tracks approved search outcomes in `acquisition_observations`, distinguishing running searches, failed searches, successful imports, partial series imports, and searches that completed without a file. The current Hot Fuzz search is recorded as `search_completed_no_file`: Radarr completed the command successfully, but no release was imported. That state creates at most one approval-gated retry task; it never loops or retries silently. Use the task-ID approval control in the dashboard to approve a follow-up. When an add is approved successfully, CineSwarm automatically creates the next search-approval task using the returned Radarr/Sonarr ID; when an import succeeds, it creates a Plex-refresh follow-up. This removes manual ID copying while retaining approval at each write boundary.

## Discovery Queue

The discovery engine runs a daily Gemini-powered candidate pass, validates each suggestion through Radarr/Sonarr lookup, removes titles already in the catalog or Plex, scores candidates against collection genre and era patterns, and stores a ranked queue. Discord lists durable candidate IDs; under full autopilot, `!cine acquire ID` revalidates and executes the add and search immediately while recording the decision. `!cine discover refresh` runs discovery immediately. Dashboard proposal controls remain available for legacy/manual task workflows. `CINESWARM_DISCOVERY_ENABLED` controls the worker and `CINESWARM_DISCOVERY_INTERVAL` controls its schedule.

## Autonomous Movie Policy

When `CINESWARM_FULL_AUTOPILOT=true`, paired-user adds, discovery acquisitions, searches, and exact grabs execute without confirmation after live revalidation. Background discovery remains gated by score **70**, `HD-1080p`, allowed genres, duplicate checks, 500 GB free, and fewer than two concurrent downloads. Near-miss titles scoring ≥68 may promote once per hour when theatrical runtime and watch-affinity gates pass. Growth uses `queue_only` budget mode with no weekly cap; historical counts and estimated 20 GB-per-movie usage remain recorded for reference. Emergency stop always takes precedence.

Automatic actions remain visible in `tasks`, `autonomous_actions`, `weekly_budget_tracker`, and `audit_events`. Adds and searches are separate revalidated operations. A successful add increments the weekly movie budget immediately; failed actions are recorded and do not loop silently.

## Always-On Worker

`cineswarm_worker.py` provides the persistent maintenance loop. It stores durable jobs, schedules, leases, attempts, retry backoff, and audit events in `cineswarm_control.db`. Its jobs refresh service snapshots, sync the catalog, reconcile the library, monitor acquisition queues, refresh discovery candidates, create policy-constrained acquisition proposals, and turn failed downloads into approval-gated search-retry tasks.

Run one due job for testing:

```bash
python3 cineswarm_worker.py --once
```

Run continuously in the foreground:

```bash
python3 cineswarm_worker.py
```

The default schedules are service refresh every 15 minutes, catalog sync and failed-download inspection every 30 minutes, queue monitoring every 5 minutes, reconciliation and autonomous proposal evaluation every hour, and discovery refresh daily. Override them with `CINESWARM_REFRESH_INTERVAL`, `CINESWARM_CATALOG_INTERVAL`, `CINESWARM_FAILED_DOWNLOAD_INTERVAL`, `CINESWARM_QUEUE_INTERVAL`, `CINESWARM_RECONCILE_INTERVAL`, `CINESWARM_AUTONOMOUS_INTERVAL`, and `CINESWARM_DISCOVERY_INTERVAL`. Retry behavior is controlled by `CINESWARM_WORKER_MAX_ATTEMPTS`, `CINESWARM_WORKER_LEASE_SECONDS`, and `CINESWARM_WORKER_POLL_SECONDS`.

Failed-download inspection never changes a quality profile. Under full autopilot it creates, revalidates, and executes at most one search retry for each failed queue item, records the decision and outcome, and never loops silently. Set `CINESWARM_NOTIFY_ON_FAILED_DOWNLOAD=true` to emit failure notifications. Stored policy takes precedence over environment defaults.

Automatic Plex refresh is deliberately disabled by default. To allow the worker to refresh configured Plex movie and show libraries when files exist but Plex has not indexed them, add `CINESWARM_AUTO_PLEX_REFRESH=true` to `.env`. It is limited by `CINESWARM_PLEX_REFRESH_COOLDOWN`, which defaults to six hours. This setting does not enable downloads, metadata writes, file operations, or deletion.

A user-service unit is provided at `.devin/cineswarm-worker.service`. Install it only after reviewing the command and permissions:

```bash
mkdir -p ~/.config/systemd/user
cp .devin/cineswarm-worker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cineswarm-worker.service
```

Full autopilot executes trusted paired-user and background acquisition decisions while preserving separate audited tasks, live revalidation, duplicate prevention, queue/storage gates, decision history, and Discord reporting. Failed-download recovery executes once automatically. The worker never moves or deletes files or changes metadata, and Plex refresh remains cooldown-limited.

Run the isolated regression suite with `python3 -m unittest -v`.

## Discord Chat Bot

The private Discord Gateway bot runs from `cineswarm_discord.py` and uses an outbound connection, so no public inbound port is required. Install the pinned environment with `python3 -m venv .venv` and `.venv/bin/pip install -r requirements.txt`.

Create an application at `https://discord.com/developers/applications`, add a bot, enable **Message Content Intent**, and invite it with the `bot` scope plus View Channels, Send Messages, Read Message History, and Embed Links permissions. Store the token only in `.env`. Either configure the numeric allowlist directly or use the one-time pairing code to capture it automatically:

```dotenv
CINESWARM_DISCORD_BOT_TOKEN=
CINESWARM_DISCORD_GUILD_IDS=
CINESWARM_DISCORD_CHANNEL_IDS=
CINESWARM_DISCORD_USER_IDS=
CINESWARM_DISCORD_SETUP_CODE=
CINESWARM_DISCORD_REQUIRE_MENTION=true
CINESWARM_DISCORD_PREFIX=!cine
```

For one-time setup, start the bot with a random `CINESWARM_DISCORD_SETUP_CODE`, then send `!cine pair CODE` in the desired server channel. CineSwarm stores that server, channel, and user as the allowlist; the code cannot pair another identity afterward. To move later, the paired user can send `!cine move CODE` in a different channel in the same server; the old channel is immediately deauthorized.

Mention the bot or use `!cine`. Commands are `status`, `queue`, `discover`, `discover refresh`, `acquire ID`, `release RELEASE_NAME`, `grab RELEASE_NAME`, `add Movie Title (Year)`, `decisions`, `decision ID`, and `feedback ID good|bad NOTE`. `queue` lists actual Radarr/Sonarr releases with states, progress, remaining size, time, and errors. Full autopilot executes paired-user add, acquire, search, and exact-grab requests immediately after revalidation, records every decision and warning override, and reports outcomes to Discord. Feedback is joined into future Gemini discovery context. Legacy `confirm` remains available for older pending tasks.

The `.devin/cineswarm-discord.service` unit runs the bot with systemd restart and watchdog protection.

## Reliability and Discord Alerts

The worker writes a durable heartbeat to `worker_heartbeat` on every poll and job transition. `/api/status` exposes the heartbeat and `/api/health` returns HTTP 200 only when the worker heartbeat is current and Plex, Radarr, and Sonarr are healthy. The systemd worker unit uses `Type=notify` and `WatchdogSec=15min`; a worker that stops reporting to systemd is restarted automatically.

Create a Discord webhook under **Server Settings → Integrations → Webhooks**, choose an alert channel, and add the URL only to the untracked `.env` file:

```dotenv
CINESWARM_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
CINESWARM_NOTIFICATION_COOLDOWN=1800
CINESWARM_HEARTBEAT_STALE_SECONDS=60
```

Discord receives cooldown-deduplicated alerts for low storage, unhealthy service refreshes, terminal worker-job failures, failed downloads when enabled, and successful autonomous acquisitions. Alerts are sent through the bot to the currently paired channel, so `!cine move` moves chat and alerts together; the webhook remains a fallback. Bot tokens and webhook URLs are never written to SQLite or logs. For host-level monitoring, point Uptime Kuma or another external monitor at `http://192.168.1.23:8787/api/health`; this can detect a full machine or network outage that CineSwarm cannot report from the affected host.

A persistent dashboard unit is provided at `.devin/cineswarm-control.service`. Install it alongside the worker to keep the web control plane available after terminal sessions close:

```bash
cp .devin/cineswarm-control.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cineswarm-control.service
```
