#!/usr/bin/env bash
# Download one YouTube URL through the normal pipeline, using the project
# virtualenv and the local worker .env. Nothing is read from or written to
# the application database.
#
# Usage: bash scripts/test-local-download.sh "<youtube-url>" [extra video-downloader flags...]

set -euo pipefail

if [ "$#" -eq 0 ]; then
    echo "Usage: $0 \"<youtube-url>\" [extra video-downloader flags...]" >&2
    exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

uv sync
exec uv run video-downloader test-url "$@"
