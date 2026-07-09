# Recruiter CV screening tool — deployment guide

A small web tool for a team of recruiters: upload a JD, upload CVs, get a
ranked shortlist with cited rationale, and ask questions about it in a chat
panel. Shared by everyone who signs in with the team password — one
recruiter's scoring run is visible to the whole team.

Everything here was tested before you got it — see "What was actually
tested" at the bottom.

## Deploy it in a few minutes (no server, no IT)

**1. Put this folder in a GitHub repo** (free, and it can be private):
   - Create a new repo at https://github.com/new — make it **Private** if this
     will ever touch real candidate data
   - Upload all the files in this folder to that repo (drag-and-drop works on
     github.com, or use `git push` if you're comfortable with git)
   - **Do not** upload `.streamlit/secrets.toml` if you created one locally —
     the `.gitignore` in this folder already excludes it, so a normal git push
     won't include it

**2. Deploy on Streamlit Community Cloud** (free):
   - Go to https://share.streamlit.io and sign in with GitHub
   - Click **New app**, pick the repo you just created, and set the main file
     to `app.py`
   - Before clicking Deploy, open **Advanced settings → Secrets** and paste:
     ```
     ANTHROPIC_API_KEY = "sk-ant-your-real-key"
     APP_PASSWORD = "choose-a-shared-password-for-your-recruiters"
     ```
   - Click **Deploy**. In a minute or two you'll get a URL like
     `https://your-app-name.streamlit.app` — that's the link to share with
     your recruiters.

**3. Share the link and the password** with your 2-5 recruiters. That's the
whole rollout — no install on their end, works in any browser.

## Using it

1. **Setup tab** — upload or paste the JD, click *Extract screening criteria*.
   Then upload CV files (multi-select works) and click *Score candidates*.
2. **Shortlist tab** — ranked list; click any candidate to see the
   per-criterion scores and rationale.
3. **Ask questions tab** — a chat box grounded only in the scored candidates.
   Anyone on the team can ask questions here at any time, not just right
   after scoring.

Uploading a **new JD** clears the previous shortlist (a fresh requisition
starts fresh) — this is intentional, so old candidates from a different role
never get mixed into a new one.

## Important limits of this setup — read before rolling out with real CVs

- **The password gate is a single shared password**, appropriate for a small,
  known team — it is not per-user login, and it does not log who did what.
  If you need to know which recruiter scored or asked what, or need
  different access levels, this needs a real auth system before that matters.
- **Storage is a single JSON file on the app's server**, not a proper
  database. It's genuinely fine for 2-5 people and one requisition at a time,
  but: (a) Streamlit Community Cloud's free tier can restart your app after
  inactivity, and anything not explicitly persisted elsewhere may be lost —
  re-run scoring if the shortlist ever looks empty after a long gap; (b) it
  only tracks **one requisition at a time** — starting a new JD replaces the
  old shortlist entirely, it doesn't keep a history of past roles.
- **If you outgrow this**: the natural next step is a small free-tier
  Postgres database (e.g. Supabase) instead of the JSON file, and real
  per-recruiter login instead of a shared password — worth asking for that
  upgrade once this proves useful rather than building it up front.
- **This still never auto-rejects anyone.** Every score is a starting point
  for a human decision, same principle as every version of this tool so far.
- **Real candidate data**: check with whoever owns data protection/compliance
  before putting real CVs through any third-party API, even one as
  reputable as Anthropic's — this is a policy question, not a technical one,
  and it's better asked before rollout than after.

## What was actually tested before this was handed to you

- File parsing (`.txt`, `.docx` including tables) against real files — confirmed correct
- JSON parsing from both bare and markdown-fenced model output — confirmed correct
- Weighted-score math — confirmed correct against hand-calculated expected values
- The app boots without errors (confirmed via direct HTTP request to a running instance)
- The password gate — confirmed it blocks access before login and grants it after
- JD upload → criteria extraction → save-to-file — confirmed with mocked Claude
  responses (no live API calls were made during testing, to avoid needing a
  real key in this environment)
- Shortlist tab rendering of pre-scored data (criteria list, per-candidate
  score and rationale) — confirmed correct

**Not tested**: OCR extraction and legacy `.doc` handling (no Tesseract/
system tools in the testing environment), and the live Anthropic API calls
themselves (no real key was used during testing). Try both on a couple of
real files first before trusting this for your full CV batch.
