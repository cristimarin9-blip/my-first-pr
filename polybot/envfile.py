from __future__ import annotations

import os
from pathlib import Path


def set_env_var(env_path: str | Path, key: str, value: str) -> None:
    """Idempotently set KEY=value in a .env file, preserving other lines."""
    path = Path(env_path)
    lines = path.read_text().splitlines() if path.exists() else []
    out: list[str] = []
    found = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")
    try:
        os.chmod(path, 0o600)  # secrets may live here
    except OSError:
        pass
