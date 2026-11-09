#!/bin/sh
# Remove what install.sh added. Corpora and configuration in your project folders are kept.
set -e
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -f "$HOME/.local/bin/openalex-corpus"
rm -rf "$DATA/openalex-corpus"
echo "Removed openalex-corpus"
