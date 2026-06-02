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

    run_interactive(dry_run=dry_run)


if __name__ == "__main__":
    main()
