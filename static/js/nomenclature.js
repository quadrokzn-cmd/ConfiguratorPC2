// /nomenclature — inline-редактирование cost_base + модалка атрибутов.
// Этап 9a слияния QT↔C-PC2. Никаких HTMX/Alpine — Vanilla JS + fetch.
//
// Контракты:
//   POST /nomenclature/{id}/cost-base  (form: cost_base_rub, csrf_token)
//   POST /nomenclature/{id}/attrs      (form: <attr fields>, csrf_token)
//   POST /nomenclature/{id}/ktru       (form: ktru_codes_text, csrf_token)
//   POST /nomenclature/{id}/enrich     (form: csrf_token)
//
// Все ответы — JSON {ok: true, ...} либо {ok: false, detail}.

(function () {
  'use strict';

  const NA = 'n/a';

  // CSRF берём из любого input[name=csrf_token] на странице.
  function csrfToken() {
    const el = document.querySelector('input[name="csrf_token"]');
    return el ? el.value : '';
  }

  function flashRow(rowEl, msg, isError) {
    const flash = rowEl.querySelector('.row-flash');
    if (!flash) return;
    flash.textContent = msg || '';
    flash.style.color = isError ? 'var(--color-danger-500, #ef4444)' : 'var(--color-success-500, #10b981)';
    if (msg) {
      setTimeout(() => { flash.textContent = ''; }, 2500);
    }
  }

  // ---- Inline cost_base ------------------------------------------------
  document.querySelectorAll('.cost-base-input').forEach((input) => {
    let original = input.value;

    input.addEventListener('focus', () => {
      original = input.value;
    });

    input.addEventListener('blur', () => {
      if (input.value === original) return;
      const tr = input.closest('tr');
      const id = tr.dataset.rowId;
      const fd = new FormData();
      fd.append('cost_base_rub', input.value || '');
      fd.append('csrf_token', csrfToken());

      fetch(`/nomenclature/${id}/cost-base`, {
        method: 'POST',
        body:   fd,
        headers: { 'X-CSRF-Token': csrfToken() },
      })
        .then((r) => r.json().then((j) => ({ status: r.status, body: j })))
        .then(({ status, body }) => {
          if (status === 200 && body.ok) {
            original = input.value;
            flashRow(tr, 'cost_base сохранён', false);
          } else {
            flashRow(tr, body.detail || 'ошибка', true);
            input.value = original;
          }
        })
        .catch((err) => {
          flashRow(tr, 'сеть: ' + err.message, true);
          input.value = original;
        });
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        input.blur();
      } else if (e.key === 'Escape') {
        input.value = original;
        input.blur();
      }
    });
  });

  // ---- Modal: attrs edit ----------------------------------------------
  const dialog = document.getElementById('attrs-dialog');
  const form   = document.getElementById('attrs-form');
  const subtitle = document.getElementById('attrs-subtitle');
  const errorEl = document.getElementById('attrs-error');

  function openAttrsDialog(rowEl) {
    if (!dialog || !form) return;
    const id   = rowEl.dataset.rowId;
    const sku  = rowEl.dataset.sku;
    const mpn  = rowEl.dataset.mpn || '—';
    const brand = rowEl.dataset.brand || '—';
    const name = rowEl.dataset.name || '';
    const ktruCsv = rowEl.dataset.ktru || '';
    let attrs = {};
    try {
      attrs = JSON.parse(rowEl.dataset.attrs || '{}') || {};
    } catch (e) {
      attrs = {};
    }

    document.getElementById('attrs-row-id').value = id;
    subtitle.textContent = `${name} · SKU ${sku} · MPN ${mpn} · бренд ${brand}`;
    errorEl.textContent = '';

    // Заполняем простые поля.
    [
      'print_speed_ppm', 'colorness', 'max_format', 'duplex', 'resolution_dpi',
      'usb', 'starter_cartridge_pages', 'print_technology',
    ].forEach((key) => {
      const el = form.elements[key];
      if (!el) return;
      const val = attrs[key];
      if (val === undefined || val === null) {
        el.value = NA;
      } else if (Array.isArray(val)) {
        el.value = NA;  // защита, не должно попадать сюда
      } else {
        el.value = String(val);
      }
    });

    // network_interface: список чекбоксов.
    const nets = form.querySelectorAll('input[type="checkbox"][name="network_interface"]');
    const netVal = attrs.network_interface;
    nets.forEach((cb) => {
      cb.checked = Array.isArray(netVal) && netVal.includes(cb.value);
    });

    // KTRU.
    form.elements.ktru_codes_text.value = ktruCsv;

    if (typeof dialog.showModal === 'function') {
      dialog.showModal();
    } else {
      dialog.setAttribute('open', '');
    }
  }

  function closeAttrsDialog() {
    if (!dialog) return;
    if (typeof dialog.close === 'function') {
      dialog.close();
    } else {
      dialog.removeAttribute('open');
    }
  }

  document.querySelectorAll('.attrs-edit-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tr = btn.closest('tr');
      if (tr) openAttrsDialog(tr);
    });
  });

  const cancel1 = document.getElementById('attrs-cancel');
  const cancel2 = document.getElementById('attrs-cancel-2');
  if (cancel1) cancel1.addEventListener('click', closeAttrsDialog);
  if (cancel2) cancel2.addEventListener('click', closeAttrsDialog);

  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const id = document.getElementById('attrs-row-id').value;
      if (!id) { closeAttrsDialog(); return; }

      // Собираем FormData для /attrs (без ktru_codes_text — там оно
      // отдельным эндпоинтом).
      const fd = new FormData();
      fd.append('csrf_token', csrfToken());
      [
        'print_speed_ppm', 'colorness', 'max_format', 'duplex', 'resolution_dpi',
        'usb', 'starter_cartridge_pages', 'print_technology',
      ].forEach((key) => {
        const el = form.elements[key];
        fd.append(key, el ? el.value : NA);
      });
      const nets = form.querySelectorAll('input[type="checkbox"][name="network_interface"]');
      nets.forEach((cb) => {
        if (cb.checked) fd.append('network_interface', cb.value);
      });

      fetch(`/nomenclature/${id}/attrs`, {
        method: 'POST',
        body:   fd,
        headers: { 'X-CSRF-Token': csrfToken() },
      })
        .then((r) => r.json().then((j) => ({ status: r.status, body: j })))
        .then(({ status, body }) => {
          if (status !== 200 || !body.ok) {
            errorEl.textContent = (body && body.detail) || `ошибка ${status}`;
            return Promise.reject(new Error(body && body.detail || `${status}`));
          }
          // Дальше — KTRU отдельным запросом.
          const fd2 = new FormData();
          fd2.append('csrf_token', csrfToken());
          fd2.append('ktru_codes_text', form.elements.ktru_codes_text.value || '');
          return fetch(`/nomenclature/${id}/ktru`, {
            method: 'POST',
            body:   fd2,
            headers: { 'X-CSRF-Token': csrfToken() },
          });
        })
        .then(() => {
          closeAttrsDialog();
          // Перезагружаем страницу, чтобы UI отразил новые атрибуты в строке.
          window.location.reload();
        })
        .catch((err) => {
          if (errorEl && !errorEl.textContent) {
            errorEl.textContent = 'не удалось сохранить: ' + err.message;
          }
        });
    });
  }

  // ---- Enrich: pending в очередь Claude Code --------------------------
  document.querySelectorAll('.enrich-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tr = btn.closest('tr');
      const id = tr.dataset.rowId;
      const fd = new FormData();
      fd.append('csrf_token', csrfToken());

      btn.disabled = true;
      fetch(`/nomenclature/${id}/enrich`, {
        method: 'POST',
        body:   fd,
        headers: { 'X-CSRF-Token': csrfToken() },
      })
        .then((r) => r.json().then((j) => ({ status: r.status, body: j })))
        .then(({ status, body }) => {
          if (status === 200 && body.ok) {
            flashRow(tr, body.msg || 'в очереди', false);
          } else {
            flashRow(tr, (body && body.detail) || 'ошибка', true);
          }
        })
        .catch((err) => flashRow(tr, 'сеть: ' + err.message, true))
        .finally(() => { btn.disabled = false; });
    });
  });
})();
