# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 本專案規則繼承自全局設定（`C:\Users\qwert\.claude\CLAUDE.md`），請在此補充專案專屬規則。

---

## 啟動指令

```powershell
# 儀表板（port 8501）
cd c:\agent\market-compass
streamlit run dashboard.py

# 警報背景程序（獨立 process，可與儀表板同時執行）
python alert_worker.py
```

也可執行 `啟動儀表板.bat` 快速開啟 Streamlit。

安裝依賴：

```powershell
pip install -r requirements.txt
```

---

## 架構總覽

```
market-compass/
├── dashboard.py        # Streamlit 主程式（唯一入口，含資料抓取 + 信號邏輯 + UI）
├── alert_worker.py     # 背景程序：每小時輪詢指標，等級變化時用 Resend 寄信
├── db.py               # SQLite 訂閱者資料庫（subscribers.db）
├── .streamlit/         # Streamlit 設定（headless, port 8501）
└── .env                # 機密金鑰（RESEND_API_KEY, ALERT_FROM_EMAIL, DASHBOARD_URL）
```

### 資料流

```
外部 API（yfinance / CNN / FRED）
    → dashboard.py 的 get_*() 函式（@st.cache_data ttl=300）
        → calc_recommendation()  →  UI 顯示
    → alert_worker.py 的 _get_*() 函式（無 cache，直接呼叫）
        → _calc_recommendation() →  等級不同 → Resend 寄信 → db.py 記錄
```

### 重要設計決策

**評分邏輯刻意重複**：`calc_recommendation()`（dashboard.py）與 `_calc_recommendation()`（alert_worker.py）邏輯相同，但刻意分開維護，以避免在非 Streamlit 環境 import `st`。**若修改評分邏輯，兩處都要同步更新。**

**alert_worker 設計為本機長駐程序**：不打算部署到 Streamlit Community Cloud 或任何雲端平台。`subscribers.db`（SQLite）也只存在本機，無需考慮雲端資料庫。

---

## 5 個市場訊號與對應資料來源

| 訊號 | 資料來源 | 關鍵 ticker / API |
|------|---------|-----------------|
| VIX 恐慌指數 | yfinance | `^VIX` |
| 恐懼貪婪指數 | CNN API | `production.dataviz.cnn.io/index/fearandgreed/graphdata/` |
| 市場廣度 | yfinance | `SPY`、`RSP`、`IWM` |
| 信用市場 | yfinance + FRED | `HYG`、`JNK`、`BAMLH0A0HYM2` |
| 跨資產聯動 | yfinance | `^TNX`（10Y殖利率）、`GLD`、`UUP` |

FRED 利差抓取失敗時，儀表板仍正常顯示 HYG/JNK（graceful fallback）。

---

## 評分邏輯（`calc_recommendation`）

| 條件 | 分數 |
|------|------|
| VIX > 30 | +2 |
| VIX < 12 | -3 |
| VIX < 15 | -2 |
| Fear & Greed < 25 | +2 |
| Fear & Greed > 75 | -2 |
| RSP 近5日跑贏 SPY > 0.5% | +1 |
| RSP 近5日落後 SPY < -1% | -1 |
| HYG 近5日 ≥ 0% | +1 |
| HYG 近5日 < -2% | -2 |
| 10Y殖利率↑ + 黃金↑（各 > 0.5%） | -1 |
| 高收益利差 > 6%（FRED） | 直接觸發「危機警告」，忽略其他分數 |

結果：≥3→加倉機會、≥1→偏向加倉、≤-1→偏向謹慎、≤-3→考慮減倉、其他→觀望。

---

## 部署架構

| 元件 | 位置 | 更新方式 |
| ---- | ---- | -------- |
| `dashboard.py` | Streamlit Community Cloud | `git push origin main` 後自動重新部署（約 1~2 分鐘） |
| `alert_worker.py` | 本機長駐 process | 直接修改檔案後重啟 process，不部署到雲端 |
| `subscribers.db` | 本機 SQLite | 只存在本機，不需同步 |

**更新網站步驟：**

```powershell
git add dashboard.py          # 或其他修改的檔案
git commit -m "說明改了什麼"
git push origin main          # push 完 Streamlit Cloud 自動接手
```

`.env` 裡的 API 金鑰**不要 push 進 GitHub**，在 Streamlit Cloud 的 App Settings → Secrets 另行設定。

---

## 環境設定（`.env`）

```
RESEND_API_KEY=...       # Resend 郵件 API 金鑰
ALERT_FROM_EMAIL=...     # 寄件人地址（需在 Resend 驗證網域）
DASHBOARD_URL=...        # 郵件中的「開啟儀表板」連結
```

`db.py` 與 `alert_worker.py` 均使用硬編碼絕對路徑（`c:\agent\market-compass\...`），搬移專案時需一併修改。

---

## 已知待改進項目（備忘錄）

以下功能已規劃但尚未實作，未來開發可參考：

1. **VIX 趨勢偵測**：目前以絕對值 > 30 給分；正確做法應偵測「VIX 從高點回落」（如 `vix_5d_max > 30 and current < vix_5d_max * 0.9`）才給加倉分。
2. **市場廣度擴充**：加入 S&P 500 成分股站上 200 日均線比例（門檻：> 60% 健康 / < 40% 惡化）。
3. **信用市場擴充**：加入投資等級債 LQD，若 LQD 也下跌代表危機已蔓延至主流企業。
