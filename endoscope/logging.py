from __future__ import annotations

import json
import logging
import environs

import structlog


def configure() -> None:
    """Configure structlog for structured JSON logging.

    Set ``ENDOSCOPE_PRETTY_JSON_LOGS=true`` to indent/format JSON output.
    Default is compact single-line JSON suitable for log aggregators.
    """
    pretty = environs.Env().bool("ENDO_PRETTY_JSON_LOGS", False)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: structlog.types.Processor
    if pretty:
        renderer = structlog.processors.JSONRenderer(serializer=_dump_pretty)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, starlette, etc.) through structlog
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _dump_pretty(obj: dict, **kwargs: object) -> str:
    kwargs.setdefault("default", str)
    return json.dumps(obj, indent=2, sort_keys=True, **kwargs)  # type: ignore[arg-type]
