// Mobile order entry. Orders that cannot be sent are queued in localStorage and re-sent when the network returns.
(() => {
  const D = JSON.parse(document.getElementById("data").textContent), $ = s => document.querySelector(s);
  const KEY = "order-queue", box = $("#lines"), msg = $("#msg");
  const queue = () => { try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { return []; } };
  const save = q => { try { localStorage.setItem(KEY, JSON.stringify(q)); } catch {} showQueue(); };
  const uid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now() + "-" + Math.random().toString(16).slice(2));
  function say(t, cls) { msg.className = "flash " + (cls || ""); msg.textContent = t; msg.hidden = !t; }
  function showQueue() { const n = queue().length; $("#queued").hidden = !n; $("#queued").textContent = n + " order(s) saved on this phone, waiting to be sent"; }

  function addLine() {
    const r = document.createElement("div"); r.className = "line";
    r.innerHTML = `<select class="p">${D.products.map(p => `<option value="${p.id}">${p.name} (Rs ${p.price})</option>`).join("")}</select>
      <input class="q" type="number" min="1" step="1" inputmode="numeric" placeholder="Qty"><select class="u"></select><button type="button" class="alt x" aria-label="Remove line">×</button>`;
    const p = r.querySelector(".p"), u = r.querySelector(".u");
    const units = () => { u.innerHTML = D.products.find(x => x.id == p.value).units.map(x => `<option>${x}</option>`).join(""); };
    p.onchange = () => { units(); total(); }; units();
    r.querySelector(".q").oninput = total; u.onchange = total; r.querySelector(".x").onclick = () => { r.remove(); total(); };
    box.appendChild(r);
  }
  function total() { // display only; the server recomputes in integer paise
    let t = 0;
    box.querySelectorAll(".line").forEach(r => { const p = D.products.find(x => x.id == r.querySelector(".p").value), q = +r.querySelector(".q").value || 0;
      t += q * (r.querySelector(".u").value === "carton" ? 20 : 1) * parseFloat(p.price); });
    $("#total").textContent = "Estimated total before tax: Rs " + t.toFixed(2);
  }
  async function send(o) {
    const r = await fetch("/api/orders", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(o), credentials: "same-origin" });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  }
  async function flush() {
    let q = queue(), keep = [];
    for (const o of q) {
      try { const r = await send(o); if (r.status === 401 || r.status >= 500) keep.push(o); else if (!r.body.ok) say("A saved order was rejected: " + r.body.error, "err"); }
      catch { keep.push(o); }
    }
    if (keep.length !== q.length) say("Saved orders sent.", ""); save(keep);
  }
  $("#add").onclick = addLine; addLine();
  $("#form").onsubmit = async e => {
    e.preventDefault();
    const lines = [...box.querySelectorAll(".line")].map(r => ({ item_id: r.querySelector(".p").value, qty: r.querySelector(".q").value, unit: r.querySelector(".u").value })).filter(l => +l.qty > 0);
    if (!$("#customer").value || !lines.length) return say("Pick a customer and enter at least one quantity.", "err");
    const o = { client_ref: uid(), customer_id: $("#customer").value, note: $("#note").value, lines };
    try {
      const r = await send(o);
      if (r.body.ok) { say("Order " + r.body.order_no + " sent (" + r.body.total + " before tax).", ""); box.innerHTML = ""; addLine(); total(); $("#note").value = ""; }
      else if (r.status === 401 || r.status >= 500) throw new Error("retry");
      else say(r.body.error || "Could not send the order", "err");
    } catch { save([...queue(), o]); say("No connection. Order saved on this phone and will be sent automatically.", ""); box.innerHTML = ""; addLine(); total(); }
  };
  addEventListener("online", flush); showQueue(); flush();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
})();
