#!/bin/sh
# Install this standalone build for the current user (no root needed):
#   program -> ~/.local/share/openalex-corpus/     command -> ~/.local/bin/openalex-corpus
# `openalex-corpus self-update` later replaces ~/.local/share/openalex-corpus/.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/openalex-corpus"
mkdir -p "$DATA" "$HOME/.local/bin"
if [ "$HERE" != "$DEST" ]; then
  rm -rf "$DEST.new"; cp -R "$HERE" "$DEST.new"; rm -rf "$DEST"; mv "$DEST.new" "$DEST"
fi
ln -sf "$DEST/openalex-corpus" "$HOME/.local/bin/openalex-corpus"
echo "Installed to $DEST; command: openalex-corpus (make sure ~/.local/bin is on PATH)"
