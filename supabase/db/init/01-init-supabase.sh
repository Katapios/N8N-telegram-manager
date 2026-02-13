#!/usr/bin/env bash
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  create extension if not exists pgcrypto;
  create extension if not exists vector;

  do
  \$\$
  begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then
      create role anon nologin noinherit;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
      create role authenticated nologin noinherit;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'service_role') then
      create role service_role nologin noinherit bypassrls;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticator') then
      create role authenticator login noinherit password '${POSTGRES_PASSWORD:-change-me-strong-password}';
    else
      alter role authenticator with login password '${POSTGRES_PASSWORD:-change-me-strong-password}';
    end if;
    if not exists (select 1 from pg_roles where rolname = 'supabase_auth_admin') then
      create role supabase_auth_admin login noinherit createrole password '${POSTGRES_PASSWORD:-change-me-strong-password}';
    else
      alter role supabase_auth_admin with login password '${POSTGRES_PASSWORD:-change-me-strong-password}';
    end if;
    if not exists (select 1 from pg_roles where rolname = 'supabase_storage_admin') then
      create role supabase_storage_admin login noinherit createrole password '${POSTGRES_PASSWORD:-change-me-strong-password}';
    else
      alter role supabase_storage_admin with login password '${POSTGRES_PASSWORD:-change-me-strong-password}';
    end if;
    if not exists (select 1 from pg_roles where rolname = 'supabase_admin') then
      create role supabase_admin login noinherit createrole createdb password '${POSTGRES_PASSWORD:-change-me-strong-password}';
    else
      alter role supabase_admin with login password '${POSTGRES_PASSWORD:-change-me-strong-password}';
    end if;
    if not exists (select 1 from pg_roles where rolname = 'dashboard_user') then
      create role dashboard_user login noinherit password '${POSTGRES_PASSWORD:-change-me-strong-password}';
    else
      alter role dashboard_user with login password '${POSTGRES_PASSWORD:-change-me-strong-password}';
    end if;
  end
  \$\$;

  grant anon, authenticated, service_role to authenticator;

  create schema if not exists auth;
  create schema if not exists storage;
  create schema if not exists graphql_public;
  create schema if not exists realtime;
  create schema if not exists _realtime;
  create schema if not exists supabase_functions;

  grant usage on schema public to anon, authenticated, service_role;
  grant usage on schema storage to anon, authenticated, service_role;
  grant usage on schema graphql_public to anon, authenticated, service_role;

  alter default privileges in schema public grant select, insert, update, delete on tables to anon, authenticated, service_role;
EOSQL
