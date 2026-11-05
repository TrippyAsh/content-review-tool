"""Content Compliance Review - Streamlit front end.

Security model: this app holds ONLY the Supabase URL + anon key. Every query
runs with the signed-in user's JWT, so Postgres Row Level Security and the
SECURITY DEFINER functions decide what each user can see and do. Hiding a
button here is cosmetic; the database refuses the action anyway.

App-side hardening (defence in depth, not the access control itself):
  - generic login errors (no account enumeration) + lockout after repeated failures
  - session refresh, and sign-out after 30 minutes idle
  - input length limits (also enforced by DB constraints)
  - all user content is HTML-escaped before rendering
"""
import html
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from supabase import Client, create_client

st.set_page_config(page_title="Content Compliance Review", page_icon="🛡️", layout="wide")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]

# label, icon, css class
STATUS = {
    "screening": ("Screening", "🔄", "st-screening"),
    "auto_rejected": ("Auto-rejected", "⛔", "st-rejected"),
    "changes_needed": ("Changes needed", "✏️", "st-changes"),
    "in_review": ("In review", "👀", "st-review"),
    "approved": ("Approved", "✅", "st-approved"),
    "rejected": ("Rejected", "❌", "st-rejected"),
    "screening_failed": ("Screening failed", "⚠️", "st-failed"),
}
SEV_ORDER = {"ABSOLUTE": 0, "ESCALATE": 1, "FIX": 2}
RESUBMITTABLE = {"changes_needed", "auto_rejected", "rejected", "screening_failed"}
CHANNELS = ["Instagram caption", "Email", "Blog excerpt", "Landing page",
            "Web banner (HTML)", "Social post", "Other"]
ROLE_LABEL = {"submitter": "Marketing submitter", "shariah": "Shariah reviewer",
              "compliance": "Compliance reviewer"}

STUCK_AFTER = timedelta(minutes=2)
MAX_TITLE, MAX_CONTENT = 200, 8000
LOCK_AFTER, LOCK_SECONDS = 5, 60
IDLE_LIMIT = 30 * 60

CSS = """
<style>
.block-container {max-width: 1150px; padding-top: 2.2rem;}
h1, h2, h3 {letter-spacing: -0.01em;}
.muted {opacity: .65; font-size: .85rem;}
.pill {display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .78rem;
       font-weight: 600; line-height: 1.6; margin-right: 6px; white-space: nowrap;}
.st-screening {background:#e0e7ff; color:#3730a3;}
.st-review    {background:#fef3c7; color:#92400e;}
.st-changes   {background:#dbeafe; color:#1e40af;}
.st-approved  {background:#dcfce7; color:#166534;}
.st-rejected  {background:#fee2e2; color:#991b1b;}
.st-failed    {background:#ffedd5; color:#9a3412;}
.sev-ABSOLUTE {background:#fee2e2; color:#991b1b;}
.sev-ESCALATE {background:#fef3c7; color:#92400e;}
.sev-FIX      {background:#dbeafe; color:#1e40af;}
.team {background: rgba(128,128,128,.15);}
.finding {border: 1px solid rgba(128,128,128,.25); border-radius: 10px;
          padding: 10px 14px; margin: 0 0 8px 0;}
.finding.ABSOLUTE {border-left: 5px solid #dc2626;}
.finding.ESCALATE {border-left: 5px solid #d97706;}
.finding.FIX      {border-left: 5px solid #2563eb;}
.finding .quote {margin: 6px 0 2px 0; font-style: italic;}
.finding .fix {font-size: .9rem; opacity: .85;}
.note {border-radius: 10px; padding: 10px 14px; margin: 6px 0 12px 0;
       background: rgba(37,99,235,.08); border: 1px solid rgba(37,99,235,.25);}
.note.warn {background: rgba(234,88,12,.08); border-color: rgba(234,88,12,.3);}
.hero {text-align:center; margin: 3.5rem 0 1.2rem 0;}
.hero .logo {font-size: 2.6rem;}
.hero h1 {font-size: 1.9rem; margin: .3rem 0 .2rem 0;}
.hero p {opacity: .7; margin: 0;}
.userbox {border: 1px solid rgba(128,128,128,.25); border-radius: 12px; padding: 12px 14px;}
.userbox b {font-size: 1rem;}
.rule {font-size: .82rem; margin-bottom: 6px;}
div[data-testid="stFormSubmitButton"] button {width: 100%;}
</style>
"""


# ---------------------------------------------------------------- helpers
def e(x) -> str:
    """Escape anything that came from users or the AI before it touches HTML."""
    return html.escape(str(x if x is not None else ""))


def sb() -> Client:
    # One client PER browser session (never shared), so users never share a JWT.
    if "sb" not in st.session_state:
        st.session_state.sb = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return st.session_state.sb


def pill(status: str) -> str:
    label, icon, css = STATUS.get(status, (status, "", "team"))
    return f'<span class="pill {css}">{icon} {e(label)}</span>'


def status_label(s: str) -> str:
    label, icon, _ = STATUS.get(s, (s, "", ""))
    return f"{icon} {label}"


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt_ts(s: str | None) -> str:
    return parse_ts(s).strftime("%d %b %Y, %H:%M UTC") if s else ""


def is_stuck(sub: dict) -> bool:
    return sub["status"] == "screening" and \
        datetime.now(timezone.utc) - parse_ts(sub["updated_at"]) > STUCK_AFTER


def names() -> dict:
    if "names" not in st.session_state:
        rows = sb().table("profiles").select("id,display_name").execute().data
        st.session_state.names = {r["id"]: r["display_name"] for r in rows}
    return st.session_state.names


def rules() -> list:
    if "rules" not in st.session_state:
        st.session_state.rules = sb().table("rules").select(
            "id,region,team,severity,rule_text").eq("active", True).order("id").execute().data
    return st.session_state.rules


def run_db(fn, ok_msg: str):
    """Run a database call and show the database's own answer (incl. refusals)."""
    try:
        res = fn()
        st.toast(ok_msg, icon="✅")
        return res
    except Exception as ex:  # PostgREST errors carry the SQL exception message
        msg = getattr(ex, "message", None) or str(ex)
        st.error(f"Refused by the database: {msg}")
        return None


# ---------------------------------------------------------------- session security
def end_session(message: str | None = None):
    try:
        sb().auth.sign_out()
    except Exception:
        pass
    keep = {k: st.session_state[k] for k in ("fails", "locked_until") if k in st.session_state}
    st.session_state.clear()
    st.session_state.update(keep)
    if message:
        st.session_state.flash = message


def ensure_session() -> bool:
    """Idle timeout + token refresh. Returns False if the user must sign in again."""
    now = time.time()
    if now - st.session_state.get("last_seen", now) > IDLE_LIMIT:
        end_session("You were signed out after 30 minutes of inactivity.")
        return False
    st.session_state.last_seen = now
    exp = st.session_state.get("expires_at")
    if exp and now > exp - 120:
        try:
            res = sb().auth.refresh_session()
            sb().postgrest.auth(res.session.access_token)
            st.session_state.expires_at = res.session.expires_at
        except Exception:
            end_session("Your session expired. Please sign in again.")
            return False
    return True


# ---------------------------------------------------------------- login
def login_view():
    _, mid, _ = st.columns([1, 1.25, 1])
    with mid:
        st.markdown('<div class="hero"><div class="logo">🛡️</div>'
                    '<h1>Content Compliance Review</h1>'
                    '<p>Shariah and financial-promotion screening for marketing content</p></div>',
                    unsafe_allow_html=True)
        if msg := st.session_state.pop("flash", None):
            st.info(msg)

        locked_until = st.session_state.get("locked_until", 0)
        if time.time() < locked_until:
            st.error(f"Too many failed sign-in attempts. Try again in {int(locked_until - time.time()) + 1} seconds.")
            if st.button("Try again"):
                st.rerun()
            return

        with st.form("login"):
            email = st.text_input("Email", placeholder="name@example.com", max_chars=254)
            password = st.text_input("Password", type="password", max_chars=128)
            submitted = st.form_submit_button("Sign in", type="primary")

        if submitted:
            email = email.strip().lower()
            if not email or not password:
                st.error("Enter your email and password.")
                return
            try:
                res = sb().auth.sign_in_with_password({"email": email, "password": password})
            except Exception:
                fails = st.session_state.get("fails", 0) + 1
                st.session_state.fails = fails
                if fails >= LOCK_AFTER:
                    st.session_state.locked_until = time.time() + LOCK_SECONDS
                    st.session_state.fails = 0
                # Same message whatever went wrong: never reveal whether an email exists.
                st.error("Email or password is incorrect.")
                return
            st.session_state.fails = 0
            sb().postgrest.auth(res.session.access_token)
            prof = sb().table("profiles").select("*").eq("id", res.user.id).execute().data
            if not prof:
                end_session()
                st.error("This account has no role assigned. Contact an administrator.")
                return
            st.session_state.profile = prof[0]
            st.session_state.expires_at = res.session.expires_at
            st.session_state.last_seen = time.time()
            st.rerun()

        st.markdown('<p class="muted" style="text-align:center">🔒 Access is enforced in the database '
                    '(row-level security). Test accounts only.</p>', unsafe_allow_html=True)


# ---------------------------------------------------------------- detail view
def render_findings(findings: list):
    for f in sorted(findings, key=lambda x: (SEV_ORDER.get(x["severity"], 9), x["rule_id"])):
        sev = f["severity"] if f["severity"] in SEV_ORDER else "FIX"
        source = "checked by code" if f["source"] == "deterministic" else "found by AI"
        st.markdown(
            f'<div class="finding {sev}">'
            f'<span class="pill sev-{sev}">{e(f["severity"])}</span>'
            f'<span class="pill team">{e(f["team"])}</span>'
            f'<b>{e(f["rule_id"])}</b> <span class="muted">· {source}</span>'
            f'<div class="quote">“{e(f["offending_text"])}”</div>'
            f'<div class="fix">💡 {e(f["suggested_fix"])}</div></div>',
            unsafe_allow_html=True)


def show_detail(sub: dict, profile: dict, children_of: set, scope: str = "main"):
    key = f"{scope}_{sub['id']}"
    st.markdown(f'{pill(sub["status"])} <span class="muted">{e(sub["region"])} · {e(sub["channel"])} · '
                f'version {sub["version"]} · submitted {fmt_ts(sub["created_at"])}</span>',
                unsafe_allow_html=True)
    st.code(sub["content"], language="html" if "<" in sub["content"] else None, wrap_lines=True)

    if sub.get("screening_error"):
        st.markdown(f'<div class="note warn">⚠️ Screening error: {e(sub["screening_error"])}</div>',
                    unsafe_allow_html=True)
    if sub.get("notes"):
        st.markdown(f'<div class="note">🛡️ {e(sub["notes"])}</div>', unsafe_allow_html=True)
    if is_stuck(sub):
        st.markdown('<div class="note warn">No screening result after 2 minutes. Treated as failed; '
                    'a Compliance reviewer can retry.</div>', unsafe_allow_html=True)

    findings = sb().table("findings").select("*").eq("submission_id", sub["id"]).execute().data
    reviews = sb().table("reviews").select("*").eq("submission_id", sub["id"]).execute().data

    col_f, col_r = st.columns([2, 1])
    with col_f:
        st.markdown("**Findings**")
        if findings:
            render_findings(findings)
        elif sub["status"] in ("screening", "screening_failed"):
            st.caption("Not screened yet.")
        else:
            st.success("No issues found.")
    with col_r:
        st.markdown("**Reviews**")
        if not reviews:
            st.caption("No human review needed." if sub["status"] in ("auto_rejected", "changes_needed")
                       else "None yet.")
        for r in reviews:
            css = {"approved": "st-approved", "rejected": "st-rejected"}.get(r["decision"], "st-review")
            who = names().get(r["reviewer_id"], "") if r["reviewer_id"] else ""
            st.markdown(f'<span class="pill team">{e(r["team"])}</span>'
                        f'<span class="pill {css}">{e(r["decision"])}</span>'
                        f'<div class="muted">{e(who)} {fmt_ts(r["decided_at"])}</div>'
                        + (f'<div class="muted">“{e(r["comment"])}”</div>' if r["comment"] else ""),
                        unsafe_allow_html=True)

    # ---- actions (the database re-checks every one of these)
    role = profile["role"]
    my_team = {"shariah": "Shariah", "compliance": "Compliance"}.get(role)
    if my_team and sub["status"] == "in_review" and any(
            r["team"] == my_team and r["decision"] == "pending" for r in reviews):
        st.markdown(f"**Your decision ({my_team})**")
        comment = st.text_input("Comment (recommended when rejecting)", key=f"c_{key}", max_chars=500)
        c1, c2, _ = st.columns([1, 1, 2])
        if c1.button(f"✅ Approve {my_team} part", key=f"a_{key}", type="primary"):
            if run_db(lambda: sb().rpc("decide_review", {"p_submission_id": sub["id"],
                      "p_decision": "approved", "p_comment": comment or None}).execute(), "Approved"):
                st.rerun()
        if c2.button(f"❌ Reject {my_team} part", key=f"r_{key}"):
            if run_db(lambda: sb().rpc("decide_review", {"p_submission_id": sub["id"],
                      "p_decision": "rejected", "p_comment": comment or None}).execute(), "Rejected"):
                st.rerun()

    if role == "compliance" and (sub["status"] == "screening_failed" or is_stuck(sub)):
        if st.button("🔁 Retry screening", key=f"retry_{key}"):
            if run_db(lambda: sb().rpc("retry_screening", {"p_submission_id": sub["id"]}).execute(),
                      "Sent back for screening"):
                st.rerun()

    if role == "submitter" and sub["status"] in RESUBMITTABLE and sub["id"] not in children_of:
        with st.form(f"resub_{key}"):
            st.markdown("**Edit and resubmit** · creates a new version; this one stays on record")
            t = st.text_input("Title", sub["title"], max_chars=MAX_TITLE)
            ch = st.selectbox("Channel", CHANNELS,
                              index=CHANNELS.index(sub["channel"]) if sub["channel"] in CHANNELS else len(CHANNELS) - 1)
            ct = st.text_area("Content", sub["content"], height=160, max_chars=MAX_CONTENT)
            if st.form_submit_button("Resubmit for screening"):
                if run_db(lambda: sb().rpc("resubmit", {"p_parent_id": sub["id"], "p_title": t.strip(),
                          "p_channel": ch, "p_content": ct}).execute(), "Resubmitted for screening"):
                    st.rerun()

    audit = sb().table("audit_log").select("*").eq("submission_id", sub["id"]).order("created_at").execute().data
    with st.expander(f"Audit log ({len(audit)} entries)"):
        st.dataframe(pd.DataFrame([{
            "when": fmt_ts(a["created_at"]),
            "who": names().get(a["actor_id"], "Unknown") if a["actor_id"] else "System (screening)",
            "action": a["action"],
            "from": a["from_status"] or "",
            "to": a["to_status"] or "",
            "details": ", ".join(f"{k}: {v}" for k, v in (a["details"] or {}).items()),
        } for a in audit]), hide_index=True)


def submission_list(subs: list, profile: dict, empty_msg: str, scope: str = "main"):
    if not subs:
        st.info(empty_msg)
        return
    children_of = {s["parent_id"] for s in subs if s.get("parent_id")}
    for s in subs:
        stuck = " · stuck" if is_stuck(s) else ""
        with st.expander(f"{status_label(s['status'])}{stuck}  ·  {s['title']}  ·  {s['region']}  ·  v{s['version']}"):
            show_detail(s, profile, children_of, scope)


def metrics(subs: list, last: tuple | None = None):
    def count(*statuses):
        return sum(1 for s in subs if s["status"] in statuses)
    cols = st.columns(5)
    cols[0].metric("Total", len(subs))
    cols[1].metric("In review", count("in_review"))
    cols[2].metric("Changes needed", count("changes_needed", "screening_failed"))
    cols[3].metric("Approved", count("approved"))
    label, value = last or ("Rejected", count("rejected", "auto_rejected"))
    cols[4].metric(label, value)


# ---------------------------------------------------------------- pages
def submitter_page(profile: dict):
    subs = sb().table("submissions").select("*").order("created_at", desc=True).execute().data
    metrics(subs)
    tab_new, tab_mine = st.tabs(["✍️ New submission", f"📄 My submissions ({len(subs)})"])
    with tab_new:
        with st.form("new_sub", clear_on_submit=True):
            title = st.text_input("Title", max_chars=MAX_TITLE)
            c1, c2 = st.columns(2)
            region = c1.selectbox("Region", ["UK", "UAE"])
            channel = c2.selectbox("Channel", CHANNELS)
            content = st.text_area("Content", height=200, max_chars=MAX_CONTENT,
                                   placeholder="Paste the marketing copy exactly as it will be published.")
            if st.form_submit_button("Submit for screening", type="primary"):
                if not (title.strip() and content.strip()):
                    st.error("Title and content are required.")
                else:
                    run_db(lambda: sb().table("submissions").insert({
                        "title": title.strip(), "region": region,
                        "channel": channel, "content": content}).execute(),
                        "Submitted. Screening takes a few seconds; check My submissions.")
    with tab_mine:
        st.button("🔄 Refresh", key="refresh_mine")
        submission_list(subs, profile, "You have no submissions yet.")


def reviewer_page(profile: dict):
    team = "Shariah" if profile["role"] == "shariah" else "Compliance"
    subs = sb().table("submissions").select("*").order("created_at", desc=True).execute().data
    reviews = sb().table("reviews").select("*").execute().data
    pending_ids = {r["submission_id"] for r in reviews if r["team"] == team and r["decision"] == "pending"}

    queue = [s for s in subs if s["status"] == "in_review" and s["id"] in pending_ids]
    if profile["role"] == "compliance":
        queue += [s for s in subs if s["status"] == "screening_failed" or is_stuck(s)]

    metrics(subs, ("Waiting for you", len(queue)))
    st.button("🔄 Refresh", key="refresh_rev")
    tab_q, tab_all = st.tabs([f"📥 My queue ({len(queue)})",
                              f"🗂️ All {profile['region']} submissions ({len(subs)})"])
    with tab_q:
        submission_list(queue, profile, "Nothing waiting for you. 🎉", scope="queue")
    with tab_all:
        submission_list(subs, profile, "No submissions in your region yet.", scope="all")


def notifications_panel():
    """Bonus: in-app notifications. RLS returns only this user's rows."""
    try:
        notes = sb().table("notifications").select("*").order("created_at", desc=True).limit(15).execute().data
    except Exception:
        return  # table not created yet: the rest of the app keeps working
    unread = [n for n in notes if not n["is_read"]]
    seen = st.session_state.setdefault("toasted", set())
    for n in unread:
        if n["id"] not in seen:
            st.toast(n["message"], icon="🔔")
            seen.add(n["id"])
    with st.expander(f"🔔 Notifications ({len(unread)} new)", expanded=bool(unread)):
        if not notes:
            st.caption("No notifications yet.")
        for n in notes:
            dot = "🔵 " if not n["is_read"] else ""
            st.markdown(f'<div class="rule">{dot}{e(n["message"])}<br>'
                        f'<span class="muted">{fmt_ts(n["created_at"])}</span></div>',
                        unsafe_allow_html=True)
        if unread and st.button("Mark all as read", key="mark_read"):
            if run_db(lambda: sb().rpc("mark_notifications_read", {}).execute(), "Marked as read"):
                st.rerun()


def sidebar(p: dict):
    with st.sidebar:
        region = f' · {e(p["region"])}' if p.get("region") else ""
        st.markdown(f'<div class="userbox"><b>{e(p["display_name"])}</b><br>'
                    f'<span class="muted">{e(ROLE_LABEL.get(p["role"], p["role"]))}{region}</span></div>',
                    unsafe_allow_html=True)
        st.write("")
        notifications_panel()
        if st.button("Sign out"):
            end_session("You have been signed out.")
            st.rerun()
        st.divider()
        with st.expander("📘 Rulebook (live from the database)"):
            for r in rules():
                sev = r["severity"] if r["severity"] in SEV_ORDER else "FIX"
                st.markdown(f'<div class="rule"><span class="pill sev-{sev}">{e(r["severity"])}</span>'
                            f'<b>{e(r["id"])}</b> · {e(r["team"])}'
                            + (f' · {e(r["region"])}' if r["region"] != "All" else "")
                            + f'<br>{e(r["rule_text"])}</div>', unsafe_allow_html=True)
        st.caption("Severity sets the status: ABSOLUTE → auto-rejected, ESCALATE → expert review, "
                   "FIX → back to marketing.")


# ---------------------------------------------------------------- main
def main():
    st.markdown(CSS, unsafe_allow_html=True)
    if "profile" not in st.session_state or not ensure_session():
        login_view()
        return
    p = st.session_state.profile
    sidebar(p)
    st.title("Content Compliance Review")
    st.caption("Submissions are screened automatically against the rulebook; humans make the final call.")
    if p["role"] == "submitter":
        submitter_page(p)
    else:
        reviewer_page(p)


main()
