--
-- PostgreSQL database dump
--

\restrict 8umORUFCQPXAsaxNkr8vchrbnhoWfhqrLb8a51pgm0e57DZQibSM3cpvsFuJSz3

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
-- Data for Name: excluded_regions; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.excluded_regions (region_code, region_name, excluded, reason, changed_at, changed_by) FROM stdin;
primorsky	Приморский край	t	Логистика: слишком далеко	2026-04-25 22:24:26.997906+03	\N
sakhalin	Сахалинская область	t	Логистика: остров	2026-04-25 22:24:26.997906+03	\N
yakutia	Якутия	t	Логистика: дорогостоящая доставка	2026-04-25 22:24:26.997906+03	\N
kamchatka	Камчатский край	t	Логистика: дорогостоящая доставка	2026-04-25 22:24:26.997906+03	\N
magadan	Магаданская область	t	Логистика: дорогостоящая доставка	2026-04-25 22:24:26.997906+03	\N
chukotka	Чукотский АО	t	Логистика: дорогостоящая доставка	2026-04-25 22:24:26.997906+03	\N
kaliningrad	Калининградская область	t	Логистика: эксклав	2026-04-25 22:24:26.997906+03	\N
\.


--
-- PostgreSQL database dump complete
--

\unrestrict 8umORUFCQPXAsaxNkr8vchrbnhoWfhqrLb8a51pgm0e57DZQibSM3cpvsFuJSz3

