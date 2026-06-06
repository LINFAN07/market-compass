# 市場指標警報背景程序
# 每小時抓取一次指標，評分等級有變化時寄信給所有訂閱者
# 同時每小時將台股 + 美股建議寫入 GitHub Gist 歷史紀錄
# 啟動方式：python alert_worker.py

import os
import json
import time
import requests
import numpy as np
import pandas as pd
from io import StringIO
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv

from db import init_db, list_subscribers, get_last_label, set_last_label

load_dotenv(dotenv_path=r"c:\agent\market-compass\.env")

RESEND_API_KEY  = os.getenv("RESEND_API_KEY", "")
ALERT_FROM      = os.getenv("ALERT_FROM_EMAIL", "")
DASHBOARD_URL   = os.getenv("DASHBOARD_URL", "http://localhost:8501")
GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN", "")
GIST_ID         = os.getenv("GIST_ID", "")
TW_TZ           = pytz.timezone("Asia/Taipei")
CHECK_INTERVAL  = 3600  # 每小時（秒）

# 建議等級 → 顏色對照（與 dashboard.py 一致）
_COLOR = {
    "🚨 危機警告":       "#dc2626",
    "🔴 考慮減倉":       "#ef4444",
    "🟠 偏向謹慎":       "#f97316",
    "⏸️ 觀望":          "#f59e0b",
    "🟡 偏向加倉":       "#84cc16",
    "✅ 加倉機會":       "#22c55e",
    "🚨 系統性危機警告":  "#dc2626",
    "🔴 考慮減倉/避險":  "#ef4444",
    "✅ 強烈加倉機會":   "#22c55e",
}


# ── 美股資料抓取 ──────────────────────────────────────────────

def _get_vix():
    import yfinance as yf
    try:
        hist = yf.Ticker("^VIX").history(period="5d")["Close"].dropna()
        if len(hist) < 2:
            return None
        return {
            "current": round(hist.iloc[-1], 2),
            "max_5d":  round(float(hist.max()), 2),  # 近5日高點，判斷恐慌是否回落
        }
    except Exception:
        return None


def _get_fear_greed():
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://edition.cnn.com/",
            },
            timeout=10,
        )
        fg = r.json()["fear_and_greed"]
        return {"score": round(float(fg["score"]), 1)}
    except Exception:
        return None


def _get_market_breadth():
    import yfinance as yf
    try:
        frames = {}
        for s in ["SPY", "RSP"]:
            h = yf.Ticker(s).history(period="10d")["Close"].dropna()
            h.index = h.index.tz_localize(None)
            frames[s] = h
        df = pd.DataFrame(frames).dropna()
        rsp_5d = (df["RSP"].iloc[-1] / df["RSP"].iloc[-5] - 1) * 100
        spy_5d = (df["SPY"].iloc[-1] / df["SPY"].iloc[-5] - 1) * 100
        return {"rsp_vs_spy": round(rsp_5d - spy_5d, 2)}
    except Exception:
        return None


def _get_credit():
    import yfinance as yf
    try:
        h = yf.Ticker("HYG").history(period="10d")["Close"].dropna()
        h.index = h.index.tz_localize(None)
        hyg_5d = round((h.iloc[-1] / h.iloc[-5] - 1) * 100, 2)

        spread = None
        try:
            fred_r = requests.get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8,
            )
            sdf = pd.read_csv(StringIO(fred_r.text))
            col = sdf.columns[-1]
            sdf = sdf[sdf[col] != "."].copy()
            sdf[col] = pd.to_numeric(sdf[col])
            spread = float(sdf[col].iloc[-1])
        except Exception:
            pass

        return {"hyg_5d": hyg_5d, "spread": spread}
    except Exception:
        return None


def _get_cross_assets():
    import yfinance as yf
    try:
        frames = {}
        for s in ["^TNX", "GLD", "UUP"]:
            h = yf.Ticker(s).history(period="5d")["Close"].dropna()
            h.index = h.index.tz_localize(None)
            frames[s] = h
        df = pd.DataFrame(frames).dropna()
        return {
            "tnx_chg": round((df["^TNX"].iloc[-1] / df["^TNX"].iloc[-2] - 1) * 100, 2),
            "gld_chg": round((df["GLD"].iloc[-1]  / df["GLD"].iloc[-2]  - 1) * 100, 2),
            "uup_chg": round((df["UUP"].iloc[-1]  / df["UUP"].iloc[-2]  - 1) * 100, 2),
        }
    except Exception:
        return None


# ── 台股資料抓取 ──────────────────────────────────────────────

def _get_tw_signals():
    """台股所有短線信號，回傳 dict（失敗項目為 None）"""
    import yfinance as yf
    s = {
        "hv20": None, "hv20_falling": False,
        "foreign_net_oi": None,
        "margin_chg_pct": None, "short_chg_5d": None,
        "otc_vs_taiex_5d": None, "taiex_3d": None, "tsmc_vs_taiex_5d": None,
        "twd_5d": None, "twd_3d": None,
        "sox_below_ma60": False, "dxy_5d": None,
        "foreign_cash_3d": None, "sitc_3d": None,
        "twii_cur": None,
    }

    # HV20 + TAIEX 3日漲跌 + 當日收盤
    try:
        hist = yf.Ticker("^TWII").history(period="3mo")["Close"].dropna()
        hist.index = hist.index.tz_localize(None)
        log_ret = np.log(hist / hist.shift(1)).dropna()
        hv = log_ret.rolling(20).std() * (252 ** 0.5) * 100
        hv = hv.dropna()
        if len(hv) >= 2:
            s["hv20"] = round(float(hv.iloc[-1]), 2)
            s["hv20_falling"] = float(hv.iloc[-1]) < float(hv.iloc[-2])
        if len(hist) >= 4:
            s["taiex_3d"] = round((float(hist.iloc[-1]) / float(hist.iloc[-4]) - 1) * 100, 2)
        s["twii_cur"] = round(float(hist.iloc[-1]), 0)
    except Exception:
        pass

    # OTC vs TAIEX + 台積電相對強弱
    try:
        frames = {}
        for sym in ["^TWII", "^TWOII"]:
            h = yf.Ticker(sym).history(period="1mo")["Close"].dropna()
            h.index = h.index.tz_localize(None)
            frames[sym] = h
        df = pd.DataFrame(frames).dropna()
        if len(df) >= 6:
            taiex_5d = (df["^TWII"].iloc[-1]  / df["^TWII"].iloc[-5]  - 1) * 100
            otc_5d   = (df["^TWOII"].iloc[-1] / df["^TWOII"].iloc[-5] - 1) * 100
            s["otc_vs_taiex_5d"] = round(otc_5d - taiex_5d, 2)
            try:
                h_tsmc = yf.Ticker("2330.TW").history(period="1mo")["Close"].dropna()
                h_tsmc.index = h_tsmc.index.tz_localize(None)
                h_tsmc = h_tsmc.reindex(df.index).dropna()
                if len(h_tsmc) >= 6:
                    tsmc_5d = (float(h_tsmc.iloc[-1]) / float(h_tsmc.iloc[-5]) - 1) * 100
                    s["tsmc_vs_taiex_5d"] = round(tsmc_5d - taiex_5d, 2)
            except Exception:
                pass
    except Exception:
        pass

    # 台幣（USD/TWD）
    try:
        h = yf.Ticker("TWD=X").history(period="1mo")["Close"].dropna()
        h.index = h.index.tz_localize(None)
        if len(h) >= 6:
            s["twd_5d"] = round((float(h.iloc[-1]) / float(h.iloc[-5]) - 1) * 100, 3)
            s["twd_3d"] = round((float(h.iloc[-1]) / float(h.iloc[-3]) - 1) * 100, 3)
    except Exception:
        pass

    # 費半（SOX）+ 美元指數（DXY）
    try:
        frames2 = {}
        for sym in ["^SOX", "DX-Y.NYB"]:
            h = yf.Ticker(sym).history(period="3mo")["Close"].dropna()
            h.index = h.index.tz_localize(None)
            frames2[sym] = h
        df2 = pd.DataFrame(frames2).dropna()
        if len(df2) >= 60:
            s["sox_below_ma60"] = float(df2["^SOX"].iloc[-1]) < float(df2["^SOX"].tail(60).mean())
        if len(df2) >= 6:
            s["dxy_5d"] = round((float(df2["DX-Y.NYB"].iloc[-1]) / float(df2["DX-Y.NYB"].iloc[-5]) - 1) * 100, 2)
    except Exception:
        pass

    # TAIFEX 外資臺指期淨部位
    try:
        r = requests.get(
            "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=12,
        )
        for rec in r.json():
            if rec.get("ContractCode") == "臺股期貨" and rec.get("Item") == "外資及陸資":
                s["foreign_net_oi"] = int(rec["OpenInterest(Net)"])
                break
    except Exception:
        pass

    # TWSE MI_MARGN：融資日變動% + 融券5日變動%
    try:
        now_tw = datetime.now(TW_TZ)
        m_bals, sh_bals = [], []
        for delta in range(0, 10):
            d = now_tw - timedelta(days=delta)
            r = requests.get(
                f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d.strftime('%Y%m%d')}&response=json",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
            )
            data = r.json()
            if data.get("stat") != "OK":
                continue
            tables = data.get("tables", [])
            if tables and tables[0].get("data"):
                rows0 = tables[0]["data"]
                m_bals.append(round(int(str(rows0[-1][-1]).replace(",", "")) / 100000, 2))
            if len(tables) >= 2 and tables[1].get("data"):
                tbl    = tables[1]
                rows1  = tbl["data"]
                fields = tbl.get("fields", [])
                col    = next((i for i, f in enumerate(fields) if "餘額金額" in str(f)), -2)
                sh_bals.append(round(int(str(rows1[-1][col]).replace(",", "")) / 100000, 2))
            if len(m_bals) >= 6 and len(sh_bals) >= 6:
                break
        if len(m_bals) >= 2 and m_bals[1] > 0:
            s["margin_chg_pct"] = round((m_bals[0] / m_bals[1] - 1) * 100, 3)
        if len(sh_bals) >= 6 and sh_bals[5] > 0:
            s["short_chg_5d"] = round((sh_bals[0] / sh_bals[5] - 1) * 100, 2)
    except Exception:
        pass

    # TWSE BFI82U：外資 + 投信現股近3日累計（億元）
    try:
        now_tw = datetime.now(TW_TZ)
        fetched = []
        for delta in range(0, 8):
            d    = (now_tw - timedelta(days=delta)).strftime("%Y%m%d")
            r    = requests.get(
                f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={d}&response=json",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=12,
            )
            data = r.json()
            if data.get("stat") != "OK":
                continue
            foreign = sitc = None
            for row in data.get("data", []):
                name = str(row[0])
                if "外資及陸資" in name and "不含" in name:
                    try: foreign = round(int(str(row[3]).replace(",", "")) / 1e8, 2)
                    except Exception: pass
                elif "投信" == name.strip():
                    try: sitc = round(int(str(row[3]).replace(",", "")) / 1e8, 2)
                    except Exception: pass
            if foreign is not None or sitc is not None:
                fetched.append({"foreign": foreign, "sitc": sitc})
                if len(fetched) >= 3:
                    break
        if fetched:
            f_vals  = [row["foreign"] for row in fetched if row["foreign"] is not None]
            sv_vals = [row["sitc"]    for row in fetched if row["sitc"]    is not None]
            if f_vals:  s["foreign_cash_3d"] = round(sum(f_vals), 2)
            if sv_vals: s["sitc_3d"]         = round(sum(sv_vals), 2)
    except Exception:
        pass

    return s


# ── 評分邏輯（與 dashboard.py 相同，刻意分開避免 import st）──

def _calc_recommendation(vix_cur, fg_score, rsp_vs_spy, hyg_5d, spread,
                         tnx_chg, gld_chg, uup_chg=None,
                         vix_5d_max=None, spx_above_ma200=None, spx_20d_ret=None):
    score = 0
    crisis = False

    if vix_cur is not None:
        rolling_over = vix_5d_max is not None and vix_cur < vix_5d_max * 0.9
        if vix_cur > 40:   score += 3 if rolling_over else 1
        elif vix_cur > 30: score += 2 if rolling_over else 1
        elif vix_cur > 25: score += 1 if rolling_over else 0
        elif vix_cur < 12: score -= 3
        elif vix_cur < 15: score -= 2

    if fg_score is not None:
        if fg_score < 15:   score += 3
        elif fg_score < 25: score += 2
        elif fg_score > 75: score -= 2

    if rsp_vs_spy is not None:
        if rsp_vs_spy > 0.5:  score += 1
        elif rsp_vs_spy < -1: score -= 1

    if spread is not None and spread > 6:
        crisis = True
    elif hyg_5d is not None:
        if hyg_5d < -2:   score -= 2
        elif hyg_5d >= 0: score += 1

    if tnx_chg is not None and gld_chg is not None:
        if tnx_chg > 0.5 and gld_chg > 0.5:
            score -= 1

    if uup_chg is not None and uup_chg > 1:
        score -= 1

    # 動能確認：20日仍明顯下跌則壓抑樂觀
    if spx_20d_ret is not None and spx_20d_ret < -6:
        score -= 1

    # 200日均線總開關：價在均線下＝結構性空頭，逆向加倉分打折並加警示
    if spx_above_ma200 is False:
        if score > 0:
            score //= 2
        score -= 2

    if crisis:      return "🚨 危機警告"
    if score >= 3:  return "✅ 加倉機會"
    if score >= 1:  return "🟡 偏向加倉"
    if score <= -3: return "🔴 考慮減倉"
    if score <= -1: return "🟠 偏向謹慎"
    return "⏸️ 觀望"


def _calc_tw_recommendation(s):
    """台股綜合評分（與 dashboard.py calc_tw_recommendation 邏輯相同）"""
    score, crisis = 0, False

    if s["twd_3d"] is not None and s["taiex_3d"] is not None:
        if s["twd_3d"] > 1.5 and s["taiex_3d"] < -5:
            crisis = True

    if s["hv20"] is not None:
        if s["hv20"] > 28:    score += 3 if s["hv20_falling"] else 1
        elif s["hv20"] > 20:  score += 1
        elif s["hv20"] < 12:  score -= 3

    if s["margin_chg_pct"] is not None:
        if s["margin_chg_pct"] < -0.5:  score += 1
        elif s["margin_chg_pct"] > 0.5: score -= 1

    if s["short_chg_5d"] is not None and s["short_chg_5d"] > 3:
        score += 1

    if s["foreign_net_oi"] is not None and s["foreign_net_oi"] < -30000:
        score -= 2

    if s["otc_vs_taiex_5d"] is not None:
        if s["otc_vs_taiex_5d"] > 1:      score += 1
        elif s["otc_vs_taiex_5d"] < -1.5: score -= 1

    if s["tsmc_vs_taiex_5d"] is not None:
        if s["tsmc_vs_taiex_5d"] > 2:   score += 1
        elif s["tsmc_vs_taiex_5d"] < -2: score -= 1

    if s["twd_5d"] is not None:
        if s["twd_5d"] > 1:      score -= 2
        elif s["twd_5d"] < -0.5: score += 1

    if s["foreign_cash_3d"] is not None:
        if s["foreign_cash_3d"] > 100:    score += 1
        elif s["foreign_cash_3d"] < -200: score -= 2
        elif s["foreign_cash_3d"] < -100: score -= 1

    if s["sitc_3d"] is not None and s["sitc_3d"] > 30:
        score += 1

    if s["sox_below_ma60"] and s["dxy_5d"] is not None and s["dxy_5d"] > 1.5:
        score -= 1

    if crisis:      return "🚨 系統性危機警告"
    if score >= 3:  return "✅ 強烈加倉機會"
    if score >= 1:  return "🟡 偏向加倉"
    if score <= -3: return "🔴 考慮減倉/避險"
    if score <= -1: return "🟠 偏向謹慎"
    return "⏸️ 觀望"


# ── 美股大盤指數 ──────────────────────────────────────────────

def _get_spx():
    """S&P 500 當日收盤價"""
    import yfinance as yf
    try:
        h = yf.Ticker("^GSPC").history(period="5d")["Close"].dropna()
        return round(float(h.iloc[-1]), 2)
    except Exception:
        return None


def _get_spx_trend():
    """S&P 500 趨勢過濾：200日均線 + 20日動能（與 dashboard.py get_spx_trend 邏輯相同）"""
    import yfinance as yf
    try:
        h = yf.Ticker("^GSPC").history(period="1y")["Close"].dropna()
        if len(h) < 200:
            return None
        cur   = float(h.iloc[-1])
        ma200 = float(h.tail(200).mean())
        return {
            "above_ma200": cur > ma200,
            "ret_20d":     round((cur / float(h.iloc[-21]) - 1) * 100, 2),
        }
    except Exception:
        return None


# ── 取得目前美股評分等級（供寄信用）──────────────────────────

def get_current_label() -> str | None:
    vix       = _get_vix()
    fg        = _get_fear_greed()
    breadth   = _get_market_breadth()
    credit    = _get_credit()
    cross     = _get_cross_assets()
    spx_trend = _get_spx_trend()

    # VIX 是最關鍵指標，缺失才跳過；F&G 可選
    if not vix:
        return None

    return _calc_recommendation(
        vix["current"]        if vix     else None,
        fg["score"]           if fg      else None,
        breadth["rsp_vs_spy"] if breadth else None,
        credit["hyg_5d"]      if credit  else None,
        credit["spread"]      if credit  else None,
        cross["tnx_chg"]      if cross   else None,
        cross["gld_chg"]      if cross   else None,
        cross["uup_chg"]      if cross   else None,
        vix["max_5d"]            if vix       else None,
        spx_trend["above_ma200"] if spx_trend else None,
        spx_trend["ret_20d"]     if spx_trend else None,
    )


# ── GitHub Gist 歷史紀錄 ─────────────────────────────────────

def _load_gist_history():
    if not GIST_ID or not GITHUB_TOKEN:
        return []
    try:
        r = requests.get(
            f"https://api.github.com/gists/{GIST_ID}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        content = r.json()["files"]["rec_history.json"]["content"]
        return json.loads(content)
    except Exception:
        return []


def _save_gist_history(history):
    if not GIST_ID or not GITHUB_TOKEN:
        return
    requests.patch(
        f"https://api.github.com/gists/{GIST_ID}",
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        json={"files": {"rec_history.json": {
            "content": json.dumps(history, ensure_ascii=False, indent=2)
        }}},
        timeout=15,
    )


def _update_daily_history(us_label, tw_label, tw_index=None, us_index=None):
    """將今日台股 + 美股建議與指數收盤寫入 Gist（同日覆蓋最新值）"""
    today   = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    history = _load_gist_history()
    entry   = {
        "date":     today,
        "tw_label": tw_label, "tw_color": _COLOR.get(tw_label, "#888"),
        "tw_index": tw_index,
        "us_label": us_label, "us_color": _COLOR.get(us_label, "#888"),
        "us_index": us_index,
    }
    for i, rec in enumerate(history):
        if rec["date"] == today:
            history[i] = entry
            break
    else:
        history.append(entry)
    _save_gist_history(history)


# ── 寄信（Resend API）───────────────────────────────────────

def _send_email(to: str, old_label: str, new_label: str, now_str: str):
    subject = f"📊 市場評分等級變化：{old_label} → {new_label}"
    html = f"""
    <div style="font-family:sans-serif; max-width:560px; margin:auto;">
      <h2 style="color:#1e293b;">美股加減倉決策儀表板</h2>
      <p style="color:#475569;">偵測到評分等級異動（台北時間 {now_str}）</p>
      <table style="width:100%; border-collapse:collapse; margin:20px 0;">
        <tr>
          <td style="padding:12px; background:#f1f5f9; border-radius:6px; text-align:center; font-size:1.1rem;">
            <div style="color:#94a3b8; font-size:0.85rem;">前次等級</div>
            <div style="font-weight:bold; margin-top:4px;">{old_label if old_label else '（首次偵測）'}</div>
          </td>
          <td style="padding:12px; text-align:center; font-size:1.5rem; color:#94a3b8;">→</td>
          <td style="padding:12px; background:#f0fdf4; border-radius:6px; text-align:center; font-size:1.1rem;">
            <div style="color:#94a3b8; font-size:0.85rem;">最新等級</div>
            <div style="font-weight:bold; margin-top:4px;">{new_label}</div>
          </td>
        </tr>
      </table>
      <p style="text-align:center;">
        <a href="{DASHBOARD_URL}"
           style="display:inline-block; padding:10px 24px; background:#3b82f6;
                  color:#fff; border-radius:6px; text-decoration:none; font-weight:bold;">
          開啟儀表板查看詳情
        </a>
      </p>
      <hr style="border:none; border-top:1px solid #e2e8f0; margin:24px 0;">
      <p style="font-size:0.8rem; color:#94a3b8; text-align:center;">
        如不想繼續收到通知，請至儀表板底部取消訂閱。
      </p>
    </div>
    """
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"from": ALERT_FROM, "to": [to], "subject": subject, "html": html},
        timeout=15,
    )
    return resp.status_code == 200


# ── 主迴圈 ──────────────────────────────────────────────────

def run():
    init_db()
    print(f"[alert_worker] 啟動，每 {CHECK_INTERVAL // 60} 分鐘檢查一次")

    while True:
        now_str = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M")
        print(f"[{now_str}] 正在抓取指標…")

        try:
            tw_signals = _get_tw_signals()
            us_label   = get_current_label()
            tw_label   = _calc_tw_recommendation(tw_signals)
            tw_index   = tw_signals.get("twii_cur")
            us_index   = _get_spx()

            if us_label is None:
                print(f"[{now_str}] 美股指標資料不完整，跳過本次")
            else:
                # 寫入 Gist 歷史
                _update_daily_history(us_label, tw_label, tw_index, us_index)
                print(f"[{now_str}] Gist 已更新：美股={us_label}，台股={tw_label}")

                # 等級變化 → 寄信
                last = get_last_label()
                print(f"[{now_str}] 上次：{last}  現在：{us_label}")
                if us_label != last:
                    subscribers = list_subscribers()
                    print(f"[{now_str}] 等級變化！寄信給 {len(subscribers)} 位訂閱者")
                    ok = err = 0
                    for email in subscribers:
                        if _send_email(email, last, us_label, now_str):
                            ok += 1
                        else:
                            err += 1
                    print(f"[{now_str}] 寄信完成：成功 {ok}，失敗 {err}")
                    set_last_label(us_label)
                else:
                    print(f"[{now_str}] 等級未變，不寄信")

        except Exception as e:
            print(f"[{now_str}] 發生錯誤：{e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
