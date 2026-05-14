// Этап 6.2/9А.2.1/9А.2.3/9А.2.5: клиентская часть страницы проекта.
//
// - Ставит/снимает галочки «в спецификацию».
// - Меняет количество в активной строке.
// - Перерисовывает таблицу спецификации без перезагрузки страницы.
// - 9А.2.5: после reoptimize — короткий toast + компактная модалка
//   «Результат пересборки» с карточками-строками (имя, старая→новая цена,
//   количество изменённых компонентов, раскрывающиеся детали). После
//   закрытия модалки — page reload с восстановлением scroll-позиции
//   через sessionStorage, чтобы UI сразу показал актуальные конфигурации.
// - 9А.2.3: toast'ы — в правом нижнем углу (см. .kt-toast-container в CSS).
//
// Без фреймворков. CSRF — из <meta name="csrf-token">.

(function () {
  'use strict';

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var pidMeta = document.querySelector('meta[name="kt-project-id"]');
  if (!csrfMeta || !pidMeta) return;

  var CSRF = csrfMeta.content;
  var PROJECT_ID = pidMeta.content;
  var SCROLL_STORAGE_KEY = 'kt-reopt-scroll-' + PROJECT_ID;
  var DEFERRED_TOAST_KEY = 'kt-reopt-toast-' + PROJECT_ID;

  function fmtUsd(value) {
    return '$' + Math.round(Number(value) || 0).toString();
  }

  function rub(value) {
    var n = Math.round(Number(value) || 0);
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Курс из плашки в sidebar — для пересчёта USD→RUB при перерисовке.
  function readSidebarRate() {
    var el = document.querySelector('.kt-fx-rate');
    if (!el) return null;
    var m = (el.textContent || '').match(/(\d+(?:[.,]\d+)?)/);
    if (!m) return null;
    var v = parseFloat(m[1].replace(',', '.'));
    return isFinite(v) && v > 0 ? v : null;
  }

  function fmtRubFromUsd(usd) {
    var rate = readSidebarRate();
    if (rate == null) return '';
    return rub(usd * rate);
  }

  var lastSpec = { items: {}, total_usd: null, initialized: false };

  function flip(el) {
    if (!el) return;
    el.classList.remove('num-flip');
    void el.offsetWidth;
    el.classList.add('num-flip');
  }

  async function post(url, payload) {
    var r = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': CSRF
      },
      body: JSON.stringify(payload || {})
    });
    if (!r.ok) {
      var detail = '';
      try { detail = (await r.json()).detail || ''; } catch (e) {}
      throw new Error('spec-fail: ' + r.status + ' ' + detail);
    }
    return r.json();
  }

  function fmtRecalcStamp(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    function pad(n) { return n < 10 ? '0' + n : '' + n; }
    return 'обновлено ' + pad(d.getDate()) + '.' + pad(d.getMonth() + 1) +
      ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  var SPARKLE_ICON_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" ' +
    'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" ' +
    'stroke-linejoin="round" viewBox="0 0 24 24">' +
    '<path d="M9.94 14.06 7 21l-2.94-6.94L-2 12l6.06-2.06L7 3l2.94 6.94L17 12z" transform="translate(3,0)"/>' +
    '<path d="M19 3v4M21 5h-4M19 17v4M21 19h-4"/></svg>';

  function renderSpec(data) {
    var tbody = document.getElementById('kt-spec-tbody');
    var total = document.getElementById('kt-spec-total');
    var empty = document.getElementById('kt-spec-empty');
    var wrap = document.getElementById('kt-spec-wrap');
    if (!tbody || !total) return;

    var newItemsMap = {};
    var changedIds = {};

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
        var prev = lastSpec.items[it.id];
        if (!prev || prev.quantity !== it.quantity || prev.total_usd !== it.total_usd) {
          changedIds[it.id] = true;
        }
        newItemsMap[it.id] = {
          quantity: it.quantity,
          total_usd: it.total_usd,
          total_rub: it.total_rub
        };
        var stampHtml = '';
        if (it.recalculated_at) {
          stampHtml =
            '<div class="text-caption text-ink-muted kt-spec-recalc-stamp">' +
              escapeHtml(fmtRecalcStamp(it.recalculated_at)) +
            '</div>';
        }
        // 9А.2.3: цены RUB пересчитываем через курс из sidebar; если плашки
        // нет (null) — берём server-side it.unit_rub / it.total_rub.
        var unitRubStr  = fmtRubFromUsd(it.unit_usd)  || rub(it.unit_rub);
        var totalRubStr = fmtRubFromUsd(it.total_usd) || rub(it.total_rub);
        html +=
          '<tr data-item-id="' + it.id + '">' +
            '<td class="px-2 py-2.5 text-ink-muted tabular-nums align-top">' + it.position + '</td>' +
            '<td class="px-2 py-2.5 text-ink-primary break-words">' +
              '<div>' + escapeHtml(it.display_name) + '</div>' +
              '<div class="text-caption text-ink-muted tabular-nums">' +
                fmtUsd(it.unit_usd) + ' / шт · ' + unitRubStr + ' ₽' +
              '</div>' +
              stampHtml +
            '</td>' +
            '<td class="px-2 py-2.5 text-right text-ink-secondary tabular-nums kt-spec-qty-cell align-top">' +
              it.quantity +
            '</td>' +
            '<td class="px-2 py-2.5 text-right text-ink-primary tabular-nums whitespace-nowrap kt-spec-sum-cell align-top">' +
              fmtUsd(it.total_usd) +
              '<div class="text-caption text-ink-secondary font-normal">' +
                totalRubStr + ' ₽</div>' +
            '</td>' +
            '<td class="px-1 py-2.5 text-right align-top">' +
              '<button type="button" class="kt-spec-recalc-row p-1 rounded ' +
                'hover:bg-surface-2 text-ink-muted hover:text-brand-400 ' +
                'transition-colors duration-120" ' +
                'title="Пересобрать эту конфигурацию" ' +
                'aria-label="Пересобрать">' +
                SPARKLE_ICON_SVG +
              '</button>' +
            '</td>' +
          '</tr>';
      }
      tbody.innerHTML = html;
    }
    var totalChanged = lastSpec.total_usd !== null && lastSpec.total_usd !== data.total_usd;
    var totalRubStr = fmtRubFromUsd(data.total_usd) || rub(data.total_rub);
    total.innerHTML =
      '<span class="text-h2 text-ink-primary">' + fmtUsd(data.total_usd) + '</span>' +
      '<div class="text-caption text-ink-secondary font-normal">' +
        totalRubStr + ' ₽</div>';

    if (lastSpec.initialized) {
      Object.keys(changedIds).forEach(function (id) {
        var tr = tbody.querySelector('tr[data-item-id="' + id + '"]');
        if (!tr) return;
        flip(tr.querySelector('.kt-spec-qty-cell'));
        flip(tr.querySelector('.kt-spec-sum-cell'));
      });
      if (totalChanged) flip(total);
    }

    lastSpec = {
      items: newItemsMap,
      total_usd: data.total_usd,
      initialized: true
    };
  }

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
        var data = await post('/configurator/project/' + PROJECT_ID + '/select', {
          query_id: queryId,
          variant_manufacturer: mfg,
          quantity: qty
        });
        renderSpec(data);
      } else {
        if (qtyEl) qtyEl.disabled = true;
        var data2 = await post('/configurator/project/' + PROJECT_ID + '/deselect', {
          query_id: queryId,
          variant_manufacturer: mfg
        });
        renderSpec(data2);
      }
    } catch (e) {
      console.error(e);
      cb.checked = !cb.checked;
      if (qtyEl) qtyEl.disabled = !cb.checked;
      toast('Не удалось обновить спецификацию. Попробуйте ещё раз.', { kind: 'error' });
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
      var data = await post('/configurator/project/' + PROJECT_ID + '/update_quantity', {
        query_id: queryId,
        variant_manufacturer: mfg,
        quantity: qty
      });
      renderSpec(data);
    } catch (e) {
      console.error(e);
      toast('Не удалось изменить количество. Попробуйте ещё раз.', { kind: 'error' });
    } finally {
      input.disabled = false;
    }
  }

  document.querySelectorAll('.kt-spec-check').forEach(function (cb) {
    cb.addEventListener('change', function () { onCheckChange(cb); });
  });

  document.querySelectorAll('.kt-spec-qty').forEach(function (input) {
    input.addEventListener('change', function () { onQtyChange(input); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        input.blur();
      }
    });
  });

  document.querySelectorAll('.kt-num-stepper').forEach(function (wrap) {
    var input = wrap.querySelector('input[type="number"]');
    var upBtn = wrap.querySelector('.kt-num-stepper-up');
    var downBtn = wrap.querySelector('.kt-num-stepper-down');
    if (!input || !upBtn || !downBtn) return;

    function step(delta) {
      if (input.disabled) return;
      var current = parseFloat(input.value);
      if (!isFinite(current)) current = 0;
      var stepAttr = parseFloat(input.step);
      var stepSize = isFinite(stepAttr) && stepAttr > 0 ? stepAttr : 1;
      var next = current + delta * stepSize;
      var minAttr = parseFloat(input.min);
      if (isFinite(minAttr) && next < minAttr) next = minAttr;
      var maxAttr = parseFloat(input.max);
      if (isFinite(maxAttr) && next > maxAttr) next = maxAttr;
      next = Math.round(next * 1e6) / 1e6;
      input.value = String(next);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    upBtn.addEventListener('click', function (e) {
      e.preventDefault();
      step(1);
    });
    downBtn.addEventListener('click', function (e) {
      e.preventDefault();
      step(-1);
    });
  });

  // ---------------------------------------------------------------------
  // 9А.2.3: toast'ы — правый нижний угол
  // ---------------------------------------------------------------------

  function ensureToastContainer() {
    var box = document.getElementById('kt-toast-container');
    if (box) return box;
    box = document.createElement('div');
    box.id = 'kt-toast-container';
    box.className = 'kt-toast-container';
    document.body.appendChild(box);
    return box;
  }

  function toast(html, opts) {
    opts = opts || {};
    var box = ensureToastContainer();
    var el = document.createElement('div');
    var kind = opts.kind || 'info';
    var cls = 'kt-toast';
    if (kind === 'success') cls += ' kt-toast-success';
    if (kind === 'warn')    cls += ' kt-toast-warn';
    if (kind === 'error')   cls += ' kt-toast-error';
    el.className = cls;
    el.innerHTML = html +
      '<button type="button" class="kt-toast-close" aria-label="Закрыть">×</button>';
    box.appendChild(el);
    function close() {
      if (!el.parentNode) return;
      el.classList.add('kt-toast-leaving');
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
    }
    el.querySelector('.kt-toast-close').addEventListener('click', close);
    var ms = opts.ms || 6000;
    var timer = setTimeout(close, ms);
    el.addEventListener('mouseenter', function () { clearTimeout(timer); });
    el.addEventListener('mouseleave', function () { timer = setTimeout(close, 2500); });
    return el;
  }

  function deltaSummaryHtml(d) {
    var arrow = d.delta_pct > 0 ? '▲' : (d.delta_pct < 0 ? '▼' : '·');
    var color = d.delta_pct > 0 ? 'text-danger-500'
              : (d.delta_pct < 0 ? 'text-success-500' : 'text-ink-muted');
    var deltaStr = (d.delta_pct > 0 ? '+' : '') + (d.delta_pct || 0).toFixed(1) + '%';
    return '<span class="font-medium">' + escapeHtml(d.config_name) + '</span>: ' +
      fmtUsd(d.old_unit_usd) + ' → ' + fmtUsd(d.new_unit_usd) +
      ' <span class="' + color + '">' + arrow + ' ' + escapeHtml(deltaStr) + '</span>';
  }

  function changeListHtml(changes) {
    if (!changes || !changes.length) return '';
    var rows = changes.map(function (c) {
      var oldStr = c.old_brand_model
        ? escapeHtml(c.old_brand_model) + ' (' + fmtUsd(c.old_usd) + ')'
        : '<span class="text-ink-muted">—</span>';
      var newStr = c.new_brand_model
        ? escapeHtml(c.new_brand_model) + ' (' + fmtUsd(c.new_usd) + ')'
        : '<span class="text-ink-muted">—</span>';
      var arrow = c.new_usd < c.old_usd
        ? '<span class="text-success-500">↓</span>'
        : (c.new_usd > c.old_usd
            ? '<span class="text-danger-500">↑</span>'
            : '<span class="text-ink-muted">·</span>');
      return '<div class="text-caption">' +
        '<span class="text-ink-muted">' + escapeHtml(c.category_label) + ':</span> ' +
        oldStr + ' ' + arrow + ' ' + newStr +
      '</div>';
    });
    return rows.join('');
  }

  // ---------------------------------------------------------------------
  // 9А.2.5: компактная модалка «Результат пересборки» + reload по
  // sessionStorage. AJAX-эндпоинта для variants-фрагмента нет, поэтому
  // после применения/отката перезагружаем страницу — но сохраняем scroll
  // position, чтобы UX не «прыгал» вверх.
  // ---------------------------------------------------------------------

  function reoptimizeCardHtml(d) {
    var name = escapeHtml(d.config_name);
    if (d.status === 'unavailable') {
      return (
        '<div class="card card-pad border-warning-500/40 bg-warning-bg/20" ' +
             'data-spec-item-id="' + d.spec_item_id + '">' +
          '<div class="font-medium text-ink-primary">' + name + '</div>' +
          '<div class="text-caption text-warning-500 mt-1">' +
            escapeHtml(d.unavailable_reason || 'Не удалось пересобрать') +
          '</div>' +
        '</div>'
      );
    }
    if (d.status === 'no_changes') {
      return (
        '<div class="card card-pad opacity-70" ' +
             'data-spec-item-id="' + d.spec_item_id + '">' +
          '<div class="font-medium text-ink-primary">' + name + '</div>' +
          '<div class="text-caption text-ink-muted mt-1">' +
            'Состав и цены не изменились' +
          '</div>' +
        '</div>'
      );
    }
    // status === 'reoptimized'
    var changes = d.changed_components || [];
    var arrow = d.delta_pct < 0 ? '▼' : (d.delta_pct > 0 ? '▲' : '·');
    var deltaCls = d.delta_pct < 0 ? 'text-success-500'
                  : (d.delta_pct > 0 ? 'text-danger-500' : 'text-ink-muted');
    var deltaStr = (d.delta_pct > 0 ? '+' : '') + (d.delta_pct || 0).toFixed(1) + '%';
    var detailsBlock = '';
    if (changes.length > 0) {
      detailsBlock =
        '<button type="button" class="kt-reopt-toggle text-caption ' +
                'text-brand-400 hover:underline mt-2" aria-expanded="false">' +
          'Показать детали ▼' +
        '</button>' +
        '<div class="kt-reopt-details hidden mt-2 pl-3 space-y-1 ' +
             'border-l-2 border-line-soft">' +
          changeListHtml(changes) +
        '</div>';
    }
    return (
      '<div class="card card-pad" data-spec-item-id="' + d.spec_item_id + '">' +
        '<div class="flex items-baseline justify-between gap-3 flex-wrap">' +
          '<div class="font-medium text-ink-primary">' + name + '</div>' +
          '<div class="text-small tabular-nums">' +
            '<span class="text-ink-muted">' + fmtUsd(d.old_unit_usd) + '</span>' +
            '<span class="text-ink-muted"> → </span>' +
            '<span class="font-medium text-ink-primary">' +
              fmtUsd(d.new_unit_usd) + '</span>' +
            '<span class="' + deltaCls + ' ml-2">' +
              arrow + ' ' + escapeHtml(deltaStr) + '</span>' +
          '</div>' +
        '</div>' +
        '<div class="mt-2 text-caption text-ink-muted">' +
          'Изменено компонентов: ' + changes.length +
        '</div>' +
        detailsBlock +
      '</div>'
    );
  }

  function ensureReoptimizeModal() {
    var modal = document.getElementById('reoptimize-modal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'reoptimize-modal';
    modal.className = 'hidden modal-overlay';
    modal.innerHTML =
      '<div class="modal-container max-w-2xl">' +
        '<div class="modal-header">' +
          '<h3 class="text-h2 text-ink-primary">Результат пересборки</h3>' +
          '<button type="button" class="btn btn-sm btn-ghost p-1.5 ' +
                                       'kt-reopt-modal-close" ' +
                  'aria-label="Закрыть">×</button>' +
        '</div>' +
        '<div class="modal-body">' +
          '<div id="reoptimize-modal-cards" class="space-y-3"></div>' +
        '</div>' +
        '<div class="modal-footer flex-wrap gap-2">' +
          '<button type="button" id="reoptimize-rollback-btn" ' +
                  'class="btn btn-md btn-ghost text-danger-500 hidden">' +
            'Отменить пересборку' +
          '</button>' +
          '<button type="button" id="reoptimize-apply-btn" ' +
                  'class="btn btn-md btn-primary">' +
            'Применить' +
          '</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);
    return modal;
  }

  function closeReoptimizeModal(then) {
    var modal = document.getElementById('reoptimize-modal');
    if (!modal) { if (then) then(); return; }
    modal.classList.add('modal-leaving');
    setTimeout(function () {
      modal.classList.add('hidden');
      modal.classList.remove('modal-leaving');
      if (then) then();
    }, 150);
  }

  function showReoptimizeModal(items, opts) {
    opts = opts || {};
    var modal = ensureReoptimizeModal();
    var body = modal.querySelector('#reoptimize-modal-cards');
    body.innerHTML = (items || []).map(reoptimizeCardHtml).join('');
    body.querySelectorAll('.kt-reopt-toggle').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var details = btn.parentElement.querySelector('.kt-reopt-details');
        if (!details) return;
        var hidden = details.classList.toggle('hidden');
        btn.textContent = hidden ? 'Показать детали ▼' : 'Скрыть детали ▲';
        btn.setAttribute('aria-expanded', String(!hidden));
      });
    });
    var rollbackBtn = modal.querySelector('#reoptimize-rollback-btn');
    if (rollbackBtn) {
      rollbackBtn.classList.toggle('hidden', !opts.canRollback);
    }
    modal.classList.remove('hidden');

    // wire close/apply (replace handlers each open).
    var closeBtn = modal.querySelector('.kt-reopt-modal-close');
    var applyBtn = modal.querySelector('#reoptimize-apply-btn');
    function onApply() {
      // Применить = «увидеть актуальный список конфигураций» —
      // изменения уже в БД, просто перерисуем страницу.
      closeReoptimizeModal(reloadKeepingScroll);
    }
    function onRollback() {
      closeReoptimizeModal(function () {
        rollbackAllAndReload(opts.itemId);
      });
    }
    closeBtn.onclick = onApply;
    applyBtn.onclick = onApply;
    if (rollbackBtn) rollbackBtn.onclick = onRollback;
  }

  function reloadKeepingScroll() {
    try {
      sessionStorage.setItem(SCROLL_STORAGE_KEY, String(window.scrollY));
    } catch (e) {}
    location.reload();
  }

  function deferToastAndReload(msg, kind) {
    try {
      sessionStorage.setItem(SCROLL_STORAGE_KEY, String(window.scrollY));
      sessionStorage.setItem(
        DEFERRED_TOAST_KEY,
        JSON.stringify({ msg: msg, kind: kind || 'info' })
      );
    } catch (e) {}
    location.reload();
  }

  // На загрузке страницы — восстановить scroll и показать deferred toast.
  (function applyDeferredOnLoad() {
    try {
      var s = sessionStorage.getItem(SCROLL_STORAGE_KEY);
      if (s !== null) {
        sessionStorage.removeItem(SCROLL_STORAGE_KEY);
        var y = parseInt(s, 10);
        if (isFinite(y) && y >= 0) {
          // Сразу прыгаем (без smooth — это восстановление, а не навигация).
          window.scrollTo(0, y);
        }
      }
      var t = sessionStorage.getItem(DEFERRED_TOAST_KEY);
      if (t) {
        sessionStorage.removeItem(DEFERRED_TOAST_KEY);
        var d = JSON.parse(t);
        // Делаем toast чуть позже, чтобы DOM/CSS успели прогрузиться.
        setTimeout(function () { toast(d.msg, { kind: d.kind, ms: 4500 }); }, 50);
      }
    } catch (e) {}
  })();

  function showReoptimizeSummary(recalcData) {
    if (!recalcData) return;
    var changed = recalcData.changed_count;
    var total = recalcData.total_count;
    if (total === 0) {
      toast('Спецификация пуста.', { kind: 'info' });
      return;
    }
    var items = recalcData.items || [];
    var unavailable = items.filter(function (d) {
      return d.status === 'unavailable';
    });
    if (changed === 0 && unavailable.length === 0) {
      toast('Все конфигурации уже оптимальны: изменений нет.', { kind: 'info' });
      return;
    }
    // Короткий toast (1 строка); подробности — в модалке.
    var toastMsg = changed > 0
      ? 'Пересобрано ' + changed +
        (total > changed ? ' из ' + total : '') + ' ' +
        pluralRu(changed, ['конфигурация', 'конфигурации', 'конфигураций'])
      : unavailable.length + ' ' +
        pluralRu(unavailable.length, ['позиция', 'позиции', 'позиций']) +
        ' не удалось пересобрать';
    toast(toastMsg, {
      kind: unavailable.length > 0 && changed === 0 ? 'warn' : 'success',
      ms: 4500,
    });
    showReoptimizeModal(items, { canRollback: changed > 0 });
  }

  function pluralRu(n, forms) {
    var abs = Math.abs(n) % 100;
    var n1 = abs % 10;
    if (abs > 10 && abs < 20) return forms[2];
    if (n1 > 1 && n1 < 5) return forms[1];
    if (n1 === 1) return forms[0];
    return forms[2];
  }

  async function reoptimizeFull() {
    var btn = document.getElementById('kt-spec-recalc-btn');
    if (!btn) return;
    if (!confirm(
      'Пересобрать все конфигурации?\n\n' +
      'Состав компонентов может измениться, если у поставщиков ' +
      'появились более выгодные варианты.'
    )) return;
    var hint = document.getElementById('kt-spec-recalc-hint');
    btn.disabled = true;
    if (hint) {
      hint.textContent = 'Запускаем подбор по актуальным данным…';
      hint.classList.remove('hidden');
    }
    try {
      var data = await post('/configurator/project/' + PROJECT_ID + '/spec/reoptimize', {});
      renderSpec(data);
      showReoptimizeSummary(data.recalc);
    } catch (e) {
      console.error(e);
      toast('Не удалось пересобрать конфигурации. Попробуйте ещё раз.', { kind: 'error' });
    } finally {
      btn.disabled = false;
      if (hint) hint.classList.add('hidden');
    }
  }

  async function reoptimizeOne(itemId) {
    if (!confirm(
      'Пересобрать эту конфигурацию?\n\n' +
      'Состав компонентов может измениться.'
    )) return;
    try {
      var data = await post(
        '/configurator/project/' + PROJECT_ID + '/spec/' + itemId + '/reoptimize', {}
      );
      renderSpec(data);
      var d = data.recalc_item;
      if (!d) return;
      if (d.status === 'unavailable') {
        toast('Не удалось пересобрать «' + escapeHtml(d.config_name) + '»',
          { kind: 'warn', ms: 5000 });
        showReoptimizeModal([d], { canRollback: false, itemId: itemId });
        return;
      }
      if (d.status === 'no_changes') {
        toast('Конфигурация уже оптимальна — изменений нет.', { kind: 'info' });
        return;
      }
      toast('Пересобрана 1 конфигурация', { kind: 'success', ms: 4500 });
      showReoptimizeModal([d], { canRollback: true, itemId: itemId });
    } catch (e) {
      console.error(e);
      toast('Не удалось пересобрать позицию.', { kind: 'error' });
    }
  }

  async function rollbackAllAndReload(itemId) {
    var url = itemId
      ? '/configurator/project/' + PROJECT_ID + '/spec/' + itemId + '/rollback'
      : '/configurator/project/' + PROJECT_ID + '/spec/rollback';
    try {
      await post(url, {});
      deferToastAndReload(
        'Пересборка отменена, исходная конфигурация восстановлена.',
        'info'
      );
    } catch (e) {
      console.error(e);
      toast('Не удалось откатить пересбор.', { kind: 'error' });
    }
  }

  document.addEventListener('click', function (e) {
    var fullBtn = e.target.closest('#kt-spec-recalc-btn');
    if (fullBtn) {
      e.preventDefault();
      reoptimizeFull();
      return;
    }
    var rowBtn = e.target.closest('.kt-spec-recalc-row');
    if (rowBtn) {
      e.preventDefault();
      var tr = rowBtn.closest('tr[data-item-id]');
      if (!tr) return;
      var id = parseInt(tr.dataset.itemId, 10);
      if (id) reoptimizeOne(id);
    }
  });

})();
