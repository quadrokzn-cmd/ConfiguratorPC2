// Этап 6.2/9А.2.1/9А.2.3: клиентская часть страницы проекта.
//
// - Ставит/снимает галочки «в спецификацию».
// - Меняет количество в активной строке.
// - Перерисовывает таблицу спецификации без перезагрузки страницы.
// - 9А.2.3: кнопка «Пересобрать конфигурации» вызывает /spec/reoptimize,
//   модалка с подтверждением и diff'ом, кнопка «Отменить» (rollback).
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
      var data = await post('/project/' + PROJECT_ID + '/update_quantity', {
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

  function showReoptimizeSummary(recalcData) {
    if (!recalcData) return;
    var changed = recalcData.changed_count;
    var total = recalcData.total_count;
    if (total === 0) {
      toast('Спецификация пуста.', { kind: 'info' });
      return;
    }
    var unavailable = (recalcData.items || []).filter(function (d) {
      return d.status === 'unavailable';
    });
    if (changed === 0 && unavailable.length === 0) {
      toast('Все конфигурации уже оптимальны: изменений нет.', { kind: 'info' });
      return;
    }
    var lines = [];
    if (changed > 0) {
      lines.push(
        '<div class="font-medium mb-1">Пересобрано ' + changed + ' из ' + total + ' конфигураций</div>'
      );
      (recalcData.items || []).forEach(function (d) {
        if (d.status === 'reoptimized') {
          lines.push('<div class="mt-2">' + deltaSummaryHtml(d) + '</div>');
          var ch = changeListHtml(d.changed_components);
          if (ch) lines.push('<div class="mt-1 ml-2 space-y-0.5">' + ch + '</div>');
        }
      });
      lines.push(
        '<div class="mt-2 pt-2 border-t border-line-subtle">' +
          '<button type="button" id="kt-rollback-all-btn" ' +
                  'class="text-caption text-warning-500 underline">' +
            'Отменить пересбор' +
          '</button>' +
        '</div>'
      );
    }
    if (unavailable.length > 0) {
      lines.push(
        '<div class="mt-2 pt-2 border-t border-line-subtle text-warning-500 ' +
        'font-medium">' + unavailable.length + ' нельзя пересобрать</div>'
      );
      unavailable.forEach(function (d) {
        lines.push(
          '<div class="text-caption text-warning-500">' +
          escapeHtml(d.config_name) + ': ' +
          escapeHtml(d.unavailable_reason || 'не удалось пересобрать') +
          '</div>'
        );
      });
    }
    var t = toast(lines.join(''), {
      kind: unavailable.length > 0 ? 'warn' : 'success',
      ms: 12000,
    });
    var rollback = t.querySelector('#kt-rollback-all-btn');
    if (rollback) {
      rollback.addEventListener('click', function (e) {
        e.preventDefault();
        rollbackAll();
        t.querySelector('.kt-toast-close').click();
      });
    }
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
      var data = await post('/project/' + PROJECT_ID + '/spec/reoptimize', {});
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
        '/project/' + PROJECT_ID + '/spec/' + itemId + '/reoptimize', {}
      );
      renderSpec(data);
      var d = data.recalc_item;
      if (!d) return;
      if (d.status === 'unavailable') {
        toast(
          '<div class="font-medium">Невозможно пересобрать</div>' +
          '<div class="text-caption">' + escapeHtml(d.config_name) + ': ' +
          escapeHtml(d.unavailable_reason || 'компонент недоступен') + '</div>',
          { kind: 'warn', ms: 8000 }
        );
        return;
      }
      if (d.status === 'no_changes') {
        toast('Конфигурация уже оптимальна — изменений нет.', { kind: 'info' });
        return;
      }
      var html = deltaSummaryHtml(d);
      var ch = changeListHtml(d.changed_components);
      if (ch) html += '<div class="mt-1 space-y-0.5">' + ch + '</div>';
      html += '<div class="mt-2 pt-2 border-t border-line-subtle">' +
        '<button type="button" data-rollback-id="' + d.spec_item_id + '" ' +
                'class="kt-rollback-one-btn text-caption text-warning-500 underline">' +
          'Отменить' +
        '</button>' +
      '</div>';
      var t = toast(html, { kind: 'success', ms: 10000 });
      var rb = t.querySelector('.kt-rollback-one-btn');
      if (rb) {
        rb.addEventListener('click', function (e) {
          e.preventDefault();
          rollbackOne(parseInt(rb.dataset.rollbackId, 10));
          t.querySelector('.kt-toast-close').click();
        });
      }
    } catch (e) {
      console.error(e);
      toast('Не удалось пересобрать позицию.', { kind: 'error' });
    }
  }

  async function rollbackAll() {
    try {
      var data = await post('/project/' + PROJECT_ID + '/spec/rollback', {});
      renderSpec(data);
      toast('Отменено: возвращён предыдущий состав.', { kind: 'info' });
    } catch (e) {
      console.error(e);
      toast('Не удалось откатить пересбор.', { kind: 'error' });
    }
  }

  async function rollbackOne(itemId) {
    try {
      var data = await post(
        '/project/' + PROJECT_ID + '/spec/' + itemId + '/rollback', {}
      );
      renderSpec(data);
      toast('Отменено: возвращён предыдущий состав.', { kind: 'info' });
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
