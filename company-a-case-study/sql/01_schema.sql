-- =====================================================================
-- Content Compliance Review Tool - schema, access rules, status logic
-- Run once in the Supabase SQL editor (as the project owner).
-- All access control lives HERE (RLS + SECURITY DEFINER functions),
-- never in the Streamlit app.
-- =====================================================================

-- ---------- Tables ----------------------------------------------------

create table public.profiles (
  id           uuid primary key references auth.users(id) on delete cascade,
  display_name text not null,
  role         text not null check (role in ('submitter','shariah','compliance')),
  region       text check (region in ('UK','UAE')),
  constraint reviewer_has_region check (
    (role = 'submitter' and region is null) or
    (role <> 'submitter' and region is not null))
);

-- The rulebook is DATA, loaded by the screening workflow at run time.
-- F2 has one row per region, so P3 (region-specific rules) is a plain filter.
create table public.rules (
  id            text not null,
  region        text not null check (region in ('All','UK','UAE')),
  team          text not null check (team in ('Shariah','Compliance')),
  severity      text not null check (severity in ('ABSOLUTE','ESCALATE','FIX')),
  rule_text     text not null,
  -- how the rule is checked:
  --   ai                       -> judged by the AI only
  --   must_contain             -> code: violated if required_text is absent
  --   if_contains_must_contain -> code: violated if trigger_text present and required_text absent
  check_kind    text not null default 'ai'
                check (check_kind in ('ai','must_contain','if_contains_must_contain')),
  trigger_text  text,
  required_text text,
  active        boolean not null default true,
  primary key (id, region)
);

create table public.submissions (
  id              uuid primary key default gen_random_uuid(),
  submitter_id    uuid not null default auth.uid() references public.profiles(id),
  title           text not null,
  region          text not null check (region in ('UK','UAE')),
  channel         text not null,
  content         text not null,
  version         int  not null default 1,
  parent_id       uuid references public.submissions(id),
  status          text not null default 'screening' check (status in
                  ('screening','auto_rejected','changes_needed','in_review',
                   'approved','rejected','screening_failed')),
  screening_error text,
  notes           text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index submissions_region_status on public.submissions(region, status);
create index submissions_submitter on public.submissions(submitter_id);
create index submissions_parent on public.submissions(parent_id);

create table public.findings (
  id             bigint generated always as identity primary key,
  submission_id  uuid not null references public.submissions(id),
  rule_id        text not null,
  team           text not null,
  severity       text not null,
  offending_text text,
  suggested_fix  text,
  source         text not null default 'ai' check (source in ('ai','deterministic')),
  created_at     timestamptz not null default now()
);
create index findings_submission on public.findings(submission_id);

create table public.reviews (
  submission_id uuid not null references public.submissions(id),
  team          text not null check (team in ('Shariah','Compliance')),
  decision      text not null default 'pending' check (decision in ('pending','approved','rejected')),
  reviewer_id   uuid references public.profiles(id),
  comment       text,
  decided_at    timestamptz,
  primary key (submission_id, team)
);

create table public.audit_log (
  id            bigint generated always as identity primary key,
  submission_id uuid not null references public.submissions(id),
  actor_id      uuid references public.profiles(id),   -- null = system (screening workflow)
  action        text not null,
  from_status   text,
  to_status     text,
  details       jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);
create index audit_submission on public.audit_log(submission_id);

-- ---------- Helpers (who is calling?) --------------------------------

create or replace function public.my_role() returns text
language sql stable security definer set search_path = public as $$
  select role from public.profiles where id = auth.uid()
$$;

create or replace function public.my_region() returns text
language sql stable security definer set search_path = public as $$
  select region from public.profiles where id = auth.uid()
$$;

-- One definition of "who can see a submission", reused by every policy.
create or replace function public.can_see_submission(p_id uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.submissions s
    where s.id = p_id
      and ( s.submitter_id = auth.uid()
            or (public.my_role() in ('shariah','compliance') and s.region = public.my_region()) )
  )
$$;

-- ---------- Row Level Security ----------------------------------------

alter table public.profiles    enable row level security;
alter table public.rules       enable row level security;
alter table public.submissions enable row level security;
alter table public.findings    enable row level security;
alter table public.reviews     enable row level security;
alter table public.audit_log   enable row level security;

-- Directory of names/roles (test data only; needed to show "who" in the audit log).
create policy profiles_read on public.profiles for select to authenticated using (true);
create policy rules_read    on public.rules    for select to authenticated using (true);

-- Submitter: own rows only. Reviewer: own region only.
create policy submissions_read on public.submissions for select to authenticated
  using ( submitter_id = auth.uid()
          or (public.my_role() in ('shariah','compliance') and region = public.my_region()) );

-- Only submitters can create, only for themselves, only as a fresh 'screening' v1.
create policy submissions_insert on public.submissions for insert to authenticated
  with check ( public.my_role() = 'submitter'
               and submitter_id = auth.uid()
               and status = 'screening'
               and version = 1 and parent_id is null
               and screening_error is null and notes is null );

create policy findings_read on public.findings  for select to authenticated using (public.can_see_submission(submission_id));
create policy reviews_read  on public.reviews   for select to authenticated using (public.can_see_submission(submission_id));
create policy audit_read    on public.audit_log for select to authenticated using (public.can_see_submission(submission_id));

-- No UPDATE / DELETE policies anywhere. Belt and braces: remove the privileges too.
revoke all on all tables in schema public from anon;
revoke insert, update, delete, truncate on public.profiles, public.rules, public.findings,
       public.reviews, public.audit_log from authenticated;
revoke update, delete, truncate on public.submissions from authenticated;

-- ---------- Audit + timestamps (triggers) -----------------------------

create or replace function public.tg_touch() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

create trigger submissions_touch before update on public.submissions
  for each row execute function public.tg_touch();

-- Every status change is written here, whoever caused it.
create or replace function public.tg_audit_status() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if tg_op = 'INSERT' then
    insert into public.audit_log(submission_id, actor_id, action, from_status, to_status, details)
    values (new.id, auth.uid(),
            case when new.parent_id is null then 'submitted' else 'resubmitted' end,
            null, new.status,
            jsonb_strip_nulls(jsonb_build_object('version', new.version, 'parent_id', new.parent_id)));
  elsif new.status is distinct from old.status then
    insert into public.audit_log(submission_id, actor_id, action, from_status, to_status, details)
    values (new.id, auth.uid(), 'status_changed', old.status, new.status,
            jsonb_strip_nulls(jsonb_build_object('screening_error', new.screening_error)));
  end if;
  return null;
end $$;

create trigger submissions_audit after insert or update of status on public.submissions
  for each row execute function public.tg_audit_status();

-- ---------- Status logic: called by the screening workflow only -------

-- The AI never sets a status. This function does, from the findings,
-- taking team + severity from the rules table (never from the AI).
create or replace function public.apply_screening_result(
  p_submission_id uuid, p_findings jsonb, p_notes text default null)
returns text
language plpgsql security definer set search_path = public as $$
declare
  s        public.submissions;
  v_status text;
  v_n      int;
begin
  select * into s from public.submissions where id = p_submission_id for update;
  if not found then raise exception 'submission % not found', p_submission_id; end if;
  if s.status <> 'screening' then
    raise exception 'submission % is %, not screening', p_submission_id, s.status;
  end if;
  if p_findings is null or jsonb_typeof(p_findings) <> 'array' then
    raise exception 'findings must be a JSON array';
  end if;

  delete from public.findings where submission_id = s.id;   -- clean slate on retry
  delete from public.reviews  where submission_id = s.id;

  insert into public.findings(submission_id, rule_id, team, severity, offending_text, suggested_fix, source)
  select s.id, r.id, r.team, r.severity,
         f->>'offending_text', f->>'suggested_fix',
         case when f->>'source' = 'deterministic' then 'deterministic' else 'ai' end
  from jsonb_array_elements(p_findings) f
  join public.rules r
    on r.id = f->>'rule_id' and r.active and r.region in ('All', s.region);
  get diagnostics v_n = row_count;

  -- An unknown or out-of-region rule id means the answer can't be trusted.
  if v_n <> jsonb_array_length(p_findings) then
    raise exception 'findings contain an unknown or out-of-region rule id';
  end if;

  -- P1: the most severe finding decides the status.
  if exists (select 1 from public.findings where submission_id = s.id and severity = 'ABSOLUTE') then
    v_status := 'auto_rejected';
  elsif exists (select 1 from public.findings where submission_id = s.id and severity = 'ESCALATE') then
    v_status := 'in_review';
    -- P4: every team with an ESCALATE finding must approve.
    insert into public.reviews(submission_id, team)
    select distinct s.id, team from public.findings
    where submission_id = s.id and severity = 'ESCALATE';
  elsif exists (select 1 from public.findings where submission_id = s.id and severity = 'FIX') then
    v_status := 'changes_needed';
  else
    v_status := 'in_review';
    insert into public.reviews(submission_id, team) values (s.id, 'Compliance');  -- final sign-off
  end if;

  update public.submissions
     set status = v_status, screening_error = null, notes = p_notes
   where id = s.id;
  return v_status;
end $$;

-- Any failure in the workflow lands here. Never approved.
create or replace function public.mark_screening_failed(p_submission_id uuid, p_reason text)
returns void
language plpgsql security definer set search_path = public as $$
begin
  update public.submissions
     set status = 'screening_failed',
         screening_error = left(coalesce(p_reason, 'unknown error'), 500)
   where id = p_submission_id and status = 'screening';
end $$;

-- ---------- Actions for signed-in users -------------------------------

-- Reviewer approves/rejects their team's part. The team comes from the
-- caller's role, the region from their profile - nothing is trusted from the client.
create or replace function public.decide_review(
  p_submission_id uuid, p_decision text, p_comment text default null)
returns text
language plpgsql security definer set search_path = public as $$
declare
  v_role   text := public.my_role();
  v_region text := public.my_region();
  v_team   text;
  s        public.submissions;
  v_status text;
begin
  if v_role is null or v_role not in ('shariah','compliance') then
    raise exception 'only reviewers can decide' using errcode = '42501';
  end if;
  if p_decision not in ('approved','rejected') then
    raise exception 'decision must be approved or rejected';
  end if;
  v_team := case v_role when 'shariah' then 'Shariah' else 'Compliance' end;

  select * into s from public.submissions where id = p_submission_id for update;
  if not found or s.region is distinct from v_region then
    raise exception 'submission not found' using errcode = '42501';   -- don't reveal other regions
  end if;
  if s.status <> 'in_review' then
    raise exception 'submission is %, not in review', s.status;
  end if;

  update public.reviews
     set decision = p_decision, reviewer_id = auth.uid(), comment = p_comment, decided_at = now()
   where submission_id = s.id and team = v_team and decision = 'pending';
  if not found then
    raise exception '% has no pending review on this submission', v_team;
  end if;

  insert into public.audit_log(submission_id, actor_id, action, details)
  values (s.id, auth.uid(), 'review_' || p_decision,
          jsonb_strip_nulls(jsonb_build_object('team', v_team, 'comment', p_comment)));

  if p_decision = 'rejected' then
    v_status := 'rejected';
  elsif not exists (select 1 from public.reviews where submission_id = s.id and decision <> 'approved') then
    v_status := 'approved';
  else
    v_status := 'in_review';   -- still waiting for the other team
  end if;

  if v_status <> s.status then
    update public.submissions set status = v_status where id = s.id;
  end if;
  return v_status;
end $$;

-- Compliance reviewer of the region picks up failed (or stuck >2 min) screenings.
-- Setting status back to 'screening' fires the database webhook -> n8n again.
create or replace function public.retry_screening(p_submission_id uuid)
returns void
language plpgsql security definer set search_path = public as $$
declare s public.submissions;
begin
  if public.my_role() is distinct from 'compliance' then
    raise exception 'only compliance reviewers can retry screening' using errcode = '42501';
  end if;
  select * into s from public.submissions where id = p_submission_id for update;
  if not found or s.region is distinct from public.my_region() then
    raise exception 'submission not found' using errcode = '42501';
  end if;

  if s.status = 'screening' and s.updated_at < now() - interval '2 minutes' then
    update public.submissions
       set status = 'screening_failed', screening_error = 'No screening result after 2 minutes'
     where id = s.id;
  elsif s.status <> 'screening_failed' then
    raise exception 'only failed or stuck screenings can be retried';
  end if;

  update public.submissions set status = 'screening', screening_error = null where id = s.id;
end $$;

-- Submitter edits and resubmits: always a NEW version, so an auto-rejected
-- version can never be approved.
create or replace function public.resubmit(
  p_parent_id uuid, p_title text, p_channel text, p_content text)
returns uuid
language plpgsql security definer set search_path = public as $$
declare
  p    public.submissions;
  v_id uuid;
begin
  select * into p from public.submissions where id = p_parent_id;
  if not found or p.submitter_id is distinct from auth.uid() then
    raise exception 'submission not found' using errcode = '42501';
  end if;
  if p.status not in ('changes_needed','auto_rejected','rejected','screening_failed') then
    raise exception 'a % submission cannot be resubmitted', p.status;
  end if;
  if exists (select 1 from public.submissions where parent_id = p.id) then
    raise exception 'this version has already been resubmitted';
  end if;

  insert into public.submissions(submitter_id, title, region, channel, content, version, parent_id)
  values (auth.uid(), p_title, p.region, p_channel, p_content, p.version + 1, p.id)
  returning id into v_id;
  return v_id;
end $$;

-- ---------- Function permissions --------------------------------------

revoke all on function public.apply_screening_result(uuid, jsonb, text) from public, anon, authenticated;
revoke all on function public.mark_screening_failed(uuid, text)         from public, anon, authenticated;
grant execute on function public.apply_screening_result(uuid, jsonb, text) to service_role;
grant execute on function public.mark_screening_failed(uuid, text)         to service_role;

revoke all on function public.decide_review(uuid, text, text)        from public, anon;
revoke all on function public.retry_screening(uuid)                  from public, anon;
revoke all on function public.resubmit(uuid, text, text, text)       from public, anon;
grant execute on function public.decide_review(uuid, text, text)     to authenticated;
grant execute on function public.retry_screening(uuid)               to authenticated;
grant execute on function public.resubmit(uuid, text, text, text)    to authenticated;

revoke all on function public.tg_audit_status() from public, anon, authenticated;
