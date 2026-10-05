"""Comparator and right-sizing logic for GKE container resources.

Computes comparisons between current requests and VPA recommendations,
applies recommended safety headroom, formats Kubernetes resource strings,
and calculates cost savings opportunities.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from .metrics_collector import ContainerRightsizeData


# Reference pricing for cost estimation (GKE Autopilot / standard compute approx)
COST_PER_VCPU_MONTH = 32.0   # ~$0.044 / hour
COST_PER_GIB_MONTH = 3.5     # ~$0.0048 / hour
SPOT_DISCOUNT_PERCENT = 65.0  # ~60-70% typical savings for Spot VMs on GKE

# Namespaces reserved for cluster infrastructure, operators, or system daemons
SYSTEM_NAMESPACES = {
    "kube-system",
    "gmp-system",
    "istio-system",
    "gatekeeper-system",
    "cert-manager",
    "config-management-system",
    "asm-system",
    "calico-system",
    "knative-serving",
}

# Controllers considered stateless and eligible for Spot VMs
STATELESS_CONTROLLER_KINDS = {
    "Deployment",
    "ReplicaSet",
    "Job",
    "CronJob",
}


def format_cpu_k8s(cores: float, min_millicores: int = 10) -> str:
    """Formats CPU float into standard Kubernetes resource string."""
    m_cores = max(int(round(cores * 1000)), min_millicores)
    if m_cores < 1000:
        return f"{m_cores}m"
    elif m_cores % 1000 == 0:
        return f"{m_cores // 1000}"
    else:
        return f"{m_cores / 1000:.2f}".rstrip("0").rstrip(".")


def format_memory_k8s(bytes_val: int, min_mib: int = 32) -> str:
    """Formats Memory bytes into standard Kubernetes resource string."""
    mib = max(int(round(bytes_val / (1024 * 1024))), min_mib)
    if mib >= 1024 and mib % 1024 == 0:
        return f"{mib // 1024}Gi"
    return f"{mib}Mi"


@dataclass
class RightsizeRecommendation:
    cluster_name: str
    location: str
    namespace_name: str
    controller_kind: str
    controller_name: str
    container_name: str
    
    # Current requests
    current_cpu_cores: float
    current_cpu_str: str
    current_memory_bytes: int
    current_memory_str: str
    
    # Raw VPA recommendation
    vpa_raw_cpu_cores: float
    vpa_raw_memory_bytes: int
    
    # Recommended with safety buffer & floor
    buffer_percent: float
    recommended_cpu_cores: float
    recommended_cpu_str: str
    recommended_memory_bytes: int
    recommended_memory_str: str

    # Recommended limits (standard ratio or existing limit)
    recommended_cpu_limit_str: str
    recommended_memory_limit_str: str
    
    # Deltas
    cpu_delta_cores: float
    cpu_delta_percent: float
    memory_delta_bytes: int
    memory_delta_percent: float
    
    # Status & Savings
    status: str  # OVERPROVISIONED, UNDERPROVISIONED, BALANCED
    monthly_savings_usd: float
    risk_level: str  # LOW, MEDIUM, HIGH

    # Spot VM Evaluation
    spot_eligible: bool = False
    spot_status: str = "NOT_RECOMMENDED"
    spot_reason: str = ""
    spot_monthly_savings_usd: float = 0.0
    spot_preemption_warning: str = ""
    spot_config: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "cluster_name": self.cluster_name,
            "location": self.location,
            "namespace_name": self.namespace_name,
            "controller_kind": self.controller_kind,
            "controller_name": self.controller_name,
            "container_name": self.container_name,
            "current_cpu": self.current_cpu_str,
            "current_memory": self.current_memory_str,
            "recommended_cpu": self.recommended_cpu_str,
            "recommended_memory": self.recommended_memory_str,
            "recommended_cpu_limit": self.recommended_cpu_limit_str,
            "recommended_memory_limit": self.recommended_memory_limit_str,
            "cpu_delta_percent": f"{self.cpu_delta_percent:+.1f}%",
            "memory_delta_percent": f"{self.memory_delta_percent:+.1f}%",
            "status": self.status,
            "estimated_monthly_savings_usd": round(self.monthly_savings_usd, 2),
            "buffer_percent": self.buffer_percent,
            "spot_eligible": self.spot_eligible,
            "spot_status": self.spot_status,
            "spot_reason": self.spot_reason,
            "spot_monthly_savings_usd": round(self.spot_monthly_savings_usd, 2),
            "spot_preemption_warning": self.spot_preemption_warning,
            "spot_config": self.spot_config or {},
        }


class WorkloadComparator:
    """Compares current container requests against VPA recommendations."""

    def __init__(self, default_buffer_percent: float = 15.0, min_cpu_m: int = 10, min_mem_mi: int = 32):
        self.default_buffer_percent = default_buffer_percent
        self.min_cpu_m = min_cpu_m
        self.min_mem_mi = min_mem_mi

    def analyze_workload(
        self,
        data: ContainerRightsizeData,
        buffer_percent: Optional[float] = None
    ) -> RightsizeRecommendation:
        """Computes recommendation and comparison for a single container."""
        buffer = self.default_buffer_percent if buffer_percent is None else buffer_percent
        
        # Apply buffer factor (e.g. 15% headroom = 1.15)
        buffer_factor = 1.0 + (buffer / 100.0)
        buffered_cpu = data.recommended_cpu_cores * buffer_factor
        buffered_mem = int(data.recommended_memory_bytes * buffer_factor)
        
        # Format strings with minimum floors
        rec_cpu_str = format_cpu_k8s(buffered_cpu, min_millicores=self.min_cpu_m)
        rec_mem_str = format_memory_k8s(buffered_mem, min_mib=self.min_mem_mi)
        
        # Parse effective recommended numeric values after floors
        if rec_cpu_str.endswith("m"):
            eff_rec_cpu = int(rec_cpu_str[:-1]) / 1000.0
        else:
            eff_rec_cpu = float(rec_cpu_str)
            
        if rec_mem_str.endswith("Gi"):
            eff_rec_mem = int(rec_mem_str[:-2]) * 1024 * 1024 * 1024
        else:
            eff_rec_mem = int(rec_mem_str[:-2]) * 1024 * 1024

        cur_cpu = data.current_request_cpu_cores if data.current_request_cpu_cores is not None else 0.1
        cur_mem = data.current_request_memory_bytes if data.current_request_memory_bytes is not None else (128 * 1024 * 1024)
        
        cur_cpu_str = format_cpu_k8s(cur_cpu, min_millicores=1)
        cur_mem_str = format_memory_k8s(cur_mem, min_mib=1)

        # Limits: Provide sensible limit recommendations
        # For CPU: 2x recommended request (burstable) or no limit (Kubernetes best practice to avoid throttling)
        # For Memory: 1.25x recommended request (room for cache before OOMKill)
        limit_cpu_str = format_cpu_k8s(eff_rec_cpu * 2.0, min_millicores=self.min_cpu_m * 2)
        limit_mem_str = format_memory_k8s(int(eff_rec_mem * 1.25), min_mib=self.min_mem_mi * 2)

        # Delta calculations
        cpu_delta = eff_rec_cpu - cur_cpu
        cpu_delta_pct = (cpu_delta / cur_cpu * 100.0) if cur_cpu > 0 else 0.0

        mem_delta = eff_rec_mem - cur_mem
        mem_delta_pct = (mem_delta / cur_mem * 100.0) if cur_mem > 0 else 0.0

        # Status categorization
        if cpu_delta_pct < -20.0 or mem_delta_pct < -20.0:
            status = "OVERPROVISIONED"
        elif cpu_delta_pct > 20.0 or mem_delta_pct > 20.0:
            status = "UNDERPROVISIONED"
        else:
            status = "BALANCED"

        # Savings estimation (positive if savings, negative if upsize cost)
        saved_cpu = max(0.0, cur_cpu - eff_rec_cpu)
        saved_mem_gib = max(0.0, (cur_mem - eff_rec_mem) / (1024 * 1024 * 1024))
        monthly_savings = (saved_cpu * COST_PER_VCPU_MONTH) + (saved_mem_gib * COST_PER_GIB_MONTH)

        risk_level = "LOW"
        if status == "OVERPROVISIONED" and (abs(cpu_delta_pct) > 80.0 or abs(mem_delta_pct) > 80.0):
            risk_level = "LOW (High Waste)"
        elif status == "UNDERPROVISIONED":
            risk_level = "HIGH (OOM/Throttling Risk)"

        # Spot VM Eligibility Evaluation
        # Stateless workloads (Deployments, Jobs, etc.) in non-system namespaces are candidates for Spot VMs
        is_system_ns = data.namespace_name in SYSTEM_NAMESPACES or data.namespace_name.endswith("-system")
        is_stateless = data.controller_kind in STATELESS_CONTROLLER_KINDS

        # Base monthly compute cost of the right-sized workload on standard on-demand compute
        rightsized_cpu_cost = eff_rec_cpu * COST_PER_VCPU_MONTH
        rightsized_mem_cost = (eff_rec_mem / (1024 * 1024 * 1024)) * COST_PER_GIB_MONTH
        rightsized_base_cost = rightsized_cpu_cost + rightsized_mem_cost

        if is_system_ns:
            spot_eligible = False
            spot_status = "NOT_RECOMMENDED (System Workload)"
            spot_reason = f"Workload runs in critical infrastructure namespace '{data.namespace_name}'. Preemption could disrupt cluster operations."
            spot_monthly_savings = 0.0
            spot_warning = ""
            spot_config = {}
        elif not is_stateless:
            spot_eligible = False
            spot_status = f"NOT_RECOMMENDED ({data.controller_kind})"
            spot_reason = f"Controller kind '{data.controller_kind}' may maintain local state or node coupling. Preemption risks data inconsistency."
            spot_monthly_savings = 0.0
            spot_warning = ""
            spot_config = {}
        else:
            spot_eligible = True
            spot_status = "ELIGIBLE"
            spot_reason = "Stateless workload in application namespace. Highly suitable for GKE Spot VMs with >=2 replicas and graceful shutdown."
            spot_monthly_savings = round(rightsized_base_cost * (SPOT_DISCOUNT_PERCENT / 100.0), 2)
            spot_warning = (
                "Preemption Warning: Spot VMs provide 60-91% savings but are interruptible with 30-second preemption notices. "
                "Ensure workload runs with >=2 replicas, terminationGracePeriodSeconds <= 25, and a PodDisruptionBudget."
            )
            spot_config = {
                "nodeSelector": {"cloud.google.com/gke-provisioning": "Spot"},
                "terminationGracePeriodSeconds": 25,
                "tolerations": [
                    {
                        "key": "cloud.google.com/gke-spot",
                        "operator": "Equal",
                        "value": "true",
                        "effect": "NoSchedule",
                    }
                ],
                "recommended_replicas": 2,
                "lifecycle": {
                    "preStop": {
                        "exec": {
                            "command": ["/bin/sh", "-c", "sleep 5"]
                        }
                    }
                },
            }

        return RightsizeRecommendation(
            cluster_name=data.cluster_name,
            location=data.location,
            namespace_name=data.namespace_name,
            controller_kind=data.controller_kind,
            controller_name=data.controller_name,
            container_name=data.container_name,
            current_cpu_cores=cur_cpu,
            current_cpu_str=cur_cpu_str,
            current_memory_bytes=cur_mem,
            current_memory_str=cur_mem_str,
            vpa_raw_cpu_cores=data.recommended_cpu_cores,
            vpa_raw_memory_bytes=data.recommended_memory_bytes,
            buffer_percent=buffer,
            recommended_cpu_cores=eff_rec_cpu,
            recommended_cpu_str=rec_cpu_str,
            recommended_memory_bytes=eff_rec_mem,
            recommended_memory_str=rec_mem_str,
            recommended_cpu_limit_str=limit_cpu_str,
            recommended_memory_limit_str=limit_mem_str,
            cpu_delta_cores=cpu_delta,
            cpu_delta_percent=cpu_delta_pct,
            memory_delta_bytes=mem_delta,
            memory_delta_percent=mem_delta_pct,
            status=status,
            monthly_savings_usd=monthly_savings,
            risk_level=risk_level,
            spot_eligible=spot_eligible,
            spot_status=spot_status,
            spot_reason=spot_reason,
            spot_monthly_savings_usd=spot_monthly_savings,
            spot_preemption_warning=spot_warning,
            spot_config=spot_config,
        )

    def generate_markdown_report(self, recommendations: List[RightsizeRecommendation]) -> str:
        """Formats recommendations into a GitHub-style markdown table with Spot VM evaluation."""
        if not recommendations:
            return "No workload recommendations found."

        total_savings = sum(r.monthly_savings_usd for r in recommendations)
        total_spot_savings = sum(r.spot_monthly_savings_usd for r in recommendations if r.spot_eligible)
        overprovisioned = sum(1 for r in recommendations if r.status == "OVERPROVISIONED")
        underprovisioned = sum(1 for r in recommendations if r.status == "UNDERPROVISIONED")
        spot_count = sum(1 for r in recommendations if r.spot_eligible)

        lines = [
            "# GKE Workload Rightsizing Report",
            f"**Total Workloads Analyzed:** {len(recommendations)} | "
            f"**Overprovisioned:** {overprovisioned} | "
            f"**Underprovisioned:** {underprovisioned} | "
            f"**Spot Eligible:** {spot_count} | "
            f"**Est. Rightsizing Savings:** ${total_savings:.2f}/mo | "
            f"**Potential Spot Savings:** +${total_spot_savings:.2f}/mo\n",
            "| Cluster | Namespace | Workload | Container | Current (CPU/Mem) | Recommended (CPU/Mem) | CPU Δ | Mem Δ | Status | Est. Savings/mo | Spot Eligible? |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        for r in recommendations:
            cur = f"{r.current_cpu_str} / {r.current_memory_str}"
            rec = f"{r.recommended_cpu_str} / {r.recommended_memory_str}"
            cpu_d = f"{r.cpu_delta_percent:+.0f}%"
            mem_d = f"{r.memory_delta_percent:+.0f}%"
            savings = f"${r.monthly_savings_usd:.2f}" if r.monthly_savings_usd > 0 else "-"
            if r.spot_eligible:
                spot_badge = f"✅ Yes (+${r.spot_monthly_savings_usd:.2f}/mo)"
            else:
                spot_badge = f"❌ No ({r.spot_status.split()[0]})"
            lines.append(
                f"| `{r.cluster_name}` | `{r.namespace_name}` | **{r.controller_name}** | `{r.container_name}` | "
                f"{cur} | **{rec}** | {cpu_d} | {mem_d} | `{r.status}` | {savings} | {spot_badge} |"
            )

        return "\n".join(lines)
