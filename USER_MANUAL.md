# CineSwarm User Manual

> **Private media operations for Plex, Radarr, Sonarr, Gemini, and Discord**  
> Live catalog intelligence, controlled acquisition, exact-release upgrades, autonomous discovery, durable monitoring, and audit-first operations.

---

## Current System at a Glance

| Component | Role | Current state |
|---|---|---|
| Plex | Playback library and watch-history source | Healthy |
| Radarr | Movie management, search, grabs, and imports | Healthy |
| Sonarr | Series management and queue visibility | Healthy |
| Gemini | Recommendations, discovery, and conversational answers | Connected |
| CineSwarm dashboard | LAN operations console | Active |
| CineSwarm worker | Scheduled maintenance and autonomous movie acquisition | Active |
| CineSwarm Discord bot | Private chat and approval interface | Active |
| Discord bot alerts | Operational alerts with webhook fallback | Active |

At the time of publication, CineSwarm tracks **10,575 movies** and **45 series**. Live values are always available with `!cine status` or the dashboard.

### Primary entry points

- **Discord:** mention `@Cineswarm` or begin a message with `!cine`
- **Dashboard:** `http://192.168.1.23:8787`
- **Health check:** `http://192.168.1.23:8787/api/health`

---

## Table of Contents

1. [What CineSwarm Does](#1-what-cineswarm-does)
2. [Discord Quick Start](#2-discord-quick-start)
3. [Add a New Movie](#3-add-a-new-movie)
4. [Inspect and Grab an Exact Release](#4-inspect-and-grab-an-exact-release)
5. [Recommendations and Discovery](#5-recommendations-and-discovery)
6. [AI Curation, Playlists, and Media Health](#6-ai-curation-playlists-and-media-health)
7. [Autonomous Movie Acquisition](#7-autonomous-movie-acquisition)
8. [Dashboard Guide](#8-dashboard-guide)
9. [Discord Alerts](#9-discord-alerts)
10. [Always-On Reliability](#10-always-on-reliability)
11. [Common Operating Procedures](#11-common-operating-procedures)
12. [Troubleshooting](#12-troubleshooting)
13. [Security and Safety Boundaries](#13-security-and-safety-boundaries)
14. [Administrator Reference](#14-administrator-reference)

---

## 1. What CineSwarm Does

CineSwarm maintains a normalized local catalog from the Radarr and Sonarr databases, compares that catalog with Plex, and uses Plex viewing history to understand collection preferences.

It can:

- Report Plex, Radarr, Sonarr, catalog, worker, and queue health.
- Answer conversational questions about the collection.
- Recommend movies that are absent from both Plex and Radarr.
- Validate recommendations through live Radarr lookup.
- Add and search movies immediately for the paired Discord user while preserving separate audited task boundaries.
- Inspect exact release names, including progress and Radarr warnings.
- Grab an exact release immediately after live revalidation, with warning overrides recorded in the decision ledger.
- Discover and automatically acquire strongly matching movies under queue-based growth gates (score 70+, concurrent ≤2).
- Monitor imports and refresh Plex after files arrive.
- Send Discord alerts for important operational events.
- Recover from process crashes and stalled workers through systemd watchdogs.

### Important operating principle

> Read-only questions execute immediately. Full autopilot executes paired-user and background decisions without confirmation, while duplicate checks, queue limits, storage floors, emergency stop, revalidation, decision logging, and Discord reporting remain mandatory.

---

## 2. Discord Quick Start

CineSwarm responds only to the paired Discord user in the paired server and channel.

Use either:

```text
!cine COMMAND
```

or mention the bot:

```text
@Cineswarm COMMAND
```

### Command reference

| Command | Purpose | Writes data? |
|---|---|---:|
| `!cine help` | Show the command list | No |
| `!cine status` | Show service, worker, catalog, and queue health | No |
| `!cine health` | Alias for status | No |
| `!cine monitor` | Live ops digest from `/api/monitoring/snapshot` (queues, worker, top issues) | No |
| `!cine queue` | List actual Radarr/Sonarr releases, states, progress, remaining size, time, and errors | No |
| `!cine discover` | Show ranked missing candidates with acquisition IDs | No |
| `!cine discover refresh` | Run Gemini discovery immediately and show verified results | Local queue only |
| `!cine acquire CANDIDATE_ID` | Immediately add and search a verified discovery candidate | Yes |
| `!cine franchise` | Scan collection and discover missing franchise sequels/prequels | Local queue only |
| `!cine recommendations` | Alias for discovery | No |
| `!cine add TITLE (YEAR)` | Immediately add and search an exact Radarr title | Yes |
| `!cine plan TITLE` | Check Radarr acquisition plan without adding | No |
| `!cine release RELEASE_NAME` | Inspect an exact release and its queue state | No |
| `!cine grab RELEASE_NAME` | Immediately revalidate and grab an exact release, including warned releases | Yes |
| `!cine watch [MINS] [GENRE]` | Smart Movie Concierge tailored to available time & genre | No |
| `!cine digest` | Daily library spotlight digest & stats | No |
| `!cine scan_plex` | Trigger an immediate Plex library scan | Yes |
| `!cine scan_health` | Run ffprobe/ffmpeg integrity inspection across catalog | Yes |
| `!cine sync_collections` | Automatically group films into Plex native franchise collections | Yes |
| `!cine curate THEME` | AI-curate and tag a thematic collection directly in Plex | Yes |
| `!cine playlist THEME` | AI-curate a custom playlist directly in Plex | Yes |
| `!cine commentary TITLE` | Generate timestamped AI Director Commentary track (SRT) | No |
| `!cine chapters TITLE` | Generate narrative chapter markers & scene summaries (WebVTT) | No |
| `!cine decisions` | Show the latest decision ledger entries | No |
| `!cine decision DECISION_ID` | Show reasons and outcome for one decision | No |
| `!cine feedback DECISION_ID good|bad NOTE` | Record preference feedback for future discovery context | Local learning data |
| `!cine confirm TASK_ID` | Legacy/manual confirmation for tasks created outside full autopilot | Yes |
| `!cine pair CODE` | Pair the initial server, channel, and user | Configuration |
| `!cine move CODE` | Move the bot to another channel in the paired server | Configuration |

### Conversational chat

Anything that is not a recognized command is sent to the read-only CineSwarm agent swarm.

Examples:

```text
!cine what should I watch tonight?
```

```text
!cine recommend a tense science-fiction movie I do not own
```

```text
!cine what genres are overrepresented in my library?
```

```text
!cine are Plex, Radarr, and Sonarr healthy?
```

Responses use the local catalog, service snapshots, reconciliation report, and Gemini model. Conversational chat cannot silently add, search, grab, delete, or modify media.

---

## 3. Add a New Movie

Full autopilot executes trusted requests from the paired Discord user immediately. Add and search remain separate audited tasks internally, but no confirmation message is required.

### Step 1: Request the movie

```text
!cine add Heat (1995)
```

CineSwarm will:

1. Parse the title and year.
2. Perform an exact Radarr lookup.
3. Verify the TMDB identity.
4. Check Plex and the local catalog for duplicates.
5. Select the configured movie root folder.
6. Select the `HD-1080p` quality profile.
7. Create and execute the audited add task.
8. Create and execute the audited search task.
9. Record the reasons, task IDs, service ID, command ID, and outcome in the decision ledger.
10. Post the decision to Discord.

The response includes the decision ID for later review or feedback.

### Step 2: Monitor progress

```text
!cine queue
```

or:

```text
!cine status
```

After import, the worker records the outcome and requests a cooldown-limited Plex refresh.

### Duplicate behavior

If a movie is already managed by Radarr, CineSwarm refuses to add it again. Use the release inspection and grab workflow for upgrades.

---

## 4. Inspect and Grab an Exact Release

CineSwarm understands scene-style and indexer release names.

Example:

```text
The.Big.Lebowski.1998.BluRay.2160p.DV.HDR.DTS-HD.AC3.HEVC.NL-RetailSub.REMUX
```

### Inspect a release

```text
!cine release The.Big.Lebowski.1998.BluRay.2160p.DV.HDR.DTS-HD.AC3.HEVC.NL-RetailSub.REMUX
```

You may also paste the raw release name directly after `!cine`.

CineSwarm reports:

- Parsed movie title and year
- Radarr movie ID
- Exact release availability
- Indexer
- Total size
- Queue and tracked-download state
- Download percentage and remaining size
- Radarr rejection or warning reasons

Common Radarr flags include:

- Maximum-size violations
- Hardcoded subtitle detection
- Quality cutoff already met
- Custom-format score rejection
- Language mismatch
- Existing queue item

Inspection never grabs the release.

### Grab an exact release

```text
!cine grab RELEASE_NAME
```

Full autopilot executes the paired-user request immediately. Before grabbing, CineSwarm revalidates:

1. The Radarr movie still exists.
2. The release is not already queued.
3. The exact title still matches.
4. The release GUID still matches.
5. The indexer ID still matches.
6. The release remains available through interactive search.

CineSwarm then submits the minimal Radarr grab payload, records the indexer, size, warning overrides, task outcome, and decision ID, and posts the decision to Discord.

> Full autopilot is configured to allow Radarr warning overrides for exact releases requested by the paired user. Duplicate queue entries, missing releases, identity mismatches, emergency stop, and unavailable services still block execution.

### Monitor the grabbed release

```text
!cine release RELEASE_NAME
```

This provides live queue status and progress.

---

## 5. Recommendations and Discovery

### View the ranked discovery queue

```text
!cine discover
```

CineSwarm returns up to ten missing candidates with a durable candidate ID, release year, score, and the exact `acquire` command.

### Refresh discovery immediately

```text
!cine discover refresh
```

This runs Gemini discovery now, validates every suggestion through Radarr/Sonarr, discards existing titles, stores verified candidates, and returns the refreshed queue.

### Acquire a discovery candidate

Choose an ID shown by `!cine discover`:

```text
!cine acquire CANDIDATE_ID
```

CineSwarm revalidates the candidate, selects the configured root folder and `HD-1080p` profile, executes the add and search tasks immediately, records the complete decision and outcome, and reports the action to Discord.

This gives discovery a direct path to downloading without confirmation while preserving separate internal add/search tasks, revalidation, audit records, and emergency controls.

### How discovery works

The daily discovery pass:

1. Builds a taste profile from Plex playback history.
2. Adds catalog genre and era patterns.
3. Requests distinctive candidates from Gemini.
4. Validates each title through Radarr or Sonarr lookup.
5. Requires exact normalized title and year matching.
6. Rejects titles already in Plex or the Radarr/Sonarr catalog.
7. Rejects provider-ID duplicates.
8. Scores the remaining candidates.
9. Stores them in the durable discovery queue.

### Recommendation quality safeguards

CineSwarm checks both canonical external IDs and normalized titles. This protects against:

- Different punctuation
- Missing Plex provider IDs
- Movie/series title collisions
- Wrong-year lookup results
- Stale discovery candidates
- Titles already added since discovery ran

### Franchise completeness scanning

```text
!cine franchise
```

CineSwarm samples your catalog, detects major film/TV franchises (e.g. Godzilla, James Bond, Marvel, DC, Star Trek, animated universes, horror sagas), and identifies missing sequels, prequels, spin-offs, and recent additions. High-priority entries are scored at 95 and queued with franchise metadata for immediate acquisition.

---

## 6. AI Curation, Playlists, and Media Health

### AI Thematic Collections & Playlists in Plex

Curate custom collections or playlists directly inside Plex using plain natural language:

```text
!cine curate 90s cyberpunk neo-noir thrillers
```

```text
!cine playlist high-octane 80s action synthwave
```

CineSwarm selects matching films from your library, applies native collection tags or builds custom Plex playlists, and reports tagged items immediately.

### Native Franchise Collection Syncing

```text
!cine sync_collections
```

Automatically scans movie franchise collection metadata from Radarr/TMDB and creates native, structured collections in Plex with zero manual tagging.

### Watch Tonight Concierge & Daily Digest

Need a movie suggestion matching a specific time window or genre?

```text
!cine watch 90 comedy
```

```text
!cine watch 120 sci-fi
```

```text
!cine digest
```

`watch` filters your verified catalog by runtime and genre, returning concise picks. `digest` summarizes vault stats and top spotlights.

### Media Health & Corruption Auto-Healing

```text
!cine scan_health
```

CineSwarm uses `ffprobe` and `ffmpeg` to inspect video files for missing headers, zero-byte errors, missing audio/video streams, unreadable containers, and corrupted frames. When corrupted media is detected, CineSwarm updates catalog metadata and can trigger auto-healing re-search tasks.

### AI Scene Chapters & Director Commentary

Generate rich narrative chapter summaries and director commentary subtitle tracks for any movie:

```text
!cine chapters Inception (2010)
```

```text
!cine commentary The Matrix (1999)
```

`chapters` generates 6–10 narrative chapter breakdowns with exact timestamps in WebVTT format. `commentary` creates a 10–15 marker behind-the-scenes trivia track formatted as an SRT subtitle overlay.

---

## 7. Autonomous Movie Acquisition

Autonomous movie add-and-search is currently enabled.

### Current live policy

| Policy | Current value |
|---|---:|
| Full autopilot | Enabled |
| Automatic add, search, acquire, and exact grab | Enabled |
| Growth budget mode | Queue-based only; no weekly cap |
| Estimated storage recorded per automatic movie | 20 GB for reference |
| Minimum discovery score | 70 |
| Near-miss promote floor | 68 (≤1/hour; theatrical + affinity gates) |
| Required quality profile | `HD-1080p` |
| Minimum free storage | 500 GB |
| Maximum concurrent downloads | 2 |
| Recent-release window | Last 2 years |
| Recent weekly target | First 3 slots |
| Older catalog-gap target | Final 2 slots |
| Emergency stop | Off |

Historical weekly usage remains recorded for reference and learning, but it no longer blocks growth. Queue concurrency and free-space policy now control throughput.

### Allowed genres

- Action
- Animation
- Comedy
- Crime
- Thriller
- Science Fiction
- Horror
- Adventure
- Fantasy
- Mystery

### Forbidden genres

- Reality
- Documentary
- Music
- Game-Show
- News
- Sport
- Talk
- Western

### Decision sequence

Before an automatic add, the worker checks:

1. Emergency stop is off.
2. Autonomous add/search is enabled.
3. Radarr queue count is below two.
4. Weekly movie and estimated storage budgets permit another addition.
5. Discovery is configured and has candidates.
6. Candidate is a movie.
7. Candidate score is at least 90.
8. Candidate fits the recent/older weekly slot.
9. Candidate passes allowed and forbidden genre filters.
10. Candidate is absent from Plex and Radarr.
11. Root folder and `HD-1080p` profile still exist.
12. Storage has at least 500 GB free.

The worker then creates and audits separate add and search tasks, records weekly usage, synchronizes the catalog, and monitors the outcome.

### Pause conditions

Autonomous acquisition pauses automatically when:

- Two or more Radarr queue records exist
- Free storage falls below 500 GB
- Emergency stop is active
- No candidate reaches score 90
- No candidate fits the required recent/older slot
- Required Radarr configuration is missing

### Emergency stop

Use the dashboard’s **Emergency stop** control. Emergency stop takes precedence over every autonomous policy and prevents new automatic actions.

Manual read-only status and recommendation commands remain available.

### Decision ledger and learning

Every autopilot execute, skip, block, override, and failure receives a durable UUID with actor, category, subject, reasons, outcome, and timestamps. The dashboard shows recent decisions and Discord can query them:

```text
!cine decisions
!cine decision DECISION_ID
```

Record whether a decision was useful:

```text
!cine feedback DECISION_ID good Great recommendation
!cine feedback DECISION_ID bad Wrong tone for my library
```

Feedback is joined into future Gemini discovery context, creating a persistent preference history without weakening safety controls.

---

## 8. Dashboard Guide

Open:

```text
http://192.168.1.23:8787
```

### Main controls

| Control | Action |
|---|---|
| Refresh services | Refresh Plex, Radarr, and Sonarr snapshots |
| Reconcile library | Compare Plex provider IDs with Radarr/Sonarr state |
| Analyze movie quality | Report file quality and advisory upgrade candidates |
| Analyze series completeness | Report Sonarr episode completeness |
| Playback profile | Show Plex-derived viewing preferences |
| Request Plex scan | Create a pending Plex refresh task |
| Approve pending action | Execute the selected pending task |
| Find new candidates | Run Gemini discovery immediately |
| Plan an acquisition | Perform a read-only Radarr/Sonarr lookup |
| Emergency stop | Enable or disable autonomous acquisition |

### Dashboard status panels

The dashboard displays:

- Catalog movie and series counts
- Plex, Radarr, and Sonarr health
- Worker heartbeat status and age
- Recent acquisition observations
- Discovery queue
- Autonomous policy and weekly budget
- Recent audit events

### Health endpoint

```text
http://192.168.1.23:8787/api/health
```

It returns HTTP 200 only when:

- The worker heartbeat is current
- Plex is healthy
- Radarr is healthy
- Sonarr is healthy
- All three service snapshots are present

Use this endpoint with Uptime Kuma or another external monitor.

### Unified monitoring snapshot

```text
http://192.168.1.23:8787/api/monitoring/snapshot
http://192.168.1.23:8787/api/v1/monitoring/snapshot
```

Returns compact JSON for external watchers and Discord live monitor:

- `overall_status` — `healthy`, `degraded`, or `unhealthy`
- Service rows, worker heartbeat/age, download queue counts
- Pending approval task counts, bounded recent failure summary
- `emergency_stop` flag and `generated_at`

The endpoint is read-only, free of secrets, and available without dashboard authentication (same model as `/api/health`). Uptime Kuma can keep using `/api/health` for binary up/down checks; use the snapshot when a watcher needs richer queue and failure context.

Discord:

```text
!cine monitor
/monitor
```

Formats a short ops digest from the same snapshot (overall status, queue depths, worker freshness, top issues). It complements — and does not replace — existing `!cine status` / `!cine health`.

### External monitor webhook

Optional env `CINESWARM_MONITOR_WEBHOOK`. On worker `service_refresh` health checks, CineSwarm POSTs a small JSON payload when overall status **transitions** to `unhealthy`/`degraded` or recovers to `healthy`. Same-status refresh cycles are suppressed. If unset, behavior is unchanged.

Example payload:

```json
{
  "event_type": "monitor_status_transition",
  "status": "degraded",
  "previous_status": "healthy",
  "unhealthy_services": ["sonarr"],
  "worker_healthy": true,
  "emergency_stop": false,
  "timestamp": "2026-09-07T17:00:00+00:00"
}
```

This webhook is independent of Discord chat alerts (`CINESWARM_DISCORD_WEBHOOK` / `CINESWARM_NOTIFICATION_WEBHOOK`). Use it for PagerDuty, ntfy, Slack incoming webhooks, or custom collectors.

The Overview dashboard includes a Monitoring snapshot card with overall status, queue counts, last alert, and a link to `/api/monitoring/snapshot`.

---

## 9. Discord Alerts

CineSwarm sends formatted Discord embeds for operational events. The worker uses the bot token and current paired-channel policy as the primary route; the standalone webhook remains a fallback. Moving the bot with `!cine move CODE` therefore moves both chat and future alerts to the new channel.

### Alert glossary

| Alert | Meaning | Recommended response |
|---|---|---|
| Test Alert | Webhook delivery verification | No action |
| Storage Alert | A media root is below the minimum free-space threshold | Free space before adding media |
| Service Unhealthy | Plex, Radarr, or Sonarr refresh failed | Check the named service and network |
| Job Failed | A durable worker job exhausted all retries | Review job type and error details |
| Downloads Failed | Radarr/Sonarr currently reports failed queue items | Inspect the queue and retry proposal |
| Downloads Healthy | Corrective or recovery status message | No action |
| Autonomous Acquisition | Worker automatically added and searched a candidate | Review title and Radarr queue |
| Autonomous Recovery | A partial autonomous operation was repaired | Review resulting search and budget |

### Alert deduplication

Alerts with the same event key are suppressed for 30 minutes by default. This prevents repeated failures from flooding the Discord channel.

Zero-count failed-download checks do not send alerts.

### Alert privacy

Webhook URLs, API keys, Plex tokens, Discord bot tokens, passwords, and secrets are never written to SQLite audit details or normal logs.

---

## 10. Always-On Reliability

CineSwarm runs three enabled user services.

| Service | Purpose | Watchdog |
|---|---|---:|
| `cineswarm-control.service` | Dashboard and API | Process restart |
| `cineswarm-worker.service` | Schedules, monitoring, discovery, and autonomy | 15 minutes |
| `cineswarm-discord.service` | Discord Gateway bot | 2 minutes |

All services use `Restart=always`. User lingering is enabled, allowing the services to start without an interactive login.

### Worker schedule

| Job | Frequency |
|---|---:|
| Queue monitor | Every 5 minutes |
| Service refresh | Every 15 minutes |
| Catalog sync | Every 30 minutes |
| Failed-download inspection | Every 30 minutes |
| Autonomous acquisition | Every hour |
| Reconciliation | Every hour |
| Franchise completeness scan | Every 4 hours |
| Media health & corruption scan | Every 12 hours |
| Discovery refresh | Daily |
| Daily library digest | Daily |

### Durable job behavior

Worker jobs include:

- SQLite persistence
- Atomic leases
- Recovery of expired leases
- Up to five attempts
- Exponential retry backoff
- Terminal-failure alerts
- Audit events
- Heartbeat updates on every poll and job transition

### Worker heartbeat

The worker writes its state to `worker_heartbeat` as:

- `starting`
- `idle`
- `running`
- `healthy`
- `retry`
- `failed`
- `stopped`

The heartbeat is shown in the dashboard and `/api/status`.

---

## 11. Common Operating Procedures

### Check everything quickly

Discord:

```text
!cine status
```

Terminal:

```bash
systemctl --user is-active cineswarm-control.service cineswarm-worker.service cineswarm-discord.service
```

Health API:

```bash
curl -fsS http://192.168.1.23:8787/api/health
```

### Check active downloads

```text
!cine queue
```

The response is split into Radarr and Sonarr sections and lists each actual release name, queue status, tracked state, percentage complete, remaining GiB, available time-left estimate, and error message.

For an exact release:

```text
!cine release RELEASE_NAME
```

### Ask for recommendations

```text
!cine recommend three crime movies I do not own
```

or:

```text
!cine discover
```

### Move the Discord bot to another channel

In the new channel, from the already-paired user in the paired server:

```text
!cine move ONE_TIME_SETUP_CODE
```

The new channel becomes active immediately, the old channel is deauthorized, and operational alerts follow the bot into the new channel automatically.

### Run a catalog sync manually

```bash
cd /home/neo/workspace/cineswarm
python3 cineswarm_sync.py
```

This reads the Radarr and Sonarr databases and updates the local CineSwarm catalog and markdown snapshot. It does not modify Radarr or Sonarr.

### Run one due worker job

```bash
cd /home/neo/workspace/cineswarm
python3 cineswarm_worker.py --once
```

This may execute a due job with real side effects if autonomous policy permits it. Use the dashboard emergency stop first when testing worker behavior without acquisitions.

### Run the test suite

```bash
cd /home/neo/workspace/cineswarm
.venv/bin/python -m unittest -v
```

The current suite contains **37 tests** covering catalog identifiers, Plex parsing, discovery deduplication and acquisition, acquisition safety, detailed queue formatting, exact-release grabs, worker budgets, queue monitoring, Discord authorization, pairing, channel migration, alerts, heartbeat, and watchdog-related behavior.

### View recent logs

```bash
journalctl --user -u cineswarm-worker.service -n 100 --no-pager
```

```bash
journalctl --user -u cineswarm-discord.service -n 100 --no-pager
```

```bash
journalctl --user -u cineswarm-control.service -n 100 --no-pager
```

### Restart services

```bash
systemctl --user restart cineswarm-control.service cineswarm-worker.service cineswarm-discord.service
```

### Confirm watchdog state

```bash
systemctl --user show cineswarm-worker.service cineswarm-discord.service \
  -p ActiveState -p SubState -p WatchdogUSec -p WatchdogTimestampMonotonic -p NRestarts
```

---

## 12. Troubleshooting

### The Discord bot does not answer

Check, in order:

1. Message begins with `!cine` or mentions `@Cineswarm`.
2. You are using the paired Discord identity.
3. You are in the paired server.
4. You are in the paired channel.
5. The bot appears online.
6. Message Content Intent is enabled in the Discord Developer Portal.
7. The bot has View Channel, Send Messages, Read Message History, and Embed Links permissions.

Then run:

```bash
systemctl --user status cineswarm-discord.service --no-pager
journalctl --user -u cineswarm-discord.service -n 100 --no-pager
```

If you intentionally changed channels, use `!cine move CODE` in the new channel.

### CineSwarm says a movie already exists

Use:

```text
!cine release RELEASE_NAME
```

or search Radarr interactively. `add` manages new movies; `release` and `grab` manage upgrades for existing movies.

### A release is available but has warnings

Warnings come from Radarr and may include size, subtitles, language, custom formats, or cutoff status. Inspection is read-only. Under full autopilot, `grab` displays and records those warnings, then executes the paired-user request immediately. Review the resulting decision entry and queue state if the override was not desirable, and record `bad` feedback for future reference.

### A release name is parsed incorrectly

Use the explicit command and include the complete release name:

```text
!cine release COMPLETE.RELEASE.NAME
```

CineSwarm identifies the movie from the text before the first four-digit release year.

### A download search completed but no file appeared

The worker distinguishes:

- `search_running`
- `download_active`
- `download_failed`
- `imported`
- `search_completed_no_file`
- `monitor_unavailable`

Active queue items are not treated as failures. A true no-file outcome can create one approval-gated retry proposal; it cannot loop silently.

### Discord reports Downloads Failed

Run:

```text
!cine queue
```

CineSwarm sends this alert only when Radarr or Sonarr currently reports one or more failed queue items. A zero-failure check does not alert.

### Worker heartbeat is stale

```bash
systemctl --user status cineswarm-worker.service --no-pager
journalctl --user -u cineswarm-worker.service -n 100 --no-pager
```

The worker systemd watchdog restarts a process that stops reporting for 15 minutes. Long-running jobs use the lease interval before being considered stale by the health API.

### `/api/health` returns HTTP 503

Inspect the response body. It identifies stale worker state and unhealthy services. Then check:

```bash
curl -s http://192.168.1.23:8787/api/status
```

and the relevant systemd journal.

### Autonomous acquisition is not adding anything

Typical reasons:

- Radarr queue count is two or more
- Weekly five-movie limit reached
- Estimated 200 GB budget reached
- No candidate scores 90 or higher
- Candidate does not fit the recent/older slot
- Genre is not allowed
- Candidate already exists
- Less than 500 GB is free
- Emergency stop is enabled

The absence of an automatic add is usually a policy decision, not a failure.

---

## 13. Security and Safety Boundaries

### Secrets

The following belong only in the untracked `.env` file:

- Plex token
- Radarr API key
- Sonarr API key
- Gemini API key
- Discord webhook URL
- Discord bot token
- Discord one-time pairing code

Never paste these values into Discord chat, issue trackers, source files, or commits.

### Discord authorization

The bot enforces all three:

- Paired server ID
- Paired channel ID
- Paired user ID

Only the paired Discord identity in the paired channel can issue trusted full-autopilot commands. Legacy pending tasks remain bound to the identity that created them.

### Write boundaries

| Action | Full-autopilot behavior |
|---|---|
| Status, health, queue, recommendations | Immediate read-only |
| Paired-user add | Revalidate, add, search, log, and notify immediately |
| Discovery acquire | Revalidate, add, search, log, and notify immediately |
| Release inspection | Immediate read-only |
| Paired-user exact grab | Revalidate, grab, log warning overrides, and notify immediately |
| Plex refresh | Automatic when reconciliation and cooldown policy permit |
| Background discovery add/search | Executes only after queue, storage, duplicate, genre, score, and emergency gates pass |
| Failed-download retry | Executes once automatically, then records the outcome without looping |
| Legacy `confirm` task | Available for older or externally created pending tasks |
| Delete, move, metadata edit | Not implemented |

### Actions CineSwarm does not perform

CineSwarm does not:

- Delete media files
- Move media manually
- Delete Plex items
- Modify Plex metadata
- Remove Radarr or Sonarr entries
- Change users or permissions
- Disable security controls
- Silently loop failed searches

---

## 14. Administrator Reference

### Important files

| File | Purpose |
|---|---|
| `cineswarm_control.py` | Dashboard, policy, tasks, approvals, service connectors |
| `cineswarm_agents.py` | Chat tools, reconciliation, acquisition planner |
| `cineswarm_discovery.py` | Gemini discovery, deduplication, scoring |
| `cineswarm_worker.py` | Durable schedules, autonomy, monitoring, Discord alerts |
| `cineswarm_discord.py` | Private Discord Gateway bot |
| `cineswarm_sync.py` | Radarr/Sonarr database catalog synchronization |
| `cineswarm_catalog.db` | Durable normalized media catalog |
| `cineswarm_control.db` | Tasks, audits, policies, jobs, observations, heartbeat |
| `LATEST_VAULT_ADDITIONS.md` | Human-readable catalog snapshot |
| `requirements.txt` | Pinned Discord bot dependencies |
| `.env` | Untracked local credentials and environment configuration |

### Systemd units

Repository copies:

```text
.devin/cineswarm-control.service
.devin/cineswarm-worker.service
.devin/cineswarm-discord.service
```

Installed copies:

```text
~/.config/systemd/user/cineswarm-control.service
~/.config/systemd/user/cineswarm-worker.service
~/.config/systemd/user/cineswarm-discord.service
```

### Databases

#### `cineswarm_catalog.db`

Contains normalized movie/series records and sync history. Source databases are opened read-only.

#### `cineswarm_control.db`

Contains:

- Service snapshots
- Audit events
- Approval tasks
- Acquisition observations
- Discovery candidates
- Autonomous policy
- Autonomous action history
- Weekly budgets
- Durable worker jobs and schedules
- Worker heartbeat
- Notification cooldown state

### Configuration precedence

Stored autonomous policy values take precedence over environment defaults for worker decision criteria. Service credentials and Discord secrets are loaded from `.env` into process memory.

### Reinstall the Discord environment

```bash
cd /home/neo/workspace/cineswarm
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Reload changed systemd units

```bash
systemctl --user daemon-reload
systemctl --user restart cineswarm-control.service cineswarm-worker.service cineswarm-discord.service
```

---

## Quick Reference Card

```text
STATUS
!cine status

QUEUES
!cine queue

DISCOVERY & FRANCHISE
!cine discover
!cine discover refresh
!cine acquire CANDIDATE_ID
!cine franchise

AI CURATION & PLAYLISTS
!cine curate Theme
!cine playlist Theme
!cine watch [Minutes] [Genre]
!cine digest

AI CHAPTERS & COMMENTARY
!cine chapters Movie (Year)
!cine commentary Movie (Year)

PLEX & MEDIA HEALTH
!cine scan_plex
!cine scan_health
!cine sync_collections

CHAT
!cine what should I watch tonight?

ADD AND SEARCH MOVIE
!cine add Title (Year)

INSPECT RELEASE
!cine release COMPLETE.RELEASE.NAME

GRAB EXACT RELEASE
!cine grab COMPLETE.RELEASE.NAME

DECISION HISTORY
!cine decisions
!cine decision DECISION_ID
!cine feedback DECISION_ID good|bad NOTE

MOVE DISCORD CHANNEL
!cine move ONE_TIME_SETUP_CODE

DASHBOARD
http://192.168.1.23:8787

HEALTH
http://192.168.1.23:8787/api/health
```

---

*CineSwarm is designed to observe broadly, validate carefully, act deliberately, and leave an audit trail for every write.*
