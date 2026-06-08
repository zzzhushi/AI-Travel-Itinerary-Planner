"""Entry point for the CLI itinerary planner."""

import logging
import sys

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(format="%(name)s: %(message)s")
logging.getLogger("src.agents.base").setLevel(logging.DEBUG)


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args

    from src.cli import run_interactive

    # Structured logging: opt-in via OBSLOG_SINK=postgres; default NullSink keeps
    # --dry-run and unconfigured runs DB-free. Skip entirely on --dry-run.
    sink = None
    if not dry_run:
        from src.logging_setup import install_obslog_sink, shutdown_obslog

        sink = install_obslog_sink()
        if sink is not None:
            import obslog

            with obslog.operation("cli_startup"):  # smoke op: proves end-to-end writes
                obslog.trace("cli_started")

    try:
        run_interactive(dry_run=dry_run)
    finally:
        if sink is not None:
            shutdown_obslog()


if __name__ == "__main__":
    main()
