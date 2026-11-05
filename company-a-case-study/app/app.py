"""Content Compliance Review - Streamlit front end.

Security model: this app holds ONLY the Supabase URL + anon key. Every query
runs with the signed-in user's JWT, so Postgres Row Level Security and the
SECURITY DEFINER functions decide what each user can see and do. Hiding a
button here is cosmetic; the database refuses the action anyway.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from supabase import Client, create_client

st.set_page_config(page_title="Content Review", page_icon="✅", layout="wide")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]

STATUS = {
    "screening": ("Screening", "🔄"),
    "auto_rejected": ("Auto-rejected", "⛔"),
    "changes_needed": ("Changes needed", "✏️"),
    "in_review": ("In review", "👀"),
    "approved": ("Approved", "✅"),
    "rejected": ("Rejected", "❌"),
    "screening_failed": ("Screening failed", "⚠️"),
}
RESUBMITTABLE = {"changes_needed", "auto_rejected", "rejected", "screening_failed"}
CHANNELS = ["Instagram caption", "Email", "Blog excerpt", "Landing page",
            "Web banner (HTML)", "Social post", "Other"]
STUCK_AFTER = timedelta(minutes=2)


# ---------------------------------------------------------------- helpers
def sb() -> Client:
    # One client PER browser session (never shared), so users never share a JWT.
    if "sb" not in st.session_state:
        st.session_state.sb = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return st.session_state.sb


def status_label(s: str) -> str:
    name, icon = STATUS.get(s, (s, ""))
    return f"{icon} {name}"


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt_ts(s: str | None) -> str:
    return parse_ts(s).strftime("%d %b %Y %H:%M:%S UTC") if s else ""


def is_stuck(sub: dict) -> bool:
    return sub["status"] == "screening" and \
        datetime.now(timezone.utc) - parse_ts(sub["updated_at"]) > STUCK_AFTER


def names() -> dict:
    if "names" not in st.session_state:
        rows = sb().table("profiles").select("id,display_name").execute().data
        st.session_state.names = {r["id"]: r["display_name"] for r in rows}
    return st.session_state.names


def run_db(fn, ok_msg: str):
    """Run a database call and show the database's own answer (incl. refusals)."""
    try:
        res = fn()
        st.success(ok_msg)
        return res
    except Exception as e:  # PostgREST errors carry the SQL exception message
        msg = getattr(e, "message", None) or str(e)
        st.error(f"Refused by the database: {msg}")
        return None


# ---------------------------------------------------------------- auth
def login_view():
    st.title("Content Compliance Review")
    st.caption("Sign in with your test account.")
    with st.form("login"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            try:
                res = sb().auth.sign_in_with_password({"email": email, "password": password})
                sb().postgrest.auth(res.session.access_token)
                prof = sb().table("profiles").select("*").eq("id", res.user.id).execute().data
                if not prof:
                    st.error("Signed in, but this user has no profile row (role/region).")
                    return
                st.session_state.profile = prof[0]
                st.rerun()
            except Exception as e:
                st.error(f"Sign-in failed: {e}")


def logout():
    try:
        sb().auth.sign_out()
    except Exception:
        pass
    st.session_state.clear()
    st.rerun()


# ---------------------------------------------------------------- detail view
def show_detail(sub: dict, profile: dict, children_of: set):
    st.markdown(f"**Region:** {sub['region']} &nbsp; **Channel:** {sub['channel']} &nbsp; "
                f"**Version:** {sub['version']} &nbsp; **Status:** {status_label(sub['status'])}")
    st.code(sub["content"], language="html" if "<" in sub["content"] else None, wrap_lines=True)

    if sub.get("screening_error"):
        st.warning(f"Screening error: {sub['screening_error']}")
    if sub.get("notes"):
        st.info(sub["notes"])
    if is_stuck(sub):
        st.warning("No screening result after 2 minutes. Treated as failed; a Compliance reviewer can retry.")

    findings = sb().table("findings").select("*").eq("submission_id", sub["id"]).execute().data
    st.markdown("**Findings**")
    if findings:
        order = {"ABSOLUTE": 0, "ESCALATE": 1, "FIX": 2}
        df = pd.DataFrame(findings).sort_values(by="severity", key=lambda c: c.map(order))
        st.dataframe(df[["rule_id", "severity", "team", "offending_text", "suggested_fix", "source"]],
                     hide_index=True, use_container_width=True)
    elif sub["status"] not in ("screening", "screening_failed"):
        st.write("No issues found.")
    else:
        st.write("Not screened yet.")

    reviews = sb().table("reviews").select("*").eq("submission_id", sub["id"]).execute().data
    if reviews:
        st.markdown("**Reviews required**")
        st.dataframe(pd.DataFrame([{
            "team": r["team"], "decision": r["decision"],
            "by": names().get(r["reviewer_id"], ""), "comment": r["comment"] or "",
            "when": fmt_ts(r["decided_at"]),
        } for r in reviews]), hide_index=True, use_container_width=True)

    # ---- actions (the database re-checks every one of these)
    role = profile["role"]
    my_team = {"shariah": "Shariah", "compliance": "Compliance"}.get(role)
    if my_team and sub["status"] == "in_review" and any(
            r["team"] == my_team and r["decision"] == "pending" for r in reviews):
        comment = st.text_input("Comment", key=f"c_{sub['id']}")
        c1, c2 = st.columns(2)
        if c1.button(f"Approve {my_team} part", key=f"a_{sub['id']}", type="primary"):
            if run_db(lambda: sb().rpc("decide_review", {"p_submission_id": sub["id"],
                   "p_decision": "approved", "p_comment": comment or None}).execute(), "Approved.") is not None:
                st.rerun()
        if c2.button(f"Reject {my_team} part", key=f"r_{sub['id']}"):
            if run_db(lambda: sb().rpc("decide_review", {"p_submission_id": sub["id"],
                   "p_decision": "rejected", "p_comment": comment or None}).execute(), "Rejected.") is not None:
                st.rerun()

    if role == "compliance" and (sub["status"] == "screening_failed" or is_stuck(sub)):
        if st.button("Retry screening", key=f"retry_{sub['id']}"):
            if run_db(lambda: sb().rpc("retry_screening", {"p_submission_id": sub["id"]}).execute(),
                   "Sent back for screening.") is not None:
                st.rerun()

    if role == "submitter" and sub["status"] in RESUBMITTABLE and sub["id"] not in children_of:
        with st.form(f"resub_{sub['id']}"):
            st.markdown("**Edit and resubmit (creates a new version)**")
            t = st.text_input("Title", sub["title"])
            ch = st.selectbox("Channel", CHANNELS,
                              index=CHANNELS.index(sub["channel"]) if sub["channel"] in CHANNELS else len(CHANNELS) - 1)
            ct = st.text_area("Content", sub["content"], height=160)
            if st.form_submit_button("Resubmit"):
                if run_db(lambda: sb().rpc("resubmit", {"p_parent_id": sub["id"], "p_title": t,
                       "p_channel": ch, "p_content": ct}).execute(), "Resubmitted for screening.") is not None:
                    st.rerun()

    audit = sb().table("audit_log").select("*").eq("submission_id", sub["id"]).order("created_at").execute().data
    st.markdown("**Audit log**")
    st.dataframe(pd.DataFrame([{
        "when": fmt_ts(a["created_at"]),
        "who": names().get(a["actor_id"], "System (screening)") if a["actor_id"] else "System (screening)",
        "action": a["action"],
        "from": a["from_status"] or "", "to": a["to_status"] or "",
        "details": a["details"] or {},
    } for a in audit]), hide_index=True, use_container_width=True)


def submission_list(subs: list, profile: dict, empty_msg: str):
    if not subs:
        st.write(empty_msg)
        return
    children_of = {s["parent_id"] for s in subs if s.get("parent_id")}
    for s in subs:
        stuck = " (stuck)" if is_stuck(s) else ""
        with st.expander(f"{status_label(s['status'])}{stuck} · {s['title']} · {s['region']} · v{s['version']}"):
            show_detail(s, profile, children_of)


# ---------------------------------------------------------------- pages
def submitter_page(profile: dict):
    tab_new, tab_mine = st.tabs(["New submission", "My submissions"])
    with tab_new:
        with st.form("new_sub", clear_on_submit=True):
            title = st.text_input("Title")
            c1, c2 = st.columns(2)
            region = c1.selectbox("Region", ["UK", "UAE"])
            channel = c2.selectbox("Channel", CHANNELS)
            content = st.text_area("Content", height=200)
            if st.form_submit_button("Submit for screening", type="primary"):
                if not (title.strip() and content.strip()):
                    st.error("Title and content are required.")
                else:
                    run_db(lambda: sb().table("submissions").insert({
                        "title": title.strip(), "region": region,
                        "channel": channel, "content": content}).execute(),
                        "Submitted. Screening has started; open 'My submissions' and refresh in a few seconds.")
    with tab_mine:
        st.button("Refresh")
        subs = sb().table("submissions").select("*").order("created_at", desc=True).execute().data
        submission_list(subs, profile, "You have no submissions yet.")


def reviewer_page(profile: dict):
    team = "Shariah" if profile["role"] == "shariah" else "Compliance"
    st.button("Refresh")
    subs = sb().table("submissions").select("*").order("created_at", desc=True).execute().data
    reviews = sb().table("reviews").select("*").execute().data
    pending_ids = {r["submission_id"] for r in reviews if r["team"] == team and r["decision"] == "pending"}

    queue = [s for s in subs if s["status"] == "in_review" and s["id"] in pending_ids]
    if profile["role"] == "compliance":
        queue += [s for s in subs if s["status"] == "screening_failed" or is_stuck(s)]

    tab_q, tab_all = st.tabs([f"My queue ({len(queue)})", f"All {profile['region']} submissions ({len(subs)})"])
    with tab_q:
        submission_list(queue, profile, "Nothing waiting for you.")
    with tab_all:
        submission_list(subs, profile, "No submissions in your region yet.")


# ---------------------------------------------------------------- main
def main():
    if "profile" not in st.session_state:
        login_view()
        return
    p = st.session_state.profile
    with st.sidebar:
        st.markdown(f"**{p['display_name']}**")
        st.caption(f"Role: {p['role']}" + (f" · Region: {p['region']}" if p.get("region") else ""))
        if st.button("Sign out"):
            logout()
    st.title("Content Compliance Review")
    if p["role"] == "submitter":
        submitter_page(p)
    else:
        reviewer_page(p)


main()
