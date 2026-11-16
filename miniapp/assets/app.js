(() => {
  const tg = window.Telegram?.WebApp;
  const state = {
    initData: tg?.initData || "", profile: null, products: [], freebies: false,
    category: "All", search: "", stockOnly: false, sort: "featured", cart: { items: [] },
    unread: 0, botUsername: "", depositMethods: [], currentProductId: null, buyerInfoDraft: {},
    currentView: "store", cartPending: new Map(), productDetails: new Map(),
    productsRequest: 0, productRequest: 0, catalogLoadedAt: 0, productsRequests: new Map(), cartReady: false, modal: null,
    notificationPage: 0, depositTimer: null, qrObjectUrl: null, currentDepositId: null,
    cartLoadPromise: null,
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
      cache: "no-store",
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
    if (!["store", "balance", "profile", "orders", "notifications"].includes(name)) name = "store";
    state.currentView = name;
    $$(".view").forEach(view => view.classList.toggle("active", view.id === `${name}-view`));
    $$(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.view === name));
    if (tg?.BackButton) {
      if (name === "store" && !state.modal) tg.BackButton.hide(); else tg.BackButton.show();
    }
    if (name === "balance") loadBalance();
    if (name === "profile") renderProfile();
    if (name === "orders") loadOrders();
    if (name === "notifications") loadNotifications(0);
    if (name === "store" && state.catalogLoadedAt && Date.now() - state.catalogLoadedAt > 90_000) {
      loadProducts(state.freebies, { preserveCategory: true, quiet: true }).catch(() => {});
    }
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

  function loadCart(force = false) {
    if (!force && state.cartReady) return Promise.resolve(state.cart);
    if (state.cartLoadPromise) return state.cartLoadPromise;
    state.cartLoadPromise = api("/cart").then(data => {
      state.cart = data;
      state.cartReady = true;
      syncHeader();
      if (state.modal === "cart") renderCart(false);
      return data;
    }).finally(() => { state.cartLoadPromise = null; });
    return state.cartLoadPromise;
  }

  function catalogCacheKey(freebies) {
    const telegramId = tg?.initDataUnsafe?.user?.id;
    return telegramId ? `raindeals_catalog_v1_${telegramId}_${freebies ? "freebies" : "products"}` : "";
  }

  function readCatalogCache(freebies) {
    const key = catalogCacheKey(freebies);
    if (!key) return null;
    try {
      const value = JSON.parse(sessionStorage.getItem(key) || "null");
      return value && Array.isArray(value.products) && Number(value.savedAt) > 0 ? value : null;
    } catch { return null; }
  }

  function saveCatalogCache(freebies, products) {
    const key = catalogCacheKey(freebies);
    if (!key) return;
    try { sessionStorage.setItem(key, JSON.stringify({ savedAt: Date.now(), products })); } catch {}
  }

  function invalidateCatalogCache() {
    const telegramId = tg?.initDataUnsafe?.user?.id;
    if (!telegramId) return;
    for (const kind of ["products", "freebies"]) {
      try { sessionStorage.removeItem(`raindeals_catalog_v1_${telegramId}_${kind}`); } catch {}
    }
  }

  async function loadProducts(freebies = state.freebies, { preserveCategory = false, quiet = false } = {}) {
    state.freebies = freebies;
    const previousCategory = state.category;
    const cached = readCatalogCache(freebies);
    const existing = state.productsRequests.get(Boolean(freebies));
    if (existing && existing.id === state.productsRequest) return existing.promise;
    const requestId = ++state.productsRequest;
    let hasCached = Boolean(cached && Date.now() - cached.savedAt < 10 * 60_000);
    if (hasCached) {
      state.products = cached.products;
      state.catalogLoadedAt = cached.savedAt;
      state.category = preserveCategory && state.products.some(row => row.category === previousCategory)
        ? previousCategory : "All";
      renderStore();
    } else if (!state.products.length && !quiet) {
      $("#product-grid").innerHTML = `<div class="loading-card"><span class="spinner"></span><span><b>Refreshing RainDeals catalog</b><small>Getting the latest products and stock…</small><i class="loading-progress"></i></span></div>`;
      $("#empty-state").classList.add("hidden");
    }
    const promise = (async () => {
      try {
        const data = await api(`/products?freebies=${freebies ? "true" : "false"}`);
        if (requestId !== state.productsRequest) return;
        state.catalogLoadedAt = Date.now();
        state.productDetails.clear();
        state.products = data.products || [];
        saveCatalogCache(freebies, state.products);
        state.category = preserveCategory && state.products.some(row => row.category === previousCategory)
          ? previousCategory : "All";
        renderStore();
      } catch (error) {
        if (!hasCached) throw error;
        if (!quiet) toast("Showing saved catalog. Refresh when your connection is back.");
      } finally {
        const active = state.productsRequests.get(Boolean(freebies));
        if (active?.id === requestId) state.productsRequests.delete(Boolean(freebies));
      }
    })();
    state.productsRequests.set(Boolean(freebies), { id: requestId, promise });
    return promise;
  }

  async function refreshProducts() {
    const button = $("#catalog-refresh");
    button?.classList.add("is-refreshing");
    try {
      await loadProducts(state.freebies, { preserveCategory: true, quiet: true });
      toast("Products refreshed");
    } catch (error) { toast(error.message); }
    finally { button?.classList.remove("is-refreshing"); }
  }

  function filteredProducts() {
    let products = state.products.filter(product => {
      const query = state.search.trim().toLocaleLowerCase();
      const matchesText = !query || `${product.name} ${product.category}`.toLocaleLowerCase().includes(query);
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
    if (state.modal === "deposit" && kind !== "deposit") stopDepositTimer();
    state.modal = kind;
    $("#modal-content").innerHTML = content;
    $("#modal").classList.remove("hidden");
    if (tg?.BackButton) tg.BackButton.show();
  }

  function closeModal() {
    $("#modal").classList.add("hidden");
    state.productRequest += 1;
    stopDepositTimer();
    state.currentDepositId = null;
    state.currentProductId = null;
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
      ${product.description_truncated ? `<button class="text-button" data-action="full-product-details" data-product-id="${Number(product.id)}">Read full description</button>` : ""}
      <div class="modal-price">${money(product.price)} <small>USDT</small></div>
      ${(product.required_info || []).map(field => `<div class="soft-note">Before checkout: ${esc(field.label)} (${esc(field.type)})</div>`).join("")}
      ${product.preorder ? `<div class="soft-note" style="margin:12px 0">This item is currently out of stock. Your order will be queued for manual fulfillment when accepted.</div>` : ""}
      ${unsupported ? `<div class="soft-note" style="margin:12px 0">This item needs a photo or Telegram contact. Open the bot to complete that secure form.</div><button class="primary-button wide-button" data-action="open-bot">Continue in Telegram</button>` : `<button class="primary-button wide-button" data-action="add-product" data-id="${product.id}" ${!product.purchasable ? "disabled" : ""}>${product.preorder ? "Add preorder to cart" : "Add to cart"}</button>`}
      <button class="secondary-button wide-button" style="margin-top:8px" data-close-modal>Keep browsing</button>
    `, "product");
  }

  function openProduct(product) {
    state.currentProductId = Number(product.id);
    const cached = state.productDetails.get(Number(product.id));
    productSheet({ ...product, ...(cached || {}) });
  }

  async function showFullProductDetails(productId) {
    const id = Number(productId);
    const cached = state.productDetails.get(id);
    if (cached) { productSheet({ ...state.products.find(row => row.id === id), ...cached }); return; }
    const requestId = ++state.productRequest;
    const button = $('[data-action="full-product-details"]');
    if (button) { button.disabled = true; button.textContent = "Loading full description…"; }
    try {
      const data = await api(`/products/${id}`);
      if (data.product) state.productDetails.set(id, data.product);
      if (state.modal === "product" && id === state.currentProductId && requestId === state.productRequest) {
        productSheet({ ...state.products.find(row => row.id === id), ...(data.product || {}) });
      }
    } catch (error) {
      toast(error.message);
      if (button) { button.disabled = false; button.textContent = "Read full description"; }
    }
  }

  function applyLocalCartQuantity(snapshot, productId, quantity) {
    const product = state.products.find(row => row.id === Number(productId));
    const items = [...(snapshot.items || [])];
    const index = items.findIndex(row => Number(row.product_id) === Number(productId));
    if (quantity <= 0) {
      if (index >= 0) items.splice(index, 1);
      return { ...snapshot, items };
    }
    const old = index >= 0 ? items[index] : null;
    const unit = Number(product?.price ?? (old ? Number(old.total) / Math.max(1, Number(old.quantity)) : 0));
    const line = {
      ...(old || {}), product_id: Number(productId), name: product?.name || old?.name || "Product",
      quantity, total: String(Math.max(0, unit * quantity)), error: null, optimistic: true,
    };
    if (index >= 0) items[index] = line;
    else items.push(line);
    return { ...snapshot, items };
  }

  function changeCart(productId, quantity) {
    const id = Number(productId);
    const nextQuantity = Math.max(0, Number(quantity));
    let task = state.cartPending.get(id);
    if (!task) {
      task = { target: nextQuantity, promise: null };
      state.cartPending.set(id, task);
      task.promise = (async () => {
        try {
          while (true) {
            const requested = task.target;
            const serverCart = await api("/cart", {
              method: "POST", body: JSON.stringify({ product_id: id, quantity: requested }),
            });
            state.cart = serverCart;
            if (task.target === requested) {
              state.cartPending.delete(id);
              break;
            }
            state.cart = applyLocalCartQuantity(state.cart, id, task.target);
          }
        } catch (error) {
          state.cartPending.delete(id);
          try { state.cart = await api("/cart"); } catch {}
          toast(error.message);
        } finally {
          syncHeader();
          if (["cart", "cart-review"].includes(state.modal)) renderCart(state.modal === "cart-review");
        }
      })();
    } else {
      task.target = nextQuantity;
    }
    state.cart = applyLocalCartQuantity(state.cart, id, nextQuantity);
    syncHeader();
    if (["cart", "cart-review"].includes(state.modal)) renderCart(state.modal === "cart-review");
    return task.promise;
  }

  function addProduct(productId) {
    if (!state.cartReady) {
      toast("Loading your cart…");
      loadCart().then(() => addProduct(productId)).catch(error => toast(error.message));
      return;
    }
    const product = state.products.find(row => row.id === Number(productId));
    const current = state.cart.items.find(row => row.product_id === Number(productId))?.quantity || 0;
    const desired = current + 1;
    if (product && desired > product.sellable_stock) {
      toast(`Only ${product.sellable_stock} units are currently available.`);
      return;
    }
    changeCart(productId, desired);
    closeModal();
    toast("Added to your RainDeals cart");
  }

  function renderCart(review = false) {
    if (!state.cartReady && !state.cartPending.size) {
      openModal(`<p class="eyebrow">YOUR RAINDEALS BAG</p><h2 class="page-title" id="modal-title">Shopping cart</h2><div class="loading"><span class="spinner"></span><p>Loading your cart…</p></div>`, "cart");
      return;
    }
    const lines = state.cart.items || [];
    const productMap = new Map(state.products.map(product => [product.id, product]));
    const total = lines.reduce((sum, line) => sum + Number(line.total || 0), 0);
    const hasIssue = lines.some(line => line.error);
    const unsupported = lines.some(line => (productMap.get(line.product_id)?.required_info || line.required_info || []).some(field => ["photo", "contact"].includes(field.type)));
    const lineHtml = lines.length ? lines.map(line => {
      const product = productMap.get(line.product_id) || { required_info: [] };
      const fields = product.required_info || line.required_info || [];
      return `<div class="cart-line">
        <div class="cart-line-top"><strong>${esc(line.name)}</strong><strong>${line.optimistic ? "Updating…" : money(line.total)}</strong></div>
        <div class="cart-controls"><button class="qty-button" data-cart-qty="${line.product_id}" data-delta="-1">−</button><span>${line.quantity}</span><button class="qty-button" data-cart-qty="${line.product_id}" data-delta="1">+</button><button class="secondary-button" style="min-height:30px;padding:0 9px;margin-left:auto" data-cart-remove="${line.product_id}">Remove</button><span class="muted-count">${money(product.price || (Number(line.total) / Number(line.quantity)))} each</span></div>
        ${line.error ? `<p class="negative" style="font-size:11px;margin:8px 0 0">${esc(line.error)}</p>` : ""}
        ${fields.map((field,index) => {
          const type = field.type === "email" ? "email" : field.type === "phone" ? "tel" : "text";
          if (["photo", "contact"].includes(field.type)) return `<div class="soft-note" style="margin-top:9px">${esc(field.label)} must be submitted in the Telegram bot.</div>`;
          const saved = state.buyerInfoDraft[line.product_id]?.[field.label] || "";
          return `<label class="field-label">${esc(field.label)} · ${esc(line.name)}<input class="field-input" type="${type}" maxlength="3000" data-buyer-product="${line.product_id}" data-buyer-label="${esc(field.label)}" value="${esc(saved)}" placeholder="Enter ${esc(field.label.toLowerCase())}" autocomplete="${field.type === "email" ? "email" : "off"}"></label>`;
        }).join("")}
      </div>`;
    }).join("") : `<div class="empty-state"><span>🛍️</span><strong>Your cart is empty</strong><p>Browse the RainDeals shop and add a product.</p></div>`;
    const updating = state.cartPending.size > 0;
    const confirmBlock = review ? `
      <div class="soft-note">You’re about to spend <b>${money(total)} USDT</b> from your RainDeals balance. Each item is placed as its own order. Check My Orders if a supplier result is unclear.</div>
      <button class="primary-button wide-button" style="margin-top:12px" data-action="confirm-checkout" ${hasIssue || unsupported || updating ? "disabled" : ""}>Confirm &amp; pay ${money(total)}</button>
      <button class="secondary-button wide-button" style="margin-top:8px" data-action="cart-review-cancel">Back to cart</button>` : `
      <button class="primary-button wide-button" data-action="review-checkout" ${!lines.length || hasIssue || unsupported || updating ? "disabled" : ""}>${updating ? "Updating cart…" : "Review checkout"}</button>`;
    openModal(`
      <p class="eyebrow">YOUR RAINDEALS BAG</p><h2 class="page-title" id="modal-title">Shopping cart</h2>
      ${lines.length ? `<p class="muted-count">Prices and available stock are rechecked before checkout.</p>` : ""}
      <div class="cart-list">${lineHtml}</div>
      ${lines.length ? `<div class="cart-total"><span>Total${updating ? " · updating" : ""}</span><strong>${updating ? "Updating…" : `${money(total)} USDT`}</strong></div>${hasIssue ? `<div class="soft-note">Fix unavailable cart items before checkout.</div>` : ""}${unsupported ? `<button class="secondary-button wide-button" data-action="open-bot">Complete photo/contact details in Telegram</button>` : confirmBlock}<button class="secondary-button wide-button" style="margin-top:8px" data-action="clear-cart" ${!lines.length || updating ? "disabled" : ""}>Clear cart</button>` : `<button class="primary-button wide-button" data-action="keep-shopping">Browse products</button>`}
    `, review ? "cart-review" : "cart");
  }

  function renderBalance(data, depositData = { deposits: [] }) {
    const entries = data.entries || [];
    const deposits = depositData.deposits || [];
    $("#balance-view").innerHTML = `
      <p class="eyebrow">YOUR RAINDEALS WALLET</p><h1 class="page-title">Balance</h1>
      <div class="panel-card balance-card"><small>AVAILABLE BALANCE</small><strong>${money(data.balance)} <small>USDT</small></strong><div class="balance-actions"><button data-action="open-deposit-methods">＋ Add funds</button><button data-action="open-promocode">🎟 Redeem code</button></div></div>
      <div class="panel-card"><h2 class="panel-heading">Recent wallet activity</h2>
        ${entries.length ? entries.map(row => `<div class="row-line"><div><strong>${esc(row.label)}</strong><small>${esc(when(row.created_at))}</small></div><span class="amount ${Number(row.amount) >= 0 ? "positive" : "negative"}">${Number(row.amount) >= 0 ? "+" : "−"}${money(Math.abs(Number(row.amount)))}<small> · ${money(row.balance)} balance</small></span></div>`).join("") : `<p class="muted-count">Your wallet activity will appear here.</p>`}
      </div>
      <div class="panel-card"><div class="section-heading compact-heading"><h2 class="panel-heading">My deposits</h2><button class="text-button" data-action="open-deposit-methods">＋ Add funds</button></div>
        ${deposits.length ? deposits.slice(0, 5).map(row => `<button class="deposit-history-row" data-deposit-detail="${Number(row.id)}"><span class="deposit-history-icon">${esc(row.icon || "💳")}</span><span><strong>${esc(row.invoice || `Deposit #${row.id}`)}</strong><small>${esc(row.network_label || row.network)} · ${esc(when(row.created_at))}</small></span><span class="status-pill ${esc(row.status)}">${esc(String(row.status).replaceAll("_", " "))}</span></button>`).join("") : `<p class="muted-count">Your deposit history will appear here.</p>`}
        <button class="secondary-button wide-button" data-action="load-deposits">View deposit history</button>
      </div>`;
  }

  async function loadBalance() {
    const view = $("#balance-view");
    view.innerHTML = `<p class="eyebrow">YOUR RAINDEALS WALLET</p><h1 class="page-title">Balance</h1><div class="loading"><span class="spinner"></span><p>Loading wallet…</p></div>`;
    try {
      const [wallet, deposits] = await Promise.all([api("/wallet"), api("/deposits?page=0")]);
      renderBalance(wallet, deposits);
    } catch (error) { view.innerHTML = `<h1 class="page-title">Balance</h1><p class="soft-note">${esc(error.message)}</p>`; }
  }

  function openDepositMethods(methods) {
    const rows = methods || [];
    openModal(`<p class="eyebrow">ADD FUNDS</p><h2 class="page-title" id="modal-title">Choose a payment method</h2>
      <p class="muted-count">Use one of the payment methods enabled for RainDeals. Your verified payment amount is credited to your wallet.</p>
      <div class="payment-method-list">${rows.map(row => `<button class="payment-method-card ${row.configured ? "" : "disabled"}" data-deposit-network="${esc(row.network)}" ${row.configured ? "" : "disabled"}><span>${esc(row.icon)}</span><span><strong>${esc(row.label)}</strong><small>${row.configured ? `Submit ${esc(row.reference_label)} after paying` : "Temporarily unavailable"}</small></span><b>${row.configured ? "›" : "—"}</b></button>`).join("")}</div>
      <button class="secondary-button wide-button" data-action="load-deposits">View deposit history</button>`, "deposit-methods");
  }

  async function showDepositMethods() {
    if (state.depositMethods.length) { openDepositMethods(state.depositMethods); return; }
    openModal(`<p class="eyebrow">ADD FUNDS</p><h2 class="page-title" id="modal-title">Choose a payment method</h2><div class="loading"><span class="spinner"></span><p>Loading available methods…</p></div>`, "deposit-methods");
    try { const data = await api("/deposit-methods"); if (state.modal === "deposit-methods") openDepositMethods(data.methods || []); }
    catch (error) { if (state.modal === "deposit-methods") openModal(`<h2 class="page-title" id="modal-title">Payment methods</h2><p class="soft-note">${esc(error.message)}</p>`, "deposit-methods"); }
  }

  function stopDepositTimer() {
    if (state.depositTimer) clearInterval(state.depositTimer);
    state.depositTimer = null;
    if (state.qrObjectUrl) URL.revokeObjectURL(state.qrObjectUrl);
    state.qrObjectUrl = null;
  }

  async function loadDepositQr(id) {
    try {
      const response = await fetch(`/miniapp/api/deposits/${Number(id)}/qr`, { cache: "no-store", headers: { "X-Telegram-Init-Data": state.initData } });
      if (!response.ok) return;
      const blob = await response.blob();
      if (state.modal !== "deposit" || state.currentDepositId !== Number(id)) return;
      if (state.qrObjectUrl) URL.revokeObjectURL(state.qrObjectUrl);
      state.qrObjectUrl = URL.createObjectURL(blob);
      const image = $("#deposit-qr");
      if (image && state.modal === "deposit") { image.src = state.qrObjectUrl; image.classList.remove("hidden"); }
    } catch { /* QR is optional; the payment address remains available. */ }
  }

  function renderDeposit(deposit) {
    stopDepositTimer();
    state.currentDepositId = Number(deposit.id);
    const payment = deposit.payment;
    const waiting = deposit.status === "waiting_hash" && Number(deposit.remaining_seconds) > 0 && !deposit.reference_submitted;
    const expired = deposit.status === "waiting_hash" && !deposit.reference_submitted && !Number(deposit.remaining_seconds);
    const completed = deposit.status === "completed";
    const methodLabel = deposit.network_label || deposit.network;
    openModal(`<p class="eyebrow">${esc(deposit.icon || "💳")} DEPOSIT · #${Number(deposit.id)}</p><h2 class="page-title" id="modal-title">${esc(deposit.invoice || `Deposit #${deposit.id}`)}</h2>
      <div class="panel-card deposit-invoice-card">
        <div class="deposit-summary"><span>${esc(methodLabel)}</span><span class="status-pill ${esc(deposit.status)}">${expired ? "expired" : esc(String(deposit.status).replaceAll("_", " "))}</span></div>
        ${waiting && payment?.qr_url ? `<img id="deposit-qr" class="deposit-qr hidden" alt="Payment QR code">` : ""}
        ${waiting && payment?.address ? `<div class="deposit-address"><small>${deposit.network === "BINANCE" ? "Binance Pay ID" : deposit.network === "UPI" ? "UPI ID" : "Deposit address"}</small><code>${esc(payment.address)}</code><button class="text-button" data-copy="${esc(payment.address)}">Copy</button></div>` : ""}
        <p class="soft-note">${esc(payment?.instructions || (completed ? `Payment verified${deposit.received_amount && Number(deposit.received_amount) ? ` · ${money(deposit.received_amount)} USDT credited` : ""}.` : deposit.reference_submitted ? "Your payment reference has been saved for verification. Check back here for the result." : expired ? "This invoice expired. Create a new deposit to continue." : "This deposit is being processed."))}</p>
        ${waiting ? `<p class="invoice-timer">⏳ Invoice expires in <strong id="deposit-timer">—</strong></p><label class="field-label">${esc(deposit.reference_label)}<input id="deposit-reference" class="field-input" maxlength="255" autocomplete="off" placeholder="Enter your payment reference"></label><button class="primary-button wide-button" style="margin-top:12px" data-action="submit-deposit-reference" data-deposit-id="${Number(deposit.id)}">Submit for verification</button><button class="secondary-button wide-button" style="margin-top:8px" data-action="change-payment-method">Change payment method</button>` : `<p class="invoice-timer">${completed ? "This deposit is complete." : deposit.reference_submitted ? "Your reference is saved. Revisit this deposit to check its status." : expired ? "The invoice is no longer accepting payment references." : `Status: ${esc(String(deposit.status).replaceAll("_", " "))}`}</p>${completed ? `<button class="primary-button wide-button" data-close-modal>Done</button>` : deposit.reference_submitted ? `<button class="primary-button wide-button" data-action="load-deposits">My deposits</button>` : `<button class="primary-button wide-button" data-action="open-deposit-methods">${expired ? "Create a new deposit" : "Choose another method"}</button>`}`}
      </div><button class="secondary-button wide-button" data-action="load-deposits">My deposit history</button>`, "deposit");
    if (waiting && payment?.qr_url) {
      if (payment.qr_url.startsWith("https://")) {
        const image = $("#deposit-qr");
        if (image) { image.src = payment.qr_url; image.classList.remove("hidden"); }
      } else loadDepositQr(deposit.id);
    }
    if (waiting) {
      const expiresAt = Date.now() + Number(deposit.remaining_seconds) * 1000;
      const update = () => {
        const node = $("#deposit-timer");
        if (!node || state.modal !== "deposit") { stopDepositTimer(); return; }
        const seconds = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
        node.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
        if (!seconds) {
          stopDepositTimer();
          const input = $("#deposit-reference");
          const submit = $('[data-action="submit-deposit-reference"]');
          const changeMethod = $('[data-action="change-payment-method"]');
          if (input) input.disabled = true;
          if (submit) { submit.disabled = false; submit.textContent = "Create a new deposit"; submit.dataset.action = "open-deposit-methods"; delete submit.dataset.depositId; }
          if (changeMethod) { changeMethod.textContent = "Create a new deposit"; changeMethod.dataset.action = "open-deposit-methods"; }
          toast("Invoice expired. Create a new deposit to continue.");
        }
      };
      update(); state.depositTimer = setInterval(update, 1000);
    }
  }

  async function beginDeposit(network) {
    try {
      const data = await api("/deposits", { method: "POST", body: JSON.stringify({ network }) });
      if (!data.deposit) throw new Error("Could not load the deposit invoice.");
      renderDeposit(data.deposit);
    } catch (error) { toast(error.message); }
  }

  async function showDeposit(id) {
    try { const data = await api(`/deposits/${Number(id)}`); renderDeposit(data.deposit); }
    catch (error) { toast(error.message); }
  }

  async function submitDepositReference(id) {
    const input = $("#deposit-reference");
    const button = $('[data-action="submit-deposit-reference"]');
    const reference = input?.value.trim();
    if (!reference) { toast("Enter your payment reference first."); input?.focus(); return; }
    button.disabled = true;
    button.textContent = "Saving reference…";
    try {
      const result = await api(`/deposits/${Number(id)}/reference`, {
        method: "POST", body: JSON.stringify({ reference }),
      });
      toast(result.message || "Reference saved for verification.");
      await showDeposit(id);
      loadBalance();
    } catch (error) {
      toast(error.message);
      if (button) { button.disabled = false; button.textContent = "Submit for verification"; }
    }
  }

  async function copyText(value, success = "Copied") {
    try { await navigator.clipboard.writeText(value); toast(success); }
    catch { toast("Could not copy. Select and copy the text manually."); }
  }

  async function shareReferral() {
    const link = state.profile?.referral_link;
    if (!link) return toast("Referral link unavailable.");
    const shareUrl = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent("Join me on RainDeals")}`;
    if (tg?.openTelegramLink) tg.openTelegramLink(shareUrl);
    else await copyText(link, "Referral link copied");
  }

  async function loadDepositsHistory(page = 0) {
    try {
      const data = await api(`/deposits?page=${page}`);
      openModal(`<p class="eyebrow">WALLET</p><h2 class="page-title" id="modal-title">Deposit history</h2>${data.deposits.length ? data.deposits.map(row => `<button class="deposit-history-row" data-deposit-detail="${Number(row.id)}"><span class="deposit-history-icon">${esc(row.icon || "💳")}</span><span><strong>${esc(row.invoice || `Deposit #${row.id}`)}</strong><small>${esc(row.network_label || row.network)} · ${esc(when(row.created_at))}</small></span><span class="status-pill ${esc(row.status)}">${esc(String(row.status).replaceAll("_", " "))}</span></button>`).join("") : `<div class="empty-state"><span>💳</span><strong>No deposits yet</strong><p>Your deposits will appear here.</p></div>`}<div class="search-row" style="grid-template-columns:1fr 1fr"><button class="secondary-button" data-deposit-page="${Math.max(0, page - 1)}" ${page <= 0 ? "disabled" : ""}>‹ Previous</button><button class="secondary-button" data-deposit-page="${page + 1}" ${!data.has_more ? "disabled" : ""}>Next ›</button></div><button class="primary-button wide-button" style="margin-top:10px" data-action="open-deposit-methods">＋ Add funds</button>`, "deposit-history");
    } catch (error) { toast(error.message); }
  }

  function openPromocode() {
    openModal(`<p class="eyebrow">RAINDEALS REWARDS</p><h2 class="page-title" id="modal-title">Redeem a promocode</h2><p class="muted-count">Enter a code provided by RainDeals.</p><label class="field-label">Promocode<input id="promocode-input" class="field-input" maxlength="32" autocomplete="off" placeholder="Enter code"></label><button class="primary-button wide-button" style="margin-top:12px" data-action="redeem-promocode">Redeem code</button>`, "promocode");
  }

  async function redeemPromocode() {
    const button = $('[data-action="redeem-promocode"]');
    const code = $("#promocode-input")?.value.trim();
    if (!code) { toast("Enter a promocode first."); return; }
    button.disabled = true;
    try {
      const result = await api("/promocodes/redeem", { method: "POST", body: JSON.stringify({ code }) });
      if (state.profile) state.profile.balance = result.balance;
      closeModal(); syncHeader(); toast(`Code redeemed · ${money(result.amount)} USDT added`); loadBalance();
    } catch (error) { toast(error.message); }
    finally { if (button) button.disabled = false; }
  }

  function renderProfile() {
    const p = state.profile || {};
    const initial = (p.name || "R").trim().charAt(0).toUpperCase();
    $("#profile-view").innerHTML = `
      <p class="eyebrow">YOUR ACCOUNT</p><h1 class="page-title">Profile</h1>
      <div class="panel-card"><div class="profile-head"><div class="avatar">${esc(initial)}</div><div><h2>${esc(p.name || "RainDeals customer")}</h2><p>${p.username ? `@${esc(p.username)}` : "RainDeals member"}</p></div></div>
        <div class="profile-stats"><div><small>Balance</small><strong>${money(p.balance)} USDT</strong></div><div><small>Orders</small><strong>${Number(p.orders || 0)}</strong></div><div><small>Total spent</small><strong>${money(p.spent)} USDT</strong></div><div><small>Deposited</small><strong>${money(p.deposited)} USDT</strong></div></div>
        <div class="row-line"><div><strong>Telegram ID</strong><small>RainDeals account</small></div><span class="amount">${esc(p.telegram_id)}</span></div>
      </div>
      <div class="panel-card referral-card"><h2 class="panel-heading">🤝 Invite friends</h2><p>Share your personal RainDeals referral link.</p><div class="referral-link"><code>${esc(p.referral_link || "Referral link unavailable")}</code><button class="text-button" data-action="copy-referral" ${p.referral_link ? "" : "disabled"}>Copy</button></div><div class="referral-stats"><span><strong>${Number(p.referrals || 0)}</strong><small>Friends</small></span><span><strong>${money(p.referral_earnings)} USDT</strong><small>Earned</small></span></div><button class="primary-button wide-button" style="margin-top:12px" data-action="share-referral" ${p.referral_link ? "" : "disabled"}>Share invite link</button></div>
      <button class="order-card" data-view="notifications"><div class="order-top"><strong>🔔 Notifications</strong><span>${Number(state.unread || 0)} unread ›</span></div><p>Recent payment, order, support, and account updates.</p></button>
      <button class="order-card" data-action="notification-preferences"><div class="order-top"><strong>⚙️ Notification preferences</strong><span>›</span></div><p>Choose which bot updates you receive.</p></button>
      <button class="secondary-button wide-button" data-action="open-bot">Support &amp; account help in Telegram</button>`;
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
      invalidateCatalogCache();
      const checkout = result.checkout || {};
      state.buyerInfoDraft = {};
      closeModal();
      await loadCart(true);
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
    if (target.dataset.depositNetwork) { beginDeposit(target.dataset.depositNetwork); return; }
    if (target.dataset.action === "full-product-details") { showFullProductDetails(target.dataset.productId); return; }
    if (target.dataset.depositDetail) { showDeposit(target.dataset.depositDetail); return; }
    if (target.dataset.depositPage !== undefined) { loadDepositsHistory(Number(target.dataset.depositPage)); return; }
    if (target.dataset.copy !== undefined) { copyText(target.dataset.copy, "Payment details copied"); return; }
    if (target.dataset.action === "open-deposit-methods") { showDepositMethods(); return; }
    if (target.dataset.action === "change-payment-method") { showDepositMethods(); return; }
    if (target.dataset.action === "load-deposits") { loadDepositsHistory(0); return; }
    if (target.dataset.action === "submit-deposit-reference") { submitDepositReference(target.dataset.depositId); return; }
    if (target.dataset.action === "open-promocode") { openPromocode(); return; }
    if (target.dataset.action === "redeem-promocode") { redeemPromocode(); return; }
    if (target.dataset.action === "copy-referral") { copyText(state.profile?.referral_link || "", "Referral link copied"); return; }
    if (target.dataset.action === "share-referral") { shareReferral(); return; }
    if (target.dataset.action === "notification-preferences") { openBot(); toast("Manage notification preferences in the RainDeals bot."); return; }
    if (target.dataset.action === "refresh-catalog") { refreshProducts(); return; }
    if (target.dataset.cartQty) {
      const item = state.cart.items.find(row => row.product_id === Number(target.dataset.cartQty));
      if (!item) return;
      const next = Number(item.quantity) + Number(target.dataset.delta);
      const product = state.products.find(row => row.id === Number(item.product_id));
      if (next > 0 && product && next > Number(product.sellable_stock)) { toast(`Only ${product.sellable_stock} units are currently available.`); return; }
      changeCart(item.product_id, Math.max(0,next)); return;
    }
    if (target.dataset.cartRemove) { changeCart(target.dataset.cartRemove, 0); return; }
    if (target.dataset.order) { showOrder(target.dataset.order); return; }
    if (target.dataset.notification) { showNotification(target.dataset.notification); return; }
    if (target.dataset.noticePage) { loadNotifications(Number(target.dataset.noticePage)); return; }
    if (target.id === "cart-open") { renderCart(false); return; }
    if (target.id === "notify-open") { setView("notifications"); return; }
    if (target.id === "stock-toggle") { state.stockOnly = !state.stockOnly; target.setAttribute("aria-pressed", String(state.stockOnly)); renderStore(); return; }
    if (target.id === "sort-toggle") { state.sort = state.sort === "featured" ? "price" : state.sort === "price" ? "name" : "featured"; renderStore(); return; }
    if (target.id === "clear-search") { $("#search").value = ""; state.search = ""; target.classList.add("hidden"); renderStore(); return; }
    if (target.id === "open-bot") { openBot(); return; }
    if (target.id === "catalog-refresh") { refreshProducts(); return; }
    if (target.dataset.product) {
      const product = state.products.find(row => row.id === Number(target.dataset.product));
      if (product) openProduct(product);
    }
  }

  function bind() {
    document.addEventListener("click", onClick);
    document.addEventListener("input", event => {
      const input = event.target.closest("[data-buyer-product]");
      if (!input) return;
      const productId = input.dataset.buyerProduct;
      state.buyerInfoDraft[productId] ||= {};
      state.buyerInfoDraft[productId][input.dataset.buyerLabel] = input.value;
    });
    $("#search").addEventListener("input", event => {
      state.search = event.target.value;
      $("#clear-search").classList.toggle("hidden", !state.search);
      renderStore();
      const input = $("#search"); input.focus(); input.setSelectionRange(input.value.length,input.value.length);
    });
    tg?.BackButton?.onClick(() => state.modal ? closeModal() : setView("store"));
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && state.currentView === "store" && Date.now() - state.catalogLoadedAt > 90_000) {
        loadProducts(state.freebies, { preserveCategory: true, quiet: true }).catch(() => {});
      }
    });
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
      state.botUsername = data.main_bot_username || "";
      state.depositMethods = data.deposit_methods || [];
      syncHeader();
      $("#loading").classList.add("hidden");
      $("#main").classList.remove("hidden"); $("#bottom-nav").classList.remove("hidden");
      renderProfile();
      loadProducts(false).catch(error => {
        $("#product-grid").innerHTML = `<div class="soft-note">${esc(error.message)} <button class="text-button" data-action="refresh-catalog">Try again</button></div>`;
      });
      loadCart().catch(error => { state.cartReady = false; toast(error.message); });
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
