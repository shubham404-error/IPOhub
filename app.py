
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
    initial_sidebar_state="collapsed",
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

button {
    font-family: Helvetica Neue, Helvetica, Arial, sans-serif !important;
}

.ipo-kicker {
    font-size: 0.78rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    opacity: 0.65;
    margin-bottom: 0.25rem;
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

div[data-testid="stMetric"] {
    padding: 0.15rem 0;
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

    if decimals == 0:
        return f"₹{value:,.0f}"

    return f"₹{value:,.{decimals}f}"


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

    if not text or text.lower() in {
        "nan",
        "none",
        "null",
        "n/a",
        "na",
    }:
        return None

    return text


def format_date(value):
    value = clean_text(value)

    if not value:
        return "—"

    # Existing database values are mostly strings such as
    # "Aug 31, 2026" or "Sep 3, 2026".
    for fmt in [
        "%b %d, %Y",
        "%d %b %Y",
        "%Y-%m-%d",
    ]:
        try:
            return datetime.strptime(value, fmt).strftime("%d %b %Y")
        except ValueError:
            pass

    return value


# ---------- Status ----------

LIVE_STATUSES = {
    "live",
    "open",
    "pre-apply",
}

CLOSED_STATUSES = {
    "closed",
    "allotment out",
    "allotment awaited",
}


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

    # Fall back to dates when IPO Ji gives no usable status.
    open_date = pd.to_datetime(
        row.get("open_date"),
        errors="coerce",
    )
    close_date = pd.to_datetime(
        row.get("close_date"),
        errors="coerce",
    )

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
        return pd.read_sql_query(
            "SELECT * FROM ipos",
            conn,
        )


def get_selected_ipo(source_id):
    db = Database()

    try:
        row = db.get_ipo(source_id)
        sub_history = db.get_subscription_history(source_id)
        gmp_history = db.get_gmp_history(source_id)

        return (
            dict(row) if row else None,
            [dict(x) for x in sub_history],
            [dict(x) for x in gmp_history],
        )
    finally:
        db.close()


# ---------- Header ----------

head_left, head_right = st.columns([6, 1])
with head_left:
    st.title("IPO Intelligence")
    st.caption("Discover IPOs, evaluate demand, and get an AI view on whether an IPO is worth considering.")
with head_right:
    if st.button("Refresh data", type="primary", use_container_width=True):
        with st.spinner("Refreshing IPO data..."):
            try:
                result = collect_once(enrich=True)

                st.success(
                    f"Updated {result['count']} IPOs."
                )

                st.rerun()

            except Exception as exc:
                st.error(
                    f"Collector error: {exc}"
                )


if not Path(DB_PATH).exists():
    st.info(
        "No IPO data yet. Click Refresh data."
    )
    st.stop()


try:
    df = get_df()

except Exception as exc:
    st.error(
        f"Database error: {exc}"
    )
    st.stop()


if df.empty:
    st.info(
        "No IPOs collected yet. Click Refresh data."
    )
    st.stop()


# Add clean application-level status.
df["display_status"] = df.apply(
    normalized_status,
    axis=1,
)



# ---------- AI state / helpers ----------

if "ai_scores" not in st.session_state:
    st.session_state["ai_scores"] = {}

if "ai_chat_messages" not in st.session_state:
    st.session_state["ai_chat_messages"] = []

if "app_view" not in st.session_state:
    st.session_state["app_view"] = "discovery"


def build_ai_dataset(frame):
    cols = [
        "source_id", "company_name", "segment", "display_status",
        "open_date", "close_date", "price_low", "price_high",
        "lot_size", "issue_size", "fresh_issue", "ofs_issue",
        "gmp", "gmp_pct", "indicative_listing", "subscription",
        "qib", "nii", "snii", "bnii", "retail", "applications",
        "listing_exchange",
    ]
    available = [c for c in cols if c in frame.columns]
    data = frame[available].copy()
    return data.where(pd.notna(data), None).to_dict(orient="records")


def ai_score_for_ipo(row):
    """Ask the AI for one IPO only, then store the result in session state."""
    if not get_gemini_api_key():
        st.error("AI is not configured yet. Add GEMINI_API_KEY to Streamlit secrets.")
        return

    dataset = build_ai_dataset(pd.DataFrame([row]))
    try:
        with st.spinner("AI is reviewing this IPO..."):
            result = analyze_ipos(
                dataset,
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

    st.markdown(
        f"**AI view: {verdict}** · {confidence} confidence"
    )

    s1, s2 = st.columns(2)
    with s1:
        st.metric("Investment", f"{investment}/100" if investment is not None else "—")
    with s2:
        st.metric("Allotment", f"{allotment}/100" if allotment is not None else "—")

    reason = clean_text(score.get("reason"))
    if reason:
        st.caption(reason)


def open_discovery():
    st.session_state["app_view"] = "discovery"
    st.session_state.pop("selected_ipo", None)
    st.rerun()


def open_ai_analyst():
    st.session_state["app_view"] = "ai_analyst"
    st.session_state.pop("selected_ipo", None)
    st.rerun()


# ---------- Navigation ----------

nav1, nav2, nav3 = st.columns([1.2, 1.2, 7.6])
with nav1:
    if st.button("Discovery", use_container_width=True,
                 type="primary" if st.session_state["app_view"] == "discovery" else "secondary"):
        open_discovery()
with nav2:
    if st.button("AI Analyst", use_container_width=True,
                 type="primary" if st.session_state["app_view"] == "ai_analyst" else "secondary"):
        open_ai_analyst()

# ---------- AI Analyst page ----------

if st.session_state["app_view"] == "ai_analyst" and not st.session_state.get("selected_ipo"):
    st.markdown("## AI Analyst")
    st.caption("Ask deeper questions about the IPOs currently tracked by IPO Intelligence.")

    if not get_gemini_api_key():
        st.warning("AI is not configured. Add GEMINI_API_KEY to Streamlit secrets to use the analyst.")
        st.stop()

    st.markdown("#### Start with a question")
    prompt_cols = st.columns(4)
    prompts = [
        "Which open IPO looks best for listing gains?",
        "Which IPO has the best risk/reward right now?",
        "Which IPOs should I avoid and why?",
        "Compare the top 3 open IPOs for me.",
    ]

    for col, prompt in zip(prompt_cols, prompts):
        with col:
            if st.button(prompt, key="prompt_" + str(prompts.index(prompt)), use_container_width=True):
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

    st.stop()

# ---------- Detail view ----------

selected_id = st.session_state.get(
    "selected_ipo"
)

if selected_id:

    row, sub_history, gmp_history = get_selected_ipo(
        selected_id
    )

    if row:

        if st.button(
            "← Back to Discovery"
        ):
            st.session_state.pop(
                "selected_ipo",
                None
            )
            st.rerun()

        st.markdown("---")

        status = normalized_status(row)

        st.title(
            clean_text(row.get("company_name"))
            or "IPO"
        )

        status_col, segment_col = st.columns(
            [1, 4]
        )

        with status_col:
            st.markdown(
                f"**{status.upper()}**"
            )

        with segment_col:
            st.caption(
                clean_text(row.get("segment"))
                or "IPO"
            )

        # Hero metrics
        m1, m2, m3, m4 = st.columns(4)

        m1.metric(
            "Price band",
            price_band(row),
        )

        m2.metric(
            "Lot size",
            (
                f"{int(row['lot_size']):,}"
                if not is_missing(row.get("lot_size"))
                else "—"
            ),
        )

        m3.metric(
            "Issue size",
            (
                f"₹{float(row['issue_size']):,.2f} Cr"
                if not is_missing(row.get("issue_size"))
                else "—"
            ),
        )

        m4.metric(
            "GMP",
            money(row.get("gmp")),
        )

        # Timeline
        st.subheader("IPO timeline")

        t1, t2, t3, t4 = st.columns(4)

        t1.metric(
            "Open",
            format_date(row.get("open_date")),
        )

        t2.metric(
            "Close",
            format_date(row.get("close_date")),
        )

        t3.metric(
            "Allotment",
            format_date(row.get("allotment_date")),
        )

        t4.metric(
            "Listing",
            format_date(row.get("listing_date")),
        )

        st.markdown("---")

        # Subscription
        st.subheader("Subscription")

        s1, s2, s3, s4, s5 = st.columns(5)

        s1.metric(
            "QIB",
            multiple(row.get("qib")),
        )

        s2.metric(
            "sNII",
            multiple(row.get("snii")),
        )

        s3.metric(
            "bNII",
            multiple(row.get("bnii")),
        )

        s4.metric(
            "Retail",
            multiple(row.get("retail")),
        )

        s5.metric(
            "Total",
            multiple(row.get("subscription")),
        )

        s6, s7, s8 = st.columns(3)

        s6.metric(
            "Applications",
            applications(
                row.get("applications")
            ),
        )

        s7.metric(
            "GMP %",
            (
                f"{float(row['gmp_pct']):.1f}%"
                if not is_missing(row.get("gmp_pct"))
                else "—"
            ),
        )

        s8.metric(
            "Indicative listing",
            money(
                row.get("indicative_listing")
            ),
        )

        # Subscription chart only when we have
        # at least two distinct observations.
        if sub_history:

            sub_df = pd.DataFrame(
                sub_history
            )

            sub_df["captured_at"] = pd.to_datetime(
                sub_df["captured_at"],
                errors="coerce",
            )

            sub_df = (
                sub_df
                .dropna(
                    subset=["captured_at"]
                )
                .drop_duplicates(
                    subset=["captured_at"]
                )
                .sort_values("captured_at")
            )

            chart_cols = [
                col
                for col in [
                    "qib",
                    "nii",
                    "retail",
                    "total",
                ]
                if col in sub_df.columns
                and sub_df[col].notna().any()
            ]

            if len(sub_df) >= 2 and chart_cols:

                st.subheader(
                    "Subscription history"
                )

                chart = (
                    sub_df
                    .set_index("captured_at")
                    [chart_cols]
                )

                st.line_chart(chart)

            else:

                st.subheader(
                    "Subscription history"
                )

                st.caption(
                    "History will appear after "
                    "the next few data refreshes."
                )

        else:

            st.subheader(
                "Subscription history"
            )

            st.caption(
                "History will appear after "
                "the next few data refreshes."
            )

        # GMP chart only when we have
        # at least two distinct observations.
        if gmp_history:

            gmp_df = pd.DataFrame(
                gmp_history
            )

            gmp_df["captured_at"] = pd.to_datetime(
                gmp_df["captured_at"],
                errors="coerce",
            )

            gmp_df = (
                gmp_df
                .dropna(
                    subset=["captured_at"]
                )
                .drop_duplicates(
                    subset=["captured_at"]
                )
                .sort_values("captured_at")
            )

            if (
                len(gmp_df) >= 2
                and "gmp" in gmp_df.columns
                and gmp_df["gmp"].notna().any()
            ):

                st.subheader(
                    "GMP history"
                )

                st.line_chart(
                    gmp_df.set_index(
                        "captured_at"
                    )[["gmp"]]
                )

            else:

                st.subheader(
                    "GMP history"
                )

                st.caption(
                    "GMP history will appear "
                    "after the next few refreshes."
                )

        else:

            st.subheader(
                "GMP history"
            )

            st.caption(
                "GMP history will appear "
                "after the next few refreshes."
            )

        # Issue information
        st.markdown("---")

        st.subheader("Issue details")

        d1, d2 = st.columns(2)

        with d1:

            fresh = row.get("fresh_issue")
            ofs = row.get("ofs_issue")

            st.write(
                f"**Fresh issue:** "
                f"{money(fresh, 2)} Cr"
                if not is_missing(fresh)
                else "**Fresh issue:** —"
            )

            st.write(
                f"**OFS:** "
                f"{money(ofs, 2)} Cr"
                if not is_missing(ofs)
                else "**OFS:** —"
            )

            st.write(
                f"**Listing exchange:** "
                f"{clean_text(row.get('listing_exchange')) or '—'}"
            )

        with d2:

            st.write(
                f"**Registrar:** "
                f"{clean_text(row.get('registrar')) or '—'}"
            )

            st.write(
                f"**Lead managers:** "
                f"{clean_text(row.get('lead_managers')) or '—'}"
            )

            st.write(
                f"**Indicative listing:** "
                f"{money(row.get('indicative_listing'))}"
            )

        st.caption(
            "GMP is unofficial grey-market information "
            "and is not a guarantee of listing price."
        )

    else:

        st.error(
            "The selected IPO could not be found."
        )

        if st.button("Back to Discovery"):
            st.session_state.pop(
                "selected_ipo",
                None
            )
            st.rerun()

    st.stop()


# ---------- Discovery ----------

st.markdown("---")

c1, c2, c3, c4 = st.columns(
    [1, 1, 1, 2]
)

with c1:
    segment = st.selectbox(
        "Segment",
        ["All", "Mainboard", "SME"],
    )

with c2:
    status_filter = st.selectbox(
        "Status",
        [
            "All",
            "Live",
            "Upcoming",
            "Closed",
        ],
    )

with c3:
    sort = st.selectbox(
        "Sort",
        [
            "Close date",
            "Subscription",
            "GMP %",
        ],
    )

with c4:
    search = st.text_input(
        "Search IPO",
        placeholder="Company name...",
    )


view = df.copy()

if segment != "All":

    if segment == "SME":

        view = view[
            view["segment"]
            .fillna("")
            .str.contains(
                "SME",
                case=False,
                na=False,
            )
        ]

    else:

        view = view[
            view["segment"]
            .fillna("")
            .str.contains(
                "Mainboard",
                case=False,
                na=False,
            )
        ]


if status_filter != "All":

    view = view[
        view["display_status"]
        == status_filter
    ]


if search:

    view = view[
        view["company_name"]
        .fillna("")
        .str.contains(
            search,
            case=False,
            na=False,
        )
    ]


if sort == "Subscription":

    view = view.sort_values(
        "subscription",
        ascending=False,
        na_position="last",
    )

elif sort == "GMP %":

    view = view.sort_values(
        "gmp_pct",
        ascending=False,
        na_position="last",
    )

else:

    view["sort_close"] = pd.to_datetime(
        view["close_date"],
        errors="coerce",
    )

    view = view.sort_values(
        ["sort_close", "company_name"],
        na_position="last",
    )


# Summary
live_count = int(
    (df["display_status"] == "Live").sum()
)

upcoming_count = int(
    (df["display_status"] == "Upcoming").sum()
)

closed_count = int(
    (df["display_status"].isin(
        [
            "Closed",
            "Allotment Out",
            "Allotment Awaited",
        ]
    )).sum()
)


a, b, c, d = st.columns(4)

a.metric(
    "IPOs tracked",
    len(df),
)

b.metric(
    "Open now",
    live_count,
)

c.metric(
    "Upcoming",
    upcoming_count,
)

d.metric(
    "Closed / allotment",
    closed_count,
)


disc_head, disc_action = st.columns([6, 1])
with disc_head:
    st.markdown("## IPO Discovery")
with disc_action:
    if st.button("Open AI Analyst", use_container_width=True):
        open_ai_analyst()


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
                    st.session_state["app_view"] = "discovery"
                    st.rerun()

            if score:
                st.markdown('<div class="ai-score-strip">', unsafe_allow_html=True)
                render_ai_score(score)
                risks = score.get("key_risks", [])
                if risks:
                    st.caption("Risks: " + " · ".join(risks[:3]))
                st.markdown('</div>', unsafe_allow_html=True)

# Always show the three useful buckets.
render_section(
    "Open now",
    view[
        view["display_status"]
        == "Live"
    ],
)

render_section(
    "Upcoming",
    view[
        view["display_status"]
        == "Upcoming"
    ],
)

render_section(
    "Closed / allotment",
    view[
        view["display_status"].isin(
            [
                "Closed",
                "Allotment Out",
                "Allotment Awaited",
            ]
        )
    ],
)

if view.empty:

    st.info(
        "No IPOs match your filters."
    )
