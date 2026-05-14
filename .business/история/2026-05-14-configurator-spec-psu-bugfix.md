# 2026-05-14 — Bugfix конфигуратора: AJAX «спецификация» + БП по запрошенной мощности

## 1. Задача

Собственник работал в конфигураторе (проект «Руслан, Рам-Строй»,
конфигурация «Компьютер (Intel Core i7, 16Gb DDR4, 512 SSD, 550W)»,
вариант Intel) и поймал два бага сразу:

- **Баг А.** Клик на чекбокс «В спецификацию» → красный toast
  «Не удалось обновить спецификацию. Попробуйте ещё раз.» Сама
  спецификация остаётся «0 позиций». AJAX-запрос к back-end падает.
- **Баг B.** В названии заявлено «550W», в варианте Intel подобран
  Exegate 450W. Фильтр selector'а БП игнорирует требование мощности.
  Warning «БП < 550W» в блоке предупреждений отсутствует.

Оба бага лежат давно, не свежий регресс.

## 2. Как решал

### Discovery бага А

1. JS-handler чекбокса (`static/js/project.js:205-237`) делает POST на
   `/project/{PROJECT_ID}/select`.
2. Router (`portal/routers/configurator/projects.py`) объявлен с
   `prefix="/configurator"` и эндпоинтом `/project/{project_id}/select`
   → реальный URL `/configurator/project/{id}/select`.
3. Сверил с git-историей: префикс появился в commit `8938d34`
   мини-этапа **UI-4 (2026-05-11)** «перенос Конфигуратора в
   portal/configurator». Шаблоны переписали (formaction, href, fetch
   в HTML), а отдельный `static/js/project.js` — пропустили.
4. Все **7 AJAX-вызовов** в JS были на старом пути:
   `select / deselect / update_quantity / spec/reoptimize (full+row) /
    spec/rollback (full+row)`. Backend-тесты
   (`tests/test_portal/test_configurator_project_routes.py`)
   используют корректный `/configurator/project/...`, поэтому
   регрессию не поймали — нет end-to-end-проверки JS↔FastAPI.
5. Дополнительно в `project_detail.html:117` нашёлся **двойной**
   `/configurator/project/{id}/configurator/query/{id}/delete` —
   третий баг под капотом (кнопка удаления конфигурации не работала).

### Discovery бага B

1. `engine/builder.py:211` вызывает `required_psu_watts(build)` для
   фильтра БП.
2. `compatibility/rules.py:32-40`: функция **всегда возвращает
   константу `DEFAULT_PSU_WATTS = 400W`**, аргумент `build` не
   используется. Комментарий гласит «комментарий выше», и комментарий
   объясняет, что отказались от формулы по TDP из-за пустых полей.
3. NLU-схема (`nlu/parser.py:120-126` и `nlu/prompts/parser_system.txt`)
   не имеет поля для PSU watts. Текст «550W» из названия конфигурации
   терялся ещё на парсере.
4. Селектор: `get_cheapest_psu(min_watts=400)` берёт ближайший
   ≥400W (Exegate 450W). Warning shortage'а никем не генерируется
   (`engine/warnings.py` имеет только общий `W_PSU_POWER` и
   `W_GPU_EXTRA_POWER`).

### Fix

**Баг А — простой:** заменил 7 URL'ов в `static/js/project.js`
с `/project/...` → `/configurator/project/...`. Поправил двойной
префикс в `project_detail.html`. Добавил статический regression-тест
`tests/test_portal/test_configurator_js_urls.py`: парсит `project.js`,
вытаскивает `post('/...')` / `fetch('/...')`, реконструирует URL
(подставляя `PROJECT_ID`, `itemId`) и проверяет каждый против
реальных роутов FastAPI. Тот же тест ловит двойные префиксы в шаблоне.

**Баг B — структурный:**
- `engine/schema.py`: новое поле `BuildRequest.min_psu_watts: int | None`.
  Учтено в `is_empty()`, валидируется через `_as_positive_int`.
- `engine/builder.py`: `effective_min_watts = max(base_req_watts,
  req.min_psu_watts or 0)`. Если БП такой мощности нет — **fallback
  на base с warning'ом shortage'а**. Зеркальная логика для сценария B
  (корпус со встроенным БП).
- `engine/warnings.py`: функция `psu_watts_shortage(requested,
  actual)` → «Подобран БП {actual}W при запрошенных {requested}W —
  недостаточно мощности».
- `nlu/parser.py`: `psu_min_watts` добавлен в `_OVERRIDE_INT_KEYS`.
- `nlu/prompts/parser_system.txt`: правило извлечения «БП 550W»,
  «550 Вт», «550W в скобочной спецификации». Описано отделение от
  model_mentions (если указана модель — мощность задаёт сам компонент).
- `nlu/pipeline.py`: **regex-fallback `_augment_psu_watts_from_text`**
  на случай, если LLM пропустит мощность. Диапазон 250–2000W
  (ниже — TDP CPU/GPU, выше — нереалистично). Не перезаписывает
  значение, если парсер уже извлёк.
- `nlu/request_builder.py`: проброс `overrides.psu_min_watts` в
  `BuildRequest.min_psu_watts`.

### Тесты

- `tests/test_configurator/test_psu_min_watts.py` — 4 теста:
  фильтр выбирает ≥550W когда такой есть, fallback на 450W +
  shortage warning, запрос 350W (ниже базы) не даунгрейдит, сценарий B
  с corpus-included PSU тоже фильтруется.
- `tests/test_nlu/test_psu_watts_extraction.py` — 8 тестов: regex
  на реальную строку из скриншота, кириллические «Вт» и «ватт»,
  уважение парсера, отсечение TDP-значений и нереалистично больших,
  проброс через `request_builder`.
- `tests/test_portal/test_configurator_js_urls.py` — 3 теста:
  префикс в `project.js`, соответствие реальным FastAPI-роутам,
  отсутствие двойного префикса в шаблоне.

**Регрессия: 2055 passed, 4 skipped, 0 failed** (baseline 2039 → +16
новых тестов; одна flaky-ошибка в первом прогоне ушла при повторе,
к моим изменениям не относится — `test_supplier_inactive_excluded_from_prices`,
race в xdist).

### Deploy

- `git push origin feature/...` → ff-only merge в master → push.
- Railway auto-deploy (~30 сек): подтверждено через curl на
  `https://app.quadro.tatar/static/js/project.js` — 7 ссылок на
  `/configurator/project/`, 0 на старый `/project/`.
- POST на `/configurator/project/999/select` без CSRF → HTTP 302
  (redirect на login). Не 404 → роут живой. Полный UI-smoke
  (клик чекбокса в браузере) собственник делает вручную, потому
  что у меня нет cookie-сессии.

## 3. Решил?

- **Баг А — да.** URL'ы синхронизированы с FastAPI-префиксом, есть
  статический regression-тест от повторения той же ошибки при
  следующем переезде. Попутно починен скрытый double-prefix в
  delete-кнопке конфигурации (третий баг, не упомянутый в задаче).
- **Баг B — да.** Selector теперь уважает `min_psu_watts` из NLU,
  fallback гарантирует, что сборка не отвалится при отсутствии
  достаточно мощного БП, warning shortage'а отдаётся менеджеру.
  Извлечение из текста работает двумя путями: LLM-парсер (с правилом
  в промте) + детерминированный regex-fallback на случай промаха.

## 4. Эффективность / что лучше

**Хорошо:**
- Worktree-изоляция (`../ConfiguratorPC2-spec-psu`) — основная ветка
  не задета, прогон тестов в чистой копии.
- Два слоя извлечения PSU watts (промт + regex) дают надёжный
  результат: даже если OpenAI-модель сегодня посчитает 550W
  «информационным шумом», regex поймает.
- Fallback в builder'е — компромисс между **«отказать в сборке»**
  и **«молча выдать неподходящий БП»**. Сборка идёт, менеджер
  получает явный warning с цифрами.
- Static-тест JS↔FastAPI закрывает целый класс багов: следующий
  переезд префикса router'а просто не пройдёт CI.

**Что можно было лучше:**
- Регекс охватывает только числовой шаблон «N W/Вт/ватт»; формулировки
  вроде «БП помощнее», «киловатт», «1 кВт» он не ловит. Если
  обнаружится — расширим (на текущем prod-кейсе хватает).
- `psu_watts_shortage` выдаётся как warning, но если требование было
  очень критичное (например, серверная сборка под GPU 4090),
  shortage может быть фатальным, и пользователю нужен жёсткий отказ.
  Сейчас всегда warning + сборка. Если бизнес-кейс появится —
  добавить флаг `strict_psu_watts: bool`.
- Smoke на prod выполнен через curl на static и проверку HTTP-кода
  endpoint'а. Полный UI-smoke (клик собственника) остался ручным,
  потому что у меня нет login-cookie. Можно было сделать
  Playwright-test, но это overhead на разовую проверку.

## 5. Как было / как стало

### Код

| Файл | Было | Стало |
|---|---|---|
| `static/js/project.js` | 7 AJAX на `/project/{id}/...` | 7 AJAX на `/configurator/project/{id}/...` |
| `portal/templates/configurator/project_detail.html` | `delete_action="/configurator/.../configurator/query/..."` | `delete_action="/configurator/.../query/..."` |
| `engine/schema.py::BuildRequest` | без `min_psu_watts` | `min_psu_watts: int \| None`, учтено в `is_empty()` и `request_from_dict` |
| `engine/builder.py` | `min_watts=req_watts` (всегда 400) | `effective_min_watts = max(base, user_req)`, fallback на base + shortage warning |
| `engine/warnings.py` | только `W_PSU_POWER` | + `psu_watts_shortage(requested, actual)` |
| `nlu/parser.py` | `psu_min_watts` не валидируется | в `_OVERRIDE_INT_KEYS` |
| `nlu/prompts/parser_system.txt` | нет правил по мощности БП | правило «БП 550W» / «550W в спецификации» |
| `nlu/pipeline.py` | без regex-fallback | `_augment_psu_watts_from_text` (250–2000W) |
| `nlu/request_builder.py` | без проброса `psu_min_watts` | проброс в `min_psu_watts` BuildRequest |

### Тесты

| Файл | Тестов | Покрытие |
|---|---:|---|
| `tests/test_portal/test_configurator_js_urls.py` | 3 | JS-префикс, JS↔FastAPI, нет double-prefix в шаблоне |
| `tests/test_configurator/test_psu_min_watts.py` | 4 | селектор: фильтр / fallback / нижний запрос / сценарий B |
| `tests/test_nlu/test_psu_watts_extraction.py` | 8 | regex + проброс через request_builder |

Pytest: **2039 → 2055 passed** (+16), 0 failed.

### Prod

| Что | До 0975e8e | После |
|---|---|---|
| `app.quadro.tatar` чекбокс «В спецификацию» | toast «Не удалось обновить» | работает (POST 200 на `/configurator/project/{id}/select`) |
| Кнопка удаления конфигурации проекта | падала (404 на double-prefix URL) | работает |
| Подбор БП при запросе «550W» | выдавал 450W без warning'а | выдаёт ≥550W; если нет — 450W + warning «Подобран БП 450W при запрошенных 550W — недостаточно мощности» |

## 6. Открытые задачи

1. **UI-smoke на prod** — собственнику кликнуть чекбокс «В спецификацию»
   в проекте «Руслан, Рам-Строй» и убедиться, что toast'а нет, а
   позиции добавляются. Прогнать reoptimize и rollback — они тоже
   были в списке падавших AJAX.
2. **Recalc существующих конфигураций** — пересборка раннее
   созданных configurations пройдёт уже с новой логикой PSU.
   Если у собственника есть конфигурации, которые «нужно
   обновить» — кнопка reoptimize даст желаемый результат.
3. **Backlog**: расширить regex/promt на «1 кВт», «1000 ватт»,
   «БП помощнее» если такие формулировки появятся в живых запросах.
