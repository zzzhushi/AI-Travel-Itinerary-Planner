"""
Convenience launcher for the itinerary CLI.

From the project root (Windows CLI):

  python run_itinerary.py --trip path/to/trip.json --activities path/to/activities.csv --output-dir output

Requires GOOGLE_API_KEY in the environment (or .env).
"""

import sys
from pathlib import Path

# Ensure project root is on path
_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.cli.run import main

if __name__ == "__main__":
    sys.exit(main())
