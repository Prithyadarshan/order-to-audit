# Publish it: GitHub, live link, demo video

## 1. Push to a public GitHub repository (keeps the commit history)
```bash
cd order-to-audit
git log --oneline                    # check the history, tidy messages if you wish (git rebase -i)
# create an empty PUBLIC repo on github.com first (no README), then:
git remote add origin https://github.com/<your-username>/order-to-audit.git
git branch -M main
git push -u origin main
```
Do not squash the history into one commit: the brief penalises a single "final upload".
Make a few more small commits of your own (for example a README tweak or a new test) so the history also shows your work.

## 2. Live link (optional but recommended)
**Render (free):** Dashboard > New > Blueprint > select the repo. `render.yaml` creates the web service and a PostgreSQL
database and sets `DATABASE_URL` and `SECRET_KEY`. The first start creates the tables and loads the Section 5 seed data.
Put the resulting `https://....onrender.com` URL and the demo logins in the README. Free services sleep after idle time,
so open the link once before the evaluators do.
*The Blueprint and Dockerfile were written but not run on Render.* The same image does run against PostgreSQL 16 locally
(the whole test suite passes on it).

**Any machine with Docker:** `docker compose up --build` then open http://localhost:8000.

## 3. Demo video (max 8 minutes)
A ready-made narrated walkthrough, `walkthrough.mp4` (about 4.5 minutes, synthetic voice), was generated from the real app
with `python scripts/make_demo_video.py out_dir` (needs weasyprint, ffmpeg, espeak-ng, poppler). You can upload it as is
(YouTube *Unlisted* or Drive) or record your own live version as described below, which usually reads better to evaluators.

Follow `docs/DEMO_SCRIPT.md` step by step. Reset the data first with `python -m app.seed --reset`.
Record with any screen recorder (Windows: Win+G; macOS: Shift+Cmd+5; or OBS). Show a phone-width browser window for the
salesman steps (browser dev tools, device toolbar). Upload to YouTube as *Unlisted* or to Google Drive with link sharing on.

## 4. Submit
One link to the repository. In its README add the video link and the live link (or leave the run command).
