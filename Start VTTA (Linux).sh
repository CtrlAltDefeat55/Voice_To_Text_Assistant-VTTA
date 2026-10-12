#!/bin/bash
# VTTA - Voice to Text Assistant : launcher for Linux
# Run:  ./"Start VTTA (Linux).sh"   (or double-click and choose Run, if your file manager offers it)
# The first start sets everything up inside the VTTA folder; later starts open VTTA right away.
HERE="$(cd "$(dirname "$0")" && pwd)"
for engine in "$HERE/app/launcher.sh" "$HERE/tools/launcher.sh"; do
    if [ -f "$engine" ]; then exec /bin/bash "$engine" "$@"; fi
done
echo "  VTTA's launcher.sh is missing - please download VTTA again."
read -r -p "  Press Return to close. " _
