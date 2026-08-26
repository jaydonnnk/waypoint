#!/bin/sh
# Free-tier persistence workaround for the atlas-flight OS-keyring auth
# (docs/external/atlas-integration.md "Known issues" — the plaintext
# keyrings.alt backend is the disclosed, sandbox-only tradeoff already
# accepted there; this script just survives container restarts on a
# Render plan with no persistent disk).
#
# ATLAS_KEYRING_B64 carries ONLY the auth "api-credentials" entry
# (extracted deliberately — the raw keyring file also caches bulky
# per-search offer secrets that are neither needed nor worth shipping).
# Decoded to keyrings.alt's Linux path (keyring/util/platform_.py
# _data_root_Linux: $XDG_DATA_HOME or ~/.local/share, + python_keyring/
# keyring_pass.cfg) BEFORE uvicorn starts, so every atlas-flight
# subprocess call finds it already authorized — no shell / manual
# auth login+poll needed on each redeploy.
#
# Missing/empty var = no-op (fail-closed by omission, same posture as
# every other env-gated switch in this codebase): the app still starts;
# atlas-flight just reports AUTHORIZATION_REQUIRED until someone sets it.
set -eu

if [ -n "${ATLAS_KEYRING_B64:-}" ]; then
    DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
    KEYRING_DIR="$DATA_HOME/python_keyring"
    mkdir -p "$KEYRING_DIR"
    echo "$ATLAS_KEYRING_B64" | base64 -d > "$KEYRING_DIR/keyring_pass.cfg"
    echo "restore_atlas_keyring: wrote $KEYRING_DIR/keyring_pass.cfg from ATLAS_KEYRING_B64"
else
    echo "restore_atlas_keyring: ATLAS_KEYRING_B64 not set, skipping (atlas-flight will read AUTHORIZATION_REQUIRED)"
fi
