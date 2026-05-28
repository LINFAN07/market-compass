# 市場指標警報背景程序
# 每小時抓取一次指標，評分等級有變化時寄信給所有訂閱者
# 啟動方式：python alert_worker.py

import os
import time
import requests
import pandas as pd
from io import StringIO
from datetime import datetime

import pytz
from dotenv import load_dotenv

from db import init_db, list_subscribers, get_last_label, set_last_label

load_dotenv(dotenv_path=r"c:\agent\market-compass\.env")

RESEND_API_KEY  = os.getenv("RESEND_API_KEY", "")
ALERT_FROM      = os.getenv("ALERT_FROM_EMAIL", "")
DASHBOARD_URL   = os.getenv("DASHBOARD_URL", "http://localhost:8501")
TW_TZ           = pytz.timezone("Asia/Taipei")
CHECK_INTERVAL  = 3600  # 每小時（秒）


# ── 資料抓取（不帶 Streamlit cache）──────────────────────────

def _get_vix():
    import yfinance as yf
    try:
        hist = yf.Ticker("^VIX").history(period="5d")["Close"].dropna()
        if len(hist) < 2:
            return None
        return {"current": round(hist.iloc[-1], 2)}
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


# ── 評分邏輯（與 dashboard.py 相同）────────────────────────

def _calc_recommendation(vix_cur, fg_score, rsp_vs_spy, hyg_5d, spread, tnx_chg, gld_chg, uup_chg=None):
    score = 0
    crisis = False

    if vix_cur is not None:
        if vix_cur > 40:   score += 3
        elif vix_cur > 30: score += 2
        elif vix_cur > 25: score += 1
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
        if hyg_5d < -2:  score -= 2
        elif hyg_5d >= 0: score += 1

    if tnx_chg is not None and gld_chg is not None:
        if tnx_chg > 0.5 and gld_chg > 0.5:
            score -= 1

    if uup_chg is not None and uup_chg > 1:
        score -= 1

    if crisis:    return "🚨 危機警告"
    if score >= 3: return "✅ 加倉機會"
    if score >= 1: return "🟡 偏向加倉"
    if score <= -3: return "🔴 考慮減倉"
    if score <= -1: return "🟠 偏向謹慎"
    return "⏸️ 觀望"


# ── 取得目前評分等級 ────────────────────────────────────────

def get_current_label() -> str | None:
    """抓取所有指標並計算當前評分等級"""
    vix     = _get_vix()
    fg      = _get_fear_greed()
    breadth = _get_market_breadth()
    credit  = _get_credit()
    cross   = _get_cross_assets()

    # 任一關鍵資料缺失就跳過，避免誤報
    if not vix or not fg:
        return None

    return _calc_recommendation(
        vix["current"]            if vix     else None,
        fg["score"]               if fg      else None,
        breadth["rsp_vs_spy"]     if breadth else None,
        credit["hyg_5d"]          if credit  else None,
        credit["spread"]          if credit  else None,
        cross["tnx_chg"]          if cross   else None,
        cross["gld_chg"]          if cross   else None,
        cross["uup_chg"]          if cross   else None,
    )


# ── 寄信（Resend API）───────────────────────────────────────

def _send_email(to: str, old_label: str, new_label: str, now_str: str):
    """用 Resend REST API 寄出通知信"""
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
            current = get_current_label()
            if current is None:
                print(f"[{now_str}] 指標資料不完整，跳過本次檢查")
            else:
                last = get_last_label()
                print(f"[{now_str}] 上次：{last}  現在：{current}")

                if current != last:
                    subscribers = list_subscribers()
                    print(f"[{now_str}] 等級變化！寄信給 {len(subscribers)} 位訂閱者")
                    ok = err = 0
                    for email in subscribers:
                        if _send_email(email, last, current, now_str):
                            ok += 1
                        else:
                            err += 1
                    print(f"[{now_str}] 寄信完成：成功 {ok}，失敗 {err}")
                    set_last_label(current)
                else:
                    print(f"[{now_str}] 等級未變，不寄信")
        except Exception as e:
            print(f"[{now_str}] 發生錯誤：{e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
