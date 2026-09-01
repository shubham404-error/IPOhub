import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from collector import collect_once
from config import DB_PATH
from database import Database

st.set_page_config(page_title="IPO Intelligence", page_icon="📈", layout="wide")


def money(value):
    if value is None or pd.isna(value):
        return "—"
    return f"₹{value:,.2f}" if float(value) % 1 else f"₹{value:,.0f}"


def multiple(value):
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.2f}x"


def get_df():
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query("SELECT * FROM ipos ORDER BY close_date, company_name", conn)


st.title("IPO Intelligence")
st.caption("Discovery + IPO detail + GMP + live subscription intelligence")

if st.button("Refresh data", type="primary"):
    with st.spinner("Refreshing IPO data..."):
        try:
            result = collect_once(enrich=True)
            st.success(f"Updated {result['count']} IPOs with detail, subscription and GMP data.")
            st.rerun()
        except Exception as exc:
            st.error(f"Collector error: {exc}")

if not Path(DB_PATH).exists():
    st.info("No data yet. Click Refresh data.")
    st.stop()

try:
    df = get_df()
except Exception as exc:
    st.error(f"Database error: {exc}")
    st.stop()

if df.empty:
    st.info("No IPOs collected yet. Click Refresh data.")
    st.stop()

# Discovery filters
c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
with c1:
    segment = st.selectbox("Segment", ["All", "Mainboard", "SME"])
with c2:
    status = st.selectbox("Status", ["All", "Live", "Upcoming", "Closed"])
with c3:
    sort = st.selectbox("Sort", ["Close date", "Subscription", "GMP %"])
with c4:
    search = st.text_input("Search IPO", placeholder="Company name...")

view = df.copy()

if segment != "All":
    if segment == "SME":
        view = view[view["segment"].fillna("").str.contains("SME", case=False)]
    else:
        view = view[view["segment"].fillna("").str.contains("Mainboard", case=False)]

if status == "Live":
    view = view[view["status"].fillna("").str.lower().isin(["live", "open", "pre-apply"])]
elif status == "Upcoming":
    view = view[~view["status"].fillna("").str.lower().isin(["live", "open", "pre-apply", "closed", "allotment out", "allotment awaited"])]
elif status == "Closed":
    view = view[view["status"].fillna("").str.lower().isin(["closed", "allotment out", "allotment awaited"])]

if search:
    view = view[view["company_name"].fillna("").str.contains(search, case=False, na=False)]

if sort == "Subscription":
    view = view.sort_values("subscription", ascending=False, na_position="last")
elif sort == "GMP %":
    view = view.sort_values("gmp_pct", ascending=False, na_position="last")
else:
    view = view.sort_values(["close_date", "company_name"], na_position="last")

live_count = len(df[df["status"].fillna("").str.lower().isin(["live", "open", "pre-apply"])])
upcoming_count = len(df[~df["status"].fillna("").str.lower().isin(["live", "open", "pre-apply", "closed", "allotment out", "allotment awaited"])])

a, b, c = st.columns(3)
a.metric("IPOs tracked", len(df))
b.metric("Live / Pre-Apply", live_count)
c.metric("Upcoming", upcoming_count)

st.subheader("IPO Discovery")

for _, row in view.iterrows():
    title_col, data_col, action_col = st.columns([2.3, 5.5, 1])
    with title_col:
        st.markdown(f"### {row['company_name']}")
        st.caption(f"{row.get('segment') or 'IPO'} · {row.get('status') or 'Status unavailable'}")
    with data_col:
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Price", f"₹{row['price_low']:,.0f}–₹{row['price_high']:,.0f}" if pd.notna(row['price_low']) and pd.notna(row['price_high']) else "—")
        p2.metric("Lot", f"{int(row['lot_size']):,}" if pd.notna(row['lot_size']) else "—")
        p3.metric("GMP", money(row['gmp']))
        p4.metric("Subscription", multiple(row['subscription']))
    with action_col:
        if st.button("View", key=f"view_{row['source_id']}"):
            st.session_state["selected_ipo"] = row["source_id"]
    st.divider()

selected_id = st.session_state.get("selected_ipo")
if selected_id:
    db = Database()
    try:
        row = db.get_ipo(selected_id)
        sub_history = db.get_subscription_history(selected_id)
        gmp_history = db.get_gmp_history(selected_id)
    finally:
        db.close()

    if row:
        st.markdown(f"# {row['company_name']}")
        st.caption(f"{row['segment'] or 'IPO'} · {row['status'] or 'Status unavailable'}")

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Price band", f"₹{row['price_low']:,.0f}–₹{row['price_high']:,.0f}" if row['price_low'] is not None and row['price_high'] is not None else "—")
        k2.metric("Lot size", f"{int(row['lot_size']):,}" if row['lot_size'] is not None else "—")
        k3.metric("Issue size", f"₹{row['issue_size']:,.2f} Cr" if row['issue_size'] is not None else "—")
        k4.metric("GMP", money(row['gmp']))

        st.subheader("IPO timeline")
        t1, t2, t3, t4 = st.columns(4)
        t1.write(f"**Open**\n\n{row['open_date'] or '—'}")
        t2.write(f"**Close**\n\n{row['close_date'] or '—'}")
        t3.write(f"**Allotment**\n\n{row['allotment_date'] or '—'}")
        t4.write(f"**Listing**\n\n{row['listing_date'] or '—'}")

        st.subheader("Subscription")
        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("QIB", multiple(row['qib']))
        s2.metric("NII", multiple(row['nii']))
        s3.metric("sNII", multiple(row['snii']))
        s4.metric("bNII", multiple(row['bnii']))
        s5.metric("Retail", multiple(row['retail']))

        s6, s7, s8 = st.columns(3)
        s6.metric("Total", multiple(row['subscription']))
        s7.metric("Applications", f"{int(row['applications']):,}" if row['applications'] is not None else "—")
        s8.metric("GMP %", f"{row['gmp_pct']:.1f}%" if row['gmp_pct'] is not None else "—")

        if sub_history:
            sub_df = pd.DataFrame([dict(x) for x in sub_history])
            sub_df["captured_at"] = pd.to_datetime(sub_df["captured_at"], errors="coerce")
            chart_cols = [x for x in ["total", "qib", "nii", "retail"] if x in sub_df.columns and sub_df[x].notna().any()]
            if chart_cols:
                st.line_chart(sub_df.set_index("captured_at")[chart_cols])

        if gmp_history:
            gmp_df = pd.DataFrame([dict(x) for x in gmp_history])
            gmp_df["captured_at"] = pd.to_datetime(gmp_df["captured_at"], errors="coerce")
            if gmp_df["gmp"].notna().any():
                st.subheader("GMP history")
                st.line_chart(gmp_df.set_index("captured_at")[["gmp"]])

        st.subheader("Issue details")
        d1, d2 = st.columns(2)
        with d1:
            st.write(f"**Fresh issue:** ₹{row['fresh_issue']:,.2f} Cr" if row['fresh_issue'] is not None else "**Fresh issue:** —")
            st.write(f"**OFS:** ₹{row['ofs_issue']:,.2f} Cr" if row['ofs_issue'] is not None else "**OFS:** —")
            st.write(f"**Listing:** {row['listing_exchange'] or '—'}")
        with d2:
            st.write(f"**Registrar:** {row['registrar'] or '—'}")
            st.write(f"**Lead managers:** {row['lead_managers'] or '—'}")
            st.write(f"**Indicative listing:** {money(row['indicative_listing'])}")

        st.caption("GMP is unofficial grey-market information and is not a guarantee of listing price.")
