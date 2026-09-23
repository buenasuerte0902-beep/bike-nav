# 自転車ナビ

自転車で「走りやすい道」を優先して案内するPWA。単純な最短経路ではなく、
路肩の広さ・自転車通行帯・路面の滑らかさ・交通量・アップダウン・信号の多さの
6軸をS〜Dでランク付けし、それを加味した経路を提示する。

自動車のカーブ運転支援アプリ(`F:\運転支援アプリ`)とは別プロジェクト。

## 構成

バニラJS + ESモジュールのPWA。ビルド無し（curve-assistと同じ方針）。

- `index.html` / `js/{graph,route,geocode,map,app}.js` / `css/app.css` / `sw.js`
- `vendor/leaflet` — 同梱（オフラインPWAのためCDN不使用）
- `tools/build_graph.py` — OSMから道路網を取得しランク付けしてグラフJSONを書き出す
- `tools/fetch_mapillary_images.py` — Mapillaryから学習用の道路写真を収集する
- `tools/ml/{detect_vehicles,estimate_shoulder_width,segment_road,equirect,imutil,label_smoothness,train_smoothness,apply_ml_scores}.py`
  — Phase4(写真ベースのML、詳細は下記)
- `tools/label_via_streetview.py` — Street Viewを見た手動判定でラベル付け（自動取得はしない、詳細は下記）
- `tools/fetch_traffic_census.py` / `tools/apply_traffic_census.py` — 国交省・道路交通センサスの
  実測交通量を取得しグラフに反映（詳細は下記）
- `tools/ml/{aerial,apply_aerial_width}.py` — 国土地理院の航空写真から道路脇の舗装帯幅を推定
  （詳細は下記）
- `data/graph/<市区町村>.json` — アプリが読む道路グラフ（`build_graph.py`の出力）
- `data/photos/<市区町村>/` — Mapillaryから収集した写真（gitignore対象。Phase4のML学習材料）
- `data/census/<市区町村>.geojson` — 道路交通センサスの実測値（gitignore対象外、公式オープンデータ）
- `data/aerial_tiles/` — 国土地理院航空写真タイルのキャッシュ（gitignore対象、再DL可能）
- `models/` — YOLOv8事前学習重み・滑らかさ分類モデル（gitignore対象、再生成/再DL可能）
- `server/{app,db,geo_match}.py` — ユーザー投稿バックエンド（Flask、詳細は下記）

## 起動

```
python serve.py                 # http://127.0.0.1:8778  静的配信のみ、写真投稿機能は動かない
python serve.py --tls --lan     # https://<LAN-IP>:8444 （スマホのGPSテスト用）
```

`.claude/launch.json` の `bike-nav` 設定でも起動できる。ユーザー投稿機能を使う場合は
下記の「ユーザー投稿バックエンド」の `server/app.py` を使うこと。

## 道路グラフの作り方

```
python tools/build_graph.py --city 金沢市              # 標高込み(勾配ランクあり)
python tools/build_graph.py --city 金沢市 --no-elevation  # 標高APIを叩かず高速に試す
```

Overpass APIとOpen-Elevation APIを叩く。Open-Elevationの公開デモサーバーは
不安定・低速なことがあるため、`--no-elevation` でまず動作確認してから
時間のあるときに標高込みで再生成するのがおすすめ。標高キャッシュは
`data/graph/_elevation_cache.json` に保存され再実行時に再利用される。

## 評価ロジックの設計判断（重要）

6軸のうち4軸は **写真もMLも使わず OSMタグ＋標高から直接算出**している:

| 軸 | データ源 | 実装 |
|---|---|---|
| 自転車通行帯 | `cycleway=*` / `highway=cycleway` | `grade_bike_lane()` |
| 交通量（近似） | 道路種別 + `lanes` + `maxspeed` | `grade_traffic()` |
| 信号の多さ | `highway=traffic_signals` ノード密度 | `grade_signals()` |
| アップダウン | 国土地理院/Open-Elevationの標高差→勾配% | `grade_slope()` |

（いずれも `tools/build_graph.py`）

残り2軸は写真ベースの評価が必要。グラフJSONの `grades.shoulderWidth` / `grades.smoothness`
は `tools/build_graph.py` の時点では `null`。アプリの総合ランク計算
(`js/route.js summarize()` / 各Pythonツールの `composite()`)は `null`の軸を平均から
除外して算出するため、Phase4でMLの結果を追記すれば自動的に反映される
（フロントエンドのコード変更は不要）。

## Phase4: 写真ベースのML（`tools/ml/`）

このPC(RTX 3080 / CUDA)で動作確認済み。実行順は以下の通り:

```
python tools/fetch_mapillary_images.py --city 金沢市        # 1. 写真収集(要トークン)
python tools/ml/label_smoothness.py --city 金沢市           # 2. 滑らかさを手動ラベリング(GUI操作)
python tools/ml/train_smoothness.py --city 金沢市           # 3. 分類モデルをfine-tuning
python tools/ml/apply_ml_scores.py --city 金沢市            # 4. 推定結果をグラフJSONに反映
```

- **路肩の広さ** (`tools/ml/estimate_shoulder_width.py`, 追加学習不要): 写真内の車を
  事前学習済みYOLOv8(COCO)のcar/bus/truckクラスで検出し、車種ごとの既知の実幅
  (約1.75〜2.5m)を「物差し」にして pixels/meter を求める。路面の判定は
  `tools/ml/segment_road.py`（Cityscapes事前学習のSegFormer-b0）で画像全体を
  road/sidewalk/vegetation等にクラス分けし、車の脇からroadクラスが続く画素数を
  実寸に変換する（**色の閾値判定ではない**。最初は単純な色スキャンで実装したが、
  実写真5枚で検証したところ影・反射・水たまりで頻繁に破綻したため、意味的な
  セグメンテーション方式に置き換えた）。幾何ロジックの単体テストあり
  (`python tools/ml/estimate_shoulder_width.py --selftest`、合成ラベルマップで検証、
  SegFormerモデルは呼ばない)。
  **既知の限界**: 車と同じ奥行きに路肩があるという前提／Cityscapesの標準19クラスには
  専用の"parking"クラスが無いため駐車場や広場も road と分類されがちで過大評価しうる
  （実写真1枚で確認済み）／進行方向に対しどちら側が路肩かは確定できないため
  左右の大きい方を採用。
- **Mapillary写真の実体は360度パノラマ**: equirectangular(横:縦=2:1)形式が多く、
  `tools/ml/equirect.py`で撮影方位(compass_angle)の前後左右に透視投影してから
  車検出・路肩幅推定に使う(`estimate_shoulder_width.py`)。撮影車両自身のボンネットが
  「車」として誤検出され物差しが狂うケースも実写真で見つかったため、画像幅の45%を
  超える検出は除外している。
- **Windows特有の注意**: `cv2.imread`/`imwrite`は`金沢市`のような非ASCIIパスを
  読み書きできない既知の不具合があるため、`tools/ml/imutil.py`の
  `imread`/`imwrite`（`np.fromfile`/`tofile`経由）を必ず使うこと。
- **路面の滑らかさ** (`tools/ml/train_smoothness.py`, 要ラベリング): 事前学習済み
  MobileNetV3-Small(ImageNet)の最終層をS〜Dの5クラスに差し替えてfine-tuning。
  学習ループ自体は合成データで動作確認済み(`python tools/ml/train_smoothness.py --selftest`)。
  実際の精度は集めた写真の枚数とラベリング量に依存するため未検証。

## 交通量の実測データ（道路交通センサス, `tools/fetch_traffic_census.py` / `apply_traffic_census.py`）

「交通量」軸は当初OSMタグからの粗い近似だったが、国土交通省「令和3年度 一般交通量調査
（道路交通センサス）」の公開Web地図が使っている静的GeoJSONタイル(認証不要・商用可能な
政府オープンデータ)から実測値(24時間交通量, 台/日)を取得して置き換えられるようにした。

```
python tools/fetch_traffic_census.py --city 金沢市    # data/census/金沢市.geojson に保存
python tools/apply_traffic_census.py --city 金沢市    # グラフJSONのgrades.trafficを実測値で上書き
```

金沢市で実行したところ508区間の実測データが取得でき、47,704 edge中5,230件(11%)に反映できた
(調査対象は主要道路のみで住宅街の細い道までは無く、そこはOSM由来のヒューリスティックのまま)。
**発見**: OSMで`highway=unclassified`(格下扱い)と分類されている道路が実測24,800台/日超という
非常に交通量の多い道路だったケースがあり、日本のOSM道路分類の粗さをうまく補正できることを確認。
一致判定は道路線までの距離40m以内。データ源: https://www.mlit.go.jp/road/ir/ir-data/census_visualizationR3/

## 航空写真からの道路幅推定（`tools/ml/aerial.py` / `apply_aerial_width.py`）

Mapillaryの道路網羅率が実測50%程度だった問題を受けて追加。国土地理院「シームレスフォト」
(航空写真タイル, `https://maps.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg`, 出典明示のみで
商用利用可・全国を面でカバー)を使い、OSM由来の道路中心線に垂直な方向へ色を走査して
舗装帯(路肩+歩道相当の合計幅)を測る。Street Viewの「車を物差しにする」方式と違い、
道路の位置が既知(検出不要)・真上からなので遠近法の歪みが無い、という利点がある。

```
python tools/ml/apply_aerial_width.py --city 金沢市 --limit 50   # 動作確認
python tools/ml/apply_aerial_width.py --city 金沢市              # 全区間
python tools/ml/apply_aerial_width.py --city 金沢市 --resume     # 中断から再開
```

**金沢市全47,704区間で実行した結果**: 信頼できる値が得られたのは4,762区間(約10%)。
残りは「隣接する駐車場・広場等の似た色の舗装に扫き込まれて走査上限まで達した」ため
未計測(null)扱いにした(下記の経緯参照)。ランクの分布はD 41%/C 21%/B 21%/A 14%/S 4%と
偏りなく分散しており、測れた分については実用的な精度と考えられる。

**開発中に見つけた問題と対処**:
- 色の閾値判定は単純すぎて、道路種別ごとの走査上限(片側)近くまで達する誤検出が
  58〜92%(道路種別による)発生した。JPEG圧縮ノイズ対策としてパッチ平均(`Mosaic.sample`)＋
  適応的な基準色(EMA)に変更した上で、**走査上限の90%以上に達したら「未計測」として
  捨てる**ようにした(上記の10%という保守的なカバレッジはこの誠実さの結果)。
- タイル取得は同一IPからの並列化(`ThreadPoolExecutor`, 8並列。別IPを使った
  レート制限回避などはしていない)＋キャッシュヒット時は待機なしに変更し、
  47,704区間の処理が完走できる速度になった(タイルキャッシュが埋まるにつれ
  0.6件/秒→3.4件/秒まで加速)。
- **グラフJSONへの書き込みを全て一時ファイル+`os.replace`のアトミック書き込みに変更**
  (`build_graph.py`含む全ツール共通)。処理を`kill`で中断した際にJSONファイルが
  中途半端な状態で壊れる事故が実際に発生したため(修正前は`json.dump`を直接
  対象ファイルに書いていた)。

## 手動ラベリング（Street Viewを見て自分で判定する, `tools/label_via_streetview.py`）

Mapillaryのカバレッジが薄い区間や、トークン待ちの間に少数だけ先に埋めたい場合の代替手段。
**Street View画像の自動取得・保存は一切行わない** — 表示するのは普通のGoogle Maps URL
（ブラウザで自分で開く）だけで、保存するのはあなたが見て入力したS〜Dの文字だけ。
Googleの規約が禁止しているのは「コンテンツの自動取得・保存・インデックス化」であり、
人が自分の目で見て判断した結果をメモする行為そのものは制限されていない。

```
python tools/label_via_streetview.py --city 金沢市 --sample 30   # ランダム30区間を判定
python tools/label_via_streetview.py --city 金沢市 --merge       # 判定結果をグラフJSONに反映
```

都市全体(金沢市で47,704区間)を手作業で埋めるのは現実的ではないため、主要ルートの
スポットチェックや、ML(Phase4)の推定結果が妥当か目視検証する用途を想定。
`data/manual_labels/<city>.json` に保存され、`--merge` でグラフJSONの
`grades.shoulderWidth` / `grades.smoothness` に反映される（`tools/ml/apply_ml_scores.py`
のML推定結果を、後から手動判定で上書きすることも可能）。

## ユーザー投稿バックエンド（`server/`）

走行中に撮った写真をアプリから直接送れる機能。**Mapillaryには公開投稿せず、
自社のSQLite DBとファイルシステムに蓄積する**（商用化を見据え、データを自社資産にする
ため。Mapillary利用規約第12項の学習・データセット開発目的の商用利用は満たしつつ、
実行時にMapillaryサービス自体を呼ばない設計を維持）。

```
pip install flask   # 初回のみ
python server/app.py                # http://127.0.0.1:8779 (PC確認用、静的配信+API両方を兼ねる)
python server/app.py --tls --lan    # https://<LAN-IP>:8779 (スマホ実機テスト用)
```

**スマホ実機でカメラ・現在地を試すには`--tls --lan`が必須**（`serve.py --tls --lan`と同じ理由:
ブラウザはHTTPSでないとカメラ/位置情報を許可しない）。初回は`.certs/`に自己署名証明書を
自動生成する(`serve.py`と共有)。スマホでは証明書の警告が出るので「詳細設定」→
「アクセスする」で進む。`.claude/launch.json`の`bike-nav-server-tls`設定でも起動できる。
動作確認: `curl -sk https://<LAN-IP>:8779/`でPCから疎通確認してからスマホで開くとよい。

- `POST /api/photos` — multipart(`image`, `lat`, `lon`, `city`, `deviceId`, `compassAngle`?)。
  `server/geo_match.py`(`tools/apply_traffic_census.py`と同じgrid+shapely最近傍探索)で
  最寄りのグラフedgeを特定し、`data/photos/<city>/<edgeId>.jpg`に保存（Mapillary写真が
  既にあれば上書き=常に自社の実写真を優先）。`data/app.db`(SQLite)にメタデータを記録。
  これにより`tools/ml/apply_ml_scores.py`は無改修でユーザー投稿写真も処理できる。
- `GET /api/photos/coverage?city=` — 投稿状況の件数を返す（管理画面用の最小実装）
- フロントは`js/contribute.js`（📷ボタン→ファイル選択→現在地取得→アップロード）
- **今回作っていないもの**（MVPスコープ外、将来必要になったら追加）: 本人認証
  （匿名deviceId方式のみ）、通報・モデレーション、レート制限・スパム対策、
  画像の不適切コンテンツ検知。公開前に検討すること。
- **DB/ストレージはSQLite+ローカルファイルシステムのMVP構成**。実際に商用スケールする
  際はPostgres+S3等のオブジェクトストレージへの移行が自然なステップ（今は作り込まない）。

## ルーティング

`js/route.js` がA*探索。コスト = 距離 × ランク別ペナルティ（S=1倍 〜 D=3.2倍）＋
曲がり角ペナルティ＋上り坂ペナルティ。「最短」⇄「走りやすさ優先」はランク別ペナルティを
掛けるかどうかの切り替え(曲がり角・上り坂の扱いは両モード共通)。

- **曲がり角ペナルティ**: ランクだけで最適化すると、細切れの良路面区間を繋いでジグザグに
  なりやすい問題があった。`js/graph.js`で各edgeの出入り口の方位を1度だけ計算しておき、
  交差点で前のedgeの到着方位と次のedgeの出発方位の差(0-180度)を求め、60度以上の曲がりに
  段階的な追加コストを課す(`js/route.js turnPenaltyM`)。金沢駅↔兼六園間で検証したところ、
  120度以上(ほぼ逆走)の曲がりは0件だった。
- **上り坂ペナルティ(方向依存)**: 従来は`grades.slope`(向きを無視した起伏の激しさ)だけを
  見ており、下り坂にも上り坂と同じペナルティを課す誤りがあった。`tools/build_graph.py`が
  `slopePct`(符号付き, from→to方向)をedgeに保存し、`js/route.js`が実際の進行方向
  (`js/graph.js buildAdjacency`の`forward`フラグ)に応じて符号を反転、上り方向の時だけ
  勾配に比例した追加コストを課す(下りは既存のランク別ペナルティ以上には課さない)。

## 既知の限界

- リアルタイム追従ナビ（音声案内・自動再ルート）は未実装。今回はルート検索と
  地図表示まで（curve-assistのような走行中ナビ機能は将来拡張）
- 「交通量」は道路交通センサスの実測値がある主要道路(現状11%)はそれを使うが、
  住宅街の細い道など調査対象外の区間はOSMタグからの近似のまま
- Mapillaryの道路網羅率は実測で50%程度（半径165m以内に画像が無い区間が約半分、
  見つかっても中央値106m離れている）。「路肩の広さ」軸は航空写真ベースの推定
  (`tools/ml/apply_aerial_width.py`)に置き換えたが、こちらも誤検出を誠実に弾いた結果
  信頼できる値は全区間の約10%に留まる(金沢市で実測)。「路面の滑らかさ」軸は写真が
  無いと判定できないため引き続きMapillary頼み。商用化を見据え、ユーザーが走行中に
  撮った写真は（Mapillaryへの公開投稿ではなく）自社バックエンド(`server/`)に
  蓄積して自社資産にする方針にした（詳細は下記「ユーザー投稿バックエンド」）。
  Mapillary自体の利用（学習・データセット開発目的）は利用規約第12項で商用利用も
  許可されているが、リアルタイムのターンバイターン誘導にMapillaryサービス自体を
  直結させることは禁止されているため、本アプリのように「オフラインで一度スコア化し、
  実行時は自前データのみを見る」設計を維持すること
- **Flaskサーバー(`server/app.py`)経由だとService Workerが登録できない現象を確認**
  (`serve.py`(stdlib http.server)経由では問題なく登録できる)。レスポンスヘッダを
  `serve.py`とほぼ同一(HTTP/1.0, 最小限のヘッダ)に揃えても再現したため、原因は
  Werkzeug開発用サーバー側の何からしいが未特定。実機のブラウザで再検証すること
  (この開発環境固有の制約の可能性がある)。アップロードAPI自体はService Workerの
  登録有無に関係なく正常に動作する(curl・ブラウザ双方でエンドツーエンド確認済み)。
- 地図タイルはオフライン非対応（`data/graph/`のデータ自体はService Workerで
  自動キャッシュされ、オフラインでもルート計算はできる）
- 対応エリアは今のところ金沢市のみ（`tools/build_graph.py --city <市区町村名>`で
  他エリアも生成できるが、`js/app.js`の`CITY`定数を変更する必要あり）
- `nearestNode()`は全ノード総当たりの線形探索（都市規模なら実用上問題ない速さだが、
  複数都市を1グラフに束ねる場合は空間インデックスが必要になる）
