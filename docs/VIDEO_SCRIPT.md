# DevInbox — 3-Minute Demo Video Script

**0:00–0:20 — Intro**
"Hi, I'm [Name] and this is DevInbox — an AI-powered Issue-to-PR Autopilot built on Qwen Cloud."
"Maintainers spend huge amounts of time triaging issues. DevInbox automates that, end to end, with a human always making the final call."

**0:20–0:45 — Dashboard setup**
- Open `/dashboard/keys`
- Paste Qwen Cloud key → Save → Test (green)
- Paste GitHub token → Save → Test (shows authenticated user)
- Briefly mention: "Every classification, diff, and decision is also archived to Alibaba Cloud OSS for a durable audit trail."

**0:45–1:15 — Create a real issue and run it**
- Go to a GitHub repo, open a new issue describing a real bug
- Trigger the pipeline — either automatically (webhook already installed) or via the manual endpoint: "DevInbox can also be pointed at any public issue directly with a single API call, which matters for repos you don't have admin access to install a webhook on."
- Switch to `/dashboard/activity` — show the pipeline steps live: classification → code investigation (real `search_repo`/`read_file` tool calls) → fix generation → PR created

**1:15–1:45 — Show the PR**
- Switch to GitHub, show the draft PR with the generated diff and description
- Point out: it's a DRAFT — nothing merges automatically
- Mention: "If DevInbox doesn't have write access to a repo — say, an external open-source project — it automatically forks it first and opens a cross-repo pull request back to the original project, exactly like a human contributor would."

**1:45–2:15 — Human approval, done right**
- Mark the PR "Ready for review"
- From a **second GitHub account**, comment `/approve`
- Explain: "Approval has to come from a genuinely different identity — DevInbox rejects approval comments from its own account, so it can never merge its own work."
- Show the merge happening automatically

**2:15–2:35 — Handling ambiguity**
- Run the agent on a non-actionable issue (a question or feature request, not a bug)
- Show it correctly declines to open a PR: "It doesn't just pattern-match — it recognizes when something isn't actually a code fix."

**2:35–3:00 — Close**
"DevInbox: autonomous issue resolution with a human always in the loop, working on your own repos or forking to contribute to open source.
Built with Qwen Cloud, FastAPI, and deployed on Alibaba Cloud. Thanks for watching!"
