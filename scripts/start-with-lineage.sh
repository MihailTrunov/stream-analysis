#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(dirname -- "$script_dir")
cd "$project_root"

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
