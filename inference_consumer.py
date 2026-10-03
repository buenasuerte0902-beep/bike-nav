import os
import csv
import json
import time
import shutil
import sqlite3
import datetime
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

WATCH_DIR = "input_images"
PROCESSED_DIR = "processed_images"
OUTPUT_DIR = "output"
RESULTS_FILE = os.path.join(OUTPUT_DIR, "results.csv")
RESULTS_DB = os.path.join(OUTPUT_DIR, "results.db")
IMAGE_EXTS = (".png", ".jpg", ".jpeg")

COLUMNS = [
    "timestamp", "filename", "shoulder_width_score", "has_white_line", "step_risk",
    "source", "source_id", "lat", "lon", "captured_at",
]

for d in [WATCH_DIR, PROCESSED_DIR, OUTPUT_DIR]:
    os.makedirs(d, exist_ok=True)


def prepare_csv():
    # 列が変わる前の results.csv は消さずに別名で残し、新しい列で作り直す
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, encoding="utf-8", newline="") as f:
            header = next(csv.reader(f), [])
        if header == COLUMNS:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.replace(RESULTS_FILE, os.path.join(OUTPUT_DIR, f"results_old_{stamp}.csv"))
    with open(RESULTS_FILE, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(COLUMNS)


def open_db():
    conn = sqlite3.connect(RESULTS_DB, check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, filename TEXT, shoulder_width_score REAL,
            has_white_line INTEGER, step_risk REAL,
            source TEXT, source_id TEXT, lat REAL, lon REAL, captured_at TEXT
        )"""
    )
    conn.commit()
    return conn


def run_inference(filepath):
    import random
    print(f"[Consumer] 推論実行中: {filepath}")
    shoulder_width_score = round(random.uniform(0.0, 1.0), 2)
    has_white_line = random.choice([0, 1])
    step_risk = round(random.uniform(0.0, 1.0), 2)
    return shoulder_width_score, has_white_line, step_risk


def read_meta(meta_path):
    if not os.path.exists(meta_path):
        return {}
    try:
        with open(meta_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class ImageHandler(FileSystemEventHandler):
    def __init__(self, conn):
        super().__init__()
        self._conn = conn
        # 起動時の取り込みと監視イベントで同じ画像を二重に処理しないための記録
        self._lock = threading.Lock()
        self._seen = set()

    def on_created(self, event):
        if not event.is_directory:
            self.process(event.src_path)

    # Producer は ".part" で書いてから名前を変えるので、完成した画像は移動イベントで届く
    def on_moved(self, event):
        if not event.is_directory:
            self.process(event.dest_path)

    def process(self, filepath):
        if not filepath.lower().endswith(IMAGE_EXTS):
            return
        filename = os.path.basename(filepath)
        with self._lock:
            if filename in self._seen:
                return
            self._seen.add(filename)
        time.sleep(0.5)

        try:
            shoulder_width_score, has_white_line, step_risk = run_inference(filepath)
            meta_path = os.path.splitext(filepath)[0] + ".json"
            meta = read_meta(meta_path)

            row = {
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "filename": filename,
                "shoulder_width_score": shoulder_width_score,
                "has_white_line": has_white_line,
                "step_risk": step_risk,
                "source": meta.get("source", ""),
                "source_id": meta.get("source_id", ""),
                "lat": meta.get("lat"),
                "lon": meta.get("lon"),
                "captured_at": meta.get("captured_at", ""),
            }
            with self._lock:
                with open(RESULTS_FILE, "a", encoding="utf-8", newline="") as f:
                    csv.DictWriter(f, fieldnames=COLUMNS).writerow(row)
                self._conn.execute(
                    f"INSERT INTO results ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                    [row[c] for c in COLUMNS],
                )
                self._conn.commit()
            print(f"[Consumer] 結果を保存しました: {filename}")

            shutil.move(filepath, os.path.join(PROCESSED_DIR, filename))
            if os.path.exists(meta_path):
                shutil.move(meta_path, os.path.join(PROCESSED_DIR, os.path.basename(meta_path)))

        except Exception as e:
            print(f"[Consumer] エラー発生 ({filename}): {e}")


def main():
    print("--- 推論コンシューマー起動 ---")
    print(f"{WATCH_DIR}/ フォルダの監視を開始します。")
    prepare_csv()
    conn = open_db()

    event_handler = ImageHandler(conn)
    observer = Observer()
    observer.schedule(event_handler, WATCH_DIR, recursive=False)
    observer.start()

    # 監視開始前から input_images/ に残っている画像も処理する
    # （前回の取り残しや、Consumer の起動より先に Producer が保存した画像）
    for name in sorted(os.listdir(WATCH_DIR)):
        event_handler.on_created(FileCreatedEvent(os.path.join(WATCH_DIR, name)))

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Consumer] 監視を終了します。")
        observer.stop()
    observer.join()
    conn.close()


if __name__ == "__main__":
    main()
