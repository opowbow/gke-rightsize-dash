"""Unit tests for workload comparator and resource formatting."""

import unittest
from gke_rightsize.comparator import (
    format_cpu_k8s,
    format_memory_k8s,
    WorkloadComparator,
    RightsizeRecommendation,
)
from gke_rightsize.metrics_collector import ContainerRightsizeData


class TestComparator(unittest.TestCase):

    def test_format_cpu_k8s(self):
        self.assertEqual(format_cpu_k8s(0.001, min_millicores=10), "10m")
        self.assertEqual(format_cpu_k8s(0.05), "50m")
        self.assertEqual(format_cpu_k8s(0.25), "250m")
        self.assertEqual(format_cpu_k8s(1.0), "1")
        self.assertEqual(format_cpu_k8s(2.5), "2.5")

    def test_format_memory_k8s(self):
        self.assertEqual(format_memory_k8s(4 * 1024 * 1024, min_mib=32), "32Mi")
        self.assertEqual(format_memory_k8s(256 * 1024 * 1024), "256Mi")
        self.assertEqual(format_memory_k8s(1024 * 1024 * 1024), "1Gi")
        self.assertEqual(format_memory_k8s(2048 * 1024 * 1024), "2Gi")

    def test_overprovisioned_workload_analysis(self):
        # Example like ccc-demo-app: 600m CPU -> 1m VPA, 1900Mi -> 4Mi VPA
        data = ContainerRightsizeData(
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
        )

        comparator = WorkloadComparator(default_buffer_percent=15.0)
        rec = comparator.analyze_workload(data)

        self.assertEqual(rec.status, "OVERPROVISIONED")
        self.assertTrue(rec.cpu_delta_percent < -90.0)
        self.assertTrue(rec.memory_delta_percent < -90.0)
        self.assertEqual(rec.recommended_cpu_str, "10m")
        self.assertEqual(rec.recommended_memory_str, "32Mi")
        self.assertGreater(rec.monthly_savings_usd, 0.0)

    def test_underprovisioned_workload_analysis(self):
        # Example like gmp-operator: 1m CPU -> 5m VPA, 15Mi -> 30Mi VPA
        data = ContainerRightsizeData(
            cluster_name="gke-ops",
            location="europe-north1",
            namespace_name="gmp-system",
            controller_kind="Deployment",
            controller_name="gmp-operator",
            container_name="operator",
            recommended_cpu_cores=0.005,
            recommended_memory_bytes=30 * 1024 * 1024,
            current_request_cpu_cores=0.001,
            current_request_memory_bytes=15 * 1024 * 1024,
        )

        comparator = WorkloadComparator(default_buffer_percent=15.0)
        rec = comparator.analyze_workload(data)

        self.assertEqual(rec.status, "UNDERPROVISIONED")
        self.assertIn("OOM/Throttling Risk", rec.risk_level)

    def test_markdown_report_generation(self):
        data = ContainerRightsizeData(
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
        )
        comparator = WorkloadComparator()
        rec = comparator.analyze_workload(data)
        report = comparator.generate_markdown_report([rec])

        self.assertIn("# GKE Workload Rightsizing Report", report)
        self.assertIn("ccc-demo-app", report)
        self.assertIn("OVERPROVISIONED", report)


    def test_spot_vm_eligibility(self):
        # Stateless deployment in user namespace should be eligible for Spot
        data = ContainerRightsizeData(
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
        )
        comparator = WorkloadComparator()
        rec = comparator.analyze_workload(data)

        self.assertTrue(rec.spot_eligible)
        self.assertEqual(rec.spot_status, "ELIGIBLE")
        self.assertGreater(rec.spot_monthly_savings_usd, 0.0)
        self.assertIn("cloud.google.com/gke-provisioning", rec.spot_config["nodeSelector"])
        self.assertEqual(rec.spot_config["terminationGracePeriodSeconds"], 25)

    def test_spot_vm_system_namespace_exclusion(self):
        # Workloads in gmp-system should NOT be eligible for Spot
        data = ContainerRightsizeData(
            cluster_name="gke-ops",
            location="europe-north1",
            namespace_name="gmp-system",
            controller_kind="Deployment",
            controller_name="gmp-operator",
            container_name="operator",
            recommended_cpu_cores=0.005,
            recommended_memory_bytes=30 * 1024 * 1024,
            current_request_cpu_cores=0.001,
            current_request_memory_bytes=15 * 1024 * 1024,
        )
        comparator = WorkloadComparator()
        rec = comparator.analyze_workload(data)

        self.assertFalse(rec.spot_eligible)
        self.assertIn("NOT_RECOMMENDED (System Workload)", rec.spot_status)
        self.assertEqual(rec.spot_monthly_savings_usd, 0.0)

    def test_spot_vm_stateful_exclusion(self):
        # StatefulSets should NOT be eligible for Spot
        data = ContainerRightsizeData(
            cluster_name="cluster-a",
            location="us-central1",
            namespace_name="default",
            controller_kind="StatefulSet",
            controller_name="postgres-db",
            container_name="postgres",
            recommended_cpu_cores=0.1,
            recommended_memory_bytes=512 * 1024 * 1024,
            current_request_cpu_cores=0.5,
            current_request_memory_bytes=1024 * 1024 * 1024,
        )
        comparator = WorkloadComparator()
        rec = comparator.analyze_workload(data)

        self.assertFalse(rec.spot_eligible)
        self.assertIn("NOT_RECOMMENDED (StatefulSet)", rec.spot_status)


if __name__ == "__main__":
    unittest.main()
