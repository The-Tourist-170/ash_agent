from __future__ import annotations

import logging
import os
import sys

DATE_FORMAT = "%H:%M:%S"
_COMPONENT_WIDTH = 22

_RESET = "\033[0m"
_DIM = "\033[2m"
_LEVEL_COLORS = {
    "DEBUG": "\033[38;5;244m",
    "INFO": "\033[38;5;42m",
    "WARNING": "\033[38;5;214m",
    "ERROR": "\033[38;5;203m",
    "CRITICAL": "\033[1;38;5;196m",
}
_COMPONENT_COLORS = {
    "agent": "\033[38;5;141m",
    "svc": "\033[38;5;114m",
    "sandbox": "\033[38;5;221m",
    "store": "\033[38;5;111m",
    "intel": "\033[38;5;80m",
    "queue": "\033[38;5;39m",
    "rq": "\033[38;5;67m",
    "api": "\033[38;5;75m",
    "uvicorn": "\033[38;5;75m",
}
_COMPONENT_PREFIXES = {
    "ash_agent.agents.nodes.": "agent.",
    "ash_agent.agents.": "agent.",
    "ash_agent.services.": "svc.",
    "ash_agent.sandbox.": "sandbox.",
    "ash_agent.persistence.": "store.",
    "ash_agent.intelligence.": "intel.",
    "ash_agent.queue.": "queue.",
    "ash_agent.api.": "api.",
}
_QUIET_LOGGERS = (
    "httpx",
    "httpcore",
    "openai",
    "urllib3",
    "neo4j",
    "qdrant_client",
    "fastembed",
    "langchain",
    "langchain_openai",
)


class ConsoleFormatter(logging.Formatter):
    def __init__(self, *, use_colors: bool = True) -> None:
        super().__init__(datefmt=DATE_FORMAT)
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        level = record.levelname
        component = _short_name(record.name)
        message = record.getMessage()

        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"

        if not self.use_colors:
            prefix = f"{timestamp} {level:<7} {component:<{_COMPONENT_WIDTH}} | "
            continuation = " " * (len(prefix) - 2) + "| "
            return _format_lines(message, prefix, continuation)

        level_color = _LEVEL_COLORS.get(level, "")
        component_key = component.split(".", 1)[0]
        component_color = _COMPONENT_COLORS.get(component_key, "")
        prefix = (
            f"{_DIM}{timestamp}{_RESET} "
            f"{level_color}{level:<7}{_RESET} "
            f"{component_color}{component:<{_COMPONENT_WIDTH}}{_RESET} "
            f"{_DIM}|{_RESET} "
        )
        continuation = " " * (8 + 1 + 7 + 1 + _COMPONENT_WIDTH + 1 + 1) + f"{_DIM}|{_RESET} "
        return _format_lines(message, prefix, continuation)


def _format_lines(message: str, prefix: str, continuation: str) -> str:
    lines = message.splitlines() or [""]
    rendered = [f"{prefix}{lines[0]}"]
    rendered.extend(f"{continuation}{line}" for line in lines[1:])
    return "\n".join(rendered)


def _short_name(name: str) -> str:
    for prefix, replacement in _COMPONENT_PREFIXES.items():
        if name.startswith(prefix):
            return replacement + name[len(prefix) :]
    return name


def _colors_enabled(mode: str) -> bool:
    mode = (mode or "auto").lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def configure_logging(level: str = "INFO", colors: str = "auto") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ConsoleFormatter(use_colors=_colors_enabled(colors)))

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    root.addHandler(handler)

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
