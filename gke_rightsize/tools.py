"""ADK Tools for GKE Workload Rightsizing.

Provides standard Python functions equipped with docstrings and type annotations
that Google ADK automatically wraps as FunctionTools for the LLM agent.
"""

import os
import subprocess
from typing import Dict, List, Optional
import yaml

from .metrics_collector import GKEMetricsCollector
from .comparator import WorkloadComparator, RightsizeRecommendation
from .manifest_generator import ManifestGenerator


def list_clusters() -> dict:
    """Lists all GKE clusters in the Google Cloud project that have workload metrics.

    Returns:
        dict: A dictionary containing the list of discovered clusters and total count.
    """
    collector = GKEMetricsCollector()
    clusters = collector.list_clusters()
    return {
        "status": "success",
        "clusters": clusters,
        "total_clusters": len(clusters),
        "project_id": collector.project_id,
    }


def gather_vpa_metrics(cluster_name: str = "", namespace: str = "") -> dict:
    """Gathers VPA recommendations and current container requests from Google Cloud Monitoring.

    Args:
        cluster_name: Optional filter for a specific cluster (e.g. 'cluster-b', 'gke-ops').
        namespace: Optional filter for a specific Kubernetes namespace (e.g. 'default').

    Returns:
        dict: Workload metrics including recommended CPU/memory and current requests.
    """
    collector = GKEMetricsCollector()
    c_filter = cluster_name if cluster_name.strip() else None
    ns_filter = namespace if namespace.strip() else None

    workloads = collector.gather_workload_metrics(cluster_name=c_filter, namespace=ns_filter)
    return {
        "status": "success",
        "project_id": collector.project_id,
        "cluster_filter": c_filter or "all",
        "namespace_filter": ns_filter or "all",
        "total_workloads_found": len(workloads),
        "workloads": [w.to_dict() for w in workloads],
    }


def compare_workloads(
    cluster_name: str = "",
    namespace: str = "",
    workload_name: str = "",
    buffer_percent: float = 15.0,
) -> dict:
    """Compares current CPU and memory requests against VPA recommendations.

    Calculates over-provisioning and under-provisioning deltas, percentage changes,
    risk factors, and potential monthly cost savings.

    Args:
        cluster_name: Optional cluster name filter.
        namespace: Optional namespace filter.
        workload_name: Optional workload/deployment name filter (e.g. 'ccc-demo-app').
        buffer_percent: Safety buffer percentage headroom to add to VPA target (default: 15.0%).

    Returns:
        dict: Rightsizing analysis, savings summary, and markdown report table.
    """
    collector = GKEMetricsCollector()
    comparator = WorkloadComparator(default_buffer_percent=buffer_percent)

    c_filter = cluster_name if cluster_name.strip() else None
    ns_filter = namespace if namespace.strip() else None
    w_filter = workload_name if workload_name.strip() else None

    raw_metrics = collector.gather_workload_metrics(cluster_name=c_filter, namespace=ns_filter)
    if w_filter:
        raw_metrics = [m for m in raw_metrics if m.controller_name == w_filter]

    recommendations: List[RightsizeRecommendation] = []
    for m in raw_metrics:
        rec = comparator.analyze_workload(m, buffer_percent=buffer_percent)
        recommendations.append(rec)

    markdown_report = comparator.generate_markdown_report(recommendations)
    total_savings = sum(r.monthly_savings_usd for r in recommendations)
    total_spot_savings = sum(r.spot_monthly_savings_usd for r in recommendations if r.spot_eligible)
    spot_eligible_count = sum(1 for r in recommendations if r.spot_eligible)

    return {
        "status": "success",
        "total_analyzed": len(recommendations),
        "total_monthly_savings_usd": round(total_savings, 2),
        "total_spot_savings_usd": round(total_spot_savings, 2),
        "spot_eligible_count": spot_eligible_count,
        "recommendations": [r.to_dict() for r in recommendations],
        "markdown_report": markdown_report,
    }


def evaluate_spot_workloads(
    cluster_name: str = "",
    namespace: str = "",
) -> dict:
    """Evaluates GKE workloads to determine if they can safely run on cost-saving Spot VMs.

    Identifies stateless Deployments in application namespaces, excludes critical cluster
    infrastructure (e.g. gmp-system, kube-system), calculates additional Spot VM monthly savings
    (60-80% off right-sized compute), and provides GKE golden-path Spot configuration
    (nodeSelector, 25s termination grace period, preStop hook, PDB).

    Args:
        cluster_name: Optional cluster name filter (e.g. 'cluster-b').
        namespace: Optional namespace filter (e.g. 'default').

    Returns:
        dict: Detailed breakdown of Spot-eligible and ineligible workloads, additional Spot savings,
              and preemption risk warnings.
    """
    comp_res = compare_workloads(cluster_name=cluster_name, namespace=namespace)
    recs = comp_res.get("recommendations", [])

    eligible = [r for r in recs if r.get("spot_eligible")]
    ineligible = [r for r in recs if not r.get("spot_eligible")]
    total_spot_savings = sum(r.get("spot_monthly_savings_usd", 0.0) for r in eligible)

    return {
        "status": "success",
        "total_evaluated": len(recs),
        "total_spot_eligible": len(eligible),
        "total_spot_ineligible": len(ineligible),
        "potential_additional_spot_savings_usd": round(total_spot_savings, 2),
        "spot_eligible_workloads": eligible,
        "spot_ineligible_workloads": ineligible,
        "golden_path_guidance": {
            "node_selector": {"cloud.google.com/gke-provisioning": "Spot"},
            "termination_grace_period_seconds": 25,
            "min_replicas_for_ha": 2,
            "preemption_notice_window": "30 seconds",
            "required_prerequisites": [
                "Run with replicas >= 2 for high availability",
                "Set terminationGracePeriodSeconds to 25 (must be < 30s)",
                "Add preStop hook with sleep 5 for connection draining",
                "Deploy a PodDisruptionBudget (PDB) to prevent concurrent voluntary evictions",
            ],
        },
    }


def _get_existing_deployment_spec(workload_name: str, namespace: str) -> Optional[dict]:
    """Attempts to read the current live deployment via kubectl if available."""
    try:
        cmd = ["kubectl", "get", "deployment", workload_name, "-n", namespace, "-o", "yaml"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return yaml.safe_load(res.stdout)
    except Exception:
        pass
    return None


def generate_rightsized_manifest(
    workload_name: str,
    namespace: str = "default",
    cluster_name: str = "",
    buffer_percent: float = 15.0,
    manifest_type: str = "deployment",
    include_limits: bool = True,
    include_spot: bool = False,
) -> dict:
    """Generates clean, production-ready Kubernetes YAML manifests with recommended resource sizes.

    Args:
        workload_name: The name of the deployment/workload (e.g. 'ccc-demo-app').
        namespace: The Kubernetes namespace (default: 'default').
        cluster_name: Optional cluster name to match against metrics.
        buffer_percent: Headroom safety buffer percentage (default: 15.0%).
        manifest_type: Type of manifest to generate: 'deployment' (full manifest) or 'patch' (strategic merge patch).
        include_limits: Whether to include recommended resource limits.
        include_spot: Whether to include GKE Spot VM configuration (nodeSelector, 25s grace period, preStop).

    Returns:
        dict: Clean YAML manifest string, comparison details, Spot evaluation, and suggested filename.
    """
    collector = GKEMetricsCollector()
    comparator = WorkloadComparator(default_buffer_percent=buffer_percent)
    generator = ManifestGenerator()

    c_filter = cluster_name if cluster_name.strip() else None
    raw_metrics = collector.gather_workload_metrics(cluster_name=c_filter, namespace=namespace)
    matched = [m for m in raw_metrics if m.controller_name == workload_name]

    if not matched:
        return {
            "status": "error",
            "message": f"Workload '{workload_name}' in namespace '{namespace}' not found in cluster metrics.",
        }

    target_metric = matched[0]
    rec = comparator.analyze_workload(target_metric, buffer_percent=buffer_percent)

    # Check if live deployment can enrich image / labels
    live_spec = _get_existing_deployment_spec(workload_name, namespace)
    image = None
    if live_spec:
        try:
            containers = live_spec.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            for c in containers:
                if c.get("name") == rec.container_name:
                    image = c.get("image")
                    break
        except Exception:
            pass

    if manifest_type.lower() == "patch":
        yaml_content = generator.generate_patch_manifest(rec, include_limits=include_limits, include_spot=include_spot)
        filename = f"{workload_name}-patch.yaml"
    else:
        yaml_content = generator.generate_deployment_manifest(
            rec,
            replicas=live_spec.get("spec", {}).get("replicas", 2) if live_spec else 2,
            image=image,
            include_limits=include_limits,
            include_spot=include_spot,
        )
        filename = f"{workload_name}-rightsized.yaml"

    return {
        "status": "success",
        "workload_name": workload_name,
        "namespace": namespace,
        "cluster_name": rec.cluster_name,
        "manifest_type": manifest_type,
        "suggested_filename": filename,
        "yaml_content": yaml_content,
        "current_resources": {
            "cpu": rec.current_cpu_str,
            "memory": rec.current_memory_str,
        },
        "recommended_resources": {
            "cpu": rec.recommended_cpu_str,
            "memory": rec.recommended_memory_str,
            "cpu_limit": rec.recommended_cpu_limit_str if include_limits else None,
            "memory_limit": rec.recommended_memory_limit_str if include_limits else None,
        },
        "cpu_change": f"{rec.cpu_delta_percent:+.1f}%",
        "memory_change": f"{rec.memory_delta_percent:+.1f}%",
        "status_evaluation": rec.status,
        "spot_evaluation": {
            "eligible": rec.spot_eligible,
            "status": rec.spot_status,
            "reason": rec.spot_reason,
            "monthly_savings_usd": rec.spot_monthly_savings_usd,
            "preemption_warning": rec.spot_preemption_warning,
            "spot_config_included": include_spot and rec.spot_eligible,
        },
    }


def save_manifest(workload_name: str, yaml_content: str, filename: str = "", output_dir: str = "manifests") -> dict:
    """Saves a generated Kubernetes YAML manifest to disk.

    Args:
        workload_name: The name of the workload.
        yaml_content: The YAML string content to save.
        filename: Optional filename (e.g. 'ccc-demo-app-rightsized.yaml').
        output_dir: Directory where manifests are stored (default: 'manifests').

    Returns:
        dict: File path and status.
    """
    os.makedirs(output_dir, exist_ok=True)
    if not filename:
        filename = f"{workload_name}-rightsized.yaml"
    target_path = os.path.join(output_dir, filename)

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(yaml_content)

    return {
        "status": "success",
        "saved_path": os.path.abspath(target_path),
        "filename": filename,
        "bytes_written": len(yaml_content),
    }


def rightsize_all_workloads(
    cluster_name: str = "",
    buffer_percent: float = 15.0,
    include_spot: bool = False,
    output_dir: str = "manifests",
) -> dict:
    """Automated end-to-end rightsizing pipeline for all workloads.

    Analyzes all workloads, computes comparisons, evaluates Spot VM eligibility,
    generates clean YAML manifests, and writes them to the specified directory.

    Args:
        cluster_name: Optional cluster filter.
        buffer_percent: Headroom safety buffer percentage (default: 15.0%).
        include_spot: Whether to include Spot VM configuration for eligible workloads.
        output_dir: Output directory to save YAML manifests (default: 'manifests').

    Returns:
        dict: Summary of all generated manifests, total savings, Spot VM breakdown, and report.
    """
    comp_result = compare_workloads(cluster_name=cluster_name, buffer_percent=buffer_percent)
    recommendations = comp_result.get("recommendations", [])

    generated_files = []
    for r_data in recommendations:
        w_name = r_data["controller_name"]
        ns = r_data["namespace_name"]
        c_name = r_data["cluster_name"]

        gen_res = generate_rightsized_manifest(
            workload_name=w_name,
            namespace=ns,
            cluster_name=c_name,
            buffer_percent=buffer_percent,
            include_spot=include_spot,
        )
        if gen_res.get("status") == "success":
            yaml_str = gen_res["yaml_content"]
            fname = f"{c_name}_{ns}_{w_name}-rightsized.yaml"
            save_res = save_manifest(workload_name=w_name, yaml_content=yaml_str, filename=fname, output_dir=output_dir)
            generated_files.append({
                "workload": w_name,
                "namespace": ns,
                "cluster": c_name,
                "saved_file": save_res["saved_path"],
                "cpu_change": gen_res["cpu_change"],
                "memory_change": gen_res["memory_change"],
                "status": gen_res["status_evaluation"],
                "spot_eligible": gen_res.get("spot_evaluation", {}).get("eligible", False),
            })

    return {
        "status": "success",
        "total_manifests_generated": len(generated_files),
        "manifests": generated_files,
        "total_monthly_savings_usd": comp_result.get("total_monthly_savings_usd", 0.0),
        "total_spot_savings_usd": comp_result.get("total_spot_savings_usd", 0.0),
        "spot_eligible_count": comp_result.get("spot_eligible_count", 0),
        "markdown_report": comp_result.get("markdown_report", ""),
    }
