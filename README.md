# Spring 2027 Course Build Dashboard

A shareable web dashboard showing where each course sits in the 8-stage build
pipeline, generated from the **Spring 2027 Course Builds** Asana board.

Hosted on Netlify as a static site. Each deploy re-pulls Asana at build time, so
the Asana token lives only as a build secret and never reaches the browser.

```
GitHub push / scheduled hook ─▶ Netlify build ─▶ generate_dashboard.py
                                                    │ fetch Asana (ASANA_TOKEN)
                                                    ▼
                                          public/index.html ─▶ your-site.netlify.app
```

## Files
| File | Role |
|---|---|
| `generate_dashboard.py` | Fetches Asana, computes each course's stage, writes the HTML. Standard library only — no `pip install`. |
| `template.html` | The dashboard design. Edit styling/columns here. |
| `netlify.toml` | Build command + publish dir for Netlify. |
| `.github/workflows/refresh.yml` | Scheduled job that triggers a Netlify rebuild (auto-refresh). |

## One-time setup

### 1. Get an Asana Personal Access Token
Asana → **Settings → Apps → Manage Developer Apps → Personal access tokens →
Create new token**. Copy it (shown once). **Do not commit it anywhere.**

### 2. Connect the repo to Netlify
1. [app.netlify.com](https://app.netlify.com) → **Add new site → Import an existing project**.
2. Pick GitHub → this repo. Netlify reads `netlify.toml`, so build command and
   publish dir are filled in automatically.
3. Before the first deploy: **Site configuration → Environment variables → Add**
   a variable named `ASANA_TOKEN` with your token as the value.
4. **Deploy site.** You'll get a `https://<name>.netlify.app` URL to share.

### 3. Turn on scheduled auto-refresh
1. Netlify → **Site configuration → Build & deploy → Build hooks → Add build hook**
   (name it "Scheduled refresh"). Copy the URL.
2. GitHub repo → **Settings → Secrets and variables → Actions → New repository
   secret**: name `NETLIFY_BUILD_HOOK`, value = the build hook URL.
3. The workflow in `.github/workflows/refresh.yml` will now trigger a rebuild on
   its schedule (default: 8am/12pm/4pm ET, weekdays). Adjust the `cron` line to change it.
   You can also trigger a refresh anytime from the repo's **Actions → Refresh dashboard → Run workflow**.

## Run it locally (optional)
```bash
export ASANA_TOKEN=your_token_here
python3 generate_dashboard.py --out public/index.html
open public/index.html
```
Or from a saved Asana `GET /tasks` dump (no token):
```bash
python3 generate_dashboard.py --from-file tasks.json --out public/index.html
```

## How stage is computed
A course is 8 top-level Asana tasks sharing the course name, each tagged with a
**Stage** custom field. A stage is done when its task is complete. The **current
stage** is the first unfinished stage; all 8 complete → **Live**. Items without a
Stage (the Curriculum-Committee entries) are ignored.

Pipeline order: Course Plan → Global Resource & Shell Setup → Act I → Act II →
Act III → Act IV → Peer Review → Baselining.

Lead and Stage Due reflect the task for the course's current stage.
