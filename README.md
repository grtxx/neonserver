# neonserver

A lightweight async chat server that connects a Gemini-powered AI assistant to one or more MCP (Model Context Protocol) tool servers. Built with FastAPI and LangChain, it exposes a WebSocket-based API and a minimal web frontend.

## What it does

Neonserver acts as the backend of a company-internal AI assistant ("NEON"). It:

- Accepts user messages over a WebSocket connection and streams the AI's responses back token by token
- Connects to any number of MCP tool servers at startup, discovers their tools, and makes them available to the LLM automatically
- Stores chat sessions (history, settings, persona) in MySQL
- Supports persona-based system prompts with per-user context injection (name, email, job title, etc.)
- Implements a simple session and user memory mechanism via `$$ SESSION | ID | Value $$` and `$$ USER | ID | Value $$` markers embedded in the AI's responses

## Architecture

```
Browser (WebSocket)
       │
       ▼
 chatserver.py  (FastAPI app, WebSocket endpoint, REST API)
       │
       ▼
   aichat.py   (AIChat: LLM orchestration, tool call loop, streaming)
       │
       ├── chatsessiondata.py  (session load/save, persona, history, MySQL)
       ├── configmanager.py    (config, DB pool, MCP tool discovery)
       └── jsonmcp_client.py   (JSON-RPC MCP client)
                │
                ▼
        MCP tool servers        (SSE / StreamableHTTP / JSON-RPC)
```

## Features

- **Multi-protocol MCP support** — connects to MCP servers over SSE, StreamableHTTP, or JSON-RPC
- **Streaming responses** — tokens are streamed to the client as they arrive from the LLM
- **Tool call loop** — the LLM can invoke multiple MCP tools per turn (configurable limit, default 8)
- **Persona system** — system prompts are loaded from Markdown files under `personas/`, with template variables for user context and current date
- **Auto-generated chat titles** — after 8 messages, the server generates a short title for the conversation using the LLM
- **Bearer token auth** — per-MCP-server credentials, including user-delegated tokens from session `credentials`
- **Per-tool description overrides** — tool descriptions can be prefixed, postfixed, or fully replaced in config, and individual tools can be disabled per server

## Requirements

- Python 3.10+
- MySQL (for session persistence)
- A Google Gemini API key
- One or more MCP-compatible tool servers

## Installation

```bash
git clone https://github.com/grtxx/neonserver.git
cd neonserver
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.sample.json config.json
```

Edit `config.json` with your credentials (see Configuration below), then:

```bash
./startneon.sh
```

The server starts on the configured port (default: `8010`). Open `http://localhost:8010` for the built-in chat UI.

## Configuration

`config.json` structure:

```json
{
    "models": {
        "gemini-2.5-flash": {
            "apikey": "<GEMINI_API_KEY>"
        }
    },
    "mysql": {
        "host": "localhost",
        "database": "chatapp_db",
        "user": "chatapp",
        "password": "<MYSQL_PASSWORD>"
    },
    "webserver": {
        "port": 8010
    },
    "logging": {
        "level": "INFO",
        "filename": "logs/chatapp.log"
    },
    "chatparams": {
        "model": "gemini-2.5-flash",
        "previousmessages": 15
    },
    "personas": {
        "neon": "neon_friendly.md"
    },
    "mcpservers": {
        "my-tool-server": {
            "proto": "sse",
            "url": "http://localhost:10001/sse"
        },
        "secure-server": {
            "proto": "streamablehttp",
            "url": "https://example.com/mcp",
            "credentials": {
                "type": "bearer",
                "bearertoken": "<TOKEN>"
            },
            "overrides": {
                "prefix": "Use this tool for company data. ",
                "tools-disabled": ["some_tool_to_skip"]
            }
        }
    }
}
```

**MCP protocol options:** `sse`, `streamablehttp`, `jsonrpc`

**Credential types:**
- `bearer` — static Bearer token added to all requests
- `bearer-user` — token path resolved from the session's `credentials` object (for per-user tokens)

## Personas

Persona files are Markdown documents placed in the `personas/` directory. They define the AI's system prompt and support template variables:

| Variable | Description |
|---|---|
| `{language}` | Communication language |
| `{currentdate}` | Current date and time |
| `{guid}` | User identifier |
| `{fullname}` | User's full name |
| `{email}` | User's email address |
| `{jobtitle}` | User's job title |
| `{department}` | User's department |
| `{mobilephone}` | User's phone number |
| `{avatarurl}` | URL of user's avatar image |

Two example personas are included: `neon_friendly.md` and `neon_greedy.md`.

## REST API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/createchat` | Create a new chat session, returns `{ sid }` |
| `GET` | `/api/v1/chat/{sid}/history` | Retrieve message history for a session |
| `WS` | `/ws/{sid}` | WebSocket for sending messages and receiving streamed responses |

### WebSocket message types (server → client)

| Type | Description |
|---|---|
| `token` | Incremental text chunk |
| `done` | End of a response segment |
| `title` | Auto-generated chat title |
| `toolcall` | Tool invocation notification |
| `approverequest` | Approval request for a tool call |

## Database schema

Three tables, all in `utf8mb4`:

**`chats`** — one row per chat session
| Column | Type | Description |
|---|---|---|
| `sid` | char(64) PK | Session UUID (hex) |
| `name` | text | Auto-generated chat title |
| `settings` | mediumtext | JSON blob: model, persona, userdata, credentials |
| `otp` | char(64) | One-time token (for session handoff) |
| `status` | enum | `running` / `approvalwait` / `archived` |

**`messages`** — chat history, linked to `chats` via FK with `ON DELETE CASCADE`
| Column | Type | Description |
|---|---|---|
| `gid` | int PK | Auto-increment |
| `sid` | char(64) FK | References `chats.sid` |
| `date` | datetime | Message timestamp |
| `id` | int | Sequence number within the session |
| `message` | mediumtext | Serialized LangChain message (JSON) |

**`user_memory`** — persistent cross-session memory, keyed by user GUID and memory ID
| Column | Type | Description |
|---|---|---|
| `id` | int PK | Auto-increment |
| `userguid` | char(64) | User identifier from session userdata |
| `mem_id` | char(64) | Memory key (alphanumeric + underscore) |
| `mem_contents` | mediumtext | Memory value |
| `updated` | datetime | Last write timestamp |

To create the schema:

```bash
mysql -u chatapp -p chatapp_db < schema.sql
```

A `schema.sql` dump is included in the repository.

## License

MIT
