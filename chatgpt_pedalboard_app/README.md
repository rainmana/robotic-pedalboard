# ChatGPT Pedalboard App

This is a ChatGPT Apps SDK MCP server that gives ChatGPT an agentic Pedalboard music studio
inside the regular ChatGPT conversation.
It can inspect supported effects, render synthesized compositions through a real `pedalboard.Pedalboard`
chain, process local audio files, and serve WAV outputs that ChatGPT can link back in chat.

The app is tool-first. ChatGPT does not need a human-facing UI to make music with it. An optional
widget is included only for cases where the user explicitly asks to open a studio surface.

## App Shape

- Archetype: tool-first ChatGPT app, with an optional widget resource.
- MCP endpoint: `http://localhost:8000/mcp`
- Output route: `http://localhost:8000/outputs/<file>.wav`
- Optional widget resource: `ui://widget/pedalboard-studio-v1.html`
- Optional browser preview: `http://localhost:8000/widget`
- Optional standalone preview render route: `POST http://localhost:8000/api/render`

## Tools

- `inspect_pedalboard_effects`: lists built-in effect names, defaults, and parameter ranges.
- `open_pedalboard_studio`: opens the optional ChatGPT widget only when requested.
- `render_pedalboard_composition`: creates a WAV from JSON track/effect specs.
- `process_audio_with_pedalboard`: applies an effect chain to a local file path reachable by the server.

## Run Locally

```bash
cd chatgpt_pedalboard_app
uv run python -m robotic_pedalboard_app.app
```

If port `8000` is busy:

```bash
PORT=8017 PUBLIC_BASE_URL=http://localhost:8017 uv run python -m robotic_pedalboard_app.app
```

Check the server:

```bash
curl http://127.0.0.1:8000/health
```

## Connect In ChatGPT Developer Mode

1. Start the server locally.
2. Expose it with an HTTPS tunnel, for example `ngrok http 8000`.
3. Set `PUBLIC_BASE_URL` to the public tunnel origin before starting the app, so rendered WAV URLs are public.
4. In ChatGPT, enable Developer Mode under Settings -> Apps & Connectors -> Advanced settings.
5. Create a new app and use the tunneled MCP URL, ending in `/mcp`.
6. Refresh the app after changing tool descriptions, metadata, or widget HTML.

## Validation

Run the local smoke checks:

```bash
uv run python -m py_compile robotic_pedalboard_app/app.py robotic_pedalboard_app/engine.py
uv run python - <<'PY'
from pathlib import Path
from robotic_pedalboard_app.engine import CompositionSpec, render_composition

result = render_composition(
    CompositionSpec(title="Smoke test", prompt="robotic chorus delay", duration_seconds=1),
    Path("outputs"),
    "http://localhost:8000",
)
print(result["file"]["file_name"])
PY
```

With the server running, this MCP client checks the `/mcp` loop:

```bash
uv run python - <<'PY'
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with streamable_http_client("http://127.0.0.1:8000/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([tool.name for tool in tools.tools])
            result = await session.call_tool("inspect_pedalboard_effects", {})
            print(result.structuredContent["effect_count"])

asyncio.run(main())
PY
```

## Notes

- The app does not execute arbitrary Python from ChatGPT. It exposes a structured composition and effect-chain API.
- VST3 and Audio Unit loading can be added as a follow-up with a path allowlist and plugin scan cache.
- For production, host the MCP server behind stable HTTPS and set a fixed `PUBLIC_BASE_URL`.
