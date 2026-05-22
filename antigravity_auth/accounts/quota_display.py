"""Color-coded CLI progress bars for account quota visualization."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"
GRAY = "\033[90m"


@dataclass
class QuotaDisplayInfo:
  email: str | None
  index: int
  quota_groups: dict[str, dict[str, Any]] = field(default_factory=dict)
  is_enabled: bool = True
  status_text: str = ""


def get_quota_status_line(quota_group: dict[str, Any] | None) -> str:
  if quota_group is None:
    return "N/A"

  remaining_fraction = quota_group.get("remainingFraction")
  used = quota_group.get("used")
  limit = quota_group.get("limit")

  if remaining_fraction is not None:
    percent = remaining_fraction * 100
    if remaining_fraction <= 0:
      return f"{RED}\u26a0 EXHAUSTED{RESET}"
    if remaining_fraction < 0.2:
      return f"{YELLOW}\u26a0 LOW ({percent:.0f}%){RESET}"
    return f"{GREEN}\u2713 OK ({percent:.0f}%){RESET}"

  if used is not None and limit is not None:
    return f"{used}/{limit}"

  return "N/A"


def format_quota_progress_bar(remaining_fraction: float, width: int = 20) -> str:
  filled = max(0, min(width, int(remaining_fraction * width)))
  empty = width - filled

  if remaining_fraction > 0.5:
    color = GREEN
  elif remaining_fraction > 0.2:
    color = YELLOW
  else:
    color = RED

  full_block = "\u2588"
  empty_block = "\u2591"
  bar = f"{color}{full_block * filled}{RESET}{GRAY}{empty_block * empty}{RESET}"
  percent = f"{remaining_fraction * 100:.0f}%"
  return f"[{bar}] {percent}"


def format_single_account_quota(account: QuotaDisplayInfo) -> str:
  label = account.email or f"Account {account.index + 1}"
  status = f"{GREEN}\u2713{RESET}" if account.is_enabled else f"{RED}\u2717{RESET}"
  lines: list[str] = []

  if account.status_text:
    lines.append(f"  Account {account.index + 1}: {label} [{status}] {account.status_text}")
  else:
    lines.append(f"  Account {account.index + 1}: {label} [{status}]")

  for group_name, group_data in account.quota_groups.items():
    status_line = get_quota_status_line(group_data)
    remaining = group_data.get("remainingFraction", 0)
    bar = format_quota_progress_bar(remaining)
    display_name = group_name.replace("_", " ").title()
    lines.append(f"    {display_name}: {status_line} {bar}")

  return "\n".join(lines)


def format_quota_display(accounts: list[QuotaDisplayInfo]) -> str:
  lines: list[str] = []
  lines.append(f"{BOLD}Quota Status{RESET}")
  lines.append("")

  enabled_count = sum(1 for a in accounts if a.is_enabled)
  rate_limited = sum(1 for a in accounts if not a.is_enabled)

  for account in accounts:
    lines.append(format_single_account_quota(account))
    lines.append("")

  lines.append(f"{len(accounts)} account(s), {enabled_count} enabled, {rate_limited} rate-limited")
  return "\n".join(lines)


def print_quota_display(accounts: list[QuotaDisplayInfo]) -> None:
  print(format_quota_display(accounts))


def format_account_list(accounts: list[Any]) -> str:
  lines: list[str] = []
  for i, acc in enumerate(accounts):
    label = getattr(acc, "email", None) or f"Account {i + 1}"
    is_enabled = getattr(acc, "is_enabled", True) if hasattr(acc, "is_enabled") else getattr(acc, "enabled", True)
    symbol = f"{GREEN}\u2713{RESET}" if is_enabled else f"{RED}\u2717{RESET}"
    lines.append(f"  {i + 1}. {label} {symbol}")
  return "\n".join(lines)
