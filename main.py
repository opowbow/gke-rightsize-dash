"""CLI Runner for GKE Rightsizing ADK Agent.

Supports interactive conversation, one-shot prompt execution, and direct batch rightsizing.
"""

import sys
import argparse
import asyncio
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from gke_rightsize.agent import root_agent
from gke_rightsize.tools import rightsize_all_workloads


def run_agent_query(user_prompt: str, verbose: bool = True):
    """Executes a single prompt with the ADK agent and prints the full conversation."""
    session_service = InMemorySessionService()
    session = session_service.create_session(app_name="gke_rightsize_app", user_id="platform_engineer")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="gke_rightsize_app")

    if verbose:
        print(f"\n[Platform Engineer] > {user_prompt}\n")

    content = types.Content(parts=[types.Part(text=user_prompt)], role="user")
    events = list(runner.run(user_id="platform_engineer", session_id=session.id, new_message=content))

    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.function_call:
                    args_summary = ", ".join(f"{k}={v}" for k, v in part.function_call.args.items())
                    print(f"⚙️  [Calling Tool] {part.function_call.name}({args_summary})")
                elif part.function_response:
                    status = part.function_response.response.get("status", "done")
                    print(f"✅ [Tool Finished] Status: {status}")
                elif part.text:
                    print(f"\n🤖 [ADK Agent]:\n{part.text.strip()}\n")


def interactive_mode():
    """Starts an interactive session with the ADK agent."""
    session_service = InMemorySessionService()
    session = session_service.create_session(app_name="gke_rightsize_app", user_id="platform_engineer")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="gke_rightsize_app")

    print("=" * 70)
    print(" GKE Workload Rightsizing Agent (Google ADK + Vertex AI)")
    print(" Type your questions or instructions (type 'exit' or 'quit' to end).")
    print("=" * 70)

    while True:
        try:
            user_input = input("\n[Platform Engineer] > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit", "q"]:
                print("Exiting rightsizing session.")
                break

            content = types.Content(parts=[types.Part(text=user_input)], role="user")
            events = list(runner.run(user_id="platform_engineer", session_id=session.id, new_message=content))
            for event in events:
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.function_call:
                            args_str = ", ".join(f"{k}={v}" for k, v in part.function_call.args.items())
                            print(f"⚙️  [Calling Tool] {part.function_call.name}({args_str})")
                        elif part.function_response:
                            status = part.function_response.response.get("status", "done")
                            print(f"✅ [Tool Finished] Status: {status}")
                        elif part.text:
                            print(f"\n🤖 [ADK Agent]:\n{part.text.strip()}\n")
        except (KeyboardInterrupt, EOFError):
            print("\nSession ended.")
            break


def main():
    parser = argparse.ArgumentParser(description="GKE Workload Rightsizing Agent using Google ADK.")
    parser.add_argument("prompt", nargs="?", default="", help="Prompt for the agent (e.g. 'rightsize workloads in cluster-b')")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start interactive mode")
    parser.add_argument("--run-all", action="store_true", help="Run automated rightsizing for all workloads immediately")
    parser.add_argument("--spot", action="store_true", help="Include Spot VM configuration in generated manifests for eligible workloads")
    parser.add_argument("--cluster", default="", help="Filter by cluster name")
    parser.add_argument("--buffer", type=float, default=15.0, help="Safety buffer percentage (default: 15.0)")
    parser.add_argument("--web", "--serve", dest="serve_web", action="store_true", help="Launch interactive web dashboard package locally")
    parser.add_argument("--host", default="0.0.0.0", help="Host address for web dashboard (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port for web dashboard (default: 8080)")

    args = parser.parse_args()

    if args.serve_web:
        from gke_rightsize.web import run_dashboard
        run_dashboard(host=args.host, port=args.port)
        return

    if args.run_all:
        spot_txt = " (with Spot VM config for eligible workloads)" if args.spot else ""
        print(f"Running automated rightsizing pipeline for all workloads (Buffer: {args.buffer}%){spot_txt}...")
        res = rightsize_all_workloads(cluster_name=args.cluster, buffer_percent=args.buffer, include_spot=args.spot)
        print(res.get("markdown_report", ""))
        print(f"\nGenerated {res.get('total_manifests_generated')} manifests in 'manifests/' directory.")
        for m in res.get("manifests", []):
            spot_tag = " [Spot Enabled]" if m.get("spot_eligible") and args.spot else ""
            print(f"  - {m['workload']} ({m['cluster']}): {m['saved_file']}{spot_tag}")
        return

    if args.interactive:
        interactive_mode()
    elif args.prompt:
        run_agent_query(args.prompt)
    else:
        # Default prompt if none provided
        run_agent_query("Inspect VPA recommendations on our clusters, compare them with current CPU and memory requests, evaluate Spot VM eligibility, and output clean YAML manifests with the recommended sizes.")


if __name__ == "__main__":
    main()
