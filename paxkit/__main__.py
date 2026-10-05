"""Run: python -m paxkit [--config config.yaml]  (GUI)  |  python -m paxkit <command> ...  (CLI, see --help)"""
import sys

from paxkit.cli import main

if __name__ == "__main__":
    sys.exit(main())
