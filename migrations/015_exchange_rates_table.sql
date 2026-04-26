-- Этап 9А.2.3: курс ЦБ хранится в БД с историей.
--
-- Раньше (этап 8.1): курс лежал в файловом JSON-кэше. На каждом инстансе свой,
-- автоматического обновления не было — менеджер видел курс «вчерашнего дня»
-- пока кто-нибудь не дёрнет экспорт.
--
-- Теперь:
--   - таблица exchange_rates хранит ВСЕ полученные курсы (история);
--   - APScheduler 5 раз в день (8:30, 13:00, 16:00, 17:00, 18:15 МСК)
--     ходит на ЦБ и кладёт сюда новый курс;
--   - страницы UI берут самый свежий курс по rate_date DESC, fetched_at DESC
--     и умножают на цены в USD «на лету».

CREATE TABLE IF NOT EXISTS exchange_rates (
    id              SERIAL PRIMARY KEY,
    rate_date       DATE NOT NULL,
    rate_usd_rub    NUMERIC(10, 4) NOT NULL,
    source          VARCHAR(20) NOT NULL DEFAULT 'cbr',
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(rate_date, source)
);

CREATE INDEX IF NOT EXISTS idx_exchange_rates_date
    ON exchange_rates(rate_date DESC);
