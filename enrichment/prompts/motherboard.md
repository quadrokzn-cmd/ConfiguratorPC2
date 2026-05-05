# Обогащение характеристик МАТЕРИНСКИХ ПЛАТ через Claude Code

> Перед началом обязательно прочти `_общие_правила.md` — там общие
> требования к источникам, формат ответа и описание полей входного JSON
> (`mpn`, `gtin`, `raw_names`, `current`, `to_fill`).

Этап 11.6.2.7 закрывает финальный хвост видимых materinok без
`chipset` или `socket` — на 2026-05-05 это **3 NULL-ячейки в 2 строках**
из 963 видимых mb (chipset_null=2, socket_null=1). Все 3 — это
mining-платы AFOX (id=378 AFHM65-ETH8EX, id=379 AFB250-BTC12EX),
которые regex не разобрал из-за нестандартного формата прайса.

## Целевые поля и валидация

| Поле          | Тип   | Допустимые значения / формат |
|---------------|-------|-------------------------------|
| `memory_type` | enum  | `"DDR3"`, `"DDR4"`, `"DDR5"`, `"DDR4+DDR5"` (гибрид) |
| `has_m2_slot` | bool  | `true` если есть хотя бы один M.2-слот |
| `socket`      | str   | uppercase ASCII, 1..30 символов: `"AM5"`, `"AM4"`, `"LGA1700"`, `"LGA1851"`, `"LGA1200"`, `"LGA1151"`, `"sTRX4"`, `"SP3"`, `"BGA1023"` (mobile/embedded), и т.п. |
| `chipset`     | str   | uppercase ASCII, 1..30 символов: `"Z790"`, `"B650"`, `"X670E"`, `"H610"`, `"B250"`, `"HM65"`, и т.п. (без префикса `Intel`/`AMD`) |

> Валидатор делает `.upper().replace(" ","")` и применяет regex
> `^[A-Z0-9+\-]{1,30}$` — см. `_v_mb_socket` и `_v_mb_chipset`.
> Никаких пробелов, нижнего регистра, точек / слешей в значении быть
> не должно.

## ⚠ Защитный слой 1: mining-платы AFOX (актуально на 11.6.2.7)

Hard-coded факты по двум плитам, на которые осталось 3 NULL:

* **AFOX AFHM65-ETH8EX** (id=378): чипсет `HM65` (Intel HM65 Express
  Mobile), CPU встроен в плату (Celeron 1037U, BGA1023), отдельного
  процессорного сокета нет. На спецстранице AFOX socket указан как
  `BGA1023` или «Onboard». Источник: `afox.eu`/`afox-corp.com`.
* **AFOX AFB250-BTC12EX** (id=379): чипсет `B250` (Intel B250),
  socket уже `LGA1151` в БД — заполнять только chipset. Источник:
  `afox.eu`/`afox-corp.com`.

Все три whitelist-домена AFOX уже есть: `afox.eu`, `afox.ru`,
`afox-corp.com` (см. schema.py).

## ⚠ Защитный слой 2: socket BGA как «нет socket»

У плат с распаянным CPU (mini-ITX, embedded, mining-mobo с Celeron
1037U / N4500 / J4125) сокет физически не съёмный. Валидатор по
формату принимает `"BGA1023"`, `"BGA1170"`, `"BGA1296"` и т. п.
Если на оф. странице сокет указан как `Onboard CPU` без BGA-кода —
вернуть socket=null с reason «CPU распаян (BGA), отдельного socket
нет на плате».

## ⚠ Защитный слой 3: manufacturer / source

Если бренд из `raw_name` **не** извлекается или его оф. сайт вне
whitelist (см. `OFFICIAL_DOMAINS` в `schema.py`, motherboard-секция:
`asus.com`, `msi.com`, `gigabyte.com`, `aorus.com`, `asrock.com`,
`palit.com`, `zotac.com`, `pny.com`, `biostar.com.tw`, `matrox.com`,
`afox.eu`, `afox.ru`, `afox-corp.com`, `supermicro.com` — кросс
с GPU-AIB whitelist) — все поля `null` с reason «оф. сайт бренда
вне whitelist» / «карта не найдена».

## Где искать

* **ASUS** — `asus.com/.../motherboards-components/motherboards/.../<series>/spec/`.
* **MSI** — `msi.com/Motherboard/<model>/Specification`.
* **Gigabyte / AORUS** — `gigabyte.com/Motherboard/<model>` → Specification.
* **ASRock** — `asrock.com/mb/<chipset>/<model>/index.asp` → Specifications.
* **Biostar** — `biostar.com.tw/app/en/mb/`.
* **Supermicro (серверные)** — `supermicro.com/en/products/motherboard/<model>`.
* **AFOX** — `afox.eu/products/motherboards/<series>/<model>/`,
  `afox-corp.com` (международный головной сайт), `afox.ru`
  (российский).

## Нормализация значений

* `socket` — uppercase, БЕЗ пробелов и префиксов:
  * `LGA-1700`, `LGA 1700`, `Socket-LGA1700` → `"LGA1700"`.
  * `Soc-AM5`, `Socket AM5`, `AMD AM5` → `"AM5"`.
  * `Socket BGA1023` → `"BGA1023"`.
* `chipset` — uppercase, БЕЗ префикса вендора:
  * `Intel Z790`, `Intel® Z790` → `"Z790"`.
  * `AMD X670E`, `AMD® X670E Chipset` → `"X670E"`.
  * `Intel HM65 Mobile` → `"HM65"`.
  * `Intel B250 Express` → `"B250"`.
* `memory_type` — базовый стандарт без частоты: DDR4-3200 → `"DDR4"`.
* `has_m2_slot` — `true` при ≥1 M.2-разъёме (любого типа).

## Honest-null

* Карта по `mpn`/`gtin` не найдена ни на одном whitelist-домене —
  `null` + reason «карта не найдена».
* Чипсет / socket не указан явно в datasheet (часто на серверных и
  embedded SuperMicro) — `null` + reason «не указан в spec'ах».
* Сокет физически распаян (BGA), и нет конкретного BGA-кода в spec —
  `null` + reason «CPU распаян, отдельного socket нет».

## Формат ответа

Стандартный из `_общие_правила.md`. `source_url` обязателен на каждое
непустое значение (HTTPS, домен из whitelist), в корне — массив
`sources_used`.

## Куда сохранить

`enrichment/done/motherboard/<имя_входного_файла>` (то же имя, что у
входного batch-файла, чтобы автоматический импорт его подцепил).

## Чек-лист самопроверки

- [ ] `socket` / `chipset` ∈ `^[A-Z0-9+\-]{1,30}$` (uppercase, без пробелов).
- [ ] Префикс вендора `Intel`/`AMD` срезан в `chipset`.
- [ ] `memory_type` без скорости (DDR4, не DDR4-3200).
- [ ] Каждое непустое value сопровождает source_url с https + whitelist-домен.
- [ ] Заполнен `sources_used` в корне ответа.
- [ ] Ничего из `current` не дублировано в `fields`.
