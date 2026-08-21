"""`age` encrypt/decrypt of the state database file. Nothing else.

No git, no state branch, no force-push, no artifact backup -- those are Phase 7
and building them here means building them before the workflow that calls them
exists. This module takes a path in and puts a path out.

Two security properties it is responsible for, on a PUBLIC repo (constraint 9):

**The identity never touches argv.** `/proc/<pid>/cmdline` is world-readable and
a process listing on a shared runner is not a hypothetical. The private key is
written to a mode-0600 temporary file, passed by path, and unlinked in a
`finally`. `age` offers no stdin route for `-i`, so a temp file is the honest
option rather than the lazy one.

**No key value ever reaches a log line or an exception message.** `age`'s stderr
is echoed back on failure, and stderr is a place a key can appear. Every string
that leaves this module is scrubbed of the known secret first, in addition to
the process-wide redaction filter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from agent.util.logging import PROCESS_FILTER, get_logger

RECIPIENT_ENV = "AGE_PUBLIC_KEY"
IDENTITY_ENV = "AGE_SECRET_KEY"

logger = get_logger("agent.memory.crypto")

Runner = Callable[..., subprocess.CompletedProcess]


class AgeError(Exception):
    """Any failure to encrypt or decrypt. Callers HALT on this -- constraint 14:
    a state file that will not decrypt must never degrade to empty memory."""


def age_available(*, which: Callable[[str], str | None] = shutil.which) -> bool:
    """Whether the `age` binary is on PATH. Injectable so the test suite can
    assert both branches without depending on what happens to be installed --
    the binary is absent on the owner's machine and present on the runner."""
    return which("age") is not None


def _scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "***REDACTED***") if secret else text


def _run(cmd: list[str], runner: Runner, secret: str | None, what: str) -> None:
    try:
        proc = runner(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise AgeError(f"{what} failed: the `age` binary is not installed") from exc
    if proc.returncode != 0:
        stderr = _scrub((proc.stderr or "").strip(), secret)
        raise AgeError(f"{what} failed (exit {proc.returncode}): {stderr}")


def _atomic_output(dest: Path) -> Path:
    """Write to a sibling `.part` file and rename on success.

    Without this, a decrypt that fails halfway leaves a truncated plaintext file
    where the state database belongs. `db.open_db` would find it, fail its
    integrity check, and halt -- correct, but for the wrong reason and one step
    further from the actual cause. Worse, a zero-byte result is a VALID empty
    SQLite database, so on a different code path it could read as fresh state.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest.with_name(dest.name + ".part")


def encrypt_file(
    src: Path, dest: Path, recipient: str, *, runner: Runner = subprocess.run
) -> Path:
    """age-encrypt `src` to `dest` for `recipient` (an age public key).

    The recipient is a PUBLIC key, so it is safe in argv -- unlike the identity
    in `decrypt_file`. It is still passed through the scrubber on error, because
    a misconfiguration that puts a private key in this argument is exactly the
    kind of mistake whose stderr should not be published to a public log.
    """
    src, dest = Path(src), Path(dest)
    if not src.exists():
        raise AgeError(f"nothing to encrypt: {src} does not exist")
    tmp = _atomic_output(dest)
    try:
        _run(["age", "-r", recipient, "-o", str(tmp), str(src)], runner, recipient, "encrypt")
        os.replace(tmp, dest)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    logger.info("encrypted state to %s (%d bytes)", dest.name, dest.stat().st_size)
    return dest


def decrypt_file(
    src: Path, dest: Path, identity: str, *, runner: Runner = subprocess.run
) -> Path:
    """age-decrypt `src` to `dest` using `identity` (an age private key).

    Raises AgeError on any failure. The caller must treat that as fatal and must
    not fall through to creating a new database -- see `db.open_db`, whose
    `create_if_absent` defaults to False for exactly this reason.
    """
    src, dest = Path(src), Path(dest)
    PROCESS_FILTER.register(identity)
    if not src.exists():
        raise AgeError(f"nothing to decrypt: {src} does not exist")
    tmp = _atomic_output(dest)
    key_file: str | None = None
    try:
        fd, key_file = tempfile.mkstemp(prefix="age-id-", suffix=".txt")
        os.chmod(key_file, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(identity if identity.endswith("\n") else identity + "\n")
        _run(
            ["age", "-d", "-i", key_file, "-o", str(tmp), str(src)],
            runner, identity, "decrypt",
        )
        os.replace(tmp, dest)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    finally:
        if key_file:
            Path(key_file).unlink(missing_ok=True)
    logger.info("decrypted state to %s", dest.name)
    return dest


def _from_env(env, name: str) -> str:
    value = (env.get(name) or "").strip()
    if not value:
        raise AgeError(f"{name} is not set")
    PROCESS_FILTER.register(value)
    return value


def recipient_from_env(env) -> str:
    return _from_env(env, RECIPIENT_ENV)


def identity_from_env(env) -> str:
    return _from_env(env, IDENTITY_ENV)
