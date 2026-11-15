(() => {
  const tg = window.Telegram?.WebApp;
  const state = {
    initData: tg?.initData || "", profile: null, products: [], freebies: false,
    category: "All", search: "", stockOnly: false, sort: "featured", cart: { items: [] },
    unread: 0, apiKeys: 0, apiBase: "", botUsername: "", currentView: "store",
    modal: null, notificationPage: 0,
  };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
  const money = value => {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return "$0";
    return "$" + n.toFixed(3).replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, "");
  };
  const when = value => {
    if (!value) return "";
    const d = new Date(value.endsWith?.("Z") ? value : `${value}Z`);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  };
  const detailText = data => {
    const detail = data?.detail;
    return typeof detail === "string" ? detail : detail?.message || detail?.error || data?.message || "Something went wrong. Please try again.";
  };

  async function api(path, options = {}) {
    const response = await fetch(`/miniapp/api${path}`, {
      ...options,
      headers: {
        "X-Telegram-Init-Data": state.initData,
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(detailText(data));
    return data;
  }

  function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.remove("show"), 2400);
  }

  function openBot() {
    if (state.botUsername && tg?.openTelegramLink) tg.openTelegramLink(`https://t.me/${encodeURIComponent(state.botUsername)}`);
    else if (tg) tg.close();
  }

  function setView(name) {
    if (!["store", "balance", "api", "profile", "orders", "notifications"].includes(name)) name = "store";
    state.currentView = name;
    $$(".view").forEach(view => view.classList.toggle("active", view.id === `${name}-view`));
    $$(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.view === name));
    if (tg?.BackButton) {
      if (name === "store" && !state.modal) tg.BackButton.hide(); else tg.BackButton.show();
    }
    if (name === "balance") loadBalance();
    if (name === "api") renderApi();
    if (name === "profile") renderProfile();
    if (name === "orders") loadOrders();
    if (name === "notifications") loadNotifications(0);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function syncHeader() {
    const count = state.cart.items.reduce((sum, item) => sum + Number(item.quantity || 0), 0);
    const badge = $("#cart-count");
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.classList.toggle("hidden", count < 1);
    $("#notify-dot").classList.toggle("hidden", !state.unread);
    if (state.profile) {
      $("#balance-mini").textContent = money(state.profile.balance);
      $("#greeting").textContent = `HI, ${(state.profile.name || "FRIEND").split(/\s+/)[0].toUpperCase()}`;
    }
  }

  async function loadCart() {
    state.cart = await api("/cart");
    syncHeader();
  }

  async function loadProducts(freebies = state.freebies) {
    state.freebies = freebies;
    const data = await api(`/products?freebies=${freebies ? "true" : "false"}`);
    state.products = data.products || [];
    state.category = "All";
    renderStore();
  }

  function filteredProducts() {
    let products = state.products.filter(product => {
      const query = state.search.trim().toLocaleLowerCase();
      const matchesText = !query || `${product.name} ${product.category} ${product.description}`.toLocaleLowerCase().includes(query);
      return matchesText && (state.category === "All" || product.category === state.category) && (!state.stockOnly || product.in_stock);
    });
    if (state.sort === "price") products = [...products].sort((a,b) => Number(a.price)-Number(b.price) || a.name.localeCompare(b.name));
    if (state.sort === "name") products = [...products].sort((a,b) => a.name.localeCompare(b.name));
    return products;
  }

  function renderCategories() {
    const counts = new Map();
    for (const product of state.products) counts.set(product.category, (counts.get(product.category) || 0) + 1);
    const categories = ["All", ...[...counts.keys()].sort((a,b) => a.localeCompare(b))];
    $("#categories").innerHTML = categories.map(category => {
      const label = category === "All" ? (state.freebies ? "🎁 Freebies" : "All") : category;
      const count = category === "All" ? state.products.length : counts.get(category);
      return `<button class="category-chip ${state.category === category ? "active" : ""}" data-category="${esc(category)}">${esc(label)} <em>${count}</em></button>`;
    }).join("") + `<button class="category-chip ${state.freebies ? "" : ""}" data-freebies="toggle">${state.freebies ? "🛍 Paid shop" : "🎁 Freebies"}</button>`;
  }

  function renderStore() {
    renderCategories();
    const items = filteredProducts();
    $("#catalog-title").textContent = state.category === "All" ? (state.freebies ? "Freebies" : "All products") : state.category;
    $("#catalog-count").textContent = `${items.length} ${items.length === 1 ? "product" : "products"}${state.sort === "name" ? " · A → Z" : state.sort === "price" ? " · Price ↑" : ""}`;
    $("#product-grid").innerHTML = items.map((product, index) => `
      <article class="product-card" data-product="${product.id}" tabindex="0" aria-label="${esc(product.name)}">
        <div class="product-art art-${index % 5}">
          <span class="stock-pill">${product.preorder ? "⏳ Preorder" : product.stock > 999 ? "◇ 999+ in stock" : `◇ ${product.stock} ${product.stock === 1 ? "left" : "in stock"}`}</span>
          <span class="flash-pill">ϟ</span>
          <span class="product-icon">${esc(product.icon || "📦")}</span>
        </div>
        <div class="product-info">
          <div class="product-category">${esc(product.category)}</div>
          <h3 class="product-name">${esc(product.name)}</h3>
          <div class="product-foot"><div class="product-price">${money(product.price)} <small>USDT</small></div><button class="add-button" data-add="${product.id}" aria-label="Add ${esc(product.name)} to cart" ${!product.purchasable ? "disabled" : ""}>+</button></div>
        </div>
      </article>`).join("");
    $("#empty-state").classList.toggle("hidden", items.length > 0);
  }

  function openModal(content, kind) {
    state.modal = kind;
    $("#modal-content").innerHTML = content;
    $("#modal").classList.remove("hidden");
    if (tg?.BackButton) tg.BackButton.show();
  }

  function closeModal() {
    $("#modal").classList.add("hidden");
    state.modal = null;
    if (tg?.BackButton) state.currentView === "store" ? tg.BackButton.hide() : tg.BackButton.show();
  }

  function productInputWarning(product) {
    return (product.required_info || []).some(field => ["photo", "contact"].includes(field.type));
  }

  function productSheet(product) {
    const unsupported = productInputWarning(product);
    openModal(`
      <div class="modal-art">${esc(product.icon || "📦")}</div>
      <p class="eyebrow" style="margin-top:15px">${esc(product.category)} · ${product.preorder ? "PREORDER" : product.in_stock ? "IN STOCK" : "SOLD OUT"}</p>
      <h2 class="modal-product-name" id="modal-title">${esc(product.name)}</h2>
      <p class="modal-description">${esc(product.description || "Digital product, delivered securely through RainDeals.")}</p>
      <div class="modal-price">${money(product.price)} <small>USDT</small></div>
      ${(product.required_info || []).map(field => `<div class="soft-note">Before checkout: ${esc(field.label)} (${esc(field.type)})</div>`).join("")}
      ${product.preorder ? `<div class="soft-note" style="margin:12px 0">This item is currently out of stock. Your order will be queued for manual fulfillment when accepted.</div>` : ""}
      ${unsupported ? `<div class="soft-note" style="margin:12px 0">This item needs a photo or Telegram contact. Open the bot to complete that secure form.</div><button class="primary-button wide-button" data-action="open-bot">Continue in Telegram</button>` : `<button class="primary-button wide-button" data-action="add-product" data-id="${product.id}" ${!product.purchasable ? "disabled" : ""}>${product.preorder ? "Add preorder to cart" : "Add to cart"}</button>`}
      <button class="secondary-button wide-button" style="margin-top:8px" data-close-modal>Keep browsing</button>
    `, "product");
  }

  async function changeCart(productId, quantity) {
    state.cart = await api("/cart", { method: "POST", body: JSON.stringify({ product_id: Number(productId), quantity: Number(quantity) }) });
    syncHeader();
  }

  async function addProduct(productId) {
    try {
      const product = state.products.find(row => row.id === Number(productId));
      const current = state.cart.items.find(row => row.product_id === Number(productId))?.quantity || 0;
      const desired = current + 1;
      if (product && desired > product.sellable_stock) throw new Error(`Only ${product.sellable_stock} units are currently available.`);
      await changeCart(productId, desired);
      closeModal();
      toast("Added to your RainDeals cart");
    } catch (error) { toast(error.message); }
  }

  function renderCart(review = false) {
    const lines = state.cart.items || [];
    const productMap = new Map(state.products.map(product => [product.id, product]));
    const total = lines.reduce((sum, line) => sum + Number(line.total || 0), 0);
    const hasIssue = lines.some(line => line.error);
    const unsupported = lines.some(line => (productMap.get(line.product_id)?.required_info || line.required_info || []).some(field => ["photo", "contact"].includes(field.type)));
    const lineHtml = lines.length ? lines.map(line => {
      const product = productMap.get(line.product_id) || { required_info: [] };
      const fields = product.required_info || line.required_info || [];
      return `<div class="cart-line">
        <div class="cart-line-top"><strong>${esc(line.name)}</strong><strong>${money(line.total)}</strong></div>
        <div class="cart-controls"><button class="qty-button" data-cart-qty="${line.product_id}" data-delta="-1">−</button><span>${line.quantity}</span><button class="qty-button" data-cart-qty="${line.product_id}" data-delta="1">+</button><button class="secondary-button" style="min-height:30px;padding:0 9px;margin-left:auto" data-cart-remove="${line.product_id}">Remove</button><span class="muted-count">${money(product.price || (Number(line.total) / Number(line.quantity)))} each</span></div>
        ${line.error ? `<p class="negative" style="font-size:11px;margin:8px 0 0">${esc(line.error)}</p>` : ""}
        ${fields.map((field,index) => {
          const type = field.type === "email" ? "email" : field.type === "phone" ? "tel" : "text";
          if (["photo", "contact"].includes(field.type)) return `<div class="soft-note" style="margin-top:9px">${esc(field.label)} must be submitted in the Telegram bot.</div>`;
          return `<label class="field-label">${esc(field.label)} · ${esc(line.name)}<input class="field-input" type="${type}" maxlength="3000" data-buyer-product="${line.product_id}" data-buyer-label="${esc(field.label)}" placeholder="Enter ${esc(field.label.toLowerCase())}" autocomplete="${field.type === "email" ? "email" : "off"}"></label>`;
        }).join("")}
      </div>`;
    }).join("") : `<div class="empty-state"><span>🛍️</span><strong>Your cart is empty</strong><p>Browse the RainDeals shop and add a product.</p></div>`;
    const confirmBlock = review ? `
      <div class="soft-note">You’re about to spend <b>${money(total)} USDT</b> from your RainDeals balance. Each item is placed as its own order. Check My Orders if a supplier result is unclear.</div>
      <button class="primary-button wide-button" style="margin-top:12px" data-action="confirm-checkout" ${hasIssue || unsupported ? "disabled" : ""}>Confirm &amp; pay ${money(total)}</button>
      <button class="secondary-button wide-button" style="margin-top:8px" data-action="cart-review-cancel">Back to cart</button>` : `
      <button class="primary-button wide-button" data-action="review-checkout" ${!lines.length || hasIssue || unsupported ? "disabled" : ""}>Review checkout</button>`;
    openModal(`
      <p class="eyebrow">YOUR RAINDEALS BAG</p><h2 class="page-title" id="modal-title">Shopping cart</h2>
      ${lines.length ? `<p class="muted-count">Prices and available stock are rechecked before checkout.</p>` : ""}
      <div class="cart-list">${lineHtml}</div>
      ${lines.length ? `<div class="cart-total"><span>Total</span><strong>${money(total)} USDT</strong></div>${hasIssue ? `<div class="soft-note">Fix unavailable cart items before checkout.</div>` : ""}${unsupported ? `<button class="secondary-button wide-button" data-action="open-bot">Complete photo/contact details in Telegram</button>` : confirmBlock}<button class="secondary-button wide-button" style="margin-top:8px" data-action="clear-cart" ${!lines.length ? "disabled" : ""}>Clear cart</button>` : `<button class="primary-button wide-button" data-action="keep-shopping">Browse products</button>`}
    `, review ? "cart-review" : "cart");
  }

  function renderBalance(data) {
    const entries = data.entries || [];
    $("#balance-view").innerHTML = `
      <p class="eyebrow">YOUR RAINDEALS WALLET</p><h1 class="page-title">Balance</h1>
      <div class="panel-card balance-card"><small>AVAILABLE BALANCE</small><strong>${money(data.balance)} <small>USDT</small></strong><button data-action="open-bot">＋ Add funds in Telegram</button></div>
      <div class="panel-card"><h2 class="panel-heading">Recent activity</h2>
        ${entries.length ? entries.map(row => `<div class="row-line"><div><strong>${esc(row.label)}</strong><small>${esc(when(row.created_at))}</small></div><span class="amount ${Number(row.amount) >= 0 ? "positive" : "negative"}">${Number(row.amount) >= 0 ? "+" : "−"}${money(Math.abs(Number(row.amount)))}<small> · ${money(row.balance)} balance</small></span></div>`).join("") : `<p class="muted-count">Your wallet activity will appear here.</p>`}
      </div><p class="soft-note">Deposits are completed in the RainDeals Telegram bot. Your wallet balance is shared with the store.</p>`;
  }

  async function loadBalance() {
    try { const data = await api("/wallet"); renderBalance(data); }
    catch (error) { $("#balance-view").innerHTML = `<h1 class="page-title">Balance</h1><p class="soft-note">${esc(error.message)}</p>`; }
  }

  function renderApi() {
    const docs = state.apiBase ? `${state.apiBase.replace(/\/$/, "")}/docs/` : "";
    $("#api-view").innerHTML = `
      <p class="eyebrow">DEVELOPER ACCESS</p><h1 class="page-title">Reseller API</h1>
      <div class="panel-card"><div class="api-key-icon">⌘</div><h2 class="panel-heading">Your API access</h2>
        <p class="muted-count">${state.apiKeys ? `${state.apiKeys} active API key${state.apiKeys === 1 ? "" : "s"}` : "No active API key yet"}</p>
        ${state.apiBase ? `<p class="api-base"><b>Base URL</b><br>${esc(state.apiBase)}</p>` : ""}
        <p class="soft-note">API keys are managed in the RainDeals bot and are never shown by this Mini App. Keep them on your server; don’t paste them into website code.</p>
        ${docs ? `<a class="secondary-button wide-button" style="display:grid;place-items:center;text-decoration:none;margin-top:12px" href="${esc(docs)}" target="_blank" rel="noopener">Read API documentation ↗</a>` : ""}
        <button class="primary-button wide-button" style="margin-top:9px" data-action="open-bot">Manage API access in Telegram</button>
      </div>
      <div class="panel-card"><h2 class="panel-heading">What you can do</h2><div class="row-line"><strong>🛍 Browse available products</strong><span class="muted-count">GET</span></div><div class="row-line"><strong>💳 Check your wallet</strong><span class="muted-count">GET</span></div><div class="row-line"><strong>📦 Place and track orders</strong><span class="muted-count">POST / GET</span></div></div>`;
  }

  function renderProfile() {
    const p = state.profile || {};
    const initial = (p.name || "R").trim().charAt(0).toUpperCase();
    $("#profile-view").innerHTML = `
      <p class="eyebrow">YOUR ACCOUNT</p><h1 class="page-title">Profile</h1>
      <div class="panel-card"><div class="profile-head"><div class="avatar">${esc(initial)}</div><div><h2>${esc(p.name || "RainDeals customer")}</h2><p>${p.username ? `@${esc(p.username)}` : "RainDeals member"}</p></div></div>
        <div class="row-line"><div><strong>Telegram ID</strong><small>Your account identifier</small></div><span class="amount">${esc(p.telegram_id)}</span></div>
        <div class="row-line"><div><strong>Total spent</strong></div><span class="amount">${money(p.spent)} USDT</span></div>
        <div class="row-line"><div><strong>Orders</strong></div><span class="amount">${Number(p.orders || 0)}</span></div>
      </div>
      <button class="order-card" data-view="orders"><div class="order-top"><strong>📦 My orders</strong><span>›</span></div><p>Check order status and delivery progress.</p></button>
      <button class="order-card" data-view="notifications"><div class="order-top"><strong>🔔 Notifications</strong><span>${Number(state.unread || 0)} unread ›</span></div><p>Payment, order, support, and account updates.</p></button>
      <button class="secondary-button wide-button" data-action="open-bot">Open RainDeals Telegram bot</button>`;
  }

  async function loadOrders() {
    const view = $("#orders-view");
    view.innerHTML = `<p class="eyebrow">YOUR PURCHASES</p><h1 class="page-title">My orders</h1><div class="loading"><span class="spinner"></span><p>Loading orders…</p></div>`;
    try {
      const data = await api("/orders");
      view.innerHTML = `<p class="eyebrow">YOUR PURCHASES</p><h1 class="page-title">My orders</h1>${data.orders.length ? data.orders.map(order => `<button class="order-card" data-order="${order.id}"><div class="order-top"><strong>Order #${order.id}</strong><span class="status-pill ${esc(order.status)}">${esc(String(order.status).replaceAll("_", " "))}</span></div><p>${esc(order.product_name)} · Qty ${order.quantity} · ${money(order.amount)} USDT</p><p>${esc(when(order.created_at))}${order.refunded ? " · Refunded" : ""}</p></button>`).join("") : `<div class="empty-state"><span>📦</span><strong>No orders yet</strong><p>Your purchases will appear here.</p></div>`}<button class="secondary-button wide-button" data-view="store">Browse products</button>`;
    } catch (error) { view.innerHTML = `<h1 class="page-title">My orders</h1><p class="soft-note">${esc(error.message)}</p>`; }
  }

  async function showOrder(orderId) {
    try {
      const order = await api(`/orders/${Number(orderId)}`);
      openModal(`<p class="eyebrow">ORDER DETAILS</p><h2 class="page-title" id="modal-title">Order #${order.id}</h2><div class="panel-card"><div class="row-line"><strong>Product</strong><span>${esc(order.product_name)}</span></div><div class="row-line"><strong>Status</strong><span class="status-pill ${esc(order.status)}">${esc(String(order.status).replaceAll("_", " "))}</span></div><div class="row-line"><strong>Quantity</strong><span>${order.quantity}</span></div><div class="row-line"><strong>Paid</strong><span>${money(order.amount)} USDT</span></div><div class="row-line"><strong>Placed</strong><span>${esc(when(order.created_at))}</span></div></div><p class="soft-note">For delivered account details or warranty help, open this order in the RainDeals Telegram bot.</p><button class="primary-button wide-button" data-action="open-bot">Open order in Telegram</button>`, "order");
    } catch (error) { toast(error.message); }
  }

  async function loadNotifications(page = 0) {
    state.notificationPage = page;
    const view = $("#notifications-view");
    view.innerHTML = `<p class="eyebrow">YOUR ACCOUNT UPDATES</p><h1 class="page-title">Notifications</h1><div class="loading"><span class="spinner"></span><p>Loading updates…</p></div>`;
    try {
      const data = await api(`/notifications?page=${page}`);
      state.unread = Number(data.unread || 0);
      syncHeader();
      const rows = data.rows || [];
      view.innerHTML = `<p class="eyebrow">YOUR ACCOUNT UPDATES</p><h1 class="page-title">Notifications <span class="muted-count">${state.unread} unread</span></h1>
        ${rows.length ? rows.map(row => `<button class="notification-card" data-notification="${row.id}"><div class="notification-top"><strong>${esc(row.title)}</strong><span>${row.unread ? "🔵" : "✓"}</span></div><p>${esc(row.summary)}</p><p>${esc(when(row.created_at))}</p></button>`).join("") : `<div class="empty-state"><span>✨</span><strong>You’re all caught up</strong><p>New RainDeals updates will appear here.</p></div>`}
        <div class="search-row" style="grid-template-columns:1fr 1fr"><button class="secondary-button" data-notice-page="${Math.max(0,page-1)}" ${page <= 0 ? "disabled" : ""}>‹ Previous</button><button class="secondary-button" data-notice-page="${page+1}" ${!data.has_more ? "disabled" : ""}>Next ›</button></div>
        <button class="secondary-button wide-button" style="margin-top:12px" data-action="open-bot">Open updates in Telegram</button>`;
    } catch (error) { view.innerHTML = `<h1 class="page-title">Notifications</h1><p class="soft-note">${esc(error.message)}</p>`; }
  }

  async function showNotification(id) {
    try {
      const row = await api(`/notifications/${Number(id)}/read`, { method: "POST" });
      const latest = await api("/notifications?page=0");
      state.unread = Number(latest.unread || 0);
      syncHeader();
      openModal(`<p class="eyebrow">RAINDEALS UPDATE</p><h2 class="page-title" id="modal-title">${esc(row.title)}</h2><div class="panel-card"><p>${esc(row.content || row.summary)}</p><small class="muted-count">${esc(when(row.created_at))}</small></div><button class="primary-button wide-button" data-action="open-bot">Open in Telegram</button>`, "notification");
    } catch (error) { toast(error.message); }
  }

  function buildBuyerInfo() {
    const result = {};
    $$('[data-buyer-product]').forEach(input => {
      const productId = input.dataset.buyerProduct;
      result[productId] ||= {};
      result[productId][input.dataset.buyerLabel] = input.value.trim();
    });
    return result;
  }

  async function confirmCheckout() {
    const buyerInfo = buildBuyerInfo();
    try {
      const result = await api("/checkout", { method: "POST", body: JSON.stringify({ buyer_info: buyerInfo }) });
      const checkout = result.checkout || {};
      closeModal();
      await loadCart();
      if (checkout.status === "completed") toast("Purchase complete. Open My Orders for details.");
      else if (checkout.status === "partial") toast("Some items completed. Check My Orders before retrying.");
      else if (checkout.status === "held") toast("A supplier result is being checked. Don’t reorder it.");
      else toast("Checkout finished. Review the result in My Orders.");
      setView("orders");
    } catch (error) {
      toast(error.message);
      if (/photo|contact/i.test(error.message)) toast("Open the Telegram bot to submit this product’s photo/contact field.");
    }
  }

  function onClick(event) {
    const target = event.target.closest("button, [data-product], [data-close-modal]");
    if (!target) return;
    if (target.hasAttribute("data-close-modal")) { closeModal(); return; }
    if (target.dataset.view) { setView(target.dataset.view); return; }
    if (target.dataset.category !== undefined) { state.category = target.dataset.category; renderStore(); return; }
    if (target.dataset.freebies) { loadProducts(!state.freebies).catch(error => toast(error.message)); return; }
    if (target.dataset.add) { event.stopPropagation(); addProduct(target.dataset.add); return; }
    if (target.dataset.action === "add-product") { addProduct(target.dataset.id); return; }
    if (target.dataset.action === "open-bot") { openBot(); return; }
    if (target.dataset.action === "keep-shopping") { closeModal(); setView("store"); return; }
    if (target.dataset.action === "review-checkout") { renderCart(true); return; }
    if (target.dataset.action === "cart-review-cancel") { renderCart(false); return; }
    if (target.dataset.action === "confirm-checkout") { confirmCheckout(); return; }
    if (target.dataset.action === "clear-cart") { api("/cart", { method: "DELETE" }).then(data => { state.cart = data; syncHeader(); renderCart(false); toast("Cart cleared"); }).catch(error => toast(error.message)); return; }
    if (target.dataset.cartQty) {
      const item = state.cart.items.find(row => row.product_id === Number(target.dataset.cartQty));
      if (!item) return;
      const next = Number(item.quantity) + Number(target.dataset.delta);
      changeCart(item.product_id, Math.max(0,next)).then(() => renderCart(false)).catch(error => toast(error.message)); return;
    }
    if (target.dataset.cartRemove) { changeCart(target.dataset.cartRemove, 0).then(() => renderCart(false)).catch(error => toast(error.message)); return; }
    if (target.dataset.order) { showOrder(target.dataset.order); return; }
    if (target.dataset.notification) { showNotification(target.dataset.notification); return; }
    if (target.dataset.noticePage) { loadNotifications(Number(target.dataset.noticePage)); return; }
    if (target.id === "cart-open") { renderCart(false); return; }
    if (target.id === "notify-open") { setView("notifications"); return; }
    if (target.id === "stock-toggle") { state.stockOnly = !state.stockOnly; target.setAttribute("aria-pressed", String(state.stockOnly)); renderStore(); return; }
    if (target.id === "sort-toggle") { state.sort = state.sort === "featured" ? "price" : state.sort === "price" ? "name" : "featured"; renderStore(); return; }
    if (target.id === "clear-search") { $("#search").value = ""; state.search = ""; target.classList.add("hidden"); renderStore(); return; }
    if (target.id === "open-bot") { openBot(); return; }
    if (target.dataset.product) {
      const product = state.products.find(row => row.id === Number(target.dataset.product));
      if (product) productSheet(product);
    }
  }

  function bind() {
    document.addEventListener("click", onClick);
    $("#search").addEventListener("input", event => {
      state.search = event.target.value;
      $("#clear-search").classList.toggle("hidden", !state.search);
      renderStore();
      const input = $("#search"); input.focus(); input.setSelectionRange(input.value.length,input.value.length);
    });
    tg?.BackButton?.onClick(() => state.modal ? closeModal() : setView("store"));
  }

  async function start() {
    if (tg) {
      tg.ready(); tg.expand();
      try { tg.setHeaderColor("#f3f6fc"); tg.setBackgroundColor("#f3f6fc"); } catch {}
    }
    bind();
    if (!state.initData) {
      $("#loading").classList.add("hidden");
      $("#auth-error").classList.remove("hidden");
      $("#open-bot").classList.toggle("hidden", !tg);
      return;
    }
    try {
      const data = await api("/bootstrap");
      state.profile = data.profile; state.unread = data.unread_notifications || 0;
      state.apiKeys = data.active_api_keys || 0; state.apiBase = data.api_base_url || "";
      state.botUsername = data.main_bot_username || "";
      await Promise.all([loadProducts(false), loadCart()]);
      syncHeader();
      $("#loading").classList.add("hidden");
      $("#main").classList.remove("hidden"); $("#bottom-nav").classList.remove("hidden");
      renderProfile(); renderApi();
    } catch (error) {
      $("#loading").classList.add("hidden");
      $("#auth-error").classList.remove("hidden");
      $("#auth-error h1").textContent = error.message.includes("/start") ? "Activate your RainDeals account" : "Could not open RainDeals";
      $("#auth-error p").textContent = error.message;
      $("#open-bot").classList.toggle("hidden", !tg);
    }
  }

  start();
})();
