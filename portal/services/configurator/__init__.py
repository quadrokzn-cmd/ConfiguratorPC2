# UI-4 (Путь B, 2026-05-11): сервисы конфигуратора ПК.
#
# Содержимое переехало из app/services/* в рамках мини-этапа UI-4 слияния
# портала и конфигуратора в одно FastAPI. Доступ из portal/routers/configurator/*.
#
# Состав:
#   - engine/         — движок сборки конфигурации (бывший app/services/configurator/)
#   - nlu/            — парсер NLU-запросов (gpt-4o-mini)
#   - compatibility/  — правила совместимости компонентов
#   - manual_edit/    — ручное редактирование компонентов через CSV
#   - enrichment/     — обогащение каталога (regex + openai_search + claude_code CSV)
#   - auto_price/     — автозагрузка прайсов поставщиков (cron-задачи в portal/scheduler.py)
#   - export/         — Excel/Word/email экспорт КП + курс ЦБ
#   - price_loaders/  — загрузка прайсов через orchestrator (CSV/HTTP/IMAP/SOAP)
#   - openai_service, web_service, web_result_view — рендер конфигурации
#   - spec_naming, spec_service, spec_recalc — пересчёт спецификаций
#   - budget_guard    — дневной лимит расходов OpenAI
#   - price_loader    — тонкая обёртка load_ocs_price для совместимости
#
# После UI-4.5 (Путь B, 2026-05-11): кросс-импорт `from app.services.auctions/catalog ...`
# в price_loaders/orchestrator.py заменён на `from portal.services.auctions/catalog ...`.
# Сами модули auctions/ и catalog/ переехали в portal/services/auctions/ и
# portal/services/catalog/ — общие зависимости портала, а не «конфигуратор».
