"""Settings come from .env next to this file (see .env.example). No extra packages needed."""
import os
from pathlib import Path

HERE = Path(__file__).parent
DEFAULTS = {
    "PROVIDER": "gemini",
    "GEMINI_MODEL": "gemini-3.1-flash-lite",
    "CLAUDE_MODEL": "claude-haiku-5-5",
}


def load_env() -> None:
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.split(" #")[0]  # allow trailing comments
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k, v in DEFAULTS.items():
        os.environ.setdefault(k, v)
