"""Proves access is enforced by the DATABASE, not the UI (for the video).

It talks to Supabase directly with each user's own login, bypassing the app.
Usage:
    pip install supabase
    python access_test.py
It asks for the Supabase URL, anon key, a UAE submission id, a Submitter A
submission id, and the test passwords (nothing is stored).
"""
from getpass import getpass

from supabase import create_client


def login(url, anon, email, pw):
    c = create_client(url, anon)
    s = c.auth.sign_in_with_password({"email": email, "password": pw})
    c.postgrest.auth(s.session.access_token)
    return c


def attempt(label, fn):
    try:
        res = fn()
        data = getattr(res, "data", res)
        print(f"[{'BLOCKED' if not data else 'VISIBLE'}] {label}: {data!r}")
    except Exception as e:
        print(f"[REFUSED] {label}: {getattr(e, 'message', None) or e}")


url = input("Supabase URL: ").strip()
anon = input("Supabase anon key: ").strip()
uae_id = input("Id of a UAE submission: ").strip()
a_id = input("Id of a Submitter A submission: ").strip()

shariah_uk = login(url, anon, "shariah.uk@example.com", getpass("Password for shariah.uk: "))
sub_b = login(url, anon, "submitter.b@example.com", getpass("Password for submitter.b: "))

print("\n--- Shariah Reviewer (UK) vs a UAE submission ---")
attempt("read UAE submission", lambda: shariah_uk.table("submissions").select("*").eq("id", uae_id).execute())
attempt("read its findings", lambda: shariah_uk.table("findings").select("*").eq("submission_id", uae_id).execute())
attempt("read its audit log", lambda: shariah_uk.table("audit_log").select("*").eq("submission_id", uae_id).execute())
attempt("approve it via RPC", lambda: shariah_uk.rpc("decide_review", {
    "p_submission_id": uae_id, "p_decision": "approved", "p_comment": "should fail"}).execute())
attempt("set status directly", lambda: shariah_uk.table("submissions").update({"status": "approved"}).eq("id", uae_id).execute())

print("\n--- Submitter B vs Submitter A's submission ---")
attempt("read A's submission", lambda: sub_b.table("submissions").select("*").eq("id", a_id).execute())
attempt("approve own/any via RPC", lambda: sub_b.rpc("decide_review", {
    "p_submission_id": a_id, "p_decision": "approved"}).execute())
attempt("insert pre-approved row", lambda: sub_b.table("submissions").insert({
    "title": "x", "region": "UK", "channel": "Email", "content": "x", "status": "approved"}).execute())
attempt("write a fake audit row", lambda: sub_b.table("audit_log").insert({
    "submission_id": a_id, "action": "fake"}).execute())
