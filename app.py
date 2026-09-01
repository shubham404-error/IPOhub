import sqlite3
from pathlib import Path
import pandas as pd, streamlit as st
from collector import collect_once
from config import DB_PATH
st.set_page_config(page_title='IPO Intelligence',page_icon='📈',layout='wide')
st.title('IPO Intelligence'); st.caption('Live IPO discovery powered by IPO Ji.')
if st.button('Refresh now',type='primary'):
 try: st.success(f"Updated {collect_once()['count']} IPOs."); st.rerun()
 except Exception as e: st.error(f'Collector error: {e}')
if not Path(DB_PATH).exists(): st.info('No data yet. Click Refresh now.'); st.stop()
with sqlite3.connect(DB_PATH) as c: df=pd.read_sql_query('SELECT company_name AS Company,segment AS Segment,status AS Status,price_low AS "Price Low",price_high AS "Price High",lot_size AS "Lot Size",issue_size AS "Issue Size",open_date AS Open,close_date AS Close,gmp AS GMP,subscription AS Subscription,updated_at AS Updated FROM ipos ORDER BY close_date,company_name',c)
st.dataframe(df,use_container_width=True,hide_index=True)
