#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Tear down the compose project and remove all volumes (including data).
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

echo "WARNING: This will stop all containers and delete all volumes (including persistent data)."
read -rp "Are you sure you want to continue? [y/N]: " answer

if [[ "${answer,,}" =~ ^y(es)?$ ]]; then
  docker compose -f "$SCRIPT_DIR/docker-compose.yaml" down -v
  echo "All containers and volumes have been removed."
else
  echo "Operation cancelled."
fi
