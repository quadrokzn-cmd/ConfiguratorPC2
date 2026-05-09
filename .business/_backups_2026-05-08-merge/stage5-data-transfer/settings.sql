--
-- PostgreSQL database dump
--

\restrict YCypqyxEskzBu4GqaePars3HXo8Nw9qZEWrsnjD9dkxOkQP0qdolhe0vXQ5hcTV

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

--
-- Data for Name: settings; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.settings (key, value, updated_at, updated_by) FROM stdin;
margin_threshold_pct	15	2026-04-25 22:24:26.997906+03	\N
nmck_min_rub	30000	2026-04-25 22:24:26.997906+03	\N
max_price_per_unit_rub	300000	2026-04-25 22:24:26.997906+03	\N
contract_reminder_days	3	2026-04-25 22:24:26.997906+03	\N
deadline_alert_hours	24	2026-04-25 22:24:26.997906+03	\N
\.


--
-- PostgreSQL database dump complete
--

\unrestrict YCypqyxEskzBu4GqaePars3HXo8Nw9qZEWrsnjD9dkxOkQP0qdolhe0vXQ5hcTV

