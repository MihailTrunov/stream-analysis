#!/bin/sh
set -eu

if [ "${1:-}" = "--seed-demo" ]; then
    STREAM_ANALYSIS_SEED_REPLAY_DEMO=1
    export STREAM_ANALYSIS_SEED_REPLAY_DEMO
    shift
fi
STREAM_ANALYSIS_SEED_REPLAY_DEMO=${STREAM_ANALYSIS_SEED_REPLAY_DEMO:-0}
export STREAM_ANALYSIS_SEED_REPLAY_DEMO

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(dirname -- "$script_dir")
cd "$project_root"

explicit_env_file=false
for argument in "$@"; do
    case "$argument" in
        --env-file|--env-file=*) explicit_env_file=true ;;
    esac
done
if [ "$explicit_env_file" = false ] && [ ! -f .env ] && [ -f app/.env ]; then
    set -- --env-file app/.env "$@"
    echo "Using app/.env for local startup." >&2
fi

if revision=$(git rev-parse --verify HEAD 2>/dev/null); then
    STREAM_ANALYSIS_GIT_COMMIT=$revision
    STREAM_ANALYSIS_GIT_DIRTY=
    if status_output=$(git status --porcelain --untracked-files=normal 2>/dev/null); then
        STREAM_ANALYSIS_GIT_DIRTY=false
        if [ -n "$status_output" ]; then
            STREAM_ANALYSIS_GIT_DIRTY=true
        fi
    fi
    STREAM_ANALYSIS_BUILD_ID=${STREAM_ANALYSIS_BUILD_ID:-$revision}
    export STREAM_ANALYSIS_GIT_COMMIT STREAM_ANALYSIS_GIT_DIRTY STREAM_ANALYSIS_BUILD_ID
fi

exec docker compose "$@" up --build
