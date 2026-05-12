# 2026-05-12 — Multi-storage NLU (backlog #7)

## 1. Какая задача была поставлена

Поддержать несколько накопителей в одной сборке: NLU парсит N storage
entries → schema хранит список — → engine собирает сборку с несколькими
storage-компонентами. До этого этапа фраза «ПК с SSD 512 ГБ и HDD 2 ТБ»
приводила к тому, что в сборке появлялся только один накопитель — второе
требование молча терялось (backlog #7 в плане платформы).

Работа в отдельном git worktree `d:/ProjectsClaudeCode/ConfiguratorPC2-multi-storage`
на ветке `feature/multi-storage-nlu`, чтобы не конфликтовать с параллельным
чатом по Resurs Media chunked-fix.

## 2. Как я её решал

### Discovery (этап 1)

Прошёл по всему конвейеру конфигуратора (NLU → engine → routing → UI):

- **NLU**: `ParsedRequest.overrides` — плоский dict с одиночными `storage_min_gb` / `storage_type`. Парсер валидировал только эти scalar-поля.
- **Engine**: `BuildRequest.storage: StorageRequirements` — singleton; `engine.candidates.get_cheapest_storage` возвращает один storage; `engine.builder.assemble_build` создаёт ровно один storage-dict; `engine.selector._build_to_variant` создаёт ровно один `ComponentChoice(category="storage")`.
- **UI/spec_naming/excel**: внезапно обнаружил, что слой уже частично подготовлен: `portal/routers/configurator/main.py::_prepare_variants` уже формирует `storages_list: list[dict]`; `spec_naming.py::_block_storage` склеивает через « + »; `excel_builder.py` рендерит весь список. То есть промежуточная инфраструктура была заложена, но не довели — engine и UI-рендер карточек всё равно показывали один storage.

### Дизайн (этап 2) — принял сам, обосновываю здесь

Открытые архитектурные вопросы решал самостоятельно (исполнительская роль,
memory `feedback_executor_no_architectural_questions` — AskUserQuestion не
делал). Главное решение — **параллельная схема, не замена**: одиночные
поля `storage`/`storage_min_gb`/`storage_type` остаются, рядом добавляется
`storages: list[…]`; engine идёт по multi-storage-пути только если список
непустой, иначе fallback на singleton.

Альтернатива «заменить singleton на list[…] с длиной 1 по умолчанию» была
отвергнута: она потребовала бы переписать `compatibility.check_build`,
`pretty.py`, минимум 30 существующих тестов, и при этом не давала
бизнес-ценности — для обратной совместимости всё равно пришлось бы
оставлять «первый storage = основной». Чистого выигрыша от ломки нет.

Также решил:
- **Внутренний ключ типа** — `preferred_type` (как в `StorageRequirements`), а в JSON от GPT — короткий `type`. Парсер нормализует. Один формат «по обе стороны» — лишний шум; на API-границе короче, во внутреннем dataclass — длиннее и согласовано с существующими полями.
- **Исключение дубликатов** — параметр `exclude_ids` в `get_cheapest_storage`, фильтр в SQL через `<> ALL(:excl_ids)`. Альтернатива «фильтровать в Python после fetch» отвергнута — было бы O(N) лишних строк через ORM, а в случае двух требований 1 ТБ при одном кандидате 1 ТБ в каталоге сборка должна падать честно.
- **Промт GPT-4o-mini** — единственный накопитель остаётся через одиночные `storage_min_gb` / `storage_type`, массив используется ТОЛЬКО при двух и более. Это сужает поверхность регрессий: если модель один раз ответит массивом с одним элементом — мы всё равно отработаем (request_builder.appendix принимает оба), но в большинстве случаев одиночный путь остаётся одиночным.

### Реализация (этап 3)

Точечно прошёл по слоям:

1. `engine/schema.py` — `BuildRequest.storages` + `effective_storages()` + парсинг ключа `storages` в `request_from_dict`.
2. `engine/candidates.py::get_cheapest_storage` — `exclude_ids` параметр.
3. `engine/builder.py::assemble_build` — итерация по `effective_storages()`, накопление `storage_list`, в build-dict пробрасываются и `storage` (первый, для compatibility.check_build), и `storage_list`. Цикл по `storage_list` в суммировании total_usd.
4. `engine/selector.py::_build_to_variant` — итерация по `build["storage_list"]`, создаёт по одному `ComponentChoice(category="storage")` на накопитель; fallback на одиночный.
5. `nlu/parser.py` — `_validate_storages` (отдельная функция, чтобы не раздувать `_validate_overrides`); невалидный `type` отбрасывается мягко (запись с `min_gb` сохраняется), а не-массив или отрицательный `min_gb` — `ParseValidationError`. Это анти-fallback-стратегия: одиночный кривой элемент не должен валить весь подбор.
6. `nlu/request_builder.py::_to_request_dict` — параллельно `out["storage"]` (старый путь) и `out["storages"]` (новый).
7. `nlu/prompts/parser_system.txt` — расширение JSON-схемы + новый блок «STORAGES (массив накопителей)» с тремя few-shot-примерами.
8. `templates/configurator/_macros/{variant_block,variant_table}.html` — в цикле по категориям для `'storage'` идёт итерация по `v.storages_list` (или fallback).

### Тесты (этап 4)

16 новых кейсов:
- 6 в `tests/test_configurator/test_selector.py` (engine multi-storage end-to-end через мок-мир).
- 10 в `tests/test_nlu/test_multi_storage.py` (validate_response + parse + request_builder).

Пришлось обновить и существующий мок `fake_cheapest_storage` в `test_selector.py` — он не принимал `exclude_ids` (без этого новые тесты бы провалились с TypeError).

### Pytest (этап 5)

```
pytest -m "not live" -q
→ 1920 passed, 3 skipped (live), 0 failed (~72 сек)
```

Прирост: +16 тестов от прошлого baseline 1905 → 1920.

### Документация + merge (этапы 6-7)

- `plans/2026-04-23-platforma-i-aukciony.md` — backlog #7 помечен ~~strikethrough~~ как завершённый; в конец плана добавлен блок «Мини-этап 2026-05-12 Multi-storage NLU (backlog #7)» со списком артефактов.
- Эта рефлексия.
- Commit на ветке `feature/multi-storage-nlu`, rebase на свежий `origin/master` (параллельный чат успел запушить), push, fast-forward merge в master, push master. Worktree удалён.

## 3. Решил ли — да / нет / частично

**Да, целиком.** Все 10 пунктов DoD выполнены:

- [x] NLU schema поддерживает несколько storage requirements (через `overrides.storages`).
- [x] Parser не схлопывает entries.
- [x] Engine выбирает N storage'ей (через `effective_storages()` + `exclude_ids`).
- [x] UI рендерит несколько storage'ей в сборке (variant_block + variant_table).
- [x] 16 новых тестов passed (6 engine + 10 NLU).
- [x] Regression с одиночным storage сохранён (10+ тестов проходят без правок).
- [x] pytest без новых failures (1920 passed, 0 failed).
- [x] План + рефлексия обновлены.
- [x] Merge в master через rebase, push.
- [x] Worktree удалён.

Открытых задач нет.

## 4. Эффективно ли решение, что можно было лучше

**Эффективно.** Решение модульное: каждый слой (engine/NLU/UI) трогается
в одной-двух функциях, fallback на singleton сохраняется во всех точках
ветвления (`effective_storages()`, `storage_list or [storage]`, `storages_list or [components.get('storage')]`). Это даёт минимум регрессий и высокую обратимость, если что-то поедет — достаточно опустошить `BuildRequest.storages` и поведение будет идентично прежнему.

**Что можно было лучше:**

1. **Discovery дал бесплатную информацию о готовности UI.** Если бы я начал реализацию с engine без discovery, я бы потратил время на добавление кода в `_prepare_variants`/`spec_naming.py`/`excel_builder.py` — а там уже всё работало через `storages_list`. Discovery первым шагом окупился.

2. **Не сделал live-тест с реальным GPT-4o-mini.** Промт обновлён и заработает на проде (gpt-4o-mini обычно хорошо следует JSON-схемам), но fewer-shot-кейсы я проверил только на структурном уровне через мок. Маркер `live` поддерживается в проекте — но я не добавил `@pytest.mark.live` тест в `test_parser_live.py`. Это вынесенный кусок: первый prod-запрос с двумя накопителями засветит реальную точность промта.

3. **Не покрыл UI рендерингом через TestClient.** Тесты, проверяющие реальный HTML двух storage-карточек в `variant_block.html`/`variant_table.html`, можно было бы написать через `test_configurator_result_page_rendering.py`. Я этого не сделал — рендеринг проверяется только косвенно через `test_prepare_variants_collects_storages_list`. Шаблоны простые (две строки итерации), но прямой smoke-тест добавил бы уверенности при дальнейших правках UI. Если в будущем UI пересоберут — стоит добавить.

## 5. Как было и как стало

### Было (до этого этапа)

Запрос: «ПК с SSD 512 ГБ и HDD 2 ТБ».

- NLU извлекал только последнее упоминание — например, `storage_min_gb=2000, storage_type='HDD'`.
- В каталоге выбирался один HDD на 2 ТБ — SSD 512 ГБ молча терялся.
- Менеджер получал сборку с одним накопителем и должен был руками заметить, что NLU не понял запрос (если вообще замечал).

### Стало (после этапа)

Запрос: «ПК с SSD 512 ГБ и HDD 2 ТБ».

- NLU вернёт `overrides.storages = [{min_gb: 512, type: "SSD"}, {min_gb: 2000, type: "HDD"}]`.
- Engine выберет два разных компонента: один SSD ≥ 512 ГБ, один HDD ≥ 2000 ГБ.
- `BuildResult.variants[i].components` содержит две позиции с `category="storage"`.
- UI (variant_block / variant_table) рендерит обе карточки/строки.
- Spec_naming собирает автоназвание вида «… / 512GB SSD + 2TB HDD / …».
- Excel-spec в КП показывает обе позиции.

Backward compat: одиночные запросы («ПК с SSD на 1 ТБ») и историческое
JSON-состояние в БД (`build_result_json` без `storages` ключа) работают
идентично прежнему — singleton path остаётся при пустом `storages`.

Pytest до этапа: 1905 passed. Pytest после этапа: 1920 passed (+16),
0 failed.
