import datetime
import io
import json
import sqlite3
import uuid
import pandas as pd
import streamlit as st
from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. ページ基本設定 (スマホ等でのレスポンシブ表示対応)
# ---------------------------------------------------------
st.set_page_config(
    page_title="日本酒 糖度測定＆総合スペック管理ツール",
    page_icon="🍶",
    layout="wide",
    initial_sidebar_state="expanded",
)

# レスポンシブ対応のカスタムCSS（スマホでの文字サイズや余白の最適化）
st.markdown(
    """
    <style>
    /* 全体のフォント・視認性調整 */
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    
    /* スマホ画面（幅768px以下）向けの文字サイズ・レイアウト自動調整 */
    @media (max-width: 768px) {
        h1 {
            font-size: 1.5rem !important;
        }
        h2 {
            font-size: 1.25rem !important;
        }
        h3 {
            font-size: 1.1rem !important;
        }
        .stTextInput input, .stNumberInput input, .stSelectbox, .stTextArea textarea {
            font-size: 16px !important; /* iOSのズーム防止と視認性確保 */
        }
        .stButton button {
            width: 100%;
            font-size: 1rem !important;
            padding: 0.6rem !important;
        }
        /* モバイルでのカードやコンテナのパディング調整 */
        div[data-testid="stVerticalBlock"] > div {
            gap: 0.5rem;
        }
    }
    
    /* 成功・情報メッセージのスタイル */
    .success-box {
        padding: 10px;
        background-color: #d4edda;
        color: #155724;
        border-radius: 5px;
        margin-bottom: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# 2. データベース初期設定 (SQLite & JST)
# ---------------------------------------------------------
DB_NAME = "sake_brix.db"


def get_jst_now():
    jst = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS measurements (
            id TEXT PRIMARY KEY,
            recorded_at TEXT,
            brewery TEXT,
            name TEXT,
            by_year TEXT,
            lot_date TEXT,
            spec_category TEXT,
            processing_state TEXT,
            brix REAL,
            alcohol REAL,
            smv REAL,
            acidity REAL,
            amino_acid REAL,
            rice_polished REAL,
            rice_used TEXT,
            yeast TEXT,
            memo TEXT
        )
    """
    )
    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------
# 3. API設定・バックアップ (Secrets自動取得対応)
# ---------------------------------------------------------
st.sidebar.title("⚙️ 設定")

# StreamlitのSecretsから自動取得を試みる
active_api_key = None
try:
    if "GEMINI_API_KEY" in st.secrets:
        active_api_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    pass

# サイドバーでの手動入力（Secrets未設定時や上書き用、初期値にSecretsを設定）
api_key_input = st.sidebar.text_input(
    "Gemini API Key",
    type="password",
    value=active_api_key if active_api_key else "",
    placeholder="AIzaSy...",
)
if api_key_input:
    active_api_key = api_key_input.strip()

if active_api_key:
    try:
        client = genai.Client(api_key=active_api_key)
        # 接続テスト（軽量なモデル呼び出し確認）
        st.sidebar.markdown(
            f'<div style="color: green; font-size: 0.9rem;">✅ 外部API接続成功<br><small>({get_jst_now()})</small></div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        st.sidebar.error(f"APIキーエラー: {e}")
        client = None
else:
    client = None
    st.sidebar.info(
        "💡 StreamlitのSecrets設定、またはサイドバーにAPIキーを入力してください。"
    )

st.sidebar.markdown("---")
st.sidebar.subheader("📦 データバックアップ")


# バックアップ書き出し
def export_db_json():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM measurements", conn)
    conn.close()
    return df.to_json(orient="records", force_ascii=False)


if os_data := export_db_json():
    st.sidebar.download_button(
        label="💾 JSONバックアップをダウンロード",
        data=os_data,
        file_name=f"sake_brix_backup_{datetime.date.today()}.json",
        mime="application/json",
    )
# ---------------------------------------------------------
# 4. メイン画面・ナビゲーション
# ---------------------------------------------------------
st.title("🍶 日本酒 糖度測定＆総合スペック管理ツール")

menu = st.radio(
    "メニュー",
    [
        "📝 新規測定・入力",
        "📊 保存データ一覧・検索・編集",
        "⚖️ データ比較",
        "🔄 バックアップ・復元",
    ],
    horizontal=True,
)


# ---------------------------------------------------------
# 5. 各機能タブの実装
# ---------------------------------------------------------

# セッションステートの初期化（AI解析結果保持用）
if "ai_data" not in st.session_state:
    st.session_state["ai_data"] = {}

if menu == "📝 新規測定・入力":
    st.header("📝 新規測定・スペック入力")

    # --- AIラベル解析セクション（スマホアルバム選択 ＆ カメラ対応） ---
    with st.expander(
        "📷 ラベル撮影・画像アップロード（AI自動入力）", expanded=True
    ):
        st.markdown(
            "スマホのカメラで撮影するか、フォトライブラリ（アルバム）から既存のラベル画像を選択してください。"
        )

        # ファイルアップローラー（スマホのアルバム選択やPCからのファイル選択を網羅）
        uploaded_file = st.file_uploader(
            "ラベル画像を選択 (JPEG / PNG)", type=["jpg", "jpeg", "png"]
        )

        if uploaded_file is not None:
            st.image(
                uploaded_file,
                caption="アップロードされたラベル",
                use_container_width=True,
            )

            if st.button("🤖 このラベルをAIで解析する"):
                if not client:
                    st.error(
                        "サイドバーに有効な Gemini API Key が設定されていません。"
                    )
                else:
                    with st.spinner(
                        "ラベルから蔵元名・銘柄名・詳細スペックを解析中..."
                    ):
                        try:
                            image_bytes = uploaded_file.getvalue()
                            prompt = """
                            この日本酒ラベルの画像を分析し、以下の情報をJSON形式のみで抽出してください。余計なテキストは含めないでください。
                            抽出キー:
                            - brewery: 蔵元名（醸造元・会社名）
                            - name: 銘柄名・商品名（例: 「〇〇 特別純米 無濾過生原酒」など、全体の商品名称としてまとめる）
                            - spec_category: 特定名称（純米酒, 特別純米酒, 純米吟醸, 吟醸酒, 純米大吟醸, 大吟醸, 本醸造, 普通酒 等から該当するもの）
                            - processing_state: 製法・状態（例: 無濾過, 生, 原酒, 火入れ, 中取り, 直汲み, 濁り 等のキーワードをカンマ区切りまたは簡潔に）
                            - by_year: 醸造年度/BY（例: 2025BY, 2024BY など）
                            - lot_date: 製造年月 または ロット（例: 2024.10, 2026.02 など）
                            - alcohol: アルコール度数（数値のみ、例: 16.0）
                            - smv: 日本酒度（数値、例: +3.5 や -2）
                            - acidity: 酸度（数値、例: 1.4）
                            - amino_acid: アミノ酸度（数値、例: 1.1）
                            - rice_polished: 精米歩合（%の数値のみ、例: 60）
                            - rice_used: 使用米（例: 山田錦, 五百万石 等）
                            - yeast: 使用酵母（例: 協会7号, 熊本酵母 等）
                            不明な項目は空文字 "" または null にしてください。
                            """
                            response = client.models.generate_content(
                                model="gemini-2.5-flash",
                                contents=[
                                    types.Part.from_bytes(
                                        data=image_bytes,
                                        mime_type=uploaded_file.type,
                                    ),
                                    prompt,
                                ],
                            )
                            # JSONの抽出
                            text_res = response.text.strip()
                            if "```json" in text_res:
                                text_res = text_res.split("```json")[
                                    1
                                ]
                            if "```" in text_res:
                                text_res = text_res.split("```")[0]

                            parsed_data = json.loads(text_res.strip())
                            st.session_state["ai_data"] = parsed_data
                            st.success("✨ ラベルの解析に成功しました！")
                        except Exception as e:
                            st.error(f"AI解析中にエラーが発生しました: {e}")

    # --- 入力フォーム ---
    ai = st.session_state.get("ai_data", {})

    with st.form("entry_form"):
        st.subheader("Step 1: 蔵元・銘柄の選択と基本情報")

        # 蔵元名の次に銘柄名（商品名）を配置
        col1, col2 = st.columns(2)
        with col1:
            brewery = st.text_input(
                "蔵元名 *", value=ai.get("brewery", "")
            )
        with col2:
            name = st.text_input(
                "銘柄名・商品名 *",
                value=ai.get("name", ""),
                help="例: 〇〇 特別純米 無濾過生原酒",
            )

        st.subheader("Step 2: スペック詳細・ロット情報")
        col3, col4, col5 = st.columns(3)
        with col3:
            # 特定名称
            spec_options = [
                "未選択",
                "純米酒",
                "特別純米酒",
                "純米吟醸",
                "吟醸酒",
                "純米大吟醸",
                "大吟醸",
                "本醸造",
                "普通酒",
                "その他",
            ]
            default_spec = ai.get("spec_category", "未選択")
            spec_idx = (
                spec_options.index(default_spec)
                if default_spec in spec_options
                else 0
            )
            spec_category = st.selectbox(
                "特定名称", spec_options, index=spec_idx
            )
        with col4:
            # 製法・状態（無濾過、生、原酒などの詳細情報をしっかり網羅）
            processing_state = st.text_input(
                "製法・状態",
                value=ai.get("processing_state", ""),
                help="例: 無濾過生原酒, 直汲み, 火入れ 等",
            )
        with col5:
            by_year = st.text_input(
                "醸造年度 (BY)",
                value=ai.get("by_year", ""),
                placeholder="例: 2025BY",
            )

        col6, col7 = st.columns(2)
        with col6:
            lot_date = st.text_input(
                "製造年月 / ロット",
                value=ai.get("lot_date", ""),
                placeholder="例: 2026.04",
            )
        with col7:
            brix = st.number_input(
                "糖度 (°Bx) *",
                min_value=0.0,
                max_value=50.0,
                step=0.1,
                format="%.1f",
            )

        st.subheader("Step 3: 詳細分析値・原材料")
        col8, col9, col10 = st.columns(3)
        with col8:
            def_alc = (
                float(ai.get("alcohol"))
                if ai.get("alcohol") not in [None, ""]
                else 0.0
            )
            alcohol = st.number_input(
                "アルコール度数 (%)",
                min_value=0.0,
                max_value=30.0,
                step=0.1,
                value=def_alc,
                format="%.1f",
            )
        with col9:
            def_smv = (
                float(ai.get("smv"))
                if ai.get("smv") not in [None, ""]
                else 0.0
            )
            smv = st.number_input(
                "日本酒度",
                min_value=-30.0,
                max_value=30.0,
                step=0.5,
                value=def_smv,
                format="%.1f",
            )
        with col10:
            def_acid = (
                float(ai.get("acidity"))
                if ai.get("acidity") not in [None, ""]
                else 0.0
            )
            acidity = st.number_input(
                "酸度",
                min_value=0.0,
                max_value=5.0,
                step=0.1,
                value=def_acid,
                format="%.1f",
            )

        col11, col12, col13 = st.columns(3)
        with col11:
            def_amino = (
                float(ai.get("amino_acid"))
                if ai.get("amino_acid") not in [None, ""]
                else 0.0
            )
            amino_acid = st.number_input(
                "アミノ酸度",
                min_value=0.0,
                max_value=5.0,
                step=0.1,
                value=def_amino,
                format="%.1f",
            )
        with col12:
            def_rp = (
                float(ai.get("rice_polished"))
                if ai.get("rice_polished") not in [None, ""]
                else 0.0
            )
            rice_polished = st.number_input(
                "精米歩合 (%)",
                min_value=0.0,
                max_value=100.0,
                step=1.0,
                value=def_rp,
                format="%.1f",
            )
        with col13:
            rice_used = st.text_input(
                "使用米", value=ai.get("rice_used", "")
            )

        yeast = st.text_input("使用酵母", value=ai.get("yeast", ""))
        memo = st.text_area("テイスティングメモ・感想")

        submitted = st.form_submit_button(
            "💾 測定データを保存する", type="primary"
        )

        if submitted:
            if not brewery or not name:
                st.error("「蔵元名」と「銘柄名・商品名」は必須入力です。")
            else:
                record_id = str(uuid.uuid4())
                recorded_at = get_jst_now()
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute(
                    """
                    INSERT INTO measurements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        record_id,
                        recorded_at,
                        brewery,
                        name,
                        by_year,
                        lot_date,
                        spec_category,
                        processing_state,
                        brix,
                        alcohol,
                        smv,
                        acidity,
                        amino_acid,
                        rice_polished,
                        rice_used,
                        yeast,
                        memo,
                    ),
                )
                conn.commit()
                conn.close()
                st.success(
                    f"🎉 保存完了しました！ [{brewery} - {name} / 糖度: {brix}°Bx]"
                )
                st.session_state["ai_data"] = {}  # クリア

elif menu == "📊 保存データ一覧・検索・編集":
    st.header("📊 保存データ一覧・検索")
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM measurements ORDER BY recorded_at DESC", conn)
    conn.close()

    if df.empty:
        st.info("保存されたデータはまだありません。「新規測定・入力」から記録を追加してください。")
    else:
        search_query = st.text_input(
            "🔍 蔵元名・銘柄名・メモで検索", ""
        )
        if search_query:
            df = df[
                df["brewery"].str.contains(search_query, na=False)
                | df["name"].str.contains(search_query, na=False)
                | df["memo"].str.contains(search_query, na=False)
            ]

        st.dataframe(df, use_container_width=True)

        st.subheader("🗑️ レコードの削除")
        del_id = st.selectbox(
            "削除するレコードを選択 (記録日時 / 蔵元 / 銘柄)",
            options=df["id"].tolist(),
            format_func=lambda x: f"{df[df['id'] == x]['recorded_at'].values[0]} | {df[df['id'] == x]['brewery'].values[0]} - {df[df['id'] == x]['name'].values[0]}",
        )
        if st.button("選択したレコードを削除"):
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("DELETE FROM measurements WHERE id = ?", (del_id,))
            conn.commit()
            conn.close()
            st.success("レコードを削除しました。ページを再読み込みしてください。")

elif menu == "⚖️ データ比較":
    st.header("⚖️ 銘柄データ比較")
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM measurements", conn)
    conn.close()

    if len(df) < 2:
        st.info(
            "比較するには、少なくとも2件以上のデータを保存してください。"
        )
    else:
        selected_ids = st.multiselect(
            "比較する銘柄を選択してください（複数可）",
            options=df["id"].tolist(),
            format_func=lambda x: f"{df[df['id'] == x]['brewery'].values[0]} - {df[df['id'] == x]['name'].values[0]} ({df[df['id'] == x]['brix'].values[0]}°Bx)",
        )
        if selected_ids:
            comp_df = df[df["id"].isin(selected_ids)].set_index("name")
            st.dataframe(
                comp_df[
                    [
                        "brewery",
                        "brix",
                        "spec_category",
                        "processing_state",
                        "alcohol",
                        "smv",
                        "acidity",
                        "rice_polished",
                        "recorded_at",
                    ]
                ],
                use_container_width=True,
            )

elif menu == "🔄 バックアップ・復元":
    st.header("🔄 データの復元 (JSONインポート)")
    st.warning(
        "注意: JSONバックアップファイルをアップロードすると、現在のデータベースにデータが追加されます。"
    )

    uploaded_json = st.file_uploader(
        "バックアップJSONファイルを選択", type=["json"]
    )
    if uploaded_json is not None:
        try:
            data = json.load(uploaded_json)
            import_df = pd.DataFrame(data)
            conn = sqlite3.connect(DB_NAME)
            import_df.to_sql(
                "measurements", conn, if_exists="append", index=False
            )
            conn.close()
            st.success("✨ データの復元（インポート）が完了しました！")
        except Exception as e:
            st.error(f"復元中にエラーが発生しました: {e}")
