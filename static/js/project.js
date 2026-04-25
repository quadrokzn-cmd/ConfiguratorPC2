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

  // 9А.1.2: запомненное состояние спецификации между рендерами,
  // чтобы определять — какие цифры реально изменились и подсветить
  // их через .num-flip (animated numbers по ТЗ).
  // ключ — item.id, значение — { quantity, total_usd, total_rub }.
  // initialized=false на первый рендер — чтобы при загрузке страницы
  // не флипало пред-существующие позиции.
  var lastSpec = { items: {}, total_usd: null, initialized: false };

  function flip(el) {
    if (!el) return;
    el.classList.remove('num-flip');
    // Триггер reflow, чтобы класс снова сработал, даже если только
    // что был на этом элементе.
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
      body: JSON.stringify(payload)
    });
    if (!r.ok) {
      var detail = '';
      try { detail = (await r.json()).detail || ''; } catch (e) {}
      throw new Error('spec-fail: ' + r.status + ' ' + detail);
    }
    return r.json();
  }

  // 9А.2.1: красивая короткая дата для метки «обновлено DD.MM HH:MM».
  function fmtRecalcStamp(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    function pad(n) { return n < 10 ? '0' + n : '' + n; }
    return 'обновлено ' + pad(d.getDate()) + '.' + pad(d.getMonth() + 1) +
      ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  // SVG для refresh-cw — должен совпадать с lucide-стилем макроса icon().
  var REFRESH_ICON_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" ' +
    'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" ' +
    'stroke-linejoin="round" viewBox="0 0 24 24">' +
    '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>';

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
        html +=
          '<tr data-item-id="' + it.id + '">' +
            '<td class="px-2 py-2.5 text-ink-muted tabular-nums align-top">' + it.position + '</td>' +
            '<td class="px-2 py-2.5 text-ink-primary break-words">' +
              '<div>' + escapeHtml(it.display_name) + '</div>' +
              '<div class="text-caption text-ink-muted tabular-nums">' +
                fmtUsd(it.unit_usd) + ' / шт · ' + rub(it.unit_rub) + ' ₽' +
              '</div>' +
              stampHtml +
            '</td>' +
            '<td class="px-2 py-2.5 text-right text-ink-secondary tabular-nums kt-spec-qty-cell align-top">' +
              it.quantity +
            '</td>' +
            '<td class="px-2 py-2.5 text-right text-ink-primary tabular-nums whitespace-nowrap kt-spec-sum-cell align-top">' +
              fmtUsd(it.total_usd) +
              '<div class="text-caption text-ink-secondary font-normal">' +
                rub(it.total_rub) + ' ₽</div>' +
            '</td>' +
            '<td class="px-1 py-2.5 text-right align-top">' +
              '<button type="button" class="kt-spec-recalc-row p-1 rounded ' +
                'hover:bg-surface-2 text-ink-muted hover:text-brand-400 ' +
                'transition-colors duration-120" ' +
                'title="Пересчитать цену этой позиции" ' +
                'aria-label="Пересчитать цену">' +
                REFRESH_ICON_SVG +
              '</button>' +
            '</td>' +
          '</tr>';
      }
      tbody.innerHTML = html;
    }
    var totalChanged = lastSpec.total_usd !== null && lastSpec.total_usd !== data.total_usd;
    total.innerHTML =
      '<span class="text-h2 text-ink-primary">' + fmtUsd(data.total_usd) + '</span>' +
      '<div class="text-caption text-ink-secondary font-normal">' +
        rub(data.total_rub) + ' ₽</div>';

    // Подсветка изменившихся цифр через num-flip — только после
    // первого реального обновления (не флипаем то, что уже было
    // на странице на момент загрузки).
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

  // 9А.1.3: кастомный stepper для input[type=number]. Нативные стрелки
  // браузера скрыты глобально в CSS (выглядят чужеродно на тёмной теме);
  // взамен — пара кнопок вверх/вниз внутри обёртки .kt-num-stepper.
  // Клик по кнопке меняет input.value и диспатчит 'input' + 'change',
  // чтобы внешняя логика (обновление спецификации, генерация КП и т.д.)
  // подхватила изменение как при ручном вводе.
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
      // Округляем мелкие плавающие хвосты, шаг у нас всегда целый.
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
  // 9А.2.1: пересчёт цен в спецификации
  // ---------------------------------------------------------------------

  // Простой контейнер тостов в правом верхнем углу.
  function ensureToastContainer() {
    var box = document.getElementById('kt-toast-container');
    if (box) return box;
    box = document.createElement('div');
    box.id = 'kt-toast-container';
    box.className = 'fixed top-4 right-4 z-50 flex flex-col gap-2 pointer-events-none';
    document.body.appendChild(box);
    return box;
  }

  function toast(html, opts) {
    opts = opts || {};
    var box = ensureToastContainer();
    var el = document.createElement('div');
    var kind = opts.kind || 'info';
    var bg = 'bg-surface-1 border-line-default text-ink-primary';
    if (kind === 'success') bg = 'bg-success-500/10 border-success-500/40 text-success-500';
    if (kind === 'warn')    bg = 'bg-warning-500/10 border-warning-500/40 text-warning-500';
    if (kind === 'error')   bg = 'bg-danger-500/10 border-danger-500/40 text-danger-500';
    el.className =
      'pointer-events-auto rounded-lg border px-3 py-2 text-small shadow-lg ' +
      'max-w-sm break-words ' + bg;
    el.innerHTML = html;
    box.appendChild(el);
    setTimeout(function () {
      el.style.transition = 'opacity 200ms ease';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 220);
    }, opts.ms || 4000);
  }

  function deltaSummaryHtml(delta) {
    var arrow = delta.delta_pct > 0 ? '▲' : (delta.delta_pct < 0 ? '▼' : '·');
    var color = delta.delta_pct > 0 ? 'text-danger-500'
              : (delta.delta_pct < 0 ? 'text-success-500' : 'text-ink-muted');
    var deltaStr = (delta.delta_pct > 0 ? '+' : '') + delta.delta_pct.toFixed(1) + '%';
    return '<span class="font-medium">' + escapeHtml(delta.config_name) + '</span>: ' +
      fmtUsd(delta.old_unit_usd) + ' → ' + fmtUsd(delta.new_unit_usd) +
      ' <span class="' + color + '">' + arrow + ' ' + escapeHtml(deltaStr) + '</span>';
  }

  function showRecalcSummary(recalcData) {
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
      toast('Цены актуальны: изменений нет.', { kind: 'info' });
      return;
    }
    var lines = [];
    if (changed > 0) {
      lines.push(
        '<div class="font-medium mb-1">Изменилось ' + changed + ' из ' + total + ' позиций</div>'
      );
      (recalcData.items || []).forEach(function (d) {
        if (d.changed && d.status === 'ok') {
          lines.push('<div class="text-caption">' + deltaSummaryHtml(d) + '</div>');
        }
      });
    }
    if (unavailable.length > 0) {
      lines.push(
        '<div class="mt-2 pt-2 border-t border-line-subtle text-warning-500 ' +
        'font-medium">' + unavailable.length + ' нельзя пересчитать</div>'
      );
      unavailable.forEach(function (d) {
        var reasons = (d.unavailable_components || []).join(', ');
        lines.push(
          '<div class="text-caption text-warning-500">' +
          escapeHtml(d.config_name) + ': ' + escapeHtml(reasons || 'компонент недоступен') +
          '</div>'
        );
      });
    }
    toast(lines.join(''), {
      kind: unavailable.length > 0 ? 'warn' : 'success',
      ms: 8000,
    });
  }

  async function recalcFull() {
    var btn = document.getElementById('kt-spec-recalc-btn');
    if (!btn) return;
    var hint = document.getElementById('kt-spec-recalc-hint');
    btn.disabled = true;
    if (hint) {
      hint.textContent = 'Пересчитываем по актуальным ценам поставщиков…';
      hint.classList.remove('hidden');
    }
    try {
      var data = await post('/project/' + PROJECT_ID + '/spec/recalc', {});
      renderSpec(data);
      showRecalcSummary(data.recalc);
    } catch (e) {
      console.error(e);
      toast('Не удалось пересчитать цены. Попробуйте ещё раз.', { kind: 'error' });
    } finally {
      btn.disabled = false;
      if (hint) hint.classList.add('hidden');
    }
  }

  async function recalcOne(itemId) {
    try {
      var data = await post(
        '/project/' + PROJECT_ID + '/spec/' + itemId + '/recalc', {}
      );
      renderSpec(data);
      var d = data.recalc_item;
      if (!d) return;
      if (d.status === 'unavailable') {
        var reasons = (d.unavailable_components || []).join(', ');
        toast(
          '<div class="font-medium">Невозможно пересчитать</div>' +
          '<div class="text-caption">' + escapeHtml(d.config_name) +
          ': ' + escapeHtml(reasons || 'компонент недоступен') +
          '. Замените конфигурацию вручную.</div>',
          { kind: 'warn', ms: 7000 }
        );
        return;
      }
      if (!d.changed) {
        toast('Цена не изменилась.', { kind: 'info' });
        return;
      }
      toast(deltaSummaryHtml(d), { kind: 'success', ms: 5000 });
    } catch (e) {
      console.error(e);
      toast('Не удалось пересчитать позицию.', { kind: 'error' });
    }
  }

  // Делегирование: кнопка в заголовке + кнопки в строках (перерисовываются).
  document.addEventListener('click', function (e) {
    var fullBtn = e.target.closest('#kt-spec-recalc-btn');
    if (fullBtn) {
      e.preventDefault();
      recalcFull();
      return;
    }
    var rowBtn = e.target.closest('.kt-spec-recalc-row');
    if (rowBtn) {
      e.preventDefault();
      var tr = rowBtn.closest('tr[data-item-id]');
      if (!tr) return;
      var id = parseInt(tr.dataset.itemId, 10);
      if (id) recalcOne(id);
    }
  });

})();
