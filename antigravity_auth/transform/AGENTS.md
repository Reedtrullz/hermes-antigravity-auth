# transform/ — Request/Response Transformation Pipeline

Converts OpenAI-format requests to Gemini API format and back, with Antigravity envelope wrapping and thinking block handling.

## Structure

```
transform/
├── messages.py    # OpenAI → Gemini contents[].parts[] conversion
├── thinking.py    # Claude thinking block stripping + signature handling
├── schema.py      # JSON schema allowlist sanitization (const, $ref removal)
├── envelope.py    # Antigravity request envelope + header building
├── response.py    # SSE streaming response → chat-completions format
├── test_*.py      # Colocated tests per module
└── __init__.py    # Explicit exports of all public API symbols
```

## Key Patterns

- **Pipeline architecture**: Messages → Schema → Envelope → Response — each stage is independent
- **Two header styles**: `HeaderStyle.ANTIGRAVITY` (Electron UA+fingerprint) vs `HeaderStyle.GEMINI_CLI` (nodejs-client UA)
- **Schema sanitization**: Allowlist-based — removes `const`, `$ref`, `$defs`, `title`, `$schema`; merges `allOf`/`anyOf`/`oneOf`
- **Thinking block stripping**: Both Claude anthropic thinking blocks AND Gemini thought signatures — stripped from outgoing requests
- **SSE parsing**: `transform_antigravity_response()` converts Antigravity SSE stream to OpenAI chat-completions format

## Anti-Patterns

- **No inline schema mutation**: Use `clean_json_schema()`/`to_gemini_schema()` wrappers, never modify schemas inline
- **No header duplication**: Header style must be resolved once (`resolve_model_for_header_style`) and used consistently
- **No decode assumptions**: Always `decode("utf-8", errors="ignore")` on HTTP response bytes — Antigravity may return non-UTF-8 error bodies

## Testing

```bash
python3 -m pytest antigravity_auth/transform/ -v
```

Mock `urllib.request.urlopen` for SSE stream tests. Schema tests use inline dict fixtures.