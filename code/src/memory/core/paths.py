"""Product-wide filesystem paths for the global Agent data home."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional


def default_agent_data_dir(environ: Optional[Mapping[str, str]] = None) -> str:
    """Return the global Agent data home, with explicit runtime/test overrides.

    The default is deliberately machine-global, like Codex's user home, so
    sessions and memory are not recreated below every project checkout.
    """
    env = environ if environ is not None else os.environ
    explicit = env.get("AGENT_DATA_DIR", "").strip()
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    agent_home = env.get("AGENT_HOME", "").strip()
    if agent_home:
        return str(Path(agent_home).expanduser().resolve())
    return str((Path.home() / ".agent_data").resolve())
