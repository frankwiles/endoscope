set dotenv-load := false

# ----------------------------------------------------------------
# Just nice-to-haves
# ----------------------------------------------------------------

@_default:
    just --list

@fmt:
    just --fmt --unstable

# ----------------------------------------------------------------
# Common Docker Compose shortcuts
# ----------------------------------------------------------------

@down:
    docker compose down

@logs *ARGS:
    docker compose logs {{ ARGS }}

# Rebuild the containers used by docker compose
@rebuild:
    docker compose rm --force api
    docker compose build --force-rm api

# Restart a docker compose service by name
@restart *ARGS:
    docker compose restart {{ ARGS }}

# Follow all docker compose logs
@tail:
    just logs --follow --tail 100

@up *ARGS:
    docker compose up {{ ARGS }}

# ----------------------------------------------------------------
# Development commands
# ----------------------------------------------------------------

# Bash shell in the api container
@shell:
    docker compose run --rm api bash

# Execute Python code inside the api container (use -c for inline code)
run_python *ARGS:
    docker compose run --rm api python {{ ARGS }}

# Execute a Python file inside the api container
run_python_file file:
    docker compose run --rm api python {{ file }}

# Run pytest inside the api container (mounts project source)
@test *ARGS:
    docker compose run --rm -v $(pwd)/tests:/app/tests -v $(pwd)/endoscope:/app/endoscope api python -m pytest {{ ARGS }}
