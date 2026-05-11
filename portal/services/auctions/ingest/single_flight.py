"""Single-flight lock для ингеста аукционов.

Один и тот же модуль импортируется и из portal/scheduler.py (cron-job
каждые 2 часа), и из portal/routers/admin_auctions.py (POST /admin/
run-ingest{,-blocking}). Блокировка модульного уровня — общий объект
threading.Lock в рамках одного процесса. Это даёт:

- defensive guard: cron-tick и UI-кнопка не могут запустить два
  параллельных прогона ingest (zakupki забанит, plus двойная запись в
  tenders).
- Контракт: вызывающий пытается `acquire(blocking=False)`; если занято
  — пишет лог и тихо выходит. См. примеры в portal/scheduler.py и
  portal/routers/admin_auctions.py.
"""
from __future__ import annotations

import threading


# Один Lock на процесс. На Railway каждый сервис — отдельный процесс
# (configurator + portal), но ingest регистрируется только в портале,
# поэтому конкуренция возможна только в нём.
ingest_lock: threading.Lock = threading.Lock()


__all__ = ["ingest_lock"]
