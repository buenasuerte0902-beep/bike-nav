import os
import time
import shutil
import datetime
import threading
import pandas as pd
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

WATCH_DIR = "input_images"
PROCESSED_DIR = "processed_images"
OUTPUT_DIR = "output"
RESULTS_FILE = os.path.join(OUTPUT_DIR, "results.csv")

for d in [WATCH_DIR, PROCESSED_DIR, OUTPUT_DIR]:
    os.makedirs(d, exist_ok=True)

if not os.path.exists(RESULTS_FILE):
    df = pd.DataFrame(columns=["timestamp", "filename", "shoulder_width_score", "has_white_line", "step_risk"])
    df.to_csv(RESULTS_FILE, index=False)

def run_inference(filepath):
    import random
    print(f"[Consumer] 推論実行中: {filepath}")
    shoulder_width_score = round(random.uniform(0.0, 1.0), 2)
    has_white_line = random.choice([0, 1])
    step_risk = round(random.uniform(0.0, 1.0), 2)
    return shoulder_width_score, has_white_line, step_risk

class ImageHandler(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        # 起動時の取り込みと監視イベントで同じ画像を二重に処理しないための記録
        self._lock = threading.Lock()
        self._seen = set()

    def on_created(self, event):
        if event.is_directory or not event.src_path.lower().endswith(".png"):
            return
            
        filepath = event.src_path
        filename = os.path.basename(filepath)
        with self._lock:
            if filename in self._seen:
                return
            self._seen.add(filename)
        time.sleep(0.5) 
        
        try:
            shoulder_width_score, has_white_line, step_risk = run_inference(filepath)
            
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_data = pd.DataFrame([{
                "timestamp": now_str,
                "filename": filename,
                "shoulder_width_score": shoulder_width_score,
                "has_white_line": has_white_line,
                "step_risk": step_risk
            }])
            new_data.to_csv(RESULTS_FILE, mode='a', header=False, index=False)
            print(f"[Consumer] 結果を保存しました: {filename}")
            
            dest_path = os.path.join(PROCESSED_DIR, filename)
            shutil.move(filepath, dest_path)
            
        except Exception as e:
            print(f"[Consumer] エラー発生 ({filename}): {e}")

def main():
    print("--- 推論コンシューマー起動 ---")
    print(f"{WATCH_DIR}/ フォルダの監視を開始します。")
    
    event_handler = ImageHandler()
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

if __name__ == "__main__":
    main()
