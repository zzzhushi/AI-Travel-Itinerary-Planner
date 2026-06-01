"""Entry point for the CLI itinerary planner."""

import asyncio
import logging
import sys

logging.basicConfig(format="%(name)s: %(message)s")
logging.getLogger("src.agents.base").setLevel(logging.DEBUG)


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    json_path = None
    if "--json" in args:
        idx = args.index("--json")
        if idx + 1 < len(args):
            json_path = args[idx + 1]
        else:
            print("Error: --json requires a file path argument.")
            sys.exit(1)

    from src.cli import run_interactive, run_from_json

    if json_path:
        asyncio.run(run_from_json(json_path))
    else:
        run_interactive(dry_run=dry_run)


if __name__ == "__main__":
    main()
