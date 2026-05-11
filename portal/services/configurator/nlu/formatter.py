# Форматирование финального ответа для менеджера.
#
# Цель — компактный текстовый блок: интерпретация запроса, табличка по
# вариантам, комментарий AI, список проверок и предупреждения. Вся
# презентация — здесь, чтобы pipeline остался про логику.

from __future__ import annotations

from portal.services.configurator.engine.schema import (
    BuildResult,
    ComponentChoice,
    Variant,
)


_CATEGORY_LABELS = {
    "cpu":         "CPU",
    "motherboard": "MB",
    "ram":         "RAM",
    "gpu":         "GPU",
    "storage":     "Storage",
    "psu":         "PSU",
    "case":        "Case",
    "cooler":      "Cooler",
}

_HR = "-" * 71


def _fmt_money(usd: float, rub: float) -> str:
    rub_int = int(round(rub))
    return f"${usd:,.0f} (≈ {rub_int:,} ₽)".replace(",", " ")


def _truncate(s: str, width: int) -> str:
    s = (s or "").strip()
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


def _short_model(c: ComponentChoice) -> str:
    """Чистит длинные «Процессор/ APU LGA1700 …» до читаемого имени."""
    m = c.model or ""
    # Если есть «/» — берём то, что после первого «/», там обычно человекочитаемое имя
    if "/" in m:
        m = m.split("/", 1)[1]
    m = m.strip()
    if c.quantity > 1:
        m += f" ×{c.quantity}"
    return _truncate(m, 40)


def _format_variant(v: Variant) -> list[str]:
    lines: list[str] = []
    title = f"Вариант {v.manufacturer} — {_fmt_money(v.total_usd, v.total_rub)}"
    if v.used_transit:
        title += "  [включает транзит]"
    lines.append(title)

    for c in v.components:
        cat = _CATEGORY_LABELS.get(c.category, c.category)
        model = _short_model(c)
        price_total_usd = c.chosen.price_usd * c.quantity
        supplier = c.chosen.supplier
        transit_mark = " (транзит)" if c.chosen.in_transit else ""
        lines.append(
            f"  {cat:<8} {model:<40} ${price_total_usd:>6,.0f}  {supplier}{transit_mark}"
            .replace(",", " ")
        )

    if v.warnings:
        lines.append("  Предупреждения сборки:")
        for w in v.warnings:
            lines.append(f"    • {w}")
    return lines


def _format_refusals(result: BuildResult) -> list[str]:
    if not result.refusal_reason:
        return []
    lines = ["К сожалению, подобрать сборку не удалось:"]
    for key, msg in result.refusal_reason.items():
        lines.append(f"  • {key}: {msg}")
    lines.append("Попробуйте увеличить бюджет или смягчить требования.")
    return lines


def format_empty(clarifying_questions: list[str]) -> str:
    """Текст ответа на пустой запрос."""
    qs = clarifying_questions or [
        "Для каких задач будет использоваться ПК?",
        "Какой примерный бюджет?",
    ]
    lines = [
        _HR,
        "Запрос слишком общий. Уточните, пожалуйста:",
    ]
    for q in qs:
        lines.append(f"  • {q}")
    lines.append(_HR)
    return "\n".join(lines)


def format_response(
    *,
    interpretation: str,
    result: BuildResult | None,
    comment: str = "",
    checks: list[str] | None = None,
    warnings: list[str] | None = None,
) -> str:
    """Главная функция: форматирует pipeline-результат для менеджера."""
    checks = checks or []
    warnings = warnings or []

    lines: list[str] = [_HR]
    if interpretation:
        lines.append(interpretation)
        lines.append(
            "Если что-то неверно — поправьте запрос и я пересчитаю."
        )
        lines.append("")

    if result is None:
        # Не было даже попытки подбора — это нормальный путь только для empty
        lines.append(_HR)
        return "\n".join(lines)

    if result.variants:
        for v in result.variants:
            lines.extend(_format_variant(v))
            lines.append("")
    else:
        lines.extend(_format_refusals(result))
        lines.append("")

    if comment:
        lines.append(f"Комментарий: {comment}")
        lines.append("")

    if checks:
        lines.append("Менеджеру проверить:")
        for ch in checks:
            lines.append(f"  • {ch}")
        lines.append("")

    if warnings:
        lines.append("Предупреждения:")
        for w in warnings:
            lines.append(f"  • {w}")
        lines.append("")

    if result is not None:
        lines.append(
            f"Курс ЦБ: {result.usd_rub_rate:.2f} ₽/$  "
            f"(источник: {result.fx_source})"
        )

    lines.append(_HR)
    return "\n".join(lines)
