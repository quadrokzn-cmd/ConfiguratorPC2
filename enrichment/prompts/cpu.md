# Обогащение характеристик ПРОЦЕССОРОВ через Claude Code

Сначала прочти `_общие_правила.md`.

В этом этапе осталось всего ~6 полей у 2 серверных процессоров AMD EPYC,
которые регулярки не разобрали из-за нестандартного формата прайса
SuperMicro.

## Целевые поля
| Поле | Тип | Диапазон |
|---|---|---|
| `base_clock_ghz` | decimal | 0.5..6.0 |
| `turbo_clock_ghz` | decimal | 0.5..7.0, **обязательно ≥ base_clock_ghz** |
| `package_type` | enum | `"OEM"` или `"BOX"` |

## Где искать
- **AMD EPYC (Genoa / Turin)** — `amd.com/en/products/processors/server/epyc/`
  → конкретная модель → Specifications → "Base Clock", "Max. Boost Clock".
- **Intel Xeon** — `ark.intel.com/.../<sku>` → "Performance" → "Processor
  Base Frequency", "Max Turbo Frequency".

## Нюансы
- Указывай частоты в **GHz** с одной-двумя знаками после запятой
  (например, `3.0`, `3.65`). Если на сайте указано `3000 MHz` — переведи
  в `3.0`.
- `package_type`: для серверных EPYC из прайса SuperMicro по умолчанию
  `"OEM"` (если в наименовании прайса нет слов "boxed" / "BOX").

## Куда сохранить
`enrichment/done/cpu/batch_NNN.json`.
