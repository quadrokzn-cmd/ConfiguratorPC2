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
# КРОСС-ИМПОРТ (временно, до отдельного мини-этапа UI-4.5): price_loaders/orchestrator.py
# импортирует app.services.auctions.* и app.services.catalog.* — эти модули
# остаются в app/services/ до их переноса в shared/ (или portal/services/).
