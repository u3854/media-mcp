# media-mcp

An MCP runtime that enables local LLMs (like `llama-server`) to trigger and render inline WebP animations, emotes, and reaction media directly in chat.

---

## 1. Create an Action Pack

An Action Pack is a standalone folder with a `pack.json` manifest and a `media/` directory containing `.webp` files.

Scaffold a new pack:
```bash
.venv/bin/python cli.py create ~/.media-packs/reactions --name reactions
```

Drop your `.webp` images into `~/.media-packs/reactions/media/`, customize your actions in `pack.json`, and validate:
```bash
.venv/bin/python cli.py validate ~/.media-packs/reactions
```

### Minimal `pack.json` Example
```json
{
  "name": "reactions",
  "version": "1.0.0",
  "actions": [
    {
      "name": "happy_cat",
      "description": "Displays a cute happy cat animation",
      "media": "cat.webp",
      "template": "A cute cat appears!\n\n{media}"
    }
  ]
}
```

---

## 2. Configure & Start with `llama-server`

Point your `mcp.json` to `tools.py` with `--pack` or `--packs-dir`:

```json
{
  "mcpServers": {
    "media-tools": {
      "command": "/absolute/path/to/media-mcp/.venv/bin/python",
      "args": [
        "/absolute/path/to/media-mcp/tools.py",
        "--packs-dir", "/Users/yourname/.media-packs"
      ]
    }
  }
}
```

Launch `llama-server`:
```bash
llama-server -m /path/to/model.gguf --mcp-config /absolute/path/to/mcp.json
```

> **Note:** The HTTP media server starts automatically on a background daemon thread (default port `8088`, with auto-fallback to `8089..8099`) and terminates cleanly when `llama-server` exits. No separate server terminal needed.
