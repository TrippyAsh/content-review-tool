-- =====================================================================
-- Access-control proof. Run in the Supabase SQL editor (whole file at once).
-- It acts AS the real test users inside Postgres, using the same JWT claims
-- the API uses, so Row Level Security and the functions apply exactly as
-- they do for the app. Every forbidden action is attempted; if one ever
-- succeeded it is undone (raise -> rollback of that step).
-- Running this file changes nothing.
-- =====================================================================

select set_config('test.log', '', true),
       set_config('test.uae_id', (select id::text from public.submissions
                                  where region = 'UAE' order by created_at limit 1), true),
       set_config('test.a_sub',  (select s.id::text from public.submissions s
                                  join auth.users u on u.id = s.submitter_id
                                  where u.email = 'submitter.a@example.com'
                                  order by s.created_at limit 1), true),
       set_config('test.shariah_uk',     (select id::text from auth.users where email = 'shariah.uk@example.com'), true),
       set_config('test.compliance_uae', (select id::text from auth.users where email = 'compliance.uae@example.com'), true),
       set_config('test.sub_b',          (select id::text from auth.users where email = 'submitter.b@example.com'), true);

-- ---------------------------------------------------------------- Shariah Reviewer (UK)
select set_config('request.jwt.claims',
       json_build_object('sub', current_setting('test.shariah_uk'), 'role', 'authenticated')::text, true);
set local role authenticated;

select set_config('test.log', current_setting('test.log') || E'\n' ||
  'Shariah Reviewer (UK) | sanity: database role used | authenticated | ' || current_user, true);

select set_config('test.log', current_setting('test.log') || E'\n' ||
  'Shariah Reviewer (UK) | list all submissions | UK only | ' || count(*) || ' rows, regions: ' ||
  coalesce(string_agg(distinct region, ','), '-'), true)
from public.submissions;

select set_config('test.log', current_setting('test.log') || E'\n' ||
  'Shariah Reviewer (UK) | read a UAE submission + its findings + audit log | 0 rows | ' ||
  ((select count(*) from public.submissions where id = current_setting('test.uae_id')::uuid) +
   (select count(*) from public.findings    where submission_id = current_setting('test.uae_id')::uuid) +
   (select count(*) from public.audit_log   where submission_id = current_setting('test.uae_id')::uuid)) || ' rows', true);

do $$
begin
  begin
    perform public.decide_review(current_setting('test.uae_id')::uuid, 'approved', 'access test');
    raise exception 'ALLOWED';
  exception when others then
    perform set_config('test.log', current_setting('test.log') || E'\n' ||
      'Shariah Reviewer (UK) | approve a UAE submission via decide_review | refused | ' ||
      case when sqlerrm = 'ALLOWED' then 'ALLOWED (undone)' else 'refused: ' || sqlerrm end, true);
  end;
end $$;

do $$
begin
  begin
    update public.submissions set status = 'approved' where id = current_setting('test.uae_id')::uuid;
    raise exception 'ALLOWED';
  exception when others then
    perform set_config('test.log', current_setting('test.log') || E'\n' ||
      'Shariah Reviewer (UK) | set status = approved with a direct UPDATE | refused | ' ||
      case when sqlerrm = 'ALLOWED' then 'ALLOWED (undone)' else 'refused: ' || sqlerrm end, true);
  end;
end $$;

-- ---------------------------------------------------------------- Compliance Reviewer (UAE)
reset role;
select set_config('request.jwt.claims',
       json_build_object('sub', current_setting('test.compliance_uae'), 'role', 'authenticated')::text, true);
set local role authenticated;

select set_config('test.log', current_setting('test.log') || E'\n' ||
  'Compliance Reviewer (UAE) | list all submissions | UAE only | ' || count(*) || ' rows, regions: ' ||
  coalesce(string_agg(distinct region, ','), '-'), true)
from public.submissions;

-- ---------------------------------------------------------------- Submitter B
reset role;
select set_config('request.jwt.claims',
       json_build_object('sub', current_setting('test.sub_b'), 'role', 'authenticated')::text, true);
set local role authenticated;

select set_config('test.log', current_setting('test.log') || E'\n' ||
  'Submitter B | read a Submitter A submission | 0 rows | ' ||
  (select count(*) from public.submissions where id = current_setting('test.a_sub')::uuid) || ' rows', true);

do $$
begin
  begin
    insert into public.submissions (title, region, channel, content, status)
    values ('access test', 'UK', 'Email', 'access test', 'approved');
    raise exception 'ALLOWED';
  exception when others then
    perform set_config('test.log', current_setting('test.log') || E'\n' ||
      'Submitter B | insert a submission already marked approved | refused | ' ||
      case when sqlerrm = 'ALLOWED' then 'ALLOWED (undone)' else 'refused: ' || sqlerrm end, true);
  end;
end $$;

do $$
begin
  begin
    insert into public.audit_log (submission_id, action) values (current_setting('test.a_sub')::uuid, 'fake');
    raise exception 'ALLOWED';
  exception when others then
    perform set_config('test.log', current_setting('test.log') || E'\n' ||
      'Submitter B | write a fake audit log row | refused | ' ||
      case when sqlerrm = 'ALLOWED' then 'ALLOWED (undone)' else 'refused: ' || sqlerrm end, true);
  end;
end $$;

do $$
begin
  begin
    perform public.decide_review(current_setting('test.a_sub')::uuid, 'approved', 'access test');
    raise exception 'ALLOWED';
  exception when others then
    perform set_config('test.log', current_setting('test.log') || E'\n' ||
      'Submitter B | approve a submission via decide_review | refused | ' ||
      case when sqlerrm = 'ALLOWED' then 'ALLOWED (undone)' else 'refused: ' || sqlerrm end, true);
  end;
end $$;

-- ---------------------------------------------------------------- results
reset role;
select split_part(l, ' | ', 1) as acting_as,
       split_part(l, ' | ', 2) as attempt,
       split_part(l, ' | ', 3) as expected,
       split_part(l, ' | ', 4) as result
from regexp_split_to_table(substr(current_setting('test.log'), 2), E'\n') as l;
