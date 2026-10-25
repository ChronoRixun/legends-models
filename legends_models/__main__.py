# SPDX-License-Identifier: MIT
"""python -m legends_models, and the entry point of the frozen legends-models.exe.

The converters run as separate Python processes (engines.run: `<python> -B <script> args`, engines.engine_modules:
`<python> -c <code>`). In the frozen exe there is no separate interpreter, so the exe itself takes that role:
when its first argument is an interpreter flag or a .py path it runs the script with the bundled NumPy / SciPy /
Pillow (python_shim) instead of the CLI. Tool commands never start that way.
"""
import os
import runpy
import sys

PY_FLAGS = ('-B', '-u', '-E', '-s', '-I')


def python_shim(argv):
    """Run `[-B] script.py args` or `[-B] -c code args` like python would; None when argv is a tool command."""
    a = list(argv)
    while a and a[0] in PY_FLAGS:
        a.pop(0)
    if not a or not (a[0] == '-c' or a[0].lower().endswith('.py')):
        return None
    try:
        if a[0] == '-c':
            sys.argv = ['-c', *a[2:]]
            exec(compile(a[1], '<string>', 'exec'), {'__name__': '__main__', '__builtins__': __builtins__})
        else:
            path = os.path.abspath(a[0])
            sys.argv = [path, *a[1:]]
            sys.path.insert(0, os.path.dirname(path))
            runpy.run_path(path, run_name='__main__')
    except SystemExit as e:
        if e.code is None:
            return 0
        if isinstance(e.code, int):
            return e.code
        print(e.code, file=sys.stderr)
        return 1
    return 0


def _main():
    if getattr(sys, 'frozen', False):
        code = python_shim(sys.argv[1:])
        if code is not None:
            sys.stdout.flush()
            sys.exit(code)
    from legends_models.cli import main
    sys.exit(main())


if __name__ == '__main__':
    _main()
