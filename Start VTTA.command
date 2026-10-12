#!/bin/bash
# macOS: double-click to start VTTA (first run sets everything up).
# If macOS says it can't be opened, right-click this file and choose Open.
exec "$(dirname "$0")/start-vtta.sh" "$@"
