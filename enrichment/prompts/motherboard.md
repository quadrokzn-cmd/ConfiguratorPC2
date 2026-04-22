# Обогащение характеристик МАТЕРИНСКИХ ПЛАТ через Claude Code

Сначала прочти `_общие_правила.md`.

## Целевые поля
| Поле | Тип | Значения |
|---|---|---|
| `memory_type` | enum | `DDR3`, `DDR4`, `DDR5` (редко `DDR4+DDR5` для гибридных плат) |
| `has_m2_slot` | bool | `true`, если на плате есть хотя бы один M.2-слот |

## Где искать
- **ASUS** — `asus.com/.../motherboards-components/motherboards/.../<series>/spec/`,
  раздел "Memory" → стандарт памяти; "Storage" → "M.2 ...".
- **MSI** — `msi.com/Motherboard/<model>/Specification`. Memory → стандарт;
  M.2 Slot — раздел Storage.
- **Gigabyte / AORUS** — `gigabyte.com/Motherboard/<model>` →
  Specification → Memory / Storage.
- **ASRock** — `asrock.com/mb/<chipset>/<model>/index.asp` → раздел
  Specifications.
- **Supermicro (серверные)** — `supermicro.com/en/products/motherboard/<model>` →
  Memory: DDR4 RDIMM/LRDIMM …
- **Biostar** — `biostar.com.tw/app/en/mb/`.

## Важные нюансы
- `memory_type` пишем **базовый стандарт** платы. Если плата DDR5 —
  `"DDR5"`, не `"DDR5-5600"`. Гибридная плата (что встречается у некоторых
  Z690/B760) — `"DDR4+DDR5"`.
- `has_m2_slot`: достаточно одного M.2-разъёма (любого типа: SATA/NVMe).
  Если в spec-листе вообще нет упоминания M.2 — `false`.

## Куда сохранить
`enrichment/done/motherboard/batch_NNN.json`.
