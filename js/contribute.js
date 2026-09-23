// contribute.js — 走行中に撮った写真をバックエンド(server/app.py)に送る。
// serve.py(静的配信のみ)ではこの機能は使えない(server/app.pyで起動している場合のみ動作)。
const CITY = "金沢市";
const DEVICE_ID_KEY = "bikeNavDeviceId";

const btn = document.getElementById("contributeBtn");
const fileInput = document.getElementById("contributeFile");
const toastEl = document.getElementById("toast");

if (btn && fileInput) {
  btn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", onFileSelected);
}

function getDeviceId() {
  let id = localStorage.getItem(DEVICE_ID_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(DEVICE_ID_KEY, id);
  }
  return id;
}

async function onFileSelected() {
  const file = fileInput.files[0];
  fileInput.value = ""; // 同じファイルを選び直せるようにリセット
  if (!file) return;

  toast("現在地を取得中…", 2000);
  let pos;
  try {
    pos = await getPosition();
  } catch {
    toast("現在地が取得できないため送信できませんでした");
    return;
  }

  const form = new FormData();
  form.append("image", file);
  form.append("lat", pos.coords.latitude);
  form.append("lon", pos.coords.longitude);
  form.append("city", CITY);
  form.append("deviceId", getDeviceId());
  if (pos.coords.heading != null && !Number.isNaN(pos.coords.heading)) {
    form.append("compassAngle", pos.coords.heading);
  }

  toast("送信中…", 3000);
  try {
    const res = await fetch("/api/photos", { method: "POST", body: form });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      toast(body.error || "送信に失敗しました");
      return;
    }
    toast(`ありがとうございます！区間 ${body.edgeId} に登録しました`, 4000);
  } catch {
    toast("送信に失敗しました（サーバーに接続できません）");
  }
}

function getPosition() {
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      timeout: 8000,
    });
  });
}

function toast(msg, ms = 3000) {
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (toastEl.hidden = true), ms);
}
