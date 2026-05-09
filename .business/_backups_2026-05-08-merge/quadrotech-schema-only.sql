--
-- PostgreSQL database dump
--

\restrict DklGSVEEfPc3G5fGiuKvdI4yw243aIghDOMM4AXoRtFjBhmKc9SmqgQh0Pvth0x

-- Dumped from database version 16.13
-- Dumped by pg_dump version 16.13

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: _migrations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public._migrations (
    filename text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public._migrations OWNER TO postgres;

--
-- Name: excluded_regions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.excluded_regions (
    region_code text NOT NULL,
    region_name text NOT NULL,
    excluded boolean DEFAULT true NOT NULL,
    reason text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_by text
);


ALTER TABLE public.excluded_regions OWNER TO postgres;

--
-- Name: ktru_catalog; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ktru_catalog (
    code text NOT NULL,
    name text,
    category text,
    required_attrs_jsonb jsonb DEFAULT '{}'::jsonb NOT NULL,
    optional_attrs_jsonb jsonb DEFAULT '{}'::jsonb NOT NULL
);


ALTER TABLE public.ktru_catalog OWNER TO postgres;

--
-- Name: ktru_watchlist; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ktru_watchlist (
    code text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    note text,
    display_name text
);


ALTER TABLE public.ktru_watchlist OWNER TO postgres;

--
-- Name: matches; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.matches (
    id bigint NOT NULL,
    tender_item_id bigint NOT NULL,
    nomenclature_id bigint NOT NULL,
    match_type text NOT NULL,
    rule_hits_jsonb jsonb DEFAULT '{}'::jsonb NOT NULL,
    price_total_rub numeric(14,2),
    margin_rub numeric(14,2),
    margin_pct numeric(7,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT matches_match_type_check CHECK ((match_type = ANY (ARRAY['primary'::text, 'alternative'::text])))
);


ALTER TABLE public.matches OWNER TO postgres;

--
-- Name: matches_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.matches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.matches_id_seq OWNER TO postgres;

--
-- Name: matches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.matches_id_seq OWNED BY public.matches.id;


--
-- Name: nomenclature; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.nomenclature (
    id bigint NOT NULL,
    sku text NOT NULL,
    mpn text,
    gtin text,
    brand text,
    name text NOT NULL,
    category text,
    ktru_codes_array text[] DEFAULT ARRAY[]::text[] NOT NULL,
    attrs_jsonb jsonb DEFAULT '{}'::jsonb NOT NULL,
    cost_base_rub numeric(14,2),
    margin_pct_target numeric(5,2),
    price_updated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    attrs_source text,
    attrs_updated_at timestamp with time zone
);


ALTER TABLE public.nomenclature OWNER TO postgres;

--
-- Name: nomenclature_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.nomenclature_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.nomenclature_id_seq OWNER TO postgres;

--
-- Name: nomenclature_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.nomenclature_id_seq OWNED BY public.nomenclature.id;


--
-- Name: price_uploads; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.price_uploads (
    id bigint NOT NULL,
    supplier_id bigint NOT NULL,
    filename text NOT NULL,
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
    uploaded_by text,
    rows_total integer DEFAULT 0 NOT NULL,
    rows_matched integer DEFAULT 0 NOT NULL,
    rows_unmatched integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'success'::text NOT NULL,
    notes text
);


ALTER TABLE public.price_uploads OWNER TO postgres;

--
-- Name: price_uploads_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.price_uploads_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.price_uploads_id_seq OWNER TO postgres;

--
-- Name: price_uploads_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.price_uploads_id_seq OWNED BY public.price_uploads.id;


--
-- Name: settings; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.settings (
    key text NOT NULL,
    value text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text
);


ALTER TABLE public.settings OWNER TO postgres;

--
-- Name: supplier_prices; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.supplier_prices (
    id bigint NOT NULL,
    supplier_id bigint NOT NULL,
    nomenclature_id bigint NOT NULL,
    supplier_sku text,
    price_rub numeric(14,2) NOT NULL,
    stock_qty integer DEFAULT 0 NOT NULL,
    transit_qty integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.supplier_prices OWNER TO postgres;

--
-- Name: supplier_prices_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.supplier_prices_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.supplier_prices_id_seq OWNER TO postgres;

--
-- Name: supplier_prices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.supplier_prices_id_seq OWNED BY public.supplier_prices.id;


--
-- Name: suppliers; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.suppliers (
    id bigint NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    adapter_class text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.suppliers OWNER TO postgres;

--
-- Name: suppliers_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.suppliers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.suppliers_id_seq OWNER TO postgres;

--
-- Name: suppliers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.suppliers_id_seq OWNED BY public.suppliers.id;


--
-- Name: tender_items; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.tender_items (
    id bigint NOT NULL,
    tender_id text NOT NULL,
    position_num integer NOT NULL,
    ktru_code text,
    name text,
    qty numeric(14,3) DEFAULT 1 NOT NULL,
    unit text,
    required_attrs_jsonb jsonb DEFAULT '{}'::jsonb NOT NULL,
    nmck_per_unit numeric(14,2)
);


ALTER TABLE public.tender_items OWNER TO postgres;

--
-- Name: tender_items_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.tender_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.tender_items_id_seq OWNER TO postgres;

--
-- Name: tender_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.tender_items_id_seq OWNED BY public.tender_items.id;


--
-- Name: tender_status; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.tender_status (
    tender_id text NOT NULL,
    status text DEFAULT 'new'::text NOT NULL,
    assigned_to text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_by text,
    note text,
    contract_registry_number text,
    contract_key_dates_jsonb jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT tender_status_status_check CHECK ((status = ANY (ARRAY['new'::text, 'in_review'::text, 'will_bid'::text, 'submitted'::text, 'won'::text, 'skipped'::text])))
);


ALTER TABLE public.tender_status OWNER TO postgres;

--
-- Name: tenders; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.tenders (
    reg_number text NOT NULL,
    customer text,
    customer_region text,
    customer_contacts_jsonb jsonb DEFAULT '{}'::jsonb NOT NULL,
    nmck_total numeric(14,2),
    publish_date timestamp with time zone,
    submit_deadline timestamp with time zone,
    delivery_deadline timestamp with time zone,
    ktru_codes_array text[] DEFAULT ARRAY[]::text[] NOT NULL,
    url text,
    raw_html text,
    flags_jsonb jsonb DEFAULT '{}'::jsonb NOT NULL,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.tenders OWNER TO postgres;

--
-- Name: users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    email text NOT NULL,
    role text NOT NULL,
    notify_telegram_chat_id bigint,
    notify_max_chat_id bigint,
    digest_time_msk time without time zone NOT NULL,
    digest_period text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_digest_period_check CHECK ((digest_period = ANY (ARRAY['yesterday'::text, 'today'::text]))),
    CONSTRAINT users_role_check CHECK ((role = ANY (ARRAY['manager'::text, 'owner'::text])))
);


ALTER TABLE public.users OWNER TO postgres;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO postgres;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: matches id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.matches ALTER COLUMN id SET DEFAULT nextval('public.matches_id_seq'::regclass);


--
-- Name: nomenclature id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.nomenclature ALTER COLUMN id SET DEFAULT nextval('public.nomenclature_id_seq'::regclass);


--
-- Name: price_uploads id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.price_uploads ALTER COLUMN id SET DEFAULT nextval('public.price_uploads_id_seq'::regclass);


--
-- Name: supplier_prices id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.supplier_prices ALTER COLUMN id SET DEFAULT nextval('public.supplier_prices_id_seq'::regclass);


--
-- Name: suppliers id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.suppliers ALTER COLUMN id SET DEFAULT nextval('public.suppliers_id_seq'::regclass);


--
-- Name: tender_items id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tender_items ALTER COLUMN id SET DEFAULT nextval('public.tender_items_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: _migrations _migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public._migrations
    ADD CONSTRAINT _migrations_pkey PRIMARY KEY (filename);


--
-- Name: excluded_regions excluded_regions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.excluded_regions
    ADD CONSTRAINT excluded_regions_pkey PRIMARY KEY (region_code);


--
-- Name: ktru_catalog ktru_catalog_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ktru_catalog
    ADD CONSTRAINT ktru_catalog_pkey PRIMARY KEY (code);


--
-- Name: ktru_watchlist ktru_watchlist_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ktru_watchlist
    ADD CONSTRAINT ktru_watchlist_pkey PRIMARY KEY (code);


--
-- Name: matches matches_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.matches
    ADD CONSTRAINT matches_pkey PRIMARY KEY (id);


--
-- Name: nomenclature nomenclature_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.nomenclature
    ADD CONSTRAINT nomenclature_pkey PRIMARY KEY (id);


--
-- Name: nomenclature nomenclature_sku_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.nomenclature
    ADD CONSTRAINT nomenclature_sku_key UNIQUE (sku);


--
-- Name: price_uploads price_uploads_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.price_uploads
    ADD CONSTRAINT price_uploads_pkey PRIMARY KEY (id);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (key);


--
-- Name: supplier_prices supplier_prices_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.supplier_prices
    ADD CONSTRAINT supplier_prices_pkey PRIMARY KEY (id);


--
-- Name: supplier_prices supplier_prices_supplier_id_nomenclature_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.supplier_prices
    ADD CONSTRAINT supplier_prices_supplier_id_nomenclature_id_key UNIQUE (supplier_id, nomenclature_id);


--
-- Name: suppliers suppliers_code_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.suppliers
    ADD CONSTRAINT suppliers_code_key UNIQUE (code);


--
-- Name: suppliers suppliers_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.suppliers
    ADD CONSTRAINT suppliers_pkey PRIMARY KEY (id);


--
-- Name: tender_items tender_items_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tender_items
    ADD CONSTRAINT tender_items_pkey PRIMARY KEY (id);


--
-- Name: tender_items tender_items_tender_id_position_num_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tender_items
    ADD CONSTRAINT tender_items_tender_id_position_num_key UNIQUE (tender_id, position_num);


--
-- Name: tender_status tender_status_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tender_status
    ADD CONSTRAINT tender_status_pkey PRIMARY KEY (tender_id);


--
-- Name: tenders tenders_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tenders
    ADD CONSTRAINT tenders_pkey PRIMARY KEY (reg_number);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: idx_matches_nomenclature; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_matches_nomenclature ON public.matches USING btree (nomenclature_id);


--
-- Name: idx_matches_tender_item; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_matches_tender_item ON public.matches USING btree (tender_item_id);


--
-- Name: idx_matches_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_matches_type ON public.matches USING btree (match_type);


--
-- Name: idx_nomenclature_attrs_source; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_nomenclature_attrs_source ON public.nomenclature USING btree (attrs_source);


--
-- Name: idx_nomenclature_brand; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_nomenclature_brand ON public.nomenclature USING btree (brand);


--
-- Name: idx_nomenclature_ktru; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_nomenclature_ktru ON public.nomenclature USING gin (ktru_codes_array);


--
-- Name: idx_nomenclature_mpn; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_nomenclature_mpn ON public.nomenclature USING btree (mpn);


--
-- Name: idx_supplier_prices_nomencl; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_supplier_prices_nomencl ON public.supplier_prices USING btree (nomenclature_id);


--
-- Name: idx_supplier_prices_supplier; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_supplier_prices_supplier ON public.supplier_prices USING btree (supplier_id);


--
-- Name: idx_tender_items_ktru; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_tender_items_ktru ON public.tender_items USING btree (ktru_code);


--
-- Name: idx_tender_status_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_tender_status_status ON public.tender_status USING btree (status);


--
-- Name: idx_tenders_ktru; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_tenders_ktru ON public.tenders USING gin (ktru_codes_array);


--
-- Name: idx_tenders_region; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_tenders_region ON public.tenders USING btree (customer_region);


--
-- Name: idx_tenders_submit_deadline; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_tenders_submit_deadline ON public.tenders USING btree (submit_deadline);


--
-- Name: matches matches_nomenclature_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.matches
    ADD CONSTRAINT matches_nomenclature_id_fkey FOREIGN KEY (nomenclature_id) REFERENCES public.nomenclature(id) ON DELETE CASCADE;


--
-- Name: matches matches_tender_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.matches
    ADD CONSTRAINT matches_tender_item_id_fkey FOREIGN KEY (tender_item_id) REFERENCES public.tender_items(id) ON DELETE CASCADE;


--
-- Name: price_uploads price_uploads_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.price_uploads
    ADD CONSTRAINT price_uploads_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.suppliers(id) ON DELETE CASCADE;


--
-- Name: supplier_prices supplier_prices_nomenclature_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.supplier_prices
    ADD CONSTRAINT supplier_prices_nomenclature_id_fkey FOREIGN KEY (nomenclature_id) REFERENCES public.nomenclature(id) ON DELETE CASCADE;


--
-- Name: supplier_prices supplier_prices_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.supplier_prices
    ADD CONSTRAINT supplier_prices_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.suppliers(id) ON DELETE CASCADE;


--
-- Name: tender_items tender_items_tender_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tender_items
    ADD CONSTRAINT tender_items_tender_id_fkey FOREIGN KEY (tender_id) REFERENCES public.tenders(reg_number) ON DELETE CASCADE;


--
-- Name: tender_status tender_status_tender_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tender_status
    ADD CONSTRAINT tender_status_tender_id_fkey FOREIGN KEY (tender_id) REFERENCES public.tenders(reg_number) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict DklGSVEEfPc3G5fGiuKvdI4yw243aIghDOMM4AXoRtFjBhmKc9SmqgQh0Pvth0x

