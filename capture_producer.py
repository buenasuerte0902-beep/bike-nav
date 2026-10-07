import os
import time
import datetime
import json

from app_config import load_config

SAVE_DIR = "input_images"
os.makedirs(SAVE_DIR, exist_ok=True)

# 書き込み途中の画像を Consumer が読まないよう、".part" で書いてから名前を変える。
# 位置などの付随情報は画像と同じ名前の .json に置き、画像より先に書く。
PART_SUFFIX = ".part"


def publish_image(filename, write_image, meta):
    filepath = os.path.join(SAVE_DIR, filename)
    meta_path = os.path.splitext(filepath)[0] + ".json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    write_image(filepath + PART_SUFFIX)
    os.replace(filepath + PART_SUFFIX, filepath)
    return filepath


def capture_screen():
    import pyautogui

    now = datetime.datetime.now()
    filename = now.strftime("%Y%m%d_%H%M%S_%f")[:19] + ".png"
    screenshot = pyautogui.screenshot()
    filepath = publish_image(
        filename,
        lambda path: screenshot.save(path, format="PNG"),
        {"source": "screen", "captured_at": now.isoformat(timespec="seconds")},
    )
    print(f"[Producer] Captured: {filepath}")


def run_screen(cfg):
    import keyboard

    interval = cfg["interval_sec"]
    hotkey = cfg["hotkey"]
    print("--- キャプチャプロデューサー起動（画面キャプチャ） ---")
    if cfg["auto"]:
        print(f"{interval}秒ごとに画面をキャプチャして {SAVE_DIR}/ に保存します。")
    print(f"手動でキャプチャしたい場合は '{hotkey}' を押してください。")

    keyboard.add_hotkey(hotkey, capture_screen)

    try:
        while True:
            if cfg["auto"]:
                capture_screen()
                time.sleep(interval)
            else:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Producer] キャプチャを終了します。")


def main():
    config = load_config()
    source = config["source"]
    if source == "screen":
        run_screen(config["screen"])
    elif source == "mapillary":
        import mapillary_source

        mapillary_source.run(config["mapillary"], publish_image)
    elif source == "googlemaps":
        import googlemaps_source

        googlemaps_source.run(config["googlemaps"], publish_image)
    else:
        raise SystemExit(f"[Producer] config.json の source が不明です: {source}（screen / mapillary / googlemaps）")


if __name__ == "__main__":
    main()
