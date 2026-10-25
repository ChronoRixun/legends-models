# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec of legends-models.exe: one folder, console, no UPX, a version resource and an asInvoker,
long-path-aware manifest (the same shape as the port builder's xml1-builder.spec). Run it through
tools/freeze.py, which passes:

    LM_FREEZE_GEN      a folder holding build-info.json (bundled as legends_models/data/build-info.json)
    LM_FREEZE_SAMPLER  sample_enbaya.exe to bundle as legends_models/bin/ (engines.sampler_path looks there), '' = none

dist/legends-models/ = legends-models.exe + _internal/:
    legends_models/data/            acceptance.json, community.json, community-credits.md, build-info.json
    legends_models/engines/vendor/  the vendored converters (run as scripts by the exe itself: __main__.python_shim)
    legends_models/engines/drivers/ the tool's own driver scripts
NumPy, SciPy (spatial, sparse, optimize) and Pillow are bundled for those scripts; PyInstaller cannot see their
imports (the scripts are data), so they are listed here. onnxruntime (optional AI smoothing) is not bundled."""
import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

REPO = Path(SPECPATH).resolve().parent          # noqa: F821 - set by PyInstaller (tools/)
sys.path.insert(0, str(REPO))
from legends_models import __version__ as BASE_VERSION  # noqa: E402

VERSION = os.environ.get('LM_FREEZE_VERSION') or BASE_VERSION   # the full release version (0.3.0-beta.1)

GEN = Path(os.environ['LM_FREEZE_GEN'])
SAMPLER = os.environ.get('LM_FREEZE_SAMPLER', '')
BUILD_INFO = GEN / 'build-info.json'
if not BUILD_INFO.is_file():
    raise SystemExit(f'{BUILD_INFO} is missing: run tools/freeze.py')

PKG = REPO / 'legends_models'
datas = []
for p in sorted(PKG.rglob('*')):
    if not p.is_file() or '__pycache__' in p.parts or p.suffix == '.pyc':
        continue
    rel = p.relative_to(REPO)
    if rel.parts[1] == 'engines' and rel.parts[2] in ('vendor', 'drivers') or rel.parts[1] == 'data':
        if p.suffix in ('.py', '.json', '.md', '.cpp', '.cmd'):
            datas.append((str(p), rel.parent.as_posix()))
datas.append((str(BUILD_INFO), 'legends_models/data'))
binaries = [(SAMPLER, 'legends_models/bin')] if SAMPLER else []

hiddenimports = sorted(set(collect_submodules('legends_models')
                           + ['numpy', 'scipy.spatial', 'scipy.sparse', 'scipy.sparse.linalg', 'scipy.optimize',
                              'scipy.spatial.transform', 'PIL.Image', 'PIL.ImageFilter', 'PIL.ImageOps',
                              'PIL.ImageDraw', 'PIL.PngImagePlugin', 'xml.etree.ElementTree', 'html.parser',
                              'urllib.parse', 'urllib.request', 'fnmatch', 'zlib', 'ctypes', 'ctypes.wintypes',
                              'dataclasses', 'runpy']))
# The freezing Python may have more installed (torch, pygame, pandas, ...): scipy's optional array-API backends and
# test helpers would drag them in. None of them is used by the converters.
excludes = ['tkinter', '_tkinter', 'matplotlib', 'IPython', 'pytest', 'onnxruntime', 'numpy.f2py', 'numpy.tests',
            'scipy.tests', 'PIL.ImageQt', 'PIL.ImageTk', 'torch', 'torchvision', 'torchaudio', 'tensorflow', 'jax',
            'jaxlib', 'cupy', 'dask', 'sparse', 'pandas', 'pyarrow', 'sympy', 'numba', 'llvmlite', 'pygame', 'cv2',
            'sklearn', 'notebook', 'jupyter', 'ipykernel', 'h5py', 'tables', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
            'wx', 'gi', 'zmq', 'tornado', 'lxml', 'cryptography', 'psutil', 'pydantic', 'transformers', 'onnx',
            'array_api_strict', 'ndonnx', 'mkl', 'setuptools', 'pkg_resources', 'Cython', 'cffi']


def _v4(v):
    nums = [int(x) for x in re.findall(r'\d+', v)[:4]]
    return tuple(nums + [0] * (4 - len(nums)))


VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_v4(VERSION), prodvers=_v4(VERSION), mask=0x3F, flags=0x0, OS=0x40004,
                      fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'Ultimate Legends community'),
        StringStruct('FileDescription', 'legends-models: optional character models for Legends Classic'),
        StringStruct('FileVersion', VERSION),
        StringStruct('InternalName', 'legends-models'),
        StringStruct('LegalCopyright', 'MIT License. Contains no game data and no community skins.'),
        StringStruct('OriginalFilename', 'legends-models.exe'),
        StringStruct('ProductName', 'legends-models'),
        StringStruct('ProductVersion', VERSION),
        StringStruct('Comments', 'Builds character-model mods on your PC from games you own.')])]),
          VarFileInfo([VarStruct('Translation', [0x0409, 1200])])])

MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
</assembly>
"""

a = Analysis(  # noqa: F821
    [str(PKG / '__main__.py')],
    pathex=[str(REPO)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='legends-models',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=VERSION_INFO,
    manifest=MANIFEST,
    uac_admin=False,
    contents_directory='_internal',
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='legends-models',
)
