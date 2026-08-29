"""Run the pytest suite without pytest. stdlib-only, never imported by the
pipeline.

Why: the agent sandbox has no PyPI access, so `pip install pytest` is
impossible and the real suite can only run on the owner's Windows. This shim
executes the same test files in one process and reproduces the baseline
count exactly, which is what makes a green claim falsifiable -- a number
that does not match the predicted total is investigated, not ignored.

Supports the subset the suite uses: pytest.raises(match=), mark.parametrize
(stacked decorators -> cartesian product), fixture(fn / autouse / scope),
yield-fixtures with teardown, autouse fixtures (function + session),
monkeypatch (setenv/delenv/setattr/setitem with undo), caplog (at_level,
clear, records, text), tmp_path, and the pytest module import itself.

Usage:  PYTHONPATH=src python3 tools/pytest_shim.py [test file or dir ...]
"""

from __future__ import annotations

import contextlib
import functools
import importlib.util
import itertools
import logging
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path
from types import ModuleType, SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
_TESTS = _REPO_ROOT / "tests"


# ---------------------------------------------------------------------------
# The fake pytest module
# ---------------------------------------------------------------------------


class RaisesContext:
    def __init__(self, exc, match=None):
        self.exc = exc
        self.match = match
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            raise AssertionError(f"DID NOT RAISE <class '{self.exc.__name__}'>")
        if not issubclass(et, self.exc):
            return False
        if self.match is not None and not re.search(self.match, str(ev)):
            raise AssertionError(
                f"Pattern '{self.match}' not found in '{ev}'"
            ) from ev
        self.value = ev  # pytest's ExceptionInfo exposes .value
        return True


class _Marker:
    def __init__(self, argnames, argvalues):
        if isinstance(argnames, str):
            argnames = tuple(a.strip() for a in argnames.split(","))
        self.argnames = tuple(argnames)
        self.argvalues = list(argvalues)


def _parametrize(argnames, argvalues, ids=None):
    # `ids` accepted and ignored: pytest uses it for display only.
    def deco(fn):
        markers = getattr(fn, "_shim_parametrize", None)
        if markers is None:
            markers = []
            fn._shim_parametrize = markers
        markers.append(_Marker(argnames, argvalues))
        return fn
    return deco


def _fixture(fn=None, *, autouse=False, scope="function"):
    def deco(f):
        f._shim_fixture = {"autouse": autouse, "scope": scope}
        return f
    if fn is None:
        return deco
    return deco(fn)


def _skip(reason=""):
    raise _Skipped(reason)


class _Skipped(Exception):
    pass


_fake_pytest = SimpleNamespace(
    raises=RaisesContext,
    fixture=_fixture,
    mark=SimpleNamespace(parametrize=_parametrize),
    skip=_skip,
    fail=lambda msg: (_ for _ in ()).throw(AssertionError(msg)),
)


# ---------------------------------------------------------------------------
# Builtin fixtures
# ---------------------------------------------------------------------------


class MonkeyPatch:
    def __init__(self):
        self._undo: list = []

    def setenv(self, name, value):
        old = os.environ.get(name)
        os.environ[name] = value
        self._undo.append(lambda: os.environ.__setitem__(name, old)
                          if old is not None else os.environ.pop(name, None))

    def delenv(self, name, raising=True):
        if name not in os.environ and raising:
            raise KeyError(name)
        old = os.environ.get(name)
        os.environ.pop(name, None)
        self._undo.append(lambda: os.environ.__setitem__(name, old)
                          if old is not None else os.environ.pop(name, None))

    def setattr(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self._undo.append(lambda: setattr(obj, name, old))

    def setitem(self, mapping, key, value):
        existed = True
        try:
            old = mapping[key]
        except KeyError:
            existed = False
            old = None
        mapping[key] = value
        if existed:
            self._undo.append(lambda: mapping.__setitem__(key, old))
        else:
            self._undo.append(lambda: mapping.pop(key, None))

    def undo(self):
        for fn in reversed(self._undo):
            fn()
        self._undo.clear()


class _LevelContext:
    """Returned by Caplog.at_level(): usable as a context manager, so
    `with caplog.at_level(level, logger=...):` restores the logger's level
    when the block exits (pytest semantics)."""

    def __init__(self, caplog, level, logger):
        self.caplog = caplog
        self.level = level
        self.logger = logger
        self._prior = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.caplog._restore_level(self.logger, self._prior)
        return False


class Caplog:
    def __init__(self):
        self.handler = _CaptureHandler()
        self.records: list = []
        self._levels: dict = {}
        self.handler._records = self.records

    def at_level(self, level, logger=None):
        ctx = _LevelContext(self, level, logger)
        if logger is None:
            lg = logging.getLogger()
        else:
            lg = logging.getLogger(logger)
        ctx._prior = lg.level
        lg.setLevel(level)
        self._levels[logger] = ctx._prior
        return ctx

    def set_level(self, level, logger=None):
        self.at_level(level, logger)

    def _restore_level(self, logger, prior):
        lg = logging.getLogger() if logger is None else logging.getLogger(logger)
        lg.setLevel(prior)

    def clear(self):
        self.records.clear()

    # Deliberately NO __enter__/__exit__ here: real pytest's
    # LogCaptureFixture is not a context manager, and a shim that is one
    # masks the exact misuse (with caplog:) that CI then rejects. The
    # supported context-manager form is `with caplog.at_level(...)`, which
    # returns a _LevelContext. Learned 2026-08-29 from a red CI run.

    @property
    def text(self):
        return "\n".join(r.getMessage() for r in self.records)

    def attach(self):
        root = logging.getLogger()
        root.addHandler(self.handler)
        return self

    def detach(self):
        root = logging.getLogger()
        root.removeHandler(self.handler)
        for name, level in self._levels.items():
            self._restore_level(name, level)
        self.records.clear()


class _CaptureHandler(logging.Handler):
    def emit(self, record):
        self._records.append(record)


_BUILTINS = {
    "tmp_path": lambda ctx: Path(tempfile.mkdtemp(prefix="shim-tmp-")),
    "monkeypatch": lambda ctx: ctx.make_monkeypatch(),
    "caplog": lambda ctx: ctx.make_caplog(),
}


# ---------------------------------------------------------------------------
# Fixture registry and resolution
# ---------------------------------------------------------------------------


class _FixtureDef:
    def __init__(self, fn):
        self.fn = fn
        meta = getattr(fn, "_shim_fixture", {})
        self.autouse = meta.get("autouse", False)
        self.scope = meta.get("scope", "function")
        self.name = fn.__name__


class _Context:
    """Per-test context. Session-scoped state lives in a _Session, shared
    across tests; function-scoped state lives here and dies with the test."""

    def __init__(self, module_ns, session):
        self.module_ns = module_ns
        self.session = session
        self.func_cache: dict = {}
        self.monkeypatches: list = []
        self.caplogs: list = []
        self._function_generators: list = []

    def make_monkeypatch(self):
        mp = MonkeyPatch()
        self.monkeypatches.append(mp)
        return mp

    def make_caplog(self):
        caplog = Caplog().attach()
        self.caplogs.append(caplog)
        return caplog

    def add_generator(self, gen, scope):
        if scope == "session":
            self.session.generators.append(gen)
        else:
            self._function_generators.append(gen)


class _Session:
    def __init__(self):
        self.cache: dict = {}
        self.generators: list = []


def _find_fixture(ctx, name, all_defs):
    for fd in all_defs:
        if fd.name == name:
            return fd
    return None


def _resolve(ctx, name, all_defs, chain):
    if name in chain:
        raise RuntimeError(f"fixture cycle: {chain + [name]}")
    if name in ctx.func_cache:
        return ctx.func_cache[name]
    if name in _BUILTINS:
        value = _BUILTINS[name](ctx)
        ctx.func_cache[name] = value
        return value
    fd = _find_fixture(ctx, name, all_defs)
    if fd is None:
        raise RuntimeError(f"unknown fixture '{name}'")
    if fd.scope == "session" and name in ctx.session.cache:
        return ctx.session.cache[name]
    args = [a for a in _sig_params(fd.fn) if a not in ("self",)]
    resolved = {a: _resolve(ctx, a, all_defs, chain + [name]) for a in args}
    result = fd.fn(**resolved)
    if hasattr(result, "__next__"):
        value = next(result)
        ctx.add_generator(result, fd.scope)
    else:
        value = result
    (ctx.session.cache if fd.scope == "session" else ctx.func_cache)[name] = value
    return value


def _sig_params(fn):
    import inspect
    try:
        return list(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return []


# ---------------------------------------------------------------------------
# Collection and execution
# ---------------------------------------------------------------------------


def _load_module(path: Path) -> ModuleType:
    name = path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _collect(paths):
    """Returns list of (test_name, callable, module, conftest_defs)."""
    conftest = _load_module(_TESTS / "conftest.py")
    all_defs = []
    for ns in (conftest.__dict__,):
        for obj in ns.values():
            if callable(obj) and getattr(obj, "_shim_fixture", None) is not None:
                all_defs.append(_FixtureDef(obj))

    items = []
    files = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files.extend(sorted(path.rglob("test_*.py")))
        else:
            files.append(path)
    for f in sorted(set(files)):
        module = _load_module(f)
        mod_defs = list(all_defs)
        for obj in module.__dict__.values():
            if callable(obj) and getattr(obj, "_shim_fixture", None) is not None:
                mod_defs.append(_FixtureDef(obj))
        for name, obj in module.__dict__.items():
            if not name.startswith("test_") or not callable(obj):
                continue
            markers = getattr(obj, "_shim_parametrize", [])
            if markers:
                for combo in itertools.product(*[m.argvalues for m in markers]):
                    bound = {}
                    for marker, values in zip(markers, combo):
                        if len(marker.argnames) == 1:
                            bound[marker.argnames[0]] = values
                        else:
                            for k, v in zip(marker.argnames, values):
                                bound[k] = v
                    items.append((name, obj, bound, module, mod_defs))
            else:
                items.append((name, obj, {}, module, mod_defs))
    return items


def _run_one(ctx, test_fn, bound):
    """Execute one test with autouse fixtures applied. Returns None or an
    exception."""
    try:
        ctx.func_cache = {}
        autouse = [fd for fd in ctx.defs if fd.autouse]
        for fd in autouse:
            _resolve(ctx, fd.name, ctx.defs, [])
        args = {}
        for p in _sig_params(test_fn):
            if p in bound:
                args[p] = bound[p]
            else:
                args[p] = _resolve(ctx, p, ctx.defs, [])
        test_fn(**args)
        return None
    except _Skipped as exc:
        return ("skip", exc)
    except Exception as exc:  # noqa: BLE001 -- the shim IS the runner
        return ("fail", exc)


def _teardown(ctx):
    for gen in reversed(ctx._function_generators):
        with contextlib.suppress(StopIteration):
            next(gen)
    for mp in ctx.monkeypatches:
        mp.undo()
    for caplog in ctx.caplogs:
        caplog.detach()


def _teardown_session(session):
    for gen in reversed(session.generators):
        with contextlib.suppress(StopIteration):
            next(gen)


def main(argv=None):
    paths = [Path(p) for p in (argv or [_TESTS])]
    sys.path.insert(0, str(_SRC))
    sys.path.insert(0, str(_REPO_ROOT))
    sys.modules["pytest"] = _fake_pytest
    items = _collect(paths)

    session = _Session()
    passed = failed = skipped = 0
    failures = []
    for name, fn, bound, module, defs in items:
        ctx = _Context(module.__dict__, session)
        ctx.defs = defs
        try:
            outcome = _run_one(ctx, fn, bound)
        except Exception as exc:  # noqa: BLE001 -- teardown/setup errors
            outcome = ("fail", exc)
        _teardown(ctx)
        if outcome is None:
            passed += 1
            print(".", end="", flush=True)
        elif outcome[0] == "skip":
            skipped += 1
            print("s", end="", flush=True)
        else:
            failed += 1
            print("F", end="", flush=True)
            failures.append((name, bound, outcome[1]))
    _teardown_session(session)

    print()
    for name, bound, exc in failures:
        print(f"\nFAILED: {name} {bound or ''}")
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        print("".join(tb[-6:]))
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
