"""Assemble screen-submission.workflow.json from the code_*.js files.

Run:  python build_workflow.py
The output has NO credentials in it; after import, pick them in each node:
  - Webhook                -> Header Auth credential (shared secret with Supabase webhook)
  - Get rules / Apply / Mark failed -> "Supabase API" credential (service_role key)
  - OpenAI                 -> "OpenAI API" credential (Company A's replacement key)
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
code = lambda name: (HERE / name).read_text(encoding="utf-8")

SUPA = "={{ $('Config').first().json.supabase_url }}"


def http(name, pos, method, url, cred_type, body=None, query=None, error_output=True, always=False):
    p = {
        "method": method,
        "url": url,
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": cred_type,
        "options": {"timeout": 30000},
    }
    if query:
        p["sendQuery"] = True
        p["queryParameters"] = {"parameters": [{"name": k, "value": v} for k, v in query]}
    if body:
        p["sendBody"] = True
        p["specifyBody"] = "json"
        p["jsonBody"] = body
    node = {
        "parameters": p,
        "name": name,
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": pos,
    }
    if error_output:
        node["onError"] = "continueErrorOutput"
    if always:
        node["alwaysOutputData"] = True
    return node


def code_node(name, pos, js, error_output=False):
    node = {
        "parameters": {"jsCode": js},
        "name": name,
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": pos,
    }
    if error_output:
        node["onError"] = "continueErrorOutput"
    return node


nodes = [
    {
        "parameters": {
            "httpMethod": "POST",
            "path": "screen-submission",
            "authentication": "headerAuth",
            "options": {},
        },
        "name": "Webhook",
        "type": "n8n-nodes-base.webhook",
        "typeVersion": 2,
        "position": [0, 0],
        "webhookId": "screen-submission",
    },
    code_node("Config", [220, 0], code("code_config.js")),
    http(
        "Get rules", [440, 0], "GET", SUPA + "/rest/v1/rules", "supabaseApi",
        query=[
            ("select", "id,region,team,severity,rule_text,check_kind,trigger_text,required_text"),
            ("active", "eq.true"),
            ("region", "=in.(All,{{ $('Config').first().json.region }})"),
        ],
        always=True,
    ),
    code_node("Build prompt", [660, 0], code("code_build_prompt.js"), error_output=True),
    http(
        "OpenAI", [880, 0], "POST", "https://api.openai.com/v1/chat/completions", "openAiApi",
        body="={{ JSON.stringify($json.request) }}",
    ),
    code_node("Validate findings", [1100, 0], code("code_validate.js"), error_output=True),
    http(
        "Apply result (SQL decides status)", [1320, 0], "POST",
        SUPA + "/rest/v1/rpc/apply_screening_result", "supabaseApi",
        body="={{ JSON.stringify({ p_submission_id: $json.submission_id, p_findings: $json.findings, p_notes: $json.notes }) }}",
    ),
    http(
        "Mark screening failed", [1100, 300], "POST",
        SUPA + "/rest/v1/rpc/mark_screening_failed", "supabaseApi",
        body=(
            "={{ JSON.stringify({ p_submission_id: $('Config').first().json.submission_id, "
            "p_reason: ($json.error && ($json.error.message || $json.error.description)) "
            "|| $json.message || 'Screening failed (no detail returned)' }) }}"
        ),
        error_output=False,
    ),
]

FAIL = {"node": "Mark screening failed", "type": "main", "index": 0}


def to(name):
    return {"node": name, "type": "main", "index": 0}


connections = {
    "Webhook": {"main": [[to("Config")]]},
    "Config": {"main": [[to("Get rules")]]},
    "Get rules": {"main": [[to("Build prompt")], [FAIL]]},
    "Build prompt": {"main": [[to("OpenAI")], [FAIL]]},
    "OpenAI": {"main": [[to("Validate findings")], [FAIL]]},
    "Validate findings": {"main": [[to("Apply result (SQL decides status)")], [FAIL]]},
    "Apply result (SQL decides status)": {"main": [[], [FAIL]]},
}

workflow = {
    "name": "screen-submission",
    "nodes": nodes,
    "connections": connections,
    "settings": {"executionOrder": "v1"},
    "pinData": {},
}

out = HERE / "screen-submission.workflow.json"
out.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
print("wrote", out)
