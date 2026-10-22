# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: el motor Python empaquetado para ir dentro de la app de escritorio.
#   .venv/bin/pyinstaller motor.spec --noconfirm --distpath app/build
# Salida: app/build/motor/ (onedir: arranca rápido, sin descomprimir en cada inicio).
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = (
    collect_submodules("uvicorn") + collect_submodules("passlib.handlers")
    + collect_submodules("api.routes") + collect_submodules("core")
    + ["pydantic_settings", "jwt", "multipart", "signxml", "pdf417gen", "brotli", "zstandard", "pypdf"]
)
datas = [
    ("static", "static"),
    ("core/xsd", "core/xsd"),
] + collect_data_files("signxml") + collect_data_files("reportlab", includes=["fonts/*"])

a = Analysis(
    ["motor_embebido.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide2", "IPython", "pytest", "wails"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="motor",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="motor")
