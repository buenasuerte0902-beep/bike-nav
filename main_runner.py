import subprocess
import sys
import time

def main():
    print("========================================")
    print("  自転車ナビ用 路肩判定システム 起動")
    print("  (キャプチャ＆推論パイプライン)")
    print("  終了する場合は Ctrl+C を押してください")
    print("========================================")
    
    producer_process = subprocess.Popen([sys.executable, "capture_producer.py"])
    consumer_process = subprocess.Popen([sys.executable, "inference_consumer.py"])
    
    try:
        while True:
            time.sleep(1)
            if producer_process.poll() is not None or consumer_process.poll() is not None:
                print("子プロセスが終了しました。")
                break
    except KeyboardInterrupt:
        print("\n--- 終了シグナルを受信しました ---")
        
    finally:
        print("プロセスを安全に終了します...")
        producer_process.terminate()
        consumer_process.terminate()
        producer_process.wait()
        consumer_process.wait()
        print("システムを終了しました。")

if __name__ == "__main__":
    main()
