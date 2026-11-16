# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "tools" / "epubcheck"), "tools/epubcheck"),
    (str(ROOT / "tools" / "jre"), "tools/jre"),
    (str(ROOT / "resources" / "icons"), "resources/icons"),
]

a = Analysis(
    ["app.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["lxml.etree", "lxml._elementpath"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pydoc", "doctest",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
        "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtOpenGL",
        "numpy", "matplotlib", "pandas", "scipy",
    ],
    noarchive=False,
    optimize=1,
)

jre_root = str((ROOT / "tools" / "jre").resolve()).lower()


def keep_binary(entry):
    destination = str(entry[0]).replace("\\", "/").lower()
    source = str(entry[1]).replace("\\", "/").lower()
    if destination in {"icuuc.dll", "icudt78.dll", "icudt74.dll"}:
        return False
    if source.startswith(jre_root + "/") and not destination.startswith("tools/jre/"):
        return False
    return True


a.binaries = [entry for entry in a.binaries if keep_binary(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DiscourseEpubArchive",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "resources" / "icons" / "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DiscourseEpubArchive",
)
