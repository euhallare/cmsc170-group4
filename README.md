# AMIS Scheduling CSP — CMSC 170 Project Toolkit

Collect UPLB AMIS class data **once**, then build **two** CSP datasets from it:

| Dataset | Problem | Variables | Values | Hard constraint |
|---|---|---|---|---|
| **A – Faculty** | Faculty course-load scheduler | class sections | instructors | one instructor can't teach two overlapping sections |
| **B – Student** | Student schedule builder | curriculum slots (e.g. "CMSC 12") | LEC+LAB bundles | no two chosen bundles overlap |

We pick A or B later. Nothing is wasted.

## Files

| File | What it is |
|---|---|
| `auth.py` | Opens a browser, you log in to AMIS, it returns a Bearer token |
| `scrape.py` | READ-ONLY collector. Downloads class pages, strips instructor names, saves JSON |
| `build_datasets.py` | Turns raw JSON into CSV/JSON datasets A and B (standard library only) |
| `curriculum.csv` | BS CS 2018 flowchart (SP + Thesis tracks) used to build student instances |
| `.gitignore` | Keeps login sessions and tokens out of GitHub |

## Do you need to scrape at all?

**Only one person needs to scrape.** Everyone else can skip to
[Step 6](#step-6--build-the-datasets) if the scraped data is already in the repo
(`data/raw/term_1261/`). It has instructor names removed, so it is safe to commit.

If you do want to scrape yourself, follow every step below.

---

## Setup on Windows with WSL (what we used)

Tested on: Windows + WSL2 + Ubuntu.

### Step 1 — Check WSL can show windows

`auth.py` opens a visible Chromium window so you can log in to AMIS. Open **Ubuntu**
(Start menu) and run:

```bash
echo $DISPLAY
```

- Prints `:0` (or similar) → good, continue.
- Prints nothing → in **PowerShell** run `wsl --update` then `wsl --shutdown`,
  reopen Ubuntu, and check again.

Also check you are on WSL **2** (PowerShell): `wsl -l -v` should show `VERSION 2`.

### Step 2 — Make the project folder in Linux (not in `/mnt/c`)

Working inside `/mnt/c/...` is slow and can break the saved browser login.

```bash
mkdir -p ~/amis_project && cd ~/amis_project
```

Copy the project files in from Windows (change the path to wherever yours are;
`ls /mnt/c/Users` shows the usernames):

```bash
cp /mnt/c/Users/<WINDOWS_USERNAME>/Desktop/CMSC170/project/{auth.py,scrape.py,build_datasets.py,curriculum.csv} .
ls
```

If you cloned the repo instead, just `cd` into the cloned folder.

### Step 3 — Install Python and a virtual environment

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
pip install requests playwright
```

Your prompt should now start with `(.venv)`.
**Every new terminal needs `source .venv/bin/activate` again.**

### Step 4 — Install the browser

```bash
playwright install --with-deps chromium
```

This asks for your password and takes a few minutes. Leave the
`# executable_path=...` line in `auth.py` commented out.

Test that a window opens:

```bash
python -c "from playwright.sync_api import sync_playwright as s; p=s().start(); b=p.chromium.launch(headless=False); pg=b.new_page(); pg.goto('https://example.com'); input('window visible? press Enter'); b.close(); p.stop()"
```

You should see a Chromium window with "Example Domain". Press Enter to close it.

### Step 5 — Scrape

Always run from the project folder, with `(.venv)` showing.

**5a. Probe first (about 15 requests):**

```bash
python scrape.py --probe
```

A Chromium window opens. Log in to AMIS with Google if asked. You have about 60
seconds. The window closes by itself and the terminal prints how the API behaves.

What we found: for CMSC in term 1261 there are **267 sections**, and `items=50`
works (6 pages instead of 14). The `ERROR` lines for `per_page`, `limit` and
`size` are harmless (those are not real parameters).

**5b. Real scrape (CMSC only):**

```bash
python scrape.py --items 50
```

Later, once the instructor has confirmed it is okay, to also get MATH, STAT, HK,
NSTP and the GE subjects:

```bash
python scrape.py --set curriculum --items 50
```

Notes:
- Raw pages are saved to `data/raw/term_1261/<SUBJECT>/page_NNNN.json`.
- Ctrl+C is safe. Running again skips pages already saved.
- It waits 1–2 s between requests and only sends GET requests. It never
  enlists, bookmarks or changes anything in AMIS.
- The token lasts about 30 minutes and refreshes automatically. After the first
  login the window may flash open and close quickly.
- Change the term with `--term <ID>` (we use `1261`).

### Step 6 — Build the datasets

```bash
python build_datasets.py
```

This only needs the standard library. Output goes to `data/datasets/term_1261/`.
It prints a `=== SUMMARY ===` at the end (also saved in `summary.json`).

### Step 7 — Look at the output

```bash
ls -R data/datasets
explorer.exe data/datasets/term_1261     # opens the folder in Windows Explorer
```

```
data/datasets/term_1261/
├── sections.csv        one row per class section (LEC or LAB)
├── meetings.csv        one row per (section, day) meeting; times in minutes after midnight
├── summary.json        counts to quote on the project page
├── faculty/            Dataset A
│   ├── assignments.csv     who teaches what right now (real-life baseline)
│   ├── instructors.csv     per-instructor load summary (faculty_id only)
│   └── instance.json       the CSP: variables = sections, values = instructors
└── student/            Dataset B
    ├── bundles.csv         LEC+LAB bundles (what a student actually enlists in)
    ├── coverage.csv        which curriculum courses exist this term
    └── instances.json      one CSP instance per curriculum term (Y1-FS, Y1-SS, ...)
```

---

## Our results so far (term 1261, CMSC only)

| Item | Count |
|---|---|
| Rows scraped | 267 |
| Sections (incl. 27 LEC parents found inside LAB rows) | 294 |
| LEC / LAB | 179 / 115 |
| Sections with no meeting times | 130 |
| Schedulable sections (faculty CSP variables) | 164 |
| Instructors | 40 |
| Baseline conflicts in the real assignment | 0 |
| Student bundles / usable bundles | 267 / 137 |
| Student instances (curriculum terms) | 10 |

**The 130 sections with no meetings are expected.** They are CMSC 190 (Special
Problem), CMSC 200, CMSC 300, CMSC 400 (thesis/research) and CMSC 198 (practicum).
These have no fixed time, so they cannot clash and are left out of the CSP.

Curriculum courses shown as "without offering" (MATH, STAT, HK, NSTP) are only
missing because we scraped CMSC only. Some CMSC courses (e.g. 127, 128, 131, 137)
may simply not be offered this semester.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Could not get a Bearer token` | Log in within ~60 s in the browser window, then run the command again |
| Browser fails to launch | Run `playwright install --with-deps chromium` (don't skip `--with-deps`) |
| No window appears | `echo $DISPLAY` must print `:0`; otherwise `wsl --update` and `wsl --shutdown` in PowerShell |
| Google says "this browser may not be secure" | Run `playwright install chrome` and add `channel="chrome"` to the launch call in `auth.py` |
| `ModuleNotFoundError: playwright` | You forgot `source .venv/bin/activate` |
| `429` / "too many requests" | Stop, wait, and run again later. Do not remove the delays |
| Token keeps expiring | Normal after ~30 min; the script refreshes it itself |

Each person logs in with **their own** AMIS account. Use a different session folder
per account if you switch accounts: `get_fresh_bearer_token("amis_session_name")`.

## Privacy and safety

- Instructor names and user ids are removed in `scrape.py` before anything is
  saved. Only the numeric `faculty_id` stays.
- **Never commit `amis_session*/`, any token, or your own enlistment scripts
  (`main.py`).** `.gitignore` covers these:

```
amis_session*/
main.py
__pycache__/
.venv/
*.token
```

- The scraper only reads data. Do not run it at the same time as any enlistment
  script.

## For the project page (disclosure)

State that:
- the data was collected from AMIS with a read-only script using our own login,
- instructor names were removed at scrape time,
- AI assistance (Claude) was used to write the collection and preprocessing
  scripts and this README, and every member can explain every line.

`requests` and `playwright` are only used for *collecting* data. The algorithms we
implement later must use only the standard library, NumPy and Matplotlib. Confirm
with the instructor that using them for data collection is fine.

## Known assumptions

- Faculty "eligible instructors" for a section = instructors who already teach that
  subject prefix this term (our assumption; AMIS has no expertise data). With CMSC
  only, this makes every instructor eligible for every section, so we plan to
  refine it (per course) or add more subjects.
- Load = weekly meeting minutes (unit-based load is ambiguous).
- Curriculum prerequisites in `curriculum.csv` were read off the flowchart arrows;
  compare with the `prerequisites` column from AMIS and fix differences.