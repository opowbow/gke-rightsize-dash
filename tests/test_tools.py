"""Unit tests for ADK tools."""

import unittest
from unittest.mock import patch
from gke_rightsize.metrics_collector import ContainerRightsizeData, GKEMetricsCollector
from gke_rightsize.tools import (
    list_clusters,
    gather_vpa_metrics,
    compare_workloads,
    generate_rightsized_manifest,
    evaluate_spot_workloads,
)


def _get_mock_workloads(*args, **kwargs):
    cluster_name = kwargs.get("cluster_name")
    namespace = kwargs.get("namespace")
    workloads = [
        ContainerRightsizeData(
            cluster_name="cluster-b",
            location="us-central1",
            namespace_name="default",
            controller_kind="Deployment",
            controller_name="ccc-demo-app",
            container_name="demo-container",
            recommended_cpu_cores=0.001,
            recommended_memory_bytes=4 * 1024 * 1024,
            current_request_cpu_cores=0.6,
            current_request_memory_bytes=1992294400,
            current_limit_cpu_cores=1.2,
            current_limit_memory_bytes=3984588800,
        ),
        ContainerRightsizeData(
            cluster_name="cluster-b",
            location="us-central1",
            namespace_name="gmp-system",
            controller_kind="Deployment",
            controller_name="gmp-operator",
            container_name="operator",
            recommended_cpu_cores=0.005,
            recommended_memory_bytes=30 * 1024 * 1024,
            current_request_cpu_cores=0.001,
            current_request_memory_bytes=15 * 1024 * 1024,
            current_limit_cpu_cores=0.01,
            current_limit_memory_bytes=30 * 1024 * 1024,
        ),
    ]
    if cluster_name:
        workloads = [w for w in workloads if w.cluster_name == cluster_name]
    if namespace:
        workloads = [w for w in workloads if w.namespace_name == namespace]
    return workloads


class TestTools(unittest.TestCase):

    def setUp(self):
        self.patcher_init = patch.object(GKEMetricsCollector, "__init__", return_value=None)
        self.patcher_gather = patch.object(
            GKEMetricsCollector,
            "gather_workload_metrics",
            side_effect=_get_mock_workloads,
        )
        self.patcher_kubectl = patch("gke_rightsize.tools._get_existing_deployment_spec", return_value=None)
        self.patcher_init.start()
        self.patcher_gather.start()
        self.patcher_kubectl.start()
        GKEMetricsCollector.project_id = "test-project"

    def tearDown(self):
        self.patcher_kubectl.stop()
        self.patcher_gather.stop()
        self.patcher_init.stop()

    def test_list_clusters(self):
        res = list_clusters()
        self.assertEqual(res["status"], "success")
        self.assertGreater(len(res["clusters"]), 0)
        self.assertIn("cluster-b", res["clusters"])

    def test_gather_vpa_metrics(self):
        res = gather_vpa_metrics(cluster_name="cluster-b")
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["total_workloads_found"], 0)

    def test_compare_workloads(self):
        res = compare_workloads(cluster_name="cluster-b", workload_name="ccc-demo-app")
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(res["recommendations"]), 1)
        rec = res["recommendations"][0]
        self.assertEqual(rec["controller_name"], "ccc-demo-app")
        self.assertEqual(rec["status"], "OVERPROVISIONED")

    def test_generate_rightsized_manifest(self):
        res = generate_rightsized_manifest(workload_name="ccc-demo-app", namespace="default", include_spot=True)
        self.assertEqual(res["status"], "success")
        self.assertIn("apiVersion: apps/v1", res["yaml_content"])
        self.assertEqual(res["recommended_resources"]["cpu"], "10m")
        self.assertEqual(res["recommended_resources"]["memory"], "32Mi")
        self.assertTrue(res["spot_evaluation"]["eligible"])
        self.assertIn("cloud.google.com/gke-provisioning: Spot", res["yaml_content"])

    def test_evaluate_spot_workloads(self):
        res = evaluate_spot_workloads(cluster_name="cluster-b")
        self.assertEqual(res["status"], "success")
        self.assertGreaterEqual(res["total_evaluated"], 1)
        self.assertIn("golden_path_guidance", res)
        # ccc-demo-app in default namespace should be among eligible workloads
        eligible_names = [w["controller_name"] for w in res["spot_eligible_workloads"]]
        self.assertIn("ccc-demo-app", eligible_names)


if __name__ == "__main__":
    unittest.main()
