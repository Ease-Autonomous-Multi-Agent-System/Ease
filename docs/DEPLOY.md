# Deploying Ease as a public website (free, on Hugging Face Spaces)

The result is a permanent public website like `https://<your-username>-ease.hf.space`. Anyone can open it, create
an account, add **their own** free AI keys and use every feature. No credit card is needed, and your laptop can be
off.

**Whose keys are used:** the public site runs with `USER_KEYS_ONLY=true`. Every run uses the Groq / Gemini /
search / Notion / Telegram keys of the person who started it, stored encrypted in their own account. The server
has no keys of yours, so visitors cannot spend your quota, and sign-up can be open to everyone.

The whole system runs in one container: database, Redis, API, worker with Chromium, web app and the demo test
sites. The free Space has 2 CPUs and 16 GB of memory. Data resets whenever the Space restarts.

## 1. Create the Space (5 minutes)

1. Create a free account at https://huggingface.co/join.
2. Go to https://huggingface.co/new-space and set:
   - **Space name:** `ease`
   - **SDK:** **Docker** → template **Blank**
   - **Hardware:** CPU basic (free)
   - **Visibility:** Public
3. Click **Create Space**.

## 2. Upload two files (2 minutes)

In the Space, open **Files → Add file → Upload files** and upload these two files from this repository:

- `deploy/hf/Dockerfile`
- `deploy/hf/README.md` (replaces the Space's README; it holds the Space settings)

Click **Commit changes to main**. The build starts automatically. No secrets are required.

Optional, under **Settings → Variables and secrets**:

| Name | Type | Why |
|---|---|---|
| `JWT_SECRET`, `VAULT_MASTER_KEY` | secret | Fixed values instead of new random ones on each start. Not needed, because data resets on restart anyway. |
| `FRAME_ANCESTORS` = `https://huggingface.co` | variable | Show the site inside the Space page too, not only at its direct link. |

## 3. Wait for the build (about 15 minutes the first time)

The **Logs** tab shows progress: it clones this GitHub repository, builds the website, installs Chromium, then
prints `Ease is starting on port 7860`. The status badge turns **Running**.

## 4. Open and share it

- **Your website:** `https://<your-username>-ease.hf.space` (also under **⋯ → Embed this Space**).
- Visitors land on the sign-in page, which links to **What is Ease?** and **Getting started**. The guide walks them
  through getting free Groq and Gemini keys and adding them under **Profile & apps → AI model keys**.

## Keeping it up to date

After pushing new code to GitHub `main`, open the Space **Settings → Factory rebuild**. It clones the latest code.

## Good to know

- **Sleeping:** a free Space sleeps after 48 hours without visitors. The next visit wakes it in about a minute.
- **Resets:** a restart or rebuild clears accounts and runs. Users sign up again and re-add their keys.
- **Fair use:** each account gets 150 AI calls per day, 15 tasks per hour and 2 runs at a time. The 2 CPUs are
  shared, so busy times queue runs.
- **Safety on a public server:**
  - Keys are encrypted per user (AES-256-GCM) and never sent to an AI model.
  - Rate limits use each visitor's real IP.
  - The browser agent can reach the demo sites but not the API, database or Redis inside the container
    (`FIXTURE_PORTS`).
  - The interactive API docs are switched off.
- **A private server instead:** to let a few people use *your* keys, set `USER_KEYS_ONLY=false` plus
  `GROQ_API_KEY`, `GEMINI_API_KEY` and `REGISTRATION_INVITE_CODE` as secrets. The server refuses to start in that
  mode without an invite code.
