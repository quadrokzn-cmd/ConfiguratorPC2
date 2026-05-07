// Этап 12.5a: HTML-модал и toast вместо нативных confirm()/alert().
//
// Зачем:
// - Computer-use агенты (Claude в Chrome via CDP) не могут кликать
//   нативные confirm/alert — они рендерятся вне DOM.
// - Нативные диалоги нельзя стилизовать под dark-тему портала.
//
// Глобально на window:
//   confirmDialog(message, opts?) -> Promise<boolean>
//     opts:
//       title?       — заголовок (по умолчанию «Подтверждение»)
//       okLabel?     — текст кнопки подтверждения (по умолчанию «Подтвердить»)
//       cancelLabel? — текст отмены (по умолчанию «Отменить»)
//       danger?      — bool, делает кнопку «Подтвердить» красной
//                       (для опасных действий: «удалить навсегда»)
//
//   toastDialog(message, opts?)
//     opts:
//       kind?: 'info' | 'success' | 'warn' | 'error' (по умолчанию 'info')
//       ms?:   автозакрытие через N мс (по умолчанию 4000)
//
// Поведение confirmDialog:
// - Esc и клик по подложке = «Отменить» (resolve(false)).
// - Tab / Shift+Tab — focus trap внутри диалога.
// - При открытии фокус на «Отменить» (безопасный default).
// - При закрытии возвращает фокус на элемент, который был активен
//   до открытия.
// - Поддерживается несколько диалогов подряд (но не одновременно —
//   API строит один за раз).
//
// Без фреймворков. Подключается из portal/templates/base.html.

(function () {
  'use strict';

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ---------------------------------------------------------------------
  // confirmDialog
  // ---------------------------------------------------------------------

  function confirmDialog(message, opts) {
    opts = opts || {};
    var title       = opts.title       || 'Подтверждение';
    var okLabel     = opts.okLabel     || 'Подтвердить';
    var cancelLabel = opts.cancelLabel || 'Отменить';
    var danger      = !!opts.danger;
    var previouslyFocused = document.activeElement;

    return new Promise(function (resolve) {
      var overlay = document.createElement('div');
      overlay.className = 'kt-confirm-overlay';
      overlay.setAttribute('role', 'presentation');
      overlay.setAttribute('data-testid', 'kt-confirm-overlay');

      var titleId = 'kt-confirm-title-' + Math.random().toString(36).slice(2, 9);
      var msgId   = 'kt-confirm-msg-'   + Math.random().toString(36).slice(2, 9);

      var okBtnClass = danger ? 'btn btn-md btn-danger' : 'btn btn-md btn-primary';

      overlay.innerHTML =
        '<div class="kt-confirm-card" role="dialog" aria-modal="true"' +
        '     aria-labelledby="' + titleId + '"' +
        '     aria-describedby="' + msgId + '">' +
        '  <h3 id="' + titleId + '" class="kt-confirm-title">' +
              escapeHtml(title) + '</h3>' +
        '  <p id="' + msgId + '" class="kt-confirm-message">' +
              escapeHtml(message) + '</p>' +
        '  <div class="kt-confirm-actions">' +
        '    <button type="button" class="btn btn-md btn-secondary kt-confirm-cancel"' +
        '            data-testid="kt-confirm-cancel">' +
                escapeHtml(cancelLabel) + '</button>' +
        '    <button type="button" class="' + okBtnClass + ' kt-confirm-ok"' +
        '            data-testid="kt-confirm-ok">' +
                escapeHtml(okLabel) + '</button>' +
        '  </div>' +
        '</div>';

      document.body.appendChild(overlay);

      var card     = overlay.querySelector('.kt-confirm-card');
      var okBtn    = overlay.querySelector('.kt-confirm-ok');
      var cancelBtn = overlay.querySelector('.kt-confirm-cancel');

      var settled = false;
      function close(value) {
        if (settled) return;
        settled = true;
        document.removeEventListener('keydown', onKeyDown, true);
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
        // Возвращаем фокус на исходный элемент (если он ещё в DOM
        // и фокусируем).
        if (previouslyFocused && typeof previouslyFocused.focus === 'function'
            && document.contains(previouslyFocused)) {
          try { previouslyFocused.focus(); } catch (_) { /* noop */ }
        }
        resolve(value);
      }

      function onKeyDown(e) {
        if (e.key === 'Escape') {
          e.preventDefault();
          e.stopPropagation();
          close(false);
          return;
        }
        if (e.key === 'Enter') {
          // Enter подтверждает только если фокус не на «Отменить».
          if (document.activeElement === cancelBtn) return;
          e.preventDefault();
          e.stopPropagation();
          close(true);
          return;
        }
        if (e.key === 'Tab') {
          // Focus trap: переключаем между двумя кнопками.
          var focusables = [cancelBtn, okBtn];
          var idx = focusables.indexOf(document.activeElement);
          if (idx === -1) {
            e.preventDefault();
            focusables[0].focus();
            return;
          }
          var nextIdx;
          if (e.shiftKey) {
            nextIdx = (idx - 1 + focusables.length) % focusables.length;
          } else {
            nextIdx = (idx + 1) % focusables.length;
          }
          e.preventDefault();
          focusables[nextIdx].focus();
        }
      }

      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) close(false);
      });
      okBtn.addEventListener('click',     function () { close(true); });
      cancelBtn.addEventListener('click', function () { close(false); });
      document.addEventListener('keydown', onKeyDown, true);

      // Фокус по умолчанию — на «Отменить» (безопасный default,
      // нечаянный Enter не подтвердит опасное действие).
      cancelBtn.focus();
    });
  }

  // ---------------------------------------------------------------------
  // toastDialog
  //
  // Работает поверх существующей kt-toast-* инфраструктуры в main.css.
  // На страницах портала static/js/project.js не подключён, поэтому
  // повторяем минимальный builder здесь.
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

  function toastDialog(message, opts) {
    opts = opts || {};
    var box = ensureToastContainer();
    var el = document.createElement('div');
    var kind = opts.kind || 'info';
    var cls = 'kt-toast';
    if (kind === 'success') cls += ' kt-toast-success';
    if (kind === 'warn')    cls += ' kt-toast-warn';
    if (kind === 'error')   cls += ' kt-toast-error';
    el.className = cls;
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.setAttribute('data-testid', 'kt-toast');
    el.innerHTML = escapeHtml(message) +
      '<button type="button" class="kt-toast-close" aria-label="Закрыть">×</button>';
    box.appendChild(el);

    function close() {
      if (!el.parentNode) return;
      el.classList.add('kt-toast-leaving');
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 220);
    }
    el.querySelector('.kt-toast-close').addEventListener('click', close);

    var ms = typeof opts.ms === 'number' ? opts.ms : 4000;
    if (ms > 0) {
      var timer = setTimeout(close, ms);
      el.addEventListener('mouseenter', function () { clearTimeout(timer); });
      el.addEventListener('mouseleave', function () {
        timer = setTimeout(close, 2500);
      });
    }
    return el;
  }

  // ---------------------------------------------------------------------
  // Авто-привязка: <form class="kt-confirm-form" data-confirm-message="...">
  //
  // Делает то же, что раньше делал onsubmit="return confirm(...)" —
  // только через HTML-модал. Поддерживаемые data-атрибуты:
  //   data-confirm-message  — текст сообщения (обязательно)
  //   data-confirm-title    — заголовок (опционально)
  //   data-confirm-ok       — текст кнопки подтверждения (опционально)
  //   data-confirm-cancel   — текст кнопки отмены (опционально)
  //   data-confirm-danger   — "1" → красная кнопка подтверждения
  //
  // Идиома использования в шаблоне:
  //   <form method="post" action="..." class="kt-confirm-form"
  //         data-confirm-message="Запустить?">
  //     ...
  //   </form>
  //
  // Если форму нужно подтверждать только при условии (как self-demotion
  // в users.html) — лучше вешать onsubmit-обработчик руками и вызывать
  // confirmDialog() явно, а класс kt-confirm-form не использовать.
  // ---------------------------------------------------------------------

  function wireConfirmForm(form) {
    if (form._ktConfirmWired) return;
    form._ktConfirmWired = true;
    form.addEventListener('submit', function (e) {
      // Если форма уже подтверждена и сабмитится повторно — пропускаем.
      if (form.dataset.confirmAccepted === '1') {
        // Сбрасываем флаг, чтобы следующий submit снова требовал подтверждения.
        form.dataset.confirmAccepted = '';
        return;
      }
      e.preventDefault();
      var message = form.dataset.confirmMessage || 'Подтвердите действие.';
      var opts = {};
      if (form.dataset.confirmTitle)  opts.title       = form.dataset.confirmTitle;
      if (form.dataset.confirmOk)     opts.okLabel     = form.dataset.confirmOk;
      if (form.dataset.confirmCancel) opts.cancelLabel = form.dataset.confirmCancel;
      if (form.dataset.confirmDanger === '1') opts.danger = true;
      confirmDialog(message, opts).then(function (ok) {
        if (!ok) return;
        form.dataset.confirmAccepted = '1';
        // submit() не вызывает onsubmit — но наш обработчик повесил
        // listener, и он выше пустит submit с дисабленной защитой.
        // Используем requestSubmit, если доступен (триггерит native
        // submit-семантику с валидацией и onsubmit-listenerами,
        // но наш guard уже пропустит). Иначе — submit().
        if (typeof form.requestSubmit === 'function') {
          form.requestSubmit();
        } else {
          form.submit();
        }
      });
    });
  }

  function wireAllConfirmForms(root) {
    (root || document).querySelectorAll('form.kt-confirm-form').forEach(wireConfirmForm);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      wireAllConfirmForms(document);
    });
  } else {
    wireAllConfirmForms(document);
  }

  // Глобальный экспорт.
  window.confirmDialog = confirmDialog;
  window.toastDialog   = toastDialog;
  window.ktWireConfirmForms = wireAllConfirmForms;
})();
