# Обогащение характеристик МАТЕРИНСКИХ ПЛАТ через Claude Code

> Перед началом обязательно прочти `_общие_правила.md`.

## Целевые поля
| Поле | Тип | Значения |
|---|---|---|
| `memory_type` | enum | `"DDR3"`, `"DDR4"`, `"DDR5"`, `"DDR4+DDR5"` (гибрид) |
| `has_m2_slot` | bool | `true`, если есть хотя бы один M.2-разъём |
| `socket` | str | `"AM5"`, `"AM4"`, `"LGA1700"`, `"LGA1851"`, `"LGA1200"`, `"sTRX4"`, `"SP3"`, и т.п. |
| `chipset` | str | `"Z790"`, `"B650"`, `"X670E"`, `"H610"`, `"X299"` и т.п. |

> Хвост по `socket`/`chipset` — единичные позиции (1-2 платы), у которых
> regex не разобрал нестандартный формат прайса (часто SuperMicro или
> ОЕМ-комплект). Поэтому перед заполнением убедись, что плата
> существует — поищи `mpn` сначала на оф. сайте бренда; если нет
> вообще — `null` с пояснением.

## Где искать
- **ASUS** — `asus.com/.../motherboards-components/motherboards/.../<series>/spec/`,
  раздел "Memory" → стандарт памяти; "Storage" → "M.2 ...";
  верхняя строка spec — Socket / Chipset.
- **MSI** — `msi.com/Motherboard/<model>/Specification`.
- **Gigabyte / AORUS** — `gigabyte.com/Motherboard/<model>` →
  Specification → Memory / Storage / CPU.
- **ASRock** — `asrock.com/mb/<chipset>/<model>/index.asp` → раздел
  Specifications.
- **Supermicro (серверные)** — `supermicro.com/en/products/motherboard/<model>`.
- **Biostar** — `biostar.com.tw/app/en/mb/`.
- **AFOX / AFOX-corp** — `afox.eu`, `afox.ru`, `afox-corp.com`.

## Важные нюансы
- `memory_type` пишем **базовый стандарт** платы. Если плата DDR5 —
  `"DDR5"`, не `"DDR5-5600"`. Гибридная плата (что встречается у
  некоторых Z690/B760) — `"DDR4+DDR5"`.
- `has_m2_slot`: достаточно одного M.2-разъёма (любого типа: SATA/NVMe).
  Если в spec-листе вообще нет упоминания M.2 — `false`.
- `socket`: пиши uppercase без префиксов "Soc-", "Socket-". `LGA-1700`
  → `"LGA1700"`. AMD `Socket AM5` → `"AM5"`.
- `chipset`: уроверь, что это название чипсета, а не сокета. Например,
  `Intel Z790` → `"Z790"` (без "Intel"); `AMD X670E` → `"X670E"`.
  Серверные иногда без чипсета — тогда `null` + reason.

## Куда сохранить
`enrichment/done/motherboard/<имя_входного_файла>`.
