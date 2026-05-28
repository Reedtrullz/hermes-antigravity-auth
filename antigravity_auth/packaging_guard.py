"""Release-safety checks for package builds."""
from __future__ import annotations

from pathlib import Path

_LOCAL_CREDENTIALS_RELATIVE = Path("antigravity_auth") / "_credentials.py"


def assert_no_local_credentials_module(root: str | Path | None = None) -> None:
  """Raise if the gitignored local credentials module would be packaged."""
  base = Path(root) if root is not None else Path(__file__).resolve().parents[1]
  credentials_path = base / _LOCAL_CREDENTIALS_RELATIVE
  if credentials_path.exists():
    raise RuntimeError(
      "Refusing to build with local antigravity_auth/_credentials.py present. "
      "Move credentials to environment variables or ~/.hermes/antigravity-credentials.json "
      "before building a wheel/sdist."
    )
