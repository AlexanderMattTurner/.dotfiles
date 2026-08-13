# shellcheck shell=bash
# Duplicati backup health — single source of truth.
#
# Duplicati writes one "fileset" row per completed backup version into each
# job's local database; the server database maps each job to that database.
# A recent fileset is the only honest proof a backup actually landed:
# Schedule.LastRun advances when a run is *triggered*, so it stays fresh even
# when every run since has failed.
#
# Every read opens the database with immutable=1. The job database is
# multi-GB and is written by the live duplicati-server, and immutable takes
# no locks at all — so doctor can never block on, or perturb, a running
# backup. The tradeoff is that a backup in flight reads as its pre-run state,
# which can only ever *under*-report freshness. That is the safe direction:
# this check exists to catch backups that stopped, and it must not invent a
# newer backup than the one on the remote.
#
# Consumers that must stay in sync (see CLAUDE.md "Backups"):
# bin/doctor.bash, tests/test_duplicati_status.py.

# A backup older than this many days is stale. The job runs daily; the slack
# absorbs a laptop that was asleep or offline at the scheduled hour.
DUPLICATI_STALE_DAYS="${DUPLICATI_STALE_DAYS:-3}"

# Path to Duplicati's server database. Overridable so tests can point at a
# fixture instead of the real one.
duplicati_server_db() {
    printf '%s\n' \
        "${DUPLICATI_SERVER_DB:-$HOME/Library/Application Support/Duplicati/Duplicati-server.sqlite}"
}

# Run a query against $1, printing nothing on any failure.
_duplicati_query() {
    sqlite3 "file:$1?mode=ro&immutable=1" "$2" 2>/dev/null
}

# Print the unix timestamp of the newest completed backup across all jobs.
# Exit codes let the caller tell the failure modes apart:
#   0  printed a timestamp
#   2  server database missing or unreadable
#   3  server database has no backup jobs configured
#   4  jobs exist but none has ever produced a fileset
duplicati_last_backup() {
    local server_db job_db jobs ts newest=""

    server_db="$(duplicati_server_db)"
    [ -f "$server_db" ] || return 2

    jobs="$(_duplicati_query "$server_db" 'SELECT DBPath FROM Backup;')" || return 2
    [ -n "$jobs" ] || return 3

    while IFS= read -r job_db; do
        if [ -z "$job_db" ] || [ ! -f "$job_db" ]; then
            continue
        fi
        ts="$(_duplicati_query "$job_db" 'SELECT MAX(Timestamp) FROM Fileset;')"
        # Guards an empty table (NULL -> "") and any sqlite error text.
        case "$ts" in '' | *[!0-9]*) continue ;; esac
        if [ -z "$newest" ] || [ "$ts" -gt "$newest" ]; then
            newest="$ts"
        fi
    done <<EOF
$jobs
EOF

    [ -n "$newest" ] || return 4
    printf '%s\n' "$newest"
}

# Classify backup health. Prints one of:
#   ok:<days>     newest backup is <days> old, within DUPLICATI_STALE_DAYS
#   stale:<days>  newest backup is <days> old — backups have stopped landing
#   never         a job is configured but has never completed
#   no-jobs       Duplicati is installed and running but backs nothing up
#   no-db         no server database — Duplicati has never been configured
#   no-sqlite     sqlite3 unavailable, so health is unknowable
duplicati_health() {
    local ts rc=0 now days

    command -v sqlite3 >/dev/null 2>&1 || {
        echo no-sqlite
        return 0
    }

    ts="$(duplicati_last_backup)" || rc=$?
    case "$rc" in
    2)
        echo no-db
        return 0
        ;;
    3)
        echo no-jobs
        return 0
        ;;
    4)
        echo never
        return 0
        ;;
    esac

    now="$(date +%s)"
    days=$(((now - ts) / 86400))
    if [ "$days" -le "$DUPLICATI_STALE_DAYS" ]; then
        printf 'ok:%s\n' "$days"
    else
        printf 'stale:%s\n' "$days"
    fi
}

# Count iCloud-evicted ("dataless") files under the given roots, defaulting to
# the two folders iCloud's Desktop & Documents sync evicts from.
#
# These are the silent hole in the backup: Duplicati cannot materialize a
# dataless placeholder, so it logs "Excluding path due to file locked ...
# Resource deadlock avoided" and moves on. The run still reports success, and
# the file is in no backup version — iCloud is its only copy.
duplicati_dataless_count() {
    local roots=("$@")
    [ "${#roots[@]}" -gt 0 ] || roots=("$HOME/Documents" "$HOME/Desktop")

    local existing=()
    local root
    for root in "${roots[@]}"; do
        [ -d "$root" ] && existing+=("$root")
    done
    [ "${#existing[@]}" -gt 0 ] || {
        echo 0
        return 0
    }

    find "${existing[@]}" -flags +dataless -print 2>/dev/null | wc -l | tr -d ' '
}
