// Emparejamiento: si SpotPad contesta 401 «falta emparejar», muestra una pantalla para escribir el código de
// 4 dígitos (está en el menú de SpotPad, en la Mac). Lo comparten la botonera, la vista del artista y el carímetro.
(function () {
  const rawFetch = window.fetch.bind(window);
  let overlay = null, esperando = [];

  function mostrar() {
    return new Promise(ok => {
      esperando.push(ok);
      if (overlay) return;
      overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;display:grid;place-items:center;padding:24px;' +
        'background:rgba(10,10,12,.94);color:#ecebe6;font:16px -apple-system,system-ui,sans-serif;text-align:center';
      overlay.innerHTML = '<div style="max-width:340px;width:100%"><div style="font-size:22px;font-weight:650;margin-bottom:6px">' +
        'Conectar con SpotPad</div><p style="color:#a9a8b0;margin:0 0 18px">Escribí el código de 4 dígitos que aparece ' +
        'en el menú de SpotPad, en la Mac.</p><input id="pairCode" inputmode="numeric" pattern="[0-9]*" maxlength="4" ' +
        'autocomplete="one-time-code" style="width:100%;box-sizing:border-box;font:700 40px ui-monospace,Menlo,monospace;' +
        'letter-spacing:.4em;text-align:center;padding:12px 8px;border-radius:14px;border:1px solid #3a3a42;' +
        'background:#1b1b1f;color:#E8A33D"><div id="pairMsg" style="min-height:22px;margin-top:12px;color:#ff8a80"></div></div>';
      document.body.append(overlay);
      const inp = overlay.querySelector('#pairCode'), msg = overlay.querySelector('#pairMsg');
      inp.focus();
      let busy = false;
      inp.addEventListener('input', async () => {
        inp.value = inp.value.replace(/\D/g, '').slice(0, 4);
        if (inp.value.length < 4 || busy) return;
        busy = true; msg.textContent = '';
        try {
          const r = await rawFetch('/api/pair', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                  body: JSON.stringify({codigo: inp.value})});
          const j = await r.json().catch(() => ({}));
          if (r.ok && j.ok) { overlay.remove(); overlay = null; const w = esperando; esperando = []; w.forEach(f => f()); return; }
          msg.textContent = j.error || 'No pude emparejar'; inp.value = '';
        } catch { msg.textContent = 'No encuentro SpotPad: ¿está abierto en la Mac?'; }
        busy = false; inp.focus();
      });
    });
  }

  window.fetch = async function (input, init) {
    const r = await rawFetch(input, init);
    if (r.status !== 401) return r;
    let j = null; try { j = await r.clone().json(); } catch {}
    if (!j || !j.pair) return r;
    await mostrar();                       // espera el código y repite el pedido una vez
    return rawFetch(input, init);
  };

  // QR de /conectar: ?par=1234 → se empareja solo y se limpia la dirección
  try {
    const u = new URL(location.href), par = u.searchParams.get('par');
    if (par) {
      u.searchParams.delete('par'); history.replaceState(null, '', u.pathname + (u.search || '') + u.hash);
      rawFetch('/api/pair', {method: 'POST', headers: {'Content-Type': 'application/json'},
                             body: JSON.stringify({codigo: par})}).then(() => location.reload()).catch(() => {});
    }
  } catch {}
})();
