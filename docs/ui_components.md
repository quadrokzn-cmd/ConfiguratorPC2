# UI-компоненты КВАДРО-ТЕХ — справка

Этап 9А.1.1: «свет, а не заливка». Тёмная enterprise-тема, синий —
точечный акцент. Здесь — короткая справка по тому, как делать
типичные элементы интерфейса в новых правилах. Полная таблица
токенов и workflow сборки — в [ui_design_system.md](ui_design_system.md).

## Палитра — что используем

| Роль                    | Токен                | Назначение                                 |
| ----------------------- | -------------------- | ------------------------------------------ |
| Фон страницы            | `surface.base`       | Чёрно-синий тёмный фон + radial-gradient   |
| Карточка                | `surface.1`          | Контейнер контента, чуть светлее фона      |
| Инпут / плашка          | `surface.2`          | Поле ввода, hover-фон                      |
| Hover-подложка          | `surface.3`          | Чуть ярче, для активных интерактивов       |
| Граница «белая мягкая»  | `line.soft`           | rgba(255,255,255,0.06) — тонкая рамка карточек |
| Граница hover           | `line.softer`         | rgba(255,255,255,0.10) — карточка в hover  |
| Бренд-синий             | `brand.500` = #2052E8 | Полоска активного пункта, primary, focus  |
| Свечение бренда         | `boxShadow.glow-brand` | Активная карточка, выбранная конфигурация |
| Свечение мягкое         | `boxShadow.glow-soft`  | Hover-карточка, hover-secondary-кнопки    |

Подробности — в [tailwind.config.js](../tailwind.config.js).

## Карточки

```html
{# Обычная #}
<section class="card card-pad">…</section>

{# Подсвеченная как «активная» — тонкая синяя граница + glow-brand #}
<section class="card card-pad card-active">…</section>
```

- В покое: фон `surface.1` + граница `line.soft` (≈6% белого).
- В hover: граница `line.softer`, добавляется `glow-soft`.
- Для «эта карточка выбрана / активна»: добавить `.card-active` —
  тонкая синяя граница с `glow-brand`. Никаких ring-2-классов.

Размеры внутреннего паддинга: `.card-pad-sm` (16), `.card-pad` (20),
`.card-pad-lg` (24).

## Кнопки

| Класс             | Когда использовать                                  |
| ----------------- | --------------------------------------------------- |
| `.btn-primary`    | Главное действие на экране (1-2 шт максимум)       |
| `.btn-secondary`  | Все вспомогательные действия. Тёмная, белая граница |
| `.btn-ghost`      | Inline-ссылки, действия в строке (текст + hover)   |
| `.btn-danger`     | Удалить, отписаться (явное опасное действие)       |
| `.btn-success`    | Только финальные «отправить» в модалках             |

```html
<button class="btn btn-md btn-primary">Подобрать конфигурацию</button>
<button class="btn btn-md btn-secondary">Скачать Excel</button>
<button class="btn btn-sm btn-ghost">отмена</button>
```

Размеры: `.btn-sm` (32px), `.btn-md` (36px), `.btn-lg` (44px).

**Иерархия по странице:** на странице нет более одной primary-кнопки.
На странице проекта три действия экспорта (Excel / КП / Цены) — все
secondary, чтобы не конкурировали между собой.

## Бейджи

```html
<span class="badge badge-neutral">1 конфигурация</span>
<span class="badge badge-brand">{{ icon('check') }} 1 в спецификации</span>
<span class="badge badge-warning">черновик</span>
```

Все бейджи — outline-стиль: тонкая граница в семантическом тоне +
лёгкая прозрачная плашка под текстом (≈6-8%). Не заливать сплошным.
Доступные: `neutral / brand / success / warning / danger / info`.

## Сайдбар

```html
<a href="/projects" class="nav-item nav-item-active">
  {{ icon('folder', class='nav-item-icon') }}
  <span>Проекты</span>
</a>
```

Активный пункт = добавить `.nav-item-active`. Стиль:
- НЕТ сплошной синей заливки фона.
- Фон чуть светлее (`surface.2`), вертикальная синяя полоса слева 3px
  с тёплым свечением.
- Иконка подсвечивается в `brand.400`.

Никогда не использовать `bg-brand-500/10` для активного пункта.

## Toggle-переключатель

Для бинарных «вкл/выкл», особенно тех, у которых важна заметность
(«В спецификацию» на варианте сборки):

```html
<input type="checkbox" class="toggle">
```

iOS-стиля свитч 36×20px. В active — заливается `brand.500` + glow.

## Точечные градиенты

- Фон страницы: тонкий radial-gradient в углах (5% brand-500), задан
  в `body::before` в [main.css](../static/src/main.css). Не трогать.
- Утилита `.brand-frame` — squircle-обёртка для логотипа в шапках,
  с лёгким brand-градиентом-подложкой и тонкой светлой рамкой.
- Утилита `.brand-caption` — мелкая uppercase-подпись «КОНФИГУРАТОР»
  под логотипом.
- Утилита `.hairline` — горизонтальная разделительная линия 1px
  цвета `line.soft` для секций.

## Где жить новым правилам

- [tailwind.config.js](../tailwind.config.js) — токены (цвета,
  тени, размеры). Если меняете brand или surface — здесь.
- [static/src/main.css](../static/src/main.css) — все компонентные
  классы. После правок: `npm run build:css`.
- [app/templates/_macros/icons.html](../app/templates/_macros/icons.html) —
  макрос `brand_mark` (логотип) и `icon` (lucide-style outline 1.75).
