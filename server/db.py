"""
ユーザー投稿写真のメタデータを持つSQLite DB。
写真本体は data/photos/<city>/<edgeId>.jpg に保存し、DBには出典・位置・投稿者だけ持つ。
"""
import os
import sqlite3
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.normpath(os.path.join(HERE, "..", "data", "app.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    edge_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    source TEXT NOT NULL,              -- "user" | "mapillary"
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    distance_m REAL,                   -- edgeまでのスナップ距離
    compass_angle REAL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_photos_city_edge ON photos(city, edge_id);
CREATE INDEX IF NOT EXISTS idx_photos_device ON photos(device_id);
"""


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_photo(conn, *, city, edge_id, device_id, source, lat, lon, distance_m, compass_angle):
    row_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO photos (id, city, edge_id, device_id, source, lat, lon, distance_m, compass_angle, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row_id, city, edge_id, device_id, source, lat, lon, distance_m, compass_angle, int(time.time())),
    )
    conn.commit()
    return row_id


def coverage_by_city(conn, city):
    cur = conn.execute(
        "SELECT source, COUNT(*) AS n, COUNT(DISTINCT edge_id) AS edges FROM photos WHERE city = ? GROUP BY source",
        (city,),
    )
    return [dict(r) for r in cur.fetchall()]
