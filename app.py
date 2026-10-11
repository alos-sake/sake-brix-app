from datetime import datetime
import json
import os
import sqlite3
import statistics
import uuid
from zoneinfo import ZoneInfo
from google import genai
from google.genai import types
import pandas as pd
import requests
import streamlit as st

# ==========================================
# データベース初期設定（絶対パス管理 ＆ 自動マイグレーション・UUID対応）
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "sake_brix.db")
CACHE_FILE = os.path.join(BASE_DIR, "sakenowa_cache.json")


def init_db():
  conn = sqlite3.connect(DB_NAME)
  c = conn.cursor()
  c.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_uuid TEXT UNIQUE,
            measured_at TEXT,
            brand_name TEXT,
            brewery_name TEXT,
            by_year TEXT,
            manufacture_date TEXT,
            alcohol TEXT,
            rice TEXT,
            polish TEXT,
            sake_meter TEXT,
            acidity TEXT,
            amino TEXT,
            yeast TEXT,
            m1 REAL,
            m2 REAL,
            m3 REAL,
            m4 REAL,
            m5 REAL,
            m6 REAL,
            count INTEGER,
            mean REAL,
            max_val REAL,
            min_val REAL,
            range_val REAL,
            median REAL,
            stdev REAL,
            unit TEXT,
            device_info TEXT
        )
    """)
  # カラム追加のマイグレーション（UUIDがない古いDBへの対応）
  try:
    c.execute("ALTER TABLE measurements ADD COLUMN record_uuid TEXT;")
  except sqlite3.OperationalError:
    pass
  try:
    c.execute("ALTER TABLE measurements ADD COLUMN unit TEXT;")
  except sqlite3.OperationalError:
    pass

  # 既存レコードにUUIDがない場合は一意IDを割り振る
  c.execute("SELECT id FROM measurements WHERE record_uuid IS NULL OR record_uuid = '';")
  rows = c.fetchall()
  for r in rows:
    new_uuid = str(uuid.uuid4())
    c.execute("UPDATE measurements SET record_uuid = ? WHERE id = ?;", (new_uuid, r[0]))

  conn.commit()
  conn.close()


init_db()

st.set_page_config(
    page_title="日本酒 糖度測定＆総合スペック管理ツール", layout="wide"
)

st.title("🍶 日本酒 糖度測定＆総合スペック管理ツール")

# サイドバー：APIキー設定
st.sidebar.header("⚙️ 設定")
api_key = st.sidebar.text_input(
    "Gemini API Key",
    type="password",
    placeholder="AIzaSy...",
    help="スマホカメラによるAIラベル読み取りに使用します",
    key="sidebar_api_key",
)

# セッションステート初期化（入力値の確実に管理）
jst_now_init = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M")
default_states = {
    "brand_name": "",
    "brewery_name": "",
    "by_year": "",
    "manufacture_date": "",
    "alcohol": "",
    "rice": "",
    "polish": "",
    "sake_meter": "",
    "acidity": "",
    "amino": "",
    "yeast": "",
    "unit": "°Bx",
    "device_info": "",
    "last_selected_label": None,
    "m1": None,
    "m2": None,
    "m3": None,
    "m4": None,
    "m5": None,
    "m6": None,
    "measured_at": jst_now_init,
    "is_submitting": False,
}
for k, v in default_states.items():
  if k not in st.session_state:
    st.session_state[k] = v


# 1. APIキャッシュ取得（取得成功時刻とキャッシュ利用の厳密な区別）
@st.cache_data(ttl=3600)
def fetch_sake_data():
  brands = []
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
          " like Gecko) Chrome/120.0.0.0 Safari/537.36"
      )
  }
  api_success = False
  try:
    brands_res = requests.get(
        "https://muro.sakenowa.com/sakenowa-data/api/brands",
        headers=headers,
        timeout=5,
    )
    breweries_res = requests.get(
        "https://muro.sakenowa.com/sakenowa-data/api/breweries",
        headers=headers,
        timeout=5,
    )

    if brands_res.status_code == 200 and breweries_res.status_code == 200:
      b_json = brands_res.json()
      br_json = breweries_res.json()
      brewery_dict = {b["id"]: b["name"] for b in br_json.get("breweries", [])}

      for b in b_json.get("brands", []):
        brewery_name = brewery_dict.get(b["breweryId"], "不明")
        brands.append({
            "label": f"{b['name']} （蔵元: {brewery_name}）",
            "name": b["name"],
            "brewery": brewery_name,
        })

      cache_payload = {
          "api_success_at": datetime.now(ZoneInfo("Asia/Tokyo")).strftime(
              "%Y-%m-%d %H:%M:%S"
          ),
          "brands": brands,
      }
      with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_payload, f, ensure_ascii=False, indent=2)
      api_success = True
    else:
      raise Exception("HTTP Error")
  except Exception:
    pass

  # API取得できなかった場合は既存キャッシュを読み込む
  cached_time = "不明"
  if os.path.exists(CACHE_FILE):
    try:
      with open(CACHE_FILE, "r", encoding="utf-8") as f:
        p_data = json.load(f)
        if isinstance(p_data, dict):
          brands = p_data.get("brands", [])
          cached_time = p_data.get("api_success_at", "不明")
        elif isinstance(p_data, list):
          brands = p_data
    except Exception:
      pass

  return brands, api_success, cached_time


sake_list, api_connected_now, last_api_success_time = fetch_sake_data()

if api_connected_now:
  st.sidebar.success(
      f"🌐 外部API接続成功 (最新取得: {last_api_success_time})"
  )
else:
  st.sidebar.info(
      f"📴 外部API未接続 - キャッシュ利用 (最終成功時刻: {last_api_success_time})"
  )


# 2. 過去保存カスタム銘柄の統合
def get_user_saved_brands():
  try:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
            SELECT brand_name, brewery_name, alcohol, rice, polish, sake_meter, acidity, amino, yeast 
            FROM measurements 
            WHERE brand_name IS NOT NULL AND brand_name != ''
            ORDER BY id DESC
        """)
    rows = c.fetchall()
    conn.close()
    saved = []
    seen = set()
    for r in rows:
      b_name, br_name, alc, rice, polish, sm, ac, am, ye = r
      br_label = br_name if br_name else "マイデータ（限定・独自）"
      key = (b_name, br_label)
      if key not in seen:
        seen.add(key)
        saved.append({
            "label": f"★ {b_name} （蔵元: {br_label}）",
            "name": b_name,
            "brewery": br_name if br_name else "",
            "alcohol": alc if alc else "",
            "rice": rice if rice else "",
            "polish": polish if polish else "",
            "sake_meter": sm if sm else "",
            "acidity": ac if ac else "",
            "amino": am if am else "",
            "yeast": ye if ye else "",
        })
    return saved
  except Exception:
    return []


user_saved_list = get_user_saved_brands()
combined_sake_list = user_saved_list + sake_list
brand_labels = [s["label"] for s in combined_sake_list]

# ==========================================
# 画面タブ構成
# ==========================================
tab_new, tab_list, tab_compare, tab_backup = st.tabs([
    "📝 新規測定・入力",
    "📊 保存データ一覧・検索・編集",
    "⚖️ データ比較",
    "⚙️ バックアップ・復元",
])

# ------------------------------------------
# タブ1: 新規測定・入力
# ------------------------------------------
with tab_new:
  st.subheader("📸 ラベル撮影（要インターネット接続・AI機能）")
  with st.expander("スマホカメラでラベルを撮影して自動入力"):
    st.info("💡 AIラベル読み取りにはHTTPS通信環境とインターネット接続が必要です。")
    camera_image = st.camera_input("ラベルや裏書きを撮影", key="cam_input")
    if camera_image is not None and st.button(
        "AIに解析させる", key="btn_ai_parse"
    ):
      if not api_key:
        st.warning("APIキーを入力してください。")
      else:
        with st.spinner("AI解析中..."):
          try:
            client = genai.Client(api_key=api_key)
            prompt = (
                "この日本酒のラベル画像から次の項目を純粋なJSONのみで出力：\n"
                '{"brand_name":"","brewery_name":"","by_year":"","manufacture_date":"",'
                '"alcohol":"","rice":"","polish":"","sake_meter":"","acidity":"","amino":"","yeast":""}\n'
                "不明な項目は空文字にしてください。"
            )
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[
                    types.Part.from_bytes(
                        data=camera_image.getvalue(), mime_type=camera_image.type
                    ),
                    prompt,
                ],
            )
            text_res = response.text.strip()
            if "```json" in text_res:
              text_res = text_res.split("```json")[1].split("```")[0].strip()
            elif "```" in text_res:
              text_res = text_res.split("```")[1].split("```")[0].strip()

            parsed = json.loads(text_res)
            st.session_state["brand_name"] = parsed.get("brand_name", "")
            st.session_state["brewery_name"] = parsed.get("brewery_name", "")
            st.session_state["by_year"] = parsed.get("by_year", "")
            st.session_state["manufacture_date"] = parsed.get(
                "manufacture_date", ""
            )
            st.session_state["alcohol"] = parsed.get("alcohol", "")
            st.session_state["rice"] = parsed.get("rice", "")
            st.session_state["polish"] = parsed.get("polish", "")
            st.session_state["sake_meter"] = parsed.get("sake_meter", "")
            st.session_state["acidity"] = parsed.get("acidity", "")
            st.session_state["amino"] = parsed.get("amino", "")
            st.session_state["yeast"] = parsed.get("yeast", "")
            st.success("読み取り完了！入力欄に反映されました。")
            st.rerun()
          except Exception as e:
            st.error(f"AI解析エラー: {e}")

  st.divider()

  # Step 1: 銘柄選択
  st.subheader("Step 1: 銘柄・蔵元の選択")
  selected_label = st.selectbox(
      "公式データベース / 過去の保存履歴から選択（★印は保存済み）",
      options=brand_labels,
      index=None,
      placeholder="タップして検索・選択...",
      key="db_brand_select",
  )

  if selected_label and selected_label != st.session_state.get(
      "last_selected_label"
  ):
    st.session_state["last_selected_label"] = selected_label
    s_sake = next(
        (s for s in combined_sake_list if s["label"] == selected_label), None
    )
    if s_sake:
      st.session_state["brand_name"] = s_sake["name"]
      st.session_state["brewery_name"] = s_sake.get("brewery", "")
      if s_sake.get("alcohol"):
        st.session_state["alcohol"] = s_sake["alcohol"]
      if s_sake.get("rice"):
        st.session_state["rice"] = s_sake["rice"]
      if s_sake.get("polish"):
        st.session_state["polish"] = s_sake["polish"]
      if s_sake.get("sake_meter"):
        st.session_state["sake_meter"] = s_sake["sake_meter"]
      if s_sake.get("acidity"):
        st.session_state["acidity"] = s_sake["acidity"]
      if s_sake.get("amino"):
        st.session_state["amino"] = s_sake["amino"]
      if s_sake.get("yeast"):
        st.session_state["yeast"] = s_sake["yeast"]
      st.rerun()

  st.divider()
  st.subheader("Step 2: スペック詳細・ロット情報の確認と入力")

  brand_name = st.text_input("銘柄名 *", key="brand_name")
  brewery_name = st.text_input("蔵元名 *", key="brewery_name")

  c_b1, c_b2 = st.columns(2)
  with c_b1:
    by_year = st.text_input(
        "醸造年度 (BY)", placeholder="例: 2023BY", key="by_year"
    )
  with c_b2:
    manufacture_date = st.text_input(
        "製造年月 / ロット", placeholder="例: 2024.03", key="manufacture_date"
    )

  c_s1, c_s2 = st.columns(2)
  with c_s1:
    alcohol = st.text_input(
        "アルコール度数", placeholder="例: 16度", key="alcohol"
    )
    rice = st.text_input("原料米", placeholder="例: 山田錦", key="rice")
    polish = st.text_input("精米歩合", placeholder="例: 50%", key="polish")
  with c_s2:
    sake_meter = st.text_input("日本酒度", placeholder="例: +3", key="sake_meter")
    acidity = st.text_input("酸度", placeholder="例: 1.4", key="acidity")
    amino = st.text_input("アミノ酸度", placeholder="例: 1.0", key="amino")
    yeast = st.text_input("使用酵母", placeholder="例: 協会9号", key="yeast")

  st.divider()

  st.subheader("Step 3: 糖度計測定値（最大6回・未入力は空欄可）")
  c_m1, c_m2, c_m3 = st.columns(3)
  with c_m1:
    m1 = st.number_input(
        "1回目", value=st.session_state["m1"], format="%.2f", placeholder="未入力", key="m1_input"
    )
    m4 = st.number_input(
        "4回目", value=st.session_state["m4"], format="%.2f", placeholder="未入力", key="m4_input"
    )
  with c_m2:
    m2 = st.number_input(
        "2回目", value=st.session_state["m2"], format="%.2f", placeholder="未入力", key="m2_input"
    )
    m5 = st.number_input(
        "5回目", value=st.session_state["m5"], format="%.2f", placeholder="未入力", key="m5_input"
    )
  with c_m3:
    m3 = st.number_input(
        "3回目", value=st.session_state["m3"], format="%.2f", placeholder="未入力", key="m3_input"
    )
    m6 = st.number_input(
        "6回目", value=st.session_state["m6"], format="%.2f", placeholder="未入力", key="m6_input"
    )

  # 入力値のセッション状態への反映
  st.session_state["m1"] = m1
  st.session_state["m2"] = m2
  st.session_state["m3"] = m3
  st.session_state["m4"] = m4
  st.session_state["m5"] = m5
  st.session_state["m6"] = m6

  c_info1, c_info2 = st.columns(2)
  with c_info1:
    # 単位の標準化（°Bx と Brix を統合、単位不明の勝手な補完なし）
    unit = st.selectbox(
        "測定単位", options=["°Bx", "%", "単位不明"], key="unit"
    )
  with c_info2:
    measured_at = st.text_input(
        "測定日時 (JST)",
        key="measured_at",
    )

  device_info = st.text_input(
      "測定機器・モード情報（任意）",
      placeholder="例: デジタル糖度計 Model-X",
      key="device_info",
  )

  raw_measurements = [m1, m2, m3, m4, m5, m6]
  valid_measurements = [m for m in raw_measurements if m is not None]
  count_val = len(valid_measurements)

  st.markdown("### 📊 測定統計情報")
  if count_val > 0:
    mean_val = statistics.mean(valid_measurements)
    max_val = max(valid_measurements)
    min_val = min(valid_measurements)
    range_val = max_val - min_val
    median_val = statistics.median(valid_measurements)
    stdev_val = (
        statistics.stdev(valid_measurements) if count_val >= 2 else None
    )

    col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
    col_stat1.metric("測定回数", f"{count_val} 回")
    col_stat2.metric("平均値", f"{mean_val:.2f} {unit}")
    col_stat3.metric("最大値", f"{max_val:.2f} {unit}")
    col_stat4.metric("最小値", f"{min_val:.2f} {unit}")

    col_stat5, col_stat6, col_stat7 = st.columns(3)
    col_stat5.metric("最大-最小差 (範囲)", f"{range_val:.2f} {unit}")
    col_stat6.metric("中央値", f"{median_val:.2f} {unit}")
    col_stat7.metric(
        "標本標準偏差",
        f"{stdev_val:.3f}" if stdev_val is not None else "未算出 (データ1件)",
    )
  else:
    st.warning("⚠️ 有効な測定値が1件も入力されていません。数値を入力してください。")

  st.divider()


  def validate_datetime_str(dt_str):
    try:
      datetime.strptime(dt_str.strip(), "%Y-%m-%d %H:%M")
      return True
    except ValueError:
      return False


  # 保存処理（一意ID発行・正当な再測定を阻まない設計）
  if st.button("💾 測定データを保存する", use_container_width=True):
    if not brand_name.strip():
      st.error("エラー: 銘柄名は必須です。入力内容は保持されます。")
    elif count_val == 0:
      st.error("エラー: 有効な測定値が1件もありません。入力内容は保持されます。")
    elif not validate_datetime_str(measured_at):
      st.error(
          "エラー: 測定日時の形式が正しくありません。「YYYY-MM-DD HH:MM」形式で入力してください。"
      )
    else:
      conn = None
      try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("BEGIN TRANSACTION;")

        new_record_uuid = str(uuid.uuid4())

        cursor.execute(
            """
                INSERT INTO measurements (
                    record_uuid, measured_at, brand_name, brewery_name, by_year, manufacture_date,
                    alcohol, rice, polish, sake_meter, acidity, amino, yeast,
                    m1, m2, m3, m4, m5, m6, count, mean, max_val, min_val, range_val, median, stdev, unit, device_info
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_record_uuid,
                measured_at,
                brand_name,
                brewery_name,
                by_year,
                manufacture_date,
                alcohol,
                rice,
                polish,
                sake_meter,
                acidity,
                amino,
                yeast,
                m1,
                m2,
                m3,
                m4,
                m5,
                m6,
                count_val,
                mean_val,
                max_val,
                min_val,
                range_val,
                median_val,
                stdev_val,
                unit,
                device_info,
            ),
        )
        conn.commit()
        st.success(
            "🎉 測定データをデータベースに正常に保存しました！「保存データ一覧・検索・編集」タブから確認できます。"
        )

        # 保存成功後に測定値入力欄を確実にクリアし、日時を最新のJSTに更新
        st.session_state["m1"] = None
        st.session_state["m2"] = None
        st.session_state["m3"] = None
        st.session_state["m4"] = None
        st.session_state["m5"] = None
        st.session_state["m6"] = None
        st.session_state["measured_at"] = datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).strftime("%Y-%m-%d %H:%M")
        st.rerun()

      except Exception as e:
        if conn:
          conn.rollback()
        st.error(f"❌ データベース保存エラー（ロールバックしました）: {e}")
      finally:
        if conn:
          conn.close()

# ------------------------------------------
# タブ2: 保存データ一覧・検索・編集
# ------------------------------------------
with tab_list:
  st.subheader("📊 保存済みデータ一覧・検索・編集・削除")

  conn = sqlite3.connect(DB_NAME)
  df_all = pd.read_sql_query(
      "SELECT * FROM measurements ORDER BY id DESC", conn
  )
  conn.close()

  if df_all.empty:
    st.info("保存されたデータはまだありません。")
  else:
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
      q_brand = st.text_input("銘柄名で絞り込み", "", key="q_brand")
    with col_f2:
      q_brewery = st.text_input("蔵元名で絞り込み", "", key="q_brewery")
    with col_f3:
      q_lot = st.text_input("製造年月・ロットで絞り込み", "", key="q_lot")

    filtered_df = df_all.copy()
    if q_brand:
      filtered_df = filtered_df[
          filtered_df["brand_name"].str.contains(q_brand, na=False)
      ]
    if q_brewery:
      filtered_df = filtered_df[
          filtered_df["brewery_name"].str.contains(q_brewery, na=False)
      ]
    if q_lot:
      filtered_df = filtered_df[
          filtered_df["manufacture_date"].str.contains(q_lot, na=False)
      ]

    st.write(f"該当件数: {len(filtered_df)} 件")

    for idx, row in filtered_df.iterrows():
      u_str = (
          row["unit"]
          if row["unit"] is not None and str(row["unit"]).strip() != ""
          else "単位不明"
      )
      stdev_str = (
          f"{row['stdev']:.3f}"
          if row["stdev"] is not None and pd.notna(row["stdev"])
          else "未算出"
      )
      with st.expander(
          f"🍶 ID:{row['id']} | {row['brand_name']} ({row['brewery_name'] or '蔵元不明'}) - 平均: {row['mean']:.2f} {u_str} (測定日: {row['measured_at']})"
      ):
        edit_mode_key = f"edit_mode_{row['id']}"
        if edit_mode_key not in st.session_state:
          st.session_state[edit_mode_key] = False

        if not st.session_state[edit_mode_key]:
          c_d1, c_d2 = st.columns(2)
          with c_d1:
            st.write(f"**醸造年度(BY):** {row['by_year'] or '-'}")
            st.write(f"**製造年月/ロット:** {row['manufacture_date'] or '-'}")
            st.write(f"**アルコール度数:** {row['alcohol'] or '-'}")
            st.write(
                f"**原料米 / 精米歩合:** {row['rice'] or '-'} / {row['polish'] or '-'}"
            )
            st.write(
                f"**日本酒度/酸度/アミノ:** {row['sake_meter'] or '-'} / {row['acidity'] or '-'} / {row['amino'] or '-'}"
            )
            st.write(f"**使用酵母:** {row['yeast'] or '-'}")
          with c_d2:
            st.write(f"**測定日時:** {row['measured_at']}")
            valid_m = [
                m
                for m in [
                    row["m1"],
                    row["m2"],
                    row["m3"],
                    row["m4"],
                    row["m5"],
                    row["m6"],
                ]
                if m is not None and pd.notna(m)
            ]
            st.write(f"**個別測定値:** {valid_m}")
            st.write(f"**測定回数:** {row['count']}回")
            st.write(f"**平均値:** {row['mean']:.2f} {u_str}")
            st.write(
                f"**最大 / 最小:** {row['max_val']:.2f} {u_str} / {row['min_val']:.2f} {u_str}"
            )
            st.write(
                f"**中央値 / 標準偏差:** {row['median']:.2f} {u_str} / {stdev_str}"
            )
            st.write(f"**測定単位:** {u_str}")
            st.write(f"**機器情報:** {row['device_info'] or 'なし'}")

          col_btn1, col_btn2 = st.columns(2)
          with col_btn1:
            if st.button(
                f"✏️ この記録を編集する", key=f"to_edit_{row['id']}"
            ):
              st.session_state[edit_mode_key] = True
              st.rerun()
          with col_btn2:
            delete_confirm_key = f"confirm_del_{row['id']}"
            if delete_confirm_key not in st.session_state:
              st.session_state[delete_confirm_key] = False

            if not st.session_state[delete_confirm_key]:
              if st.button(
                  f"🗑️ 削除ボタン (ID: {row['id']})", key=f"btn_del_{row['id']}"
              ):
                st.session_state[delete_confirm_key] = True
                st.rerun()
            else:
              st.warning(
                  f"⚠️ 本当に削除しますか？\n[ID: {row['id']}] 銘柄: {row['brand_name']} (測定日時: {row['measured_at']})"
              )
              c_yes, c_no = st.columns(2)
              with c_yes:
                if st.button("はい、削除する", key=f"yes_del_{row['id']}"):
                  conn_del = None
                  try:
                    conn_del = sqlite3.connect(DB_NAME)
                    c_del = conn_del.cursor()
                    c_del.execute("BEGIN TRANSACTION;")
                    c_del.execute(
                        "DELETE FROM measurements WHERE id = ?", (row["id"],)
                    )
                    conn_del.commit()
                    st.success("削除が完了しました。")
                    st.rerun()
                  except Exception as e:
                    if conn_del:
                      conn_del.rollback()
                    st.error(f"削除失敗: {e}")
                  finally:
                    if conn_del:
                      conn_del.close()
              with c_no:
                if st.button("いいえ（キャンセル）", key=f"no_del_{row['id']}"):
                  st.session_state[delete_confirm_key] = False
                  st.rerun()

        else:
          st.markdown(f"### ✏️ レコード編集 (ID: {row['id']})")
          with st.form(key=f"edit_form_{row['id']}_v2"):
            e_brand = st.text_input("銘柄名", value=row["brand_name"])
            e_brewery = st.text_input("蔵元名", value=row["brewery_name"] or "")
            e_by = st.text_input("醸造年度(BY)", value=row["by_year"] or "")
            e_lot = st.text_input(
                "製造年月/ロット", value=row["manufacture_date"] or ""
            )
            e_alc = st.text_input(
                "アルコール度数", value=row["alcohol"] or ""
            )
            e_rice = st.text_input("原料米", value=row["rice"] or "")
            e_polish = st.text_input("精米歩合", value=row["polish"] or "")
            e_sake_m = st.text_input("日本酒度", value=row["sake_meter"] or "")
            e_acid = st.text_input("酸度", value=row["acidity"] or "")
            e_amino = st.text_input("アミノ酸度", value=row["amino"] or "")
            e_yeast = st.text_input("使用酵母", value=row["yeast"] or "")

            st.write("--- 測定値の修正 ---")
            e_m1 = st.number_input(
                "1回目",
                value=float(row["m1"]) if row["m1"] is not None else None,
                format="%.2f",
            )
            e_m2 = st.number_input(
                "2回目",
                value=float(row["m2"]) if row["m2"] is not None else None,
                format="%.2f",
            )
            e_m3 = st.number_input(
                "3回目",
                value=float(row["m3"]) if row["m3"] is not None else None,
                format="%.2f",
            )
            e_m4 = st.number_input(
                "4回目",
                value=float(row["m4"]) if row["m4"] is not None else None,
                format="%.2f",
            )
            e_m5 = st.number_input(
                "5回目",
                value=float(row["m5"]) if row["m5"] is not None else None,
                format="%.2f",
            )
            e_m6 = st.number_input(
                "6回目",
                value=float(row["m6"]) if row["m6"] is not None else None,
                format="%.2f",
            )

            current_unit = row["unit"] if row["unit"] else "°Bx"
            unit_options = ["°Bx", "%", "単位不明"]
            if current_unit not in unit_options:
              unit_options.append(current_unit)

            e_unit = st.selectbox(
                "測定単位",
                options=unit_options,
                index=unit_options.index(current_unit)
                if current_unit in unit_options
                else 0,
            )
            e_measured_at = st.text_input("測定日時", value=row["measured_at"])
            e_device = st.text_input(
                "機器情報", value=row["device_info"] or ""
            )

            col_sub_ed, col_canc_ed = st.columns(2)
            submitted_edit = col_sub_ed.form_submit_button("更新を保存する")
            canceled_edit = col_canc_ed.form_submit_button("キャンセル")

            if canceled_edit:
              st.session_state[edit_mode_key] = False
              st.rerun()

            if submitted_edit:
              if not e_brand.strip():
                st.error("エラー: 銘柄名は必須です。")
              else:
                e_raw_m = [e_m1, e_m2, e_m3, e_m4, e_m5, e_m6]
                e_valid_m = [m for m in e_raw_m if m is not None]
                e_count = len(e_valid_m)
                if e_count == 0:
                  st.error("エラー: 有効な測定値が1件もありません。")
                else:
                  e_mean = statistics.mean(e_valid_m)
                  e_max = max(e_valid_m)
                  e_min = min(e_valid_m)
                  e_range = e_max - e_min
                  e_median = statistics.median(e_valid_m)
                  e_stdev = (
                      statistics.stdev(e_valid_m) if e_count >= 2 else None
                  )

                  conn_up = None
                  try:
                    conn_up = sqlite3.connect(DB_NAME)
                    c_up = conn_up.cursor()
                    c_up.execute("BEGIN TRANSACTION;")
                    c_up.execute(
                        """
                                            UPDATE measurements SET
                                                measured_at = ?, brand_name = ?, brewery_name = ?, by_year = ?, manufacture_date = ?,
                                                alcohol = ?, rice = ?, polish = ?, sake_meter = ?, acidity = ?, amino = ?, yeast = ?,
                                                m1 = ?, m2 = ?, m3 = ?, m4 = ?, m5 = ?, m6 = ?, count = ?, mean = ?, max_val = ?,
                                                min_val = ?, range_val = ?, median = ?, stdev = ?, unit = ?, device_info = ?
                                            WHERE id = ?
                                        """,
                        (
                            e_measured_at,
                            e_brand,
                            e_brewery,
                            e_by,
                            e_lot,
                            e_alc,
                            e_rice,
                            e_polish,
                            e_sake_m,
                            e_acid,
                            e_amino,
                            e_yeast,
                            e_m1,
                            e_m2,
                            e_m3,
                            e_m4,
                            e_m5,
                            e_m6,
                            e_count,
                            e_mean,
                            e_max,
                            e_min,
                            e_range,
                            e_median,
                            e_stdev,
                            e_unit,
                            e_device,
                            row["id"],
                        ),
                    )
                    conn_up.commit()
                    st.session_state[edit_mode_key] = False
                    st.success("🎉 データを正常に更新しました！")
                    st.rerun()
                  except Exception as ex:
                    if conn_up:
                      conn_up.rollback()
                    st.error(f"更新失敗: {ex}")
                  finally:
                    if conn_up:
                      conn_up.close()

# ------------------------------------------
# タブ3: データ比較（異なる単位の自動混合比較の完全ブロック）
# ------------------------------------------
with tab_compare:
  st.subheader("⚖️ 複数データの比較分析")
  conn = sqlite3.connect(DB_NAME)
  df_comp = pd.read_sql_query(
      "SELECT id, measured_at, brand_name, brewery_name, manufacture_date, alcohol, mean, unit, count FROM measurements ORDER BY id DESC",
      conn,
  )
  conn.close()

  if df_comp.empty:
    st.info("比較するデータがありません。")
  else:
    st.write("比較したい測定データを選択してください:")
    selected_ids = []
    for idx, row in df_comp.iterrows():
      u_str = (
          row["unit"]
          if row["unit"] is not None and str(row["unit"]).strip() != ""
          else "単位不明"
      )
      label_str = f"ID:{row['id']} | {row['brand_name']} ({row['brewery_name'] or '蔵元不明'}) - ロット:{row['manufacture_date'] or '-'} - 平均:{row['mean']:.2f} {u_str} ({row['measured_at']})"
      if st.checkbox(label_str, key=f"comp_{row['id']}"):
        selected_ids.append(row["id"])

    if selected_ids:
      conn = sqlite3.connect(DB_NAME)
      placeholders = ",".join(["?"] * len(selected_ids))
      df_selected = pd.read_sql_query(
          f"SELECT * FROM measurements WHERE id IN ({placeholders})",
          conn,
          params=selected_ids,
      )
      conn.close()

      # 異なる単位が含まれている場合の厳格なチェックと集計ブロック
      units_in_selection = df_selected["unit"].fillna("単位不明").unique()
      if len(units_in_selection) > 1:
        st.error(
            "🛑 比較エラー: 選択されたデータの中に異なる測定単位（"
            + ", ".join(units_in_selection)
            + "）が混ざっています。同一尺度ではないため、誤った集計や比較を防止するためにテーブル表示を制限しています。同じ単位のデータのみを選択してください。"
        )
      else:
        st.markdown("### 📋 選択データの比較表")
        display_cols = [
            "id",
            "brand_name",
            "brewery_name",
            "manufacture_date",
            "alcohol",
            "count",
            "mean",
            "max_val",
            "min_val",
            "range_val",
            "stdev",
            "unit",
            "measured_at",
        ]
        st.dataframe(df_selected[display_cols])

# ------------------------------------------
# タブ4: バックアップ・復元（一意ID対応・型検証・空配列全削除防止）
# ------------------------------------------
with tab_backup:
  st.subheader("⚙️ CSV出力 ＆ JSONバックアップ・復元")

  conn = sqlite3.connect(DB_NAME)
  df_export = pd.read_sql_query("SELECT * FROM measurements", conn)
  conn.close()

  if not df_export.empty:
    csv_data = df_export.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="📥 全データをCSV形式でダウンロード",
        data=csv_data,
        file_name="sake_brix_data.csv",
        mime="text/csv",
    )

    json_data = df_export.to_json(
        orient="records", force_ascii=False, indent=2
    )
    st.download_button(
        label="📦 全データをJSON形式でバックアップ",
        data=json_data,
        file_name="sake_brix_backup.json",
        mime="application/json",
    )

  st.divider()
  st.write("### ♻️ JSONバックアップからの復元・インポート")
  uploaded_json = st.file_uploader(
      "バックアップJSONファイルを選択", type=["json"], key="json_uploader"
  )

  if uploaded_json is not None:
    try:
      raw_content = uploaded_json.read().decode("utf-8")
      imported_list = json.loads(raw_content)

      if not isinstance(imported_list, list):
        st.error(
            "❌ エラー: 選択されたJSONファイルは有効なリスト形式ではありません。"
        )
      elif len(imported_list) == 0:
        st.error(
            "🛑 エラー: アップロードされたJSONファイルが空（配列 []）です。誤った全削除を防ぐためインポートを中止しました。"
        )
      else:
        st.info(f"📁 読み込んだファイルに含まれるレコード数: {len(imported_list)}件")

        import_mode = st.radio(
            "インポート方法の選択",
            options=[
                (
                    "安全に追加（一意IDまたは同一日時・銘柄・平均値の重複をスキップ）"
                ),
                (
                    "全置換（既存データをクリアしてバックアップ内容に復元 ※要確認）"
                ),
            ],
            key="import_mode_choice",
        )

        # 事前バリデーション（全レコードの型・必須条件・統計値の整合性検証）
        has_invalid = False
        for item in imported_list:
          if not item.get("brand_name") or item.get("mean") is None:
            has_invalid = True
            break
          # 測定値と平均値の簡単な矛盾チェック（個別の値がある場合）
          raw_m = [
              item.get("m1"),
              item.get("m2"),
              item.get("m3"),
              item.get("m4"),
              item.get("m5"),
              item.get("m6"),
          ]
          valid_m = [m for m in raw_m if m is not None]
          if len(valid_m) > 0:
            calc_mean = statistics.mean(valid_m)
            stored_mean = item.get("mean")
            if abs(calc_mean - stored_mean) > 0.05:
              has_invalid = True
              break

        if has_invalid:
          st.error(
              "❌ エラー: インポートファイル内に必須項目の欠落、または測定値と平均値に矛盾がある不正なレコードが含まれています。データの破損を防ぐため処理を中止します。"
          )
        else:
          if "全置換" in import_mode:
            conn_temp = sqlite3.connect(DB_NAME)
            c_temp = conn_temp.cursor()
            c_temp.execute("SELECT COUNT(*) FROM measurements;")
            existing_count = c_temp.fetchone()[0]
            conn_temp.close()

            st.warning(
                f"⚠️ 【警告】全置換を実行すると、現在の既存データ（全 {existing_count} 件）がすべて削除されます。"
            )
            confirm_replace = st.checkbox(
                "上記のリスクを理解し、全置換を実行することに同意します",
                key="confirm_replace_chk",
            )
          else:
            confirm_replace = True

          if confirm_replace and st.button(
              "インポートを実行する", key="btn_exec_import"
          ):
            success_count = 0
            skip_count = 0
            fail_count = 0

            conn_imp = None
            try:
              conn_imp = sqlite3.connect(DB_NAME)
              cursor = conn_imp.cursor()
              cursor.execute("BEGIN TRANSACTION;")

              if "全置換" in import_mode:
                cursor.execute("DELETE FROM measurements;")

              for item in imported_list:
                if not item.get("brand_name") or item.get("mean") is None:
                  fail_count += 1
                  continue

                r_uuid = item.get("record_uuid")
                if not r_uuid:
                  r_uuid = str(uuid.uuid4())

                if "安全に追加" in import_mode:
                  # 1. record_uuidによる重複判定
                  cursor.execute(
                      "SELECT COUNT(*) FROM measurements WHERE record_uuid = ?",
                      (r_uuid,),
                  )
                  if cursor.fetchone()[0] > 0:
                    skip_count += 1
                    continue
                  # 2. 補助として同一日時・銘柄・平均値による重複判定
                  cursor.execute(
                      """
                                        SELECT COUNT(*) FROM measurements 
                                        WHERE brand_name = ? AND measured_at = ? AND mean = ?
                                    """,
                      (
                          item.get("brand_name"),
                          item.get("measured_at"),
                          item.get("mean"),
                      ),
                  )
                  if cursor.fetchone()[0] > 0:
                    skip_count += 1
                    continue

                cursor.execute(
                    """
                                    INSERT INTO measurements (
                                        record_uuid, measured_at, brand_name, brewery_name, by_year, manufacture_date,
                                        alcohol, rice, polish, sake_meter, acidity, amino, yeast,
                                        m1, m2, m3, m4, m5, m6, count, mean, max_val, min_val, range_val, median, stdev, unit, device_info
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                    (
                        r_uuid,
                        item.get("measured_at"),
                        item.get("brand_name"),
                        item.get("brewery_name"),
                        item.get("by_year"),
                        item.get("manufacture_date"),
                        item.get("alcohol"),
                        item.get("rice"),
                        item.get("polish"),
                        item.get("sake_meter"),
                        item.get("acidity"),
                        item.get("amino"),
                        item.get("yeast"),
                        item.get("m1"),
                        item.get("m2"),
                        item.get("m3"),
                        item.get("m4"),
                        item.get("m5"),
                        item.get("m6"),
                        item.get("count"),
                        item.get("mean"),
                        item.get("max_val"),
                        item.get("min_val"),
                        item.get("range_val"),
                        item.get("median"),
                        item.get("stdev"),
                        item.get("unit", "単位不明"),
                        item.get("device_info"),
                    ),
                )
                success_count += 1

              conn_imp.commit()
              st.success(
                  f"🎉 インポート処理が完了しました！\n"
                  f"- 成功: {success_count} 件\n"
                  f"- スキップ（重複）: {skip_count} 件\n"
                  f"- 失敗（データ不備）: {fail_count} 件"
              )
            except Exception as db_err:
              if conn_imp:
                conn_imp.rollback()
              st.error(
                  f"❌ エラー発生のためロールバックしました（既存データは安全に保護されています）: {db_err}"
              )
            finally:
              if conn_imp:
                conn_imp.close()

    except json.JSONDecodeError:
      st.error(
          "❌ エラー: 選択されたファイルは有効なJSON形式ではありません。"
      )
    except Exception as e:
      st.error(f"❌ ファイル読み込みエラー: {e}")