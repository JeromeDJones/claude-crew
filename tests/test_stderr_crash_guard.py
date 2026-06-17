"""Structural guard: test_forced_subprocess_crash_populates_stderr_tail must exist.

AT 18: a green-suite (non-gated) deletion-detector that asserts the forced-crash
live test function exists in tests/test_live_stderr.py.  Exits non-zero if that
function is removed, so regressions to the gated live suite are caught by the
fast stub suite on every CI run.
"""

from pathlib import Path


def test_forced_crash_function_exists_in_live_stderr() -> None:
    """AT 18: test_forced_subprocess_crash_populates_stderr_tail must be present.

    Checks the source text of tests/test_live_stderr.py so the guard survives
    import errors, conditional imports, and module-level side effects.  The check
    is intentionally a plain string search — if the identifier exists anywhere in
    the file (as a def, as a reference, as a class method) the guard passes; a
    wholesale removal of the function name fails the guard.
    """
    test_file = Path(__file__).parent / "test_live_stderr.py"
    assert test_file.exists(), (
        f"Expected {test_file} to exist — the live stderr test file was removed."
    )

    content = test_file.read_text(encoding="utf-8")
    target = "test_forced_subprocess_crash_populates_stderr_tail"
    assert target in content, (
        f"Expected the function {target!r} to be present in "
        f"tests/test_live_stderr.py, but it was not found.\n"
        f"This guard (AT 18) is a deletion-detector: remove or rename the "
        f"forced-crash live test and this green-suite guard catches it on the "
        f"next stub-suite run, before the gated live suite is needed."
    )
