"""Install Hermes plugin wrappers for hermes-antigravity-auth."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse

from .package_info import GIT_PACKAGE_SPEC, __version__
from .storage import get_hermes_home


CLI_INIT = '''"""Google Antigravity CLI file-plugin wrapper."""

import sys
from pathlib import Path

try:
  from antigravity_auth.plugin_contract import load_cli_register
except Exception as exc:
  raise RuntimeError(
    "Hermes Antigravity file-plugin wrapper failed to load.\\n"
    f"Wrapper: {Path(__file__).expanduser()}\\n"
    f"Python: {Path(sys.executable).expanduser()}\\n"
    "Target: antigravity_auth.plugin_contract\\n"
    f"Cause: {type(exc).__name__}: {exc}\\n\\n"
    "Fix: run `hermes-antigravity-install` from the hermes-antigravity-auth "
    "checkout so the package is installed into Hermes' Python."
  ) from exc

register = load_cli_register(__file__)
'''

CLI_YAML = f"""name: antigravity-cli
kind: standalone
version: {__version__}
description: Google Antigravity CLI utilities - login, quotas, account management
author: NoeFabris & Reedtrullz
"""

PROVIDER_INIT = '''"""Google Antigravity provider file-plugin wrapper."""

import sys
from pathlib import Path

try:
  from antigravity_auth.plugin_contract import load_provider_namespace
except Exception as exc:
  raise RuntimeError(
    "Hermes Antigravity file-plugin wrapper failed to load.\\n"
    f"Wrapper: {Path(__file__).expanduser()}\\n"
    f"Python: {Path(sys.executable).expanduser()}\\n"
    "Target: antigravity_auth.plugin_contract\\n"
    f"Cause: {type(exc).__name__}: {exc}\\n\\n"
    "Fix: run `hermes-antigravity-install` from the hermes-antigravity-auth "
    "checkout so the package is installed into Hermes' Python."
  ) from exc

globals().update(load_provider_namespace(__file__))
'''

PROVIDER_YAML = f"""name: antigravity
kind: model-provider
version: {__version__}
description: Google Antigravity OAuth via Hermes Cloud Code transport
author: NoeFabris & Reedtrullz
"""

DEPRECATED_CLI_TOOLSETS = ("messaging", "moa")


def _write_file(path: Path, content: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_suffix(f".tmp.{os.getpid()}")
  tmp.write_text(content, encoding="utf-8")
  tmp.replace(path)
  os.chmod(path, 0o600)


def _existing_python(path: Path) -> Path | None:
  if path.exists() and path.name.startswith("python"):
    return path
  return None


def _python_from_venv_script(script: Path) -> Path | None:
  try:
    first_line = script.read_text(encoding="utf-8").splitlines()[0]
  except (OSError, IndexError, UnicodeDecodeError):
    return None
  if first_line.startswith("#!"):
    candidate = Path(first_line[2:].strip().split()[0])
    found = _existing_python(candidate)
    if found is not None:
      return found
  for name in ("python3", "python"):
    found = _existing_python(script.parent / name)
    if found is not None:
      return found
  return None


def _python_from_launcher_script(launcher: Path) -> Path | None:
  try:
    text = launcher.read_text(encoding="utf-8")
  except (OSError, UnicodeDecodeError):
    return None

  python = _python_from_venv_script(launcher)
  if python is not None:
    return python

  match = re.search(r'exec\s+"([^"]+/venv/bin/hermes)"', text)
  if match is not None:
    return _python_from_venv_script(Path(match.group(1)))
  return None


def _python_from_version_output(output: str) -> Path | None:
  match = re.search(r"^Project:\s+(.+)$", output, re.MULTILINE)
  if match is None:
    return None
  project_dir = Path(match.group(1).strip())
  for name in ("python3", "python"):
    found = _existing_python(project_dir / "venv" / "bin" / name)
    if found is not None:
      return found
  return None


def _find_hermes_launcher(explicit: str | None = None) -> Path:
  if explicit:
    launcher = Path(explicit).expanduser()
  else:
    configured = os.environ.get("HERMES_BIN")
    found = configured or shutil.which("hermes")
    if not found:
      raise RuntimeError(
        "Could not find the hermes launcher. Set HERMES_BIN=/path/to/hermes "
        "and rerun hermes-antigravity-install."
      )
    launcher = Path(found).expanduser()
  if not launcher.exists():
    raise RuntimeError(f"Hermes launcher does not exist: {launcher}")
  return launcher


def resolve_hermes_python(hermes_bin: str | None = None) -> Path:
  """Resolve the Python interpreter used by the hermes launcher."""
  launcher = _find_hermes_launcher(hermes_bin)
  python = _python_from_launcher_script(launcher)
  if python is not None:
    return python

  result = subprocess.run(
    [str(launcher), "--version"],
    capture_output=True,
    text=True,
    timeout=15,
    check=False,
  )
  python = _python_from_version_output((result.stdout or "") + "\n" + (result.stderr or ""))
  if python is not None:
    return python

  raise RuntimeError(
    "Could not resolve the Python interpreter used by hermes. "
    "Set HERMES_BIN to the hermes launcher that starts Hermes Agent, or run "
    "the installer from a Hermes installation with a venv-backed launcher."
  )


def _current_package_spec() -> str:
  root = Path(__file__).resolve().parents[1]
  if (root / "pyproject.toml").exists() and (root / "antigravity_auth").exists():
    return f"-e {root}[yaml]"

  try:
    direct_url = metadata.distribution("hermes-antigravity-auth").read_text("direct_url.json")
  except metadata.PackageNotFoundError:
    direct_url = None
  if direct_url:
    try:
      data = json.loads(direct_url)
    except json.JSONDecodeError:
      data = {}
    url = data.get("url", "")
    if url.startswith("file://"):
      source = Path(unquote(urlparse(url).path))
      if source.exists():
        return f"-e {source}[yaml]"
    vcs_info = data.get("vcs_info") or {}
    if vcs_info.get("vcs") == "git" and url:
      commit = vcs_info.get("commit_id")
      suffix = f"@{commit}" if commit else ""
      return f"hermes-antigravity-auth[yaml] @ git+{url}{suffix}"

  return GIT_PACKAGE_SPEC


def _pip_install_args(package_spec: str) -> list[str]:
  if package_spec.startswith("-e "):
    return ["-e", package_spec[3:]]
  return [package_spec]


def install_package_in_hermes_python(hermes_python: Path, package_spec: str | None = None) -> bool:
  target = hermes_python.resolve()
  current = Path(sys.executable).resolve()
  if target == current:
    return False

  spec = package_spec or _current_package_spec()
  command = [
    str(target),
    "-m",
    "pip",
    "install",
    "--upgrade",
    *_pip_install_args(spec),
  ]
  subprocess.run(command, check=True)
  return True


def _config_backup_path(config_path: Path) -> Path:
  stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
  candidate = config_path.with_name(f"{config_path.name}.bak.antigravity-toolsets.{stamp}")
  if not candidate.exists():
    return candidate
  for i in range(1, 1000):
    numbered = config_path.with_name(f"{candidate.name}.{i}")
    if not numbered.exists():
      return numbered
  raise RuntimeError(f"Could not allocate backup path for {config_path}")


def cleanup_deprecated_cli_toolsets(home: Path | None = None) -> list[str]:
  """Remove deprecated Hermes CLI toolset names from platform_toolsets.cli."""
  hermes_home = home or get_hermes_home()
  config_path = hermes_home / "config.yaml"
  if not config_path.exists():
    return []
  try:
    import yaml  # type: ignore
  except Exception:
    return []
  try:
    text = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
  except Exception:
    return []
  if not isinstance(data, dict):
    return []
  platform_toolsets = data.get("platform_toolsets")
  if not isinstance(platform_toolsets, dict):
    return []
  cli_toolsets = platform_toolsets.get("cli")
  if not isinstance(cli_toolsets, list):
    return []

  deprecated = {name.lower() for name in DEPRECATED_CLI_TOOLSETS}
  kept = []
  removed = []
  for item in cli_toolsets:
    name = str(item).strip().lower() if isinstance(item, str) else ""
    if name in deprecated:
      removed.append(str(item))
    else:
      kept.append(item)
  if not removed:
    return []

  platform_toolsets["cli"] = kept
  backup = _config_backup_path(config_path)
  shutil.copy2(config_path, backup)
  dumped = yaml.safe_dump(data, sort_keys=False)
  tmp_path = config_path.with_name(f"{config_path.name}.tmp.antigravity-toolsets")
  tmp_path.write_text(dumped, encoding="utf-8")
  try:
    tmp_path.chmod(config_path.stat().st_mode & 0o777)
  except OSError:
    pass
  tmp_path.replace(config_path)
  return removed


def install_plugins(home: Path | None = None) -> list[Path]:
  hermes_home = home or get_hermes_home()
  cli_dir = hermes_home / "plugins" / "antigravity-cli"
  provider_dir = hermes_home / "plugins" / "model-providers" / "antigravity"

  _write_file(cli_dir / "__init__.py", CLI_INIT)
  _write_file(cli_dir / "plugin.yaml", CLI_YAML)
  _write_file(provider_dir / "__init__.py", PROVIDER_INIT)
  _write_file(provider_dir / "plugin.yaml", PROVIDER_YAML)
  cleanup_deprecated_cli_toolsets(hermes_home)

  return [cli_dir, provider_dir]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Install Hermes Antigravity plugin wrappers.")
  parser.add_argument("--home", help="Hermes home directory. Defaults to HERMES_HOME or ~/.hermes.")
  parser.add_argument("--hermes-bin", help="Hermes launcher path. Defaults to HERMES_BIN or hermes on PATH.")
  parser.add_argument("--package-spec", help="Package spec to install into Hermes' Python before writing wrappers.")
  parser.add_argument(
    "--skip-python-install",
    action="store_true",
    help="Only write wrapper files; do not install the package into Hermes' Python.",
  )
  return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
  args = _parse_args(argv)
  if not args.skip_python_install:
    hermes_python = resolve_hermes_python(args.hermes_bin)
    installed = install_package_in_hermes_python(hermes_python, args.package_spec)
    action = "Verified" if not installed else "Installed"
    print(f"{action} package in Hermes Python: {hermes_python}")

  paths = install_plugins(Path(args.home).expanduser() if args.home else None)
  for path in paths:
    print(f"Installed {path}")


if __name__ == "__main__":
  main()
