// app.js - hub page logic: renders store links, fetches every store's
// stock file (CORS-open, no login needed - see
// docs/superpowers/specs/2026-08-27-component5-hub-design.md), merges the
// results client-side, and supports a live text search. A store whose
// fetch fails (offline PC, no internet, still being provisioned) is shown
// as "unreachable" instead of breaking the other stores' results -
// Promise.allSettled, not Promise.all, is what keeps one bad store from
// taking down the rest.
//
// The URL in stores.json may point at a plain "stock.json" (price, no
// cost) or a store-specific "stock-<token>.json" (cost, no price) - see
// export_static.py's module docstring. This page doesn't care which;
// it just renders whichever of item.cost / item.price is present.

let allItems = [];

async function loadStores() {
  const resp = await fetch("stores.json");
  const data = await resp.json();
  return data.stores.filter(s => s.url);
}

function renderStoreLinks(stores) {
  const el = document.getElementById("store-links");
  el.innerHTML = stores.map(s => {
    const dashboardUrl = s.url.replace(/\/stock(-[0-9a-f]+)?\.json$/, "/");
    return `<a class="store-link" href="${dashboardUrl}" target="_blank" rel="noopener">${s.name}</a>`;
  }).join(" ");
}

async function fetchStoreStock(store) {
  const resp = await fetch(store.url, { mode: "cors" });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const items = await resp.json();
  return items.map(item => ({ ...item, store: store.name }));
}

async function loadAllStock(stores) {
  const statusEl = document.getElementById("store-status");
  const results = await Promise.allSettled(stores.map(fetchStoreStock));

  const statusParts = [];
  const items = [];
  results.forEach((result, i) => {
    const store = stores[i];
    if (result.status === "fulfilled") {
      items.push(...result.value);
      statusParts.push(`<span class="ok">${store.name}: ${result.value.length} items</span>`);
    } else {
      statusParts.push(`<span class="unreachable">${store.name}: unreachable</span>`);
    }
  });
  statusEl.innerHTML = statusParts.join("");
  return items;
}

function formatBoxes(item) {
  if (item.boxes == null) return "";
  return item.boxes_remainder ? `${item.boxes} (+${item.boxes_remainder})` : `${item.boxes}`;
}

function renderResults(items) {
  const body = document.getElementById("results-body");
  body.innerHTML = items.map(item => `
    <tr>
      <td>${item.store}</td>
      <td>${item.reference ?? ""}</td>
      <td>${item.name ?? ""}</td>
      <td>${item.stock ?? ""}</td>
      <td>${formatBoxes(item)}</td>
      <td>${item.cost ?? item.price ?? ""}</td>
    </tr>
  `).join("");
}

function applySearch() {
  const q = document.getElementById("search-box").value.trim().toLowerCase();
  if (!q) {
    renderResults([]);
    return;
  }
  const filtered = allItems.filter(item =>
    (item.name ?? "").toLowerCase().includes(q) ||
    (item.reference ?? "").toLowerCase().includes(q)
  );
  renderResults(filtered.slice(0, 200));
}

async function init() {
  const stores = await loadStores();
  renderStoreLinks(stores);
  allItems = await loadAllStock(stores);
  document.getElementById("search-box").addEventListener("input", applySearch);
}

init();
