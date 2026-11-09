"""Adjust the generic first-start section of the release notes to this program:
there is no Windows build, and the macOS build is a command, not an .app."""
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
lines = [line for line in text.split("\n") if not line.startswith("- **Windows** - SmartScreen")]
text = "\n".join(lines)
text = text.replace(
    "- **macOS** - *cannot be opened because Apple cannot check it*: open **System Settings → "
    "Privacy & Security** and click **Open Anyway** (or run `xattr -dr com.apple.quarantine "
    "\"/Applications/<App>.app\"`).",
    "- **macOS** - the unpacked command is quarantined: run `xattr -dr com.apple.quarantine "
    "openalex-corpus` in the unpacked folder once (or allow it under **System Settings → Privacy & Security**).")
text = text.replace(
    "- **Linux** - no prompt; the `.deb` adds a menu entry, `install.sh` adds one for the current user.",
    "- **Linux** - no prompt; the `.deb` puts `openalex-corpus` in `/usr/bin`, `install.sh` in `~/.local/bin`.\n"
    "- **Windows** - not supported natively; use the Linux build inside WSL2.")
open(path, "w", encoding="utf-8").write(text)
