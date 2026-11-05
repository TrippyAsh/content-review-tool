// n8n Code node "Validate findings" (Run Once for All Items)
// Input: the OpenAI chat-completions response.
// The AI only DETECTS. This node:
//   1. refuses anything unreadable (throws -> error branch -> screening_failed)
//   2. keeps only rule ids that exist for this region
//   3. checks the quoted text really appears in the content
//   4. runs the literal rules (F2, S5) in code - code is authoritative there
// Severity/team are NOT taken from the AI; the database re-reads them from
// the rules table in apply_screening_result. Status is decided in SQL.

const cfg = $('Config').first().json;
const rules = $('Build prompt').first().json.rules;
const resp = $input.first().json;

const raw = resp && resp.choices && resp.choices[0] && resp.choices[0].message
  ? resp.choices[0].message.content : null;
if (!raw) throw new Error('AI returned an empty response');

let parsed;
try { parsed = JSON.parse(raw); }
catch (e) { throw new Error('AI response is not valid JSON'); }
if (!parsed || !Array.isArray(parsed.findings)) {
  throw new Error('AI response has no findings array');
}

const norm = s => String(s || '').toLowerCase().replace(/[‐-―]/g, '-').replace(/\s+/g, ' ').trim();
const content = String(cfg.content || '');
const nContent = norm(content);
// Required wording must be VISIBLE: text inside HTML comments does not count.
const nVisible = norm(content.replace(/<!--[\s\S]*?-->/g, ' '));

const ruleById = {};
for (const r of rules) ruleById[r.id] = r;
const notes = [];
let out = [];

// --- AI findings: keep only valid rule ids; flag unverifiable quotes -------
for (const f of parsed.findings) {
  const r = ruleById[f.rule_id];
  if (!r) { notes.push(`Ignored AI finding with unknown/out-of-region rule "${f.rule_id}".`); continue; }
  if (r.check_kind !== 'ai') continue;   // literal rules are decided by code below
  let quote = String(f.offending_text || '').trim();
  if (!quote || !nContent.includes(norm(quote))) {
    // Never drop a possible issue silently: keep it for a human, marked.
    quote = `[quote not found verbatim] ${quote}`;
  }
  out.push({ rule_id: r.id, offending_text: quote, suggested_fix: String(f.suggested_fix || ''), source: 'ai' });
}

// --- Literal rules, decided by code -----------------------------------------
for (const r of rules) {
  if (r.check_kind === 'must_contain') {
    if (!nVisible.includes(norm(r.required_text))) {
      out.push({
        rule_id: r.id,
        offending_text: '(required risk warning is missing)',
        suggested_fix: `Add: "${r.required_text}"`,
        source: 'deterministic',
      });
    }
  } else if (r.check_kind === 'if_contains_must_contain') {
    const aiFindings = parsed.findings.filter(f => f.rule_id === r.id);
    const hasRequired = nVisible.includes(norm(r.required_text));
    const triggered = nContent.includes(norm(r.trigger_text)) || aiFindings.length > 0;
    if (triggered && !hasRequired) {
      const aiQuote = aiFindings.map(f => String(f.offending_text || '')).find(q => q && nContent.includes(norm(q)));
      out.push({
        rule_id: r.id,
        offending_text: aiQuote || r.trigger_text,
        suggested_fix: `Add "${r.required_text}" next to "${r.trigger_text}".`,
        source: 'deterministic',
      });
    }
  }
}

// --- De-duplicate (same rule + same quote) ----------------------------------
const seen = new Set();
out = out.filter(f => {
  const k = f.rule_id + '|' + norm(f.offending_text);
  if (seen.has(k)) return false;
  seen.add(k);
  return true;
});

// --- Prompt-injection note (AI flag OR a simple pattern match) --------------
const injectionPattern = /(ignore (all )?(previous|prior) (rules|instructions)|system note|pre-?approved|return status)/i;
if (parsed.injection_detected === true || injectionPattern.test(content)) {
  notes.push('Content contains text that tries to instruct the screening AI. It was ignored and screened as normal content.');
}

return [{
  json: {
    submission_id: cfg.submission_id,
    findings: out,
    notes: notes.length ? notes.join(' ') : null,
  },
}];
