# DevInbox — 3-Minute Demo Video Script

**0:00–0:25 — Intro**
"Hi, I'm [Name] and this is DevInbox — an AI-powered Issue-to-PR Autopilot built on Qwen Cloud."
"Maintainers spend huge amounts of time triaging issues. DevInbox automates that."

**0:25–0:55 — Dashboard setup**
- Open `/dashboard/keys`
- Paste Qwen Cloud key → Save → Test (green)
- Paste GitHub token → Save → Test (shows authenticated user)

**0:55–1:40 — Create a real issue**
- Go to a GitHub repo, open a new issue describing a real bug
- Switch to DevInbox `/dashboard/activity` — show the pipeline steps live:
  webhook received → classification → code generation → PR created

**1:40–2:20 — Show the PR**
- Switch to GitHub, show the draft PR with the generated diff and description
- Point out: it's a DRAFT — nothing merges automatically

**2:20–2:50 — Human approval**
- Review the code, comment `/approve`
- Show DevInbox merging the PR and posting a success comment

**2:50–3:00 — Close**
"DevInbox: autonomous issue resolution with a human always in the loop.
Built with Qwen Cloud, FastAPI, and deployed on Alibaba Cloud. Thanks for watching!"
