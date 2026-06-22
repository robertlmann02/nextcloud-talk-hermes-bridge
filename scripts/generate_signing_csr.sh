#!/usr/bin/env bash
set -euo pipefail
app_id="hermes_talk_bridge"
out_dir="${1:-release/signing}"
mkdir -p "$out_dir"
key="$out_dir/${app_id}.key"
csr="$out_dir/${app_id}.csr"
if [ -e "$key" ] || [ -e "$csr" ]; then
  echo "Refusing to overwrite existing key/CSR in $out_dir" >&2
  exit 1
fi
openssl req -nodes -newkey rsa:4096 -keyout "$key" -out "$csr" -subj "/CN=${app_id}"
chmod 600 "$key"
echo "Private key: $key"
echo "CSR: $csr"
echo "Submit only the CSR to https://github.com/nextcloud/app-certificate-requests; keep the .key private."
