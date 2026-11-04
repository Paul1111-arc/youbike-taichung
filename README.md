# YouBike 雲端採集（GitHub Actions）

每 15 分鐘在 GitHub 的機器上抓一次台中 YouBike 全站狀態，存成 gzip CSV 推回
這個 repo。**完全不依賴你的電腦開不開機。**

```
collect_once.py            抓一次，寫 data/YYYY-MM-DD/HHMMSS_來源.csv.gz
merge_to_db.py             把 data/ 合併成 youbike.db（給 youbike_report.py 用）
.github/workflows/collect.yml   排程設定
```

---

## 一、先在本機確認來源還通

在 `cloud` 資料夾開 PowerShell：

```powershell
$py = "C:\Users\p\AppData\Local\Programs\Python\Python312\python.exe"
cd C:\Users\p\Desktop\Youbike\cloud
& $py collect_once.py --probe
```

看到至少一行 `OK` 才往下走。

---

## 二、建 GitHub repo 並推上去

```powershell
cd C:\Users\p\Desktop\Youbike\cloud
git init -b main
git add .
git commit -m "init: youbike collector"
```

到 <https://github.com/new> 建一個 repo，名字例如 `youbike-taichung`：

- **選 Public。** 公開 repo 的 Actions 用量無上限；私有的每月只有 2000 分鐘，
  每 15 分鐘跑一次大約會用掉 1400 分鐘，很容易爆。
  這裡存的全是政府公開資料，沒有任何個人資訊。
- 不要勾 README / .gitignore（本機已經有了）

建好後回到 PowerShell：

```powershell
git remote add origin https://github.com/你的帳號/youbike-taichung.git
git push -u origin main
```

推的時候會要帳密 —— **密碼欄不是你的 GitHub 密碼**，要用 Personal Access Token：
<https://github.com/settings/tokens> → Generate new token (classic) →
勾 `repo` → 產生後複製貼上。

---

## 三、打開 Actions

1. 到 repo 頁面 → **Actions** 分頁 → 按 **I understand my workflows, go ahead and enable them**
2. 左側選 **collect** → 右上 **Run workflow** → 手動跑一次
3. 等 1–2 分鐘，點進去看綠勾。成功的話 repo 裡會出現
   `data/2026-11-04/HHMMSS_govopen.csv.gz`

**手動這次一定要跑，而且一定要確認成功。** 排程要等下一個整 15 分才會自己觸發，
如果 workflow 有問題，不手動測就要等很久才發現。

### 如果手動跑失敗

點進失敗的 run，看「抓一次」那步的錯誤：

- **`govopen` / `youbike` 都 FAIL** → GitHub 的機器在美國，可能被來源端擋。
  改用 TDX（見下一節），它本來就是設計給程式介接的，不挑 IP。
- **「推回 repo」失敗** → repo 設定 → Actions → General →
  Workflow permissions 改成 **Read and write permissions**

---

## 四、（建議）加上 TDX 當備援

TDX 是交通部的官方 API，比開放平臺穩定，而且不挑來源 IP。

1. 到 <https://tdx.transportdata.tw> 免費註冊，拿到 Client Id 與 Client Secret
2. repo → **Settings** → Secrets and variables → **Actions** → New repository secret
   - `TDX_CLIENT_ID`
   - `TDX_CLIENT_SECRET`

加了之後 workflow 會在前兩個來源都失敗時自動退到 TDX。

---

## 五、兩週後把資料抓回來分析

```powershell
cd C:\Users\p\Desktop\Youbike\cloud
git pull
& $py merge_to_db.py --db ..\youbike.db
cd ..
& $py youbike_report.py --weekday --kml .\台中運動基礎設施_研究範圍.kml
```

`merge_to_db.py` 寫出的 schema 跟本機採集的完全一樣，所以：

- 可以直接併進既有的 `youbike.db`，**11/3–11/4 本機採集的資料會保留**
- 重複執行不會重複計算（`(station_id, src_time)` 是主鍵）
- 最後會印出每小時的取樣覆蓋表，一眼就看得出還有沒有缺漏

---

## 注意事項

**GitHub 排程會延遲。** 官方明說 `schedule` 在尖峰時可能延後甚至略過，實測常晚
5–15 分鐘。這對你的分析沒影響，因為每筆資料都帶來源端自己的 `src_time`，
而且台中的站點更新時間並不同步，每小時實際會拿到 8–11 個不同的取樣時刻。

**不需要關掉本機的工作排程器。** 兩邊同時跑，資料會互相補洞，合併時重複的自動去除。
雲端負責你睡覺的時段，本機負責有開機的時段。

**兩週後記得把 workflow 關掉**，不然它會一直跑下去。
repo → Actions → collect → 右上「⋯」→ Disable workflow。

**資料量**：每次約 1846 站、gzip 後 20–40 KB，一天 96 次約 3 MB，兩週約 40 MB。
