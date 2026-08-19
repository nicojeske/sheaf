#!/usr/bin/env bash
# Launch Sheaf.
#
# PyGObject cannot be installed from PyPI reliably on Arch, so the venv is
# created with --system-site-packages and inherits the system python-gobject,
# gtk4 and libadwaita. Everything else comes from PyPI via uv.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
    uv venv --system-site-packages
fi
uv pip install --quiet -e .
exec .venv/bin/python -m sheaf "$@"
