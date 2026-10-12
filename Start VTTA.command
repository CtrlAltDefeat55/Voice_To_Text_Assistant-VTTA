#!/bin/bash
# macOS: double-click to start VTTA (the first start sets everything up).
# If macOS says it can't be opened, right-click this file and choose Open.
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$HERE/app/start-vtta.sh" ]; then exec "$HERE/app/start-vtta.sh" "$@"; fi
exec "$HERE/start-vtta.sh" "$@"
