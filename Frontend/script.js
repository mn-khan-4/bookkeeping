const API_BASE = "http://localhost:8000/api/v1";

const state = {
  bankPage: 1,
  auditPage: 1,
  invoiceType: "ACCPAY",
  lastReconciliation: [],
};

const selectors = {
  navItems: document.querySelectorAll(".nav-item"),
  views: document.querySelectorAll(".view"),
  viewTitle: document.getElementById("view-title"),
  viewSubtitle: document.getElementById("view-subtitle"),
};

const viewMeta = {
  dashboard: ["Dashboard", "Operational overview and daily workload."],
  bank: ["Bank Transactions", "Review and match unreconciled statement lines."],
  reconciliation: ["Reconciliation", "Run matching and auto-reconciliation."],
  invoices: ["Invoices", "Monitor bills and sales invoices."],
  payables: ["Accounts Payable", "Validate invoices and generate ABA files."],
  exceptions: ["Exceptions", "Review and resolve escalated items."],
  rules: ["Supplier Rules", "Maintain supplier coding rules."],
  audit: ["Audit Log", "Track automation actions and approvals."],
  settings: ["Settings", "Manage Xero connection status."],
};

function setActiveView(viewId) {
  selectors.views.forEach((view) => view.classList.remove("active"));
  document.getElementById(viewId)?.classList.add("active");
  selectors.navItems.forEach((item) => item.classList.remove("active"));
  document.querySelector(`[data-view="${viewId}"]`)?.classList.add("active");
  const meta = viewMeta[viewId];
  if (meta) {
    selectors.viewTitle.textContent = meta[0];
    selectors.viewSubtitle.textContent = meta[1];
  }
}

selectors.navItems.forEach((item) => {
  item.addEventListener("click", () => setActiveView(item.dataset.view));
});

async function fetchJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function renderBadge(value, type) {
  return `<span class="badge ${type}">${value}</span>`;
}

async function loadDashboard() {
  try {
    const [bank, status, exceptionSummary, payablesSummary, exceptions, audit] = await Promise.all([
      fetchJson("/integrations/xero/bank-transactions"),
      fetchJson("/reconciliation/status"),
      fetchJson("/exceptions/summary"),
      fetchJson("/payables/summary"),
      fetchJson("/exceptions"),
      fetchJson("/admin/audit-log?limit=10&offset=0"),
    ]);

    // Update Balance
    const totalBalance = payablesSummary.total_owed ? (31180.24 - payablesSummary.total_owed).toFixed(2) : "31,180.24";
    document.getElementById("stat-total-balance").textContent = `$${totalBalance}`;

    // Render Spending Bars
    const spendingContainer = document.getElementById("spending-bars");
    const months = ["Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const heights = [40, 60, 45, 80, 55, 70, 90];
    spendingContainer.innerHTML = months.map((m, i) => `
      <div class="bar-group">
        <div class="bar ${m === "Sep" ? "active" : ""}" style="height: ${heights[i]}%;"></div>
        <span class="bar-label">${m}</span>
      </div>
    `).join("");

    // Render Transactions
    const transactionContainer = document.getElementById("dashboard-transactions");
    const mockTxns = [
      { name: "Apple Inc", time: "30 min ago", amount: "-$45.00", icon: "🍎" },
      { name: "Jerry Helfer", time: "12 Dec 2024", amount: "+$120.00", icon: "👤" },
      { name: "Dribbble", time: "11 Dec 2024", amount: "-$350.00", icon: "🏀" },
      { name: "Ekra Food", time: "09 Dec 2024", amount: "-$452.00", icon: "🍔" },
    ];
    transactionContainer.innerHTML = mockTxns.map(t => `
      <div class="transaction-item">
        <div style="display: flex; align-items: center; gap: 12px;">
          <div class="txn-icon-wrapper">${t.icon}</div>
          <div class="txn-info">
            <h4>${t.name}</h4>
            <p class="muted small">${t.time}</p>
          </div>
        </div>
        <p class="txn-amount" style="color: ${t.amount.startsWith("+") ? "var(--success)" : "var(--text)"}">${t.amount}</p>
      </div>
    `).join("");

  } catch (error) {
    console.error(error);
  }
}

async function loadBankAccounts() {
  const select = document.getElementById("bank-account-filter");
  select.innerHTML = `<option value="">All Accounts</option>`;
  try {
    const data = await fetchJson("/integrations/xero/bank-accounts");
    (data.accounts || []).forEach((account) => {
      const option = document.createElement("option");
      option.value = account.AccountID;
      option.textContent = `${account.Name} (${account.CurrencyCode})`;
      select.appendChild(option);
    });
  } catch (error) {
    console.error(error);
  }
}

async function loadBankTransactions() {
  try {
    const bankAccountId = document.getElementById("bank-account-filter").value || "";
    const data = await fetchJson(
      `/integrations/xero/bank-transactions?page=${state.bankPage}&bank_account_id=${bankAccountId}`
    );
    const rows = (data.transactions || []).map((txn) => {
      const status = txn.status ?? "";
      const statusBadge = status === "UNRECONCILED" ? "warning" : "success";
      const action =
        status === "UNRECONCILED"
          ? `<button class="btn" data-run-match="${txn.bank_transaction_id}" data-amount="${txn.amount}" data-date="${txn.date}" data-supplier="${txn.contact?.name ?? ""}">Run Match</button>`
          : "—";
      return `<tr>
        <td>${new Date(txn.date).toLocaleDateString()}</td>
        <td>${txn.amount}</td>
        <td>${txn.type}</td>
        <td>${txn.bank_account_name ?? ""}</td>
        <td>${txn.reference ?? ""}</td>
        <td>${renderBadge(status, statusBadge)}</td>
        <td>${action}</td>
      </tr>`;
    });
    document.getElementById("bank-transactions").innerHTML = rows.join("");
    document.getElementById("bank-page").textContent = state.bankPage;
  } catch (error) {
    console.error(error);
  }
}

async function runSingleMatch({ bankTransactionId, amount, date, supplier }) {
  const payload = {
    client_id: "default",
    items: [
      {
        source_reference: bankTransactionId,
        supplier_name: supplier || "",
        amount: Number(amount) || 0,
        date: date || new Date().toISOString(),
      },
    ],
  };
  await fetchJson("/reconciliation/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function loadReconciliationStatus() {
  try {
    const summary = await fetchJson("/reconciliation/status");
    document.getElementById("reconciliation-summary").innerHTML = `Auto Today: ${
      summary.auto_reconciled_today
    } | Exceptions Pending: ${summary.exceptions_pending} | Total Today: ${summary.total_processed_today}`;
  } catch (error) {
    console.error(error);
  }
}

async function runFullReconciliation() {
  const payload = {
    client_id: "default",
    items: [],
  };
  const summary = await fetchJson("/reconciliation/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  document.getElementById("reconciliation-summary").textContent = `Auto: ${
    summary.auto_reconciled
  } | Exceptions: ${summary.exceptions_created} | Total: ${summary.total}`;
}

async function loadInvoices() {
  const status = document.getElementById("invoice-status").value;
  const data = await fetchJson(
    `/integrations/xero/invoices?invoice_type=${state.invoiceType}&status=${status}`
  );
  const rows = (data.invoices || []).map((inv) => {
    const status = inv.Status ?? "";
    const badgeType =
      ["AUTHORISED", "PAID"].includes(status)
        ? "success"
        : ["SUBMITTED", "DRAFT"].includes(status)
        ? "warning"
        : ["VOIDED", "DELETED"].includes(status)
        ? "danger"
        : "neutral";
    return `<tr>
      <td>${inv.InvoiceNumber ?? ""}</td>
      <td>${inv.Contact?.Name ?? ""}</td>
      <td>${inv.Date ?? ""}</td>
      <td>${inv.DueDate ?? ""}</td>
      <td>${inv.Total ?? ""}</td>
      <td>${renderBadge(status, badgeType)}</td>
      <td>—</td>
    </tr>`;
  });
  document.getElementById("invoice-table").innerHTML = rows.join("");
}

async function loadPayables() {
  const data = await fetchJson("/payables/outstanding");
  const rows = (data || []).map((item) => {
    return `<tr>
      <td>${item.supplier ?? ""}</td>
      <td>${item.amount ?? ""}</td>
      <td>${item.due_date ?? ""}</td>
      <td>${item.invoice_number ?? ""}</td>
    </tr>`;
  });
  document.getElementById("outstanding-payables").innerHTML = rows.join("");
  const total = (data || []).reduce((sum, item) => sum + (item.amount ?? 0), 0);
  document.getElementById("payables-total").textContent = `Total: AUD ${total.toFixed(2)}`;
}

async function loadExceptions() {
  const data = await fetchJson("/exceptions");
  const rows = (data || []).map((item) => {
    const status = (item.status || "").toUpperCase();
    const badgeType = status === "RESOLVED" ? "success" : "warning";
    return `<tr>
      <td>${item.id}</td>
      <td>${item.source_reference}</td>
      <td>${item.reason}</td>
      <td>${new Date(item.created_at).toLocaleDateString()}</td>
      <td>${renderBadge(status, badgeType)}</td>
      <td><button class="btn" data-resolve="${item.id}">Resolve</button></td>
    </tr>`;
  });
  document.getElementById("exceptions-table").innerHTML = rows.join("");

  const summary = await fetchJson("/exceptions/summary");
  document.getElementById("exceptions-summary").textContent = `Open: ${summary.open} | Resolved: ${summary.resolved} | Total: ${summary.total}`;
}

async function loadRules() {
  const clientId = "default";
  const rules = await fetchJson(`/admin/supplier-rules?client_id=${clientId}`);
  const rows = (rules || []).map((rule) => {
    return `<tr>
      <td>${rule.supplier_name}</td>
      <td>${rule.account_code}</td>
      <td>${rule.gst_code}</td>
      <td>${rule.client_id}</td>
      <td>${rule.updated_at ?? ""}</td>
      <td><button class="btn" data-delete-rule="${rule.id}">Delete</button></td>
    </tr>`;
  });
  document.getElementById("rules-table").innerHTML = rows.join("");
}

async function loadAudit() {
  const data = await fetchJson(`/admin/audit-log?limit=50&offset=${(state.auditPage - 1) * 50}`);
  const rows = (data || []).map((entry) => {
    return `<tr>
      <td>${new Date(entry.timestamp).toLocaleString()}</td>
      <td>${entry.action}</td>
      <td>${entry.entity_type}</td>
      <td>${entry.entity_id}</td>
      <td>${entry.rule_applied ?? ""}</td>
      <td>${entry.confidence_score ?? ""}</td>
      <td>${entry.user_id ?? ""}</td>
    </tr>`;
  });
  document.getElementById("audit-table").innerHTML = rows.join("");
}

async function loadSettings() {
  const status = await fetchJson("/integrations/health/xero");
  document.getElementById("xero-status").textContent = `${status.status} · ${
    status.organisation_name ?? "No tenant"
  }`;
}

function initHandlers() {
  document.getElementById("refresh-data").addEventListener("click", () => initData());

  document.getElementById("bank-prev").addEventListener("click", () => {
    state.bankPage = Math.max(1, state.bankPage - 1);
    loadBankTransactions();
  });
  document.getElementById("bank-next").addEventListener("click", () => {
    state.bankPage += 1;
    loadBankTransactions();
  });

  document.getElementById("run-reconciliation").addEventListener("click", async () => {
    await runFullReconciliation();
  });

  document.getElementById("export-invoices").addEventListener("click", () => {
    window.location.href = `${API_BASE}/integrations/xero/invoices/export?invoice_type=${state.invoiceType}`;
  });

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.invoiceType = tab.dataset.type;
      loadInvoices();
    });
  });

  document.getElementById("invoice-status").addEventListener("change", loadInvoices);

  document.getElementById("invoice-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(event.target);
    const payload = Object.fromEntries(formData.entries());
    payload.amount = Number(payload.amount);
    payload.tax_amount = payload.tax_amount ? Number(payload.tax_amount) : null;

    try {
      const response = await fetchJson("/payables/process-invoice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      document.getElementById("invoice-form-result").textContent = `Draft bill created: ${
        response.publish.xero_invoice_id || "Pending"
      }`;
    } catch (error) {
      document.getElementById("invoice-form-result").textContent = `Error: ${error.message}`;
    }
  });

  document.getElementById("aba-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(event.target);
    const payload = Object.fromEntries(formData.entries());

    payload.human_approved = payload.human_approved === "on";
    try {
      payload.payments = JSON.parse(payload.payments);
    } catch (error) {
      alert("Payments must be valid JSON.");
      return;
    }

    const response = await fetch(`${API_BASE}/payables/generate-aba`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      alert("Failed to generate ABA file.");
      return;
    }

    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "payments.aba";
    link.click();
  });

  document.getElementById("rules-table").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.deleteRule) {
      await fetchJson(`/admin/supplier-rules/${button.dataset.deleteRule}`, {
        method: "DELETE",
      });
      loadRules();
    }
  });

  document.getElementById("add-rule").addEventListener("click", () => {
    document.getElementById("rule-form").classList.toggle("hidden");
  });

  document.getElementById("rule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(event.target).entries());
    await fetchJson("/admin/supplier-rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    event.target.reset();
    loadRules();
  });

  document.getElementById("exceptions-table").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.resolve) {
      openResolveModal(button.dataset.resolve);
    }
  });

  document.getElementById("close-modal").addEventListener("click", closeResolveModal);
  document.getElementById("modal").addEventListener("click", (event) => {
    if (event.target.id === "modal") {
      closeResolveModal();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeResolveModal();
    }
  });

  document.getElementById("resolve-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(event.target).entries());
    await fetchJson(`/exceptions/${state.resolvingId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    closeResolveModal();
    loadExceptions();
  });

  document.getElementById("connect-xero").addEventListener("click", () => {
    window.location.href = `${API_BASE}/integrations/xero/authorize`;
  });
  document.getElementById("refresh-xero").addEventListener("click", () => {
    fetchJson("/integrations/xero/refresh", { method: "POST" });
  });
  document.getElementById("view-tenants").addEventListener("click", async () => {
    const tenants = await fetchJson("/integrations/xero/tenants");
    document.getElementById("xero-tenants").innerHTML = (tenants.tenants || [])
      .map((tenant) => `<div class="card">${tenant.name} (${tenant.tenant_id})</div>`)
      .join("");
  });

  document.getElementById("bank-transactions").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.runMatch) {
      await runSingleMatch({
        bankTransactionId: button.dataset.runMatch,
        amount: button.dataset.amount,
        date: button.dataset.date,
        supplier: button.dataset.supplier,
      });
      loadBankTransactions();
    }
  });
}

function openResolveModal(exceptionId) {
  state.resolvingId = exceptionId;
  document.getElementById("modal").classList.remove("hidden");
}

function closeResolveModal() {
  state.resolvingId = null;
  document.getElementById("modal").classList.add("hidden");
}

async function initData() {
  await loadDashboard();
  await loadBankAccounts();
  await loadBankTransactions();
  await loadInvoices();
  await loadPayables();
  await loadExceptions();
  await loadRules();
  await loadAudit();
  await loadSettings();
}

initHandlers();
initData();
