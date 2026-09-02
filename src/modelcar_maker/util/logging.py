import logging
import logging.handlers
import sys


def make_logger(verbosity: int | None = None) -> logging.Logger:
    """Make a consistent logger that respects persistent verbosity settings."""
    logger = logging.getLogger("modelcar-maker")
    logger.setLevel(logging.DEBUG)

    stderr: logging.Handler

    if len(logger.handlers) == 0:
        _format = "{asctime} {name} ({module}/{filename} L{lineno}) [{levelname:^9s}]: {message}"
        formatter = logging.Formatter(_format, style="{")

        stderr = logging.StreamHandler(stream=sys.stderr)
        stderr.setFormatter(formatter)
        if verbosity is not None:
            stderr.setLevel(40 - (min(3, verbosity) * 10))
        else:
            stderr.setLevel(40)
        logger.addHandler(stderr)
    else:
        # CliRunner and redirected invocations replace stderr after import.
        # Keep diagnostics with the current process stderr, never stdout.
        stderr = logger.handlers[0]
        if isinstance(stderr, logging.StreamHandler):
            stderr.setStream(sys.stderr)
        if verbosity is not None:
            # Never lower the verbosity after it's been made high
            stderr.setLevel(min(stderr.level, 40 - (min(3, verbosity) * 10)))

    return logger


logger = logging.getLogger("modelcar-maker")
