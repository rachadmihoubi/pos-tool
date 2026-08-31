// app.js - hub page logic: renders store links, fetches every store's
// stock file (CORS-open, no login needed - see
// docs/superpowers/specs/2026-08-27-component5-hub-design.md), merges the
// results client-side, and supports a live text search. A store whose
// fetch fails (offline PC, no internet, still being provisioned) is shown
// as "unreachable" instead of breaking the other stores' results -
// Promise.allSettled, not Promise.all, is what keeps one bad store from
// taking down the rest.
//
// The URL in the stores list may point at a plain "stock.json" (price, no
// cost) or a store-specific "stock-<token>.json" (cost, avg_cost and
// last_purchase_cost, no price) - see export_static.py's module
// docstring. This page doesn't care which; it renders avg_cost/
// last_purchase_cost when present, falling back to price/cost for a
// store with no token configured (see renderResults).
//
// The store list itself lives at a tokenized filename, not the plain
// "stores.json" this page used before 2026-08-31 - poslib/provision.py's
// register_store_with_hub() reads and rewrites this exact file
// automatically when a new store is provisioned, using the same
// unguessable-filename-as-the-only-gate pattern already used for each
// store's own stock-<token>.json (see that function's docstring for why
// a real per-request secret isn't possible with Cloudflare Pages Direct
// Upload). STORES_JSON must stay in sync with HUB_REGISTRY_FILENAME in
// poslib/provision.py - changing one without the other breaks either the
// live page or the automated provisioning path.
const STORES_JSON = "stores-41582b721adbd68e4fb50f5245f0e56b.json";

let allItems = [];

async function loadStores() {
  const resp = await fetch(STORES_JSON);
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
      <td>${item.avg_cost ?? item.price ?? ""}</td>
      <td>${item.last_purchase_cost ?? item.price ?? ""}</td>
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
