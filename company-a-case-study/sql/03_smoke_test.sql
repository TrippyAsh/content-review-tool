-- Smoke test: SAMPLE-06 (expected: no findings -> in_review, Compliance sign-off)
insert into public.submissions (submitter_id, title, region, channel, content)
select id, 'SMOKE TEST - SAMPLE-06', 'UK', 'Social post',
 'Invest in line with your values. The investment app offers Shariah-compliant portfolios, as certified by our Shariah Supervisory Board. Capital at risk. The value of investments can go down as well as up.'
from public.profiles where display_name = 'Submitter A'
returning id, status;
