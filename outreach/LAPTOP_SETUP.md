# Laptop setup — step by step

Pehli baar sab kuch chalane ke liye. Roz ka use sabse neeche hai.

Commands do tarah ke diye hain: **macOS / Linux** aur **Windows (PowerShell)**. Apne wala use karo.

---

## Chahiye kya

| | |
|---|---|
| **Python 3.9 ya upar** | `python3 --version` (Windows: `python --version`). Nahi hai to [python.org](https://www.python.org/downloads/) |
| **Git** | `git --version`. Nahi hai to [git-scm.com](https://git-scm.com/downloads) |
| **Docker Desktop** | Sirf tab jab Grist apne laptop pe chalana ho (Step 2B). Hosted Grist ke liye zaroori nahi |

---

## Sabse chhota raasta (ek command)

Docker ki zaroorat nahi. Step 1 aur Step 2A karo (code + Grist account), `.env` bharo, phir:

```bash
./setup.sh
```

Ye packages install karta hai, tests chalata hai, tables banata hai, workbook import karta hai aur
dashboard pages bana deta hai — ~15 second. Dobara chalana safe hai.
Kuch adhoora ho to saaf-saaf batata hai ki kya karna hai.

Niche ke steps wahi cheezein ek-ek karke hain, agar alag se chalani hon.

---

## Step 1 — Code laao

```bash
git clone https://github.com/Ayush9205NITRR/job-application.git
cd job-application
git checkout claude/amazing-curie-ga0cxl
cd outreach
```

Aage ke saare commands `outreach` folder ke andar se chalenge.

---

## Step 2 — Grist kahan rahega? (ek chuno)

### 2A — Hosted (recommended)

Laptop band ho tab bhi chalta hai, link share ho sakta hai, aur GitHub ka auto-refresh isi pe kaam karega.

1. [docs.getgrist.com](https://docs.getgrist.com) pe sign up (free)
2. **Add New → Create empty document**, naam `Outreach`
3. Doc ke URL se ID copy karo:
   `docs.getgrist.com/o/docs/`**`abc123XYZ`**`/Outreach` → ID hai `abc123XYZ`
4. Upar right mein apna naam → **Profile Settings** → **API Key** → **Create** → copy

Free plan: 5,000 rows, 10MB, 3,000 API calls/month. Hamara data ~1,000 rows hai aur ek refresh 10 se kam calls leta hai.

### 2B — Apne laptop pe (Docker)

Private rehta hai, par Grist tabhi chalega jab laptop on ho — aur GitHub Actions ka auto-refresh kaam nahi karega.

1. Docker Desktop kholo aur chalne do
2. `docker-compose.yml` mein `you@example.com` ki jagah apna email daalo
3. ```bash
   docker compose up -d
   ```
4. http://localhost:8484 kholo → **Create empty document**, naam `Outreach`
5. URL se doc ID copy karo, aur Profile Settings → API Key banao

---

## Step 3 — `.env` banao

```bash
cp .env.example .env
```
```powershell
Copy-Item .env.example .env
```

`.env` ko kisi bhi text editor mein kholo aur teen line bharo:

```
GRIST_URL=https://docs.getgrist.com      # 2B wale: http://localhost:8484
GRIST_API_KEY=<Step 2 wali key>
GRIST_DOC_ID=<Step 2 wala doc id>
```

`.env` gitignored hai — GitHub pe kabhi nahi jaayegi. macOS/Linux pe ek extra safety:

```bash
chmod 600 .env
```

---

## Step 4 — Python packages

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Har nayi terminal mein `activate` wali line dobara chalani hogi.

---

## Step 5 — Sab theek hai ya nahi, check karo

```bash
python3 tests/test_all.py
python3 tests/test_ui.py
python3 tests/test_tracker.py
```

Teeno "passed" bolein. (Teesra bolega "skipped workbook checks" agar workbook abhi `imports/` mein nahi hai — wo theek hai.)

---

## Step 6 — Tables banao

```bash
python3 scripts/setup_grist.py
```

Dobara chalana safe hai — jo missing ho sirf wahi banata hai.

Grist kholo → **Founders** table → `Company` column pe click → right panel → **SHOW COLUMN** = `Company`.
Yahi `Outreach_Log` ke `Founder` column ke liye bhi karo. (Ek hi baar ka kaam.)

---

## Step 7 — Purana tracker data daalo

Workbook ko `imports/` folder mein copy karo, phir:

```bash
python3 scripts/import_tracker.py --file imports/Email_Outreach_Tracker.xlsx --dry-run
```

Ye kuch likhta nahi, sirf batata hai kya hoga. Numbers aise aane chahiye:

```
333 people, 194 companies
99 emails attempted: 92 delivered (6 replied), 7 bounced, 40 queued
```

Theek lage to asli import:

```bash
python3 scripts/import_tracker.py --file imports/Email_Outreach_Tracker.xlsx
```

Dobara chalaoge to `+0` likhega — kuch duplicate nahi hoga.

---

## Step 8 — Dashboard pages banao

```bash
python3 scripts/make_pages.py
```

Grist refresh karo. Left sidebar mein paanch nayi pages:

| Page | Kya dikhata hai |
|---|---|
| **Review** | Ek card per person jo aapke jawab ka intezaar kar raha hai, Approve ke saath |
| **Pipeline** | Status ke hisaab se counts, phir poori list |
| **Duplicates** | Jo `Is_Dup` formula ne pakde |
| **Funnel** | Bar charts: stage, status, designation, companies |
| **Data health** | Import mein kya galat mila, aur refresh runs ka log |

Dobara chalana safe hai — sirf apni hi banayi pages hatata hai, aapki banayi hui nahi.

**Yahan tak aa gaye to system chal raha hai.** Aage ke steps optional hain.

---

## Step 9 (optional) — Gmail se Sent / Replied / Bounced

Isse pata chalta hai kis mail ka jawab aaya aur kaun bounce hua.

1. [Google Cloud Console](https://console.cloud.google.com) → naya project
2. **APIs & Services → Library** → "Gmail API" → **Enable**
3. **OAuth consent screen** → External → apna email test user mein add karo
4. **Credentials → Create credentials → OAuth client ID → Desktop app** → JSON download
5. Us file ka naam `credentials.json` rakho aur `outreach` folder mein daal do
6. ```bash
   python3 scripts/reverse_map.py --dry-run
   ```
   Browser khulega, sign in karo. `token.json` ban jaayegi.
7. Theek lage to:
   ```bash
   python3 scripts/reverse_map.py --apply
   ```

Scope **read-only** hai. Is repo ka koi bhi code mail bhej nahi sakta.
`credentials.json` aur `token.json` dono gitignored hain.

---

## Step 10 (optional) — Apne aap update hota rahe

### Laptop pe (cron / Task Scheduler)

```bash
python3 scripts/refresh.py --apply
```

Ye `imports/` ki nayi ya badli hui files uthata hai (hash dekhta hai, to purani file free hai) aur phir Gmail check karta hai. Har run `Refresh_Runs` table mein ek row likhta hai.

macOS/Linux pe din mein 3 baar, `crontab -e`:

```
37 3,8,14 * * * cd ~/job-application/outreach && .venv/bin/python3 scripts/refresh.py --apply >> ~/.outreach-refresh.log 2>&1
```

### GitHub Actions pe (laptop band ho tab bhi)

Sirf **hosted Grist (2A)** ke saath kaam karega — GitHub aapke laptop tak nahi pahunch sakta.

Repo → **Settings → Secrets and variables → Actions** → ye secrets daalo:

| Secret | Value |
|---|---|
| `GRIST_URL` | `https://docs.getgrist.com` |
| `GRIST_API_KEY` | Step 2 wali key |
| `GRIST_DOC_ID` | Step 2 wala doc id |
| `GOOGLE_CLIENT_ID` | `credentials.json` se |
| `GOOGLE_CLIENT_SECRET` | `credentials.json` se |
| `GOOGLE_REFRESH_TOKEN` | `token.json` se (`refresh_token` field) |

Aakhri teen na daalo to bhi chalega — bas Gmail wala step skip ho jaayega.

Test karne ke liye: repo → **Actions** → "Outreach refresh" → **Run workflow**.
Uske baad apne aap din mein 3 baar chalega (9:07, 2:07, 8:07 IST).

---

## Roz ka use

```bash
cd job-application/outreach
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
```

| Kya karna hai | Command |
|---|---|
| Naya Apollo CSV daalna | file `imports/` mein daalo, phir `python3 scripts/refresh.py --apply` |
| Gmail se status update | `python3 scripts/refresh.py --apply` |
| Sirf dekhna kya badlega | `python3 scripts/refresh.py --dry-run` |
| Pages dobara banana | `python3 scripts/make_pages.py` |
| Kaam karna | Grist kholo — **Review** page pe approve karo, baaki pages dekho |

Drafts likhwane ke liye Claude Code `outreach` folder ke andar se kholo aur `/run-b`, `/run-c` chalao.

---

## Kuch galat ho to

| Dikhta hai | Matlab |
|---|---|
| `Set GRIST_API_KEY and GRIST_DOC_ID in .env` | `.env` nahi bani, ya `outreach` folder ke bahar se command chalayi |
| `Cannot reach Grist at ...` | Hosted: URL `https://docs.getgrist.com` hona chahiye. Local: `docker compose up -d` chala hai? |
| `HTTP 401 ... invalid API key` | API key galat ya adhoori copy hui |
| `No such file: imports/...` | Workbook `imports/` folder mein nahi hai |
| `ModuleNotFoundError: openpyxl` | venv activate nahi hua, ya `pip install -r requirements.txt` nahi chala |
| Grist pe `/boot` screen | Sirf local pe hota hai; `docker-compose.yml` mein `GRIST_IN_SERVICE` isse rokta hai — `docker compose up -d` dobara chalao |
| `table Founders is missing` | Pehle `python3 scripts/setup_grist.py` chalao |

Kuch aur atke to error ka poora text bhej dena — dekh lunga.
