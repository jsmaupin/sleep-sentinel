#!/bin/sh
# Keep platform-specific environments outside the source directory.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$(uname -s)" in
  Darwin) local_root="$HOME/Library/Application Support" ;;
  *) local_root="${XDG_DATA_HOME:-$HOME/.local/share}" ;;
esac
export UV_PROJECT_ENVIRONMENT="$local_root/Sleep-Sentinel/venv"
export PYTHONDONTWRITEBYTECODE=1
cd "$project_dir"
exec uv "$@"
