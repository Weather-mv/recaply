// Clipforge — shared page behaviour: theme switch, CSRF on every request,
// account menu, flash messages, plan limits and scroll reveals.
(() => {
  const root = document.documentElement;
  const token = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  // ---------- light / dark ----------
  function setTheme(t) {
    root.dataset.theme = t;
    try { localStorage.setItem("rc_theme", t); } catch (_) {}
    document.dispatchEvent(new CustomEvent("themechange", { detail: t }));
  }
  window.RecaplyTheme = { set: setTheme, get: () => root.dataset.theme };
  // follow the system until the user picks
  try {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", e => { if (!localStorage.getItem("rc_theme")) {
      root.dataset.theme = e.matches ? "dark" : "light";
      document.dispatchEvent(new CustomEvent("themechange", { detail: root.dataset.theme })); } });
  } catch (_) {}

  // ---------- CSRF: every same-site POST/PUT/DELETE carries the session token ----------
  const sameSite = url => { try { return new URL(url, location.href).origin === location.origin; } catch (_) { return false; } };
  const unsafe = m => !/^(GET|HEAD|OPTIONS)$/i.test(m || "GET");
  const origFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const method = init.method || (typeof input === "object" && input.method) || "GET";
    if (sameSite(url) && unsafe(method)) {
      const h = new Headers(init.headers || (typeof input === "object" ? input.headers : undefined) || {});
      h.set("X-CSRF-Token", token());
      init = { ...init, headers: h };
    }
    return origFetch(input, init).then(r => {
      if (sameSite(url)) handleLimits(r);
      return r;
    });
  };
  const xOpen = XMLHttpRequest.prototype.open, xSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) { this._rcM = method; this._rcU = url; return xOpen.call(this, method, url, ...rest); };
  XMLHttpRequest.prototype.send = function (body) {
    if (unsafe(this._rcM) && sameSite(this._rcU)) this.setRequestHeader("X-CSRF-Token", token());
    return xSend.call(this, body);
  };

  // Logged out in another tab -> back to the login; plan used up -> offer the upgrade.
  function handleLimits(r) {
    if (r.status === 401) {
      r.clone().json().then(j => { if (j && j.login) location.href = `${j.login}?next=${encodeURIComponent(location.pathname)}`; }).catch(() => {});
    } else if (r.status === 402) {
      r.clone().json().then(j => showUpgrade(j && j.error)).catch(() => {});
    } else if (r.status === 503) {                 // the Windows app is offline
      r.clone().json().then(j => j && j.error && flash(j.error, "error")).catch(() => {});
    }
  }
  const esc = v => String(v).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  function showUpgrade(msg) {
    flash(`${esc(msg || "Your plan has run out.")} <a href="/pricing">See plans</a>`, "error", true);
  }

  // ---------- flash messages ----------
  function flash(html, kind = "", trusted = false) {
    let box = document.querySelector(".flashes");
    if (!box) { box = document.createElement("div"); box.className = "flashes"; document.body.appendChild(box); }
    const el = document.createElement("div");
    el.className = `flash ${kind}`;
    el.setAttribute("role", "status");
    const span = document.createElement("span");
    if (trusted) span.innerHTML = html; else span.textContent = html;
    const x = document.createElement("button");
    x.type = "button"; x.setAttribute("aria-label", "Dismiss"); x.textContent = "×";
    el.append(span, x);
    box.appendChild(el);
    setTimeout(() => el.remove(), 9000);
  }
  window.RecaplyFlash = flash;

  document.addEventListener("click", e => {
    const t = e.target;
    if (t.closest(".theme-toggle")) setTheme(root.dataset.theme === "dark" ? "light" : "dark");
    const x = t.closest(".flash button"); if (x) x.closest(".flash").remove();
    // account menu
    const menu = t.closest(".menu");
    document.querySelectorAll(".menu.open").forEach(m => { if (m !== menu) m.classList.remove("open"); });
    if (t.closest(".menu > .avatar")) {
      menu.classList.toggle("open");
      menu.querySelector(".avatar").setAttribute("aria-expanded", menu.classList.contains("open"));
    }
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") document.querySelectorAll(".menu.open").forEach(m => m.classList.remove("open")); });

  document.addEventListener("DOMContentLoaded", () => {
    // forms posted the old way get the token as a hidden field
    document.querySelectorAll('form[method="post" i]').forEach(f => {
      if (!sameSite(f.action || location.href) || f.querySelector('input[name="csrf_token"]')) return;
      const i = document.createElement("input");
      i.type = "hidden"; i.name = "csrf_token"; i.value = token();
      f.appendChild(i);
    });
    setTimeout(() => document.querySelectorAll(".flashes .flash").forEach(f => f.remove()), 9000);

    // scroll reveal
    const els = document.querySelectorAll(".reveal");
    if (!("IntersectionObserver" in window)) { els.forEach(el => el.classList.add("in")); return; }
    const io = new IntersectionObserver(entries => entries.forEach(e => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
    els.forEach(el => io.observe(el));
  });

  // ---------- Windows app: pick up plan changes made on the website ----------
  // (after upgrading in the browser, coming back to the window updates the plan pill)
  const D = window.RECAPLY_DESKTOP;
  if (D) {
    let last = 0;
    addEventListener("focus", async () => {
      if (Date.now() - last < 10000) return;
      last = Date.now();
      try {
        const r = await origFetch("/api/plan");
        const { ent } = r.ok ? await r.json() : {};
        if (!ent || (ent.plan === D.plan && ent.left === D.left && !ent.offline)) return;
        const changed = ent.plan !== D.plan;
        Object.assign(D, { plan: ent.plan, left: ent.left });
        const pill = document.querySelector(".usage");
        if (pill) {
          pill.querySelector(".plan").className = `plan ${ent.plan}`;
          pill.querySelector(".plan").textContent = ent.name;
          pill.querySelector(".n").textContent = ent.offline ? "Offline" : ent.limit == null ? "Unlimited"
            : `${ent.left} clip${ent.left === 1 ? "" : "s"} left`;
          pill.classList.toggle("low", ent.limit != null && ent.left <= 2);
        }
        if (changed) flash(`You're on ${ent.name} now.`, "ok");
      } catch (_) {}
    });
  }
})();
