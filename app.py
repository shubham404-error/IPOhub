
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

st.title("IPO Intelligence")
st.caption(
    "Discover IPOs, compare demand, and inspect live subscription and GMP data."
)

top_left, top_right = st.columns([1, 5])

with top_left:
    if st.button(
        "Refresh data",
        type="primary",
        use_container_width=True,
    ):
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



# ---------- AI Advisor ----------

if not st.session_state.get("selected_ipo"):

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

    if "ai_messages" not in st.session_state:
        st.session_state["ai_messages"] = []

    with st.expander("AI IPO Advisor", expanded=False):

        st.caption(
            "Gemini reviews the IPO data currently collected by this app. "
            "It separates investment attractiveness from allotment attractiveness "
            "and does not treat GMP as a guarantee."
        )

        api_key_available = bool(get_gemini_api_key())

        if not api_key_available:
            st.warning(
                "Gemini API key not configured. Add GEMINI_API_KEY to "
                "Streamlit secrets or your environment."
            )

        ai_col1, ai_col2, ai_col3 = st.columns(3)

        with ai_col1:
            ai_objective = st.selectbox(
                "What are you optimizing for?",
                [
                    "Balanced",
                    "Listing gains",
                    "Long-term investment",
                    "Allotment probability",
                ],
                key="ai_objective",
            )

        with ai_col2:
            ai_risk = st.selectbox(
                "Risk tolerance",
                ["Conservative", "Moderate", "Aggressive"],
                key="ai_risk",
            )

        with ai_col3:
            ai_horizon = st.selectbox(
                "Holding horizon",
                ["Listing day", "Short term", "Long term"],
                key="ai_horizon",
            )

        if st.button(
            "Analyze current IPOs",
            type="primary",
            disabled=not api_key_available,
            key="analyze_ipos_button",
        ):
            with st.spinner("Gemini is analyzing the current IPO set..."):
                try:
                    st.session_state["ai_analysis"] = analyze_ipos(
                        build_ai_dataset(df),
                        objective=ai_objective,
                        risk_tolerance=ai_risk,
                        horizon=ai_horizon,
                    )
                except Exception as exc:
                    st.error(f"Gemini error: {exc}")

        analysis = st.session_state.get("ai_analysis")

        if analysis:

            st.markdown("### What looks worth applying?")
            st.caption(analysis.get("summary", ""))

            for item in analysis.get("recommendations", []):

                verdict = str(item.get("verdict", "Insufficient data"))

                with st.container(border=True):

                    c1, c2, c3 = st.columns([3, 1, 1])

                    with c1:
                        st.markdown(
                            f"**{item.get('company_name', 'Unknown')}**"
                        )
                        st.caption(
                            f"{verdict.upper()} · "
                            f"{item.get('confidence', 'Low')} confidence"
                        )

                    with c2:
                        st.metric(
                            "Investment",
                            f"{item.get('investment_score', 0)}/100",
                        )

                    with c3:
                        st.metric(
                            "Allotment",
                            f"{item.get('allotment_score', 0)}/100",
                        )

                    st.write(
                        item.get(
                            "reason",
                            "No explanation provided.",
                        )
                    )

                    risks = item.get("key_risks", [])
                    if risks:
                        st.caption(
                            "Key risks: " + " · ".join(risks[:3])
                        )

            st.markdown("### Ask the advisor")

            for message in st.session_state["ai_messages"]:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

            user_question = st.chat_input(
                "Ask: Which IPO is best for listing gains?",
                key="ai_chat_input",
            )

            if user_question:

                st.session_state["ai_messages"].append(
                    {
                        "role": "user",
                        "content": user_question,
                    }
                )

                with st.spinner("Thinking..."):
                    try:
                        answer = chat_with_advisor(
                            build_ai_dataset(df),
                            analysis,
                            user_question,
                        )

                        st.session_state["ai_messages"].append(
                            {
                                "role": "assistant",
                                "content": answer,
                            }
                        )

                        st.rerun()

                    except Exception as exc:
                        st.error(f"Gemini error: {exc}")


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


st.markdown("## IPO Discovery")


def render_section(
    title,
    section_df,
):

    if section_df.empty:
        return

    st.markdown(
        f"### {title}"
    )

    for _, row in section_df.iterrows():

        company = (
            clean_text(
                row.get("company_name")
            )
            or "Unknown IPO"
        )

        status = normalized_status(
            row
        )

        segment_name = (
            clean_text(
                row.get("segment")
            )
            or "IPO"
        )

        # Use a bordered container so each IPO
        # reads as a self-contained card.
        with st.container(
            border=True
        ):

            title_col, metrics_col, action_col = st.columns(
                [2.4, 5.8, 1]
            )

            with title_col:

                st.markdown(
                    f"**{company}**"
                )

                st.caption(
                    f"{segment_name} · {status}"
                )

                close_date = format_date(
                    row.get("close_date")
                )

                if status == "Live":
                    st.caption(
                        f"Closes {close_date}"
                    )

                elif status == "Upcoming":
                    st.caption(
                        f"Opens {format_date(row.get('open_date'))}"
                    )

            with metrics_col:

                m1, m2, m3, m4 = st.columns(4)

                m1.metric(
                    "Price",
                    price_band(row),
                )

                m2.metric(
                    "Lot",
                    (
                        f"{int(row['lot_size']):,}"
                        if not is_missing(
                            row.get("lot_size")
                        )
                        else "—"
                    ),
                )

                m3.metric(
                    "GMP",
                    money(row.get("gmp")),
                )

                m4.metric(
                    "Subscription",
                    multiple(
                        row.get("subscription")
                    ),
                )

            with action_col:

                st.write("")

                if st.button(
                    "View",
                    key=f"view_{row['source_id']}",
                    use_container_width=True,
                ):

                    st.session_state[
                        "selected_ipo"
                    ] = row["source_id"]

                    st.rerun()


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
