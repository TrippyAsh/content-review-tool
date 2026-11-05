// n8n Code node "Config" (Run Once for All Items)
// Input: the Supabase Database Webhook payload {type, table, record, old_record}.
// Lets through only submissions that have JUST entered 'screening'
// (a new insert, or a retry). Everything else is dropped -> workflow ends.

const body = $input.first().json.body || {};
const rec = body.record || {};
const old = body.old_record || null;

const justEnteredScreening =
  rec.status === 'screening' && (!old || old.status !== 'screening');
if (!justEnteredScreening) return [];

return [{
  json: {
    // Not secrets. Keys live only in n8n credentials.
    supabase_url: 'https://ifpmvpvhpjabuuzaacqo.supabase.co',
    model: 'gpt-4o-mini',          // set AFTER verifying via GET /v1/models
    use_strict_schema: true,       // set false if json_schema is rejected -> json_object fallback
    submission_id: rec.id,
    region: rec.region,
    channel: rec.channel,
    title: rec.title,
    content: rec.content,
  },
}];
