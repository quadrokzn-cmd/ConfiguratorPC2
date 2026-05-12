# Resurs Media: переход с тестового API на prod (код-часть)

**Дата:** 2026-05-12.
**Канонический план:** `plans/2026-04-23-platforma-i-aukciony.md` (блок «Мини-этап 2026-05-12 Resurs Media переход на prod-API (код-часть)»).
**Предыдущие этапы:** smoke 2026-05-12 → Notification 2026-05-12 → Catalog delta 2026-05-12.

## 1. Поставленная задача

Собственник получил prod-креды от Сергея Волкова (ГК Ресурс-Медиа). Нужно подготовить код к работе против prod-API, не выставляя сами prod-креды в этом этапе (это сделает собственник руками на фазах B+C). Архитектурные решения зафиксированы в промте оркестратора:

1. Одна переменная `RESURS_MEDIA_WSDL_URL` (без `_TEST`).
2. Никакого `RESURS_MEDIA_ENV=test|prod` — URL сам задаёт окружение.
3. Sanity-check в `smoke` через явный CLI-флаг `--allow-prod` + интерактивный `YES`.
4. Аналогичная защита в `bootstrap`.
5. Bootstrap должен поддерживать `--env-file` для одновременной загрузки prod-кредов И prod-`DATABASE_URL`.

## 2. Как решал

- **Discovery** через `grep -rn "RESURS_MEDIA_WSDL_URL_TEST\|RESURS_MEDIA_WSDL_URL"` нашёл точки: fetcher (комментарий + fallback в `__init__`), smoke (`make_client()` + hard sanity), bootstrap (нет sanity, нет `--env-file`), 3 тест-файла с `monkeypatch.delenv("RESURS_MEDIA_WSDL_URL_TEST")`.
- **Общий хелпер** — `scripts/_resurs_media_safety.py::check_prod_safety(wsdl_url, allow_prod, *, input_fn=input, out=sys.stdout)`. Параметризованные `input_fn`/`out` нужны были чтобы unit-тесты не требовали `monkeypatch.setattr('builtins.input', ...)` — это упростило сами тесты и позволило прогонять их в parallel-xdist без коллизий.
- **Bootstrap:** перенёс `load_dotenv()` и тяжёлые import'ы (`shared.db`, fetcher, `upsert_catalog`) ВНУТРЬ `main()` после `parser.parse_args()`. Это важно — `shared.db::engine` инициализируется по `settings.database_url` в момент import'а; чтобы `--env-file .env.local.prod.resurs.v1` сработал, окружение нужно загрузить ДО первого import'а `shared.config`. Без этого `DATABASE_URL` остался бы dev'ым, и bootstrap писал бы в локальную БД.
- **Smoke:** заменил hard-проверку «WSDL не содержит 'test' → RuntimeError» на `check_prod_safety` с `--allow-prod`; добавил флаг в argparse и в `main()`.
- **Чистка `_TEST`:** удалил fallback-чтение в fetcher'е, упростил error-message, убрал три `monkeypatch.delenv("RESURS_MEDIA_WSDL_URL_TEST", raising=False)` в тестах (они были no-op без fallback). Финальный `grep -rn "RESURS_MEDIA_WSDL_URL_TEST" .` пуст.
- **Тесты:** 7 кейсов в `tests/test_resurs_media/test_safety.py` — 5 базовых из промта + 2 доп. (нижний регистр `yes` не считается, trailing whitespace стрипается).
- **Pytest:** прогон `python -m pytest -m "not live" -q` → **1905 passed, 1 skipped (live), 0 failed** в 130 сек. Прирост +7 от прошлого baseline 1898.
- **Документация:** `.env.example` обогащён блоком Resurs Media; план получил блок мини-этапа; `CLAUDE.md` упоминаний `_TEST` не содержит — править нечего.

## 3. Решил ли — да

DoD пройден целиком:
- `grep -rn "RESURS_MEDIA_WSDL_URL_TEST" .` пуст.
- Sanity-check работает по матрице промта (5 базовых кейсов + 2 доп.).
- pytest = 1905 passed (≥ 1903 порог из промта).
- Рефлексия с инструкцией собственника на фазы B+C+D (см. §6 ниже).
- Финальный коммит и push — следующий шаг.

## 4. Эффективно / что можно было лучше

**Эффективно:**
- Параметризация `input_fn`/`out` в хелпере вместо monkeypatch — тесты стали в 2 раза короче, без зависимостей от pytest-фикстур.
- Перенос `load_dotenv()` внутрь `main()` — единственное место, где `--env-file` мог сломаться, и я закрыл его сразу.

**Что можно было лучше:**
- Можно было бы добавить в bootstrap `--dry-run` (вызвать `check_prod_safety` и распечатать URL/USER без реального SOAP). Не входил в DoD, не делал — собственник может убедиться через ручной `--env-file` + Ctrl-C на YES-запросе. Если на фазе C обнаружится, что хочется явный dry-run — отдельный мини-промт.

## 5. Как было / как стало

**Было:**
- В коде fetcher'а и smoke жил fallback `RESURS_MEDIA_WSDL_URL` → `RESURS_MEDIA_WSDL_URL_TEST` (legacy с этапа разведки 12.4-РМ-0).
- В smoke стояла hard-проверка «WSDL не содержит 'test' → RuntimeError» — нельзя было запустить smoke против prod даже если действительно хочется (например для финальной валидации на staging-окружении РМ).
- Bootstrap не поддерживал `--env-file`, читал только `.env` в корне репо — нельзя было запустить против prod-БД без подмены `.env`.
- 3 тест-файла явно делали `monkeypatch.delenv("RESURS_MEDIA_WSDL_URL_TEST")` — техдолг от смены имени.

**Стало:**
- Одна переменная `RESURS_MEDIA_WSDL_URL`, разные значения per environment.
- Двойная защита от prod в CLI: `--allow-prod` + интерактивный `YES`. На test-URL — молча.
- `bootstrap` принимает `--env-file PATH` (override `.env`), `--allow-prod`, `--force` независимо.
- Общий sanity-хелпер `scripts/_resurs_media_safety.py` переиспользуется обоими скриптами.
- 7 unit-тестов гарантируют, что safety-логика не сломается при будущих правках.

## 6. Что собственнику делать руками

### Фаза B (Railway Variables)

1. **Railway production environment** — добавить через UI точечно (не Raw Editor, см. memory `feedback_railway_raw_editor_secrets`):
   - `RESURS_MEDIA_WSDL_URL`   = `<prod-URL из email Сергея>`
   - `RESURS_MEDIA_USERNAME`   = `<prod-логин>`
   - `RESURS_MEDIA_PASSWORD`   = `<prod-пароль>`
   - `RESURS_MEDIA_CLIENT_ID`  = `<prod-ClientID, если отличается от username>`

2. **Railway preprod environment** — оставить test-значения. Если существующая переменная называется `RESURS_MEDIA_WSDL_URL_TEST`:
   - Создать новую `RESURS_MEDIA_WSDL_URL` с тем же тестовым значением.
   - Удалить старую `RESURS_MEDIA_WSDL_URL_TEST` (теперь код её не читает).

3. **Dev-машина** — создать файл `.env.local.prod.resurs.v1` в корне репо (уже gitignored через паттерн `.env.local.prod*`):

   ```
   RESURS_MEDIA_WSDL_URL=<prod-URL>
   RESURS_MEDIA_USERNAME=<prod-логин>
   RESURS_MEDIA_PASSWORD=<prod-пароль>
   RESURS_MEDIA_CLIENT_ID=<prod-ClientID>
   DATABASE_URL=<prod-DATABASE_PUBLIC_URL из Railway prod postgres>
   ```

   `DATABASE_PUBLIC_URL` берётся из Railway → проект → environment `production` → service `Postgres` → tab «Variables» → переменная `DATABASE_PUBLIC_URL`.

### Фаза C (CLI bootstrap каталога против prod-БД)

```
python scripts/resurs_media_bootstrap_catalog.py \
    --env-file .env.local.prod.resurs.v1 \
    --allow-prod
```

Поведение:
1. Загрузит env из файла (override).
2. Распечатает большой WARNING «РАБОТА ПРОТИВ PRODUCTION RESURS MEDIA» с prod-URL.
3. Спросит `Введите 'YES' (заглавными) для продолжения:` — ввести `YES` (CAPS).
4. Дальше `GetMaterialData` без параметров (=весь каталог) и `upsert_catalog`. На prod заранее не знаем объём; на test-стенде было ~25 729 строк за ~15 сек. При попадании в rate-limit (Result=3) `_call_with_rate_limit` делает sleep + один retry; на повторном Result=3 — `RuntimeError`, скрипт упадёт. В этом случае подождать минуту и запустить снова.

Sanity после bootstrap (с той же dev-машины):

```
psql "<prod DATABASE_PUBLIC_URL>" -c "SELECT COUNT(*) FROM resurs_media_catalog;"
```

Ожидание: число ≫ 0 (несколько десятков тысяч позиций). Если 0 — bootstrap упал, перечитать stderr.

### Фаза D (мониторинг)

Следующий 07:40 МСК после фазы B/C — `auto_price_loads_resurs_media` в `portal/scheduler.py` на Railway prod автоматически тикнет против prod-API. Утром:

1. Открыть `/settings/...` или соответствующий UI с историей `auto_price_load_runs`.
2. Найти последний run для slug `resurs_media`, проверить:
   - Статус (`completed` / `failed` / `no_new_data`).
   - `report_json` → нет ли `errors > 0` в Notification или `rate_limited_pending` в delta-блоке.
   - `skus_updated` — должно быть > 0 (на prod-объёме весь каталог обновляется по дельте).
3. Если есть `errors` — посмотреть Railway logs за 07:40 МСК.

Если первый prod-тик прошёл чисто — этап Resurs Media закрыт целиком (smoke + Notification + Catalog delta + prod-switch).
