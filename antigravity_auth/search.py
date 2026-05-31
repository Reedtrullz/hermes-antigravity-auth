"""Google Search tool via Antigravity API with URL context analysis."""
from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

try:
  from .constants import (
    ANTIGRAVITY_ENDPOINT_PROD,
    ANTIGRAVITY_VERSION_FALLBACK,
    get_antigravity_headers,
    get_platform,
  )
except ImportError:
  from constants import (
    ANTIGRAVITY_ENDPOINT_PROD,
    ANTIGRAVITY_VERSION_FALLBACK,
    get_antigravity_headers,
    get_platform,
  )

SEARCH_MODEL = "gemini-3.5-flash-low"

SEARCH_TIMEOUT_MS = 60000

SEARCH_SYSTEM_INSTRUCTION = (
  "You are an expert web search assistant with access to Google Search and URL analysis tools.\n"
  "\n"
  "Your capabilities:\n"
  "- Use google_search to find real-time information from the web\n"
  "- Use url_context to fetch and analyze content from specific URLs when provided\n"
  "\n"
  "Guidelines:\n"
  "- Always provide accurate, well-sourced information\n"
  "- Cite your sources when presenting facts\n"
  "- If analyzing URLs, extract the most relevant information\n"
  "- Be concise but comprehensive in your responses\n"
  "- If information is uncertain or conflicting, acknowledge it\n"
  "- Focus on answering the user's question directly"
)

@dataclass
class GroundingChunk:
  web: dict[str, str] | None = None


@dataclass
class GroundingSupport:
  segment: dict[str, Any] | None = None
  groundingChunkIndices: list[int] | None = None


@dataclass
class GroundingMetadata:
  webSearchQueries: list[str] | None = None
  groundingChunks: list[GroundingChunk] | None = None
  groundingSupports: list[GroundingSupport] | None = None
  searchEntryPoint: dict[str, str] | None = None


@dataclass
class UrlMetadata:
  retrieved_url: str = ""
  url_retrieval_status: str = ""


@dataclass
class UrlContextMetadata:
  url_metadata: list[UrlMetadata] | None = None


@dataclass
class SearchArgs:
  query: str
  urls: list[str] | None = None
  thinking: bool = True


@dataclass
class SearchResult:
  text: str = ""
  sources: list[dict[str, str]] = field(default_factory=list)
  searchQueries: list[str] = field(default_factory=list)
  urlsRetrieved: list[dict[str, str]] = field(default_factory=list)


_session_counter = 0
_session_prefix = f"search-{int(time.time() * 1000):x}"


def generate_request_id() -> str:
  timestamp = f"{int(time.time() * 1000):x}"
  rand_part = secrets.token_hex(3)
  return f"search-{timestamp}-{rand_part}"


def get_session_id() -> str:
  global _session_counter
  _session_counter += 1
  return f"{_session_prefix}-{_session_counter}"


def filter_search_urls(urls: Any) -> list[str]:
  """Return non-empty string URLs, ignoring malformed list members."""
  if isinstance(urls, str):
    candidates = [urls]
  elif isinstance(urls, list):
    candidates = urls
  else:
    return []

  filtered: list[str] = []
  for url in candidates:
    if not isinstance(url, str):
      continue
    stripped = url.strip()
    if stripped:
      filtered.append(stripped)
  return filtered


def format_search_result(result: SearchResult) -> str:
  lines: list[str] = []

  lines.append("## Search Results\n")
  lines.append(result.text)
  lines.append("")

  if result.sources:
    lines.append("### Sources")
    for source in result.sources:
      lines.append(f"- [{source['title']}]({source['url']})")
    lines.append("")

  if result.urlsRetrieved:
    lines.append("### URLs Retrieved")
    for url_item in result.urlsRetrieved:
      status = "✓" if url_item["status"] == "URL_RETRIEVAL_STATUS_SUCCESS" else "✗"
      lines.append(f"- {status} {url_item['url']}")
    lines.append("")

  if result.searchQueries:
    lines.append("### Search Queries Used")
    for q in result.searchQueries:
      lines.append(f'- "{q}"')

  return "\n".join(lines)


def parse_search_response(data: dict[str, Any]) -> SearchResult:
  result = SearchResult()

  if not isinstance(data, dict):
    return result

  def _error_message(error_data: Any) -> str:
    if isinstance(error_data, dict):
      return str(error_data.get("message", "Unknown error"))
    return "Unknown error"

  response = data.get("response")
  if not isinstance(response, dict):
    if data.get("error"):
      result.text = f"Error: {_error_message(data.get('error'))}"
    return result

  candidates = response.get("candidates")
  if not isinstance(candidates, list) or not candidates:
    if data.get("error"):
      result.text = f"Error: {_error_message(data.get('error'))}"
    elif response.get("error"):
      result.text = f"Error: {_error_message(response.get('error'))}"
    return result

  candidate = candidates[0]
  if not isinstance(candidate, dict):
    return result

  content = candidate.get("content", {})
  if isinstance(content, dict):
    parts = content.get("parts", [])
    if isinstance(parts, list):
      texts: list[str] = []
      for part in parts:
        if not isinstance(part, dict):
          continue
        text = part.get("text")
        if isinstance(text, str) and text:
          texts.append(text)
      result.text = "\n".join(texts)

  grounding_meta = candidate.get("groundingMetadata")
  if isinstance(grounding_meta, dict):
    queries = grounding_meta.get("webSearchQueries")
    if isinstance(queries, list):
      filtered_queries = [q for q in queries if isinstance(q, str) and q]
      if filtered_queries:
        result.searchQueries = filtered_queries

    chunks = grounding_meta.get("groundingChunks", [])
    if isinstance(chunks, list):
      for chunk in chunks:
        if not isinstance(chunk, dict):
          continue
        web = chunk.get("web", {})
        if not isinstance(web, dict):
          continue
        uri = web.get("uri")
        title = web.get("title")
        if isinstance(uri, str) and isinstance(title, str) and uri and title:
          result.sources.append({"title": title, "url": uri})

  url_ctx = candidate.get("urlContextMetadata", {})
  if isinstance(url_ctx, dict):
    url_metadata = url_ctx.get("url_metadata") or url_ctx.get("urlMetadata") or []
    if isinstance(url_metadata, list):
      for meta in url_metadata:
        if not isinstance(meta, dict):
          continue
        retrieved_url = meta.get("retrieved_url") or meta.get("retrievedUrl")
        if retrieved_url:
          status = (
            meta.get("url_retrieval_status")
            or meta.get("urlRetrievalStatus")
            or "UNKNOWN"
          )
          result.urlsRetrieved.append({
            "url": str(retrieved_url),
            "status": str(status),
          })

  return result


def execute_search(
  args: SearchArgs,
  access_token: str,
  project_id: str,
  timeout_ms: int = 60000,
) -> str:
  query = args.query
  urls = filter_search_urls(args.urls)

  prompt = query
  if urls:
    url_list = "\n".join(urls)
    prompt = f"{query}\n\nURLs to analyze:\n{url_list}"

  tools: list[dict[str, Any]] = []
  tools.append({"googleSearch": {}})
  if urls:
    tools.append({"urlContext": {}})

  request_payload = {
    "systemInstruction": {
      "parts": [{"text": SEARCH_SYSTEM_INSTRUCTION}],
    },
    "contents": [
      {
        "role": "user",
        "parts": [{"text": prompt}],
      },
    ],
    "tools": tools,
    "generationConfig": {
      "temperature": 0,
      "topP": 1,
    },
  }

  wrapped_body = {
    "project": project_id,
    "model": SEARCH_MODEL,
    "userAgent": "antigravity",
    "requestId": generate_request_id(),
    "request": {
      **request_payload,
      "sessionId": get_session_id(),
    },
  }

  url = f"{ANTIGRAVITY_ENDPOINT_PROD}/v1internal:generateContent"

  headers = get_antigravity_headers()
  headers["Authorization"] = f"Bearer {access_token}"
  headers["Content-Type"] = "application/json"

  try:
    data = json.dumps(wrapped_body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    timeout_s = timeout_ms / 1000
    with urllib.request.urlopen(req, timeout=timeout_s) as response:
      resp_bytes = response.read()

    resp_data: dict[str, Any] = json.loads(resp_bytes.decode("utf-8", errors="ignore"))
    result = parse_search_response(resp_data)
    return format_search_result(result)
  except urllib.error.HTTPError as e:
    try:
      error_text = e.read().decode("utf-8", errors="ignore")
    except Exception:
      error_text = str(e)
    return (
      f"## Search Error\n\n"
      f"Failed to execute search: {e.code} {e.reason}\n\n"
      f"{error_text}\n\n"
      f"Please try again with a different query."
    )
  except Exception as e:
    return (
      f"## Search Error\n\n"
      f"Failed to execute search: {e}.\n\n"
      f"Please try again with a different query."
    )
