"""age encrypt/decrypt. Offline, and with no `age` binary installed.

The binary is stubbed rather than invoked. That is requirement 8, but it is also
the only honest option: `age` is absent on the owner's Windows machine and
present on the runner, so a test that shelled out for real would pass in one
place and fail in the other for reasons having nothing to do with the code.

Two assertions here are security assertions, not behaviour assertions, and they
matter because this repo is PUBLIC (constraint 9):
  - the private key never appears in argv (`/proc/<pid>/cmdline` is world
    readable, and a process listing on a shared runner is not hypothetical)
  - no key value survives into an error message, which is where `age`'s own
    stderr would otherwise be echoed verbatim
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.memory import crypto

KEY = "AGE-SECRET-KEY-1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQZZZZZZ"
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqzzzzzz"


class FakeAge:
    """Stands in for the binary. Records argv and writes whatever `-o` names."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, cmd, capture_output=True, text=True):
        self.calls.append(list(cmd))
        if self.returncode == 0:
            out = Path(cmd[cmd.index("-o") + 1])
            out.write_bytes(b"ciphertext-or-plaintext")
        return subprocess.CompletedProcess(cmd, self.returncode, "", self.stderr)

    @property
    def argv(self) -> str:
        return " ".join(self.calls[-1])


@pytest.fixture
def plain(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    path.write_bytes(b"SQLite format 3\x00 pretend database")
    return path


def test_encrypt_invokes_age_with_the_recipient(plain: Path, tmp_path: Path) -> None:
    runner = FakeAge()
    dest = crypto.encrypt_file(plain, tmp_path / "state.db.age", RECIPIENT, runner=runner)
    assert dest.exists()
    assert runner.calls[0][:3] == ["age", "-r", RECIPIENT]


def test_decrypt_never_puts_the_identity_in_argv(tmp_path: Path) -> None:
    cipher = tmp_path / "state.db.age"
    cipher.write_bytes(b"ciphertext")
    runner = FakeAge()
    crypto.decrypt_file(cipher, tmp_path / "state.db", KEY, runner=runner)
    assert KEY not in runner.argv
    assert "-i" in runner.calls[0]


def test_the_identity_file_is_deleted_afterwards(tmp_path: Path) -> None:
    cipher = tmp_path / "state.db.age"
    cipher.write_bytes(b"ciphertext")
    runner = FakeAge()
    crypto.decrypt_file(cipher, tmp_path / "state.db", KEY, runner=runner)
    key_path = Path(runner.calls[0][runner.calls[0].index("-i") + 1])
    assert not key_path.exists()


def test_failed_decrypt_raises_and_leaves_no_partial_plaintext(tmp_path: Path) -> None:
    """Constraint 14 depends on this. A truncated plaintext left behind would be
    found by open_db on the next run; a ZERO-byte one is a valid empty SQLite
    database, which is the silent-reset path this project must never take."""
    cipher = tmp_path / "state.db.age"
    cipher.write_bytes(b"ciphertext")
    dest = tmp_path / "state.db"
    runner = FakeAge(returncode=2, stderr="age: error: no identity matched")

    with pytest.raises(crypto.AgeError):
        crypto.decrypt_file(cipher, dest, KEY, runner=runner)

    assert not dest.exists()
    assert not (tmp_path / "state.db.part").exists()


def test_error_message_does_not_echo_the_key(tmp_path: Path) -> None:
    cipher = tmp_path / "state.db.age"
    cipher.write_bytes(b"ciphertext")
    runner = FakeAge(returncode=2, stderr=f"age: failed to parse {KEY}")
    with pytest.raises(crypto.AgeError) as excinfo:
        crypto.decrypt_file(cipher, tmp_path / "state.db", KEY, runner=runner)
    assert KEY not in str(excinfo.value)
    assert "REDACTED" in str(excinfo.value)


def test_missing_binary_is_an_age_error_not_a_traceback(tmp_path: Path, plain: Path) -> None:
    def absent(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'age'")

    with pytest.raises(crypto.AgeError, match="not installed"):
        crypto.encrypt_file(plain, tmp_path / "out.age", RECIPIENT, runner=absent)


def test_missing_input_is_an_age_error(tmp_path: Path) -> None:
    with pytest.raises(crypto.AgeError):
        crypto.encrypt_file(tmp_path / "nope.db", tmp_path / "o.age", RECIPIENT, runner=FakeAge())
    with pytest.raises(crypto.AgeError):
        crypto.decrypt_file(tmp_path / "nope.age", tmp_path / "o.db", KEY, runner=FakeAge())


def test_age_available_is_injectable() -> None:
    assert crypto.age_available(which=lambda _: "/usr/bin/age") is True
    assert crypto.age_available(which=lambda _: None) is False


def test_env_helpers_require_the_variable() -> None:
    with pytest.raises(crypto.AgeError, match=crypto.IDENTITY_ENV):
        crypto.identity_from_env({})
    with pytest.raises(crypto.AgeError, match=crypto.RECIPIENT_ENV):
        crypto.recipient_from_env({"AGE_PUBLIC_KEY": "   "})
    assert crypto.identity_from_env({crypto.IDENTITY_ENV: KEY}) == KEY


def test_reading_the_key_from_env_registers_it_for_redaction(caplog) -> None:
    from agent.util.logging import get_logger

    crypto.identity_from_env({crypto.IDENTITY_ENV: KEY})
    logger = get_logger("agent.memory.crypto.test")
    with caplog.at_level("INFO"):
        logger.info("provider said: %s", KEY)
    assert KEY not in caplog.text


def test_round_trip_through_the_stub(plain: Path, tmp_path: Path) -> None:
    """Shape check: encrypt then decrypt, both through the stub, and assert the
    output path is where the caller asked for it rather than a `.part` sibling."""
    runner = FakeAge()
    cipher = crypto.encrypt_file(plain, tmp_path / "s.age", RECIPIENT, runner=runner)
    restored = crypto.decrypt_file(cipher, tmp_path / "restored.db", KEY, runner=runner)
    assert restored.name == "restored.db"
    assert not any(p.name.endswith(".part") for p in tmp_path.iterdir())
