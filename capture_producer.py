import os
import time
import datetime
import pyautogui
import keyboard

INTERVAL = 3
SAVE_DIR = "input_images"
os.makedirs(SAVE_DIR, exist_ok=True)

def capture_screen():
    now = datetime.datetime.now()
    filename = now.strftime("%Y%m%d_%H%M%S_%f")[:19] + ".png"
    filepath = os.path.join(SAVE_DIR, filename)
    screenshot = pyautogui.screenshot()
    screenshot.save(filepath)
    print(f"[Producer] Captured: {filepath}")

def main():
    print(f"--- キャプチャプロデューサー起動 ---")
    print(f"{INTERVAL}秒ごとに画面をキャプチャして {SAVE_DIR}/ に保存します。")
    print("手動でキャプチャしたい場合は 'ctrl+space' を押してください。")
    
    keyboard.add_hotkey('ctrl+space', capture_screen)
    
    try:
        while True:
            capture_screen()
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\n[Producer] キャプチャを終了します。")

if __name__ == "__main__":
    main()
