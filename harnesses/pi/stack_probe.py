"""Run one installed infer-stack env query with filesystem writes blocked."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys

from aivm_probe import deny_writes


def main():
    executable, name = json.loads(sys.stdin.read())
    attempted = []
    # Backend construction ensures existing directories even for env queries.
    # Existing directories need no mutation; suppress just these no-op calls.
    original_mkdir = Path.mkdir
    def existing_mkdir(path, mode=0o777, parents=False, exist_ok=False):
        if exist_ok and path.is_dir():
            return None
        return original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
    Path.mkdir = existing_mkdir
    def guard(event, args):
        try:
            deny_writes(event, args)
        except PermissionError:
            attempted.append(event)
            raise
    sys.addaudithook(guard)
    sys.argv = [executable, 'env', name]
    output = io.StringIO()
    status = 0
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            runpy.run_path(executable, run_name='__main__')
    except SystemExit as exc:
        status = exc.code or 0
    except Exception:
        status = 1
    if attempted:
        # A library may swallow a denied mutation and return fallback values.
        # Do not present such a fallback as successful discovery.
        sys.exit(76)
    if status:
        sys.exit(1)
    print(output.getvalue(), end='')


if __name__ == '__main__':
    main()
