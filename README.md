# GKE Workload Rightsizing Agent (Google ADK)

An autonomous FinOps and Platform Engineering AI Agent built with **Google ADK (Agent Development Kit)** and **Gemini 2.5 on Vertex AI**.

The agent inspects **Vertical Pod Autoscaler (VPA)** recommendations across GKE clusters directly from **Google Cloud Monitoring metrics**, compares them against current CPU and memory requests/limits, identifies cost-saving or throttling risks, and outputs clean, production-ready Kubernetes YAML manifests with safety headroom.

---

## Architecture Overview

```
                                      +-------------------------------+
                                      |   Google Cloud Project        |
                                      |   (your-google-cloud-project) |
                                      +---------------+---------------+
                                                      |
                                    Google Cloud Monitoring API
                                    (Metrics & VPA Recommendations)
                                                      |
                                                      v
+------------------------+             +-------------------------------+
|  Platform Engineer     |  Prompt /   |   Google ADK Agent            |
|  (CLI / Interactive /  | <---------> |   (Gemini 2.5 Flash           |
|   ADK Web UI)          |             |    + Function Tools)          |
+------------------------+             +---------------+---------------+
                                                      |
                                                      v
                                       +-------------------------------+
                                       |  GKE Rightsizing Tools        |
                                       |  - Metrics Collector          |
                                       |  - Workload Comparator        |
                                       |  - Manifest Generator         |
                                       +---------------+---------------+
                                                      |
                                                      v
                                       +-------------------------------+
                                       |  Clean YAML Manifests         |
                                       |  - Deployments / StatefulSets |
                                       |  - Strategic Merge Patches    |
                                       +-------------------------------+
```

### Key Highlights
- **No external `kubernetes` Python package needed**: Queries live cluster VPA recommendations and resource allocations directly via Google Cloud Monitoring API metrics:
  - `kubernetes.io/autoscaler/container/cpu/per_replica_recommended_request_cores`
  - `kubernetes.io/autoscaler/container/memory/per_replica_recommended_request_bytes`
  - `kubernetes.io/container/cpu/request_cores` & `limit_cores`
  - `kubernetes.io/container/memory/request_bytes` & `limit_bytes`
- **GKE Spot VM Feasibility & Golden Path Advisory**:
  - Automatically flags stateless application workloads (e.g., Deployments in user namespaces) eligible for GKE Spot VMs, unlocking **60% to 91% cost savings** on compute.
  - Excludes system namespaces (`kube-system`, `gmp-system`) and stateful workloads (`StatefulSet`, `DaemonSet`).
  - Automatically injects GKE best-practice Spot configurations:
    - `nodeSelector: {"cloud.google.com/gke-provisioning": "Spot"}`
    - `terminationGracePeriodSeconds: 25` (strictly under 30s preemption window)
    - Spot tolerations
    - `preStop: sleep 5` hook for connection draining
    - Replicas &ge; 2 for high availability during node preemptions.
- **Interactive Local Web Dashboard Package (`gke_rightsize.web`)**:
  - Modern FastAPI Single-Page Application (SPA).
  - Executive FinOps KPI summary cards (monthly on-demand savings, Spot VM potential, annual run-rate).
  - Real-time safety buffer headroom slider (5% to 50%) with dynamic metric recalculation.
  - Interactive workloads data table with cluster, namespace, status, and Spot VM filtering.
  - YAML manifest & patch modal with on-the-fly Spot VM configuration toggling, copy, and download.
  - Embedded AI assistant chat powered by Google ADK & Gemini 2.5.
- **Production Best Practices**:
  - Configurable safety headroom buffer (default: **15%**)
  - Minimum scheduling safety floors: **10m CPU** and **32Mi Memory** to prevent pod startup throttling or immediate OOMKills.
- **Clean YAML Output**: Generates valid Kubernetes manifests and strategic merge patches annotated with change tracking metadata and estimated monthly savings.

---

## Prerequisites

1. **Python 3.10+**
2. **Google Cloud CLI (`gcloud`)** installed and authenticated:
   ```bash
   gcloud auth application-default login
   gcloud config set project <YOUR_PROJECT_ID>
   ```
3. **IAM Permissions** on the Google Cloud project:
   - `roles/monitoring.viewer` or `roles/monitoring.metricReader` (to query Cloud Monitoring VPA metrics)
   - `roles/aiplatform.user` (to query Gemini on Vertex AI)
4. **Target GKE Clusters**:
   - GKE Autopilot (VPA enabled by default), OR
   - GKE Standard with Vertical Pod Autoscaler enabled:
     ```bash
     gcloud container clusters update <CLUSTER_NAME> --enable-vertical-pod-autoscaling
     ```

---

## Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/<your-username>/gke-rightsize-dash.git
   cd gke-rightsize-dash
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   # Or install in editable mode:
   pip install -e .
   ```

4. **Configure environment variables**:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` to set your project:
   ```bash
   GOOGLE_CLOUD_PROJECT=your-google-cloud-project-id
   GOOGLE_CLOUD_LOCATION=us-central1
   GOOGLE_GENAI_USE_VERTEXAI=1
   MODEL_NAME=gemini-2.5-flash
   ```

---

## Workspace Structure

```
gke-rightsize-dash/
├── gke_rightsize/
│   ├── __init__.py              # Exports root_agent
│   ├── agent.py                 # Google ADK Agent configuration & system instructions
│   ├── auth_helper.py           # Seamless ADC and Compute Engine credential resolution
│   ├── metrics_collector.py     # Google Cloud Monitoring VPA metric queries & caching
│   ├── comparator.py            # Resource diff, delta %, Spot VM advisor & cost savings engine
│   ├── manifest_generator.py    # Production Kubernetes YAML & patch generator (Spot-aware)
│   ├── tools.py                 # ADK FunctionTools for Gemini (including evaluate_spot_workloads)
│   └── web/                     # Interactive Web Dashboard Package
│       ├── __init__.py          # Exports create_app and run_dashboard
│       ├── __main__.py          # Module entrypoint: `python3 -m gke_rightsize.web`
│       ├── app.py               # FastAPI application & REST endpoints
│       ├── static/              # CSS & JS assets
│       │   ├── css/style.css
│       │   └── js/dashboard.js
│       └── templates/
│           └── index.html       # Modern single-page interactive UI
├── tests/
│   ├── test_comparator.py       # Unit tests for resource calculation, formatting, and Spot logic
│   ├── test_manifest_generator.py # Manifest generation and Spot YAML validity tests
│   ├── test_tools.py            # Unit tests for ADK tools (mock-resilient, offline)
│   └── test_web.py              # Test suite for FastAPI endpoints & static assets
├── manifests/                   # Sample right-sized YAML manifests & patches
├── agent.py                     # Root ADK entrypoint (`root_agent`)
├── __init__.py                  # Root package exports
├── main.py                      # CLI runner (prompt, interactive, batch, and web dashboard)
├── requirements.txt             # Required Python dependencies
├── pyproject.toml               # PEP 517/518 build and packaging configuration
├── .gitignore                   # Git exclusion rules for bytecode, secrets, and caches
├── .env.example                 # Template for environment configuration
├── LICENSE                      # Apache 2.0 open-source license
└── README.md                    # Project documentation
```

---

## Quickstart

### 1. Launch the Interactive Web Dashboard
Run the dashboard locally on port 8080 (or any custom port):
```bash
python3 main.py --web --port 8080
```
Or run as a Python package:
```bash
python3 -m gke_rightsize.web --port 8080
```
Then open `http://localhost:8080` in your browser.

### 2. Run Automated Rightsizing Across All Workloads
To run the automated analysis on all clusters in the Google Cloud project and generate right-sized manifests (add `--spot` to include Spot VM configurations for eligible workloads):
```bash
python3 main.py --run-all --spot
```

Output:
```markdown
# GKE Workload Rightsizing Report
Total Workloads Analyzed: 5 | Overprovisioned: 2 | Underprovisioned: 3 | Spot Eligible: 2 | Est. Rightsizing Savings: $32.94/mo | Potential Spot Savings: +$2.19/mo

| Cluster | Namespace | Workload | Container | Current (CPU/Mem) | Recommended (CPU/Mem) | CPU Δ | Mem Δ | Status | Est. Savings/mo | Spot Eligible? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `cluster-a` | `gmp-system` | **gmp-operator** | `operator` | 1m / 15Mi | **10m / 32Mi** | +900% | +110% | `UNDERPROVISIONED` | - | ❌ No (NOT_RECOMMENDED) |
| `cluster-b` | `default` | **ccc-demo-app** | `demo-container` | 600m / 1900Mi | **10m / 32Mi** | -98% | -98% | `OVERPROVISIONED` | $25.26 | ✅ Yes (+$0.28/mo) |
| `cluster-b` | `gmp-system` | **gmp-operator** | `operator` | 1m / 15Mi | **10m / 32Mi** | +900% | +110% | `UNDERPROVISIONED` | - | ❌ No (NOT_RECOMMENDED) |
| `gke-ops` | `default` | **vpa-rightsizer-agent** | `agent` | 250m / 512Mi | **10m / 766Mi** | -96% | +50% | `OVERPROVISIONED` | $7.68 | ✅ Yes (+$1.91/mo) |
| `gke-ops` | `gmp-system` | **gmp-operator** | `operator` | 1m / 15Mi | **10m / 41Mi** | +900% | +169% | `UNDERPROVISIONED` | - | ❌ No (NOT_RECOMMENDED) |
```

### 3. Run a One-Shot Query with the ADK Agent
```bash
python3 main.py "Inspect VPA recommendations for ccc-demo-app in cluster-b, compare them with current CPU and memory requests, and output clean YAML manifests with the recommended sizes."
```

### 4. Interactive Mode
```bash
python3 main.py -i
```

### 5. Run with the Google ADK CLI
The project is fully compatible with `adk`:
```bash
adk run .
```

---

## Generated Manifest Example

`manifests/cluster-b_default_ccc-demo-app-rightsized.yaml`:
```yaml
# ====================================================================
# Right-sized Manifest for ccc-demo-app (default)
# Cluster: cluster-b | Location: us-central1
# Status: OVERPROVISIONED | Safety Buffer: 15.0%
# CPU Change: 600m -> 10m (-98.3%)
# Memory Change: 1900Mi -> 32Mi (-98.3%)
# Spot VM Configuration: ENABLED (GKE Provisioning: Spot)
# Estimated Spot Additional Savings: $0.28/mo
# WARNING: Spot VMs are subject to 30s preemption. Replicas set to >= 2 with 25s grace period.
# ====================================================================
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ccc-demo-app
  namespace: default
  labels:
    app.kubernetes.io/name: ccc-demo-app
    app.kubernetes.io/managed-by: gke-rightsize-adk-agent
  annotations:
    rightsize.gke.io/last-updated: '2026-10-02T14:52:49Z'
    rightsize.gke.io/cluster: cluster-b
    rightsize.gke.io/previous-cpu: 600m
    rightsize.gke.io/previous-memory: 1900Mi
    rightsize.gke.io/savings-status: OVERPROVISIONED
    rightsize.gke.io/safety-buffer: 15.0%
    rightsize.gke.io/spot-eligible: 'true'
    rightsize.gke.io/spot-provisioning: Spot
    rightsize.gke.io/spot-est-savings: $0.28/mo
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ccc-demo-app
  template:
    metadata:
      labels:
        app: ccc-demo-app
    spec:
      nodeSelector:
        cloud.google.com/gke-provisioning: Spot
      terminationGracePeriodSeconds: 25
      tolerations:
      - key: cloud.google.com/gke-spot
        operator: Equal
        value: 'true'
        effect: NoSchedule
      containers:
      - name: demo-container
        image: gcr.io/cluster-b/ccc-demo-app:latest
        resources:
          requests:
            cpu: 10m
            memory: 32Mi
          limits:
            cpu: 20m
            memory: 64Mi
        lifecycle:
          preStop:
            exec:
              command:
              - /bin/sh
              - -c
              - sleep 5
```

Apply the right-sized deployment:
```bash
kubectl apply -f manifests/cluster-b_default_ccc-demo-app-rightsized.yaml
```
Or apply the patch:
```bash
kubectl patch deployment ccc-demo-app -n default --patch-file manifests/ccc-demo-app-patch.yaml
```

---

## Running Unit & Integration Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```
All tests validate resource formatting, delta calculations, YAML generation, and ADK tool execution.

---

## License

This project is licensed under the [Apache 2.0 License](LICENSE).

