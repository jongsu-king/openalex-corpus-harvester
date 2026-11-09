#!/usr/bin/env bash
# Package dist/openalex-corpus as <name>-<version>-<os>-<arch>.tar.gz (with install.sh)
# and, on Linux, a .deb.   packaging/package_unix.sh <version>
set -euo pipefail
VERSION="$1"
OS=$(uname -s | tr '[:upper:]' '[:lower:]'); [ "$OS" = darwin ] && OS=macos
ARCH=$(uname -m); [ "$ARCH" = arm64 ] || [ "$ARCH" = aarch64 ] && ARCH=arm64 || ARCH=x86_64
mkdir -p out
STAGE=$(mktemp -d)/openalex-corpus
cp -R dist/openalex-corpus "$STAGE"
cp packaging/linux/install.sh packaging/linux/uninstall.sh "$STAGE/"
chmod +x "$STAGE/install.sh" "$STAGE/uninstall.sh"
cp README.md LICENSE "$STAGE/"
tar -C "$(dirname "$STAGE")" -czf "out/openalex-corpus-${VERSION}-${OS}-${ARCH}.tar.gz" openalex-corpus
if [ "$OS" = linux ]; then
  PKG=$(mktemp -d)/openalex-corpus_${VERSION}_amd64
  mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/doc/openalex-corpus"
  cp -R dist/openalex-corpus "$PKG/opt/openalex-corpus"
  ln -s /opt/openalex-corpus/openalex-corpus "$PKG/usr/bin/openalex-corpus"
  cp LICENSE "$PKG/usr/share/doc/openalex-corpus/copyright"
  cat > "$PKG/DEBIAN/control" <<CONTROL
Package: openalex-corpus
Version: ${VERSION}
Section: science
Priority: optional
Architecture: amd64
Installed-Size: $(du -sk "$PKG" | cut -f1)
Depends: libc6 (>= 2.35)
Recommends: tesseract-ocr, ghostscript, default-jre-headless
Maintainer: Jongsu Lim <313024960+jongsu-king@users.noreply.github.com>
Homepage: https://github.com/jongsu-king/openalex-corpus-harvester
Description: Auditable OpenAlex metadata and scientific PDF corpus harvester
 Collects OpenAlex records and open-access PDFs, keeps raw responses, files and
 checksums, and extracts text into Markdown, structured data and SQLite.
CONTROL
  dpkg-deb --build --root-owner-group "$PKG" "out/openalex-corpus_${VERSION}_amd64.deb"
fi
ls -la out
