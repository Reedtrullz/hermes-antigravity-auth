"""Central secret redaction helpers for diagnostics and logs."""
from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_KEY_FRAGMENTS = (
  "access_token",
  "accesstoken",
  "refresh_token",
  "refreshtoken",
  "id_token",
  "idtoken",
  "authorization",
  "client_secret",
  "clientsecret",
  "code_verifier",
  "codeverifier",
  "oauth_code",
  "oauthcode",
  "session_token",
  "sessiontoken",
  "api_key",
  "apikey",
  "cookie",
  "set_cookie",
  "setcookie",
)

_EXACT_SECRET_KEYS = {
  "access",
  "refresh",
  "token",
  "secret",
  "code",
  "cookie",
  "set_cookie",
  "setcookie",
}

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_QUERY_SECRET_RE = re.compile(
  r"(?i)([?&](?:access_token|accessToken|refresh_token|refreshToken|id_token|idToken|client_secret|clientSecret|code|code_verifier|codeVerifier|session_token|sessionToken|api_key|apiKey|apikey|x-api-key|x-goog-api-key|key)=)[^&#\s]+"
)
_JSON_SECRET_RE = re.compile(
  r'(?i)("(?:access_token|refresh_token|id_token|accessToken|refreshToken|idToken|client_secret|clientSecret|code_verifier|codeVerifier|session_token|sessionToken|oauth_code|oauthCode|authorization|refresh|access|code|api_key|apiKey|apikey|x-api-key|x-goog-api-key|cookie|set-cookie|set_cookie|setCookie)"\s*:\s*")[^"]*(")'
)
_PYTHON_REPR_SECRET_RE = re.compile(
  r"(?i)('(?:access_token|refresh_token|id_token|accessToken|refreshToken|idToken|client_secret|clientSecret|code_verifier|codeVerifier|session_token|sessionToken|oauth_code|oauthCode|authorization|refresh|access|code|api_key|apiKey|apikey|x-api-key|x-goog-api-key|cookie|set-cookie|set_cookie|setCookie)'\s*:\s*')[^']*(')"
)
_FORM_SECRET_RE = re.compile(
  r"(?i)\b(access_token|accessToken|refresh_token|refreshToken|id_token|idToken|client_secret|clientSecret|code_verifier|codeVerifier|session_token|sessionToken|authorization|code|api_key|apiKey|apikey|x-api-key|x-goog-api-key|key|cookie|set-cookie|set_cookie|setCookie)=([^&\s]+)"
)
_HEADER_SECRET_RE = re.compile(
  r"(?i)(\b(?:authorization|x-api-key|x-goog-api-key|api-key|cookie|set-cookie)\s*:\s*)[^\r\n]+"
)


def _is_secret_key(key: Any) -> bool:
  normalized = str(key).replace("-", "_").lower()
  compact = normalized.replace("_", "")
  metadata_suffixes = (
    "_cached", "cached", "_expires", "expires", "_expires_at", "expiresat",
    "_last_refresh_at", "lastrefreshat",
  )
  if normalized.endswith(metadata_suffixes) or compact.endswith(metadata_suffixes):
    return False
  if normalized in _EXACT_SECRET_KEYS:
    return True
  return any(fragment in normalized or fragment in compact for fragment in _SECRET_KEY_FRAGMENTS)


def redact_secret_text(text: str) -> str:
  """Redact token-like values from free-form log/error text."""
  if not text:
    return text
  redacted = _BEARER_RE.sub("Bearer " + REDACTED, text)
  redacted = _HEADER_SECRET_RE.sub(lambda m: m.group(1) + REDACTED, redacted)
  redacted = _QUERY_SECRET_RE.sub(lambda m: m.group(1) + REDACTED, redacted)
  redacted = _JSON_SECRET_RE.sub(lambda m: m.group(1) + REDACTED + m.group(2), redacted)
  redacted = _PYTHON_REPR_SECRET_RE.sub(lambda m: m.group(1) + REDACTED + m.group(2), redacted)
  redacted = _FORM_SECRET_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", redacted)
  return redacted


def redact_secrets(obj: Any) -> Any:
  """Return a copy of obj with OAuth tokens, auth headers, and secrets redacted.

  Handles nested dict/list structures and free-form strings. Keys that are known
  to carry secrets are replaced wholesale; other strings are scanned for Bearer
  tokens and common OAuth query/form/JSON values.
  """
  if isinstance(obj, dict):
    result: dict[Any, Any] = {}
    for key, value in obj.items():
      if _is_secret_key(key):
        result[key] = REDACTED if value not in (None, "") else value
      else:
        result[key] = redact_secrets(value)
    return result
  if isinstance(obj, list):
    return [redact_secrets(item) for item in obj]
  if isinstance(obj, tuple):
    return tuple(redact_secrets(item) for item in obj)
  if isinstance(obj, str):
    return redact_secret_text(obj)
  return obj
