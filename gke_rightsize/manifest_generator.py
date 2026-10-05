"""Kubernetes Manifest Generator for Right-Sized Workloads.

Generates clean, production-ready Kubernetes YAML manifests and patch files
reflecting VPA-recommended CPU and memory requests and limits.
"""

import os
from datetime import datetime, timezone
from typing import Dict, List, Optional
import yaml
from .comparator import RightsizeRecommendation


class ManifestGenerator:
    """Generates clean YAML manifests for right-sized GKE workloads."""

    def __init__(self, output_dir: str = "manifests"):
        self.output_dir = output_dir

    def generate_deployment_manifest(
        self,
        recommendation: RightsizeRecommendation,
        replicas: int = 2,
        image: Optional[str] = None,
        include_limits: bool = True,
        include_spot: bool = False,
    ) -> str:
        """Generates a full, clean Kubernetes Deployment manifest with optional Spot VM config."""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        container_image = image or f"gcr.io/{recommendation.cluster_name}/{recommendation.controller_name}:latest"

        resources: Dict[str, dict] = {
            "requests": {
                "cpu": recommendation.recommended_cpu_str,
                "memory": recommendation.recommended_memory_str,
            }
        }
        if include_limits:
            resources["limits"] = {
                "cpu": recommendation.recommended_cpu_limit_str,
                "memory": recommendation.recommended_memory_limit_str,
            }

        effective_replicas = replicas
        template_spec: Dict[str, any] = {}

        container_spec = {
            "name": recommendation.container_name,
            "image": container_image,
            "resources": resources,
        }

        annotations = {
            "rightsize.gke.io/last-updated": now_iso,
            "rightsize.gke.io/cluster": recommendation.cluster_name,
            "rightsize.gke.io/previous-cpu": recommendation.current_cpu_str,
            "rightsize.gke.io/previous-memory": recommendation.current_memory_str,
            "rightsize.gke.io/savings-status": recommendation.status,
            "rightsize.gke.io/safety-buffer": f"{recommendation.buffer_percent}%",
            "rightsize.gke.io/spot-eligible": "true" if recommendation.spot_eligible else "false",
        }

        if include_spot and recommendation.spot_eligible:
            effective_replicas = max(replicas, 2)
            annotations["rightsize.gke.io/spot-provisioning"] = "Spot"
            annotations["rightsize.gke.io/spot-est-savings"] = f"${recommendation.spot_monthly_savings_usd:.2f}/mo"
            template_spec["nodeSelector"] = {"cloud.google.com/gke-provisioning": "Spot"}
            template_spec["terminationGracePeriodSeconds"] = 25
            template_spec["tolerations"] = [
                {
                    "key": "cloud.google.com/gke-spot",
                    "operator": "Equal",
                    "value": "true",
                    "effect": "NoSchedule",
                }
            ]
            container_spec["lifecycle"] = {
                "preStop": {
                    "exec": {
                        "command": ["/bin/sh", "-c", "sleep 5"]
                    }
                }
            }

        template_spec["containers"] = [container_spec]

        manifest = {
            "apiVersion": "apps/v1",
            "kind": recommendation.controller_kind or "Deployment",
            "metadata": {
                "name": recommendation.controller_name,
                "namespace": recommendation.namespace_name,
                "labels": {
                    "app.kubernetes.io/name": recommendation.controller_name,
                    "app.kubernetes.io/managed-by": "gke-rightsize-adk-agent",
                },
                "annotations": annotations,
            },
            "spec": {
                "replicas": effective_replicas,
                "selector": {
                    "matchLabels": {
                        "app": recommendation.controller_name,
                    }
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app": recommendation.controller_name,
                        }
                    },
                    "spec": template_spec,
                },
            },
        }

        spot_comment = ""
        if include_spot and recommendation.spot_eligible:
            spot_comment = (
                f"# Spot VM Configuration: ENABLED (GKE Provisioning: Spot)\n"
                f"# Estimated Spot Additional Savings: ${recommendation.spot_monthly_savings_usd:.2f}/mo\n"
                f"# WARNING: Spot VMs are subject to 30s preemption. Replicas set to >= 2 with 25s grace period.\n"
            )

        header = (
            f"# ====================================================================\n"
            f"# Right-sized Manifest for {recommendation.controller_name} ({recommendation.namespace_name})\n"
            f"# Cluster: {recommendation.cluster_name} | Location: {recommendation.location}\n"
            f"# Status: {recommendation.status} | Safety Buffer: {recommendation.buffer_percent}%\n"
            f"# CPU Change: {recommendation.current_cpu_str} -> {recommendation.recommended_cpu_str} ({recommendation.cpu_delta_percent:+.1f}%)\n"
            f"# Memory Change: {recommendation.current_memory_str} -> {recommendation.recommended_memory_str} ({recommendation.memory_delta_percent:+.1f}%)\n"
            f"{spot_comment}"
            f"# ====================================================================\n"
        )
        return header + yaml.dump(manifest, sort_keys=False, default_flow_style=False)

    def generate_patch_manifest(
        self,
        recommendation: RightsizeRecommendation,
        include_limits: bool = True,
        include_spot: bool = False,
    ) -> str:
        """Generates a clean Strategic Merge Patch YAML for kubectl patch."""
        resources: Dict[str, dict] = {
            "requests": {
                "cpu": recommendation.recommended_cpu_str,
                "memory": recommendation.recommended_memory_str,
            }
        }
        if include_limits:
            resources["limits"] = {
                "cpu": recommendation.recommended_cpu_limit_str,
                "memory": recommendation.recommended_memory_limit_str,
            }

        container_patch = {
            "name": recommendation.container_name,
            "resources": resources,
        }

        template_spec_patch: Dict[str, any] = {
            "containers": [container_patch]
        }

        patch_spec: Dict[str, any] = {
            "template": {
                "spec": template_spec_patch
            }
        }

        spot_note = ""
        if include_spot and recommendation.spot_eligible:
            patch_spec["replicas"] = 2
            template_spec_patch["nodeSelector"] = {"cloud.google.com/gke-provisioning": "Spot"}
            template_spec_patch["terminationGracePeriodSeconds"] = 25
            template_spec_patch["tolerations"] = [
                {
                    "key": "cloud.google.com/gke-spot",
                    "operator": "Equal",
                    "value": "true",
                    "effect": "NoSchedule",
                }
            ]
            container_patch["lifecycle"] = {
                "preStop": {
                    "exec": {
                        "command": ["/bin/sh", "-c", "sleep 5"]
                    }
                }
            }
            spot_note = (
                f"# Spot VM Configuration: ENABLED (+${recommendation.spot_monthly_savings_usd:.2f}/mo extra savings)\n"
                f"# Enforces 25s termination grace period and Spot nodeSelector.\n"
            )

        patch = {
            "spec": patch_spec
        }

        header = (
            f"# Strategic Merge Patch for {recommendation.controller_name}\n"
            f"{spot_note}"
            f"# Apply with: kubectl patch {recommendation.controller_kind.lower()} {recommendation.controller_name} -n {recommendation.namespace_name} --patch-file <filename>\n"
        )
        return header + yaml.dump(patch, sort_keys=False, default_flow_style=False)

    def save_manifest(
        self,
        recommendation: RightsizeRecommendation,
        yaml_content: str,
        filename: Optional[str] = None,
    ) -> str:
        """Saves generated YAML to output directory and returns file path."""
        os.makedirs(self.output_dir, exist_ok=True)
        if not filename:
            safe_cluster = recommendation.cluster_name.replace("/", "-")
            filename = f"{safe_cluster}_{recommendation.namespace_name}_{recommendation.controller_name}.yaml"

        target_path = os.path.join(self.output_dir, filename)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        return target_path
