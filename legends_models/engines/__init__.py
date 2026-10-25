# SPDX-License-Identifier: MIT
"""Run the vendored research converters.

Each engine (vendor/<engine>) is the research workspace's tools folder, copied verbatim except for
hard-coded research-PC paths (vendor/PATCHES.json) and the layered-cloth / loose-geometry rules
(vendor/_shared/layers.py, wired into the MUA1/MUA2 fitters and validators). The research scripts locate their workspace as
`Path(__file__).parents[1]` and use cwd-relative paths, so each engine is materialised into
<work>/<engine>/tools and run there in its own Python process: identical behaviour, no module-name
clashes between the four copies of model.py / validate.py, and nothing written outside <work>.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import __version__
from ..errors import MISSING_INPUT, ToolError

VENDOR = Path(__file__).resolve().parent / 'vendor'
# Modules shared by several engines (layered-cloth / loose-geometry rules): copied into every
# engine folder that runs a fitter or validator (community: next to its round-2 validator too).
SHARED = VENDOR / '_shared'
SHARED_DIRS = {'mua1': ['.'], 'mua2': ['.'], 'xml2': ['.'], 'community': ['.', 'round2']}
DRIVERS = Path(__file__).resolve().parent / 'drivers'
SAMPLER = 'sample_enbaya.exe'
SAMPLER_SHA256 = None  # built locally from tools/sample_enbaya.cpp; RVAs are tied to the libIGSg.dll hash checked by export_animations.py
# Where the sampler must sit (export_animations.py looks next to itself).
SAMPLER_DIRS = {'mua1': ['.'], 'mua2': ['.'], 'xml2': ['.'], 'community': ['.', 'round2']}


def python_exe():
    """Interpreter for the engines (needs NumPy, SciPy, Pillow): LEGENDS_MODELS_PYTHON, else this one."""
    return os.environ.get('LEGENDS_MODELS_PYTHON') or sys.executable


def engine_modules():
    code = 'import importlib.util as u,json;print(json.dumps({m:u.find_spec(m) is not None for m in ("numpy","scipy","PIL")}))'
    try:
        r = subprocess.run([python_exe(), '-c', code], capture_output=True, text=True, timeout=60)
        import json
        return json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'numpy': False, 'scipy': False, 'PIL': False}


def sampler_path(work=None):
    for c in filter(None, [os.environ.get('LEGENDS_MODELS_SAMPLER'),
                           work and Path(work) / 'bin' / SAMPLER,
                           Path(__file__).resolve().parents[1] / 'bin' / SAMPLER]):
        if Path(c).is_file():
            return Path(c)
    return None


def _tree_digest(folder):
    h = hashlib.sha256(__version__.encode())
    for base in (folder, SHARED):
        for p in sorted(base.rglob('*.py')):
            h.update(p.relative_to(base).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def materialise(engine, work, needs_sampler=True):
    """Copy vendor/<engine> to <work>/<engine>/tools (refreshed when the package changes)."""
    src = VENDOR / engine
    root = Path(work) / engine
    tools = root / 'tools'
    digest = _tree_digest(src)
    stamp = tools / '.legends-models-engine'
    if not (stamp.is_file() and stamp.read_text() == digest):
        if tools.exists():
            shutil.rmtree(tools)
        shutil.copytree(src, tools, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for d in SHARED_DIRS.get(engine, ['.']):
            for p in SHARED.glob('*.py'):
                shutil.copyfile(p, tools / d / p.name)
        stamp.write_text(digest)
    if not needs_sampler:
        return root
    sampler = sampler_path(work)
    if sampler is None:
        raise ToolError('offline animation sampler sample_enbaya.exe not found: build it with tools\\build_sampler.cmd '
                        'and put it in <work>\\bin or set LEGENDS_MODELS_SAMPLER', MISSING_INPUT)
    for d in SAMPLER_DIRS[engine]:
        dest = tools / d / SAMPLER
        if not dest.is_file() or dest.stat().st_size != sampler.stat().st_size:
            shutil.copyfile(sampler, dest)
    return root


def run(engine, root, script, args, env=None, log=None, report=None, driver=False, timeout=None):
    """python -B <tools|drivers>/<script> args, cwd=<work>/<engine>. Output goes to `log`."""
    path = (DRIVERS if driver else Path(root) / 'tools') / script
    cmd = [python_exe(), '-B', str(path), *map(str, args)]
    full_env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', **(env or {}))
    if driver:
        full_env['LEGENDS_ENGINE_TOOLS'] = str(Path(root) / 'tools')
    log = Path(log) if log else Path(root) / 'last-run.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    if report:
        report.emit('engine', engine=engine, script=script, log=str(log))
    with log.open('a', encoding='utf-8') as out:
        out.write('\n$ ' + ' '.join(cmd) + '\n')
        out.flush()
        proc = subprocess.Popen(cmd, cwd=root, env=full_env, stdout=out, stderr=subprocess.STDOUT)
        _bind_to_job(proc)
        try:
            return proc.wait(timeout=timeout)
        finally:
            if proc.poll() is None:
                proc.kill()


_JOB = None


def _bind_to_job(proc):
    """Windows: put the converter in a kill-on-close job, so cancelling the CLI (even by killing it)
    never leaves an orphaned fitter running."""
    global _JOB
    if os.name != 'nt':
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        if _JOB is None:
            job = k32.CreateJobObjectW(None, None)

            class BASIC(ctypes.Structure):
                _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                            ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                            ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                            ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                            ('SchedulingClass', wintypes.DWORD)]

            class IO(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ('r', 'w', 'o', 'rb', 'wb', 'ob')]

            class EXTENDED(ctypes.Structure):
                _fields_ = [('BasicLimitInformation', BASIC), ('IoInfo', IO), ('ProcessMemoryLimit', ctypes.c_size_t),
                            ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                            ('PeakJobMemoryUsed', ctypes.c_size_t)]
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
                return
            _JOB = job
        handle = k32.OpenProcess(0x0100 | 0x0001, False, proc.pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if handle:
            k32.AssignProcessToJobObject(wintypes.HANDLE(_JOB), wintypes.HANDLE(handle))
            k32.CloseHandle(wintypes.HANDLE(handle))
    except (OSError, AttributeError):
        pass


def tail(log, lines=12):
    try:
        return '\n'.join(Path(log).read_text(encoding='utf-8', errors='replace').splitlines()[-lines:])
    except OSError:
        return ''
