# Обогащение характеристик КОРПУСОВ через Claude Code

Сначала прочти `_общие_правила.md`.

Обогащение Case разбито на ДВА прогона. В первом батче целевые поля —
`has_psu_included` и `supported_form_factors`. После их импорта запускается
второй прогон только для `included_psu_watts` (см. `case_psu_pass: true`
в заголовке батча).

## 1-й прогон. Целевые поля
| Поле | Тип | Значения |
|---|---|---|
| `has_psu_included` | bool | `true`, если в комплекте идёт БП |
| `supported_form_factors` | list[str] | подмножество `{"E-ATX","ATX","mATX","ITX","XL-ATX","SSI-EEB","SSI-CEB"}` |

### Где искать
- **JONSBO** — `jonsbo.com/en/products/<series>` → Specification.
- **Fractal Design** — `fractal-design.com/products/cases/<series>/<model>` →
  раздел Specifications → "Motherboard compatibility" и "Power supply".
- **Lian Li** — `lian-li.com/product/<series>/<model>`.
- **NZXT** — `nzxt.com/product/<model>` → Tech Specs.
- **Phanteks** — `phanteks.com/<series>` → Specifications.
- **Thermaltake** — `thermaltake.com/products/<series>` → Specifications.
- **Chenbro** — `chenbro.com/.../products/...` (серверные/индустриальные).
- **Raspberry Pi** (корпуса для Pi) — `raspberrypi.com/products/<case>`. Для
  таких корпусов `supported_form_factors` оставляй `null` с пояснением
  "Корпус для одноплатного компьютера, не для ATX-плат".

### Нормализация форм-факторов
Возвращай каноничные значения:
- "Mini-ITX" → `"ITX"`
- "Micro-ATX" / "M-ATX" → `"mATX"`
- "Extended ATX" / "EATX" → `"E-ATX"`
- "ATX" → `"ATX"` без изменений.

Список не должен содержать дубликатов.

### `has_psu_included`
- `true` — если в spec-листе явно указан встроенный БП мощностью N Вт.
- `false` — если "Power Supply: Not Included" / "Поддерживаемый БП: ATX,
  не входит в комплект" / нет упоминания мощности БП.

## 2-й прогон. Целевое поле
| Поле | Тип | Диапазон |
|---|---|---|
| `included_psu_watts` | int | 100..2000 |

Поле обогащается **только** для корпусов, где в БД уже зафиксировано
`has_psu_included = TRUE`. Берём номинальную мощность встроенного БП в Вт
(например, для корпуса Inwin EM039 со встроенным БП 450 Вт — `450`).

## Куда сохранить
`enrichment/done/case/batch_NNN.json`.
