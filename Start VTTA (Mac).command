#!/bin/bash
# VTTA - Voice to Text Assistant : launcher for macOS
# Double-click to start VTTA. The very first time, right-click this file and choose Open.
# The first start sets everything up inside the VTTA folder; later starts open VTTA right away.
HERE="$(cd "$(dirname "$0")" && pwd)"
for engine in "$HERE/app/launcher.sh" "$HERE/tools/launcher.sh"; do
    if [ -f "$engine" ]; then exec /bin/bash "$engine" "$@"; fi
done
echo "  VTTA's launcher.sh is missing - please download VTTA again."
read -r -p "  Press Return to close. " _
