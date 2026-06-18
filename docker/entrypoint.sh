#!/bin/sh
set -eu

: "${TEXTING_DATA_DIR:=/data}"
: "${TEXTING_DB:=$TEXTING_DATA_DIR/switchboard.sqlite}"
: "${TEXTING_MEDIA_DIR:=$TEXTING_DATA_DIR/media}"
: "${TEXTING_PUBLIC_UPLOAD_DIR:=$TEXTING_DATA_DIR/public-uploads}"
export TEXTING_DATA_DIR TEXTING_DB TEXTING_MEDIA_DIR TEXTING_PUBLIC_UPLOAD_DIR

mkdir -p "$TEXTING_DATA_DIR" "$TEXTING_MEDIA_DIR" "$TEXTING_PUBLIC_UPLOAD_DIR" "$(dirname "$TEXTING_DB")"

if [ "$(id -u)" = "0" ]; then
    chown -R switchboard:switchboard "$TEXTING_DATA_DIR" "$TEXTING_MEDIA_DIR" "$TEXTING_PUBLIC_UPLOAD_DIR" "$(dirname "$TEXTING_DB")"
    exec gosu switchboard "$@"
fi

exec "$@"
