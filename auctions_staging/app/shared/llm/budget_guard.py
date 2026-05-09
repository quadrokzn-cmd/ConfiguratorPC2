from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


class BudgetExceededError(RuntimeError):
    pass


@dataclass
class BudgetGuard:
    """Stub cost counter for LLM calls.

    MVP keeps this disabled; it activates in the post-MVP LLM-fallback project.
    """

    daily_limit_rub: float = 100.0
    enabled: bool = False
    _today: date = field(default_factory=date.today)
    _spent_rub: float = 0.0

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self._today:
            self._today = today
            self._spent_rub = 0.0

    def check(self, cost_rub: float) -> None:
        if not self.enabled:
            return
        self._reset_if_new_day()
        if self._spent_rub + cost_rub > self.daily_limit_rub:
            raise BudgetExceededError(
                f"Daily LLM budget {self.daily_limit_rub} RUB would be exceeded"
            )

    def record(self, cost_rub: float) -> None:
        if not self.enabled:
            return
        self._reset_if_new_day()
        self._spent_rub += cost_rub

    @property
    def spent_today_rub(self) -> float:
        self._reset_if_new_day()
        return self._spent_rub


guard = BudgetGuard()
