"""Run the test suite without pytest installed (pytest also works: `pytest tests`).

Stands in for the few pytest features the tests use: raises, mark.parametrize, and the
tmp_path and capsys fixtures.
"""
import contextlib, importlib, inspect, io, re, sys, tempfile, time, traceback, types
from pathlib import Path


def _shim() -> types.ModuleType:
    shim = types.ModuleType("pytest")

    @contextlib.contextmanager
    def raises(exc, match=None):
        try:
            yield
        except exc as caught:
            if match is not None and not re.search(match, str(caught)):
                raise AssertionError(f"{exc.__name__} raised, but {match!r} is not in: {caught}")
            return
        raise AssertionError(f"{exc.__name__} not raised")

    def parametrize(names, cases):
        names = [n.strip() for n in names.split(",")]

        def mark(fn):
            fn._cases = [dict(zip(names, case if len(names) > 1 else (case,))) for case in cases]
            return fn
        return mark

    shim.raises = raises
    shim.mark = types.SimpleNamespace(parametrize=parametrize)
    return shim


class _Capsys:
    """Captures what the test prints; readouterr() returns it and starts again."""
    def __init__(self):
        self._out, self._err = io.StringIO(), io.StringIO()
        self._real = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = self._out, self._err

    def readouterr(self):
        got = types.SimpleNamespace(out=self._out.getvalue(), err=self._err.getvalue())
        for stream in (self._out, self._err):
            stream.seek(0)
            stream.truncate()
        return got

    def close(self):
        sys.stdout, sys.stderr = self._real


def _run(fn, case):
    wants = inspect.signature(fn).parameters
    capsys = _Capsys() if "capsys" in wants else None
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            kwargs = dict(case)
            if "tmp_path" in wants:
                kwargs["tmp_path"] = Path(tmp)
            if capsys is not None:
                kwargs["capsys"] = capsys
            fn(**kwargs)
    finally:
        if capsys is not None:
            capsys.close()


def main() -> int:
    import logging
    logging.disable(logging.CRITICAL)      # the tests break things on purpose; keep the report readable
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "tests"))
    try:
        import pytest  # noqa: F401
    except ImportError:
        sys.modules["pytest"] = _shim()
    import conftest  # noqa: F401

    passed = failed = 0
    started = time.time()
    for path in sorted((root / "tests").glob("test_*.py")):
        module = importlib.import_module(path.stem)
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_") or fn.__module__ != module.__name__:
                continue
            for case in getattr(fn, "_cases", [{}]):
                label = f"{path.stem}.{name}" + (f" {list(case.values())!r}" if case else "")
                try:
                    _run(fn, case)
                    passed += 1
                except Exception:
                    failed += 1
                    print(f"FAILED {label}")
                    traceback.print_exc()
    print(f"{passed} passed, {failed} failed in {time.time() - started:.1f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
