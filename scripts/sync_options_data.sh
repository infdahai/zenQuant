#!/bin/bash
# Sync Deribit options snapshots from S3 to local.
# Usage: bash scripts/sync_options_data.sh

set -euo pipefail

BUCKET="${S3_BUCKET:-zenquant-data}"
LOCAL_DIR="data/raw/deribit_options"

mkdir -p "$LOCAL_DIR"
aws s3 sync "s3://${BUCKET}/deribit_options/" "$LOCAL_DIR/" --quiet
echo "Synced options data to $LOCAL_DIR"
ls -lh "$LOCAL_DIR"/BTC/*/ 2>/dev/null | tail -5
