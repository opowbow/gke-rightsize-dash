"""Web dashboard package for GKE Workload Rightsizing Agent.

Provides an interactive web UI and REST API to explore cluster workloads,
inspect VPA recommendations, evaluate Spot VM eligibility, adjust safety buffers,
and generate or download right-sized Kubernetes manifests.
"""

from .app import create_app, run_dashboard

__all__ = ["create_app", "run_dashboard"]
