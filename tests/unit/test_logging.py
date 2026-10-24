import logging

from ash_agent.observability.logging import (
    ConsoleFormatter,
    _colors_enabled,
    _short_name,
)


def record(
    name: str = "ash_agent.agents.nodes.diagnose",
    level: int = logging.INFO,
    message: str = "LLM diagnosis completed for run %s",
    args: tuple = ("abc",),
) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, message, args, None)


class TestConsoleFormatter:
    def test_plain_output_is_aligned_and_readable(self) -> None:
        line = ConsoleFormatter(use_colors=False).format(record())

        assert "INFO" in line
        assert "agent.diagnose" in line
        assert "LLM diagnosis completed for run abc" in line
        assert "\x1b[" not in line

    def test_color_output_contains_ansi_codes(self) -> None:
        line = ConsoleFormatter(use_colors=True).format(record())

        assert "\x1b[" in line
        assert "agent.diagnose" in line

    def test_multiline_messages_are_indented(self) -> None:
        line = ConsoleFormatter(use_colors=False).format(
            record(message="first line\nsecond line", args=())
        )

        lines = line.splitlines()
        assert len(lines) == 2
        assert "first line" in lines[0]
        assert lines[1].startswith(" " * 30)
        assert "| second line" in lines[1]

    def test_component_names_are_shortened(self) -> None:
        assert _short_name("ash_agent.services.repair_service") == "svc.repair_service"
        assert _short_name("ash_agent.sandbox.executor") == "sandbox.executor"
        assert _short_name("ash_agent.persistence.neo4j") == "store.neo4j"
        assert _short_name("rq.worker") == "rq.worker"


class TestColorMode:
    def test_never_and_always_override_tty(self) -> None:
        assert _colors_enabled("never") is False
        assert _colors_enabled("always") is True
