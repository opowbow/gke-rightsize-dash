"""Metrics Collector for GKE VPA recommendations and container resource requests.

Queries the Google Cloud Monitoring API directly to gather:
- VPA recommended CPU cores per replica
- VPA recommended Memory bytes per replica
- Current container CPU and Memory requests and limits
- Discovered clusters, namespaces, and workloads
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from google.cloud import monitoring_v3

from .auth_helper import get_google_credentials

logger = logging.getLogger(__name__)


@dataclass
class ContainerRightsizeData:
    cluster_name: str
    location: str
    namespace_name: str
    controller_kind: str
    controller_name: str
    container_name: str
    recommended_cpu_cores: float
    recommended_memory_bytes: int
    current_request_cpu_cores: Optional[float] = None
    current_request_memory_bytes: Optional[int] = None
    current_limit_cpu_cores: Optional[float] = None
    current_limit_memory_bytes: Optional[int] = None
    matched_pod_name: Optional[str] = None
    sample_time: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "cluster_name": self.cluster_name,
            "location": self.location,
            "namespace_name": self.namespace_name,
            "controller_kind": self.controller_kind,
            "controller_name": self.controller_name,
            "container_name": self.container_name,
            "recommended_cpu_cores": self.recommended_cpu_cores,
            "recommended_memory_bytes": self.recommended_memory_bytes,
            "current_request_cpu_cores": self.current_request_cpu_cores,
            "current_request_memory_bytes": self.current_request_memory_bytes,
            "current_limit_cpu_cores": self.current_limit_cpu_cores,
            "current_limit_memory_bytes": self.current_limit_memory_bytes,
            "matched_pod_name": self.matched_pod_name,
        }


class GKEMetricsCollector:
    """Collects VPA recommendations and resource metrics from Google Cloud Monitoring."""

    VPA_CPU_METRIC = "kubernetes.io/autoscaler/container/cpu/per_replica_recommended_request_cores"
    VPA_MEM_METRIC = "kubernetes.io/autoscaler/container/memory/per_replica_recommended_request_bytes"
    REQUEST_CPU_METRIC = "kubernetes.io/container/cpu/request_cores"
    REQUEST_MEM_METRIC = "kubernetes.io/container/memory/request_bytes"
    LIMIT_CPU_METRIC = "kubernetes.io/container/cpu/limit_cores"
    LIMIT_MEM_METRIC = "kubernetes.io/container/memory/limit_bytes"

    _cache: Dict[str, Tuple[float, List[ContainerRightsizeData]]] = {}
    _cache_ttl = 300  # 5 minutes

    def __init__(self, project_id: Optional[str] = None):
        self.creds, detected_project = get_google_credentials()
        self.project_id = project_id or detected_project
        self.project_name = f"projects/{self.project_id}"
        self.client = monitoring_v3.MetricServiceClient(credentials=self.creds)

    def list_clusters(self) -> List[str]:
        """Discovers active GKE clusters with VPA or container metrics."""
        workloads = self.gather_workload_metrics()
        clusters = sorted(list({w.cluster_name for w in workloads}))
        return clusters

    def gather_workload_metrics(
        self,
        cluster_name: Optional[str] = None,
        namespace: Optional[str] = None,
        lookback_seconds: int = 7200,  # 2 hours
        force_refresh: bool = False,
    ) -> List[ContainerRightsizeData]:
        """Gathers VPA recommendations and matches them with current container requests."""
        cache_key = f"{cluster_name or '*'}:{namespace or '*'}"
        now = time.time()

        if not force_refresh:
            if cache_key in self._cache:
                cache_time, data = self._cache[cache_key]
                if now - cache_time < self._cache_ttl:
                    return data
            all_key = "*:*"
            if all_key in self._cache:
                cache_time, all_data = self._cache[all_key]
                if now - cache_time < self._cache_ttl:
                    filtered = [
                        w for w in all_data
                        if (not cluster_name or w.cluster_name == cluster_name)
                        and (not namespace or w.namespace_name == namespace)
                    ]
                    return filtered

        interval = monitoring_v3.TimeInterval({
            "end_time": {"seconds": int(now)},
            "start_time": {"seconds": int(now - lookback_seconds)},
        })

        # Step 1: Collect VPA recommended CPU
        vpa_cpu_map: Dict[Tuple[str, str, str, str], Tuple[float, str, str]] = {}
        # Key: (cluster, namespace, controller_name, container_name) -> (value, location, controller_kind)
        try:
            results = self.client.list_time_series(request={
                "name": self.project_name,
                "filter": f'metric.type = "{self.VPA_CPU_METRIC}"',
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            })
            for ts in results:
                c_name = ts.resource.labels.get("cluster_name", "")
                ns_name = ts.resource.labels.get("namespace_name", "")
                ctrl_name = ts.resource.labels.get("controller_name", "")
                ctrl_kind = ts.resource.labels.get("controller_kind", "Deployment")
                loc = ts.resource.labels.get("location", "")
                cont_name = ts.metric.labels.get("container_name", "")

                if cluster_name and c_name != cluster_name:
                    continue
                if namespace and ns_name != namespace:
                    continue

                if ts.points:
                    val = ts.points[0].value.double_value
                    key = (c_name, ns_name, ctrl_name, cont_name)
                    vpa_cpu_map[key] = (val, loc, ctrl_kind)
        except Exception as e:
            logger.error(f"Error querying VPA CPU metrics: {e}")

        # Step 2: Collect VPA recommended Memory
        vpa_mem_map: Dict[Tuple[str, str, str, str], int] = {}
        try:
            results = self.client.list_time_series(request={
                "name": self.project_name,
                "filter": f'metric.type = "{self.VPA_MEM_METRIC}"',
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            })
            for ts in results:
                c_name = ts.resource.labels.get("cluster_name", "")
                ns_name = ts.resource.labels.get("namespace_name", "")
                ctrl_name = ts.resource.labels.get("controller_name", "")
                cont_name = ts.metric.labels.get("container_name", "")

                if cluster_name and c_name != cluster_name:
                    continue
                if namespace and ns_name != namespace:
                    continue

                if ts.points:
                    val = ts.points[0].value.int64_value
                    key = (c_name, ns_name, ctrl_name, cont_name)
                    vpa_mem_map[key] = val
        except Exception as e:
            logger.error(f"Error querying VPA Memory metrics: {e}")

        # Step 3: Collect current container requests and limits
        current_requests_cpu: Dict[Tuple[str, str, str, str], Tuple[float, str]] = {}
        current_requests_mem: Dict[Tuple[str, str, str, str], Tuple[int, str]] = {}
        current_limits_cpu: Dict[Tuple[str, str, str, str], float] = {}
        current_limits_mem: Dict[Tuple[str, str, str, str], int] = {}

        # Fetch CPU requests
        try:
            req_results = self.client.list_time_series(request={
                "name": self.project_name,
                "filter": f'metric.type = "{self.REQUEST_CPU_METRIC}"',
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            })
            for ts in req_results:
                c_name = ts.resource.labels.get("cluster_name", "")
                ns_name = ts.resource.labels.get("namespace_name", "")
                pod_name = ts.resource.labels.get("pod_name", "")
                cont_name = ts.resource.labels.get("container_name", "")
                if ts.points:
                    val = ts.points[0].value.double_value
                    # Match pod to controller
                    for (v_cluster, v_ns, v_ctrl, v_cont) in vpa_cpu_map.keys():
                        if c_name == v_cluster and ns_name == v_ns and cont_name == v_cont:
                            if pod_name.startswith(v_ctrl + "-") or pod_name == v_ctrl:
                                current_requests_cpu[(v_cluster, v_ns, v_ctrl, v_cont)] = (val, pod_name)
        except Exception as e:
            logger.error(f"Error querying container CPU requests: {e}")

        # Fetch Memory requests
        try:
            mem_results = self.client.list_time_series(request={
                "name": self.project_name,
                "filter": f'metric.type = "{self.REQUEST_MEM_METRIC}"',
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            })
            for ts in mem_results:
                c_name = ts.resource.labels.get("cluster_name", "")
                ns_name = ts.resource.labels.get("namespace_name", "")
                pod_name = ts.resource.labels.get("pod_name", "")
                cont_name = ts.resource.labels.get("container_name", "")
                if ts.points:
                    val = ts.points[0].value.int64_value
                    for (v_cluster, v_ns, v_ctrl, v_cont) in vpa_cpu_map.keys():
                        if c_name == v_cluster and ns_name == v_ns and cont_name == v_cont:
                            if pod_name.startswith(v_ctrl + "-") or pod_name == v_ctrl:
                                current_requests_mem[(v_cluster, v_ns, v_ctrl, v_cont)] = (val, pod_name)
        except Exception as e:
            logger.error(f"Error querying container Memory requests: {e}")

        # Fetch CPU limits
        try:
            lim_cpu_results = self.client.list_time_series(request={
                "name": self.project_name,
                "filter": f'metric.type = "{self.LIMIT_CPU_METRIC}"',
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            })
            for ts in lim_cpu_results:
                c_name = ts.resource.labels.get("cluster_name", "")
                ns_name = ts.resource.labels.get("namespace_name", "")
                pod_name = ts.resource.labels.get("pod_name", "")
                cont_name = ts.resource.labels.get("container_name", "")
                if ts.points:
                    val = ts.points[0].value.double_value
                    for (v_cluster, v_ns, v_ctrl, v_cont) in vpa_cpu_map.keys():
                        if c_name == v_cluster and ns_name == v_ns and cont_name == v_cont:
                            if pod_name.startswith(v_ctrl + "-") or pod_name == v_ctrl:
                                current_limits_cpu[(v_cluster, v_ns, v_ctrl, v_cont)] = val
        except Exception as e:
            logger.error(f"Error querying container CPU limits: {e}")

        # Fetch Memory limits
        try:
            lim_mem_results = self.client.list_time_series(request={
                "name": self.project_name,
                "filter": f'metric.type = "{self.LIMIT_MEM_METRIC}"',
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            })
            for ts in lim_mem_results:
                c_name = ts.resource.labels.get("cluster_name", "")
                ns_name = ts.resource.labels.get("namespace_name", "")
                pod_name = ts.resource.labels.get("pod_name", "")
                cont_name = ts.resource.labels.get("container_name", "")
                if ts.points:
                    val = ts.points[0].value.int64_value
                    for (v_cluster, v_ns, v_ctrl, v_cont) in vpa_cpu_map.keys():
                        if c_name == v_cluster and ns_name == v_ns and cont_name == v_cont:
                            if pod_name.startswith(v_ctrl + "-") or pod_name == v_ctrl:
                                current_limits_mem[(v_cluster, v_ns, v_ctrl, v_cont)] = val
        except Exception as e:
            logger.error(f"Error querying container Memory limits: {e}")

        # Combine into ContainerRightsizeData objects
        all_keys = set(vpa_cpu_map.keys()) | set(vpa_mem_map.keys())
        workloads: List[ContainerRightsizeData] = []

        for key in sorted(all_keys):
            c_name, ns_name, ctrl_name, cont_name = key
            cpu_val, loc, ctrl_kind = vpa_cpu_map.get(key, (0.01, "unknown", "Deployment"))
            mem_val = vpa_mem_map.get(key, 32 * 1024 * 1024)

            cur_cpu_info = current_requests_cpu.get(key)
            cur_cpu = cur_cpu_info[0] if cur_cpu_info else None
            matched_pod = cur_cpu_info[1] if cur_cpu_info else None

            cur_mem_info = current_requests_mem.get(key)
            cur_mem = cur_mem_info[0] if cur_mem_info else None
            if not matched_pod and cur_mem_info:
                matched_pod = cur_mem_info[1]

            lim_cpu = current_limits_cpu.get(key)
            lim_mem = current_limits_mem.get(key)

            data_obj = ContainerRightsizeData(
                cluster_name=c_name,
                location=loc,
                namespace_name=ns_name,
                controller_kind=ctrl_kind,
                controller_name=ctrl_name,
                container_name=cont_name,
                recommended_cpu_cores=cpu_val,
                recommended_memory_bytes=mem_val,
                current_request_cpu_cores=cur_cpu,
                current_request_memory_bytes=cur_mem,
                current_limit_cpu_cores=lim_cpu,
                current_limit_memory_bytes=lim_mem,
                matched_pod_name=matched_pod,
            )
            workloads.append(data_obj)

        self._cache[cache_key] = (now, workloads)
        return workloads
