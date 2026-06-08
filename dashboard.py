# 美股＋台股加減倉決策儀表板
# 美股：VIX / 恐懼貪婪 / 市場廣度 / 信用市場 / 跨資產
# 台股：HV20波動率 / 籌碼面 / 市場廣度 / 台幣資金流 / 費半+美元

import numpy as np
import streamlit as st
import yfinance as yf
import requests
import pandas as pd
import plotly.graph_objects as go
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import os
import re
import json
import pytz
from streamlit_autorefresh import st_autorefresh

# 本機用 .env 讀設定；Streamlit Cloud 無 dotenv 套件/檔案，靜默略過改用 st.secrets
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=r"c:\agent\market-compass\.env")
except Exception:
    pass

# ── 頁面設定 ────────────────────────────────────────────────
st.set_page_config(
    page_title="台股 + 美股加減倉決策儀表板",
    page_icon="📊",
    layout="wide",
)

TW_TZ    = pytz.timezone("Asia/Taipei")
# GIST_ID：本機從 .env，雲端從 Streamlit secrets
GIST_ID  = os.getenv("GIST_ID", "")
if not GIST_ID:
    try:
        GIST_ID = st.secrets.get("GIST_ID", "")
    except Exception:
        GIST_ID = ""

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
            "max_5d": round(float(hist.tail(5).max()), 2),  # 近5日高點，判斷恐慌是否回落
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
                timeout=5,   # FRED 在部分地區會逾時，快速失敗走 fallback
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


@st.cache_data(ttl=28800, show_spinner=False)
def get_spx_trend():
    """S&P 500 趨勢過濾：200日均線（多空總開關）＋ 20日動能"""
    try:
        h = yf.Ticker("^GSPC").history(period="1y")["Close"].dropna()
        if len(h) < 200:
            return None
        cur   = float(h.iloc[-1])
        ma200 = float(h.tail(200).mean())
        return {
            "current":     round(cur, 2),
            "ma200":       round(ma200, 2),
            "above_ma200": cur > ma200,
            "ret_20d":     round((cur / float(h.iloc[-21]) - 1) * 100, 2),
        }
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def _get_coinmetrics_btc():
    """CoinMetrics Community API — BTC 鏈上歷史（每日快取）

    免費層指標：CapMVRVCur / CapMrktCurUSD / CapRealUSD / SplyCur / IssTotNtv / PriceUSD
    拉取 2012-01-01 至今完整序列，供 MVRV Z-Score 的全歷史標準差計算。
    """
    try:
        r = requests.get(
            "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
            params={
                "assets": "btc",
                # CapRealUSD 屬於 CoinMetrics 付費層（免費帳號 403），故移除
                # Z-Score / Realized Price / Realized Ratio 無法計算，顯示「—」
                "metrics": "CapMVRVCur,CapMrktCurUSD,SplyCur,IssTotNtv,PriceUSD",
                "start_time": "2023-01-01",   # Puell 需要 365 日滾動，取近 2 年即可
                "frequency": "1d",
                "page_size": 10000,
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        r.raise_for_status()
        records = r.json().get("data", [])
        if not records:
            return None
        df = pd.DataFrame(records)
        df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
        df = df.set_index("time").sort_index()
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def _get_fred_macro():
    """FRED — 美國 M2 年增率 + Fed 資產負債表 MA4W（每日快取）

    M2SL：美國廣義貨幣供給量（月度），全球 M2 的免費代理，相關性 ~80%。
    WALCL：Fed 總資產（週度，百萬美元）；取 MA4W 過濾 TGA 轉帳雜訊。
    """
    from io import StringIO
    H = {"User-Agent": "Mozilla/5.0"}
    out = dict(m2_yoy=None, m2_expanding=None, m2_history=None,
               fed_bs=None, fed_bs_ma4w=None, fed_bs_expanding=None, fed_bs_history=None)
    try:
        r = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=M2SL&cosd=2019-01-01",
            headers=H, timeout=8)
        df = pd.read_csv(StringIO(r.text))
        df.columns = ["date", "m2"]
        df = df[df["m2"] != "."].copy()
        df["m2"]   = pd.to_numeric(df["m2"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        if len(df) >= 13:
            yoy = (float(df["m2"].iloc[-1]) / float(df["m2"].iloc[-13]) - 1) * 100
            out["m2_yoy"]       = round(yoy, 2)
            out["m2_expanding"] = yoy >= 0
            out["m2_history"]   = df["m2"].tail(60)
    except Exception:
        pass
    try:
        r = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=WALCL&cosd=2022-01-01",
            headers=H, timeout=8)
        df = pd.read_csv(StringIO(r.text))
        df.columns = ["date", "bs"]
        df = df[df["bs"] != "."].copy()
        df["bs"]   = pd.to_numeric(df["bs"]) / 1e6   # 百萬 → 兆美元
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        if len(df) >= 8:
            ma4w_cur  = float(df["bs"].tail(4).mean())
            ma4w_prev = float(df["bs"].tail(8).head(4).mean())
            out["fed_bs"]           = round(float(df["bs"].iloc[-1]), 2)
            out["fed_bs_ma4w"]      = round(ma4w_cur, 2)
            out["fed_bs_expanding"] = ma4w_cur >= ma4w_prev
            out["fed_bs_history"]   = df["bs"].tail(104)
    except Exception:
        pass
    return out


@st.cache_data(ttl=28800, show_spinner=False)
def get_crypto():
    """加密貨幣四層決策指標（免費版，8 小時快取）

    L0 宏觀閘門  ：US M2 YoY（FRED M2SL）+ Fed 資產負債表 MA4W（WALCL）
    L1 加倉面    ：MVRV / MVRV Z-Score / NUPL近似 / Realized Price /
                   Puell Multiple / Mayer Multiple / 加密 F&G / AHR999近似
    L1 減倉面    ：MVRV Z-Score / NUPL近似 / Pi Cycle Top（111DMA vs 350DMA×2）
    L2 衍生品    ：資金費率（%/8h）+ 加密 F&G 高位

    資料來源全數選用美國 IP 友善端點（Streamlit Cloud 部署於美國機房），
    避開會封鎖美國 IP 的幣安/Bybit/OKX 現貨衍生品端點。
    """
    H = {"User-Agent": "Mozilla/5.0"}
    res = dict(
        # L0
        m2_yoy=None, m2_expanding=None,
        fed_bs=None, fed_bs_ma4w=None, fed_bs_expanding=None,
        # L1 加倉面
        mvrv=None, mvrv_zscore=None, nupl_approx=None,
        realized_price=None, realized_ratio=None, puell=None,
        mayer=None, btc_price=None,
        cfg=None, cfg_label=None, cfg_hist=None,
        ahr999_approx=None,
        # L1 減倉面（Pi Cycle Top）
        pi_111dma=None, pi_350x2=None, pi_cross=None, pi_warning=None,
        # L2
        funding=None,
        # 展示輔助
        btc_dom=None, stable_dom=None,
    )

    # FRED + CoinMetrics 使用各自的 86400s 快取；命中後不占本次請求時間
    try:
        fred = _get_fred_macro()
        for k in ("m2_yoy", "m2_expanding", "fed_bs", "fed_bs_ma4w", "fed_bs_expanding"):
            res[k] = fred.get(k)
    except Exception:
        pass

    try:
        cm = _get_coinmetrics_btc()
        if cm is not None and len(cm) >= 365:
            if "CapMVRVCur" in cm.columns:
                s = cm["CapMVRVCur"].dropna()
                if len(s):
                    v = float(s.iloc[-1])
                    res["mvrv"] = round(v, 2)
                    if v > 0:
                        res["nupl_approx"] = round(1 - 1 / v, 3)
            # MVRV Z-Score：(市值 - 已實現市值) / std(歷史市值)
            if "CapMrktCurUSD" in cm.columns and "CapRealUSD" in cm.columns:
                mc = cm["CapMrktCurUSD"].dropna()
                rc = cm["CapRealUSD"].reindex(mc.index).dropna()
                common = mc.index.intersection(rc.index)
                if len(common) >= 200:
                    std = float(mc.loc[common].std())
                    if std > 0:
                        diff = float(mc.loc[common].iloc[-1]) - float(rc.loc[common].iloc[-1])
                        res["mvrv_zscore"] = round(diff / std, 2)
            # Realized Price = 已實現市值 / 流通供應量
            if "CapRealUSD" in cm.columns and "SplyCur" in cm.columns:
                rc = cm["CapRealUSD"].dropna()
                sp = cm["SplyCur"].dropna()
                common = rc.index.intersection(sp.index)
                if len(common):
                    res["realized_price"] = round(
                        float(rc.loc[common].iloc[-1]) / float(sp.loc[common].iloc[-1]), 0)
            # Puell Multiple = 今日礦工收入 / 365日均礦工收入
            if "IssTotNtv" in cm.columns and "PriceUSD" in cm.columns:
                iss = cm["IssTotNtv"].dropna()
                px  = cm["PriceUSD"].dropna()
                common = iss.index.intersection(px.index)
                if len(common) >= 365:
                    rev = iss.loc[common] * px.loc[common]
                    avg = float(rev.tail(365).mean())
                    if avg > 0:
                        res["puell"] = round(float(rev.iloc[-1]) / avg, 2)
    except Exception:
        pass

    # 4 個並行來源（F&G / 資金費率 / yfinance / CoinGecko 主導率）
    def _fng():
        d = requests.get("https://api.alternative.me/fng/?limit=30",
                         headers=H, timeout=10).json()["data"]
        vals = [int(x["value"]) for x in reversed(d)]
        idx  = pd.to_datetime([int(x["timestamp"]) for x in reversed(d)], unit="s")
        return {"cfg": int(d[0]["value"]),
                "cfg_label": d[0].get("value_classification", ""),
                "cfg_hist": pd.Series(vals, index=idx)}

    def _funding():
        # 資金費率（CoinGecko derivatives，Binance BTC 永續優先，中位數備援）
        dv = requests.get("https://api.coingecko.com/api/v3/derivatives",
                          headers=H, timeout=12).json()
        btc_perp = [x for x in dv
                    if x.get("contract_type") == "perpetual"
                    and str(x.get("symbol", "")).upper().startswith("BTC")
                    and x.get("funding_rate") is not None]
        bnb = next((x for x in btc_perp if "Binance" in str(x.get("market", ""))), None)
        if bnb:
            return {"funding": round(float(bnb["funding_rate"]), 4)}
        if btc_perp:
            return {"funding": round(float(np.median([float(x["funding_rate"]) for x in btc_perp])), 4)}
        return {}

    def _yf_btc():
        h = yf.Ticker("BTC-USD").history(period="2y")["Close"].dropna()
        if len(h) < 200:
            return {}
        price = float(h.iloc[-1])
        out = {"btc_price": round(price, 0),
               "mayer":     round(price / float(h.tail(200).mean()), 2)}
        # Pi Cycle Top：111DMA 穿越 350DMA×2 是歷史頂部訊號
        if len(h) >= 350:
            ma111   = float(h.tail(111).mean())
            ma350x2 = float(h.tail(350).mean()) * 2
            gap = (ma350x2 - ma111) / ma350x2
            out.update(pi_111dma=round(ma111, 0), pi_350x2=round(ma350x2, 0),
                       pi_cross=ma111 >= ma350x2, pi_warning=(0 <= gap < 0.05))
        if len(h) >= 120:
            out["_ma120"] = float(h.tail(120).mean())
        return out

    def _dominance():
        mc = requests.get("https://api.coingecko.com/api/v3/global",
                          headers=H, timeout=10).json()["data"]["market_cap_percentage"]
        return {"btc_dom":    round(mc.get("btc", 0), 1),
                "stable_dom": round(mc.get("usdt", 0) + mc.get("usdc", 0) + mc.get("dai", 0), 1)}

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(fn) for fn in (_fng, _funding, _yf_btc, _dominance)]
        for f in futures:
            try:
                res.update(f.result())
            except Exception:
                pass

    # 派生指標（需要 btc_price + realized_price + _ma120）
    ma120 = res.pop("_ma120", None)
    if res["btc_price"] and res["realized_price"]:
        res["realized_ratio"] = round(res["btc_price"] / res["realized_price"], 3)
        if ma120:
            res["ahr999_approx"] = round(
                (res["btc_price"] / ma120) * (res["btc_price"] / res["realized_price"]), 3)

    return res


# ── 台股資料抓取 ─────────────────────────────────────────────

@st.cache_data(ttl=28800, show_spinner=False)
def get_tw_vix_proxy():
    """^TWII 20日年化歷史波動率（HV20）作為台指VIX代理"""
    try:
        hist = yf.Ticker("^TWII").history(period="3mo")["Close"].dropna()
        if len(hist) < 22:
            return None
        log_ret = np.log(hist / hist.shift(1)).dropna()
        hv = log_ret.rolling(20).std() * (252 ** 0.5) * 100
        hv = hv.dropna()
        if len(hv) < 2:
            return None
        hist.index = hist.index.tz_localize(None)
        hv.index = hv.index.tz_localize(None)
        cur, prev = float(hv.iloc[-1]), float(hv.iloc[-2])
        return {
            "hv20":       round(cur, 2),
            "hv20_prev":  round(prev, 2),
            "falling":    cur < prev,           # True = 波動收斂（加倉訊號）
            "hv_history": hv.tail(60),
            "twii_cur":   round(float(hist.iloc[-1]), 0),
            "twii_chg_pct": round((float(hist.iloc[-1]) / float(hist.iloc[-2]) - 1) * 100, 2),
            "twii_history": hist.tail(60),
        }
    except Exception:
        return None


@st.cache_data(ttl=28800, show_spinner=False)
def get_tw_chips():
    """外資臺指期淨部位（TAIFEX OpenAPI）"""

    result = {
        "foreign_net_oi":    None,  # 最新外資淨部位（正=多，負=空）
        "foreign_3d_change": None,  # 近3交易日累計變動
    }

    # ── TAIFEX OpenAPI：外資臺股期貨淨未平倉口數 ──
    try:
        r = requests.get(
            "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=12,
        )
        records = r.json()
        for rec in records:
            if rec.get("ContractCode") == "臺股期貨" and rec.get("Item") == "外資及陸資":
                result["foreign_net_oi"] = int(rec["OpenInterest(Net)"])
                break
    except Exception:
        pass

    return result


@st.cache_data(ttl=28800, show_spinner=False)
def get_tw_margin_history():
    """融資 + 融券餘額歷史（TWSE MI_MARGN，近20個交易日）

    MI_MARGN 格式：
    - tables[0]：融資統計，最後列合計，最後欄 = 融資餘額（仟元）
    - tables[1]：融券統計，最後列合計，「餘額金額」欄 = 融券餘額（仟元）
    同一次請求同時取兩個表，避免重複呼叫 API。
    """

    def fetch_both(date_str):
        """查單日融資 + 融券餘額（仟元 → 億元）；無資料回傳 (None, None)"""
        try:
            r = requests.get(
                f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date_str}&response=json",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            data = r.json()
            if data.get("stat") != "OK":
                return None, None
            tables = data.get("tables", [])

            # 融資：tables[0] 最後列最後欄 = 融資餘額（仟元）
            margin_bal = None
            if tables:
                rows = tables[0].get("data", [])
                if rows:
                    margin_bal = round(int(str(rows[-1][-1]).replace(",", "")) / 100000, 2)

            # 融券：tables[1] 最後列，尋找「餘額金額」欄位；找不到用倒數第2欄
            short_bal = None
            if len(tables) >= 2:
                tbl = tables[1]
                rows = tbl.get("data", [])
                if rows:
                    fields = tbl.get("fields", [])
                    col = next((i for i, f in enumerate(fields) if "餘額金額" in str(f)), -2)
                    short_bal = round(int(str(rows[-1][col]).replace(",", "")) / 100000, 2)

            return margin_bal, short_bal
        except Exception:
            return None, None

    # 往前找最多 30 個日曆日，取最近 20 個有效交易日
    margin_history, short_history = {}, {}
    for delta in range(0, 31):
        d = datetime.now(TW_TZ) - timedelta(days=delta)
        label = d.strftime("%m/%d")
        m_bal, s_bal = fetch_both(d.strftime("%Y%m%d"))
        if m_bal is not None:
            margin_history[label] = m_bal
        if s_bal is not None:
            short_history[label] = s_bal
        if len(margin_history) >= 20 and len(short_history) >= 20:
            break

    if not margin_history:
        return None

    # 融資 Series（由舊到新）
    margin_series = pd.Series(dict(reversed(list(margin_history.items()))))
    chg_pct = None
    if len(margin_series) >= 2:
        t, p = float(margin_series.iloc[-1]), float(margin_series.iloc[-2])
        if p > 0:
            chg_pct = round((t / p - 1) * 100, 3)

    # 融券 Series + 5日變動
    short_series, short_chg_5d, short_latest = None, None, None
    if short_history:
        short_series = pd.Series(dict(reversed(list(short_history.items()))))
        short_latest = round(float(short_series.iloc[-1]), 0)
        if len(short_series) >= 6:
            t5, p5 = float(short_series.iloc[-1]), float(short_series.iloc[-5])
            if p5 > 0:
                short_chg_5d = round((t5 / p5 - 1) * 100, 2)

    return {
        "series":       margin_series,
        "chg_pct":      chg_pct,
        "latest":       round(float(margin_series.iloc[-1]), 0),
        "short_series": short_series,   # 融券餘額歷史
        "short_latest": short_latest,   # 融券最新值（億元）
        "short_chg_5d": short_chg_5d,  # 融券近5日變動%（逆向指標）
    }


@st.cache_data(ttl=28800, show_spinner=False)
def get_tw_institutional_cash():
    """三大法人現股買賣超（TWSE BFI82U，外資 + 投信，近3個交易日）

    BFI82U 格式：每列一個法人，欄位 [單位名稱, 買進金額, 賣出金額, 買賣差額]（元）
    需逐日查詢（dayDate=YYYYMMDD），最多往前找7個日曆日取3個交易日。
    """
    result = {
        "foreign_net_3d": None,
        "sitc_net_3d":    None,
        "foreign_net_1d": None,
        "sitc_net_1d":    None,
        "display_rows":   [],
    }

    def parse_yi(s):
        """字串金額（元）→ 億元"""
        try:
            return round(int(str(s).replace(",", "")) / 1e8, 2)
        except Exception:
            return None

    def fetch_day(date_str):
        """查單日資料，回傳 (foreign_億元, sitc_億元)；無資料回傳 (None, None)"""
        try:
            r = requests.get(
                f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={date_str}&response=json",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=12,
            )
            data = r.json()
            if data.get("stat") != "OK":
                return None, None
            rows = data.get("data", [])
            foreign, sitc = None, None
            for row in rows:
                name = str(row[0])
                if "外資及陸資" in name and "不含" in name:  # 排除「外資自營商」獨立列
                    foreign = parse_yi(row[3])
                elif "投信" == name.strip():
                    sitc = parse_yi(row[3])
            return foreign, sitc
        except Exception:
            return None, None

    # 從今天往前找，最多7個日曆日，取最近3個有資料的交易日
    fetched = []
    for delta in range(0, 8):
        d = (datetime.now(TW_TZ) - timedelta(days=delta)).strftime("%Y%m%d")
        label = (datetime.now(TW_TZ) - timedelta(days=delta)).strftime("%m/%d")
        foreign, sitc = fetch_day(d)
        if foreign is not None or sitc is not None:
            fetched.append({"date": label, "foreign": foreign, "sitc": sitc})
            if len(fetched) >= 3:
                break

    if not fetched:
        return result

    result["foreign_net_1d"] = fetched[0]["foreign"]
    result["sitc_net_1d"]    = fetched[0]["sitc"]

    f_vals = [row["foreign"] for row in fetched if row["foreign"] is not None]
    s_vals = [row["sitc"]    for row in fetched if row["sitc"]    is not None]
    if f_vals:
        result["foreign_net_3d"] = round(sum(f_vals), 2)
    if s_vals:
        result["sitc_net_3d"] = round(sum(s_vals), 2)

    result["display_rows"] = fetched
    return result


@st.cache_data(ttl=28800, show_spinner=False)
def get_tw_market_breadth():
    """台股市場廣度：^TWII（加權）vs ^TWOII（櫃買）vs 2330.TW（台積電）

    台積電佔加權指數約 30%，其相對強弱補足 OTC vs TAIEX 的盲點：
    若加權漲但台積電大幅落後，代表廣度問題比 OTC 數字顯示的更嚴重。
    2330.TW 資料抓取失敗不影響主要廣度計算。
    """
    try:
        frames = {}
        for s in ["^TWII", "^TWOII"]:
            h = yf.Ticker(s).history(period="3mo")["Close"].dropna()
            h.index = h.index.tz_localize(None)
            frames[s] = h
        df = pd.DataFrame(frames).dropna()
        if len(df) < 6:
            return None

        # 台積電單獨抓取，失敗不影響主要指標
        try:
            h_tsmc = yf.Ticker("2330.TW").history(period="3mo")["Close"].dropna()
            h_tsmc.index = h_tsmc.index.tz_localize(None)
            # 只取與主表對齊的日期
            h_tsmc = h_tsmc.reindex(df.index)
            df["2330.TW"] = h_tsmc
        except Exception:
            pass

        norm = (df / df.iloc[0]) * 100
        taiex_5d = (df["^TWII"].iloc[-1]  / df["^TWII"].iloc[-5]  - 1) * 100
        otc_5d   = (df["^TWOII"].iloc[-1] / df["^TWOII"].iloc[-5] - 1) * 100
        taiex_3d = (df["^TWII"].iloc[-1]  / df["^TWII"].iloc[-3]  - 1) * 100

        # 台積電相對強弱（可能為 None）
        tsmc_cur = tsmc_chg_pct = tsmc_vs_taiex_5d = None
        if "2330.TW" in df.columns:
            tsmc_series = df["2330.TW"].dropna()
            if len(tsmc_series) >= 6:
                tsmc_cur       = round(float(tsmc_series.iloc[-1]), 0)
                tsmc_chg_pct   = round((float(tsmc_series.iloc[-1]) / float(tsmc_series.iloc[-2]) - 1) * 100, 2)
                tsmc_5d        = (float(tsmc_series.iloc[-1]) / float(tsmc_series.iloc[-5]) - 1) * 100
                tsmc_vs_taiex_5d = round(tsmc_5d - taiex_5d, 2)

        return {
            "normalized":        norm[["^TWII", "^TWOII"]].tail(60),
            "tsmc_normalized":   norm["2330.TW"].tail(60) if "2330.TW" in norm.columns else None,
            "taiex_cur":         round(float(df["^TWII"].iloc[-1]), 0),
            "taiex_chg_pct":     round((float(df["^TWII"].iloc[-1])  / float(df["^TWII"].iloc[-2])  - 1) * 100, 2),
            "otc_cur":           round(float(df["^TWOII"].iloc[-1]), 2),
            "otc_chg_pct":       round((float(df["^TWOII"].iloc[-1]) / float(df["^TWOII"].iloc[-2]) - 1) * 100, 2),
            "otc_vs_taiex_5d":   round(otc_5d - taiex_5d, 2),
            "taiex_3d":          round(taiex_3d, 2),
            "tsmc_cur":          tsmc_cur,
            "tsmc_chg_pct":      tsmc_chg_pct,
            "tsmc_vs_taiex_5d":  tsmc_vs_taiex_5d,
        }
    except Exception:
        return None


@st.cache_data(ttl=28800, show_spinner=False)
def get_tw_credit():
    """新台幣匯率（USD/TWD）作為台股資金流動性代理"""
    try:
        h = yf.Ticker("TWD=X").history(period="3mo")["Close"].dropna()
        h.index = h.index.tz_localize(None)
        if len(h) < 6:
            return None
        cur, prev = float(h.iloc[-1]), float(h.iloc[-2])
        # 正值 = 台幣貶（USD/TWD 上升）；負值 = 台幣升
        twd_5d = (cur / float(h.iloc[-5]) - 1) * 100
        twd_3d = (cur / float(h.iloc[-3]) - 1) * 100
        return {
            "usdtwd":    round(cur, 3),
            "chg_pct":   round((cur / prev - 1) * 100, 3),
            "twd_5d":    round(twd_5d, 3),
            "twd_3d":    round(twd_3d, 3),
            "history":   h.tail(60),
        }
    except Exception:
        return None


@st.cache_data(ttl=28800, show_spinner=False)
def get_tw_cross_assets():
    """跨資產：費城半導體指數（SOX）+ 美元指數（DXY）"""
    try:
        frames = {}
        for s in ["^SOX", "DX-Y.NYB"]:
            h = yf.Ticker(s).history(period="6mo")["Close"].dropna()
            h.index = h.index.tz_localize(None)
            frames[s] = h
        df = pd.DataFrame(frames).dropna()
        if len(df) < 60:
            return None
        norm = (df / df.iloc[0]) * 100
        sox_cur  = float(df["^SOX"].iloc[-1])
        sox_ma60 = float(df["^SOX"].tail(60).mean())
        dxy_5d   = (float(df["DX-Y.NYB"].iloc[-1]) / float(df["DX-Y.NYB"].iloc[-5]) - 1) * 100
        return {
            "normalized":    norm.tail(60),
            "sox_cur":       round(sox_cur, 2),
            "sox_chg_pct":   round((sox_cur / float(df["^SOX"].iloc[-2]) - 1) * 100, 2),
            "sox_ma60":      round(sox_ma60, 2),
            "sox_below_ma60": sox_cur < sox_ma60,
            "dxy_cur":       round(float(df["DX-Y.NYB"].iloc[-1]), 2),
            "dxy_chg_pct":   round((float(df["DX-Y.NYB"].iloc[-1]) / float(df["DX-Y.NYB"].iloc[-2]) - 1) * 100, 2),
            "dxy_5d":        round(dxy_5d, 2),
        }
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def get_tw_macro_indicators():
    """景氣對策信號燈（國發會，月更） — 含重試機制"""
    import time
    LIGHT_INFO = {
        1: ("紅燈",   "景氣過熱", "#ef4444"),
        2: ("黃紅燈", "景氣趨熱", "#f97316"),
        3: ("綠燈",   "景氣穩定", "#22c55e"),
        4: ("黃藍燈", "景氣趨緩", "#eab308"),
        5: ("藍燈",   "景氣衰退", "#3b82f6"),
    }
    def score_to_light(s):
        if s <= 16: return 5
        if s <= 22: return 4
        if s <= 31: return 3
        if s <= 37: return 2
        return 1

    for attempt in range(3):  # 最多重試 3 次
        try:
            session = requests.Session()
            hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            r = session.get("https://index.ndc.gov.tw/n/zh_tw", headers=hdrs, timeout=15)
            r.raise_for_status()
            m = re.search(r'<meta name="csrf-token" content="([^"]+)"', r.text)
            if not m:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return None
            csrf = m.group(1)
            post_hdrs = {**hdrs, "X-CSRF-TOKEN": csrf,
                         "X-Requested-With": "XMLHttpRequest",
                         "Content-Type": "application/json",
                         "Referer": "https://index.ndc.gov.tw/n/zh_tw"}
            time.sleep(0.5)  # 避免被擋
            r2 = session.post("https://index.ndc.gov.tw/n/json/lightscore",
                              headers=post_hdrs, timeout=15)
            r2.raise_for_status()
            data = r2.json()
            line_data = data.get("line", [])
            if not line_data:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return None
            latest   = line_data[-1]
            score    = latest["y"]
            x        = latest["x"]
            month    = f"{x[:4]}-{x[4:]}"
            light_n  = score_to_light(score)
            name, desc, color = LIGHT_INFO[light_n]
            months  = [f"{d['x'][:4]}-{d['x'][4:]}" for d in line_data]
            scores  = [d["y"] for d in line_data]
            history = pd.Series(scores, index=pd.to_datetime(months))
            return {
                "month": month, "score": score,
                "light_name": name, "light_desc": desc, "light_color": color,
                "history": history, "next_release": data.get("next", ""),
            }
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
    return None


@st.cache_data(ttl=86400, show_spinner=False)
def get_tw_pmi():
    """台灣製造業 PMI（國發會，月更） — 含重試機制"""
    import time
    for attempt in range(3):  # 最多重試 3 次
        try:
            session = requests.Session()
            hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            r = session.get("https://index.ndc.gov.tw/n/zh_tw/PMI", headers=hdrs, timeout=15)
            r.raise_for_status()
            m = re.search(r'<meta name="csrf-token" content="([^"]+)"', r.text)
            if not m:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return None
            csrf = m.group(1)
            post_hdrs = {**hdrs, "X-CSRF-TOKEN": csrf,
                         "X-Requested-With": "XMLHttpRequest",
                         "Content-Type": "application/json",
                         "Referer": "https://index.ndc.gov.tw/n/zh_tw/PMI"}
            time.sleep(0.5)  # 避免被擋
            r2 = session.post("https://index.ndc.gov.tw/n/json/PMI",
                              headers=post_hdrs, timeout=15)
            r2.raise_for_status()
            data = r2.json()
            # key "55" = 製造業PMI(季調值)
            pmi_d = data.get("right", {}).get("55", {}).get("d", [])
            if not pmi_d:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return None
            latest  = pmi_d[-1]
            value   = latest["n"]
            x       = latest["m"]
            month   = f"{x[:4]}-{x[4:]}"
            prev    = pmi_d[-2]["n"] if len(pmi_d) >= 2 else None
            change  = round(value - prev, 1) if prev is not None else None
            expanding = value >= 50
            color   = "#22c55e" if expanding else "#ef4444"
            status  = "擴張" if expanding else "收縮"
            return {
                "value": value, "month": month, "prev": prev,
                "change": change, "status": status, "color": color,
                "next_release": data.get("next", ""),
            }
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
    return None


@st.cache_data(ttl=28800, show_spinner=False)
def get_us_macro():
    """美股宏觀背景（多指標綜合，不影響短線評分）

    資料來源：
      yfinance - 殖利率曲線（^TNX-^IRX, 10Y-3M 利差）、Fed 利率代理（^IRX）、WTI（CL=F）
      BLS API  - 失業率（LNS14000000）、核心 CPI 指數（CUSR0000SA0L1E）
      Sahm Rule 由 UNRATE 月度資料自算
    """
    import json as _json

    def _pack(s, fmt_change=True):
        """打包單一序列的現值、變化、歷史。"""
        if s is None or len(s) < 1:
            return None
        cur  = float(s.iloc[-1])
        prev = float(s.iloc[-2]) if len(s) >= 2 else None
        chg  = round(cur - prev, 2) if (fmt_change and prev is not None) else None
        return {"value": cur, "prev": prev, "change": chg,
                "month": s.index[-1].strftime("%Y-%m-%d"), "history": s}

    def _parse_bls(series_obj):
        """BLS JSON series → 按月排序的 pd.Series（index=月初 Timestamp）"""
        rows = []
        for d in series_obj.get("data", []):
            if not d["period"].startswith("M"):
                continue
            try:
                val = float(d["value"])   # BLS 用 "-" 表示暫無資料，跳過
            except (ValueError, TypeError):
                continue
            rows.append((pd.Timestamp(int(d["year"]), int(d["period"][1:]), 1), val))
        rows.sort()
        if not rows:
            return None
        dates, vals = zip(*rows)
        return pd.Series(list(vals), index=list(dates))

    try:
        # ── 1. yfinance：一次批量下載殖利率 + 油價 ──
        import yfinance as yf
        yf_raw = yf.download(["^TNX", "^IRX", "CL=F"], period="2y",
                              progress=False, auto_adjust=True)
        yf_close = yf_raw["Close"] if "Close" in yf_raw.columns.get_level_values(0) else yf_raw
        curve_s = ffr_s = wti_s = None
        if "^TNX" in yf_close.columns and "^IRX" in yf_close.columns:
            spread = (yf_close["^TNX"] - yf_close["^IRX"]).dropna()
            curve_s = spread.tail(400) if not spread.empty else None
        if "^IRX" in yf_close.columns:
            ffr_raw = yf_close["^IRX"].dropna()
            ffr_s = ffr_raw.tail(60) if not ffr_raw.empty else None
        if "CL=F" in yf_close.columns:
            wti_raw = yf_close["CL=F"].dropna()
            wti_s = wti_raw.tail(400) if not wti_raw.empty else None

        # ── 2. BLS：一次查詢拿失業率 + 核心 CPI ──
        bls_resp = requests.post(
            "https://api.bls.gov/publicAPI/v1/timeseries/data/",
            data=_json.dumps({"seriesid": ["LNS14000000", "CUSR0000SA0L1E"], "lastn": 36}),
            headers={"Content-type": "application/json"},
            timeout=15,
        )
        unrate_raw = cpi_raw = None
        for s in bls_resp.json().get("Results", {}).get("series", []):
            parsed = _parse_bls(s)
            if s["seriesID"] == "LNS14000000":
                unrate_raw = parsed
            elif s["seriesID"] == "CUSR0000SA0L1E":
                cpi_raw = parsed

        unrate_s = unrate_raw.tail(60) if unrate_raw is not None else None

        # Sahm Rule 自算：3M移動均值 − 過去12個月內最低3M均值（≥0.5 = 衰退確認）
        sahm_s = None
        if unrate_raw is not None and len(unrate_raw) >= 15:
            u3m = unrate_raw.rolling(3).mean()
            sahm_calc = u3m - u3m.rolling(12).min()
            sahm_s = sahm_calc.dropna().tail(60)

        # 核心通膨 YoY（BLS 指數 → 年增率）
        cpi_yoy = None
        if cpi_raw is not None and len(cpi_raw) >= 13:
            yoy = (cpi_raw.iloc[-1] / cpi_raw.iloc[-13] - 1) * 100
            yoy_hist = (cpi_raw / cpi_raw.shift(12) - 1) * 100
            cpi_yoy = {"value": round(float(yoy), 2),
                       "month": cpi_raw.index[-1].strftime("%Y-%m"),
                       "history": yoy_hist.dropna()}

        ind = {
            "curve":   _pack(curve_s),
            "sahm":    _pack(sahm_s,  fmt_change=False),
            "unrate":  _pack(unrate_s),
            "cpi_yoy": cpi_yoy,
            "ffr":     _pack(ffr_s),
            "wti":     _pack(wti_s),
        }

        if all(v is None for v in ind.values()):
            raise RuntimeError("宏觀指標全部取得失敗")

        # ── 綜合「宏觀燈號」：分數越高＝對高估值股越不利（緊縮/風險）──
        score, notes = 0, []
        if cpi_yoy is not None:
            v = cpi_yoy["value"]
            if   v >= 4:   score += 2; notes.append(f"核心通膨 {v}%（高，Fed 難降息）")
            elif v >= 3:   score += 1; notes.append(f"核心通膨 {v}%（偏高）")
            elif v < 2.5:  score -= 1; notes.append(f"核心通膨 {v}%（溫和）")
        if ind["curve"] is not None and ind["curve"]["value"] < 0:
            score += 1; notes.append(f"殖利率曲線倒掛 {ind['curve']['value']:.2f}%（衰退預警）")
        if ind["wti"] is not None and ind["wti"]["value"] >= 90:
            score += 1; notes.append(f"油價 ${ind['wti']['value']:.0f}（供給通膨壓力）")

        sahm_triggered = ind["sahm"] is not None and ind["sahm"]["value"] >= 0.5
        if sahm_triggered:
            light_name, light_desc, light_color = "衰退確認", "Sahm Rule 已觸發", "#dc2626"
            macro_bullish = False
        elif score >= 3:
            light_name, light_desc, light_color = "明顯緊縮", "高估值股承壓", "#ef4444"
            macro_bullish = False
        elif score >= 1:
            light_name, light_desc, light_color = "偏緊", "流動性偏向收緊", "#f97316"
            macro_bullish = False
        elif score <= -1:
            light_name, light_desc, light_color = "偏寬鬆", "利於風險資產", "#22c55e"
            macro_bullish = True
        else:
            light_name, light_desc, light_color = "中性", "無明顯方向", "#eab308"
            macro_bullish = True

        return {
            "indicators": ind, "score": score,
            "light_name": light_name, "light_desc": light_desc,
            "light_color": light_color, "macro_bullish": macro_bullish,
            "sahm_triggered": sahm_triggered, "notes": notes,
        }
    except RuntimeError:
        raise          # 全失敗：往外傳，st.cache_data 不快取，下次重抓
    except Exception as _e:
        import traceback as _tb
        print(f"[get_us_macro ERROR] {_e}\n{_tb.format_exc()}", flush=True)
        return None    # 非預期錯誤：回 None，避免整頁壞掉


@st.cache_data(ttl=86400, show_spinner=False)
def get_tw_export_orders():
    """電子產品外銷訂單年增率（經濟部，月更） — 含重試機制"""
    import time
    from io import StringIO

    for attempt in range(3):  # 最多重試 3 次
        try:
            hdrs = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            }
            r = requests.get(
                "https://service.moea.gov.tw/EE521/common/Common.aspx?code=B&no=9",
                headers=hdrs, timeout=15)
            r.raise_for_status()

            # 改善 HTML 解析：嘗試多個表格位置
            tables = pd.read_html(StringIO(r.text))
            if not tables:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return None

            df = tables[0]
            # 動態列名（防止網站改版）
            if len(df.columns) < 3:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return None

            # 取前幾列作為欄名
            if pd.isna(df.iloc[0, 0]):
                df.columns = df.iloc[0]
                df = df.iloc[1:].reset_index(drop=True)

            df_clean = df.copy()
            df_clean.columns = ["year", "month_raw"] + list(df_clean.columns[2:])
            df_clean["year"] = df_clean["year"].ffill()

            # 去掉 NaN 與累計行
            df2 = df_clean.dropna(subset=[df_clean.columns[2]])  # 第一個數值欄
            if df2.empty:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return None

            df2 = df2[~df2["month_raw"].astype(str).str.contains(r"-\d", na=False)]
            df2 = df2.copy()

            # 提取年份和月份
            df2["roc"] = df2["year"].apply(
                lambda x: int(re.findall(r"\d+", str(x))[0]) if re.findall(r"\d+", str(x)) else None)
            df2["mon"] = df2["month_raw"].apply(
                lambda x: int(re.findall(r"\d+", str(x))[0]) if re.findall(r"\d+", str(x)) else None)

            # 提取數值（嘗試第一個數值欄）
            val_col = df_clean.columns[2]
            df2["val"] = pd.to_numeric(
                df2[val_col].astype(str).str.replace(r"\s+|%|,", "", regex=True),
                errors="coerce")

            df2 = df2.dropna(subset=["roc", "mon", "val"])
            if df2.empty:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return None

            last = df2.iloc[-1]
            ad_year  = int(last["roc"]) + 1911
            month    = f"{ad_year}-{int(last['mon']):02d}"
            value    = float(last["val"])

            # 12 個月歷史
            history_dates = [f"{int(r)+1911}-{int(m):02d}" for r, m in zip(df2["roc"], df2["mon"])]
            history = pd.Series(df2["val"].values, index=pd.to_datetime(history_dates))
            color   = "#22c55e" if value >= 0 else "#ef4444"

            return {
                "value": round(value, 1), "month": month,
                "history": history.tail(12), "color": color,
            }
        except Exception as e:
            if attempt < 2:
                time.sleep(1)

    return None


# ── 每日建議歷史（從 GitHub Gist 讀取）─────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def load_rec_history():
    """從 GitHub Gist 讀取每日建議歷史（TTL=1小時，公開 Gist 無需 token）"""
    gist_id = GIST_ID
    if not gist_id:
        return []
    try:
        r = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            timeout=10,
        )
        r.raise_for_status()
        content = r.json()["files"]["rec_history.json"]["content"]
        return json.loads(content)
    except Exception:
        return []


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


def calc_recommendation(vix_cur, fg_score, rsp_vs_spy, hyg_5d, spread,
                        tnx_chg=None, gld_chg=None, uup_chg=None,
                        vix_5d_max=None, spx_above_ma200=None, spx_20d_ret=None):
    """綜合評分 → 加倉/觀望/減倉建議"""
    score = 0
    crisis = False

    if vix_cur is not None:
        # 逆向加倉分須「VIX 高且已從近期高點回落」才算恐慌見頂；仍在飆升只給半分
        rolling_over = vix_5d_max is not None and vix_cur < vix_5d_max * 0.9
        if vix_cur > 40:       score += 3 if rolling_over else 1   # 極度恐慌
        elif vix_cur > 30:     score += 2 if rolling_over else 1   # 恐慌
        elif vix_cur > 25:     score += 1 if rolling_over else 0   # 情緒緊張
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

    # 動能確認：20日仍明顯下跌代表趨勢未止穩，壓抑樂觀
    if spx_20d_ret is not None and spx_20d_ret < -6:
        score -= 1

    # 200日均線總開關：價在均線下＝結構性空頭，逆向加倉分打折並加警示
    if spx_above_ma200 is False:
        if score > 0:
            score //= 2          # 把「買恐慌」的樂觀分數砍半
        score -= 2               # 空頭趨勢警示

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


def calc_crypto_recommendation(c):
    """免費版四層加密評分 → 加倉/觀望/減倉建議（回傳 5-tuple）

    回傳：(label, color, desc, buy_score_adj, sell_score)
    減倉面優先：先計算 sell；若觸發減倉，加倉面直接暫停。
    L0 US M2 YoY 收縮時，加倉有效分乘以 0.5。

    六段標籤（由空到滿）：
      ✅ 重倉加倉  →  🟢 階梯加倉  →  🟡 基礎定投
      ⏸️ 觀望
      🟠 偏向謹慎  →  🔴 考慮減倉
    """
    mvrv       = c.get("mvrv")
    mvrv_z     = c.get("mvrv_zscore")
    nupl       = c.get("nupl_approx")
    rp_ratio   = c.get("realized_ratio")
    puell      = c.get("puell")
    mayer      = c.get("mayer")
    cfg        = c.get("cfg")
    ahr999     = c.get("ahr999_approx")
    funding    = c.get("funding")
    pi_cross   = c.get("pi_cross")
    pi_warning = c.get("pi_warning")
    m2_expand  = c.get("m2_expanding")

    # L0：M2 收縮時加倉訊號打折（宏觀逆風）
    m2_mult = 0.5 if (m2_expand is not None and not m2_expand) else 1.0

    # === L1 加倉面（理論滿分 15 分）===
    buy = 0.0
    if mvrv is not None:
        if mvrv <= 1.0:    buy += 2
        elif mvrv <= 1.2:  buy += 1
    if mvrv_z is not None:
        if mvrv_z <= 0.1:   buy += 2
        elif mvrv_z <= 0.5: buy += 1
    if nupl is not None:
        if nupl <= 0:       buy += 2
        elif nupl <= 0.25:  buy += 1
    if rp_ratio is not None:
        if rp_ratio <= 1.0:    buy += 2
        elif rp_ratio <= 1.08: buy += 1
    if puell is not None:
        if puell <= 0.5:   buy += 2
        elif puell <= 0.8: buy += 1
    if mayer is not None:
        if mayer <= 0.8:   buy += 2
        elif mayer <= 1.0: buy += 1
    if cfg is not None:
        if cfg < 15:   buy += 2
        elif cfg < 25: buy += 1
    if ahr999 is not None:
        if ahr999 <= 0.45:  buy += 1
        elif ahr999 <= 1.2: buy += 0.5

    buy_adj = buy * m2_mult

    # === L1/L2 減倉面（理論滿分 10 分）===
    sell = 0
    if mvrv_z is not None:
        if mvrv_z >= 6.0:   sell += 2
        elif mvrv_z >= 4.0: sell += 1
    if nupl is not None:
        if nupl >= 0.75:   sell += 2
        elif nupl >= 0.5:  sell += 1
    if pi_cross:           sell += 2
    elif pi_warning:       sell += 1
    if cfg is not None:
        if cfg >= 85:   sell += 2
        elif cfg >= 80: sell += 1
    fr_ann = (funding * 3 * 365 * 100) if funding is not None else None
    if fr_ann is not None:
        if fr_ann >= 50:   sell += 2
        elif fr_ann >= 30: sell += 1

    # === 決策（減倉優先）===
    m2_tag = f"（L0 ×{m2_mult}）" if m2_mult < 1.0 else ""
    if sell >= 8:
        return ("🔴 考慮減倉", "#ef4444",
                "頂部共振警告（減倉≥8分），建議大幅減倉或清空槓桿，需動能確認後執行",
                buy_adj, sell)
    if sell >= 5:
        return ("🟠 偏向謹慎", "#f97316",
                "明顯過熱（減倉5-7分），停止加倉，分批減倉 1/3，需動能確認",
                buy_adj, sell)
    if sell >= 3:
        return ("⏸️ 觀望", "#94a3b8",
                "部分指標偏熱（減倉3-4分），暫停加倉，等待指標冷卻",
                buy_adj, sell)
    if buy_adj >= 9:
        gate = fr_ann is None or fr_ann < 30
        if gate:
            return ("✅ 重倉加倉", "#22c55e",
                    f"歷史級低估共振（加倉≥9分{m2_tag}），啟動備用抄底資金，需動能確認 + 費率年化<30%",
                    buy_adj, sell)
        return ("🟢 階梯加倉", "#84cc16",
                f"低估但費率過熱（年化>{fr_ann:.0f}%），等待槓桿清洗後再重倉",
                buy_adj, sell)
    if buy_adj >= 6:
        return ("🟢 階梯加倉", "#84cc16",
                f"明顯低估（加倉6-8分{m2_tag}），在支撐位分批買入",
                buy_adj, sell)
    if buy_adj >= 3:
        return ("🟡 基礎定投", "#eab308",
                f"偏低估（加倉3-5分{m2_tag}），持續定投，不擇時",
                buy_adj, sell)
    return ("⏸️ 觀望", "#94a3b8",
            f"訊號不足（加倉{buy_adj:.1f}分{m2_tag}），等待更多指標進入低估區間",
            buy_adj, sell)


# ── 台股訊號判讀 ─────────────────────────────────────────────

def hv20_zone(v):
    if v < 12:  return "極度平靜（過熱警示）",           "#f59e0b"
    if v < 20:  return "常態波動區",                    "#22c55e"
    if v < 28:  return "波動升溫，情緒緊張",              "#f97316"
    return "高度波動（觀察趨勢方向決定加倉時機）",          "#dc2626"


def calc_tw_recommendation(
    hv20, hv20_falling,
    foreign_net_oi, foreign_3d_change,
    margin_chg_pct,
    otc_vs_taiex_5d,
    twd_5d, twd_3d,
    sox_below_ma60, dxy_5d,
    taiex_3d,
    foreign_cash_3d=None,
    sitc_3d=None,
    short_chg_5d=None,
    tsmc_vs_taiex_5d=None,
):
    """台股綜合評分 → 加倉/觀望/減倉建議"""
    score, crisis = 0, False

    # ── 終極危機：台幣3日急貶 + TAIEX 3日急殺 ──
    if twd_3d is not None and taiex_3d is not None:
        if twd_3d > 1.5 and taiex_3d < -5:
            crisis = True

    # ── HV20 波動率（代理台指VIX）──
    if hv20 is not None:
        if hv20 > 28:
            score += 3 if hv20_falling else 1
        elif hv20 > 20:
            score += 1
        elif hv20 < 12:
            score -= 3

    # ── 融資金額日變動（代理融資維持率方向）──
    if margin_chg_pct is not None:
        if margin_chg_pct < -0.5:
            score += 1
        elif margin_chg_pct > 0.5:
            score -= 1

    # ── 融券餘額（逆向指標：空方過擠 → 潛在軋空）──
    if short_chg_5d is not None and short_chg_5d > 3:
        score += 1

    # ── 外資期貨淨部位 ──
    if foreign_net_oi is not None and foreign_net_oi < -30000:
        score -= 2
    if foreign_3d_change is not None and foreign_3d_change > 10000:
        score += 1

    # ── 市場廣度：OTC vs TAIEX ──
    if otc_vs_taiex_5d is not None:
        if otc_vs_taiex_5d > 1:
            score += 1
        elif otc_vs_taiex_5d < -1.5:
            score -= 1

    # ── 台積電相對強弱（廣度升級：最大權值股方向確認）──
    if tsmc_vs_taiex_5d is not None:
        if tsmc_vs_taiex_5d > 2:
            score += 1   # 台積電領漲，半導體訊號確認
        elif tsmc_vs_taiex_5d < -2:
            score -= 1   # 台積電落後，最大權值股轉弱

    # ── 資金流動性：台幣匯率（正值=台幣貶）──
    if twd_5d is not None:
        if twd_5d > 1:
            score -= 2
        elif twd_5d < -0.5:
            score += 1

    # ── 外資現股買賣超（連3日方向）──
    if foreign_cash_3d is not None:
        if foreign_cash_3d > 100:
            score += 1    # 持續買入，資金流入確認
        elif foreign_cash_3d < -200:
            score -= 2    # 大量撤資
        elif foreign_cash_3d < -100:
            score -= 1    # 偏向出場

    # ── 投信現股（護盤訊號）──
    if sitc_3d is not None and sitc_3d > 30:
        score += 1

    # ── 跨資產：SOX破季線 + DXY急漲 ──
    if sox_below_ma60 and dxy_5d is not None and dxy_5d > 1.5:
        score -= 1

    if crisis:
        return ("🚨 系統性危機警告", "#dc2626",
                "新台幣失控貶值且市場急殺（強制斷頭潮訊號），先保留現金，切勿急著抄底")
    if score >= 3:
        return ("✅ 強烈加倉機會", "#22c55e",
                "多項指標共振確認市場超賣，為中長期極佳買點，可依 3-3-4 策略分批布局")
    if score >= 1:
        return ("🟡 偏向加倉", "#84cc16",
                "市場出現回調但資金未失控，可依 3-3-4 策略分批進場，保留後續加倉空間")
    if score <= -3:
        return ("🔴 考慮減倉/避險", "#ef4444",
                "市場極度自滿、融資過高，應逢高獲利了結，保留現金")
    if score <= -1:
        return ("🟠 偏向謹慎", "#f97316",
                "市場結構轉弱（如拉積盤），建議縮減槓桿，不追高")
    return ("⏸️ 觀望", "#f59e0b",
            "多空訊號交織，或市場處於常態區，建議靜待轉折")


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


@st.cache_data(ttl=3600, show_spinner=False)
def load_index_price(market):
    """抓指數股價歷史作為背景走勢線（tw=^TWII 加權, us=^GSPC S&P500, crypto=BTC-USD）"""
    ticker = {"tw": "^TWII", "us": "^GSPC", "crypto": "BTC-USD"}.get(market, "^GSPC")
    try:
        h = yf.Ticker(ticker).history(period="3mo")["Close"].dropna()
        return ([d.strftime("%Y-%m-%d") for d in h.index],
                [round(float(v), 2) for v in h.values])
    except Exception:
        return [], []


def _render_history_section(history, market):
    """指數股價走勢線 + 建議標記 + 緊湊表格（market='tw' / 'us' / 'crypto'）"""
    index_name = {"tw": "加權指數 (TAIEX)", "us": "S&P 500", "crypto": "比特幣 (BTC)"}[market]
    label_key  = f"{market}_label"
    color_key  = f"{market}_color"
    index_key  = f"{market}_index"

    valid = [e for e in history if e.get(index_key) is not None]

    st.markdown(
        "<div style='font-size:1.1rem;font-weight:700;margin:8px 0 10px'>📅 歷史建議紀錄</div>",
        unsafe_allow_html=True,
    )

    if not valid:
        st.info("尚無歷史紀錄，alert_worker 執行後將自動累積")
        return

    dates  = [e["date"] for e in valid]
    labels = [e.get(label_key, "—") for e in valid]
    colors = [e.get(color_key, "#888") for e in valid]

    fig = go.Figure()

    # 指數真實股價走勢（背景連續線）
    px_dates, px_vals = load_index_price(market)
    if px_dates:
        fig.add_trace(go.Scatter(
            x=px_dates, y=px_vals, mode="lines",
            line=dict(color="#3b82f6", width=1.6),
            name=index_name, showlegend=False,
            hovertemplate=f"%{{x}}<br>{index_name} %{{y:,.0f}}<extra></extra>",
        ))

    # 建議標記疊在股價線上：對齊到當天真實收盤價
    px_map = dict(zip(px_dates, px_vals))
    grouped = {}
    for d, lbl, clr in zip(dates, labels, colors):
        y = px_map.get(d)              # 優先用真實收盤，讓點落在線上
        if y is None:                  # 該日尚無股價（如假日）→ 用紀錄的指數值
            y = next((e[index_key] for e in valid if e["date"] == d), None)
        if y is None:
            continue
        grouped.setdefault(lbl, {"x": [], "y": [], "color": clr})
        grouped[lbl]["x"].append(d)
        grouped[lbl]["y"].append(y)

    for lbl, data in grouped.items():
        # 圖例名稱去掉開頭 emoji（如 🟠），避免和 Plotly 圓點重複成雙圓
        legend_name = lbl.split(" ", 1)[-1] if " " in lbl else lbl
        fig.add_trace(go.Scatter(
            x=data["x"], y=data["y"],
            mode="markers", name=legend_name,
            marker=dict(
                color=data["color"], size=13, symbol="circle",
                line=dict(color="white", width=1.5),  # 白框讓點在線上更醒目
            ),
            hovertemplate=(
                f"<b>%{{x}}</b><br>{index_name}: %{{y:,.0f}}<br>"
                f"<b>{lbl}</b><extra></extra>"
            ),
        ))

    fig.update_layout(
        height=300,
        margin=dict(l=4, r=4, t=6, b=4),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(
            orientation="h", y=-0.22, font=dict(size=11),
            bgcolor="rgba(0,0,0,0)", itemsizing="constant",
        ),
        xaxis=dict(gridcolor="rgba(128,128,128,0.15)",
                   tickformat="%m/%d", tickfont=dict(size=10)),
        yaxis=dict(gridcolor="rgba(128,128,128,0.15)", tickformat=",.0f"),
        hovermode="closest",
    )
    st.plotly_chart(fig, use_container_width=True)

    # 緊湊表格（最新在上）
    ix_col  = {"tw": "加權指數", "us": "S&P 500", "crypto": "比特幣"}[market]
    rows_html = ""
    for e in reversed(valid):
        c   = e.get(color_key, "#888")
        lbl = e.get(label_key, "—")
        ix  = e.get(index_key)
        ix_str = f"{ix:,.0f}" if ix is not None else "—"
        rows_html += (
            f"<tr style='border-bottom:1px solid rgba(255,255,255,0.06)'>"
            f"<td style='padding:6px 12px;font-size:0.82rem;opacity:0.55;white-space:nowrap'>{e['date']}</td>"
            f"<td style='padding:6px 12px'>"
            f"<span style='background:{c}22;color:{c};border:1px solid {c}55;"
            f"border-radius:8px;padding:3px 10px;font-size:0.80rem;font-weight:600;"
            f"white-space:nowrap'>{lbl}</span></td>"
            f"<td style='padding:6px 12px;font-size:0.82rem;opacity:0.65;white-space:nowrap'>{ix_str}</td>"
            f"</tr>"
        )
    st.markdown(
        f"<table style='width:100%;border-collapse:collapse'>"
        f"<thead><tr style='border-bottom:2px solid rgba(255,255,255,0.12)'>"
        f"<th style='padding:6px 12px;text-align:left;font-size:0.75rem;opacity:0.4;font-weight:600'>日期</th>"
        f"<th style='padding:6px 12px;text-align:left;font-size:0.75rem;opacity:0.4;font-weight:600'>建議</th>"
        f"<th style='padding:6px 12px;text-align:left;font-size:0.75rem;opacity:0.4;font-weight:600'>{ix_col}</th>"
        f"</tr></thead><tbody>{rows_html}</tbody></table>",
        unsafe_allow_html=True,
    )


def _parallel_fetch(jobs):
    """並行執行多個無參數抓取函式，回傳 {名稱: 結果}；個別失敗回 None。

    各 get_*() 互相獨立，並行可把冷啟動從「全部相加」降到「最慢的那一個」。
    會把當前 Streamlit 執行緒 context 傳給子執行緒，確保 @st.cache_data 正常運作。
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
    ctx = get_script_run_ctx()

    def _run(fn):
        add_script_run_ctx(threading.current_thread(), ctx)
        try:
            return fn()
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as ex:
        futures = {k: ex.submit(_run, fn) for k, fn in jobs.items()}
        return {k: f.result() for k, f in futures.items()}


def _quad_card(is_active, bg_rgba_base, border_color_hex, label, title_color, title_text, body_text):
    """背離 2×2 矩陣格子 HTML；active 格子加重顯示（台股與美股共用）"""
    if is_active:
        bg      = bg_rgba_base.replace("0.15", "0.30")
        border  = f"2px solid {border_color_hex}"
        badge   = (f"<span style='float:right;background:{border_color_hex};color:#fff;"
                   f"font-size:0.62rem;padding:2px 7px;border-radius:10px;font-weight:bold;margin-top:1px'>"
                   f"▶ 當前</span>")
        dim     = ""
    else:
        bg      = bg_rgba_base
        border  = f"1px solid {border_color_hex}66"
        badge   = ""
        dim     = "opacity:0.4;"
    return (
        f"<div style='{dim}background:{bg};border:{border};"
        f"border-radius:8px;padding:12px'>"
        f"<div style='font-size:0.72rem;opacity:0.55;margin-bottom:4px'>{label}{badge}</div>"
        f"<div style='color:{title_color};font-weight:bold;font-size:0.9rem'>{title_text}</div>"
        f"<div style='font-size:0.8rem;margin-top:5px'>{body_text}</div></div>"
    )


def _us_macro_mini_line(hist, color, hlines=None):
    """美股宏觀指標的小型折線圖（緊湊版，無圖例）"""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist.values, mode="lines",
        line=dict(color=color, width=2),
    ))
    for y_val, y_c, y_lbl in (hlines or []):
        fig.add_hline(y=y_val, line_dash="dot", line_color=y_c,
                      annotation_text=y_lbl, annotation_font_size=9)
    fig.update_layout(
        height=120, margin=dict(l=2, r=2, t=6, b=2),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        xaxis=dict(gridcolor="rgba(128,128,128,0.2)", tickformat="%m/%y",
                   tickfont=dict(size=9)),
        yaxis=dict(gridcolor="rgba(128,128,128,0.2)", tickfont=dict(size=9)),
    )
    return fig


def _us_macro_card(icon, title, value_str, status_txt, color, sub_txt):
    """美股宏觀單一指標卡片 HTML"""
    return (
        f"<div style='background:var(--secondary-background-color);border-left:4px solid {color};"
        f"border-radius:8px;padding:12px 16px'>"
        f"<div style='color:{color};font-weight:bold;font-size:0.82rem;margin-bottom:6px'>{icon} {title}</div>"
        f"<div style='font-size:1.9rem;font-weight:bold;line-height:1.1'>{value_str}</div>"
        f"<div style='color:{color};font-size:0.88rem;font-weight:600;margin:4px 0'>{status_txt}</div>"
        f"<div style='font-size:0.75rem;opacity:0.5'>{sub_txt}</div></div>"
    )


def _render_us_macro_section(us_macro, rec_label):
    """美股宏觀背景 expander：綜合燈號 + 6 指標卡片 + 背離 2×2 矩陣"""
    with st.expander("🌐 美股宏觀背景（利率/通膨/景氣循環，不影響短線評分）", expanded=False):
        st.markdown(
            "<div style='background:var(--secondary-background-color);border-left:3px solid #888;"
            "border-radius:8px;padding:9px 16px;margin-bottom:14px;font-size:0.88rem'>"
            "🕐 <b>宏觀指標看的是利率與通膨大環境</b>，市場通常領先景氣循環。"
            "　宏觀不告訴你「什麼時候買」，而是告訴你「這次下跌可能有多深、需要多久復原」。</div>",
            unsafe_allow_html=True,
        )

        if not us_macro:
            st.markdown(
                "<div style='background:var(--secondary-background-color);border-left:4px solid #888;"
                "border-radius:8px;padding:12px 16px'>"
                "<div style='font-weight:bold;font-size:0.82rem;margin-bottom:6px'>🌐 美股宏觀背景</div>"
                "<div style='font-size:0.85rem;opacity:0.55'>宏觀資料取得失敗，請稍後重試</div></div>",
                unsafe_allow_html=True,
            )
            st.link_button("前往 FRED 經濟數據", "https://fred.stlouisfed.org/")
            return

        ind = us_macro["indicators"]

        # ── 綜合「宏觀燈號」橫幅（緊縮 ↔ 寬鬆）──
        lc = us_macro["light_color"]
        notes_txt = "　".join(us_macro["notes"]) if us_macro["notes"] else "目前無明顯緊縮/寬鬆訊號"
        st.markdown(
            f"<div style='background:var(--secondary-background-color);border-left:5px solid {lc};"
            f"border-radius:8px;padding:12px 18px;margin-bottom:14px'>"
            f"<span style='color:{lc};font-weight:bold;font-size:1.15rem'>🚦 宏觀燈號：{us_macro['light_name']}</span>"
            f"<span style='color:{lc};font-size:0.9rem;margin-left:8px'>· {us_macro['light_desc']}</span>"
            f"<div style='font-size:0.8rem;opacity:0.65;margin-top:6px'>{notes_txt}</div></div>",
            unsafe_allow_html=True,
        )

        # ── 第一排：殖利率曲線 / Sahm Rule / 核心通膨 ──
        r1c1, r1c2, r1c3 = st.columns(3)

        with r1c1:  # 殖利率曲線（衰退預警）
            d = ind["curve"]
            if d:
                inverted = d["value"] < 0
                c = "#ef4444" if inverted else "#22c55e"
                status = "倒掛（衰退預警）" if inverted else "正斜率（正常）"
                st.markdown(_us_macro_card(
                    "📐", "殖利率曲線 10Y-3M", f"{d['value']:+.2f}%", status, c,
                    f"最新：{d['month']}", ), unsafe_allow_html=True)
                st.plotly_chart(_us_macro_mini_line(d["history"], c,
                    [(0, "#ef4444", "0 倒掛線")]), use_container_width=True)
            else:
                st.markdown(_us_macro_card("📐", "殖利率曲線 10Y-3M", "—", "資料取得失敗", "#888", ""),
                            unsafe_allow_html=True)

        with r1c2:  # Sahm Rule（衰退確認）
            d = ind["sahm"]
            if d:
                triggered = d["value"] >= 0.5
                c = "#dc2626" if triggered else "#22c55e"
                status = "已觸發（衰退確認）" if triggered else "未觸發（未衰退）"
                st.markdown(_us_macro_card(
                    "🔔", "Sahm Rule 衰退指標", f"{d['value']:.2f}", status, c,
                    f"最新：{d['month']} · 門檻 0.5", ), unsafe_allow_html=True)
                st.plotly_chart(_us_macro_mini_line(d["history"], c,
                    [(0.5, "#dc2626", "0.5 觸發")]), use_container_width=True)
            else:
                st.markdown(_us_macro_card("🔔", "Sahm Rule 衰退指標", "—", "資料取得失敗", "#888", ""),
                            unsafe_allow_html=True)

        with r1c3:  # 核心通膨（Fed 能否降息）
            d = ind["cpi_yoy"]
            if d:
                v = d["value"]
                c = "#ef4444" if v >= 3 else ("#22c55e" if v < 2.5 else "#eab308")
                status = ("偏高，Fed 難降息" if v >= 3 else
                          "溫和，降息空間大" if v < 2.5 else "中性")
                st.markdown(_us_macro_card(
                    "🔥", "核心通膨 CPI（年增）", f"{v:.1f}%", status, c,
                    f"最新：{d['month']} · 目標 2%", ), unsafe_allow_html=True)
                st.plotly_chart(_us_macro_mini_line(d["history"], c,
                    [(2, "#22c55e", "2% 目標"), (3, "#ef4444", "3%")]), use_container_width=True)
            else:
                st.markdown(_us_macro_card("🔥", "核心通膨 CPI（年增）", "—", "資料取得失敗", "#888", ""),
                            unsafe_allow_html=True)

        # ── 第二排：失業率 / 聯邦基金利率 / WTI 油價 ──
        r2c1, r2c2, r2c3 = st.columns(3)

        with r2c1:  # 失業率
            d = ind["unrate"]
            if d:
                rising = d["change"] is not None and d["change"] > 0
                c = "#ef4444" if rising else "#22c55e"
                chg_txt = (f"較上月 {'↑' if rising else '↓'} {abs(d['change']):.1f}"
                           if d["change"] is not None else "")
                st.markdown(_us_macro_card(
                    "👷", "失業率", f"{d['value']:.1f}%",
                    "上升中（景氣降溫）" if rising else "持平/下降", c,
                    f"最新：{d['month']} · {chg_txt}", ), unsafe_allow_html=True)
                st.plotly_chart(_us_macro_mini_line(d["history"], c), use_container_width=True)
            else:
                st.markdown(_us_macro_card("👷", "失業率", "—", "資料取得失敗", "#888", ""),
                            unsafe_allow_html=True)

        with r2c2:  # 聯邦基金利率
            d = ind["ffr"]
            if d:
                c = "#3b82f6"
                st.markdown(_us_macro_card(
                    "🏦", "聯邦基金利率", f"{d['value']:.2f}%", "Fed 政策水位", c,
                    f"最新：{d['month']}", ), unsafe_allow_html=True)
                st.plotly_chart(_us_macro_mini_line(d["history"], c), use_container_width=True)
            else:
                st.markdown(_us_macro_card("🏦", "聯邦基金利率", "—", "資料取得失敗", "#888", ""),
                            unsafe_allow_html=True)

        with r2c3:  # WTI 油價
            d = ind["wti"]
            if d:
                hot = d["value"] >= 90
                c = "#ef4444" if hot else "#22c55e"
                status = "偏高（供給通膨壓力）" if hot else "溫和"
                st.markdown(_us_macro_card(
                    "🛢️", "WTI 原油", f"${d['value']:.0f}", status, c,
                    f"最新：{d['month']}", ), unsafe_allow_html=True)
                st.plotly_chart(_us_macro_mini_line(d["history"], c,
                    [(90, "#ef4444", "90")]), use_container_width=True)
            else:
                st.markdown(_us_macro_card("🛢️", "WTI 原油", "—", "資料取得失敗", "#888", ""),
                            unsafe_allow_html=True)

        # ── 背離 2×2 矩陣（宏觀寬鬆/緊縮 × 短線加倉/謹慎）──
        macro_bullish = us_macro["macro_bullish"]
        short_is_buy = "加倉" in rec_label
        short_is_caution = any(k in rec_label for k in ("謹慎", "減倉", "危機"))
        if short_is_buy:
            active_cell = (macro_bullish, True)
        elif short_is_caution:
            active_cell = (macro_bullish, False)
        else:
            active_cell = None  # 觀望，不落入特定象限

        _QUADRANT_NAMES = {
            (True,  True):  "✅ 最強買點",
            (False, True):  "⚡ 真實機會，需更高容忍度",
            (True,  False): "📈 健康修正",
            (False, False): "🛡️ 雙重警示",
        }
        if active_cell is not None:
            macro_str = "宏觀寬鬆" if macro_bullish else "宏觀緊縮"
            short_str = "短線加倉" if short_is_buy else "短線謹慎"
            banner_html = (
                f"<div style='background:var(--secondary-background-color);border-left:3px solid #6366f1;"
                f"border-radius:8px;padding:8px 14px;margin:12px 0 8px;font-size:0.85rem'>"
                f"<b>▶ 當前象限</b>：{macro_str}（{us_macro['light_name']}）× {short_str}（{rec_label}）"
                f"→ <b>{_QUADRANT_NAMES[active_cell]}</b></div>"
            )
        else:
            banner_html = (
                "<div style='background:var(--secondary-background-color);border-left:3px solid #94a3b8;"
                "border-radius:8px;padding:8px 14px;margin:12px 0 8px;font-size:0.85rem;opacity:0.7'>"
                "目前短線信號觀望，不落入特定象限</div>"
            )
        st.markdown(
            "<div style='margin:16px 0 4px;font-weight:bold;font-size:0.9rem'>宏觀與短線背離解讀"
            "<span style='color:#94a3b8;font-size:0.78rem;font-weight:normal;margin-left:6px'>"
            "（僅供參考，非投資建議）</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(banner_html, unsafe_allow_html=True)

        d1, d2 = st.columns(2)
        d3, d4 = st.columns(2)
        with d1:
            st.markdown(_quad_card(
                active_cell == (True, True),
                "rgba(34,197,94,0.15)", "#16a34a",
                "宏觀寬鬆 × 短線加倉", "#16a34a",
                "✅ 最強買點", "流動性寬鬆＋市場恐慌，可順勢分批進場",
            ), unsafe_allow_html=True)
        with d2:
            st.markdown(_quad_card(
                active_cell == (False, True),
                "rgba(249,115,22,0.15)", "#c2410c",
                "宏觀緊縮 × 短線加倉", "#c2410c",
                "⚡ 真實機會，需更高容忍度",
                "升息/通膨壓力下抄底，回調可能更深、復原更慢",
            ), unsafe_allow_html=True)
        with d3:
            st.markdown(_quad_card(
                active_cell == (True, False),
                "rgba(234,179,8,0.15)", "#a16207",
                "宏觀寬鬆 × 短線謹慎", "#a16207",
                "📈 健康修正", "寬鬆環境中的回調，可比平時更積極",
            ), unsafe_allow_html=True)
        with d4:
            st.markdown(_quad_card(
                active_cell == (False, False),
                "rgba(239,68,68,0.15)", "#b91c1c",
                "宏觀緊縮 × 短線謹慎", "#b91c1c",
                "🛡️ 雙重警示", "緊縮＋短線轉弱，保持防禦、縮短操作週期",
            ), unsafe_allow_html=True)
        st.markdown(
            "<div style='font-size:0.78rem;opacity:0.5;margin-top:10px'>"
            "💡 宏觀影響你的「心理準備」與「等待時長」，不影響短線評分的進出場方向。<br>"
            "資料來源：yfinance（殖利率/油價）· BLS 美國勞工局（失業率/核心CPI）· 月更/日更。</div>",
            unsafe_allow_html=True,
        )


# ── 主程式 ──────────────────────────────────────────────────

def main():
    now = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M")

    # 標題列
    col_h, col_r = st.columns([6, 1])
    with col_h:
        st.title("📊 加減倉決策儀表板", anchor=False)
        st.caption(f"台北時間 {now}　｜　每 8 小時自動更新")
    with col_r:
        st.write("")
        st.write("")
        if st.button("🔄 立即更新", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    tab_tw, tab_us = st.tabs(["📊 台股", "🇺🇸 美股"])
    tab_crypto = None  # ₿ 加密 Tab 暫時隱藏，等 Realized Cap 資料來源修復後恢復
    rec_history = load_rec_history()

    # ════════════════════════════════════════════════════════
    # 台股 Tab
    # ════════════════════════════════════════════════════════
    with tab_tw:
        with st.spinner("載入台股資料（TAIFEX / TWSE / yfinance）…"):
            _tw = _parallel_fetch({
                "vix": get_tw_vix_proxy,        "chips": get_tw_chips,
                "inst_cash": get_tw_institutional_cash, "margin": get_tw_margin_history,
                "breadth": get_tw_market_breadth, "credit": get_tw_credit,
                "cross": get_tw_cross_assets,    "macro": get_tw_macro_indicators,
                "pmi": get_tw_pmi,               "exports": get_tw_export_orders,
            })
            tw_vix, tw_chips, tw_inst_cash = _tw["vix"], _tw["chips"], _tw["inst_cash"]
            tw_margin, tw_breadth, tw_credit = _tw["margin"], _tw["breadth"], _tw["credit"]
            tw_cross, tw_macro = _tw["cross"], _tw["macro"]
            tw_pmi, tw_exports = _tw["pmi"], _tw["exports"]

        # ── 提前計算短線信號（供宏觀背離矩陣使用）──
        tw_rec_label, tw_rec_color, tw_rec_desc = calc_tw_recommendation(
            hv20              = tw_vix["hv20"]          if tw_vix    else None,
            hv20_falling      = tw_vix["falling"]        if tw_vix    else False,
            foreign_net_oi    = tw_chips.get("foreign_net_oi")    if tw_chips  else None,
            foreign_3d_change = tw_chips.get("foreign_3d_change") if tw_chips  else None,
            margin_chg_pct    = tw_margin["chg_pct"] if tw_margin else None,
            otc_vs_taiex_5d   = tw_breadth["otc_vs_taiex_5d"]     if tw_breadth else None,
            twd_5d            = tw_credit["twd_5d"]      if tw_credit else None,
            twd_3d            = tw_credit["twd_3d"]      if tw_credit else None,
            sox_below_ma60    = tw_cross["sox_below_ma60"] if tw_cross else False,
            dxy_5d            = tw_cross["dxy_5d"]        if tw_cross else None,
            taiex_3d          = tw_breadth["taiex_3d"]    if tw_breadth else None,
            foreign_cash_3d   = tw_inst_cash.get("foreign_net_3d") if tw_inst_cash else None,
            sitc_3d           = tw_inst_cash.get("sitc_net_3d")    if tw_inst_cash else None,
            short_chg_5d      = tw_margin.get("short_chg_5d")      if tw_margin  else None,
            tsmc_vs_taiex_5d  = tw_breadth.get("tsmc_vs_taiex_5d") if tw_breadth else None,
        )

        # ── 宏觀背景（月更，不影響短線評分）──
        with st.expander("🌐 宏觀背景（月更指標，不影響短線評分）", expanded=False):
            st.markdown(
                "<div style='background:var(--secondary-background-color);border-left:3px solid #888;"
                "border-radius:8px;padding:9px 16px;margin-bottom:14px;font-size:0.88rem'>"
                "🕐 <b>宏觀指標為落後指標</b>，市場通常比景氣燈早見底 3–6 個月。"
                "　宏觀不告訴你「什麼時候買」，而是告訴你「這次下跌可能有多深、需要多久復原」。</div>",
                unsafe_allow_html=True,
            )

            # ── 三欄指標卡片 ──
            mc1, mc2, mc3 = st.columns(3)

            # 景氣燈（自動抓取）
            with mc1:
                if tw_macro:
                    c = tw_macro["light_color"]
                    st.markdown(
                        f"<div style='background:var(--secondary-background-color);border-left:4px solid {c};"
                        f"border-radius:8px;padding:12px 16px'>"
                        f"<div style='color:{c};font-weight:bold;font-size:0.82rem;margin-bottom:6px'>🔦 景氣對策信號燈</div>"
                        f"<div style='font-size:2rem;font-weight:bold;line-height:1.1'>{tw_macro['score']}"
                        f"<span style='font-size:0.95rem;opacity:0.55'> 分</span></div>"
                        f"<div style='color:{c};font-size:0.9rem;font-weight:600;margin:4px 0'>"
                        f"{tw_macro['light_name']} · {tw_macro['light_desc']}</div>"
                        f"<div style='font-size:0.75rem;opacity:0.5'>最新月份：{tw_macro['month']}</div></div>",
                        unsafe_allow_html=True,
                    )
                    # 12 個月折線趨勢
                    fig_macro = go.Figure()
                    fig_macro.add_trace(go.Scatter(
                        x=tw_macro["history"].index, y=tw_macro["history"].values,
                        mode="lines+markers", line=dict(color=c, width=2),
                        marker=dict(size=4),
                    ))
                    for y_val, y_c, y_lbl in [(37, "#ef4444", "37"), (31, "#22c55e", "31"), (22, "#3b82f6", "22")]:
                        fig_macro.add_hline(y=y_val, line_dash="dot", line_color=y_c,
                                            annotation_text=y_lbl, annotation_font_size=9)
                    fig_macro.update_layout(
                        height=130, margin=dict(l=2, r=2, t=6, b=2),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        showlegend=False,
                        xaxis=dict(gridcolor="rgba(128,128,128,0.2)", tickformat="%m/%y",
                                   showticklabels=True, tickfont=dict(size=9)),
                        yaxis=dict(gridcolor="rgba(128,128,128,0.2)", range=[0, 50],
                                   tickfont=dict(size=9)),
                    )
                    st.plotly_chart(fig_macro, use_container_width=True)
                else:
                    st.markdown(
                        "<div style='background:var(--secondary-background-color);border-left:4px solid #888;"
                        "border-radius:8px;padding:12px 16px'>"
                        "<div style='font-weight:bold;font-size:0.82rem;margin-bottom:6px'>🔦 景氣對策信號燈</div>"
                        "<div style='font-size:0.85rem;opacity:0.55'>資料取得失敗，請手動查詢</div></div>",
                        unsafe_allow_html=True,
                    )
                    st.link_button("前往國發會景氣指標", "https://index.ndc.gov.tw/n/zh_tw")

            # PMI（自動抓取）
            with mc2:
                if tw_pmi:
                    c_pmi = tw_pmi["color"]
                    chg_txt = (f"{'↑' if tw_pmi['change'] >= 0 else '↓'} {abs(tw_pmi['change']):.1f}"
                               if tw_pmi["change"] is not None else "")
                    st.markdown(
                        f"<div style='background:var(--secondary-background-color);border-left:4px solid {c_pmi};"
                        f"border-radius:8px;padding:12px 16px'>"
                        f"<div style='color:{c_pmi};font-weight:bold;font-size:0.82rem;margin-bottom:6px'>📊 台灣製造業 PMI</div>"
                        f"<div style='font-size:2rem;font-weight:bold;line-height:1.1'>{tw_pmi['value']}"
                        f"<span style='font-size:0.95rem;opacity:0.55'> 點</span></div>"
                        f"<div style='color:{c_pmi};font-size:0.9rem;font-weight:600;margin:4px 0'>"
                        f"{tw_pmi['status']} {'（高於 50）' if tw_pmi['value'] >= 50 else '（低於 50）'}</div>"
                        f"<div style='font-size:0.78rem;opacity:0.55'>"
                        f"較上月 {chg_txt} &nbsp;·&nbsp; {tw_pmi['month']}</div></div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        "<div style='font-size:0.72rem;opacity:0.45;margin-top:6px;padding:0 4px'>"
                        "來源：國發會（S&P Global 季調）·&nbsp;"
                        f"下次發布：{tw_pmi['next_release'][:10] if tw_pmi['next_release'] else '—'}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        "<div style='background:var(--secondary-background-color);border-left:4px solid #888;"
                        "border-radius:8px;padding:12px 16px'>"
                        "<div style='font-weight:bold;font-size:0.82rem;margin-bottom:6px'>📊 台灣製造業 PMI</div>"
                        "<div style='font-size:0.85rem;opacity:0.55'>資料取得失敗，請手動查詢</div></div>",
                        unsafe_allow_html=True,
                    )
                    st.link_button("前往國發會 PMI 頁", "https://index.ndc.gov.tw/n/zh_tw/PMI")

            # 外銷訂單（自動抓取）
            with mc3:
                if tw_exports:
                    c_exp = tw_exports["color"]
                    sign = "+" if tw_exports["value"] >= 0 else ""
                    st.markdown(
                        f"<div style='background:var(--secondary-background-color);border-left:4px solid {c_exp};"
                        f"border-radius:8px;padding:12px 16px'>"
                        f"<div style='color:{c_exp};font-weight:bold;font-size:0.82rem;margin-bottom:6px'>📦 電子產品外銷訂單年增率</div>"
                        f"<div style='font-size:2rem;font-weight:bold;line-height:1.1'>{sign}{tw_exports['value']}"
                        f"<span style='font-size:0.95rem;opacity:0.55'> %</span></div>"
                        f"<div style='color:{c_exp};font-size:0.9rem;font-weight:600;margin:4px 0'>"
                        f"{'年增' if tw_exports['value'] >= 0 else '年減'} · 半導體庫存循環指標</div>"
                        f"<div style='font-size:0.75rem;opacity:0.5'>最新月份：{tw_exports['month']}</div></div>",
                        unsafe_allow_html=True,
                    )
                    fig_exp = go.Figure()
                    fig_exp.add_trace(go.Bar(
                        x=tw_exports["history"].index,
                        y=tw_exports["history"].values,
                        marker_color=[c_exp if v >= 0 else "#ef4444"
                                      for v in tw_exports["history"].values],
                    ))
                    fig_exp.add_hline(y=0, line_color="rgba(128,128,128,0.5)", line_width=1)
                    fig_exp.update_layout(
                        height=130, margin=dict(l=2, r=2, t=6, b=2),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        showlegend=False,
                        xaxis=dict(gridcolor="rgba(128,128,128,0.15)", tickformat="%m/%y",
                                   tickfont=dict(size=9)),
                        yaxis=dict(gridcolor="rgba(128,128,128,0.15)", tickfont=dict(size=9)),
                    )
                    st.plotly_chart(fig_exp, use_container_width=True)
                else:
                    st.markdown(
                        "<div style='background:var(--secondary-background-color);border-left:4px solid #888;"
                        "border-radius:8px;padding:12px 16px'>"
                        "<div style='font-weight:bold;font-size:0.82rem;margin-bottom:6px'>📦 電子產品外銷訂單年增率</div>"
                        "<div style='font-size:0.85rem;opacity:0.55'>資料取得失敗，請手動查詢</div></div>",
                        unsafe_allow_html=True,
                    )
                    st.link_button("前往經濟部統計處", "https://service.moea.gov.tw/EE521/common/Common.aspx?code=B&no=9")

            # ── 背離解讀 2×2 矩陣（自動判斷當前象限）──
            _macro_bullish = (
                tw_macro is not None and
                tw_macro["light_name"] in ("紅燈", "黃紅燈", "綠燈")
            )
            _macro_known = tw_macro is not None
            _short_is_buy = "加倉" in tw_rec_label
            _short_is_caution = any(k in tw_rec_label for k in ("謹慎", "減倉", "危機"))

            # 判斷四格中哪格 active：(宏觀樂觀?, 短線買?)
            if _macro_known and _short_is_buy:
                _active_cell = (_macro_bullish, True)
            elif _macro_known and _short_is_caution:
                _active_cell = (_macro_bullish, False)
            else:
                _active_cell = None  # 觀望或宏觀未知

            # 狀態橫幅
            _QUADRANT_NAMES = {
                (True,  True):  "✅ 最強買點",
                (False, True):  "⚡ 真實機會，需更高容忍度",
                (True,  False): "📈 健康修正",
                (False, False): "🛡️ 雙重警示",
            }
            if _active_cell is not None:
                _macro_str = "宏觀樂觀" if _macro_bullish else "宏觀悲觀"
                _short_str = "短線加倉" if _short_is_buy else "短線謹慎"
                _q_name    = _QUADRANT_NAMES[_active_cell]
                _banner_html = (
                    f"<div style='background:var(--secondary-background-color);border-left:3px solid #6366f1;"
                    f"border-radius:8px;padding:8px 14px;margin:12px 0 8px;font-size:0.85rem'>"
                    f"<b>▶ 當前象限</b>：{_macro_str}（{tw_macro['light_name']}）× {_short_str}（{tw_rec_label}）"
                    f"→ <b>{_q_name}</b></div>"
                )
            else:
                _short_txt = "短線信號觀望" if _macro_known else "宏觀數據未取得"
                _banner_html = (
                    f"<div style='background:var(--secondary-background-color);border-left:3px solid #94a3b8;"
                    f"border-radius:8px;padding:8px 14px;margin:12px 0 8px;font-size:0.85rem;opacity:0.7'>"
                    f"目前 {_short_txt}，不落入特定象限</div>"
                )
            st.markdown(
                "<div style='margin:16px 0 4px;font-weight:bold;font-size:0.9rem'>宏觀與短線背離解讀"
                "<span style='color:#94a3b8;font-size:0.78rem;font-weight:normal;margin-left:6px'>"
                "（僅供參考，非投資建議）</span></div>",
                unsafe_allow_html=True,
            )
            st.markdown(_banner_html, unsafe_allow_html=True)

            d1, d2 = st.columns(2)
            d3, d4 = st.columns(2)
            with d1:
                st.markdown(_quad_card(
                    _active_cell == (True, True),
                    "rgba(34,197,94,0.15)", "#16a34a",
                    "宏觀樂觀 × 短線加倉", "#16a34a",
                    "✅ 最強買點", "牛市回調，可積極執行 3-3-4 策略",
                ), unsafe_allow_html=True)
            with d2:
                st.markdown(_quad_card(
                    _active_cell == (False, True),
                    "rgba(249,115,22,0.15)", "#c2410c",
                    "宏觀悲觀 × 短線加倉", "#c2410c",
                    "⚡ 真實機會，需更高容忍度",
                    "宏觀底部最悲觀，但市場已領先 3–6 個月。可買，但回調更深、復原更慢",
                ), unsafe_allow_html=True)
            with d3:
                st.markdown(_quad_card(
                    _active_cell == (True, False),
                    "rgba(234,179,8,0.15)", "#a16207",
                    "宏觀樂觀 × 短線謹慎", "#a16207",
                    "📈 健康修正", "牛市中的健康回調，可比平時更積極執行",
                ), unsafe_allow_html=True)
            with d4:
                st.markdown(_quad_card(
                    _active_cell == (False, False),
                    "rgba(239,68,68,0.15)", "#b91c1c",
                    "宏觀悲觀 × 短線謹慎", "#b91c1c",
                    "🛡️ 雙重警示", "保持防禦，縮短操作週期",
                ), unsafe_allow_html=True)
            st.markdown(
                "<div style='font-size:0.78rem;opacity:0.5;margin-top:10px'>"
                "💡 宏觀影響你的「心理準備」與「等待時長」，不影響短線評分的進出場方向。</div>",
                unsafe_allow_html=True,
            )

        # ────────────────────────────────────────────────────
        # 第一行：HV20 波動率 / 籌碼面 / 市場廣度
        # ────────────────────────────────────────────────────
        tc1, tc2, tc3 = st.columns(3)

        # ── HV20 波動率（台指 VIX 代理）──
        with tc1:
            _t, _i = st.columns([10, 2], vertical_alignment="center")
            _t.subheader("📈 波動率（HV20）", anchor=False)
            with _i:
                with st.popover("ℹ️"):
                    st.markdown("""
**HV20（20日年化歷史波動率）** 作為台指 VIX 代理。

台指 VIX 原始 API 已無法取得，改用 ^TWII 計算。
數值偏低約 5–10%，門檻已相應校正。

| HV20 | 狀態 |
|------|------|
| < 12% | 極度平靜，過熱警示 |
| 12–20% | 常態波動 |
| 20–28% | 情緒緊張，分批建倉起點 |
| > 28% ↓ | **確認恐慌回落，安全加倉點** |
| > 28% ↑ | 恐慌仍飆升，小量試水 |

⚠️ 代理指標，趨勢方向比絕對值更重要。
""")
            if tw_vix:
                zone_lbl, zone_color = hv20_zone(tw_vix["hv20"])
                trend_icon = "↓ 收斂" if tw_vix["falling"] else "↑ 擴張"
                trend_color = "#22c55e" if tw_vix["falling"] else "#ef4444"
                st.metric(
                    "HV20（代理台指VIX）",
                    f"{tw_vix['hv20']}%",
                    f"{trend_icon}（前日 {tw_vix['hv20_prev']}%）",
                )
                st.markdown(
                    f"<span style='color:{zone_color}; font-weight:bold; font-size:1.05rem'>"
                    f"▌ {zone_lbl}</span>",
                    unsafe_allow_html=True,
                )
                st.metric("加權指數 (^TWII)", f"{tw_vix['twii_cur']:,.0f}",
                          f"{tw_vix['twii_chg_pct']:+.2f}%")
                if tw_vix["hv20"] > 28:
                    if tw_vix["falling"]:
                        st.success("加倉訊號：HV20 > 28% 且已回落，恐慌確認見頂，可開始分批布局")
                    else:
                        st.warning("飛刀警告：HV20 > 28% 且仍上升，僅可試水第一批（30%）")
                elif tw_vix["hv20"] < 12:
                    st.warning("過熱警示：波動極低代表市場極度自滿，不宜追高")
                else:
                    st.info("波動率處於常態區間，持續觀察")
                fig = mini_chart(
                    {"HV20 (%)": (tw_vix["hv_history"], zone_color)},
                    h_lines=[
                        (28, "dash", "#ef4444", "28% 高波動"),
                        (12, "dash", "#f59e0b", "12% 過熱"),
                    ],
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.error("HV20 資料載入失敗")

        # ── 籌碼面：外資期貨 + 融資金額 ──
        with tc2:
            _t, _i = st.columns([10, 2], vertical_alignment="center")
            _t.subheader("🧩 籌碼面", anchor=False)
            with _i:
                with st.popover("ℹ️"):
                    st.markdown("""
**外資臺指期淨部位**（來源：TAIFEX）

| 條件 | 評分 |
|------|------|
| 淨空單 > 30,000 口 | -2（外資強烈看空） |
| 近3日累計回補 > 10,000 口 | +1（外資態度轉向） |

**外資現股買賣超**（來源：TWSE BFI82U）

| 條件 | 評分 |
|------|------|
| 近3日累計買超 > 100億 | +1（資金持續流入） |
| 近3日累計賣超 > 100億 | -1（外資偏向出場） |
| 近3日累計賣超 > 200億 | -2（外資大量撤資） |

**投信現股買賣超**（來源：TWSE BFI82U）

| 條件 | 評分 |
|------|------|
| 近3日累計買超 > 30億 | +1（投信護盤） |

**融資金額日變動**（來源：TWSE，⚠️代理指標）

| 條件 | 評分 |
|------|------|
| 單日減少 > 0.5% | +1（斷頭壓力浮現） |
| 單日增加 > 0.5% | -1（槓桿持續上升） |

融資維持率無法直接取得，改用金額變動方向近似。

**融券餘額（逆向指標）**（來源：TWSE，同 MI_MARGN）

| 條件 | 評分 |
|------|------|
| 近5日增加 > 3% | +1（空方過擠，潛在軋空） |

融券上升代表空方積極佈局；若同時出現 HV20 恐慌訊號，代表空方過度擠擁，是短線偏多的逆向指標。
""")
            _tab1, _tab2, _tab3 = st.tabs(["外資期貨", "三大法人", "融資餘額"])

            # ── tab1：外資臺指期 ──
            with _tab1:
                if tw_chips:
                    oi = tw_chips.get("foreign_net_oi")
                    ch = tw_chips.get("foreign_3d_change")
                    if oi is not None:
                        oi_label = f"淨空 {abs(oi):,} 口" if oi < 0 else f"淨多 {oi:,} 口"
                        st.metric("外資臺指期淨部位", oi_label)
                        if oi < -30000:
                            st.error(f"⚠️ 外資淨空超過 30,000 口（{oi:,} 口），強烈看空警示")
                        elif oi > 0:
                            st.success("外資偏多方，籌碼面支撐")
                        else:
                            st.info(f"外資小幅淨空（{oi:,} 口），尚未達警戒門檻")
                        if ch is not None:
                            ch_label = f"+{ch:,} 口（回補）" if ch > 0 else f"{ch:,} 口（加空）"
                            st.metric("近3日累計變動", ch_label)
                            if ch > 10000:
                                st.success("外資近3日大幅回補，態度轉向，與HV20共振可加倉")
                    else:
                        st.warning("外資期貨資料暫時無法取得（TAIFEX 連線問題）")
                else:
                    st.error("籌碼資料載入失敗")

            # ── tab2：三大法人現股 ──
            with _tab2:
                if tw_inst_cash:
                    f1 = tw_inst_cash.get("foreign_net_1d")
                    f3 = tw_inst_cash.get("foreign_net_3d")
                    s1 = tw_inst_cash.get("sitc_net_1d")
                    s3 = tw_inst_cash.get("sitc_net_3d")
                    ic1, ic2 = st.columns(2)
                    with ic1:
                        if f1 is not None:
                            f_dir = "買超" if f1 >= 0 else "賣超"
                            st.metric("外資現股（今日）",
                                      f"{f_dir} {abs(f1):.0f}億",
                                      f"3日累 {f3:+.0f}億" if f3 is not None else "")
                            if f3 is not None:
                                if f3 > 100:
                                    st.success(f"外資近3日買超 {f3:.0f}億，資金流入")
                                elif f3 < -200:
                                    st.error(f"⚠️ 外資近3日賣超 {abs(f3):.0f}億，大量撤資")
                                elif f3 < -100:
                                    st.warning(f"外資近3日賣超 {abs(f3):.0f}億，偏向出場")
                                else:
                                    st.info(f"外資近3日 {f3:+.0f}億，未達警戒")
                        else:
                            st.info("外資現股資料未取得")
                    with ic2:
                        if s1 is not None:
                            s_dir = "買超" if s1 >= 0 else "賣超"
                            st.metric("投信現股（今日）",
                                      f"{s_dir} {abs(s1):.0f}億",
                                      f"3日累 {s3:+.0f}億" if s3 is not None else "")
                            if s3 is not None:
                                if s3 > 30:
                                    st.success(f"投信近3日買超 {s3:.0f}億，護盤訊號")
                                elif s3 < -15:
                                    st.warning(f"投信近3日賣超 {abs(s3):.0f}億")
                                else:
                                    st.info(f"投信近3日 {s3:+.0f}億")
                        else:
                            st.info("投信資料未取得")
                else:
                    st.warning("三大法人現股資料暫時無法取得（TWSE 連線問題）")

            # ── tab3：融資 + 融券餘額 ──
            with _tab3:
                if tw_margin:
                    # 融資餘額
                    chg = tw_margin["chg_pct"]
                    bal = tw_margin["latest"]
                    delta_str = f"{chg:+.3f}%" if chg is not None else ""
                    st.metric("融資餘額（近20日最新）", f"{bal:,.0f}億", delta_str,
                              help="仟元→億元換算；日變動作為維持率方向代理")
                    if chg is not None:
                        if chg < -0.5:
                            st.success(f"融資餘額減少 {abs(chg):.3f}%，槓桿收縮（斷頭壓力訊號）")
                        elif chg > 0.5:
                            st.warning(f"融資餘額增加 {chg:.3f}%，槓桿持續上升，留意過熱")
                        else:
                            st.info(f"融資餘額日變動 {chg:+.3f}%，平穩")
                    fig = mini_chart(
                        {"融資餘額（億）": (tw_margin["series"], "#a78bfa")},
                        height=120,
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    # 融券餘額（逆向指標）
                    st.divider()
                    st.markdown("**融券餘額（空方籌碼，逆向指標）**")
                    short_latest = tw_margin.get("short_latest")
                    short_5d     = tw_margin.get("short_chg_5d")
                    short_series = tw_margin.get("short_series")
                    if short_latest is not None:
                        delta_s = f"近5日 {short_5d:+.2f}%" if short_5d is not None else ""
                        st.metric("融券餘額（最新）", f"{short_latest:,.0f}億", delta_s,
                                  help="融券5日漲幅 > 3% 代表空方過擠，是逆向偏多訊號")
                        if short_5d is not None:
                            if short_5d > 3:
                                st.success(f"融券近5日增加 {short_5d:.2f}%，空方過擠，潛在軋空動能（+1）")
                            elif short_5d < -3:
                                st.info(f"融券近5日減少 {abs(short_5d):.2f}%，空方快速撤退")
                            else:
                                st.info(f"融券近5日變動 {short_5d:+.2f}%，無特殊訊號")
                        if short_series is not None:
                            fig_s = mini_chart(
                                {"融券餘額（億）": (short_series, "#f97316")},
                                height=120,
                            )
                            st.plotly_chart(fig_s, use_container_width=True)
                    else:
                        st.info("融券餘額資料暫無法取得")
                else:
                    st.info("融資餘額歷史資料暫無法取得")

        # ── 市場廣度：TAIEX vs OTC vs 台積電 ──
        with tc3:
            _t, _i = st.columns([10, 2], vertical_alignment="center")
            _t.subheader("📐 市場廣度", anchor=False)
            with _i:
                with st.popover("ℹ️"):
                    st.markdown("""
**市場廣度**　觀察台股上漲是「台積電獨撐」還是「多數股普漲」。

| 指標 | 說明 |
|------|------|
| **加權指數 ^TWII** | 市值加權，台積電佔約 30% |
| **櫃買指數 ^TWOII** | 中小型股為主，反映整體廣度 |
| **台積電 2330.TW** | 最大權值股，領漲/落後是方向確認指標 |

**OTC vs 加權：**
- OTC 跑贏 > 1% → 廣度健康，中小型股活躍
- OTC 落後 > 1.5% → 拉積盤，大盤虛弱

**台積電相對強弱：**
- 台積電近5日跑贏大盤 > 2% → +1（半導體訊號確認）
- 台積電近5日落後大盤 > 2% → -1（最大權值股轉弱）
""")
            if tw_breadth:
                mc1, mc2 = st.columns(2)
                with mc1:
                    st.metric("加權指數 (TAIEX)",
                              f"{tw_breadth['taiex_cur']:,.0f}",
                              f"{tw_breadth['taiex_chg_pct']:+.2f}%")
                with mc2:
                    st.metric("櫃買指數 (OTC)",
                              f"{tw_breadth['otc_cur']:,.2f}",
                              f"{tw_breadth['otc_chg_pct']:+.2f}%")

                rvs = tw_breadth["otc_vs_taiex_5d"]
                if rvs > 1:
                    st.success(f"廣度健康：OTC 近5日跑贏加權 {rvs:+.2f}%（中小型股活躍）")
                elif rvs < -1.5:
                    st.warning(f"拉積盤警告：OTC 近5日落後加權 {rvs:.2f}%，市場底子虛弱")
                else:
                    st.info(f"廣度中性：OTC vs 加權 近5日差距 {rvs:+.2f}%")

                # 台積電相對強弱
                tsmc_rvs = tw_breadth.get("tsmc_vs_taiex_5d")
                if tsmc_rvs is not None and tw_breadth.get("tsmc_cur") is not None:
                    tsmc_color = "#22c55e" if tsmc_rvs >= 0 else "#ef4444"
                    st.metric("台積電 (2330.TW)",
                              f"{tw_breadth['tsmc_cur']:,.0f}",
                              f"{tw_breadth['tsmc_chg_pct']:+.2f}%")
                    if tsmc_rvs > 2:
                        st.success(f"台積電領漲：近5日跑贏大盤 {tsmc_rvs:+.2f}%（+1）")
                    elif tsmc_rvs < -2:
                        st.warning(f"台積電落後：近5日落後大盤 {tsmc_rvs:.2f}%（-1）")
                    else:
                        st.markdown(
                            f"<span style='color:{tsmc_color}; font-size:0.9rem'>"
                            f"台積電 vs 大盤 近5日：{tsmc_rvs:+.2f}%</span>",
                            unsafe_allow_html=True,
                        )

                norm = tw_breadth["normalized"]
                chart_series = {
                    "加權 (TAIEX)": (norm["^TWII"],  "#3b82f6"),
                    "櫃買 (OTC)":   (norm["^TWOII"], "#22c55e"),
                }
                if tw_breadth.get("tsmc_normalized") is not None:
                    chart_series["台積電 (2330)"] = (tw_breadth["tsmc_normalized"], "#a78bfa")
                fig = mini_chart(chart_series)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.error("市場廣度資料載入失敗")

        st.divider()

        # ────────────────────────────────────────────────────
        # 第二行：資金流動性（台幣）/ 跨資產（費半+美元）
        # ────────────────────────────────────────────────────
        tc4, tc5 = st.columns(2)

        # ── 資金流動性：新台幣匯率 ──
        with tc4:
            _t, _i = st.columns([10, 2], vertical_alignment="center")
            _t.subheader("💱 資金流動性（台幣）", anchor=False)
            with _i:
                with st.popover("ℹ️"):
                    st.markdown("""
**新台幣匯率（USD/TWD）** 是台股的資金生命線。

外資撤資 → 台幣貶值（USD/TWD 上升）
外資流入 → 台幣升值（USD/TWD 下降）

| 條件 | 評分 |
|------|------|
| 近5日升值 > 0.5% | +1（資金流入）|
| 近5日急貶 > 1% | -2（外資撤資）|
| 近3日急貶 > 1.5% | 終極危機觸發條件之一 |

🩸 **台幣是台股的血液** — 只要台幣穩，回檔通常屬技術修正。
""")
            if tw_credit:
                # USD/TWD 值越大 = 台幣越貶
                depr = tw_credit["twd_5d"] > 0
                delta_label = f"台幣近5日{'貶' if depr else '升'} {abs(tw_credit['twd_5d']):.2f}%"
                st.metric("USD/TWD", f"{tw_credit['usdtwd']:.3f}",
                          f"{tw_credit['chg_pct']:+.3f}%（日）")

                twd5 = tw_credit["twd_5d"]
                twd3 = tw_credit["twd_3d"]
                if twd5 > 1:
                    st.error(f"⚠️ 台幣近5日急貶 {twd5:.2f}%，外資撤資訊號，嚴格防禦")
                elif twd5 < -0.5:
                    st.success(f"台幣近5日升值 {abs(twd5):.2f}%，外資資金流入，偏向加倉")
                else:
                    st.info(f"台幣匯率近5日變動 {twd5:+.2f}%，資金流向平穩")

                if twd3 > 1.5:
                    st.error(f"🚨 台幣近3日急貶 {twd3:.2f}%，達終極危機閾值（>1.5%），請搭配指數確認")

                fig = mini_chart(
                    {"USD/TWD": (tw_credit["history"], "#f97316")},
                    h_lines=None,
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.error("台幣匯率資料載入失敗")

        # ── 跨資產：費半 + 美元指數 ──
        with tc5:
            _t, _i = st.columns([10, 2], vertical_alignment="center")
            _t.subheader("🌐 跨資產（費半＋美元）", anchor=False)
            with _i:
                with st.popover("ℹ️"):
                    st.markdown("""
**跨資產聯動**　台股與美股半導體及美元走勢連動極高。

| 指標 | 說明 |
|------|------|
| **費半 (SOX)** | 費城半導體指數，台積電/聯發科等重要先行指標 |
| **美元指數 (DXY)** | 美元走強 → 新興市場資金回流美國 |

⚠️ **評分條件（-1）**：SOX 跌破季線（60MA）且 DXY 近5日漲幅 > 1.5%

季線 = 近60交易日均線（約3個月）。
""")
            if tw_cross:
                sox_color = "#ef4444" if tw_cross["sox_below_ma60"] else "#22c55e"
                ma_diff = tw_cross["sox_cur"] - tw_cross["sox_ma60"]
                ma_diff_pct = ma_diff / tw_cross["sox_ma60"] * 100

                tc5a, tc5b = st.columns(2)
                with tc5a:
                    st.metric("費半 SOX",
                              f"{tw_cross['sox_cur']:,.0f}",
                              f"{tw_cross['sox_chg_pct']:+.2f}%")
                    st.markdown(
                        f"<span style='color:{sox_color}; font-size:0.9rem'>"
                        f"{'↓ 跌破季線' if tw_cross['sox_below_ma60'] else '↑ 站上季線'}"
                        f"（{ma_diff_pct:+.1f}%）</span>",
                        unsafe_allow_html=True,
                    )
                with tc5b:
                    dxy_color = "#ef4444" if tw_cross["dxy_5d"] > 1.5 else "#22c55e"
                    st.metric("美元指數 DXY",
                              f"{tw_cross['dxy_cur']:.2f}",
                              f"{tw_cross['dxy_chg_pct']:+.2f}%")
                    st.markdown(
                        f"<span style='color:{dxy_color}; font-size:0.9rem'>"
                        f"近5日 {tw_cross['dxy_5d']:+.2f}%</span>",
                        unsafe_allow_html=True,
                    )

                if tw_cross["sox_below_ma60"] and tw_cross["dxy_5d"] > 1.5:
                    st.error("🚨 警示：費半跌破季線 + 美元急漲（近5日 >"
                             f" 1.5%），科技股殺估值且美元強勢，台股承壓")
                elif tw_cross["sox_below_ma60"]:
                    st.warning(f"費半跌破季線（{ma_diff_pct:+.1f}%），半導體景氣疑慮，留意台積電走勢")
                elif tw_cross["dxy_5d"] > 1.5:
                    st.warning(f"美元近5日急漲 {tw_cross['dxy_5d']:.2f}%，資金回流美元資產，留意外資動向")
                else:
                    st.info("跨資產訊號無特殊警告")

                norm = tw_cross["normalized"]
                fig = mini_chart({
                    "SOX 費半":    (norm["^SOX"],     "#8b5cf6"),
                    "DXY 美元":    (norm["DX-Y.NYB"], "#3b82f6"),
                })
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.error("跨資產資料載入失敗")

        st.divider()

        # ────────────────────────────────────────────────────
        # 台股綜合建議
        # ────────────────────────────────────────────────────
        st.markdown(
            "<div style='display:flex; align-items:baseline; gap:8px; margin-bottom:8px;'>"
            "<span style='font-size:1.5rem; font-weight:700; color:#1e293b;'>🎯 台股綜合建議</span>"
            "<span style='color:#94a3b8; font-size:0.8rem;'>（僅供參考，非投資建議）</span>"
            "</div>",
            unsafe_allow_html=True,
        )

        # tw_rec_label / tw_rec_color / tw_rec_desc 已在 spinner 後提前計算

        TW_STAGES = [
            ("🚨 系統性危機警告", "#dc2626"),
            ("🔴 考慮減倉/避險", "#ef4444"),
            ("🟠 偏向謹慎",      "#f97316"),
            ("⏸️ 觀望",         "#f59e0b"),
            ("🟡 偏向加倉",      "#84cc16"),
            ("✅ 強烈加倉機會",   "#22c55e"),
        ]
        tw_cards = ""
        for lbl, clr in TW_STAGES:
            if lbl == tw_rec_label:
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
            tw_cards += f'<div style="{style}">{lbl}</div>'
        st.markdown(
            f'<div style="display:flex;gap:6px;margin:14px 0 10px 0;">{tw_cards}</div>',
            unsafe_allow_html=True,
        )

        st.markdown(f"""
        <div style="
            background:{tw_rec_color}22;
            border:2px solid {tw_rec_color};
            border-radius:12px;
            padding:16px 28px;
            text-align:center;
            margin:0 0 18px 0;
        ">
            <div style="font-size:1.2rem; font-weight:700; color:#1d4ed8;">{tw_rec_desc}</div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("📖 台股綜合建議的評分基準"):
            _col_left, _col_right = st.columns(2)
            with _col_left:
                st.markdown("""
**各訊號加減分條件**

| 訊號 | 條件 | 分數 |
|------|------|:----:|
| **HV20（代理VIX）** | > 28% 且波動收斂（↓） | +3 |
| | > 28% 且波動仍升（↑） | +1 |
| | 20–28% | +1 |
| | 12–20% | 0 |
| | < 12% | -3 |
| **融資金額（代理）** | 單日減少 > 0.5% | +1 |
| | 單日增加 > 0.5% | -1 |
| **融券餘額（逆向）** | 近5日增加 > 3% | +1 |
| **外資期貨** | 淨空單 > 30,000 口 | -2 |
| | 近3日累計回補 > 10,000 口 | +1 |
| **外資現股** | 近3日累計買超 > 100億 | +1 |
| | 近3日累計賣超 > 100億 | -1 |
| | 近3日累計賣超 > 200億 | -2 |
| **投信現股** | 近3日累計買超 > 30億 | +1 |
| **市場廣度** | OTC 近5日跑贏加權 > 1% | +1 |
| | OTC 近5日落後加權 > 1.5% | -1 |
| **台積電相對強弱** | 近5日跑贏大盤 > 2% | +1 |
| | 近5日落後大盤 > 2% | -1 |
| **台幣匯率** | 近5日升值 > 0.5% | +1 |
| | 近5日急貶 > 1% | -2 |
| **跨資產** | SOX 跌破季線 + DXY 近5日漲 > 1.5% | -1 |
| **終極危機** | 台幣3日急貶 > 1.5% **且** TAIEX 3日跌 > 5% | 直接觸發危機警告 |
                """)
            with _col_right:
                st.markdown("""
**六種建議對應**

<table style="width:100%; border-collapse:collapse; font-size:0.9rem;">
  <thead>
    <tr style="border-bottom:2px solid #e2e8f0;">
      <th style="width:80px; text-align:center; padding:6px 8px; white-space:nowrap;">總分</th>
      <th style="padding:6px 8px; white-space:nowrap;">建議</th>
      <th style="padding:6px 8px;">說明</th>
    </tr>
  </thead>
  <tbody>
    <tr style="border-bottom:1px solid #e2e8f0;">
      <td style="text-align:center; padding:6px 8px;">危機觸發</td>
      <td style="padding:6px 8px; white-space:nowrap;">🚨 系統性危機</td>
      <td style="padding:6px 8px;">台幣失控貶值且大盤急殺，切勿急著抄底</td>
    </tr>
    <tr style="border-bottom:1px solid #e2e8f0;">
      <td style="text-align:center; padding:6px 8px;">≥ 3</td>
      <td style="padding:6px 8px; white-space:nowrap;">✅ 強烈加倉</td>
      <td style="padding:6px 8px;">多訊號共振，中長期極佳買點，依 3-3-4 策略</td>
    </tr>
    <tr style="border-bottom:1px solid #e2e8f0;">
      <td style="text-align:center; padding:6px 8px;">1–2</td>
      <td style="padding:6px 8px; white-space:nowrap;">🟡 偏向加倉</td>
      <td style="padding:6px 8px;">回調中，資金未失控，可小量分批進場</td>
    </tr>
    <tr style="border-bottom:1px solid #e2e8f0;">
      <td style="text-align:center; padding:6px 8px;">0</td>
      <td style="padding:6px 8px; white-space:nowrap;">⏸️ 觀望</td>
      <td style="padding:6px 8px;">多空訊號交織，等待更明確方向</td>
    </tr>
    <tr style="border-bottom:1px solid #e2e8f0;">
      <td style="text-align:center; padding:6px 8px;">-1 至 -2</td>
      <td style="padding:6px 8px; white-space:nowrap;">🟠 偏向謹慎</td>
      <td style="padding:6px 8px;">結構轉弱，縮減槓桿，不追高</td>
    </tr>
    <tr>
      <td style="text-align:center; padding:6px 8px;">≤ -3</td>
      <td style="padding:6px 8px; white-space:nowrap;">🔴 考慮減倉</td>
      <td style="padding:6px 8px;">市場過熱，逢高獲利了結，保留現金</td>
    </tr>
  </tbody>
</table>

> **黃金加倉組合**：HV20 > 28% 回落 + 外資期貨回補 + 外資現股買超
> **黃金減倉組合**：HV20 < 12% + 融資金額持續上升 + 外資現股賣超
                """, unsafe_allow_html=True)

        with st.expander("📖 台股 3-3-4 分批建倉策略"):
            st.markdown("""
| HV20 區間 | 判讀 | 操作 | 倉位比例 |
|-----------|------|------|---------|
| < 12% | 市場極度自滿 | 減倉 / 觀望 | — |
| 12–20% | 常態波動 | 保留現金 | — |
| **20–28%** | 情緒緊張 | **第一批進場** | **30%** |
| **> 28% 且↑** | 恐慌仍升 | **小量試水** | **30%** |
| **> 28% 且↓確認** | 恐慌見頂 | **第三批確認** | **40%** |

> **黃金加倉組合**：HV20 > 28% 且回落 + 外資期貨回補 > 10,000 口 + 外資現股近3日買超
""")

        st.divider()
        _render_history_section(rec_history, market="tw")

    # ════════════════════════════════════════════════════════
    # 美股 Tab
    # ════════════════════════════════════════════════════════
    with tab_us:
        with st.spinner("載入美股資料…"):
            # 宏觀一併放進並行批次：其內部 FRED 已改循序（避免並發被擋），
            # 與其他來源並行不互相影響，最多同時 2 條 FRED 連線（macro+credit）
            _us = _parallel_fetch({
                "vix": get_vix,         "fg": get_fear_greed,
                "breadth": get_market_breadth, "credit": get_credit,
                "cross": get_cross_assets, "spx_trend": get_spx_trend,
                "macro": get_us_macro,
            })
            vix, fg, breadth = _us["vix"], _us["fg"], _us["breadth"]
            credit, cross, spx_trend = _us["credit"], _us["cross"], _us["spx_trend"]
            us_macro = _us["macro"]   # 全失敗時 get_us_macro 拋例外 → _parallel_fetch 回 None

        # ── 提前計算短線信號（供宏觀背離矩陣使用）──
        rec_label, rec_color, rec_desc = calc_recommendation(
            vix["current"]                     if vix     else None,
            fg["score"]                        if fg      else None,
            breadth["rsp_vs_spy"]              if breadth else None,
            credit["hyg_5d"]                   if credit  else None,
            credit["spread"]                   if credit  else None,
            cross["latest"]["^TNX"]["chg_pct"] if cross   else None,
            cross["latest"]["GLD"]["chg_pct"]  if cross   else None,
            cross["latest"]["UUP"]["chg_pct"]  if cross   else None,
            vix["max_5d"]            if vix       else None,
            spx_trend["above_ma200"] if spx_trend else None,
            spx_trend["ret_20d"]     if spx_trend else None,
        )

        # ── 宏觀背景（月更/日更，不影響短線評分）──
        _render_us_macro_section(us_macro, rec_label)

        # ────────────────────────────────────────────────────
        # 第一行：VIX ／ 恐懼貪婪 ／ 市場廣度
        # ────────────────────────────────────────────────────
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
            "<span style='color:#94a3b8; font-size:0.8rem;'>（僅供參考，非投資建議）</span>"
            "</div>",
            unsafe_allow_html=True,
        )

        # rec_label / rec_color / rec_desc 已在 spinner 後提前計算

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

        st.divider()
        _render_history_section(rec_history, market="us")

    # ════════════════════════════════════════════════════════
    # 加密貨幣 Tab（暫時隱藏；tab_crypto = None 時跳過整個區塊）
    # ════════════════════════════════════════════════════════
    if tab_crypto is not None:
     with tab_crypto:
        with st.spinner("載入加密指標（CoinMetrics / FRED / yfinance / alternative.me / CoinGecko）…"):
            crypto = get_crypto()

        _t, _i = st.columns([10, 2], vertical_alignment="center")
        _t.subheader("₿ BTC 量化決策系統（免費版）", anchor=False)
        with _i:
            with st.popover("ℹ️"):
                st.markdown("""
**四層決策架構（由上至下）**

| 層 | 指標 | 方向 |
|----|------|------|
| **L0 宏觀閘門** | US M2 YoY · Fed 資產負債表 MA4W | M2 收縮→加倉分×0.5 |
| **L1 加倉面** | MVRV · Z-Score · NUPL · Realized比 · Puell · Mayer · F&G · AHR999 | 超跌→正分（滿分15） |
| **L1 減倉面** | MVRV Z-Score · NUPL · Pi Cycle Top | 過熱→正分（滿分6） |
| **L2 衍生品** | 資金費率年化 · F&G 高位 | 槓桿過熱→減倉加分（滿分4） |

**決策（減倉優先）：** 減倉分≥8→考慮減倉；≥5→偏向謹慎；≥3→暫停加倉。
加倉有效分≥9→重倉加倉；≥6→階梯加倉；≥3→基礎定投；其餘→觀望。

資料來源：CoinMetrics Community（免費）· FRED · yfinance · alternative.me · CoinGecko。
                """)

        if crypto and any(crypto.get(k) is not None for k in ("mvrv", "mayer", "cfg", "btc_price")):

            # ── BTC 現況列（5 個關鍵指標）──────────────────────
            kc1, kc2, kc3, kc4, kc5 = st.columns(5)
            with kc1:
                px = crypto.get("btc_price")
                bd = crypto.get("btc_dom")
                st.metric("BTC 現價", f"${px:,.0f}" if px else "—",
                          f"主導率 {bd:.1f}%" if bd else None, delta_color="off")
            with kc2:
                mm = crypto.get("mayer")
                if mm is not None:
                    mm_clr = "#22c55e" if mm <= 0.8 else ("#ef4444" if mm >= 2.4 else "#f59e0b")
                    st.metric("Mayer 倍數", f"{mm:.2f}")
                    note = ("超跌區" if mm <= 0.8 else "高估區" if mm >= 2.4 else
                            "跌破均線" if mm < 1.0 else "均線上方")
                    st.markdown(f"<span style='color:{mm_clr};font-weight:bold'>▌ {note}</span>",
                                unsafe_allow_html=True)
                else:
                    st.metric("Mayer 倍數", "—")
            with kc3:
                mv = crypto.get("mvrv")
                if mv is not None:
                    mv_clr = "#22c55e" if mv <= 1.0 else ("#ef4444" if mv >= 3.5 else "#f59e0b")
                    st.metric("MVRV 比值", f"{mv:.2f}")
                    note = ("深度超跌" if mv <= 1.0 else "偏低" if mv <= 1.2 else
                            "偏高" if mv >= 3.5 else "中性")
                    st.markdown(f"<span style='color:{mv_clr};font-weight:bold'>▌ {note}</span>",
                                unsafe_allow_html=True)
                else:
                    st.metric("MVRV 比值", "—")
            with kc4:
                rr = crypto.get("realized_ratio")
                rp = crypto.get("realized_price")
                if rr is not None:
                    rr_clr = "#22c55e" if rr <= 1.0 else ("#ef4444" if rr >= 5.0 else "#f59e0b")
                    st.metric("現價/Realized", f"{rr:.2f}×",
                              f"已實現 ${rp:,.0f}" if rp else None, delta_color="off")
                    note = "貼近成本區" if rr <= 1.08 else ("過熱" if rr >= 5 else "中性")
                    st.markdown(f"<span style='color:{rr_clr};font-weight:bold'>▌ {note}</span>",
                                unsafe_allow_html=True)
                else:
                    st.metric("現價/Realized", "—")
            with kc5:
                pu = crypto.get("puell")
                if pu is not None:
                    pu_clr = "#22c55e" if pu <= 0.5 else ("#ef4444" if pu >= 4.0 else "#f59e0b")
                    st.metric("Puell Multiple", f"{pu:.2f}")
                    note = ("礦工承壓（底部）" if pu <= 0.5 else
                            "過熱（礦工暴利）" if pu >= 4.0 else "中性")
                    st.markdown(f"<span style='color:{pu_clr};font-weight:bold'>▌ {note}</span>",
                                unsafe_allow_html=True)
                else:
                    st.metric("Puell Multiple", "—")

            # ── L0 宏觀閘門橫幅 ────────────────────────────────
            m2_yoy     = crypto.get("m2_yoy")
            m2_expand  = crypto.get("m2_expanding")
            fed_ma4w   = crypto.get("fed_bs_ma4w")
            fed_expand = crypto.get("fed_bs_expanding")
            m2_mult    = 0.5 if (m2_expand is not None and not m2_expand) else 1.0
            m2_str  = f"{m2_yoy:+.1f}% YoY" if m2_yoy is not None else "載入中"
            fed_str = f"MA4W {fed_ma4w:.2f}兆" if fed_ma4w is not None else "載入中"
            l0_open = m2_expand is not False   # None 視為中性（不扣分）
            l0_clr  = "#22c55e" if l0_open else "#ef4444"
            l0_icon = "🟢" if l0_open else "🔴"
            l0_tag  = f"加倉係數 <b>×{m2_mult}</b>" + ("（M2 收縮，加倉訊號減半）" if m2_mult < 1 else "（M2 擴張，正常加倉）")
            m2_fed_icon = ("📈" if fed_expand else "📉") if fed_expand is not None else "⏳"
            st.markdown(
                f"""<div style="background:{l0_clr}18;border:1.5px solid {l0_clr}55;
                    border-radius:10px;padding:10px 20px;margin:10px 0;">
                    <b>{l0_icon} L0 宏觀閘門</b> &nbsp;|&nbsp;
                    US M2：<b style='color:{l0_clr}'>{m2_str}</b> &nbsp;|&nbsp;
                    Fed 資產負債表：<b>{fed_str}</b> {m2_fed_icon} &nbsp;|&nbsp;
                    {l0_tag}
                </div>""",
                unsafe_allow_html=True,
            )

            st.divider()

            # ── 評分計算 ──────────────────────────────────────
            crec_label, crec_color, crec_desc, buy_adj, sell_score = calc_crypto_recommendation(crypto)

            # 個別買方指標分（供面板展示；與 calc_crypto_recommendation 邏輯保持同步）
            def _bscore(key, thresholds):
                """thresholds: list of (比較值, 得分)，按優先順序排"""
                v = crypto.get(key)
                if v is None:
                    return v, 0
                for thr, sc in thresholds:
                    if v <= thr:
                        return v, sc
                return v, 0
            def _bscore_cfg(v, thresholds):
                if v is None:
                    return 0
                for thr, sc in thresholds:
                    if v < thr:
                        return sc
                return 0

            mvrv_v, mvrv_sc = _bscore("mvrv", [(1.0, 2), (1.2, 1)])
            mvrv_z_v, mvrv_z_sc = _bscore("mvrv_zscore", [(0.1, 2), (0.5, 1)])
            nupl_v, nupl_sc = _bscore("nupl_approx", [(0, 2), (0.25, 1)])
            rr_v, rr_sc    = _bscore("realized_ratio", [(1.0, 2), (1.08, 1)])
            pu_v, pu_sc    = _bscore("puell", [(0.5, 2), (0.8, 1)])
            may_v, may_sc  = _bscore("mayer", [(0.8, 2), (1.0, 1)])
            cfg_v = crypto.get("cfg")
            cfg_buy_sc = _bscore_cfg(cfg_v, [(15, 2), (25, 1)])
            ahr_v = crypto.get("ahr999_approx")
            ahr_sc = (1 if ahr_v is not None and ahr_v <= 0.45 else
                      0.5 if ahr_v is not None and ahr_v <= 1.2 else 0)
            buy_raw = mvrv_sc + mvrv_z_sc + nupl_sc + rr_sc + pu_sc + may_sc + cfg_buy_sc + ahr_sc

            # 個別賣方指標分
            mvrv_z_sell = (2 if mvrv_z_v is not None and mvrv_z_v >= 6.0 else
                           1 if mvrv_z_v is not None and mvrv_z_v >= 4.0 else 0)
            nupl_sell   = (2 if nupl_v is not None and nupl_v >= 0.75 else
                           1 if nupl_v is not None and nupl_v >= 0.5 else 0)
            pi_sell     = (2 if crypto.get("pi_cross") else
                           1 if crypto.get("pi_warning") else 0)
            cfg_sell_sc = (2 if cfg_v is not None and cfg_v >= 85 else
                           1 if cfg_v is not None and cfg_v >= 80 else 0)
            fr = crypto.get("funding")
            fr_ann = (fr * 3 * 365 * 100) if fr is not None else None
            fr_sell = (2 if fr_ann is not None and fr_ann >= 50 else
                       1 if fr_ann is not None and fr_ann >= 30 else 0)

            # ── 評分面板（買方 + 賣方）────────────────────────
            def _row(name, stars, val_str, sc, max_sc, status, s_clr=""):
                badge_clr = ("#22c55e" if sc >= max_sc else
                             "#eab308" if sc > 0 else "#94a3b8")
                sc_str = f"+{sc:.4g}" if sc > 0 else f"{sc:.4g}"
                row_clr = s_clr or ("#22c55e" if sc >= max_sc else
                                    "#eab308" if sc > 0 else "#94a3b8")
                return (
                    f"<tr style='border-bottom:1px solid rgba(255,255,255,0.07)'>"
                    f"<td style='padding:7px 6px;font-size:0.82rem'><b>{name}</b>"
                    f" <span style='opacity:0.4;font-size:0.7rem'>{stars}</span></td>"
                    f"<td style='padding:7px 6px;text-align:right;font-size:0.82rem;"
                    f"opacity:0.75'>{val_str}</td>"
                    f"<td style='padding:7px 6px;font-size:0.78rem;color:{row_clr}'>{status}</td>"
                    f"<td style='padding:7px 6px;text-align:right'>"
                    f"<span style='color:{badge_clr};font-weight:700;font-size:0.82rem'>"
                    f"{sc_str}/{max_sc}</span></td></tr>"
                )

            tbl_style = ("width:100%;border-collapse:collapse;"
                         "background:rgba(255,255,255,0.03);border-radius:8px;"
                         "overflow:hidden;")

            # 買方面板
            buy_col, sell_col = st.columns([3, 2])
            with buy_col:
                st.markdown(f"**📈 加倉評分（L1）**　<span style='font-size:0.8rem;color:#94a3b8'>"
                            f"原始 {buy_raw:.1f}/15 → 有效 {buy_adj:.1f}/15（×{m2_mult}）</span>",
                            unsafe_allow_html=True)
                b_rows = (
                    _row("MVRV",         "★★★",
                         f"{mvrv_v:.2f}" if mvrv_v else "—",
                         mvrv_sc, 2,
                         "深度超跌" if mvrv_sc == 2 else "偏低" if mvrv_sc == 1 else "中性/過熱")
                  + _row("MVRV Z-Score", "★★★",
                         f"{mvrv_z_v:.2f}" if mvrv_z_v is not None else "—",
                         mvrv_z_sc, 2,
                         "歷史底部" if mvrv_z_sc == 2 else "偏低" if mvrv_z_sc == 1 else "中性")
                  + _row("NUPL（近似）",  "★★★",
                         f"{nupl_v:.3f}" if nupl_v is not None else "—",
                         nupl_sc, 2,
                         "投降區" if nupl_sc == 2 else "希望區" if nupl_sc == 1 else "中性/過熱")
                  + _row("現價/Realized", "★★",
                         f"{rr_v:.3f}" if rr_v is not None else "—",
                         rr_sc, 2,
                         "貼近成本" if rr_sc == 2 else "接近成本" if rr_sc == 1 else "中性")
                  + _row("Puell Multiple","★★",
                         f"{pu_v:.2f}" if pu_v is not None else "—",
                         pu_sc, 2,
                         "礦工承壓" if pu_sc == 2 else "偏低" if pu_sc == 1 else "中性")
                  + _row("Mayer 倍數",   "★★",
                         f"{may_v:.2f}" if may_v is not None else "—",
                         may_sc, 2,
                         "超跌" if may_sc == 2 else "跌破均線" if may_sc == 1 else "均線上方")
                  + _row("加密 F&G",     "★★",
                         str(cfg_v) if cfg_v is not None else "—",
                         cfg_buy_sc, 2,
                         "極度恐懼" if cfg_buy_sc == 2 else "恐懼" if cfg_buy_sc == 1 else "中性/貪婪")
                  + _row("AHR999（近似）","★",
                         f"{ahr_v:.2f}" if ahr_v is not None else "—",
                         ahr_sc, 1,
                         "定投區" if ahr_sc == 1 else "偏低" if ahr_sc == 0.5 else "中性/過熱")
                )
                st.markdown(
                    f'<table style="{tbl_style}">{b_rows}</table>',
                    unsafe_allow_html=True,
                )

            # 賣方面板
            with sell_col:
                st.markdown(f"**📉 減倉評分（L1/L2）**　<span style='font-size:0.8rem;color:#94a3b8'>"
                            f"減倉分 {sell_score}/10</span>",
                            unsafe_allow_html=True)
                # Pi Cycle 顯示值
                pi111 = crypto.get("pi_111dma")
                pi350 = crypto.get("pi_350x2")
                pi_val_str = (f"{pi111:,.0f} vs {pi350:,.0f}" if pi111 and pi350 else "—")
                pi_status = ("已穿越！頂部訊號" if pi_sell == 2 else
                             "接近穿越（警戒）" if pi_sell == 1 else "安全距離")
                cfg_sell_status = ("極度貪婪" if cfg_sell_sc == 2 else
                                   "高度貪婪" if cfg_sell_sc == 1 else "中性/恐懼")
                fr_str = f"{fr_ann:.0f}%" if fr_ann is not None else "—"
                fr_status = ("費率過熱" if fr_sell == 2 else "偏高" if fr_sell == 1 else "正常")
                s_rows = (
                    _row("MVRV Z-Score", "★★★",
                         f"{mvrv_z_v:.2f}" if mvrv_z_v is not None else "—",
                         mvrv_z_sell, 2,
                         "頂部過熱" if mvrv_z_sell == 2 else "偏高" if mvrv_z_sell == 1 else "安全",
                         s_clr="#ef4444" if mvrv_z_sell else "")
                  + _row("NUPL（近似）", "★★★",
                         f"{nupl_v:.3f}" if nupl_v is not None else "—",
                         nupl_sell, 2,
                         "信念/歡欣頂部" if nupl_sell == 2 else "偏高" if nupl_sell == 1 else "安全",
                         s_clr="#ef4444" if nupl_sell else "")
                  + _row("Pi Cycle Top", "★★★",
                         pi_val_str, pi_sell, 2, pi_status,
                         s_clr="#ef4444" if pi_sell == 2 else "#f97316" if pi_sell == 1 else "")
                  + _row("加密 F&G 高位","★★",
                         str(cfg_v) if cfg_v is not None else "—",
                         cfg_sell_sc, 2, cfg_sell_status,
                         s_clr="#ef4444" if cfg_sell_sc else "")
                  + _row("費率（年化）", "★★",
                         fr_str, fr_sell, 2, fr_status,
                         s_clr="#ef4444" if fr_sell == 2 else "#f97316" if fr_sell == 1 else "")
                )
                st.markdown(
                    f'<table style="{tbl_style}">{s_rows}</table>',
                    unsafe_allow_html=True,
                )

            st.divider()

            # ── 六段綜合建議 ────────────────────────────────
            st.markdown(
                "<div style='display:flex; align-items:baseline; gap:8px; margin-bottom:8px;'>"
                "<span style='font-size:1.3rem; font-weight:700;'>🎯 BTC 綜合建議</span>"
                "<span style='color:#94a3b8; font-size:0.8rem;'>（僅供參考，非投資建議）</span>"
                "</div>",
                unsafe_allow_html=True,
            )

            CSTAGES = [
                ("🔴 考慮減倉", "#ef4444"),
                ("🟠 偏向謹慎", "#f97316"),
                ("⏸️ 觀望",    "#94a3b8"),
                ("🟡 基礎定投", "#eab308"),
                ("🟢 階梯加倉", "#84cc16"),
                ("✅ 重倉加倉", "#22c55e"),
            ]
            cards = ""
            for lbl, clr in CSTAGES:
                if lbl == crec_label:
                    style = (
                        f"flex:1;text-align:center;padding:10px 4px;border-radius:10px;"
                        f"background:{clr}33;border:2px solid {clr};"
                        f"color:{clr};font-weight:bold;font-size:0.82rem;"
                    )
                else:
                    style = (
                        "flex:1;text-align:center;padding:10px 4px;border-radius:10px;"
                        "background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.12);"
                        "color:#888;font-size:0.79rem;"
                    )
                cards += f'<div style="{style}">{lbl}</div>'
            st.markdown(
                f'<div style="display:flex;gap:6px;margin:14px 0 10px 0;">{cards}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div style="background:{crec_color}22;border:2px solid {crec_color};'
                f'border-radius:12px;padding:14px 24px;text-align:center;margin:0 0 18px 0;">'
                f'<span style="font-size:1.1rem;font-weight:700">{crec_desc}</span></div>',
                unsafe_allow_html=True,
            )

            with st.expander("📖 評分基準與指標說明"):
                st.markdown("""
**L1 加倉面（滿分 15 分）**

| 指標 | 加倉條件 | 分數 |
|------|---------|:----:|
| MVRV ★★★ | ≤1.0（深度超跌）/ ≤1.2（偏低） | +2 / +1 |
| MVRV Z-Score ★★★ | ≤0.1（歷史底部）/ ≤0.5（偏低） | +2 / +1 |
| NUPL近似 ★★★ | ≤0（投降）/ ≤0.25（希望） | +2 / +1 |
| 現價/Realized ★★ | ≤1.0（貼成本）/ ≤1.08（接近） | +2 / +1 |
| Puell Multiple ★★ | ≤0.5（礦工承壓）/ ≤0.8（偏低） | +2 / +1 |
| Mayer 倍數 ★★ | ≤0.8（超跌）/ ≤1.0（跌破均線） | +2 / +1 |
| 加密 F&G ★★ | <15（極度恐懼）/ <25（恐懼） | +2 / +1 |
| AHR999近似 ★ | ≤0.45（定投區）/ ≤1.2（偏低） | +1 / +0.5 |

**L1/L2 減倉面（滿分 10 分）**

| 指標 | 減倉條件 | 分數 |
|------|---------|:----:|
| MVRV Z-Score ★★★ | ≥6.0（歷史頂部）/ ≥4.0（偏高） | +2 / +1 |
| NUPL近似 ★★★ | ≥0.75（歡欣頂部）/ ≥0.5（信念） | +2 / +1 |
| Pi Cycle Top ★★★ | 111DMA 穿越 350DMA×2 / 接近5%以內 | +2 / +1 |
| 加密 F&G 高位 ★★ | ≥85（極度貪婪）/ ≥80（高度貪婪） | +2 / +1 |
| 費率年化 ★★ | ≥50%（過熱）/ ≥30%（偏高） | +2 / +1 |

**決策（減倉優先）：**
減倉分≥8→考慮減倉；≥5→偏向謹慎；≥3→觀望。
有效加倉分≥9→重倉加倉（費率年化<30%）；≥6→階梯加倉；≥3→基礎定投。
L0：US M2 YoY<0 時加倉有效分乘以 0.5。
                """)

            # 加密恐懼貪婪歷史走勢（保留輔助圖）
            if crypto.get("cfg_hist") is not None and len(crypto["cfg_hist"]) > 1:
                st.divider()
                st.caption("加密恐懼貪婪指數 30 日走勢")
                fig = mini_chart(
                    {"加密恐懼貪婪": (crypto["cfg_hist"], "#a855f7")},
                    h_lines=[
                        (80, "dash", "#ef4444", "80 極度貪婪"),
                        (25, "dash", "#22c55e", "25 恐懼"),
                    ],
                )
                st.plotly_chart(fig, use_container_width=True)

            st.divider()
            _render_history_section(rec_history, market="crypto")
        else:
            st.error("加密指標載入失敗（CoinMetrics / yfinance 等外部 API 暫時無法連線），請稍後重試")


if __name__ == "__main__":
    main()
