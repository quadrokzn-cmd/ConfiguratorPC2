// /auctions/{reg_number} — клиентская логика карточки лота.
// Этап 9a-fixes: модалка характеристик SKU и toast «Сохранено» для
// flash_info со страницы.
//
// Контракт:
//   GET /auctions/sku/{nomenclature_id}/details — HTML-фрагмент
//   с таблицей attrs_jsonb. Открывается в нативном <dialog>.
//
// Без HTMX/Alpine — Vanilla JS + fetch.

(function () {
  'use strict';

  const dialog = document.getElementById('sku-details-dialog');
  const body   = document.getElementById('sku-details-body');
  const closeBtn = document.getElementById('sku-details-close');

  function openDialog() {
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }

  function closeDialog() {
    if (!dialog) return;
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  }

  if (closeBtn) closeBtn.addEventListener('click', closeDialog);

  // Клик по подложке (область вне inner-card) — закрытие.
  if (dialog) {
    dialog.addEventListener('click', function (e) {
      if (e.target === dialog) closeDialog();
    });
  }

  function loadSkuDetails(skuId) {
    if (!body) return;
    body.innerHTML = '<p class="text-ink-muted text-small">Загрузка…</p>';
    openDialog();
    fetch('/auctions/sku/' + encodeURIComponent(skuId) + '/details', {
      method:  'GET',
      headers: { 'Accept': 'text/html' },
      credentials: 'same-origin',
    })
      .then(function (r) {
        if (!r.ok) {
          throw new Error(r.status === 404 ? 'SKU не найден' : ('HTTP ' + r.status));
        }
        return r.text();
      })
      .then(function (html) { body.innerHTML = html; })
      .catch(function (err) {
        body.innerHTML =
          '<p class="text-danger-500 text-small">Не удалось загрузить: ' +
          (err && err.message ? err.message : 'ошибка') + '</p>';
      });
  }

  document.querySelectorAll('.sku-details-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      var id = btn.dataset.skuId;
      if (id) loadSkuDetails(id);
    });
  });
})();
