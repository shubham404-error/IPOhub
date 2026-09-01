import json
import sqlite3
from pathlib import Path

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS ipos (
    ipo_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    company_name TEXT,
    symbol TEXT,
    ipo_type TEXT,
    segment TEXT,
    status TEXT,
    open_date TEXT,
    close_date TEXT,
    allotment_date TEXT,
    listing_date TEXT,
    listing_exchange TEXT,
    price_low REAL,
    price_high REAL,
    lot_size INTEGER,
    issue_size REAL,
    fresh_issue REAL,
    ofs_issue REAL,
    gmp REAL,
    gmp_pct REAL,
    indicative_listing REAL,
    gmp_updated_at TEXT,
    subscription REAL,
    qib REAL,
    nii REAL,
    snii REAL,
    bnii REAL,
    retail REAL,
    employee REAL,
    others REAL,
    applications INTEGER,
    subscription_updated_at TEXT,
    registrar TEXT,
    lead_managers TEXT,
    source_url TEXT,
    detail_url TEXT,
    subscription_url TEXT,
    gmp_url TEXT,
    collected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    raw_json TEXT,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS subscription_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ipo_source TEXT NOT NULL,
    ipo_source_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    qib REAL,
    nii REAL,
    snii REAL,
    bnii REAL,
    retail REAL,
    employee REAL,
    others REAL,
    total REAL,
    applications INTEGER,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS gmp_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ipo_source TEXT NOT NULL,
    ipo_source_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    gmp REAL,
    gmp_pct REAL,
    indicative_listing REAL,
    raw_json TEXT
);
"""

MIGRATIONS = {
    "allotment_date": "TEXT",
    "listing_exchange": "TEXT",
    "fresh_issue": "REAL",
    "ofs_issue": "REAL",
    "gmp_pct": "REAL",
    "indicative_listing": "REAL",
    "gmp_updated_at": "TEXT",
    "qib": "REAL",
    "nii": "REAL",
    "snii": "REAL",
    "bnii": "REAL",
    "retail": "REAL",
    "employee": "REAL",
    "others": "REAL",
    "applications": "INTEGER",
    "subscription_updated_at": "TEXT",
    "registrar": "TEXT",
    "lead_managers": "TEXT",
    "detail_url": "TEXT",
    "subscription_url": "TEXT",
    "gmp_url": "TEXT",
}


class Database:
    def __init__(self, path=DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        existing = {
            row[1] for row in self.conn.execute("PRAGMA table_info(ipos)")
        }
        for name, sql_type in MIGRATIONS.items():
            if name not in existing:
                self.conn.execute(
                    f"ALTER TABLE ipos ADD COLUMN {name} {sql_type}"
                )

    def upsert_ipos(self, rows):
        query = """
        INSERT INTO ipos (
            source, source_id, company_name, symbol, ipo_type, segment, status,
            open_date, close_date, allotment_date, listing_date, listing_exchange,
            price_low, price_high, lot_size, issue_size, fresh_issue, ofs_issue,
            gmp, gmp_pct, indicative_listing, gmp_updated_at,
            subscription, qib, nii, snii, bnii, retail, employee, others,
            applications, subscription_updated_at, registrar, lead_managers,
            source_url, detail_url, subscription_url, gmp_url,
            collected_at, updated_at, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source, source_id) DO UPDATE SET
            company_name=excluded.company_name,
            symbol=excluded.symbol,
            ipo_type=excluded.ipo_type,
            segment=excluded.segment,
            status=excluded.status,
            open_date=excluded.open_date,
            close_date=excluded.close_date,
            allotment_date=excluded.allotment_date,
            listing_date=excluded.listing_date,
            listing_exchange=excluded.listing_exchange,
            price_low=excluded.price_low,
            price_high=excluded.price_high,
            lot_size=excluded.lot_size,
            issue_size=excluded.issue_size,
            fresh_issue=excluded.fresh_issue,
            ofs_issue=excluded.ofs_issue,
            gmp=excluded.gmp,
            gmp_pct=excluded.gmp_pct,
            indicative_listing=excluded.indicative_listing,
            gmp_updated_at=excluded.gmp_updated_at,
            subscription=excluded.subscription,
            qib=excluded.qib,
            nii=excluded.nii,
            snii=excluded.snii,
            bnii=excluded.bnii,
            retail=excluded.retail,
            employee=excluded.employee,
            others=excluded.others,
            applications=excluded.applications,
            subscription_updated_at=excluded.subscription_updated_at,
            registrar=excluded.registrar,
            lead_managers=excluded.lead_managers,
            source_url=excluded.source_url,
            detail_url=excluded.detail_url,
            subscription_url=excluded.subscription_url,
            gmp_url=excluded.gmp_url,
            updated_at=excluded.updated_at,
            raw_json=excluded.raw_json
        """

        for r in rows:
            values = (
                r.get("source"), r.get("source_id"), r.get("company_name"),
                r.get("symbol"), r.get("ipo_type"), r.get("segment"), r.get("status"),
                r.get("open_date"), r.get("close_date"), r.get("allotment_date"),
                r.get("listing_date"), r.get("listing_exchange"), r.get("price_low"),
                r.get("price_high"), r.get("lot_size"), r.get("issue_size"),
                r.get("fresh_issue"), r.get("ofs_issue"), r.get("gmp"),
                r.get("gmp_pct"), r.get("indicative_listing"), r.get("gmp_updated_at"),
                r.get("subscription"), r.get("qib"), r.get("nii"), r.get("snii"),
                r.get("bnii"), r.get("retail"), r.get("employee"), r.get("others"),
                r.get("applications"), r.get("subscription_updated_at"),
                r.get("registrar"), r.get("lead_managers"), r.get("source_url"),
                r.get("detail_url"), r.get("subscription_url"), r.get("gmp_url"),
                r.get("collected_at"), r.get("collected_at"),
                json.dumps(r.get("raw", {}), default=str),
            )
            self.conn.execute(query, values)
        self.conn.commit()
        return len(rows)

    def add_subscription_snapshot(self, source, source_id, data, captured_at):
        self.conn.execute(
            """INSERT INTO subscription_snapshots
            (ipo_source, ipo_source_id, captured_at, qib, nii, snii, bnii,
             retail, employee, others, total, applications, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                source, source_id, captured_at, data.get("qib"), data.get("nii"),
                data.get("snii"), data.get("bnii"), data.get("retail"),
                data.get("employee"), data.get("others"), data.get("total"),
                data.get("applications"), json.dumps(data.get("raw", {}), default=str),
            ),
        )
        self.conn.commit()

    def add_gmp_snapshot(self, source, source_id, data, captured_at):
        self.conn.execute(
            """INSERT INTO gmp_snapshots
            (ipo_source, ipo_source_id, captured_at, gmp, gmp_pct,
             indicative_listing, raw_json)
            VALUES (?,?,?,?,?,?,?)""",
            (
                source, source_id, captured_at, data.get("gmp"),
                data.get("gmp_pct"), data.get("indicative_listing"),
                json.dumps(data.get("raw", {}), default=str),
            ),
        )
        self.conn.commit()

    def get_ipos(self):
        return self.conn.execute(
            "SELECT * FROM ipos ORDER BY close_date, company_name"
        ).fetchall()

    def get_ipo(self, source_id):
        return self.conn.execute(
            "SELECT * FROM ipos WHERE source='ipoji' AND source_id=?",
            (source_id,),
        ).fetchone()

    def get_subscription_history(self, source_id):
        return self.conn.execute(
            """SELECT * FROM subscription_snapshots
               WHERE ipo_source='ipoji' AND ipo_source_id=?
               ORDER BY captured_at""",
            (source_id,),
        ).fetchall()

    def get_gmp_history(self, source_id):
        return self.conn.execute(
            """SELECT * FROM gmp_snapshots
               WHERE ipo_source='ipoji' AND ipo_source_id=?
               ORDER BY captured_at""",
            (source_id,),
        ).fetchall()

    def close(self):
        self.conn.close()
