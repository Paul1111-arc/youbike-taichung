#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雲端採集：抓一次台中 YouBike 全站狀態，存成一個 gzip CSV。

每次執行都是全新環境，所以不碰資料庫，只寫檔：
    data/YYYY-MM-DD/HHMMSS.csv.gz

不同次執行寫不同檔名，永遠不會衝突，git 也不需要 read-modify-write。
本機再用 merge_to_db.py 把這些檔合併成 youbike.db。

用法
----
    python collect_once.py              # 依序嘗試所有來源
    python collect_once.py --probe      # 只測哪個來源通，不寫檔
    python collect_once.py --source tdx
"""
from __future__ import annotations

import argparse
import csv
import gzip
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

TPE = timezone(timedelta(hours=8))
CITY = "Taichung"
CITY_ZH = "臺中市"
UA = {"User-Agent": "youbike-research-collector/2.0 (academic research)"}
FIELDS = ["station_id", "name", "district", "address", "lat", "lng",
          "capacity", "rent", "ret", "active", "service_type", "src_time"]


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_time(v) -> str:
    if v is None:
        return datetime.now(TPE).strftime("%Y-%m-%d %H:%M:%S")
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S",
                "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s, fmt).astimezone(TPE).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return s[:19]


# --------------------------------------------------------------- 來源
def fetch_govopen(s: requests.Session) -> list[dict]:
    """政府資料開放平臺 dataset 136781（臺中市交通局）。"""
    meta = s.get("https://data.gov.tw/api/v2/rest/dataset/136781",
                 headers=UA, timeout=40)
    meta.raise_for_status()
    urls = [d.get("resourceDownloadUrl")
            for d in meta.json().get("result", {}).get("distribution", [])
            if d.get("resourceDownloadUrl")]
    if not urls:
        raise RuntimeError("metadata 無資源網址")
    r = s.get(urls[0], headers=UA, timeout=60)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = data.get("retVal") or data.get("data") or list(data.values())
    rows = [dict(station_id=f"YB_{d.get('sno')}", name=d.get("sna", ""),
                 district=d.get("sarea", ""), address=d.get("ar", ""),
                 lat=_float(d.get("lat")), lng=_float(d.get("lng")),
                 capacity=_int(d.get("tot")), rent=_int(d.get("sbi")),
                 ret=_int(d.get("bemp")), active=_int(d.get("act")) or 0,
                 service_type="YouBike2.0", src_time=_norm_time(d.get("mday")))
            for d in data if isinstance(d, dict) and d.get("sno")]
    if not rows:
        raise RuntimeError("govopen 回傳 0 筆")
    return rows


def fetch_youbike(s: requests.Session) -> list[dict]:
    """YouBike 官方全台 JSON。"""
    last = None
    for url in ("https://apis.youbike.com.tw/json/station-min-yb2.json",
                "https://apis.youbike.com.tw/api/front/station/all?lang=tw"):
        try:
            r = s.get(url, headers=UA, timeout=40)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                data = data.get("retVal") or data.get("data") or []
            rows = []
            for d in data:
                city = str(d.get("city_tw") or d.get("city") or "")
                if CITY_ZH not in city and "台中" not in city:
                    continue
                rows.append(dict(
                    station_id=f"YB_{d.get('station_no') or d.get('sno')}",
                    name=d.get("name_tw") or d.get("sna") or "",
                    district=d.get("district_tw") or d.get("sarea") or "",
                    address=d.get("address_tw") or d.get("ar") or "",
                    lat=_float(d.get("lat")), lng=_float(d.get("lng")),
                    capacity=_int(d.get("parking_spaces") or d.get("tot")),
                    rent=_int(d.get("available_spaces") or d.get("sbi")),
                    ret=_int(d.get("empty_spaces") or d.get("bemp")),
                    active=1 if str(d.get("status", 1)) in ("1", "true", "True") else 0,
                    service_type=str(d.get("type") or "YouBike2.0"),
                    src_time=_norm_time(d.get("updated_at") or d.get("mday"))))
            if rows:
                return rows
            last = f"{url}: 0 筆台中站點"
        except Exception as e:                                   # noqa: BLE001
            last = f"{url}: {e}"
    raise RuntimeError(last or "youbike 無資料")


def fetch_tdx(s: requests.Session) -> list[dict]:
    """TDX v2。需要 TDX_CLIENT_ID / TDX_CLIENT_SECRET 環境變數。"""
    cid, sec = os.getenv("TDX_CLIENT_ID"), os.getenv("TDX_CLIENT_SECRET")
    if not (cid and sec):
        raise RuntimeError("未設定 TDX_CLIENT_ID / TDX_CLIENT_SECRET")
    tk = s.post("https://tdx.transportdata.tw/auth/realms/TDXConnect/"
                "protocol/openid-connect/token",
                data={"grant_type": "client_credentials",
                      "client_id": cid, "client_secret": sec},
                headers=UA, timeout=40)
    tk.raise_for_status()
    h = dict(UA, authorization=f"Bearer {tk.json()['access_token']}")
    base = "https://tdx.transportdata.tw/api/basic/v2/Bike"
    st = s.get(f"{base}/Station/City/{CITY}?%24format=JSON", headers=h, timeout=90)
    st.raise_for_status()
    av = s.get(f"{base}/Availability/City/{CITY}?%24format=JSON", headers=h, timeout=90)
    av.raise_for_status()

    def unwrap(j):
        if isinstance(j, dict):
            return j.get("BikeStations") or j.get("BikeAvailabilities") or []
        return j

    meta = {}
    for d in unwrap(st.json()):
        pos = d.get("StationPosition") or {}
        addr = d.get("StationAddress")
        addr = addr.get("Zh_tw", "") if isinstance(addr, dict) else str(addr or "")
        meta[d.get("StationUID")] = dict(
            name=(d.get("StationName") or {}).get("Zh_tw", ""),
            district=addr[:3], address=addr,
            lat=_float(pos.get("PositionLat")), lng=_float(pos.get("PositionLon")),
            capacity=_int(d.get("BikesCapacity")),
            service_type=str(d.get("ServiceType", "")))
    rows = []
    for d in unwrap(av.json()):
        m = meta.get(d.get("StationUID"), {})
        rent, ret = _int(d.get("AvailableRentBikes")), _int(d.get("AvailableReturnBikes"))
        rows.append(dict(
            station_id=d.get("StationUID"), name=m.get("name", ""),
            district=m.get("district", ""), address=m.get("address", ""),
            lat=m.get("lat"), lng=m.get("lng"),
            capacity=m.get("capacity") or ((rent or 0) + (ret or 0)),
            rent=rent, ret=ret,
            active=1 if _int(d.get("ServiceStatus")) == 1 else 0,
            service_type=m.get("service_type", ""),
            src_time=_norm_time(d.get("SrcUpdateTime") or d.get("UpdateTime"))))
    if not rows:
        raise RuntimeError("tdx 回傳 0 筆")
    return rows


SOURCES = {"govopen": fetch_govopen, "youbike": fetch_youbike, "tdx": fetch_tdx}
ORDER = ["govopen", "youbike", "tdx"]


# --------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=list(SOURCES))
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--attempts", type=int, default=3)
    a = ap.parse_args()

    s = requests.Session()

    if a.probe:
        bad = 0
        for nm in ORDER:
            t0 = time.time()
            try:
                rows = SOURCES[nm](s)
                print(f"OK   {nm:8s} {len(rows):5d} 站  "
                      f"{sum(1 for r in rows if r['lat']):5d} 有座標  "
                      f"{time.time()-t0:4.1f}s  例：{rows[0]['name']}")
            except Exception as e:                               # noqa: BLE001
                bad += 1
                print(f"FAIL {nm:8s} {e}")
        return 0 if bad < len(ORDER) else 1

    names = [a.source] if a.source else ORDER
    errs = []
    for attempt in range(a.attempts):
        if attempt:
            time.sleep(15)
        for nm in names:
            try:
                rows = SOURCES[nm](s)
            except Exception as e:                               # noqa: BLE001
                errs.append(f"{nm}: {e}")
                continue
            now = datetime.now(TPE)
            d = os.path.join(a.outdir, now.strftime("%Y-%m-%d"))
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, now.strftime("%H%M%S") + f"_{nm}.csv.gz")
            with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            print(f"{now:%Y-%m-%d %H:%M:%S} {nm}: {len(rows)} 站 -> {path} "
                  f"({os.path.getsize(path)/1024:.0f} KB)")
            return 0
    print("全部來源失敗：" + " | ".join(errs), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
