# endoscope — Lightweight Debug Artifact Capture System

**endoscope** is a lightweight system for capturing structured debugging data and files from distributed applications, storing them in S3-compatible object storage, and retrieving them locally via a CLI.

This is **not** an observability platform. It is designed for:

- Ad hoc debugging
- Environments without existing observability tooling
- Situations where debugging data needs to be isolated or externalized
- Fast, low-friction instrumentation
- One project per deployment (if you need multiple API keys, deploy multiple instances)

## Key Principles

- **Simple** — Minimal dependencies, easy to understand
- **Stateless** — No database, all state in S3
- **Session-first** — Sessions are the primary abstraction
- **Developer-friendly** — Easy CLI, clear error messages

## Quick Start

1. **Deploy endoscope server somewhere**

  FIXME

2. **Set your environment variables:**

   ```bash
   export ENDO_API_KEY=local-dev-api-key
   export ENDO_PROJECT=local-test-project
   export ENDO_API_URL=http://localhost:8000
   ```

4. **Use the CLI:**

   ```bash
   # List sessions
   endoscope list

   # Pull last 5 sessions to local environment
   endoscope pull --last 5

   # Delete all sessions from S3 storage
   endoscope purge --all
   ```

## Core Concepts

### Session

A **session** is the primary unit of data in endoscope. Each session contains:

- `session_id` — A unique UUID
- `timestamp` — When the session was created
- `project` — The project name (for isolation)
- Optional metadata

Each session stores:
- `metadata.json` — Session metadata
- `events/` — Structured event data (JSON)
- `files/` — Binary files (logs, screenshots, dumps, etc.)

### Project

A **project** is a namespace for sessions. Each endoscope deployment is intended to serve a single project. If you need multiple projects, deploy separate instances.



## Architecture

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│   Your App      │─────▶│  endoscope API  │─────▶│  S3 Storage     │
│   (SDK/HTTP)    │      │   (Starlette)   │      │  (RustFS/AWS)   │
└─────────────────┘      └─────────────────┘      └─────────────────┘
                                │
                                ▼
                         ┌─────────────────┐
                         │     CLI         │
                         │  (pull, list,   │
                         │   show, etc.)   │
                         └─────────────────┘
```

**Components:**

| Component     | Technology     | Description                              |
|------------|----------------|------------------------------------------|
| API Service   | Starlette      | Stateless REST API for session ingestion |
| Python SDK    | httpx          | Synchronous client for instrumenting apps|
| CLI           | Typer + Rich   | Manage and retrieve sessions             |
| Storage       | S3-compatible  | RustFS (local) or AWS S3 (production)    |



## Installation

### From Source

```bash
# Clone the repository
git clone https://github.com/your-org/endoscope.git
cd endoscope

# Install dependencies with uv
uv sync

# Install the CLI
uv pip install -e .
```

### Using Docker

```bash
docker compose up -d
```


## Configuration

### Environment Variables

| Variable              | Description                          | Example                        |
|--------------------|--------------------------------------|--------------------------------|
| `ENDO_API_KEY`        | API key for authentication           | `my-secret-key`                |
| `ENDO_PROJECT`        | Project name                         | `my-app`                       |
| `ENDO_API_URL`        | API base URL (CLI only)              | `http://localhost:8000`        |
| `ENDO_S3_ENDPOINT`    | S3-compatible endpoint               | `http://rustfs:9000`           |
| `ENDO_S3_ACCESS_KEY`  | S3 access key                        | `rustfsadmin`                  |
| `ENDO_S3_SECRET_KEY`  | S3 secret key                        | `rustfsadmin`                  |
| `ENDO_S3_BUCKET`      | S3 bucket name                       | `endoscope`                    |
| `ENDO_S3_REGION`      | S3 region                            | `us-east-1`                    |
| `ENDO_HOST`           | Host to bind (serve command)         | `0.0.0.0`                      |
| `ENDO_PORT`           | Port to bind (serve command)         | `8000`                         |
| `ENDO_DEBUG`          | Enable debug mode                    | `true`                         |
| `ENDO_PRETTY_JSON_LOGS` | Pretty-print JSON logs             | `true`                         |

### Local Development

For local development

```bash
ENDO_PROJECT=local-test-project
ENDO_API_KEY=local-dev-api-key
ENDO_API_URL=http://localhost:8000
```


## Using the API Service

### Start the Service

```bash
# Using the CLI
endoscope serve \
  --project my-project \
  --s3-endpoint http://localhost:9000 \
  --s3-access-key rustfsadmin \
  --s3-secret-key rustfsadmin \
  --s3-bucket endoscope

# Or with Docker Compose
docker compose up api
```

### Authentication

All API requests require the `X-API-Key` header:

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/healthz
```



## Python SDK

### Installation

```bash
pip install endoscope
```

### Basic Usage

```python
from endoscope import EndoscopeClient

with EndoscopeClient.from_env() as client:
    # Start a session
    session = client.start_session(project="my-app")

    # Add events
    session.event("step", {"x": 1, "y": 2})
    session.event("error", {"message": "Something went wrong"})

    # Add files
    session.file("output.txt", b"hello world")
    session.file("screenshot.png", open("img.png", "rb").read())
```

### SDK Methods

| Method                    | Description                    |
|------------------------|--------------------------------|
| `start_session(project)`  | Create a new session           |
| `session.event(name, data)` | Add a structured event       |
| `session.file(name, data)`  | Upload a binary file         |

### Example

```python
from endoscope import EndoscopeClient

# Create client from environment
client = EndoscopeClient.from_env()

# Start a session and record debug data
session = client.start_session(project="my-app")
session.event("step", {"x": 1, "y": 2})
session.file("output.txt", b"hello world")

# Clean up
client.close()
```



## CLI Reference

### Setup

```bash
# Set required environment variables
export ENDO_API_KEY=your-api-key
export ENDO_PROJECT=your-project

# Or use flags
endoscope --api-key=xxx --project=foo list
```

### Commands

| Command                          | Description                              |
|-------------------------------|------------------------------------------|
| `endoscope serve`                | Start the API service                    |
| `endoscope list [--json]`        | List all sessions                        |
| `endoscope show <session-id>`    | Show session details                     |
| `endoscope pull <session-id>`    | Download session files                   |
| `endoscope pull --all`           | Download all sessions                    |
| `endoscope pull --last 10`       | Download N most recent sessions          |
| `endoscope delete <session-id>`  | Delete a session                         |
| `endoscope prune --older-than 7d`| Delete sessions older than 7 days        |
| `endoscope prune --all`          | Delete all sessions                      |
| `endoscope api-key`              | Generate a random API key                |

### Examples

```bash
# List sessions as a table
endoscope list

# List sessions as JSON
endoscope list --json

# Show a specific session
endoscope show 550e8400-e29b-41d4-a716-446655440000

# Pull a session to local disk
endoscope pull 550e8400-e29b-41d4-a716-446655440000

# Pull the last 10 sessions
endoscope pull --last 10

# Delete old sessions
endoscope prune --older-than 30d

# Generate a new API key
endoscope api-key
```



## Storage Model

Sessions are stored in S3 with the following structure:

```
bucket/
  <project>/
    yyyy/mm/dd/
      <timestamp>--<session_uuid>/
        metadata.json
        events/
          event-001.json
          event-002.json
        files/
          output.txt
          screenshot.png
```

This structure enables:
- Efficient listing by date
- Easy lifecycle policies for retention
- Project-level isolation



## API Endpoints

| Method | Endpoint                          | Description              |
|--------|-----------------------------------|--------------------------|
| GET    | `/healthz`                        | Health check             |
| GET    | `/readyz`                         | Readiness check          |
| POST   | `/v1/sessions`                    | Create a session         |
| POST   | `/v1/sessions/{id}/events`        | Add an event to session  |
| POST   | `/v1/sessions/{id}/files`         | Upload a file            |
| GET    | `/v1/sessions/{id}/manifest`      | Get session manifest     |

### Example: Create a Session

```bash
curl -X POST http://localhost:8000/v1/sessions \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"project": "my-app", "metadata": {"version": "1.0"}}'
```



## Development

### Running Tests

```bash
just test
```

### Formatting Code

```bash
just fmt
```

### Docker Utilities

```bash
just up          # Start all services
just down        # Stop all services
just logs        # Follow all logs
just tail        # Follow logs with tail
just shell       # Shell in API container
just test        # Run tests
```


## Roadmap

- [x] API Service
- [x] Python SDK (in this repo)
- [ ] Javascript SDK

## License and Author

MIT - Written by [Frank Wiles](https://www.frankwiles.com/about/)
