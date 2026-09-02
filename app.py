import sqlite3
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

from collector import collect_once
from config import DB_PATH
from database import Database
from ai_advisor import analyze_ipos, chat_with_advisor, get_gemini_api_key


st.set_page_config(
    page_title="IPO Intelligence",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- UI styling ----------

st.markdown("""
<style>
html, body, [class*="css"] {
    font-family: Helvetica Neue, Helvetica, Arial, sans-serif;
}

.stApp {
    background: #0b0d10;
}

.block-container {
    max-width: 1440px;
    padding-top: 1.2rem;
    padding-bottom: 3rem;
}

button, input, textarea, select {
    font-family: Helvetica Neue, Helvetica, Arial, sans-serif !important;
}

.ipo-card-title {
    font-size: 1.08rem;
    font-weight: 700;
    line-height: 1.2;
}

.ipo-card-meta {
    font-size: 0.82rem;
    opacity: 0.62;
}

.ai-score-strip {
    border-top: 1px solid rgba(255,255,255,0.10);
    margin-top: 0.7rem;
    padding-top: 0.7rem;
}

.calc-hero {
    padding: 1rem 1.1rem;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
    background: rgba(255,255,255,0.025);
}

.small-note {
    font-size: 0.82rem;
    opacity: 0.65;
}
</style>
""", unsafe_allow_html=True)


# ---------- Formatting ----------

def is_missing(value):
    return value is None or pd.isna(value)


def money(value, decimals=0):
    if is_missing(value):
        return "—"
    value = float(value)
    return f"₹{value:,.{decimals}f}" if decimals else f"₹{value:,.0f}"


def price_band(row):
    low = row.get("price_low")
    high = row.get("price_high")
    if is_missing(low):
        return "—"
    if is_missing(high):
        return money(low)
    return f"{money(low)}–{money(high)}"


def multiple(value):
    if is_missing(value):
        return "—"
    return f"{float(value):,.2f}x"


def applications(value):
    if is_missing(value):
        return "—"
    return f"{int(float(value)):,}"


def clean_text(value):
    if is_missing(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "n/a", "na"}:
        return None
    return text


def format_date(value):
    value = clean_text(value)
    if not value:
        return "—"
    for fmt in ["%b %d, %Y", "%d %b %Y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(value, fmt).strftime("%d %b %Y")
        except ValueError:
            pass
    return value


# ---------- Status ----------

LIVE_STATUSES = {"live", "open", "pre-apply"}
CLOSED_STATUSES = {"closed", "allotment out", "allotment awaited"}


def normalized_status(row):
    raw = clean_text(row.get("status"))
    if raw:
        value = raw.lower()
        if value in LIVE_STATUSES:
            return "Live"
        if value in CLOSED_STATUSES:
            if value == "allotment out":
                return "Allotment Out"
            if value == "allotment awaited":
                return "Allotment Awaited"
            return "Closed"
        if value in {"tentative dates", "drhp approved"}:
            return "Upcoming"

    open_date = pd.to_datetime(row.get("open_date"), errors="coerce")
    close_date = pd.to_datetime(row.get("close_date"), errors="coerce")
    today = pd.Timestamp.now().normalize()

    if pd.notna(open_date) and pd.notna(close_date):
        if open_date.normalize() <= today <= close_date.normalize():
            return "Live"
        if today < open_date.normalize():
            return "Upcoming"
        if today > close_date.normalize():
            return "Closed"
    return "Upcoming"


# ---------- Data ----------

def get_df():
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query("SELECT * FROM ipos", conn)


def get_selected_ipo(source_id):
    db = Database()
    try:
        row = db.get_ipo(source_id)
        sub_history = db.get_subscription_history(source_id)
        gmp_history = db.get_gmp_history(source_id)
        return dict(row) if row else None, [dict(x) for x in sub_history], [dict(x) for x in gmp_history]
    finally:
        db.close()


def build_ai_dataset(frame):
    cols = [
        "source_id", "company_name", "segment", "display_status",
        "open_date", "close_date", "price_low", "price_high", "lot_size",
        "issue_size", "fresh_issue", "ofs_issue", "gmp", "gmp_pct",
        "indicative_listing", "subscription", "qib", "nii", "snii",
        "bnii", "retail", "applications", "listing_exchange",
    ]
    available = [c for c in cols if c in frame.columns]
    data = frame[available].copy()
    return data.where(pd.notna(data), None).to_dict(orient="records")


# ---------- AI ----------

def ai_score_for_ipo(row):
    if not get_gemini_api_key():
        st.error("AI is not configured yet. Add GEMINI_API_KEY to Streamlit secrets.")
        return
    try:
        with st.spinner("AI is reviewing this IPO..."):
            result = analyze_ipos(
                build_ai_dataset(pd.DataFrame([row])),
                objective="Balanced",
                risk_tolerance="Moderate",
                horizon="Listing day",
            )
        recommendations = result.get("recommendations", [])
        if recommendations:
            st.session_state["ai_scores"][str(row["source_id"])] = recommendations[0]
    except Exception as exc:
        st.error(f"AI error: {exc}")


def render_ai_score(score):
    if not score:
        return
    verdict = score.get("verdict", "Insufficient data")
    investment = score.get("investment_score")
    allotment = score.get("allotment_score")
    confidence = score.get("confidence", "Low")
    st.markdown(f"**AI view: {verdict}** · {confidence} confidence")
    s1, s2 = st.columns(2)
    with s1:
        st.metric("Investment", f"{investment}/100" if investment is not None else "—")
    with s2:
        st.metric("Allotment", f"{allotment}/100" if allotment is not None else "—")
    reason = clean_text(score.get("reason"))
    if reason:
        st.caption(reason)


# ---------- Page: Discovery ----------

def discovery_page(df):
    st.title("IPO Discovery")
    st.caption("Find open and upcoming IPOs, then use Ask AI for a quick decision view.")

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    with c1:
        segment = st.selectbox("Segment", ["All", "Mainboard", "SME"])
    with c2:
        status_filter = st.selectbox("Status", ["All", "Live", "Upcoming", "Closed"])
    with c3:
        sort = st.selectbox("Sort", ["Close date", "Subscription", "GMP %"])
    with c4:
        search = st.text_input("Search IPO", placeholder="Company name...")

    view = df.copy()
    if segment != "All":
        view = view[view["segment"].fillna("").str.contains(segment, case=False, na=False)]
    if status_filter != "All":
        view = view[view["display_status"] == status_filter]
    if search:
        view = view[view["company_name"].fillna("").str.contains(search, case=False, na=False)]

    if sort == "Subscription":
        view = view.sort_values("subscription", ascending=False, na_position="last")
    elif sort == "GMP %":
        view = view.sort_values("gmp_pct", ascending=False, na_position="last")
    else:
        view["sort_close"] = pd.to_datetime(view["close_date"], errors="coerce")
        view = view.sort_values(["sort_close", "company_name"], na_position="last")

    live_count = int((df["display_status"] == "Live").sum())
    upcoming_count = int((df["display_status"] == "Upcoming").sum())
    closed_count = int(df["display_status"].isin(["Closed", "Allotment Out", "Allotment Awaited"]).sum())

    a, b, c, d = st.columns(4)
    a.metric("IPOs tracked", len(df))
    b.metric("Open now", live_count)
    c.metric("Upcoming", upcoming_count)
    d.metric("Closed / allotment", closed_count)

    def render_section(title, section_df):
        if section_df.empty:
            return
        st.markdown(f"### {title}")
        for _, row in section_df.iterrows():
            source_id = str(row["source_id"])
            company = clean_text(row.get("company_name")) or "Unknown IPO"
            status = normalized_status(row)
            segment_name = clean_text(row.get("segment")) or "IPO"
            score = st.session_state["ai_scores"].get(source_id)

            with st.container(border=True):
                title_col, m1, m2, m3, m4, actions = st.columns([2.35, 1.25, 1.0, 1.05, 1.35, 1.45])
                with title_col:
                    st.markdown(f'<div class="ipo-card-title">{company}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="ipo-card-meta">{segment_name} · {status}</div>', unsafe_allow_html=True)
                    if status == "Live":
                        st.markdown(f'<div class="ipo-card-meta">Closes {format_date(row.get("close_date"))}</div>', unsafe_allow_html=True)
                    elif status == "Upcoming":
                        st.markdown(f'<div class="ipo-card-meta">Opens {format_date(row.get("open_date"))}</div>', unsafe_allow_html=True)
                with m1:
                    st.metric("Price", price_band(row))
                with m2:
                    st.metric("Lot", f'{int(row["lot_size"]):,}' if not is_missing(row.get("lot_size")) else "—")
                with m3:
                    st.metric("GMP", money(row.get("gmp")))
                with m4:
                    st.metric("Subscription", multiple(row.get("subscription")))
                with actions:
                    if st.button("Ask AI", key=f"ask_ai_{source_id}", use_container_width=True):
                        ai_score_for_ipo(row)
                        st.rerun()
                    if st.button("View IPO", key=f"view_{source_id}", use_container_width=True):
                        st.session_state["selected_ipo"] = source_id
                        st.session_state["app_view"] = "ipo_detail"
                        st.switch_page("ipo_detail")

                if score:
                    st.markdown('<div class="ai-score-strip">', unsafe_allow_html=True)
                    render_ai_score(score)
                    risks = score.get("key_risks", [])
                    if risks:
                        st.caption("Risks: " + " · ".join(risks[:3]))
                    st.markdown('</div>', unsafe_allow_html=True)

    render_section("Open now", view[view["display_status"] == "Live"])
    render_section("Upcoming", view[view["display_status"] == "Upcoming"])
    render_section("Closed / allotment", view[view["display_status"].isin(["Closed", "Allotment Out", "Allotment Awaited"])])

    if view.empty:
        st.info("No IPOs match your filters.")


# ---------- Page: AI Analyst ----------

def ai_analyst_page(df):
    st.title("AI Analyst")
    st.caption("Ask deeper questions about the IPOs currently tracked by IPO Intelligence.")

    if not get_gemini_api_key():
        st.warning("AI is not configured. Add GEMINI_API_KEY to Streamlit secrets to use the analyst.")
        return

    st.markdown("#### Start with a question")
    prompts = [
        "Which open IPO looks best for listing gains?",
        "Which IPO has the best risk/reward right now?",
        "Which IPOs should I avoid and why?",
        "Compare the top 3 open IPOs for me.",
    ]
    cols = st.columns(4)
    for i, prompt in enumerate(prompts):
        with cols[i]:
            if st.button(prompt, key=f"analyst_prompt_{i}", use_container_width=True):
                st.session_state["ai_pending_prompt"] = prompt

    pending = st.session_state.pop("ai_pending_prompt", None)
    if pending:
        st.session_state["ai_chat_messages"].append({"role": "user", "content": pending})
        try:
            answer = chat_with_advisor(
                pending,
                analysis={"recommendations": list(st.session_state["ai_scores"].values())},
                ipos=build_ai_dataset(df),
            )
            st.session_state["ai_chat_messages"].append({"role": "assistant", "content": answer})
        except Exception as exc:
            st.error(f"AI error: {exc}")

    for message in st.session_state["ai_chat_messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Ask about an IPO, listing gains, allotment, risks...")
    if question:
        st.session_state["ai_chat_messages"].append({"role": "user", "content": question})
        try:
            answer = chat_with_advisor(
                question,
                analysis={"recommendations": list(st.session_state["ai_scores"].values())},
                ipos=build_ai_dataset(df),
            )
            st.session_state["ai_chat_messages"].append({"role": "assistant", "content": answer})
            st.rerun()
        except Exception as exc:
            st.error(f"AI error: {exc}")


# ---------- Page: Allotment Chances ----------

def allotment_page(df):
    st.title("Allotment Chances")
    st.caption("Estimate the chance of getting at least one retail allotment across multiple eligible accounts.")

    live = df[df["display_status"] == "Live"].copy()
    candidates = live if not live.empty else df[df["display_status"] == "Upcoming"].copy()

    if candidates.empty:
        st.info("No IPOs are available for the calculator yet.")
        return

    candidates["label"] = candidates.apply(
        lambda r: f"{clean_text(r.get('company_name')) or 'IPO'} · {normalized_status(r)}",
        axis=1,
    )

    selected_label = st.selectbox("Select IPO", candidates["label"].tolist())
    row = candidates[candidates["label"] == selected_label].iloc[0].to_dict()

    lot_size = None if is_missing(row.get("lot_size")) else int(float(row["lot_size"]))
    price = None if is_missing(row.get("price_high")) else float(row["price_high"])
    retail_sub = None if is_missing(row.get("retail")) else float(row["retail"])

    if lot_size and price:
        retail_application = lot_size * price
    else:
        retail_application = None

    st.markdown("### Your application setup")
    c1, c2, c3 = st.columns(3)
    with c1:
        capital = st.number_input("Capital available", min_value=0.0, value=200000.0, step=1000.0, format="%.0f")
    with c2:
        accounts = st.number_input("Eligible PAN / demat accounts", min_value=1, value=1, step=1)
    with c3:
        category = st.selectbox("Category", ["Retail", "sNII", "bNII"])

    if category != "Retail":
        st.markdown('<div class="calc-hero">', unsafe_allow_html=True)
        st.markdown("**HNI categories work differently.** The simple lottery-style probability used for Retail should not be applied to sNII/bNII. We show the subscription-based allocation ratio as an indicator instead.")
        st.markdown('</div>', unsafe_allow_html=True)

        sub_col = {"sNII": "snii", "bNII": "bnii"}[category]
        subscription = row.get(sub_col)
        if is_missing(subscription) or float(subscription) <= 0:
            st.warning(f"{category} subscription data is not available yet.")
            return

        allocation_ratio = min(1.0, 1.0 / float(subscription))
        m1, m2, m3 = st.columns(3)
        m1.metric(f"{category} subscription", multiple(subscription))
        m2.metric("Indicative allocation ratio", f"{allocation_ratio * 100:.1f}%")
        m3.metric("Capital entered", money(capital))
        st.caption("This is an indicative allocation ratio, not a guaranteed allotment probability. Actual HNI allotment follows the issue's final basis of allotment.")
        return

    if retail_application is None or retail_application <= 0:
        st.warning("Price band and lot size are required to calculate the Retail application amount.")
        return

    affordable_accounts = int(capital // retail_application)
    applications_used = min(int(accounts), affordable_accounts)

    st.markdown(f"**1 Retail application = {money(retail_application)}** at the upper price band.")

    if retail_sub is None or retail_sub <= 0:
        st.warning("Retail subscription data is not available yet, so the allotment chance cannot be estimated.")
        return

    # Simplified retail lottery estimator. For oversubscribed retail issues,
    # one-lot-per-applicant probability is approximated as 1 / subscription.
    if retail_sub <= 1:
        per_account = 1.0
    else:
        per_account = min(1.0, 1.0 / retail_sub)

    at_least_one = 1 - ((1 - per_account) ** applications_used) if applications_used else 0.0
    expected = applications_used * per_account

    st.markdown("### Estimated outcome")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Retail subscription", multiple(retail_sub))
    m2.metric("Applications possible", applications_used)
    m3.metric("Chance per account", f"{per_account * 100:.1f}%")
    m4.metric("At least one allotment", f"{at_least_one * 100:.1f}%")

    st.markdown("### What the calculator is doing")
    st.write(
        f"With {applications_used} independent eligible accounts, it estimates the chance of at least one allotment as "
        f"1 − (1 − p)ⁿ, where p is approximated from the Retail subscription level."
    )
    st.caption(
        f"Expected allotments under this simplified model: {expected:.2f}. "
        "This is an estimate, not a prediction of the exchange/registrar's final basis of allotment."
    )


# ---------- Page: IPO Detail ----------

def ipo_detail_page(df):
    selected_id = st.session_state.get("selected_ipo")
    if not selected_id:
        st.info("Select an IPO from Discovery to view its details.")
        return

    row, sub_history, gmp_history = get_selected_ipo(selected_id)
    if not row:
        st.error("The selected IPO could not be found.")
        if st.button("Back to Discovery"):
            st.session_state.pop("selected_ipo", None)
            st.switch_page("discovery")
        return

    if st.button("← Back to Discovery"):
        st.session_state.pop("selected_ipo", None)
        st.switch_page("discovery")

    st.markdown("---")
    st.title(clean_text(row.get("company_name")) or "IPO")
    status = normalized_status(row)
    st.caption(f"{status} · {clean_text(row.get('segment')) or 'IPO'}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Price band", price_band(row))
    m2.metric("Lot size", f'{int(row["lot_size"]):,}' if not is_missing(row.get("lot_size")) else "—")
    m3.metric("Issue size", f'₹{float(row["issue_size"]):,.2f} Cr' if not is_missing(row.get("issue_size")) else "—")
    m4.metric("GMP", money(row.get("gmp")))

    st.subheader("IPO timeline")
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Open", format_date(row.get("open_date")))
    t2.metric("Close", format_date(row.get("close_date")))
    t3.metric("Allotment", format_date(row.get("allotment_date")))
    t4.metric("Listing", format_date(row.get("listing_date")))

    st.markdown("---")
    st.subheader("Subscription")
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("QIB", multiple(row.get("qib")))
    s2.metric("NII", multiple(row.get("nii")))
    s3.metric("sNII", multiple(row.get("snii")))
    s4.metric("bNII", multiple(row.get("bnii")))
    s5.metric("Retail", multiple(row.get("retail")))

    s6, s7, s8 = st.columns(3)
    s6.metric("Total", multiple(row.get("subscription")))
    s7.metric("Applications", applications(row.get("applications")))
    s8.metric("GMP %", f'{float(row["gmp_pct"]):.1f}%' if not is_missing(row.get("gmp_pct")) else "—")

    if sub_history:
        sub_df = pd.DataFrame(sub_history)
        sub_df["captured_at"] = pd.to_datetime(sub_df["captured_at"], errors="coerce")
        sub_df = sub_df.dropna(subset=["captured_at"]).drop_duplicates(subset=["captured_at"]).sort_values("captured_at")
        chart_cols = [c for c in ["qib", "nii", "retail", "total"] if c in sub_df.columns and sub_df[c].notna().any()]
        st.subheader("Subscription history")
        if len(sub_df) >= 2 and chart_cols:
            st.line_chart(sub_df.set_index("captured_at")[chart_cols])
        else:
            st.caption("History will appear after the next few data refreshes.")
    else:
        st.subheader("Subscription history")
        st.caption("History will appear after the next few data refreshes.")

    if gmp_history:
        gmp_df = pd.DataFrame(gmp_history)
        gmp_df["captured_at"] = pd.to_datetime(gmp_df["captured_at"], errors="coerce")
        gmp_df = gmp_df.dropna(subset=["captured_at"]).drop_duplicates(subset=["captured_at"]).sort_values("captured_at")
        st.subheader("GMP history")
        if len(gmp_df) >= 2 and "gmp" in gmp_df.columns and gmp_df["gmp"].notna().any():
            st.line_chart(gmp_df.set_index("captured_at")[["gmp"]])
        else:
            st.caption("History will appear after the next few data refreshes.")
    else:
        st.subheader("GMP history")
        st.caption("History will appear after the next few data refreshes.")

    st.markdown("---")
    st.subheader("Issue details")
    d1, d2 = st.columns(2)
    with d1:
        st.write(f"**Fresh issue:** {money(row.get('fresh_issue'), 2)} Cr")
        st.write(f"**OFS:** {money(row.get('ofs_issue'), 2)} Cr")
        st.write(f"**Listing exchange:** {clean_text(row.get('listing_exchange')) or '—'}")
    with d2:
        st.write(f"**Registrar:** {clean_text(row.get('registrar')) or '—'}")
        st.write(f"**Lead managers:** {clean_text(row.get('lead_managers')) or '—'}")
        st.write(f"**Indicative listing:** {money(row.get('indicative_listing'))}")
    st.caption("GMP is unofficial grey-market information and is not a guarantee of listing price.")


# ---------- Initial state ----------

if "ai_scores" not in st.session_state:
    st.session_state["ai_scores"] = {}
if "ai_chat_messages" not in st.session_state:
    st.session_state["ai_chat_messages"] = []


# ---------- Data availability ----------

if not Path(DB_PATH).exists():
    with st.sidebar:
        st.markdown("# IPO Intelligence")
        st.caption("Research, demand, and AI decision support")
        st.markdown("---")
        if st.button("Refresh data", use_container_width=True):
            try:
                with st.spinner("Refreshing IPO data..."):
                    result = collect_once(enrich=True)
                st.success(f"Updated {result['count']} IPOs.")
                st.rerun()
            except Exception as exc:
                st.error(f"Collector error: {exc}")
        st.caption("Data is refreshed from the configured IPO sources.")
    st.title("IPO Intelligence")
    st.info("No IPO data yet. Use Refresh data in the sidebar.")
    st.stop()

try:
    df = get_df()
except Exception as exc:
    st.error(f"Database error: {exc}")
    st.stop()

if df.empty:
    with st.sidebar:
        st.markdown("# IPO Intelligence")
        st.caption("Research, demand, and AI decision support")
        st.markdown("---")
        if st.button("Refresh data", use_container_width=True):
            try:
                with st.spinner("Refreshing IPO data..."):
                    result = collect_once(enrich=True)
                st.success(f"Updated {result['count']} IPOs.")
                st.rerun()
            except Exception as exc:
                st.error(f"Collector error: {exc}")
        st.caption("Data is refreshed from the configured IPO sources.")
    st.title("IPO Intelligence")
    st.info("No IPOs collected yet. Use Refresh data in the sidebar.")
    st.stop()

df["display_status"] = df.apply(normalized_status, axis=1)


# ---------- Native Streamlit navigation ----------

pages = [
    st.Page(lambda: discovery_page(df), title="Discovery", icon="🔎", url_path="discovery", default=True),
    st.Page(lambda: allotment_page(df), title="Allotment Chances", icon="🎯", url_path="allotment-chances"),
    st.Page(lambda: ai_analyst_page(df), title="AI Analyst", icon="🤖", url_path="ai-analyst"),
    st.Page(lambda: ipo_detail_page(df), title="IPO Detail", icon="📄", url_path="ipo-detail"),
]

pg = st.navigation(pages, position="sidebar")

with st.sidebar:
    st.markdown("---")
    if st.button("Refresh data", use_container_width=True):
        try:
            with st.spinner("Refreshing IPO data..."):
                result = collect_once(enrich=True)
            st.success(f"Updated {result['count']} IPOs.")
            st.rerun()
        except Exception as exc:
            st.error(f"Collector error: {exc}")
    st.caption("Data is refreshed from the configured IPO sources.")

pg.run()
