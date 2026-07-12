#!/bin/sh
# Use the flet client bundled in this flatpak instead of the ~/.flet download
export FLET_VIEW_PATH=/app/share/flet-client
exec /app/bin/python3.14t /app/share/dlss-updater/main.py "$@"
