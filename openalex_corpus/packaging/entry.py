"""Entry point of the standalone (PyInstaller) build: `openalex-corpus ...`."""

from openalex_corpus.cli import app

if __name__ == "__main__":
    app(prog_name="openalex-corpus")
