// Общие UI-хелперы (этап 9А.2.3).
//
// Подключается на каждой странице через base.html.
// Содержит:
//   - kt-toggle: при переключении checkbox с классом .kt-toggle обновляет
//     текст рядом стоящего <span class="kt-toggle-text" data-toggle-text="<id>">
//     согласно data-toggle-on / data-toggle-off на самом input'е.
//
// Сейчас используется в /admin/components/<cat>/<id> (поля булевых
// характеристик типа «БП в комплекте») и в /admin/suppliers/new|edit
// (toggle is_active).

(function () {
  'use strict';

  function applyToggleText(input) {
    var id = input.id;
    if (!id) return;
    var span = document.querySelector(
      '.kt-toggle-text[data-toggle-text="' + id + '"]'
    );
    if (!span) return;
    var labelOn  = input.getAttribute('data-toggle-on')  || 'есть';
    var labelOff = input.getAttribute('data-toggle-off') || 'нет';
    span.textContent = input.checked ? labelOn : labelOff;
  }

  function wireToggle(input) {
    if (input._ktToggleWired) return;
    input._ktToggleWired = true;
    input.addEventListener('change', function () { applyToggleText(input); });
  }

  // На уже отрендеренные toggle'ы — сразу.
  document.querySelectorAll('input.kt-toggle').forEach(wireToggle);

  // На динамически появившиеся (если когда-нибудь будет ajax-редактор) —
  // через делегирование change-события.
  document.addEventListener('change', function (e) {
    var input = e.target;
    if (input && input.classList && input.classList.contains('kt-toggle')) {
      applyToggleText(input);
    }
  });
})();
