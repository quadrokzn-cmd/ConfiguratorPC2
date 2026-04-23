// Этап 6.2: клиентская часть спецификации проекта.
//
// - Ставит/снимает галочки «в спецификацию».
// - Меняет количество в активной строке.
// - Перерисовывает таблицу спецификации без перезагрузки страницы.
// - Вешает alert-заглушки на кнопки экспорта (этап 7).
//
// Без фреймворков. CSRF-токен — из <meta name="csrf-token">,
// передаём в заголовке X-CSRF-Token.

(function () {
  'use strict';

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var pidMeta = document.querySelector('meta[name="kt-project-id"]');
  if (!csrfMeta || !pidMeta) return;

  var CSRF = csrfMeta.content;
  var PROJECT_ID = pidMeta.content;

  function rub(value) {
    var n = Math.round(Number(value) || 0);
    // Разделитель разрядов — неразрывный пробел, как в шаблонах.
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }

  function fmtUsd(value) {
    return '$' + Math.round(Number(value) || 0).toString();
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  async function post(url, payload) {
    var r = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': CSRF
      },
      body: JSON.stringify(payload)
    });
    if (!r.ok) {
      var detail = '';
      try { detail = (await r.json()).detail || ''; } catch (e) {}
      throw new Error('spec-fail: ' + r.status + ' ' + detail);
    }
    return r.json();
  }

  function renderSpec(data) {
    var tbody = document.getElementById('kt-spec-tbody');
    var total = document.getElementById('kt-spec-total');
    var empty = document.getElementById('kt-spec-empty');
    var wrap = document.getElementById('kt-spec-wrap');
    if (!tbody || !total) return;

    if (!data.items || data.items.length === 0) {
      tbody.innerHTML = '';
      if (empty) empty.classList.remove('hidden');
      if (wrap) wrap.classList.add('hidden');
    } else {
      if (empty) empty.classList.add('hidden');
      if (wrap) wrap.classList.remove('hidden');
      var html = '';
      for (var i = 0; i < data.items.length; i++) {
        var it = data.items[i];
        html +=
          '<tr data-item-id="' + it.id + '">' +
            '<td class="px-3 py-3 text-zinc-500">' + it.position + '</td>' +
            '<td class="px-3 py-3 text-zinc-100 break-words">' +
              escapeHtml(it.display_name) +
            '</td>' +
            '<td class="px-3 py-3 text-right text-zinc-300">' + it.quantity + '</td>' +
            '<td class="px-3 py-3 text-right text-zinc-300 whitespace-nowrap">' +
              fmtUsd(it.unit_usd) +
              '<div class="text-xs text-zinc-500">' + rub(it.unit_rub) + ' ₽</div>' +
            '</td>' +
            '<td class="px-3 py-3 text-right text-zinc-100 whitespace-nowrap">' +
              fmtUsd(it.total_usd) +
              '<div class="text-xs text-zinc-400">' + rub(it.total_rub) + ' ₽</div>' +
            '</td>' +
          '</tr>';
      }
      tbody.innerHTML = html;
    }
    total.innerHTML =
      fmtUsd(data.total_usd) +
      '<div class="text-xs text-zinc-400 font-normal">' +
        rub(data.total_rub) + ' ₽</div>';
  }

  // На /query/{id} нет панели спецификации — рендер пропустим,
  // но сохранение всё равно отработает.
  function qtyInputFor(queryId, manufacturer) {
    return document.querySelector(
      '.kt-spec-qty[data-query-id="' + queryId + '"]' +
      '[data-manufacturer="' + manufacturer + '"]'
    );
  }

  async function onCheckChange(cb) {
    var queryId = parseInt(cb.dataset.queryId, 10);
    var mfg = cb.dataset.manufacturer;
    var qtyEl = qtyInputFor(queryId, mfg);
    var qty = qtyEl ? parseInt(qtyEl.value, 10) || 1 : 1;

    cb.disabled = true;
    try {
      if (cb.checked) {
        if (qtyEl) qtyEl.disabled = false;
        var data = await post('/project/' + PROJECT_ID + '/select', {
          query_id: queryId,
          variant_manufacturer: mfg,
          quantity: qty
        });
        renderSpec(data);
      } else {
        if (qtyEl) qtyEl.disabled = true;
        var data2 = await post('/project/' + PROJECT_ID + '/deselect', {
          query_id: queryId,
          variant_manufacturer: mfg
        });
        renderSpec(data2);
      }
    } catch (e) {
      console.error(e);
      // Откатим состояние галочки, чтобы не расходилось с сервером.
      cb.checked = !cb.checked;
      if (qtyEl) qtyEl.disabled = !cb.checked;
      alert('Не удалось обновить спецификацию. Попробуйте ещё раз.');
    } finally {
      cb.disabled = false;
    }
  }

  async function onQtyChange(input) {
    var queryId = parseInt(input.dataset.queryId, 10);
    var mfg = input.dataset.manufacturer;
    var qty = parseInt(input.value, 10);
    if (!qty || qty < 1) {
      input.value = '1';
      qty = 1;
    }
    input.disabled = true;
    try {
      var data = await post('/project/' + PROJECT_ID + '/update_quantity', {
        query_id: queryId,
        variant_manufacturer: mfg,
        quantity: qty
      });
      renderSpec(data);
    } catch (e) {
      console.error(e);
      alert('Не удалось изменить количество. Попробуйте ещё раз.');
    } finally {
      input.disabled = false;
    }
  }

  document.querySelectorAll('.kt-spec-check').forEach(function (cb) {
    cb.addEventListener('change', function () { onCheckChange(cb); });
  });

  document.querySelectorAll('.kt-spec-qty').forEach(function (input) {
    input.addEventListener('change', function () { onQtyChange(input); });
    // Enter — обычное поведение формы, но формы нет, поэтому
    // снимаем Enter-сабмит и отправляем явно.
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        input.blur();
      }
    });
  });

  document.querySelectorAll('.kt-stub').forEach(function (btn) {
    btn.addEventListener('click', function () {
      alert('Функция появится в Этапе 7.');
    });
  });
})();
