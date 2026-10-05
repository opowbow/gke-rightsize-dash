"""Main entry point for running the web dashboard as a module: `python3 -m gke_rightsize.web`."""

import argparse
from gke_rightsize.web.app import run_dashboard


def main():
    parser = argparse.ArgumentParser(description="Run GKE Rightsizing Web Dashboard.")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    args = parser.parse_args()

    run_dashboard(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
