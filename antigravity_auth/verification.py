from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

try:
    from .constants import ANTIGRAVITY_ENDPOINT_PROD, get_antigravity_headers
    from .token import refresh_access_token
    from .storage import sync_token_to_auth_json
except ImportError:
    from constants import ANTIGRAVITY_ENDPOINT_PROD, get_antigravity_headers
    from token import refresh_access_token
    from storage import sync_token_to_auth_json


@dataclass
class VerificationProbeResult:
    status: str
    message: str
    verify_url: str | None = None


def decode_escaped_text(input_str: str) -> str:
    result = input_str.replace("&amp;", "&")
    result = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda m: chr(int(m.group(1), 16)),
        result,
    )
    return result


def _collect_urls_from_text(text: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(
        r"https://accounts\.google\.com/[^\s\"'<>]+",
        text,
        re.IGNORECASE,
    ):
        url = match.group(0)
        if url not in urls:
            urls.append(url)
    return urls


def _normalize_google_verification_url(raw_url: str) -> str | None:
    normalized = decode_escaped_text(raw_url).strip()
    if not normalized:
        return None
    try:
        parsed = urlparse(normalized)
        if parsed.hostname != "accounts.google.com":
            return None
        return normalized
    except Exception:
        return None


def _select_best_verification_url(urls: list[str]) -> str | None:
    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        normalized = _normalize_google_verification_url(url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)

    if not unique:
        return None

    def score(value: str) -> int:
        total = 0
        if "plt=" in value:
            total += 4
        if "/signin/continue" in value:
            total += 3
        if "continue=" in value:
            total += 2
        if "service=cloudcode" in value:
            total += 1
        return total

    unique.sort(key=score, reverse=True)
    return unique[0]


def extract_verification_error_details(body_text: str) -> dict:
    decoded_body = decode_escaped_text(body_text)
    lower_body = decoded_body.lower()
    validation_required = "validation_required" in lower_body
    message: str | None = None
    verification_urls: list[str] = []

    verification_urls.extend(_collect_urls_from_text(decoded_body))

    payloads: list = []
    trimmed = decoded_body.strip()
    if trimmed.startswith("{") or trimmed.startswith("["):
        try:
            payloads.append(json.loads(trimmed))
        except json.JSONDecodeError:
            pass

    for raw_line in decoded_body.split("\n"):
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload_text = line[5:].strip()
        if not payload_text or payload_text == "[DONE]":
            continue
        try:
            payloads.append(json.loads(payload_text))
        except json.JSONDecodeError:
            verification_urls.extend(_collect_urls_from_text(payload_text))

    visited: set[int] = set()

    def walk(value: object, key: str | None = None) -> None:
        nonlocal validation_required, message

        if isinstance(value, str):
            normalized_value = decode_escaped_text(value)
            lower_value = normalized_value.lower()
            lower_key = (key or "").lower()

            if "validation_required" in lower_value:
                validation_required = True

            if (
                not message
                and (
                    "message" in lower_key
                    or "detail" in lower_key
                    or "description" in lower_key
                )
            ):
                message = normalized_value

            if (
                "validation_url" in lower_key
                or "verify_url" in lower_key
                or "verification_url" in lower_key
                or lower_key == "url"
            ):
                verification_urls.append(normalized_value)

            verification_urls.extend(_collect_urls_from_text(normalized_value))
            return

        if value is None or not isinstance(value, (dict, list)):
            return

        value_id = id(value)
        if value_id in visited:
            return
        visited.add(value_id)

        if isinstance(value, list):
            for item in value:
                walk(item)
            return

        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, child_key)

    for payload in payloads:
        walk(payload)

    if not validation_required:
        validation_required = (
            "verification required" in lower_body
            or "verify your account" in lower_body
            or "account verification" in lower_body
        )

    if not message:
        for raw_line in decoded_body.split("\n"):
            line = raw_line.strip()
            if line and not line.startswith("data:") and re.search(r"(verify|validation|required)", line, re.IGNORECASE):
                message = line
                break

    return {
        "validationRequired": validation_required,
        "message": message,
        "verifyUrl": _select_best_verification_url(verification_urls),
    }


def verify_account_access(
    account: dict,
    access_token: str,
    project_id: str | None = None,
) -> VerificationProbeResult:
    if not access_token:
        return VerificationProbeResult(
            status="error",
            message="Missing access token",
        )

    headers: dict[str, str] = {
        **get_antigravity_headers(),
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    if project_id:
        headers["x-goog-user-project"] = project_id

    request_body = {
        "model": "gemini-3-flash",
        "request": {
            "model": "gemini-3-flash",
            "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
            "generationConfig": {"maxOutputTokens": 1, "temperature": 0},
        },
    }

    data = json.dumps(request_body).encode("utf-8")
    url = f"{ANTIGRAVITY_ENDPOINT_PROD}/v1internal:streamGenerateContent?alt=sse"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return VerificationProbeResult(
                status="ok",
                message="Account verification check passed.",
            )
    except urllib.error.HTTPError as e:
        try:
            response_body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            response_body = ""

        extracted = extract_verification_error_details(response_body)
        if e.code == 403 and extracted.get("validationRequired"):
            return VerificationProbeResult(
                status="blocked",
                message=extracted.get("message") or "Google requires additional account verification.",
                verify_url=extracted.get("verifyUrl"),
            )

        fallback_message = extracted.get("message") or f"Request failed ({e.code} {e.reason})."
        return VerificationProbeResult(
            status="error",
            message=fallback_message,
        )
    except urllib.error.URLError as e:
        error_str = str(e)
        if "timed out" in error_str.lower():
            return VerificationProbeResult(
                status="error",
                message="Verification check timed out.",
            )
        return VerificationProbeResult(
            status="error",
            message=f"Verification check failed: {error_str}",
        )
    except Exception as e:
        return VerificationProbeResult(
            status="error",
            message=f"Verification check failed: {e}",
        )


def probe_account_health(account: dict) -> VerificationProbeResult:
    refresh_token = account.get("refresh_token") or account.get("refreshToken")
    if not refresh_token:
        return VerificationProbeResult(
            status="error",
            message="Missing refresh token for account.",
        )

    try:
        auth: dict = {"refresh": refresh_token}
        if account.get("email"):
            auth["email"] = account["email"]
        refreshed = refresh_access_token(auth)
    except Exception as e:
        return VerificationProbeResult(
            status="error",
            message=f"Token refresh failed: {e}",
        )

    access_token = refreshed.get("access") if isinstance(refreshed, dict) else None
    if not access_token:
        return VerificationProbeResult(
            status="error",
            message="Could not refresh access token for this account.",
        )

    # Persist the refreshed token to auth.json
    project_id_for_sync = (
        account.get("managedProjectId")
        or account.get("projectId")
        or account.get("project_id")
    ) or ""
    email_for_sync = account.get("email")
    sync_token_to_auth_json(
        access_token=access_token,
        refresh_token=refresh_token,
        project_id=project_id_for_sync,
        email=email_for_sync,
        set_active=False
    )

    project_id = (
        account.get("managedProjectId")
        or account.get("projectId")
        or account.get("project_id")
    ) or None

    return verify_account_access(
        account=account,
        access_token=access_token,
        project_id=project_id,
    )
