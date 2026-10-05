"""FastAPI application for GKE Workload Rightsizing and Spot VM Dashboard."""

import os
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.responses import HTMLResponse, Response, JSONResponse
import uvicorn

from gke_rightsize.metrics_collector import GKEMetricsCollector
from gke_rightsize.comparator import WorkloadComparator
from gke_rightsize.tools import (
    list_clusters,
    compare_workloads,
    evaluate_spot_workloads,
    generate_rightsized_manifest,
    save_manifest,
    rightsize_all_workloads,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"


def create_app() -> FastAPI:
    app = FastAPI(
        title="GKE Rightsizing & Spot VM Dashboard",
        description="Interactive local dashboard for GKE workload rightsizing and Spot VM advisory",
        version="1.0.0",
    )

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_file = TEMPLATES_DIR / "index.html"
        if not html_file.exists():
            raise HTTPException(status_code=404, detail="Dashboard template not found.")
        with open(html_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())

    @app.get("/static/css/style.css")
    async def get_css():
        css_file = STATIC_DIR / "css" / "style.css"
        if not css_file.exists():
            return Response(content="", media_type="text/css")
        with open(css_file, "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/css")

    @app.get("/static/js/dashboard.js")
    async def get_js():
        js_file = STATIC_DIR / "js" / "dashboard.js"
        if not js_file.exists():
            return Response(content="", media_type="application/javascript")
        with open(js_file, "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="application/javascript")

    @app.get("/api/clusters")
    async def get_clusters():
        res = list_clusters()
        return res

    @app.get("/api/overview")
    async def get_overview(buffer_percent: float = Query(15.0, ge=0.0, le=100.0)):
        comp = compare_workloads(buffer_percent=buffer_percent)
        recs = comp.get("recommendations", [])
        
        total_analyzed = len(recs)
        overprovisioned = sum(1 for r in recs if r.get("status") == "OVERPROVISIONED")
        underprovisioned = sum(1 for r in recs if r.get("status") == "UNDERPROVISIONED")
        balanced = sum(1 for r in recs if r.get("status") == "BALANCED")
        spot_eligible = sum(1 for r in recs if r.get("spot_eligible"))
        
        on_demand_savings = sum(r.get("estimated_monthly_savings_usd", 0.0) for r in recs)
        spot_savings = sum(r.get("spot_monthly_savings_usd", 0.0) for r in recs if r.get("spot_eligible"))
        annual_savings = (on_demand_savings + spot_savings) * 12.0

        clusters = sorted(list({r.get("cluster_name") for r in recs if r.get("cluster_name")}))
        namespaces = sorted(list({r.get("namespace_name") for r in recs if r.get("namespace_name")}))

        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

        return {
            "status": "success",
            "project_id": project_id,
            "location": location,
            "total_workloads": total_analyzed,
            "overprovisioned_count": overprovisioned,
            "underprovisioned_count": underprovisioned,
            "balanced_count": balanced,
            "spot_eligible_count": spot_eligible,
            "monthly_ondemand_savings_usd": round(on_demand_savings, 2),
            "monthly_spot_savings_usd": round(spot_savings, 2),
            "total_monthly_potential_savings_usd": round(on_demand_savings + spot_savings, 2),
            "total_annual_potential_savings_usd": round(annual_savings, 2),
            "buffer_percent": buffer_percent,
            "clusters": clusters,
            "namespaces": namespaces,
        }

    @app.get("/api/workloads")
    async def get_workloads(
        cluster: Optional[str] = None,
        namespace: Optional[str] = None,
        status: Optional[str] = None,
        spot_eligible: Optional[bool] = None,
        buffer_percent: float = Query(15.0, ge=0.0, le=100.0),
        search: Optional[str] = None,
        force_refresh: bool = False,
    ):
        if force_refresh:
            GKEMetricsCollector._cache.clear()

        comp = compare_workloads(
            cluster_name=cluster or "",
            namespace=namespace or "",
            buffer_percent=buffer_percent,
        )
        recs = comp.get("recommendations", [])

        # Filter by status if requested
        if status and status.upper() != "ALL":
            recs = [r for r in recs if r.get("status") == status.upper()]

        # Filter by Spot eligibility if requested
        if spot_eligible is not None:
            recs = [r for r in recs if bool(r.get("spot_eligible")) == spot_eligible]

        # Search query filter (matches workload or container name)
        if search:
            q = search.lower().strip()
            recs = [
                r for r in recs
                if q in r.get("controller_name", "").lower()
                or q in r.get("container_name", "").lower()
                or q in r.get("cluster_name", "").lower()
                or q in r.get("namespace_name", "").lower()
            ]

        return {
            "status": "success",
            "total_count": len(recs),
            "buffer_percent": buffer_percent,
            "workloads": recs,
            "markdown_report": comp.get("markdown_report", ""),
        }

    @app.get("/api/manifest")
    async def get_manifest(
        workload_name: str,
        namespace: str = "default",
        cluster_name: str = "",
        manifest_type: str = "deployment",
        buffer_percent: float = Query(15.0, ge=0.0, le=100.0),
        include_limits: bool = True,
        include_spot: bool = False,
    ):
        res = generate_rightsized_manifest(
            workload_name=workload_name,
            namespace=namespace,
            cluster_name=cluster_name,
            buffer_percent=buffer_percent,
            manifest_type=manifest_type,
            include_limits=include_limits,
            include_spot=include_spot,
        )
        if res.get("status") != "success":
            raise HTTPException(status_code=404, detail=res.get("message", "Workload not found."))
        return res

    @app.post("/api/manifest/save")
    async def save_generated_manifest(payload: Dict[str, Any] = Body(...)):
        w_name = payload.get("workload_name")
        yaml_content = payload.get("yaml_content")
        filename = payload.get("filename", "")
        if not w_name or not yaml_content:
            raise HTTPException(status_code=400, detail="workload_name and yaml_content are required.")
        res = save_manifest(workload_name=w_name, yaml_content=yaml_content, filename=filename)
        return res

    @app.post("/api/rightsize-all")
    async def run_rightsize_all(payload: Dict[str, Any] = Body(default={})):
        c_name = payload.get("cluster_name", "")
        buffer = float(payload.get("buffer_percent", 15.0))
        spot = bool(payload.get("include_spot", False))
        res = rightsize_all_workloads(cluster_name=c_name, buffer_percent=buffer, include_spot=spot)
        return res

    @app.get("/api/spot-advisory")
    async def get_spot_advisory(cluster: Optional[str] = None, namespace: Optional[str] = None):
        res = evaluate_spot_workloads(cluster_name=cluster or "", namespace=namespace or "")
        return res

    @app.post("/api/chat")
    async def chat_with_agent(payload: Dict[str, str] = Body(...)):
        user_message = payload.get("message", "").strip()
        if not user_message:
            raise HTTPException(status_code=400, detail="Message is required.")

        # Provide fast, contextual rightsizing insights
        comp = compare_workloads()
        recs = comp.get("recommendations", [])
        total_savings = sum(r.get("estimated_monthly_savings_usd", 0.0) for r in recs)
        total_spot = sum(r.get("spot_monthly_savings_usd", 0.0) for r in recs if r.get("spot_eligible"))
        over = [r for r in recs if r.get("status") == "OVERPROVISIONED"]
        under = [r for r in recs if r.get("status") == "UNDERPROVISIONED"]
        spot_eligible = [r for r in recs if r.get("spot_eligible")]

        msg_lower = user_message.lower()
        if "spot" in msg_lower:
            reply = (
                f"### GKE Spot VM Feasibility Analysis:\n\n"
                f"We identified **{len(spot_eligible)} stateless workloads** eligible for Spot VMs, "
                f"yielding an additional **${total_spot:.2f}/month** in compute savings (~65% discount).\n\n"
                f"**Eligible Workloads:**\n"
                + "\n".join([f"- `{w['controller_name']}` in namespace `{w['namespace_name']}` (Cluster: `{w['cluster_name']}`) -> +${w['spot_monthly_savings_usd']:.2f}/mo extra savings" for w in spot_eligible])
                + "\n\n**Golden Path Spot Requirements:**\n"
                f"- Configure `nodeSelector: cloud.google.com/gke-provisioning: Spot`\n"
                f"- Set `terminationGracePeriodSeconds: 25` (< 30s preemption window)\n"
                f"- Ensure at least 2 replicas with a PodDisruptionBudget (PDB) for zero-downtime draining."
            )
        elif "save" in msg_lower or "cost" in msg_lower or "saving" in msg_lower:
            reply = (
                f"### Cost Savings Summary:\n\n"
                f"- **Monthly On-Demand Rightsizing Savings:** ${total_savings:.2f}/month\n"
                f"- **Additional Spot VM Savings:** ${total_spot:.2f}/month\n"
                f"- **Total Combined Monthly Potential:** ${total_savings + total_spot:.2f}/month\n"
                f"- **Annualized Savings Run-rate:** ${(total_savings + total_spot) * 12:.2f}/year\n\n"
                f"Top opportunity: `{over[0]['controller_name']}` in `{over[0]['cluster_name']}` saving ${over[0]['estimated_monthly_savings_usd']:.2f}/mo."
            )
        elif "risk" in msg_lower or "underprovision" in msg_lower or "oom" in msg_lower:
            reply = (
                f"### Reliability & OOM Risk Assessment:\n\n"
                f"We detected **{len(under)} under-provisioned workloads** that are at risk of CPU throttling or OOMKills:\n\n"
                + "\n".join([f"- `{w['controller_name']}` in `{w['namespace_name']}` ({w['cluster_name']}): Current requests are {w['current_cpu']} / {w['current_memory']}, but VPA recommends {w['recommended_cpu']} / {w['recommended_memory']}." for w in under])
                + "\n\nWe recommend upsizing requests immediately using the generated patch manifests."
            )
        else:
            reply = (
                f"### Rightsizing Telemetry Overview:\n\n"
                f"- **Analyzed Workloads:** {len(recs)} across {len({r['cluster_name'] for r in recs})} clusters\n"
                f"- **Over-provisioned:** {len(over)} workloads (reclaim ${total_savings:.2f}/mo)\n"
                f"- **Under-provisioned:** {len(under)} workloads (OOM risk)\n"
                f"- **Spot Eligible:** {len(spot_eligible)} workloads (save up to +${total_spot:.2f}/mo on Spot)\n\n"
                f"You can use the interactive table to inspect YAML manifests, toggle Spot VM configurations, and adjust the safety headroom buffer."
            )

        return {"status": "success", "response": reply}

    return app


def run_dashboard(host: str = "0.0.0.0", port: int = 8080):
    """Runs the interactive rightsizing dashboard server."""
    app = create_app()
    print(f"🚀 Starting GKE Rightsizing Dashboard on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_dashboard()
