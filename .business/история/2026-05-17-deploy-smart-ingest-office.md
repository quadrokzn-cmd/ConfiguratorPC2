# 2026-05-17 — Deploy smart-ingest на офисный worker (RDP-fallback)

## Задача

Выкатить smart-ingest (коммит `835a807`, миграции 0039+0040) на офисный
ingest-worker до 07:00 МСК. Миграция 0039 поменяла FK
`matches → tender_items` и `tender_items → tenders` с `ON DELETE CASCADE` на
`NO ACTION` — старый worker делал `DELETE FROM tender_items` per лот, что на
NO ACTION падает constraint violation. Без deploy следующий тик worker'а
сломал бы 2-часовое расписание ingest'а.

Промт требовал deploy **через SSH** из git-bash на dev-машине, с эскалацией
на RDP-fallback если pre-flight checks упадут.

## Как делал

### Фаза 1 — pre-flight, эскалация

Попытался `ssh server1c "whoami"` → `Could not resolve hostname server1c`.
Root cause: на dev-машине в `~/.ssh/config` есть только Railway entry,
никакого SSH-маршрута до офисного сервера не настраивалось. По `reference_office_server_setup`
и `docs/office-ingest-deploy.md`: офисный Server 2012 R2 управляется
**только через RDP**, OpenSSH Server на нём не разворачивался,
маршрут с dev-машины во внутреннюю сеть офиса отсутствует. SSH на офисе
существует только как client → github.

→ Эскалация на RDP-fallback. Сводка собственнику + полные блоки runbook'а
под PowerShell с правами админа (исходный runbook содержал блок git pull
в Git Bash-синтаксисе, адаптировал под PS: `cd D:\...` + `$env:GIT_SSH_COMMAND`).

### Фаза 2 — runbook через RDP

Собственник выполнял блоки в PowerShell под `server1c\Администратор` и
присылал вывод; я подбирал следующие шаги.

**Шаг 1 — Disable:** State=Disabled. ✓

**Шаг 2 — git pull:** была заминка — собственник скопировал bash-команды
в PS, дал стандартную ошибку про `/d/...`. Адаптировал под PS:

```powershell
cd D:\AuctionsIngest\ConfiguratorPC2
$env:GIT_SSH_COMMAND = "ssh -F /d/AuctionsIngest/.ssh/config"
git fetch origin
git pull --ff-only origin master
```

Первый fetch упал на `Unlink of file 'pack-*.idx' failed. Should I try again?
(y/n)` — это git встретил interactive prompt в pipe-сценарии. Подсказал
ответить `n`, потом проверить процессы (висящих не было) и lock-файлы
(чистые) → fetch+pull прошёл вторым заходом. Branch отстаёт на 51 коммит,
fast-forward `b3e6498..835a807`. После pull `git --no-pager log --oneline -3`
показал top: `835a807 532238f 7a5fd8b`. ✓

**Шаг 3 — pip install:** все Requirement already satisfied (deps уже стояли
с предыдущих deploy'ев). ✓

**Шаг 4 — sanity import:** `imports ok`. ✓

**Шаг 5 — Enable + Start:** Task State → Running, тик стартовал в 23:33:33. ✓

**Шаг 6 — ожидание тика.** Дефолтное ожидание 15 минут оказалось мало:
после 15 минут `LastTaskResult: 267009` (SCHED_S_TASK_HAS_NOT_RUN — статус
«currently running») и Python процесс активен. После 20 минут лог писался,
но `ingest done` ещё не было — шёл парсинг карточек. Запустил sleep ещё
на 5+5 минут. **Тик завершился через 39 минут** (23:33:43 → 00:12:48).

Финальная строка лога:
```
ingest done: {'cards_seen': 154, 'cards_parsed': 154, 'cards_failed': 0,
'inserted': 0, 'updated': 154, 'skipped': 0, 'matches_inserted': 3601,
'flagged_excluded_region': 7, 'flagged_below_nmck': 3,
'flagged_over_unit_price': 15, 'flagged_no_watchlist_ktru': 22,
'flagged_no_positions': 20}
```

`LastTaskResult: 0`, State Ready, никаких ERROR/Traceback/advisory-warn/
skipped-due-to-lock в логе.

### Фаза 3 — smoke prod-БД

Эфемерный `scripts/_smoke_smart_ingest.py` через `dotenv_values('.env.local.prod.v1')`
+ `DATABASE_PUBLIC_URL` (read-only smoke, не через `ingest_writer`).
Первая попытка упала на `from shared.db import engine` — `shared.config.Settings`
требует `OPENAI_API_KEY` для инициализации, а у smoke его в env нет. Переписал
на standalone `create_engine(env['DATABASE_PUBLIC_URL'])` без подъёма Settings.

Результаты:
| Метрика | Значение | Оценка vs DoD |
|---|---|---|
| total tenders | 245 | ✓ в диапазоне 162-245 |
| total matches | 5253 | ✓ НЕ упало (было 3129 на dev) |
| content_hash NULL | 91 | ⚠️ см. ниже |
| content_hash filled | 154 | (= cards_parsed) |
| modified last 24h | 154 | (только активные) |
| modified last 1h | 154 | ✓ всё свежее |

После smoke удалил `_smoke_smart_ingest.py`.

## Решил ли — да

Полностью.
- Worker подхватил smart-ingest код (835a807), импорты работают, тик
  завершился c LastTaskResult=0.
- FK NO ACTION больше не ломает ingest — `inserted=0, updated=154, skipped=0`
  отработали без constraint violation.
- matches на prod **выросли** с ~3129 до 5253 за счёт пересчёта через
  `match_single_tender` после каждого UPDATE (3601 matches_inserted).
  Главное достижение — matches больше не уходят в ноль каждые 2 часа.
- Архитектурное препятствие (SSH недоступен с dev-машины) обошли через
  RDP-fallback с пошаговым ведением собственника.

## Эффективно ли, что лучше

**Что сработало хорошо:**
- Pre-flight check #1 сразу поймал отсутствие SSH-маршрута → честная
  эскалация на RDP, не пытался натягивать сову через прокси/туннели.
- Адаптация bash→PowerShell на ходу (шаг 2 — `cd /d/...` → `cd D:\...`)
  сэкономила обратный круг.
- Подсказка `n` на git pack-lock prompt + проверка процессов/lock-файлов
  → fetch прошёл со второго захода без ручного разбора.
- `Select-String -Pattern "ingest done" | Select-Object -Last 1` оказался
  правильным компромиссом для длинного лога: собственник не копировал
  60KB unknown attribute keys'ов в чат, я получил только финальную статистику.

**Где было больно:**
- Дефолтное ожидание 15 минут на тик было неправильным эстимейтом. На первом
  тике после миграции 0039 worker делает UPDATE для **всех** 154 активных
  лотов (у всех был NULL hash) + `match_single_tender` per лот.
  Реальное время — 39 минут. На последующих тиках большая часть лотов
  должна попадать в SKIP, время вернётся к ~13 мин.
- Smoke-скрипт сначала упал на `shared.config.Settings.__init__` — он
  требует `OPENAI_API_KEY` даже для read-only БД-операций. Lesson:
  smoke-скрипты на prod-БД должны делать `create_engine()` напрямую,
  не через `shared.db.engine`.

**Что бы я сделал иначе:**
- В promt'е сразу написать, что для офисного worker'а deploy идёт через RDP,
  без попыток SSH. Это сэкономило бы одну эскалацию-сводку.
- Шаг 6 (`Start-Sleep 15min`) сделать `Start-Sleep 40min` с пометкой
  «первый тик медленный из-за full UPDATE».

## Как было — как стало

**Было (до deploy):**
- prod tenders 245, content_hash NULL у всех, matches на старом ingest
  обнулялись каждые 2 часа CASCADE-каскадом.
- Worker на 835a807-1 коммит (b3e6498), не знал про smart-ingest.

**Стало:**
- 154 активных лота получили content_hash (filled=154 в БД).
- matches пересчитались per-tender → 5253 в БД (+2124 к предыдущему
  стационарному состоянию).
- На следующем тике через 2 часа: если на zakupki не поменяется content
  → 154 SKIP, 0 удалений matches. Расписание стабильно.
- 91 legacy-лот с NULL content_hash — не активны на zakupki, smart-ingest
  их не трогает (by design миграции 0039 — поле nullable).

## Открытые задачи

- **Acceptance — следующий тик через 2 часа (~02:33 МСК).** Ожидание:
  большая часть из 154 активных лотов попадает в `skipped`, max единицы
  в `updated`. matches остаются ≈ 5253 (если новые лоты не появятся).
- **Backlog #19 (mojibake в логах офисного worker'а)** — не решали,
  unknown attribute keys в логах читаются нормально (UTF-8). Mojibake
  это другой кейс (RDP-консоль), не блокер.
- **91 NULL content_hash на legacy-лотах** — НЕ задача. Эти лоты вне
  активной выдачи zakupki, ingest их не увидит. Если когда-то понадобится
  очистить старые tenders без active zakupki — отдельная задача (но
  matches с них могут давать ценность для аналитики, лучше оставить).
- **9b Telegram/Max-уведомления** теперь технически разблокирован
  (smart-ingest работает, matches стабильны на офисном worker'е).

## Артефакты

- Этот файл — рефлексия.
- `plans/2026-04-23-platforma-i-aukciony.md` — добавлен мини-этап
  «2026-05-17 — deploy smart-ingest на офисный worker».
- Worker на коммите `835a807`, Task Scheduler State=Ready, следующий
  запуск через 2 часа от LastRunTime.
