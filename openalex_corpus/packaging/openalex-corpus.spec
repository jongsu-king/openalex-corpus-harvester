# Standalone command-line build (one folder). Run from openalex_corpus/ after
# `pip install .`:   pyinstaller --noconfirm packaging/openalex-corpus.spec
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

datas = []
for dist in ("openalex-corpus", "httpx", "pydantic", "pymupdf", "pdfplumber", "lxml", "pandas", "pyarrow"):
    try:
        datas += copy_metadata(dist)  # `doctor` reports these versions
    except Exception:
        pass

a = Analysis(
    ["entry.py"],
    hiddenimports=collect_submodules("openalex_corpus"),
    datas=datas,
    excludes=["tkinter", "PySide6", "PyQt5", "PyQt6", "IPython", "pytest", "docling", "torch"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="openalex-corpus", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="openalex-corpus")
