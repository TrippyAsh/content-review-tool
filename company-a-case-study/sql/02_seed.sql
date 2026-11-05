-- =====================================================================
-- Seed: the rulebook (Appendix A) + test user profiles.
-- Step 1: create these users in Supabase -> Authentication -> Users ->
--         "Add user" (tick "Auto Confirm User"), test passwords only:
--   submitter.a@example.com, submitter.b@example.com,
--   shariah.uk@example.com, shariah.uae@example.com,
--   compliance.uk@example.com, compliance.uae@example.com
-- Step 2: run this file.
-- =====================================================================

insert into public.rules (id, region, team, severity, rule_text, check_kind, trigger_text, required_text) values
('S1','All','Shariah','ABSOLUTE',
 'Promotes or offers interest (riba), e.g. "interest", "APR", "AER", or a fixed interest rate on savings.',
 'ai', null, null),
('S2','All','Shariah','ABSOLUTE',
 'Promotes a prohibited sector: alcohol, gambling, conventional banks, pork, or adult entertainment.',
 'ai', null, null),
('S3','All','Shariah','ESCALATE',
 'Mentions crypto or digital assets in any way.',
 'ai', null, null),
('S4','All','Shariah','ESCALATE',
 'Makes an absolute religious claim: "100% halal", "guaranteed halal" or "halal certified".',
 'ai', null, null),
('S5','All','Shariah','FIX',
 'Describes a product as "Shariah-compliant" without the phrase "as certified by our Shariah Supervisory Board".',
 'if_contains_must_contain', 'Shariah-compliant', 'as certified by our Shariah Supervisory Board'),
('F1','All','Compliance','ABSOLUTE',
 'Guarantees returns, or says investing is "risk-free" or has "no risk".',
 'ai', null, null),
('F2','UK','Compliance','FIX',
 'Missing the required UK risk warning: "Capital at risk. The value of investments can go down as well as up."',
 'must_contain', null, 'Capital at risk. The value of investments can go down as well as up.'),
('F2','UAE','Compliance','FIX',
 'Missing the required UAE risk warning: "Investing involves risk."',
 'must_contain', null, 'Investing involves risk.'),
('F3','All','Compliance','ESCALATE',
 'Shows a past performance figure without BOTH a time period AND the sentence "Past performance is not a reliable indicator of future results."',
 'ai', null, null),
('F4','All','Compliance','FIX',
 'Uses a superlative ("best", "#1", "leading") without naming a source.',
 'ai', null, null),
('F5','All','Compliance','ESCALATE',
 'Uses pressure or urgency tactics, e.g. "only 48 hours left", "don''t miss out", countdowns.',
 'ai', null, null);

insert into public.profiles (id, display_name, role, region)
select u.id, v.display_name, v.role, v.region
from (values
  ('submitter.a@example.com',   'Submitter A',               'submitter',  null),
  ('submitter.b@example.com',   'Submitter B',               'submitter',  null),
  ('shariah.uk@example.com',    'Shariah Reviewer (UK)',     'shariah',    'UK'),
  ('shariah.uae@example.com',   'Shariah Reviewer (UAE)',    'shariah',    'UAE'),
  ('compliance.uk@example.com', 'Compliance Reviewer (UK)',  'compliance', 'UK'),
  ('compliance.uae@example.com','Compliance Reviewer (UAE)', 'compliance', 'UAE')
) as v(email, display_name, role, region)
join auth.users u on u.email = v.email;

-- Check: should return 6 rows.
select display_name, role, region from public.profiles order by role, region;
