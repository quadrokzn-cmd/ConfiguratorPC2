# Контроль дневного бюджета OpenAI.
#
# Источник правды — таблица api_usage_log (там уже накапливаются реальные
# расходы и парсера, и комментатора, и обогащения). Мы суммируем её за
# текущий день и сравниваем с лимитом (DAILY_OPENAI_BUDGET_RUB из .env).
#
# Состояния:
#   - ok       — расход < 80% лимита;
#   - warning  — расход ∈ [80%, 100%); на админку нужно повесить плашку;
#   - blocked  — расход ≥ 100%; POST /query должен отказать в вызове
#                process_query() и сохранить запрос с status='error'.

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings


# Порог, после которого показываем предупреждение админу.
_WARNING_RATIO = 0.80


@dataclass
class BudgetStatus:
    """Текущее состояние дневного бюджета OpenAI."""
    spent_rub: float       # уже потрачено сегодня
    limit_rub: float       # лимит из .env
    ratio: float           # доля 0..1+ (может превышать 1)
    state: str             # 'ok' | 'warning' | 'blocked'

    @property
    def percent(self) -> int:
        """Округлённый процент для отображения в UI."""
        return int(round(self.ratio * 100))

    @property
    def is_blocked(self) -> bool:
        return self.state == "blocked"

    @property
    def is_warning(self) -> bool:
        return self.state == "warning"


def get_today_spend_rub(session: Session) -> float:
    """Сумма cost_rub из api_usage_log за сегодня (по локальной дате сервера)."""
    row = session.execute(
        text(
            "SELECT COALESCE(SUM(cost_rub), 0) AS spent "
            "FROM api_usage_log "
            "WHERE started_at::date = CURRENT_DATE"
        )
    ).first()
    spent = row.spent if row is not None else 0
    if isinstance(spent, Decimal):
        return float(spent)
    return float(spent or 0.0)


def check_budget(session: Session) -> BudgetStatus:
    """Считает текущий расход за сутки и возвращает статус."""
    limit = float(settings.daily_openai_budget_rub)
    spent = get_today_spend_rub(session)
    # Защищаемся от нулевого или отрицательного лимита в .env —
    # трактуем как «бюджет отключён» (всегда ok).
    if limit <= 0:
        return BudgetStatus(spent_rub=spent, limit_rub=0.0, ratio=0.0, state="ok")

    ratio = spent / limit
    if ratio >= 1.0:
        state = "blocked"
    elif ratio >= _WARNING_RATIO:
        state = "warning"
    else:
        state = "ok"
    return BudgetStatus(spent_rub=spent, limit_rub=limit, ratio=ratio, state=state)


def upsert_daily_log(session: Session) -> None:
    """Пересчитывает строку за сегодня в daily_budget_log на основе
    фактических значений из api_usage_log. Вызывается после каждого
    запроса в web_service.save_query. Тихо проглатывает ошибки —
    это отчётный снэпшот, не блокирующий основной поток."""
    try:
        session.execute(
            text(
                "INSERT INTO daily_budget_log (date, total_cost_usd, total_cost_rub, calls_count, updated_at) "
                "SELECT "
                "    CURRENT_DATE, "
                "    COALESCE(SUM(cost_usd), 0), "
                "    COALESCE(SUM(cost_rub), 0), "
                "    COUNT(*), "
                "    NOW() "
                "FROM api_usage_log "
                "WHERE started_at::date = CURRENT_DATE "
                "ON CONFLICT (date) DO UPDATE SET "
                "    total_cost_usd = EXCLUDED.total_cost_usd, "
                "    total_cost_rub = EXCLUDED.total_cost_rub, "
                "    calls_count    = EXCLUDED.calls_count, "
                "    updated_at     = NOW()"
            )
        )
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
