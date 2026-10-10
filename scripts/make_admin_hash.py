"""Print a bcrypt hash for ADMIN_PASSWORD_HASH.

Run locally and type the password at the prompt. The password is not echoed,
stored, or passed as an argument, so it stays out of shell history.

    python scripts/make_admin_hash.py

"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import hash_password  # noqa: E402


MIN_LENGTH = 12


def main() -> int:
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Repeat password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    if len(password) < MIN_LENGTH:
        print(f"Password must be at least {MIN_LENGTH} characters.", file=sys.stderr)
        return 1
    print(f"ADMIN_PASSWORD_HASH={hash_password(password)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
