// GKE Workload Rightsizing & Spot VM Dashboard Client

let currentBuffer = 15.0;
let currentWorkloads = [];
let activeModalWorkload = null;
let debounceTimer = null;

document.addEventListener("DOMContentLoaded", () => {
  initEventListeners();
  loadOverview();
  loadWorkloads();
});

function initEventListeners() {
  // Buffer slider
  const slider = document.getElementById("buffer-slider");
  const bufferVal = document.getElementById("buffer-val");
  if (slider) {
    slider.addEventListener("input", (e) => {
      currentBuffer = parseFloat(e.target.value);
      bufferVal.textContent = `${currentBuffer}%`;
    });
    slider.addEventListener("change", () => {
      loadOverview();
      loadWorkloads();
    });
  }

  // Filter dropdowns
  const clusterFilter = document.getElementById("filter-cluster");
  const nsFilter = document.getElementById("filter-namespace");
  const statusFilter = document.getElementById("filter-status");
  const spotFilter = document.getElementById("filter-spot");
  const searchInput = document.getElementById("search-input");

  if (clusterFilter) clusterFilter.addEventListener("change", () => loadWorkloads());
  if (nsFilter) nsFilter.addEventListener("change", () => loadWorkloads());
  if (statusFilter) statusFilter.addEventListener("change", () => loadWorkloads());
  if (spotFilter) spotFilter.addEventListener("change", () => loadWorkloads());

  if (searchInput) {
    searchInput.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => loadWorkloads(), 300);
    });
  }

  // Refresh button
  const refreshBtn = document.getElementById("btn-refresh");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      refreshBtn.disabled = true;
      refreshBtn.innerHTML = '<span class="spinner"></span> Refreshing...';
      loadOverview();
      loadWorkloads(true).finally(() => {
        refreshBtn.disabled = false;
        refreshBtn.innerHTML = '🔄 Refresh Telemetry';
      });
    });
  }

  // Batch run button
  const batchBtn = document.getElementById("btn-batch-rightsize");
  if (batchBtn) {
    batchBtn.addEventListener("click", runBatchRightsizing);
  }

  // Modal close
  const modalClose = document.getElementById("modal-close");
  const modalOverlay = document.getElementById("manifest-modal");
  if (modalClose && modalOverlay) {
    modalClose.addEventListener("click", closeModal);
    modalOverlay.addEventListener("click", (e) => {
      if (e.target === modalOverlay) closeModal();
    });
  }

  // Modal toggles
  const spotToggle = document.getElementById("modal-spot-toggle");
  const limitsToggle = document.getElementById("modal-limits-toggle");
  const typeRadios = document.querySelectorAll('input[name="modal-manifest-type"]');

  if (spotToggle) spotToggle.addEventListener("change", updateModalManifest);
  if (limitsToggle) limitsToggle.addEventListener("change", updateModalManifest);
  typeRadios.forEach((r) => r.addEventListener("change", updateModalManifest));

  // Copy YAML button
  const copyBtn = document.getElementById("btn-copy-yaml");
  if (copyBtn) copyBtn.addEventListener("click", copyManifestToClipboard);

  // Save to disk button
  const saveBtn = document.getElementById("btn-save-yaml");
  if (saveBtn) saveBtn.addEventListener("click", saveManifestToDisk);

  // Download YAML button
  const downloadBtn = document.getElementById("btn-download-yaml");
  if (downloadBtn) downloadBtn.addEventListener("click", downloadManifestFile);

  // Chat message submit
  const chatForm = document.getElementById("chat-form");
  if (chatForm) {
    chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      handleChatSubmit();
    });
  }
}

// Fetch Overview KPI stats
async function loadOverview() {
  try {
    const res = await fetch(`/api/overview?buffer_percent=${currentBuffer}`);
    const data = await res.json();
    if (data.status === "success") {
      document.getElementById("kpi-savings").textContent = `$${data.monthly_ondemand_savings_usd.toFixed(2)}`;
      document.getElementById("kpi-spot").textContent = `+$${data.monthly_spot_savings_usd.toFixed(2)}`;
      document.getElementById("kpi-annual").textContent = `$${data.total_annual_potential_savings_usd.toFixed(2)}`;
      document.getElementById("kpi-over").textContent = data.overprovisioned_count;
      document.getElementById("kpi-under").textContent = data.underprovisioned_count;
      document.getElementById("kpi-spot-count").textContent = data.spot_eligible_count;

      // Update project info in navbar
      if (data.project_id) {
        const projEl = document.getElementById("header-project-id");
        if (projEl) projEl.textContent = data.project_id;
      }
      if (data.location) {
        const locEl = document.getElementById("header-location");
        if (locEl) locEl.textContent = `(${data.location})`;
      }

      // Populate filter dropdowns if not already populated
      populateFilterOptions("filter-cluster", data.clusters);
      populateFilterOptions("filter-namespace", data.namespaces);
    }
  } catch (err) {
    console.error("Failed to load overview:", err);
  }
}

function populateFilterOptions(selectId, items) {
  const select = document.getElementById(selectId);
  if (!select || select.options.length > 1) return;
  items.forEach((item) => {
    const opt = document.createElement("option");
    opt.value = item;
    opt.textContent = item;
    select.appendChild(opt);
  });
}

// Fetch and render Workloads table
async function loadWorkloads(forceRefresh = false) {
  const cluster = document.getElementById("filter-cluster")?.value || "";
  const namespace = document.getElementById("filter-namespace")?.value || "";
  const status = document.getElementById("filter-status")?.value || "";
  const spotFilter = document.getElementById("filter-spot")?.value || "";
  const search = document.getElementById("search-input")?.value || "";

  let url = `/api/workloads?buffer_percent=${currentBuffer}`;
  if (cluster) url += `&cluster=${encodeURIComponent(cluster)}`;
  if (namespace) url += `&namespace=${encodeURIComponent(namespace)}`;
  if (status && status !== "ALL") url += `&status=${encodeURIComponent(status)}`;
  if (spotFilter === "ELIGIBLE") url += `&spot_eligible=true`;
  if (spotFilter === "INELIGIBLE") url += `&spot_eligible=false`;
  if (search) url += `&search=${encodeURIComponent(search)}`;
  if (forceRefresh) url += `&force_refresh=true`;

  const tbody = document.getElementById("workloads-tbody");
  tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding: 2rem;"><span class="spinner"></span> Loading telemetry from Google Cloud Monitoring...</td></tr>`;

  try {
    const res = await fetch(url);
    const data = await res.json();
    currentWorkloads = data.workloads || [];
    renderWorkloadsTable(currentWorkloads);
    document.getElementById("workload-count").textContent = currentWorkloads.length;
  } catch (err) {
    console.error("Failed to load workloads:", err);
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color: var(--accent-red); padding: 2rem;">Error fetching workloads: ${err.message}</td></tr>`;
  }
}

function renderWorkloadsTable(workloads) {
  const tbody = document.getElementById("workloads-tbody");
  tbody.innerHTML = "";

  if (workloads.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding: 2rem; color: var(--text-muted);">No workloads matched the current filters.</td></tr>`;
    return;
  }

  workloads.forEach((w) => {
    const tr = document.createElement("tr");

    // Status Badge
    let statusClass = "balanced";
    if (w.status === "OVERPROVISIONED") statusClass = "over";
    else if (w.status === "UNDERPROVISIONED") statusClass = "under";

    // Spot Badge
    let spotBadge = "";
    if (w.spot_eligible) {
      spotBadge = `
        <span class="spot-badge eligible" title="${w.spot_reason}">
          ⚡ Spot Eligible <small>(+$${w.spot_monthly_savings_usd.toFixed(2)}/mo)</small>
        </span>
      `;
    } else {
      spotBadge = `
        <span class="spot-badge ineligible" title="${w.spot_reason}">
          ❌ Ineligible
        </span>
      `;
    }

    // Deltas
    const cpuDeltaClass = parseFloat(w.cpu_delta_percent) < 0 ? "neg" : "pos";
    const memDeltaClass = parseFloat(w.memory_delta_percent) < 0 ? "neg" : "pos";

    const savingsDisplay =
      w.estimated_monthly_savings_usd > 0
        ? `<span class="savings-val">$${w.estimated_monthly_savings_usd.toFixed(2)}/mo</span>`
        : `<span style="color:var(--text-muted)">-</span>`;

    tr.innerHTML = `
      <td>
        <div class="workload-cell">
          <span class="workload-name">${escapeHtml(w.controller_name)}</span>
          <span class="workload-meta">${escapeHtml(w.controller_kind)} • ${escapeHtml(w.container_name)}</span>
        </div>
      </td>
      <td><code>${escapeHtml(w.cluster_name)}</code></td>
      <td><code>${escapeHtml(w.namespace_name)}</code></td>
      <td>
        <div class="res-compare">
          <span class="res-cur">Current: ${escapeHtml(w.current_cpu)}</span>
          <div>
            <span class="res-rec">${escapeHtml(w.recommended_cpu)}</span>
            <span class="delta-pill ${cpuDeltaClass}">${w.cpu_delta_percent}</span>
          </div>
        </div>
      </td>
      <td>
        <div class="res-compare">
          <span class="res-cur">Current: ${escapeHtml(w.current_memory)}</span>
          <div>
            <span class="res-rec">${escapeHtml(w.recommended_memory)}</span>
            <span class="delta-pill ${memDeltaClass}">${w.memory_delta_percent}</span>
          </div>
        </div>
      </td>
      <td>
        <span class="status-badge ${statusClass}">${escapeHtml(w.status)}</span>
      </td>
      <td>${spotBadge}</td>
      <td>${savingsDisplay}</td>
      <td>
        <div class="action-buttons">
          <button class="btn btn-secondary btn-sm" onclick="openManifestModal('${escapeHtml(w.controller_name)}', '${escapeHtml(w.namespace_name)}', '${escapeHtml(w.cluster_name)}', 'deployment')">
            📄 Manifest
          </button>
          <button class="btn btn-secondary btn-sm" onclick="openManifestModal('${escapeHtml(w.controller_name)}', '${escapeHtml(w.namespace_name)}', '${escapeHtml(w.cluster_name)}', 'patch')">
            ⚡ Patch
          </button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

// Modal functions
async function openManifestModal(workloadName, namespace, clusterName, manifestType = "deployment") {
  activeModalWorkload = { workloadName, namespace, clusterName };
  const modal = document.getElementById("manifest-modal");
  const modalTitle = document.getElementById("modal-workload-title");
  modalTitle.textContent = `${workloadName} (${namespace}) - ${clusterName}`;

  // Set initial radio state
  const radio = document.querySelector(`input[name="modal-manifest-type"][value="${manifestType}"]`);
  if (radio) radio.checked = true;

  // Find if workload is Spot eligible
  const match = currentWorkloads.find((w) => w.controller_name === workloadName && w.namespace_name === namespace);
  const spotToggle = document.getElementById("modal-spot-toggle");
  if (spotToggle) {
    if (match && match.spot_eligible) {
      spotToggle.disabled = false;
      spotToggle.checked = true; // Default enable spot configuration for eligible workloads!
    } else {
      spotToggle.disabled = true;
      spotToggle.checked = false;
    }
  }

  modal.classList.add("active");
  await updateModalManifest();
}

async function updateModalManifest() {
  if (!activeModalWorkload) return;
  const { workloadName, namespace, clusterName } = activeModalWorkload;

  const manifestType = document.querySelector('input[name="modal-manifest-type"]:checked')?.value || "deployment";
  const includeLimits = document.getElementById("modal-limits-toggle")?.checked ?? true;
  const includeSpot = document.getElementById("modal-spot-toggle")?.checked ?? false;

  const codeBlock = document.getElementById("modal-yaml-code");
  codeBlock.textContent = "# Generating production YAML manifest...";

  try {
    const url = `/api/manifest?workload_name=${encodeURIComponent(workloadName)}&namespace=${encodeURIComponent(namespace)}&cluster_name=${encodeURIComponent(clusterName)}&manifest_type=${manifestType}&buffer_percent=${currentBuffer}&include_limits=${includeLimits}&include_spot=${includeSpot}`;
    const res = await fetch(url);
    const data = await res.json();
    if (data.status === "success") {
      codeBlock.textContent = data.yaml_content;
      activeModalWorkload.currentYaml = data.yaml_content;
      activeModalWorkload.suggestedFilename = data.suggested_filename;
    } else {
      codeBlock.textContent = `# Error generating manifest: ${data.message}`;
    }
  } catch (err) {
    codeBlock.textContent = `# Network error: ${err.message}`;
  }
}

function closeModal() {
  const modal = document.getElementById("manifest-modal");
  modal.classList.remove("active");
  activeModalWorkload = null;
}

function copyManifestToClipboard() {
  if (!activeModalWorkload || !activeModalWorkload.currentYaml) return;
  navigator.clipboard.writeText(activeModalWorkload.currentYaml).then(() => {
    const copyBtn = document.getElementById("btn-copy-yaml");
    const originalText = copyBtn.innerHTML;
    copyBtn.innerHTML = "✅ Copied!";
    setTimeout(() => {
      copyBtn.innerHTML = originalText;
    }, 2000);
  });
}

async function saveManifestToDisk() {
  if (!activeModalWorkload || !activeModalWorkload.currentYaml) return;
  const saveBtn = document.getElementById("btn-save-yaml");
  saveBtn.disabled = true;
  saveBtn.innerHTML = '<span class="spinner"></span> Saving...';

  try {
    const res = await fetch("/api/manifest/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workload_name: activeModalWorkload.workloadName,
        yaml_content: activeModalWorkload.currentYaml,
        filename: activeModalWorkload.suggestedFilename,
      }),
    });
    const data = await res.json();
    if (data.status === "success") {
      alert(`Manifest successfully saved to disk:\n${data.saved_path}`);
    } else {
      alert(`Failed to save manifest: ${data.detail || "Unknown error"}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    saveBtn.disabled = false;
    saveBtn.innerHTML = "💾 Save to manifests/";
  }
}

function downloadManifestFile() {
  if (!activeModalWorkload || !activeModalWorkload.currentYaml) return;
  const filename = activeModalWorkload.suggestedFilename || `${activeModalWorkload.workloadName}-rightsized.yaml`;
  const blob = new Blob([activeModalWorkload.currentYaml], { type: "text/yaml" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// Batch Rightsizing
async function runBatchRightsizing() {
  const btn = document.getElementById("btn-batch-rightsize");
  if (!confirm(`Run automated rightsizing pipeline for ALL workloads with ${currentBuffer}% safety buffer? Clean YAML manifests will be generated in manifests/.`)) {
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Generating Manifests...';

  try {
    const res = await fetch("/api/rightsize-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        buffer_percent: currentBuffer,
        include_spot: true,
      }),
    });
    const data = await res.json();
    if (data.status === "success") {
      alert(`Successfully generated and saved ${data.total_manifests_generated} manifests in manifests/ directory!\nTotal Monthly Savings: $${data.total_monthly_savings_usd.toFixed(2)}\nAdditional Spot Savings: +$${data.total_spot_savings_usd.toFixed(2)}`);
      loadOverview();
      loadWorkloads();
    } else {
      alert(`Batch rightsizing error: ${data.detail || "Unknown error"}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = "⚡ Batch Rightsize All";
  }
}

// AI Agent Chat Assistant
async function handleChatSubmit() {
  const input = document.getElementById("chat-input");
  const msg = input.value.trim();
  if (!msg) return;

  const messagesContainer = document.getElementById("chat-messages");

  // Append user message
  const userBubble = document.createElement("div");
  userBubble.className = "chat-bubble user";
  userBubble.textContent = msg;
  messagesContainer.appendChild(userBubble);
  input.value = "";
  messagesContainer.scrollTop = messagesContainer.scrollHeight;

  // Append loading assistant bubble
  const assistantBubble = document.createElement("div");
  assistantBubble.className = "chat-bubble assistant";
  assistantBubble.innerHTML = '<span class="spinner"></span> Analyzing telemetry...';
  messagesContainer.appendChild(assistantBubble);
  messagesContainer.scrollTop = messagesContainer.scrollHeight;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg }),
    });
    const data = await res.json();
    if (data.status === "success") {
      assistantBubble.innerHTML = formatMarkdown(data.response);
    } else {
      assistantBubble.textContent = `Error: ${data.detail || "Agent unavailable"}`;
    }
  } catch (err) {
    assistantBubble.textContent = `Error communicating with agent: ${err.message}`;
  }
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function sendPromptChip(text) {
  document.getElementById("chat-input").value = text;
  handleChatSubmit();
}

function formatMarkdown(text) {
  if (!text) return "";
  let html = escapeHtml(text);
  // Bold
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  // Inline code
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  // Headings
  html = html.replace(/^### (.*$)/gim, '<h4 style="margin:0.5rem 0 0.25rem 0; color:var(--accent-blue);">$1</h4>');
  // Lists
  html = html.replace(/^\- (.*$)/gim, '<li style="margin-left:1.25rem;">$1</li>');
  // Newlines to br
  html = html.replace(/\n\n/g, "<br><br>");
  return html;
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
