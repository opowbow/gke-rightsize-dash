"""Unit tests for manifest generation."""

import unittest
import yaml
from gke_rightsize.comparator import WorkloadComparator
from gke_rightsize.manifest_generator import ManifestGenerator
from gke_rightsize.metrics_collector import ContainerRightsizeData


class TestManifestGenerator(unittest.TestCase):

    def setUp(self):
        self.data = ContainerRightsizeData(
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
        self.comparator = WorkloadComparator()
        self.recommendation = self.comparator.analyze_workload(self.data)
        self.generator = ManifestGenerator(output_dir="/tmp/test_manifests")

    def test_generate_deployment_manifest(self):
        manifest_yaml = self.generator.generate_deployment_manifest(
            self.recommendation,
            image="nginx:alpine",
            replicas=3,
        )
        self.assertIn("apiVersion: apps/v1", manifest_yaml)
        self.assertIn("kind: Deployment", manifest_yaml)

        # Parse yaml (ignoring comment header)
        parsed = yaml.safe_load(manifest_yaml[manifest_yaml.find("apiVersion"):])
        self.assertEqual(parsed["metadata"]["name"], "ccc-demo-app")
        self.assertEqual(parsed["metadata"]["namespace"], "default")
        self.assertEqual(parsed["spec"]["replicas"], 3)
        container = parsed["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["name"], "demo-container")
        self.assertEqual(container["image"], "nginx:alpine")
        self.assertEqual(container["resources"]["requests"]["cpu"], "10m")
        self.assertEqual(container["resources"]["requests"]["memory"], "32Mi")
        self.assertIn("limits", container["resources"])

    def test_generate_patch_manifest(self):
        patch_yaml = self.generator.generate_patch_manifest(self.recommendation)
        self.assertIn("Strategic Merge Patch", patch_yaml)
        parsed = yaml.safe_load(patch_yaml[patch_yaml.find("spec:"):])
        container = parsed["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["resources"]["requests"]["cpu"], "10m")
        self.assertEqual(container["resources"]["requests"]["memory"], "32Mi")


    def test_generate_spot_deployment_manifest(self):
        manifest_yaml = self.generator.generate_deployment_manifest(
            self.recommendation,
            image="nginx:alpine",
            replicas=1,
            include_spot=True,
        )
        self.assertIn("cloud.google.com/gke-provisioning: Spot", manifest_yaml)
        self.assertIn("terminationGracePeriodSeconds: 25", manifest_yaml)
        parsed = yaml.safe_load(manifest_yaml[manifest_yaml.find("apiVersion"):])
        # Replicas bumped to 2 for Spot HA best practice
        self.assertGreaterEqual(parsed["spec"]["replicas"], 2)
        self.assertEqual(parsed["spec"]["template"]["spec"]["nodeSelector"]["cloud.google.com/gke-provisioning"], "Spot")
        self.assertEqual(parsed["spec"]["template"]["spec"]["terminationGracePeriodSeconds"], 25)

    def test_generate_spot_patch_manifest(self):
        patch_yaml = self.generator.generate_patch_manifest(self.recommendation, include_spot=True)
        self.assertIn("cloud.google.com/gke-provisioning: Spot", patch_yaml)
        self.assertIn("terminationGracePeriodSeconds: 25", patch_yaml)
        parsed = yaml.safe_load(patch_yaml[patch_yaml.find("spec:"):])
        self.assertEqual(parsed["spec"]["template"]["spec"]["nodeSelector"]["cloud.google.com/gke-provisioning"], "Spot")
        self.assertEqual(parsed["spec"]["template"]["spec"]["terminationGracePeriodSeconds"], 25)


if __name__ == "__main__":
    unittest.main()
