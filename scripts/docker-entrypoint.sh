#!/bin/sh
# Hosted volumes (Fly, Railway, Render) are mounted root-owned, which hides the image's chown of
# /app/data. Fix ownership while still root, then drop to the unprivileged app user.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/data
  chown -R app:app /app/data
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
