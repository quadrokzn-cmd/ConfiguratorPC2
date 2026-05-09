# MEMORY — индекс auto-memory

Этот файл — зеркало индекса auto-memory из `~/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/MEMORY.md`. Полные тексты записей лежат там же, в одноимённых файлах.

Назначение: любой Claude-чат, открывший репо, видит индекс памяти прямо в корне. Auto-memory — это механизм Claude Code, который грузится автоматически; этот файл нужен на случай, если чат смотрит в репо без auto-memory (другая машина, другой инструмент).

## Записи

- [Собственник-1 — domain expert по аукционам](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/user_domain_expert.md) — все вопросы по КТРУ, SKU, логике отбора — к собственнику, не к менеджеру
- [Пользователь общается на русском](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/user_language.md) — отвечать по-русски, технические идентификаторы — английские
- [Пороги и фильтры платформы — редактируемые из UI](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/feedback_ui_editable_settings.md) — любой бизнес-параметр → таблица `settings` + UI-экран, не env и не код
- [Я оркестратор, не исполнитель](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/feedback_orchestrator_role.md) — все технические задачи через промт для нового чата, не самому через Bash/sub-agents
- [Параллельность sub-agent'ов — потолок 4-5](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/feedback_subagent_parallelism.md) — больше → ловим 403 / Unable to connect / rate-limit подписки
- [Короткие сообщения, мелкие шаги](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/feedback_short_messages.md) — не вываливать большие технические описания разом, один вопрос за сообщение
- [Минимум ручных действий собственника](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/feedback_minimize_owner_actions.md) — промты «под ключ»: чат сам запускает скрипты и обновляет файлы, собственник только читает итог
- [Русский язык в коде и коммитах](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/feedback_russian.md) — комментарии/коммиты/CLI — по-русски, идентификаторы — английские
- [Пошаговая работа с явным подтверждением](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/feedback_stepwise.md) — после каждого шага ждать «продолжай», ничего не писать без команды
- [Курс USD/RUB — актуальный на день просчёта](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/feedback_usd_rub_rate.md) — брать с ЦБ, кэш на сутки, не хранить статично в .env
- [Проект КВАДРО-ТЕХ — конфигуратор ПК (legacy)](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/project_quadrotech.md) — контекст этапа 2.5В (устарело, см. CLAUDE.md и итоговый блок plans/2026-04-23-platforma-i-aukciony.md)
- [Стек ConfiguratorPC2 (legacy)](../../Users/quadr/.claude/projects/d--ProjectsClaudeCode-ConfiguratorPC2/memory/project_stack.md) — описание стека на момент 2026-04-22 (устарело, см. CLAUDE.md — реальный стек шире)
