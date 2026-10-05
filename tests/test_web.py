"""Unit and integration tests for the GKE Rightsizing Web Dashboard package."""

import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
from gke_rightsize.metrics_collector import ContainerRightsizeData, GKEMetricsCollector
from gke_rightsize.web.app import create_app


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


class TestWebDashboard(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.patcher_init = patch.object(GKEMetricsCollector, "__init__", return_value=None)
        cls.patcher_gather = patch.object(
            GKEMetricsCollector,
            "gather_workload_metrics",
            side_effect=_get_mock_workloads,
        )
        cls.patcher_kubectl = patch("gke_rightsize.tools._get_existing_deployment_spec", return_value=None)
        cls.patcher_init.start()
        cls.patcher_gather.start()
        cls.patcher_kubectl.start()
        GKEMetricsCollector.project_id = "test-project"

        cls.app = create_app()
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.patcher_kubectl.stop()
        cls.patcher_gather.stop()
        cls.patcher_init.stop()

    def test_get_index_html(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("GKE Rightsizing & FinOps Platform", response.text)
        self.assertIn("Spot VM Golden Path Advisory", response.text)

    def test_get_static_css(self):
        response = self.client.get("/static/css/style.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response.headers["content-type"])
        self.assertIn(".kpi-card", response.text)

    def test_get_static_js(self):
        response = self.client.get("/static/js/dashboard.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/javascript", response.headers["content-type"])
        self.assertIn("loadWorkloads", response.text)

    def test_api_clusters(self):
        response = self.client.get("/api/clusters")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("cluster-b", data["clusters"])

    def test_api_overview(self):
        response = self.client.get("/api/overview?buffer_percent=15.0")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["total_workloads"], 1)
        self.assertGreaterEqual(data["spot_eligible_count"], 1)
        self.assertGreater(data["monthly_ondemand_savings_usd"], 0)
        self.assertGreater(data["monthly_spot_savings_usd"], 0)

    def test_api_workloads(self):
        response = self.client.get("/api/workloads")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(len(data["workloads"]), 1)
        
        # Verify spot fields are present on each workload
        first_w = data["workloads"][0]
        self.assertIn("spot_eligible", first_w)
        self.assertIn("spot_status", first_w)
        self.assertIn("spot_monthly_savings_usd", first_w)

    def test_api_workloads_spot_filter(self):
        # Query only Spot eligible workloads
        response = self.client.get("/api/workloads?spot_eligible=true")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for w in data["workloads"]:
            self.assertTrue(w["spot_eligible"])

    def test_api_manifest_deployment_with_spot(self):
        response = self.client.get(
            "/api/manifest?workload_name=ccc-demo-app&namespace=default&manifest_type=deployment&include_spot=true"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("cloud.google.com/gke-provisioning: Spot", data["yaml_content"])
        self.assertIn("terminationGracePeriodSeconds: 25", data["yaml_content"])
        self.assertTrue(data["spot_evaluation"]["spot_config_included"])

    def test_api_manifest_patch_with_spot(self):
        response = self.client.get(
            "/api/manifest?workload_name=ccc-demo-app&namespace=default&manifest_type=patch&include_spot=true"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("cloud.google.com/gke-provisioning: Spot", data["yaml_content"])

    def test_api_spot_advisory(self):
        response = self.client.get("/api/spot-advisory")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["total_spot_eligible"], 1)
        self.assertIn("golden_path_guidance", data)

    def test_api_chat(self):
        response = self.client.post("/api/chat", json={"message": "Which workloads can run on Spot VMs?"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("Spot", data["response"])
        self.assertIn("ccc-demo-app", data["response"])


if __name__ == "__main__":
    unittest.main()
