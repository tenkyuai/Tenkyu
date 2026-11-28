-- One credit grant per user, written only by the tenkyu-signup Edge Function (service role).
create table if not exists public.credits (
  user_id     uuid primary key references auth.users (id) on delete cascade,  -- one row per user
  granted     integer not null check (granted >= 0),
  notice_pending boolean not null default false,  -- the account got 0 credits: show your "contact support" notice AFTER sign-in. Never in the sign-up response.
  decision_id text,                       -- Tenkyu decision id; null when the check was degraded (Tenkyu unreachable or flagged degraded), so outage grants are `decision_id is null and granted > 0`. Server-only.
  created_at  timestamptz not null default now()
);

-- An older copy of this table: add the flag.
alter table public.credits add column if not exists notice_pending boolean not null default false;

-- If the table already existed without a key (an older copy of this script, or hand-made), enforce
-- one row per user now. Without it, parallel sign-ups for one user could each insert a grant.
do $$
begin
  if not exists (
    select 1
    from pg_constraint c
    where c.conrelid = 'public.credits'::regclass
      and c.contype in ('p', 'u')
      and c.conkey = array[(select attnum from pg_attribute where attrelid = 'public.credits'::regclass and attname = 'user_id')]
  ) then
    alter table public.credits add constraint credits_user_id_key unique (user_id);
  end if;
end $$;

alter table public.credits enable row level security;

-- Users may read their own row. No insert/update/delete policy exists, so the browser
-- (anon or authenticated key) cannot write; the service role bypasses RLS.
drop policy if exists "credits: read own" on public.credits;
create policy "credits: read own" on public.credits
  for select to authenticated
  using ((select auth.uid()) = user_id);

-- Table privileges: start from nothing for the API roles, then grant only the columns the app shows.
-- decision_id and created_at stay unreadable: a decision id is a handle on Tenkyu's verdict about the
-- user, and a user must not be able to read it (or learn they were scored low). The service role is not
-- affected. The app must select explicit columns (`select user_id, granted, notice_pending`); `select *` is denied.
revoke all on public.credits from anon, authenticated;
grant select (user_id, granted, notice_pending) on public.credits to authenticated;
