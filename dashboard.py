# 美股加減倉決策儀表板
# 監控 5 大市場訊號：VIX / 恐懼貪婪 / 市場廣度 / 信用市場 / 跨資產

import streamlit as st
import yfinance as yf
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import pytz
from streamlit_autorefresh import st_autorefresh

# ── 頁面設定 ────────────────────────────────────────────────
st.set_page_config(
    page_title="美股加減倉決策儀表板",
    page_icon="📊",
    layout="wide",
)

TW_TZ = pytz.timezone("Asia/Taipei")

# 每 8 小時自動刷新
st_autorefresh(interval=28_800_000, key="autorefresh")

# ── 自訂樣式 ────────────────────────────────────────────────
st.markdown("""
<style>
    section[data-testid="stSidebar"] { display: none; }
    div[data-testid="stMetric"] {
        background: rgba(255,255,255,0.05);
        border-radius: 8px;
        padding: 10px 14px;
    }
    .block-container { padding-top: 1.5rem; }
    /* 說明按鈕與標題垂直對齊，圖示置中 */
    [data-testid="stPopover"] button {
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        padding: 0.25rem !important;
    }
    [data-testid="stPopover"] button p {
        margin: 0 !important;
        line-height: 1 !important;
    }
</style>
""", unsafe_allow_html=True)


# ── 資料抓取（TTL=300 秒快取）──────────────────────────────

@st.cache_data(ttl=28800, show_spinner=False)
def get_vix():
    """VIX 恐慌指數"""
    try:
        hist = yf.Ticker("^VIX").history(period="1mo")["Close"].dropna()
        if len(hist) < 2:
            return None
        cur, prev = hist.iloc[-1], hist.iloc[-2]
        return {
            "current": round(cur, 2),
            "change": round(cur - prev, 2),
            "change_pct": round((cur - prev) / prev * 100, 2),
            "history": hist.tail(22),
        }
    except Exception:
        return None


@st.cache_data(ttl=28800, show_spinner=False)
def get_fear_greed():
    """CNN 恐懼貪婪指數"""
    # 評級英文 → 中文對照
    RATING_CN = {
        "extreme fear": "極度恐懼",
        "fear":         "恐懼",
        "neutral":      "中性",
        "greed":        "貪婪",
        "extreme greed":"極度貪婪",
    }
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": "https://edition.cnn.com/",
        }
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        fg = data["fear_and_greed"]
        score = round(float(fg["score"]), 1)
        rating_en = fg.get("rating", "").lower()
        rating_cn = RATING_CN.get(rating_en, rating_en)
        # 前期比較（可選）
        prev_1d = fg.get("previous_close")
        prev_1w = fg.get("previous_1_week")
        prev_1m = fg.get("previous_1_month")
        return {
            "score": score,
            "rating": rating_cn,
            "prev_1d": round(float(prev_1d), 1) if prev_1d else None,
            "prev_1w": round(float(prev_1w), 1) if prev_1w else None,
            "prev_1m": round(float(prev_1m), 1) if prev_1m else None,
        }
    except Exception:
        return None


@st.cache_data(ttl=28800, show_spinner=False)
def get_market_breadth():
    """市場廣度：SPY / RSP / IWM"""
    try:
        syms = ["SPY", "RSP", "IWM"]
        frames = {}
        for s in syms:
            h = yf.Ticker(s).history(period="3mo")["Close"].dropna()
            h.index = h.index.tz_localize(None)
            frames[s] = h
        df = pd.DataFrame(frames).dropna()
        norm = (df / df.iloc[0]) * 100

        latest = {
            s: {
                "price": round(df[s].iloc[-1], 2),
                "chg_pct": round((df[s].iloc[-1] / df[s].iloc[-2] - 1) * 100, 2),
            }
            for s in syms
        }
        rsp_5d = (df["RSP"].iloc[-1] / df["RSP"].iloc[-5] - 1) * 100
        spy_5d = (df["SPY"].iloc[-1] / df["SPY"].iloc[-5] - 1) * 100
        return {
            "normalized": norm.tail(60),
            "latest": latest,
            "rsp_vs_spy": round(rsp_5d - spy_5d, 2),
        }
    except Exception:
        return None


@st.cache_data(ttl=28800, show_spinner=False)
def get_credit():
    """信用市場：HYG / JNK + FRED 高收益利差"""
    try:
        syms = ["HYG", "JNK"]
        frames = {}
        for s in syms:
            h = yf.Ticker(s).history(period="3mo")["Close"].dropna()
            h.index = h.index.tz_localize(None)
            frames[s] = h
        df = pd.DataFrame(frames).dropna()
        norm = (df / df.iloc[0]) * 100

        latest = {
            s: {
                "price": round(df[s].iloc[-1], 2),
                "chg_pct": round((df[s].iloc[-1] / df[s].iloc[-2] - 1) * 100, 2),
            }
            for s in syms
        }
        hyg_5d = round((df["HYG"].iloc[-1] / df["HYG"].iloc[-5] - 1) * 100, 2)

        # FRED 高收益利差（單位：%，例如 3.5 = 350 bps）
        # 若網路無法連線 FRED，以 HYG/TLT 相對表現作備用指標
        spread_cur = spread_chg = spread_hist = None
        try:
            fred_r = requests.get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8,
            )
            from io import StringIO
            sdf = pd.read_csv(StringIO(fred_r.text))
            col = sdf.columns[-1]  # 第二欄即利差值
            sdf = sdf[sdf[col] != "."].copy()
            sdf[col] = pd.to_numeric(sdf[col])
            sdf = sdf.tail(90).reset_index(drop=True)
            spread_cur = float(sdf[col].iloc[-1])
            spread_chg = round(spread_cur - float(sdf[col].iloc[-6]), 2)
            spread_hist = sdf.rename(columns={sdf.columns[0]: "DATE", col: "SPREAD"})
        except Exception:
            pass  # FRED 不可達時，dashboard 仍可顯示 HYG/JNK 資料

        return {
            "normalized": norm.tail(60),
            "latest": latest,
            "hyg_5d": hyg_5d,
            "spread": spread_cur,
            "spread_chg": spread_chg,
            "spread_hist": spread_hist,
        }
    except Exception:
        return None


@st.cache_data(ttl=28800, show_spinner=False)
def get_cross_assets():
    """跨資產：10Y美債殖利率 / 黃金 / 美元"""
    try:
        sym_names = {"^TNX": "10Y殖利率", "GLD": "黃金", "UUP": "美元"}
        frames = {}
        for s in sym_names:
            h = yf.Ticker(s).history(period="3mo")["Close"].dropna()
            h.index = h.index.tz_localize(None)
            frames[s] = h
        df = pd.DataFrame(frames).dropna()
        norm = (df / df.iloc[0]) * 100

        latest = {
            s: {
                "name": sym_names[s],
                "price": round(df[s].iloc[-1], 2),
                "chg_pct": round((df[s].iloc[-1] / df[s].iloc[-2] - 1) * 100, 2),
            }
            for s in sym_names
        }
        return {"normalized": norm.tail(60), "latest": latest}
    except Exception:
        return None


# ── 訊號判讀邏輯 ────────────────────────────────────────────

def vix_zone(v):
    if v < 12:
        return "極度自滿（減倉警示）", "#f59e0b"
    if v < 18:
        return "平靜 / 市場自滿", "#f59e0b"
    if v < 25:
        return "波動升溫", "#f97316"
    if v < 30:
        return "情緒緊張", "#ef4444"
    return "極度恐慌（等待回落確認）", "#dc2626"


def fg_label(s):
    if s < 25:
        return "極度恐懼", "#22c55e"
    if s < 45:
        return "恐懼", "#86efac"
    if s < 55:
        return "中性", "#f59e0b"
    if s < 75:
        return "貪婪", "#f97316"
    return "極度貪婪", "#ef4444"


def calc_recommendation(vix_cur, fg_score, rsp_vs_spy, hyg_5d, spread, tnx_chg=None, gld_chg=None, uup_chg=None):
    """綜合評分 → 加倉/觀望/減倉建議"""
    score = 0
    crisis = False

    if vix_cur is not None:
        if vix_cur > 40:       score += 3   # 極度恐慌，補最後子彈
        elif vix_cur > 30:     score += 2   # 恐慌，第二批加倉
        elif vix_cur > 25:     score += 1   # 情緒緊張，試水第一批
        elif vix_cur < 12:     score -= 3
        elif vix_cur < 15:     score -= 2

    if fg_score is not None:
        if fg_score < 15:      score += 3   # 極度恐懼最深層，強烈逆向買入
        elif fg_score < 25:    score += 2   # 極度恐懼
        elif fg_score > 75:    score -= 2

    if rsp_vs_spy is not None:
        if rsp_vs_spy > 0.5:
            score += 1
        elif rsp_vs_spy < -1:
            score -= 1

    if spread is not None and spread > 6:
        crisis = True
    elif hyg_5d is not None:
        if hyg_5d < -2:
            score -= 2
        elif hyg_5d >= 0:
            score += 1

    if tnx_chg is not None and gld_chg is not None:
        if tnx_chg > 0.5 and gld_chg > 0.5:
            score -= 1   # 殖利率↑+黃金↑ = 通膨/貨幣信用危機疑慮

    if uup_chg is not None and uup_chg > 1:
        score -= 1   # 美元暴漲 = 全球資金避險，非美資產承壓

    if crisis:
        return ("🚨 危機警告", "#dc2626",
                "高收益利差 > 6%，系統性危機風險！信用市場惡化，切勿急於抄底")
    if score >= 3:
        return ("✅ 加倉機會", "#22c55e",
                "多個訊號確認恐慌底部，可依 VIX 區間分批佈局（30%-30%-40%）")
    if score >= 1:
        return ("🟡 偏向加倉", "#84cc16",
                "訊號偏正面，可小量進場，保留後續加倉空間")
    if score <= -3:
        return ("🔴 考慮減倉", "#ef4444",
                "市場過度樂觀，估值偏高，可減倉或轉向核心資產、賣 Covered Call")
    if score <= -1:
        return ("🟠 偏向謹慎", "#f97316",
                "部分訊號偏負面，不宜大幅加碼，注意風險")
    return ("⏸️ 觀望", "#f59e0b",
            "訊號混合，等待更明確方向")


# ── 通用小圖表 ──────────────────────────────────────────────

def mini_chart(series_dict: dict, height=140, h_lines=None):
    """輸入 {標籤: (series, color)}，返回 Plotly 圖表"""
    fig = go.Figure()
    for label, (series, color) in series_dict.items():
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values,
            name=label, mode="lines",
            line=dict(color=color, width=2),
        ))
    if h_lines:
        for y_val, dash, color, text in h_lines:
            fig.add_hline(y=y_val, line_dash=dash, line_color=color,
                          annotation_text=text, annotation_font_size=10)
    fig.update_layout(
        height=height,
        margin=dict(l=4, r=4, t=4, b=4),
        legend=dict(orientation="h", y=1.12, font=dict(size=11)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(gridcolor="rgba(255,255,255,0.07)"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.07)"),
    )
    return fig


# ── 主程式 ──────────────────────────────────────────────────

def main():
    now = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M")

    # 標題列
    col_h, col_r = st.columns([6, 1])
    with col_h:
        st.title("📊 美股加減倉決策儀表板", anchor=False)
        st.caption(f"台北時間 {now}　｜　每 8 小時自動更新")
    with col_r:
        st.write("")
        st.write("")
        if st.button("🔄 立即更新", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    st.divider()

    # 平行載入所有資料
    with st.spinner("載入市場資料…"):
        vix      = get_vix()
        fg       = get_fear_greed()
        breadth  = get_market_breadth()
        credit   = get_credit()
        cross    = get_cross_assets()

    # ────────────────────────────────────────────────────────
    # 第一行：VIX ／ 恐懼貪婪 ／ 市場廣度
    # ────────────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)

    # ── VIX ──
    with c1:
        _t, _i = st.columns([10, 2], vertical_alignment="center")
        _t.subheader("😱 VIX 恐慌指數", anchor=False)
        with _i:
            with st.popover("ℹ️"):
                st.markdown("""
**VIX 恐慌指數**　衡量市場對未來 30 天波動的預期。

| 區間 | 狀態 |
|------|------|
| < 12 | 極度自滿，減倉警示 |
| 12–18 | 平靜 |
| 18–25 | 波動升溫 |
| 25–30 | 情緒緊張 |
| 30+ | 極度恐慌 |

⚠️ VIX 衝高不等於可以進場，需等 VIX **從高點回落**才是底部確認。
""")
        if vix:
            zone_lbl, zone_color = vix_zone(vix["current"])
            st.metric(
                "目前數值",
                vix["current"],
                f"{vix['change']:+.2f}  ({vix['change_pct']:+.2f}%)",
            )
            st.markdown(
                f"<span style='color:{zone_color}; font-weight:bold; font-size:1.05rem'>"
                f"▌ {zone_lbl}</span>",
                unsafe_allow_html=True,
            )
            if vix["current"] > 30:
                st.success("加倉參考：VIX 從高點回落時往往是底部，可分批佈局")
            elif vix["current"] < 12:
                st.warning("減倉警示：市場極度自滿，風險溢價消失，強烈建議減倉")
            elif vix["current"] < 15:
                st.warning("減倉參考：市場自滿，估值偏高，考慮減倉")
            else:
                st.info("正常波動區間，持續觀察")

            fig = mini_chart(
                {"VIX": (vix["history"], zone_color)},
                h_lines=[
                    (30, "dash", "#ef4444", "30 極恐慌"),
                    (15, "dash", "#f59e0b", "15 過樂觀"),
                ],
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("VIX 資料載入失敗，請稍後重試")

    # ── 恐懼貪婪指數 ──
    with c2:
        _t, _i = st.columns([10, 2], vertical_alignment="center")
        _t.subheader("📊 恐懼貪婪指數（CNN）", anchor=False)
        with _i:
            with st.popover("ℹ️"):
                st.markdown("""
**恐懼與貪婪指數**　CNN 綜合 7 個子指標（動能、廣度、期權、垃圾債需求等）計算，0–100 分。

| 分數 | 狀態 |
|------|------|
| 0–24 | 極度恐懼 |
| 25–49 | 恐懼 |
| 50 | 中性 |
| 51–74 | 貪婪 |
| 75–100 | 極度貪婪 |

🟢 **黃金加倉組合**：VIX > 30 且本指數 < 25
🔴 **黃金減倉組合**：VIX < 15 且本指數 > 75
""")
        if fg:
            fg_lbl, fg_color = fg_label(fg["score"])

            fig = go.Figure(go.Indicator(
                mode="gauge+number",
                value=fg["score"],
                gauge={
                    "axis": {"range": [0, 100], "tickcolor": "#aaa"},
                    "bar": {"color": fg_color},
                    "bgcolor": "rgba(0,0,0,0)",
                    "steps": [
                        {"range": [0,  25], "color": "rgba(34,197,94,0.25)"},
                        {"range": [25, 45], "color": "rgba(134,239,172,0.15)"},
                        {"range": [45, 55], "color": "rgba(245,158,11,0.15)"},
                        {"range": [55, 75], "color": "rgba(249,115,22,0.15)"},
                        {"range": [75, 100],"color": "rgba(239,68,68,0.25)"},
                    ],
                },
                number={"suffix": " 分", "font": {"color": fg_color, "size": 36}},
                title={"text": fg_lbl, "font": {"color": fg_color, "size": 16}},
            ))
            fig.update_layout(
                height=240,
                margin=dict(l=20, r=20, t=60, b=0),
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)

            # 趨勢比較（昨日/上週/上月）
            parts = []
            if fg.get("prev_1d") is not None and fg["prev_1d"] != fg["score"]:
                parts.append(f"昨日：{fg['prev_1d']}（{fg['score'] - fg['prev_1d']:+.1f}）")
            if fg.get("prev_1w") is not None:
                parts.append(f"上週：{fg['prev_1w']}（{fg['score'] - fg['prev_1w']:+.1f}）")
            if fg.get("prev_1m") is not None:
                parts.append(f"上月：{fg['prev_1m']}（{fg['score'] - fg['prev_1m']:+.1f}）")
            if parts:
                st.caption("　".join(parts))

            if fg["score"] < 25:
                st.success("🎯 黃金組合：若同時 VIX > 30，為絕佳加倉確認點")
            elif fg["score"] > 75:
                st.warning("🎯 黃金組合：若同時 VIX < 15，考慮減倉或賣 Covered Call")
            else:
                st.info(f"CNN 評級：{fg['rating']}")
        else:
            st.error("CNN Fear & Greed API 載入失敗")
            st.caption("手動查詢：edition.cnn.com/markets/fear-and-greed")

    # ── 市場廣度 ──
    with c3:
        _t, _i = st.columns([10, 2], vertical_alignment="center")
        _t.subheader("📐 市場廣度", anchor=False)
        with _i:
            with st.popover("ℹ️"):
                st.markdown("""
**市場廣度**　觀察指數上漲是「少數巨頭撐盤」還是「多股普漲」。

| 指標 | 說明 |
|------|------|
| **SPY** | 市值加權，大型股主導 |
| **RSP** | 等權重，每股同等影響力 |
| **IWM** | 小盤股（Russell 2000）|

- RSP 跑贏 SPY → 廣度健康，多股參與上漲
- RSP 落後 SPY → 底子虛，靠少數巨頭支撐
""")
        if breadth:
            la = breadth["latest"]
            mc1, mc2, mc3 = st.columns(3)
            for col, sym in zip([mc1, mc2, mc3], ["SPY", "RSP", "IWM"]):
                with col:
                    st.metric(sym, f"${la[sym]['price']:.0f}",
                              f"{la[sym]['chg_pct']:+.2f}%")

            rvs = breadth["rsp_vs_spy"]
            if rvs > 0.5:
                st.success(f"廣度健康：RSP 近5日跑贏 SPY {rvs:+.2f}%（多股普漲）")
            elif rvs < -1:
                st.warning(f"廣度虛弱：RSP 近5日落後 SPY {rvs:.2f}%（靠少數巨頭撐盤）")
            else:
                st.info(f"廣度中性：RSP vs SPY 差距 {rvs:+.2f}%")

            norm = breadth["normalized"]
            fig = mini_chart({
                "SPY": (norm["SPY"], "#3b82f6"),
                "RSP": (norm["RSP"], "#22c55e"),
                "IWM": (norm["IWM"], "#f59e0b"),
            })
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("市場廣度資料載入失敗")

    st.divider()

    # ────────────────────────────────────────────────────────
    # 第二行：信用市場 ／ 跨資產聯動
    # ────────────────────────────────────────────────────────
    c4, c5 = st.columns(2)

    # ── 信用市場 ──
    with c4:
        _t, _i = st.columns([10, 2], vertical_alignment="center")
        _t.subheader("🏦 信用市場（高收益債）", anchor=False)
        with _i:
            with st.popover("ℹ️"):
                st.markdown("""
**信用市場**　資金鏈是否健康，比股價更能反映真實危機。

| ETF | 說明 |
|-----|------|
| **HYG / JNK** | 高收益債（垃圾債），反映企業融資壓力 |

**利差（Spread）**：企業債與公債的利率差距，越大代表違約風險越高。

| 利差 | 水位 |
|------|------|
| < 4% | 正常 |
| 4–6% | 偏高，留意 |
| > 6% | 歷史危機水位（2008、2020）|

- 股跌 + 高收益債**穩定** → 健康回調，可加倉
- 股跌 + 高收益債**也跌** → 系統性危機，勿抄底
""")
        if credit:
            la = credit["latest"]
            cc1, cc2 = st.columns(2)
            with cc1:
                st.metric("HYG", f"${la['HYG']['price']}",
                          f"{la['HYG']['chg_pct']:+.2f}%")
            with cc2:
                st.metric("JNK", f"${la['JNK']['price']}",
                          f"{la['JNK']['chg_pct']:+.2f}%")

            # FRED 高收益利差
            if credit["spread"] is not None:
                sp = credit["spread"]
                sp_chg = credit["spread_chg"]
                sp_color = "#ef4444" if sp > 6 else ("#f97316" if sp > 4 else "#22c55e")
                sp_level = "🔴 危機水位（> 6%）" if sp > 6 else ("🟡 偏高（4~6%）" if sp > 4 else "🟢 正常（< 4%）")
                delta_str = f"{sp_chg:+.2f}% vs 5日前" if sp_chg is not None else None
                st.metric("高收益債利差（FRED）", f"{sp:.2f}%", delta_str)
                st.markdown(f"<span style='color:{sp_color}'>{sp_level}</span>",
                            unsafe_allow_html=True)

            hyg_5d = credit["hyg_5d"]
            if hyg_5d < -2:
                st.error(f"⚠️ 信用市場惡化！HYG 近5日 {hyg_5d:.2f}%，系統性風險，勿急於抄底")
            elif hyg_5d >= 0:
                st.success(f"信用市場穩定，HYG 近5日 {hyg_5d:+.2f}%，股跌屬估值回調可加倉")
            else:
                st.info(f"信用市場輕微走弱，HYG 近5日 {hyg_5d:.2f}%，持續觀察")

            norm = credit["normalized"]
            fig = mini_chart({
                "HYG": (norm["HYG"], "#3b82f6"),
                "JNK": (norm["JNK"], "#f97316"),
            })
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("信用市場資料載入失敗")

    # ── 跨資產聯動 ──
    with c5:
        _t, _i = st.columns([10, 2], vertical_alignment="center")
        _t.subheader("🌐 跨資產聯動", anchor=False)
        with _i:
            with st.popover("ℹ️"):
                st.markdown("""
**跨資產聯動**　從美債、美元、黃金的走向判斷市場性質。

| 指標 | 說明 |
|------|------|
| **10Y殖利率** | 美國10年公債利率，漲 = 估值壓力 |
| **黃金（GLD）** | 避險或通膨擔憂溫度計，顯示 ETF 股價 |
| **美元（UUP）** | 美元強弱指數 ETF，漲 = 資金回流美國 |

⚠️ 殖利率↑ + 黃金↑ 同步出現，代表通膨或貨幣信用危機疑慮，留意股市壓力。
""")
        if cross:
            la = cross["latest"]
            xc1, xc2, xc3 = st.columns(3)
            for col, sym in zip([xc1, xc2, xc3], ["^TNX", "GLD", "UUP"]):
                info = la[sym]
                with col:
                    suffix = "%" if sym == "^TNX" else ""
                    st.metric(info["name"],
                              f"{info['price']}{suffix}",
                              f"{info['chg_pct']:+.2f}%")

            tnx_chg = la["^TNX"]["chg_pct"]
            gld_chg = la["GLD"]["chg_pct"]
            uup_chg = la["UUP"]["chg_pct"]

            if tnx_chg > 0.5 and gld_chg > 0.5:
                st.error("🚨 警示：美債殖利率↑ + 黃金↑ → 通膨或貨幣信用危機疑慮，留意股市承壓風險")
            elif uup_chg > 0.5:
                st.warning("美元急漲 → 全球資金避險，等美元回落後再加倉")
            elif tnx_chg > 0.5:
                st.warning("美債殖利率升 → 殺估值（科技股為主），等利率企穩再加倉")
            else:
                st.info("跨資產訊號無特殊警告")

            norm = cross["normalized"]
            fig = mini_chart({
                "10Y殖利率": (norm["^TNX"], "#ef4444"),
                "黃金":      (norm["GLD"],  "#eab308"),
                "美元":      (norm["UUP"],  "#3b82f6"),
            })
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("跨資產資料載入失敗")

    st.divider()

    # ────────────────────────────────────────────────────────
    # 綜合建議
    # ────────────────────────────────────────────────────────
    st.markdown(
        "<div style='display:flex; align-items:baseline; gap:8px; margin-bottom:8px;'>"
        "<span style='font-size:1.5rem; font-weight:700; color:#1e293b;'>🎯 綜合建議</span>"
        "<span style='color:#94a3b8; font-size:0.8rem;'>（僅供參考，非投資建議。）</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    rec_label, rec_color, rec_desc = calc_recommendation(
        vix["current"]                     if vix     else None,
        fg["score"]                        if fg      else None,
        breadth["rsp_vs_spy"]              if breadth else None,
        credit["hyg_5d"]                   if credit  else None,
        credit["spread"]                   if credit  else None,
        cross["latest"]["^TNX"]["chg_pct"] if cross   else None,
        cross["latest"]["GLD"]["chg_pct"]  if cross   else None,
        cross["latest"]["UUP"]["chg_pct"]  if cross   else None,
    )

    # 六階段進度條：當前階段高亮，其餘淡化
    STAGES = [
        ("🚨 危機警告", "#dc2626"),
        ("🔴 考慮減倉", "#ef4444"),
        ("🟠 偏向謹慎", "#f97316"),
        ("⏸️ 觀望",    "#f59e0b"),
        ("🟡 偏向加倉", "#84cc16"),
        ("✅ 加倉機會", "#22c55e"),
    ]
    cards = ""
    for lbl, clr in STAGES:
        if lbl == rec_label:
            style = (
                f"flex:1;text-align:center;padding:12px 4px;border-radius:10px;"
                f"background:{clr}33;border:2px solid {clr};"
                f"color:{clr};font-weight:bold;font-size:0.88rem;"
            )
        else:
            style = (
                "flex:1;text-align:center;padding:12px 4px;border-radius:10px;"
                "background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.12);"
                "color:#888;font-size:0.85rem;"
            )
        cards += f'<div style="{style}">{lbl}</div>'
    st.markdown(
        f'<div style="display:flex;gap:6px;margin:14px 0 10px 0;">{cards}</div>',
        unsafe_allow_html=True,
    )

    # 說明文字
    st.markdown(f"""
    <div style="
        background:{rec_color}22;
        border:2px solid {rec_color};
        border-radius:12px;
        padding:16px 28px;
        text-align:center;
        margin:0 0 18px 0;
    ">
        <div style="font-size:1.2rem; font-weight:700; color:#1d4ed8;">{rec_desc}</div>
    </div>
    """, unsafe_allow_html=True)

    # ────────────────────────────────────────────────────────
    # 折疊區塊
    # ────────────────────────────────────────────────────────
    with st.expander("📖 綜合建議的評分基準"):
        _col_left, _col_right = st.columns(2)
        with _col_left:
            st.markdown("""
**各訊號加減分條件**

| 訊號 | 條件 | 分數 |
|------|------|:----:|
| **VIX 恐慌指數** | VIX > 40（極度恐慌，補最後子彈） | +3 |
| | VIX 30–40（恐慌，第二批加倉） | +2 |
| | VIX 25–30（情緒緊張，試水第一批） | +1 |
| | VIX < 12（極度自滿） | -3 |
| | VIX 12–15（市場自滿） | -2 |
| **恐懼貪婪指數** | F&G < 15（極度恐懼最深層，強烈逆向買入） | +3 |
| | F&G 15–25（極度恐懼） | +2 |
| | F&G > 75（極度貪婪） | -2 |
| **市場廣度** | RSP 近5日跑贏 SPY > 0.5% | +1 |
| | RSP 近5日落後 SPY > 1% | -1 |
| **信用市場** | HYG 近5日 ≥ 0%（信用穩定） | +1 |
| | HYG 近5日 < -2%（信用惡化） | -2 |
| **跨資產** | TNX 單日漲 > 0.5% 且 GLD 單日漲 > 0.5% | -1 |
| | 美元（UUP）單日漲 > 1%（全球資金避險） | -1 |
| **信用利差** | 高收益利差（FRED） > 6% | 直接觸發危機警告 |
            """)
        with _col_right:
            st.markdown("""
**六種建議對應**

<table style="width:100%; border-collapse:collapse; font-size:0.9rem;">
  <thead>
    <tr style="border-bottom:2px solid #e2e8f0;">
      <th style="width:80px; text-align:center; padding:6px 8px; white-space:nowrap;">總分</th>
      <th style="width:110px; padding:6px 8px; white-space:nowrap;">建議</th>
      <th style="padding:6px 8px;">說明</th>
    </tr>
  </thead>
  <tbody>
    <tr style="border-bottom:1px solid #e2e8f0;">
      <td style="text-align:center; padding:6px 8px; white-space:nowrap;">危機觸發</td>
      <td style="padding:6px 8px; white-space:nowrap;">🚨 危機警告</td>
      <td style="padding:6px 8px;">高收益利差 > 6%，系統性危機風險，切勿急於抄底</td>
    </tr>
    <tr style="border-bottom:1px solid #e2e8f0;">
      <td style="text-align:center; padding:6px 8px; white-space:nowrap;">≥ 3</td>
      <td style="padding:6px 8px; white-space:nowrap;">✅ 加倉機會</td>
      <td style="padding:6px 8px;">多訊號確認恐慌底部，可依 VIX 區間分批佈局（30%-30%-40%）</td>
    </tr>
    <tr style="border-bottom:1px solid #e2e8f0;">
      <td style="text-align:center; padding:6px 8px; white-space:nowrap;">1–2</td>
      <td style="padding:6px 8px; white-space:nowrap;">🟡 偏向加倉</td>
      <td style="padding:6px 8px;">訊號偏正面，可小量進場，保留後續加倉空間</td>
    </tr>
    <tr style="border-bottom:1px solid #e2e8f0;">
      <td style="text-align:center; padding:6px 8px; white-space:nowrap;">0</td>
      <td style="padding:6px 8px; white-space:nowrap;">⏸️ 觀望</td>
      <td style="padding:6px 8px;">訊號混合，等待更明確方向</td>
    </tr>
    <tr style="border-bottom:1px solid #e2e8f0;">
      <td style="text-align:center; padding:6px 8px; white-space:nowrap;">-1 至 -2</td>
      <td style="padding:6px 8px; white-space:nowrap;">🟠 偏向謹慎</td>
      <td style="padding:6px 8px;">部分訊號偏負面，不宜大幅加碼，注意風險</td>
    </tr>
    <tr>
      <td style="text-align:center; padding:6px 8px; white-space:nowrap;">≤ -3</td>
      <td style="padding:6px 8px; white-space:nowrap;">🔴 考慮減倉</td>
      <td style="padding:6px 8px;">市場過度樂觀，估值偏高，可減倉或轉向核心資產、賣 Covered Call</td>
    </tr>
  </tbody>
</table>

> **黃金加倉**：VIX > 30 且 F&G < 25
> **黃金減倉**：VIX < 15 且 F&G > 75
            """, unsafe_allow_html=True)

    col_exp1, col_exp2 = st.columns(2)
    with col_exp1:
        with st.expander("📖 VIX 分批加倉策略（30-30-40）"):
            st.markdown("""
| VIX 區間 | 判讀 | 操作 | 倉位比例 |
|---------|------|------|---------|
| 12 – 18 | 市場自滿 | 減倉 / 觀望 | — |
| 18 – 25 | 波動升溫 | 保留現金 | — |
| **25 – 30** | 情緒緊張 | **第一批進場** | **30%** |
| **30 – 40** | 極度恐慌 | **第二批加倉** | **30%** |
| **40+ 後回落** | 恐慌見底 | **第三批確認** | **40%** |

> **黃金加倉組合**：VIX > 30 且 Fear & Greed < 25
> **黃金減倉組合**：VIX < 15 且 Fear & Greed > 75
            """)

    with col_exp2:
        with st.expander("📖 信用市場判讀邏輯"):
            st.markdown("""
| 情境 | 股市 | HYG / JNK | 判斷 | 操作 |
|------|------|-----------|------|------|
| 健康回調 | ↓ | 穩定 | 估值修正 | **可加倉** |
| 系統性危機 | ↓↓ | ↓↓（利差急擴） | 企業違約風險 | **勿抄底** |
| 熊市反彈 | ↑ | 一起↑ | 信心回復 | 持有 |

> **高收益利差 > 6%**（600 bps）= 歷史危機水位，如 2008 GFC、2020 COVID
            """)


if __name__ == "__main__":
    main()
