import multiprocessing
import os
import signal
import sys
import time

# 出力先がファイルやパイプのとき、Windows の既定の文字コード（cp1252 など）では
# 日本語を表示できずに落ちるので UTF-8 にそろえる（子プロセスでもこの行が実行される）
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

# exe化（PyInstaller）した場合は exe の置き場所を作業フォルダにする。
# input_images / processed_images / output は exe の横に作られる。
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))


def run_producer():
    import capture_producer
    capture_producer.main()


def run_consumer():
    import inference_consumer
    inference_consumer.main()


def main():
    print("========================================")
    print("  自転車ナビ用 路肩判定システム 起動")
    print("  (キャプチャ＆推論パイプライン)")
    print("  終了する場合は Ctrl+C を押してください")
    print("========================================")

    # 子プロセスとして起動するので、Producer と Consumer は別プロセスのまま分離される。
    # 1つの exe でも動くよう、スクリプトのパスではなく関数を起動する。
    producer_process = multiprocessing.Process(target=run_producer, name="producer")
    consumer_process = multiprocessing.Process(target=run_consumer, name="consumer")
    producer_process.start()
    consumer_process.start()

    # Ctrl+C は例外ではなくフラグで受け取り、終了処理が途中で中断されないようにする
    # （子プロセスを起動した後に設定するので、子プロセス側の Ctrl+C 処理には影響しない）
    stop_requested = []
    signal.signal(signal.SIGINT, lambda signum, frame: stop_requested.append(signum))

    try:
        while not stop_requested:
            time.sleep(1)
            if not producer_process.is_alive() or not consumer_process.is_alive():
                print("子プロセスが終了しました。")
                break
        if stop_requested:
            print("\n--- 終了シグナルを受信しました ---")

    finally:
        print("プロセスを安全に終了します...")
        producer_process.terminate()
        consumer_process.terminate()
        producer_process.join()
        consumer_process.join()
        print("システムを終了しました。")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
