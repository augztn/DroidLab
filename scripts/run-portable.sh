#!/usr/bin/env bash
set -euo pipefail

# Run from any working directory; do not rely on the caller's current directory.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-${REPO_ROOT}/build}"

APP="${BUILD_DIR}/payload-dumper-gui"

if [[ ! -x "${APP}" ]]; then
  printf 'Payload Dumper has not been built yet. Expected executable: %s\n' "${APP}" >&2
  printf 'See docs/LINUX_APPIMAGE_PLAN.md for the planned build and packaging flow.\n' >&2
  exit 127
fi

exec "${APP}" "$@"
