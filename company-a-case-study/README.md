# Content Compliance Review Tool

Marketing submits content. An AI screens it against a rulebook. Regional Shariah and Compliance reviewers then approve or reject it. Every status change is written to an audit log.

## Architecture

```
Streamlit app (anon key + the signed-in user's JWT)
    -> Supabase Postgres (RLS + SECURITY DEFINER functions = all access rules + status logic)
        -> Database Webhook on submissions insert/update
            -> n8n workflow: Config -> Get rules -> Build prompt -> OpenAI -> Validate -> apply_screening_result
                                                     any error ----------------------> mark_screening_failed
```

## What the AI decides, and what code decides

| Decision | Made by | Why |
|---|---|---|
| Whether the content breaks a judgement rule (S1-S4, F1, F3-F5), the quote, the suggested fix | **AI** (OpenAI, temperature 0, JSON schema) | Needs language understanding |
| Whether a rule ID is valid for the region (P3) | Code (n8n + SQL) | The AI can invent IDs |
| Severity and team of each finding | Code (`rules` table) | Never trust the AI on what drives the status |
| F2 risk warning present, S5 certification phrase present | Code (exact text, visible content only) | Literal checks are 100% reliable in code |
| Status (P1), which teams must approve (P4) | SQL `apply_screening_result` | Deterministic and auditable. The AI cannot be talked into approval (see SAMPLE-05) |
| Approve or reject | Humans, through `decide_review` | Required by the brief |

The rules are **data**, stored in the `rules` table. A rule can be changed or added without any code change.

## Where access is enforced

All of it is in Postgres. See `sql/01_schema.sql`.

- **RLS SELECT.** Submitters see only their own rows. Reviewers see only their own region. Findings, reviews and the audit log inherit visibility through `can_see_submission()`.
- **RLS INSERT.** Only submitters can insert, only for themselves, and only as a fresh version 1 with status `screening`.
- **No UPDATE or DELETE** policy or privilege exists for app users. State changes only through these functions:
  - `decide_review`: reviewers. The team comes from the caller's role and the region from their profile.
  - `retry_screening`: Compliance, own region.
  - `resubmit`: the original submitter only. It always creates a new version, so a rejected version stays rejected.
  - `apply_screening_result` and `mark_screening_failed`: `service_role` only, which means n8n.
- **Audit.** A trigger writes every status change. Reviewer decisions are logged too. There is no client write access to `audit_log`.
- **Proof.** `scripts/access_test.py` calls the database directly, bypassing the app, and shows each attempt refused or empty.

## Failure handling

The workflow sets `screening_failed`, with the reason, whenever any of these happen:

- the AI call fails, times out (30s) or is rate-limited;
- the AI returns invalid JSON;
- the AI returns an unknown rule ID;
- the database write fails.

Rows stuck in `screening` for more than 2 minutes, for example because the webhook never fired, are treated the same way. The **regional Compliance reviewer** sees these in their queue and presses **Retry screening**. They are never approved.

## Assumptions

1. ESCALATE + FIX gives `in_review` (P1). The FIX findings are still shown to everyone (P2).
2. A rejection by either team rejects the submission.
3. A submitter can edit and resubmit after `changes_needed`, `auto_rejected`, `rejected` or `screening_failed`. This always creates a new version.
4. A quote the AI returns that is not found word for word is kept and marked, not dropped. A missed issue is worse than an extra one for a human to dismiss.
5. Text inside HTML comments does not count as a visible risk warning.

## Setup (own accounts only)

1. **Supabase.** Create a new project.
   1. SQL editor: run `sql/01_schema.sql`.
   2. Authentication > Users: add the 6 test users listed in `sql/02_seed.sql`, with Auto Confirm on.
   3. Run `sql/02_seed.sql`.
2. **n8n.**
   1. Run `python n8n/build_workflow.py` and import `n8n/screen-submission.workflow.json`.
   2. Create these credentials:
      - **Supabase API**: host plus the service_role key.
      - **OpenAI API**: Company A's replacement key.
      - **Header Auth** for the webhook: name `x-webhook-secret`, with a random value.
   3. Select those credentials in the nodes.
   4. In the **Config** node, set `supabase_url`.
   5. Activate the workflow.
3. **OpenAI model check.**
   1. Add a temporary HTTP node: GET `https://api.openai.com/v1/models` with the OpenAI credential.
   2. Pick an available model (gpt-4o-mini, then gpt-4.1-mini, then gpt-4o) and set `model` in Config.
   3. Test one sample. If `json_schema` is rejected, set `use_strict_schema: false`.
   4. Delete the temporary node.
4. **Supabase Database Webhook.**
   1. Database > Webhooks > Create.
   2. Table `submissions`, events Insert and Update.
   3. Type HTTP POST to the n8n **production** webhook URL.
   4. Add the header `x-webhook-secret` with the same value as in step 2.
5. **Streamlit.**
   1. Push `app/` to a new GitHub repo.
   2. Deploy it on Streamlit Community Cloud with main file `app/app.py`.
   3. Secrets: `SUPABASE_URL` + `SUPABASE_ANON_KEY` **only**.
6. **Before sending:** search every file and the n8n export for `sk-`, `service_role` and `eyJ`. Nothing should be found.

## Results for the six samples

Fill this in from the app.

| Sample | Expected | Actual |
|---|---|---|
| 01 UK | S5 FIX, F2 FIX -> Changes needed | |
| 02 UK | F1 ABSOLUTE, S4 ESCALATE -> Auto-rejected | |
| 03 UAE | S3 ESCALATE -> In review (Shariah) | |
| 04 UK | F3 + F5 ESCALATE, F4 FIX -> In review (Compliance) | |
| 05 UAE | S1 ABSOLUTE, injection ignored and noted -> Auto-rejected | |
| 06 UK | none -> In review (Compliance sign-off) | |

## Mocked or incomplete; next steps

- Notifications (bonus) are not built. Next step: Slack or email from n8n on status-change webhook events.
- A Streamlit session lasts about 1 hour; there is no token refresh.
- Next steps with more time:
  - a rule admin screen;
  - a regression suite that runs the 6 samples on every prompt change;
  - showing the diff between versions;
  - a per-finding override with a reason.
