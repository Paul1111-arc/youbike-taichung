#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把雲端採集的 gzip CSV 合併進 youbike.db，schema 與 youbike_collect.py 完全一致，
所以合併完直接跑 youbike_report.py 就能出圖。

可重複執行：已存在的 (station_id, src_time) 會被略過，不會重複計算。
也可以把本機採集的舊 youbike.db 當成起點，雲端資料直接疊上去。

用法
----
    python merge_to_db.py                       # 讀 ./data，寫 ./youbike.db
    python merge_to_db.py --data path/to/data --db path/to/youbike.db
    python merge_to_db.py --since 2026-11-05    # 只併這天以後的
"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import os
import sqlite3
import sys
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    station_id TEXT PRIMARY KEY, name TEXT, district TEXT, address TEXT,
    lat REAL, lng REAL, capacity INTEGER, service_type TEXT,
    first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS samples (
    station_id TEXT NOT NULL, src_time TEXT NOT NULL, polled_at TEXT NOT NULL,
    rent INTEGER, ret INTEGER, capacity INTEGER, active INTEGER,
    PRIMARY KEY (station_id, src_time));
CREATE INDEX IF NOT EXISTS idx_samples_time ON samples(src_time);
CREATE TABLE IF NOT EXISTS polls (
    polled_at TEXT PRIMARY KEY, source TEXT, n_station INTEGER,
    n_new INTEGER, ok INTEGER, note TEXT);
"""


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--db", default="youbike.db")
    ap.add_argument("--since", help="YYYY-MM-DD，只併這天（含）以後的檔案")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.data, "*", "*.csv.gz")))
    if a.since:
        files = [f for f in files
                 if os.path.basename(os.path.dirname(f)) >= a.since]
    if not files:
        print(f"{a.data} 底下找不到 csv.gz", file=sys.stderr)
        return 1

    con = sqlite3.connect(a.db)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    before = con.execute("SELECT COUNT(*) FROM samples").fetchone()[0]

    added = skipped = 0
    for i, path in enumerate(files, 1):
        day = os.path.basename(os.path.dirname(path))
        stem = os.path.basename(path).split("_")[0]
        src = os.path.basename(path).split("_")[-1].replace(".csv.gz", "")
        polled = f"{day} {stem[:2]}:{stem[2:4]}:{stem[4:6]}"
        try:
            with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
        except Exception as e:                                   # noqa: BLE001
            print(f"  跳過損壞檔 {path}: {e}", file=sys.stderr)
            skipped += 1
            continue
        cur = con.cursor()
        n_new = 0
        for r in rows:
            sid = r.get("station_id")
            if not sid:
                continue
            cur.execute(
                """INSERT INTO stations
                   (station_id,name,district,address,lat,lng,capacity,
                    service_type,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(station_id) DO UPDATE SET
                     name=excluded.name, district=excluded.district,
                     address=excluded.address, lat=excluded.lat, lng=excluded.lng,
                     capacity=excluded.capacity, last_seen=excluded.last_seen""",
                (sid, r.get("name", ""), r.get("district", ""), r.get("address", ""),
                 to_float(r.get("lat")), to_float(r.get("lng")),
                 to_int(r.get("capacity")), r.get("service_type", ""), polled, polled))
            cur.execute(
                """INSERT OR IGNORE INTO samples
                   (station_id,src_time,polled_at,rent,ret,capacity,active)
                   VALUES (?,?,?,?,?,?,?)""",
                (sid, r.get("src_time", ""), polled, to_int(r.get("rent")),
                 to_int(r.get("ret")), to_int(r.get("capacity")),
                 to_int(r.get("active"))))
            n_new += cur.rowcount
        con.execute("INSERT OR REPLACE INTO polls VALUES (?,?,?,?,?,?)",
                    (polled, src, len(rows), n_new, 1, "cloud"))
        added += n_new
        if i % 50 == 0 or i == len(files):
            con.commit()
            print(f"  {i}/{len(files)} 檔…")
    con.commit()

    after = con.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    q = con.execute("SELECT MIN(src_time),MAX(src_time),"
                    "COUNT(DISTINCT station_id) FROM samples").fetchone()
    print("=" * 52)
    print(f"檔案 {len(files)} 個" + (f"（損壞 {skipped}）" if skipped else ""))
    print(f"快照 {before:,} → {after:,}（新增 {after-before:,}）")
    print(f"站點 {q[2]}｜時間 {q[0]} ~ {q[1]}")
    print("\n每小時取樣時刻數（理想 ≥4，0 = 缺漏）")
    rows = con.execute("""SELECT substr(src_time,1,10) d, substr(src_time,12,2) h,
                          COUNT(DISTINCT src_time) n FROM samples
                          GROUP BY d,h""").fetchall()
    grid = {(r[0], r[1]): r[2] for r in rows}
    days = sorted({r[0] for r in rows})
    print("       " + " ".join(f"{h:02d}" for h in range(24)))
    for d in days[-14:]:
        line = " ".join(f"{grid.get((d, f'{h:02d}'), 0):2d}" for h in range(24))
        print(f"{d[5:]}  {line}")
    print("=" * 52)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
