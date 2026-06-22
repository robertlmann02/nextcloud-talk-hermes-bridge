#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 3 ]; then
  echo "Usage: $0 /path/to/nextcloud/occ /path/to/hermes_talk_bridge.key /path/to/hermes_talk_bridge.crt" >&2
  exit 2
fi
occ="$1"
key="$2"
cert="$3"
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
php "$occ" integrity:sign-app --privateKey="$key" --certificate="$cert" --path="$root"
