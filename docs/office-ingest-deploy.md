# Деплой office-ingest-worker'а на офисный сервер (этап 9e.3)

**Цель:** развернуть `scripts/run_auctions_ingest.py` (CLI из 9e.2) на офисном
сервере в РФ со статическим IP, который не блокируется zakupki.gov.ru. Worker
пишет лоты напрямую в Railway-PG под ограниченной ролью `ingest_writer` (роль
заведена миграцией `migrations/0035_ingest_writer_role.sql` — этап 9e.1).
Запуск раз в 2 часа через Windows Task Scheduler.

**Зачем:** Railway-датацентр (US West / EU West Amsterdam) заблокирован
zakupki.gov.ru — prod-ingest из Railway физически невозможен. Офисный сервер
в РФ обходит блок; на pre-prod smoke (`curl https://zakupki.gov.ru/...`) от
2026-05-11 показал HTTP 200 на главной/поиске/карточке.

**Конфигурация офисного сервера** (фиксирована — реальный сервер):

| Параметр | Значение |
|---|---|
| ОС | Microsoft Windows Server 2012 R2 Standard (6.3.9600) |
| Пользователь | `server1c\Администратор` (IsAdmin=True), **кириллица в имени** |
| PowerShell | 4.0 (без PSReadLine, без `::new()`, без `Set-Clipboard`) |
| Свободно | D: 750 ГБ — основной диск под проект |
| Сеть до Railway-PG | `Test-NetConnection maglev.proxy.rlwy.net:32320 → True` |

**Жёсткие принципы:**
- Все пути проектные — в `D:\AuctionsIngest\` (на C: — кириллический
  `%USERPROFILE%`, ломает MSYS2/Git Bash).
- Пароли передаются только через интерактивный `Read-Host -AsSecureString` или
  через локальный notepad — никогда не через PowerShell-блок, который
  собственник копирует целиком (paste-в-Read-Host = катастрофа, см. 9e.1).
- Лог wrapper'а — UTF-8 (`PYTHONIOENCODING=utf-8 + cmd /c redirect`), чтобы
  кириллица в логах не превращалась в «каля-баляка».

---

## Перед стартом (что должно быть)

1. Pre-prod (или prod) роль `ingest_writer` создана — миграция
   `migrations/0035_ingest_writer_role.sql` применена (см. `docs/preprod-deploy.md`
   шаг Л; для prod — повторяется в 9e.4).
2. DSN роли (`postgresql+psycopg2://ingest_writer:<pwd>@host:port/railway?sslmode=require`)
   готов к копированию из dev-машины — на dev-машине в файле
   `.env.local.preprod.v2` (или `.env.local.prod.*`) под ключом
   `INGEST_WRITER_DATABASE_URL_PREPROD` (или `_PROD`).
3. Доступ по RDP к офисному серверу под `server1c\Администратор` (IsAdmin).
4. Доступ к GitHub-репо `quadrokzn-cmd/ConfiguratorPC2` — для добавления
   Deploy Key через браузер.

---

## Шаг А. Sanity-check сервера

Открой PowerShell в RDP и выполни **один блок целиком**:

```powershell
(Get-CimInstance Win32_OperatingSystem).Caption
$PSVersionTable.PSVersion
whoami
Get-PSDrive -PSProvider FileSystem | Select Name, @{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}
foreach ($cmd in @('python','py','git')) {
    $exe = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($exe) { "$cmd  ->  $($exe.Source)  ($(& $cmd --version 2>&1))" } else { "$cmd  ->  не найдено" }
}
Test-NetConnection -ComputerName maglev.proxy.rlwy.net -Port 32320 -InformationLevel Quiet
```

Должно быть: Windows Server 2012 R2 (или новее), PS 4+, IsAdmin, минимум 50 ГБ
свободно на D:, Python/Git отсутствуют (этот документ исходит из голой
системы), `Test-NetConnection → True`.

---

## Шаг Б. Установка Python 3.11

В браузере RDP-сессии открой прямую ссылку (НЕ через python.org/downloads —
там можно случайно попасть на release candidate):

> https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe

Запусти `.exe`:
- ✅ **Add python.exe to PATH** (внизу первого экрана — критично!)
- ✅ **Install for all users**
- **Customize installation** → Next → Advanced Options → Customize install
  location: `D:\Python311\` → Install
- Закрой все PowerShell-окна и открой новое (чтобы PATH подхватился)

Проверка:
```powershell
python --version
python -m pip --version
```
Ожидание: `Python 3.11.9` + `pip 24.0+ from D:\Python311\Lib\site-packages\pip`.

⚠️ Если получил `3.11.0rc1` или другой `rc*` — переустанови строго по ссылке
выше. RC-релизы не имеют security-патчей (особенно `ssl`/openssl).

---

## Шаг В. Установка Git for Windows

Скачай и установи с https://git-scm.com/download/win (кнопка «64-bit Git for
Windows Setup» автоматически отдаст последний release).

Все опции — **дефолты**:
- Editor: Vim (не критично, мы редактирование через git не делаем)
- PATH: «Git from the command line and also from 3rd-party software»
- SSH: «Use bundled OpenSSH»
- HTTPS: «Use the OpenSSL library»
- Line endings: «Checkout Windows-style, commit Unix-style»
- Terminal: «Use MinTTY»
- Credential helper: «Git Credential Manager»

После установки закрой все PowerShell-окна, открой новое:
```powershell
git --version
```
Ожидание: `git version 2.50+.windows.1` (точная версия не критична).

---

## Шаг Г. SSH Deploy Key для GitHub

**Кириллица в `%USERPROFILE%` ломает MSYS2** — генерируем ключ в пути без
кириллицы. В Git Bash (Start → Git Bash):

```bash
# Безопасный путь без кириллицы
mkdir -p /d/AuctionsIngest/.ssh
chmod 700 /d/AuctionsIngest/.ssh

# Ed25519, без passphrase (read-only deploy key — passphrase сделал бы Task
# Scheduler невозможным; компрометация ключа эквивалентна отзыву deploy key
# в GitHub UI, без последствий для аккаунта)
ssh-keygen -t ed25519 -C "office-server-ingest" \
    -f /d/AuctionsIngest/.ssh/id_ed25519_github -N ""

# SSH config с явными путями (известный nuance: github.com по дефолту требует
# ~/.ssh/known_hosts — мы перенаправляем туда же в /d/AuctionsIngest/.ssh/)
cat > /d/AuctionsIngest/.ssh/config <<'EOF'
Host github.com
    HostName github.com
    User git
    IdentityFile /d/AuctionsIngest/.ssh/id_ed25519_github
    IdentitiesOnly yes
    UserKnownHostsFile /d/AuctionsIngest/.ssh/known_hosts
EOF

chmod 600 /d/AuctionsIngest/.ssh/config /d/AuctionsIngest/.ssh/id_ed25519_github

# Показать публичную часть для копирования в GitHub
cat /d/AuctionsIngest/.ssh/id_ed25519_github.pub
```

Скопируй строку, начинающуюся с `ssh-ed25519 AAAA...`.

В **браузере на dev-машине** (НЕ в Claude Chrome):
1. GitHub → repo `quadrokzn-cmd/ConfiguratorPC2` → Settings → Deploy keys → **Add deploy key**.
2. Title: `Office Server (Ingest worker)`.
3. Key: вставь публичный ключ.
4. **НЕ ставь** «Allow write access» (read-only — обязательно).
5. Add key.

Проверка в Git Bash на офисе (первый раз ответь `yes` на fingerprint github.com):
```bash
ssh -F /d/AuctionsIngest/.ssh/config -T git@github.com
```
Ожидание: `Hi quadrokzn-cmd/ConfiguratorPC2! You've successfully authenticated...`
Если показывает `Hi <username>!` без имени репо — значит ключ распознан как
user-key, а не deploy key. Проверь, что добавил его в Settings репо
(а не Settings → SSH keys аккаунта).

---

## Шаг Д. Клонирование репо

В Git Bash:
```bash
cd /d/AuctionsIngest
GIT_SSH_COMMAND="ssh -F /d/AuctionsIngest/.ssh/config" \
    git clone git@github.com:quadrokzn-cmd/ConfiguratorPC2.git
```
Размер ~150 МБ (в истории есть 2 SQL-дампа ~55 МБ — backlog #8, чистить не
будем). Время ~1-2 минуты.

Проверка:
```bash
cd /d/AuctionsIngest/ConfiguratorPC2
ls -la | head -10
git log --oneline -1
```
Должна быть структура проекта (`app/`, `portal/`, `scripts/`, `requirements.txt`...)
и последний коммит совпадает с remote master.

> Будущие `git pull` для обновления кода: `cd /d/AuctionsIngest/ConfiguratorPC2
> && GIT_SSH_COMMAND="ssh -F /d/AuctionsIngest/.ssh/config" git pull`.

---

## Шаг Е. Virtualenv и зависимости

В PowerShell:
```powershell
cd D:\AuctionsIngest\ConfiguratorPC2

# venv без активации через Activate.ps1 (ExecutionPolicy Restricted блокирует;
# вызываем python из venv напрямую через путь)
python -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```
Время установки ~5-10 минут. Все wheel'ы для Python 3.11+Windows доступны
на PyPI, проблем со сборкой не должно быть.

Проверка:
```powershell
.\venv\Scripts\python.exe -m pip list | Select-String -Pattern "psycopg2|httpx|sqlalchemy|fastapi|lxml|beautifulsoup4|python-dotenv|apscheduler"
```
Должно вывести 8 пакетов.

---

## Шаг Ж. `.env` с DSN ingest_writer

⚠️ **Не используй `Read-Host` для пароля/DSN с copy-paste-блоком** — paste из
буфера в Read-Host под PS 4.0 без PSReadLine ловит мусор из следующих команд
буфера (на 9e.1 такая ошибка привела к утечке пароля через stderr-grep'а;
на 9e.3 — к битому `.env` без DSN).

Создаём через notepad:

**На dev-машине:** открой `.env.local.preprod.v2` (или `.env.local.prod.*`),
найди строку `INGEST_WRITER_DATABASE_URL_PREPROD=postgresql+psycopg2://...`,
**скопируй только значение** (всё после `=`, начиная с `postgresql+psycopg2://`).

**На офисе в PowerShell:**
```powershell
cd D:\AuctionsIngest\ConfiguratorPC2
notepad .env
```
Notepad спросит «Создать новый файл?» → Да.

В пустом notepad'е:
1. Набери на клавиатуре в английской раскладке: `INGEST_WRITER_DATABASE_URL=`
2. После `=` — **правый клик мыши** (вставит DSN из RDP shared clipboard).
3. Должна быть одна строка вида
   `INGEST_WRITER_DATABASE_URL=postgresql+psycopg2://ingest_writer:<pwd>@maglev.proxy.rlwy.net:32320/railway?sslmode=require`.
4. Ctrl+S → закрыть notepad.

Проверка размера (не показывает значение):
```powershell
$content = Get-Content .env -Raw
"Размер: $($content.Length) bytes"
"Префикс: $($content.Substring(0, [Math]::Min(35, $content.Length)))..."
Remove-Variable content
```
Ожидание: `Размер: ~140-160 bytes`, `Префикс: INGEST_WRITER_DATABASE_URL=postgre...`.

---

## Шаг З. Ручной тест CLI

```powershell
cd D:\AuctionsIngest\ConfiguratorPC2
.\venv\Scripts\python.exe scripts\run_auctions_ingest.py --env-file .env
```

Ожидание (зависит от того, сколько лотов на zakupki — обычно 100-200):
- В первые 10-20 сек: `INFO ingest CLI start`, `INFO Loading settings`, `INFO
  Settings loaded: ktru_watchlist=2 codes, excluded_regions=7`.
- Через 30 секунд: `INFO search aggregated: N unique reg_numbers from 2 ktru codes`.
- Дальше ~12-13 минут парсинг карточек (`INFO card 03... parsed`).
- В конце: `INFO ingest done: {'cards_seen': N, 'cards_parsed': N, ...}`.
- exit code 0.

Если стартует `permission denied for table <name>` — значит миграция 0035
не покрыла какую-то таблицу. Не правь миграцию молча — обратись к
оркестратору, чтобы расширил миграцией 0036 (для исключения unintended write).

---

## Шаг И. Wrapper-скрипт для логирования

Создаёт ингест-лог с timestamp в имени, в UTF-8, с ротацией 30 последних.

```powershell
$wrapper = @'
$ErrorActionPreference = 'Continue'
$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$logDir = "D:\AuctionsIngest\logs"
$logFile = Join-Path $logDir "ingest_$timestamp.log"

Set-Location D:\AuctionsIngest\ConfiguratorPC2

# Запуск через cmd /c — Python пишет stdout в UTF-8, cmd делает byte-level
# redirect в файл без вмешательства PS-кодировки. Никаких NativeCommandError.
& cmd /c "set PYTHONIOENCODING=utf-8 && venv\Scripts\python.exe scripts\run_auctions_ingest.py --env-file .env > `"$logFile`" 2>&1"

# Ротация: оставить только 30 последних логов
Get-ChildItem $logDir -Filter "ingest_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 |
    Remove-Item -Force
'@

New-Item -ItemType Directory -Path D:\AuctionsIngest\logs -Force | Out-Null
$enc = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText("D:\AuctionsIngest\run_ingest.ps1", $wrapper, $enc)
"OK: wrapper создан"
Get-Item D:\AuctionsIngest\run_ingest.ps1 | Format-List Length, LastWriteTime
```

Размер `run_ingest.ps1` должен быть ~800 байт.

Тест wrapper'а:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\AuctionsIngest\run_ingest.ps1
```
Ожидание: ~12-13 минут без вывода в PS-консоль (всё в файл). После завершения:
```powershell
Get-Content (Get-ChildItem D:\AuctionsIngest\logs\ingest_*.log |
              Sort-Object LastWriteTime -Descending | Select -First 1).FullName `
    -Encoding UTF8 -Tail 15
```
Финальные строки должны содержать `ingest done: {'cards_seen': N, ...}` + русские
названия атрибутов **читаются нормально** (если каля-баляка — wrapper кривой).

---

## Шаг К. Task Scheduler

### К.1. Запрос пароля учётки (одна строка — копируй только её)

```powershell
$pwd = Read-Host -AsSecureString "Введи пароль учётки Администратор (символы скрыты)"
```

⚠️ **Только эту строку — не блок!** После запуска `Read-Host` ждёт ввод;
введи пароль с клавиатуры (НЕ через paste — paste «утянет» следующие команды
из буфера как продолжение ввода). Enter после последнего символа.

### К.2. Создание задачи (один блок — копируй целиком)

```powershell
$plainPwd = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($pwd)
)

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File D:\AuctionsIngest\run_ingest.ps1" `
    -WorkingDirectory "D:\AuctionsIngest"

$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At ([DateTime]::Now.AddMinutes(2)) `
    -RepetitionInterval (New-TimeSpan -Hours 2) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName "AuctionsIngest" `
    -Description "Ingest аукционов с zakupki.gov.ru каждые 2 часа" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -User "$env:USERDOMAIN\$env:USERNAME" `
    -Password $plainPwd `
    -RunLevel Highest `
    -Force | Out-Null

Remove-Variable pwd, plainPwd

"OK: задача AuctionsIngest создана"
Get-ScheduledTask -TaskName "AuctionsIngest" | Format-List TaskName, State
```

Ожидание: `State : Ready`.

### К.3. Тестовый ручной запуск

```powershell
Start-ScheduledTask -TaskName "AuctionsIngest"
Start-Sleep -Seconds 3
Get-ScheduledTask -TaskName "AuctionsIngest" | Format-List TaskName, State, LastRunTime
```
Через ~13 минут проверь, что:
- Новый лог-файл создался в `D:\AuctionsIngest\logs\`.
- В логе финальная статистика `ingest done: ...`.
- State снова `Ready`.
- `Get-ScheduledTaskInfo -TaskName "AuctionsIngest"` показывает
  `LastTaskResult=0`, `NumberOfMissedRuns=0`.

---

## Шаг Л. Acceptance — 24 часа наблюдения

Task Scheduler автоматически запустит задачу каждые 2 часа (≈ 12 раз за сутки).
Проверяй раз в день:

```powershell
# Сколько прогонов за последние 24 часа
Get-ChildItem D:\AuctionsIngest\logs\ingest_*.log |
    Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-1) } |
    Measure-Object | Select-Object -ExpandProperty Count

# Последний прогон: статистика
$lastLog = (Get-ChildItem D:\AuctionsIngest\logs\ingest_*.log |
            Sort-Object LastWriteTime -Descending | Select -First 1).FullName
Get-Content $lastLog -Encoding UTF8 -Tail 3
```

Ожидание за 24 часа: 12 прогонов, все с exit code 0. Если хоть один упал —
смотри `Get-ScheduledTaskInfo` и соответствующий лог, тащи проблему в
оркестратор-чат.

---

## Откат (если нужно временно остановить)

```powershell
# Поставить на паузу (триггер остаётся, ручной запуск возможен)
Disable-ScheduledTask -TaskName "AuctionsIngest"

# Полное удаление задачи (репо и venv остаются)
Unregister-ScheduledTask -TaskName "AuctionsIngest" -Confirm:$false
```

---

## Что будет дальше (9e.4)

После 24 часов стабильной работы на pre-prod:
1. Применить миграцию 0035 на prod-БД (отдельный пароль через ту же процедуру
   tempfile + psql stdin redirection — см. `docs/preprod-deploy.md` шаг Л).
2. Сохранить prod-DSN в `.env.local.prod.v1` на dev-машине (gitignored).
3. На офисе переписать `D:\AuctionsIngest\ConfiguratorPC2\.env` на prod-DSN
   (тот же `INGEST_WRITER_DATABASE_URL=`, но указывает на prod).
4. Выключить APScheduler-job `auctions_ingest` в `portal/scheduler.py` для
   prod-режима (либо через переменную окружения, либо через `settings`
   тумблер) — Railway больше не запускает ingest для prod, только офис.
5. Pre-prod при этом продолжает работать через свой Railway-портовый job —
   он не мешает CLI с офиса (upsert идёт по `reg_number`, конфликта нет).
