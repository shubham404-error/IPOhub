import json, sqlite3
from pathlib import Path
from config import DB_PATH
SCHEMA='''CREATE TABLE IF NOT EXISTS ipos (ipo_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, source_id TEXT NOT NULL, company_name TEXT, symbol TEXT, ipo_type TEXT, segment TEXT, status TEXT, open_date TEXT, close_date TEXT, listing_date TEXT, price_low REAL, price_high REAL, lot_size INTEGER, issue_size REAL, gmp REAL, subscription REAL, source_url TEXT, collected_at TEXT NOT NULL, updated_at TEXT NOT NULL, raw_json TEXT, UNIQUE(source, source_id));'''
class Database:
 def __init__(self,path=DB_PATH):
  Path(path).parent.mkdir(parents=True,exist_ok=True); self.conn=sqlite3.connect(path); self.conn.executescript(SCHEMA); self.conn.commit()
 def upsert_ipos(self,rows):
  q='''INSERT INTO ipos (source,source_id,company_name,symbol,ipo_type,segment,status,open_date,close_date,listing_date,price_low,price_high,lot_size,issue_size,gmp,subscription,source_url,collected_at,updated_at,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,source_id) DO UPDATE SET company_name=excluded.company_name,symbol=excluded.symbol,ipo_type=excluded.ipo_type,segment=excluded.segment,status=excluded.status,open_date=excluded.open_date,close_date=excluded.close_date,listing_date=excluded.listing_date,price_low=excluded.price_low,price_high=excluded.price_high,lot_size=excluded.lot_size,issue_size=excluded.issue_size,gmp=excluded.gmp,subscription=excluded.subscription,source_url=excluded.source_url,updated_at=excluded.updated_at,raw_json=excluded.raw_json'''
  for r in rows:self.conn.execute(q,(r['source'],r['source_id'],r.get('company_name'),r.get('symbol'),r.get('ipo_type'),r.get('segment'),r.get('status'),r.get('open_date'),r.get('close_date'),r.get('listing_date'),r.get('price_low'),r.get('price_high'),r.get('lot_size'),r.get('issue_size'),r.get('gmp'),r.get('subscription'),r.get('source_url'),r['collected_at'],r['collected_at'],json.dumps(r.get('raw',{}),default=str)))
  self.conn.commit(); return len(rows)
 def close(self): self.conn.close()
