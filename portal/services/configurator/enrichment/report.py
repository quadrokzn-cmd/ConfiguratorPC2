# Форматирование отчёта по итогам обогащения.

def format_report(stats: dict, *, dry_run: bool) -> str:
    """Собирает человекочитаемый отчёт по статистике одной категории."""
    cat = stats.get("category", "?")
    lines = []
    lines.append("=" * 72)
    title = f"Категория: {cat.upper()}"
    if dry_run:
        title += "   [DRY-RUN: запись в БД не выполнялась]"
    lines.append(title)
    lines.append("=" * 72)

    if stats.get("status") == "not_implemented":
        lines.append("Экстрактор ещё не реализован — пропущено.")
        return "\n".join(lines)

    total     = stats.get("total", 0)
    with_null = stats.get("with_null", 0)
    processed = stats.get("processed", 0)
    updated   = stats.get("updated", 0)
    errors    = stats.get("errors", 0)

    lines.append(f"Всего позиций в таблице:       {total}")
    lines.append(f"С хотя бы одним NULL-полем:    {with_null}")
    lines.append(f"Обработано:                    {processed}")
    lines.append(f"  из них записано 1+ поля:     {updated}")
    lines.append(f"Ошибок:                        {errors}")
    lines.append("")
    lines.append("Покрытие по обязательным полям:")

    base = with_null if with_null else 1  # защита от деления на 0
    for f, filled in stats.get("field_stats", {}).items():
        unfilled = stats.get("unfilled_fields", {}).get(f, 0)
        pct = filled / base * 100
        lines.append(
            f"  {f:28} заполнено: {filled:4}  ({pct:5.1f}%)   осталось NULL: {unfilled}"
        )

    return "\n".join(lines)
