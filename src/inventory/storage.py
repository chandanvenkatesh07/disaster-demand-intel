"""Storage module for transfer orders.
Provides DuckDB persistence for synthetic demo data.
These orders are not real procurement actions.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import duckdb
from .replenishment import TransferOrder

DB_PATH = Path(__file__).resolve().parents[2] / 'data' / 'processed' / 'regions.duckdb'


def _ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute('''
        CREATE TABLE IF NOT EXISTS transfer_order (
            to_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            dest_osm_id TEXT NOT NULL,
            dest_name TEXT NOT NULL,
            dest_county TEXT,
            sku_id TEXT NOT NULL,
            sku_name TEXT NOT NULL,
            units INTEGER NOT NULL,
            urgency TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            rationale TEXT
        )
    ''')


def persist_orders(orders: list[TransferOrder]) -> int:
    '''Insert-or-replace each TO. Returns count written.'''
    if not orders:
        return 0
    con = duckdb.connect(DB_PATH.as_posix())
    _ensure_table(con)
    now = datetime.now(timezone.utc).isoformat()
    rows = [(o.to_id, o.source_type, o.source_id, o.source_name,
             o.dest_osm_id, o.dest_name, o.dest_county,
             o.sku_id, o.sku_name, o.units, o.urgency,
             o.status, o.created_at, now, o.rationale)
            for o in orders]
    con.executemany('INSERT OR REPLACE INTO transfer_order VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    con.close()
    return len(rows)


def list_orders(status: str | None = None, limit: int = 500) -> list[dict]:
    '''Return TO rows as plain dicts. Optional status filter.'''
    con = duckdb.connect(DB_PATH.as_posix())
    _ensure_table(con)
    if status:
        rows = con.execute('SELECT * FROM transfer_order WHERE status = ? ORDER BY created_at DESC LIMIT ?',
                           [status, limit]).fetchall()
    else:
        rows = con.execute('SELECT * FROM transfer_order ORDER BY created_at DESC LIMIT ?', [limit]).fetchall()
    cols = [d[0] for d in con.description]
    con.close()
    return [dict(zip(cols, r)) for r in rows]


def update_status(to_id: str, status: str) -> bool:
    '''Flip status. Returns True if a row was updated.'''
    if status not in ('awaiting_approval', 'approved', 'rejected', 'fulfilled'):
        raise ValueError(f'invalid status: {status}')
    con = duckdb.connect(DB_PATH.as_posix())
    _ensure_table(con)
    now = datetime.now(timezone.utc).isoformat()
    exists = con.execute('SELECT 1 FROM transfer_order WHERE to_id = ? LIMIT 1', [to_id]).fetchone()
    if not exists:
        con.close()
        return False
    con.execute('UPDATE transfer_order SET status = ?, updated_at = ? WHERE to_id = ?',
                [status, now, to_id])
    con.close()
    return True


def clear_all() -> int:
    '''Truncate transfer_order. Returns count deleted.'''
    con = duckdb.connect(DB_PATH.as_posix())
    _ensure_table(con)
    n = con.execute('SELECT COUNT(*) FROM transfer_order').fetchone()[0]
    con.execute('DELETE FROM transfer_order')
    con.close()
    return n


def summary() -> dict:
    '''Returns {status: count} for all rows.'''
    con = duckdb.connect(DB_PATH.as_posix())
    _ensure_table(con)
    rows = con.execute('SELECT status, COUNT(*) FROM transfer_order GROUP BY status').fetchall()
    con.close()
    return {s: n for s, n in rows}
