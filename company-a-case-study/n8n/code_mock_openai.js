// n8n Code node "Mock OpenAI" - use ONLY until the replacement OpenAI key arrives.
// Put it in place of the "OpenAI" node. It returns the same shape as the real
// chat-completions response, with no findings, so the rest of the flow
// (validation, deterministic F2/S5 checks, SQL status logic) runs end to end.
// Must be disclosed as mocked in the README and video if still in use at the end.

return [{
  json: {
    choices: [{
      message: { content: JSON.stringify({ findings: [], injection_detected: false }) },
    }],
  },
}];
