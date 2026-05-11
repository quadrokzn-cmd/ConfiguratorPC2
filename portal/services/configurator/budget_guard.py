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

from shared.config import settings


# Порог, после которого показываем предупреждение админу.
_WARNING_RATIO = 0.80


@dataclass
class BudgetStatus:
    """Текущее состояние дневного бюджета OpenAI."""
    spent_rub: float       # уже потрачено сегодня (по курсу на момент вызова)
    limit_rub: float       # лимит из .env
    ratio: float           # доля 0..1+ (может превышать 1)
    state: str             # 'ok' | 'warning' | 'blocked'
    # 9А.2.3: для перерасчёта в RUB по актуальному курсу в шаблонах.
    spent_usd: float = 0.0

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


def get_today_spend(session: Session) -> tuple[float, float]:
    """Сумма (cost_rub, cost_usd) из api_usage_log за сегодня.

    Возвращаем оба, чтобы UI мог пересчитать RUB по актуальному курсу через
    фильтр to_rub, а также сохранить старое поведение для совместимости.
    """
    row = session.execute(
        text(
            "SELECT COALESCE(SUM(cost_rub), 0) AS spent_rub, "
            "       COALESCE(SUM(cost_usd), 0) AS spent_usd "
            "FROM api_usage_log "
            "WHERE started_at::date = CURRENT_DATE"
        )
    ).first()
    if row is None:
        return 0.0, 0.0

    def _f(v):
        if isinstance(v, Decimal):
            return float(v)
        return float(v or 0.0)

    return _f(row.spent_rub), _f(row.spent_usd)


def get_today_spend_rub(session: Session) -> float:
    """Совместимый интерфейс для тестов и старого кода."""
    return get_today_spend(session)[0]


def check_budget(session: Session) -> BudgetStatus:
    """Считает текущий расход за сутки и возвращает статус."""
    limit = float(settings.daily_openai_budget_rub)
    spent_rub, spent_usd = get_today_spend(session)
    # Защищаемся от нулевого или отрицательного лимита в .env —
    # трактуем как «бюджет отключён» (всегда ok).
    if limit <= 0:
        return BudgetStatus(
            spent_rub=spent_rub, limit_rub=0.0, ratio=0.0, state="ok",
            spent_usd=spent_usd,
        )

    ratio = spent_rub / limit
    if ratio >= 1.0:
        state = "blocked"
    elif ratio >= _WARNING_RATIO:
        state = "warning"
    else:
        state = "ok"
    return BudgetStatus(
        spent_rub=spent_rub, limit_rub=limit, ratio=ratio, state=state,
        spent_usd=spent_usd,
    )


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
