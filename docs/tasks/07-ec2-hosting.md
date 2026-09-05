# EC2 Hosting

Lowest priority of the seven — the rubric doesn't require cloud hosting,
and with limited days left before the event, a laptop running both
services live is an equally valid, lower-risk demo. Only do this if tasks
01-06 are done with time to spare.

## What it does

Runs both the FastAPI backend and the React frontend (built as static
files) on one small EC2 instance, so the demo works from any browser
without the presenter's laptop being on stage.

## Setup

1. **Instance**: `t3.small` is enough — this serves one audience live,
   not production traffic. Ubuntu 22.04 LTS.
2. **Security group**: open port `22` (SSH, restricted to your IP), `80`
   (frontend), `8000` (backend API) — or put both behind nginx on `80`
   with `/api` proxied to `8000`, simpler for the browser's CORS story.
3. **Backend**:
   ```
   git clone git@github.com:AdarshZapcom/COD-RTO-backend.git
   cd COD-RTO-backend
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   # copy .env (SUPABASE_DB_URL, SUPABASE_SECRET_KEY, OPENAI_API_KEY) — never commit it
   uvicorn src.api:app --host 0.0.0.0 --port 8000
   ```
   Run under `systemd` (or just `screen`/`tmux` for a one-day demo — a
   full unit file is overkill for a single event) so it survives an SSH
   disconnect.
4. **Frontend**:
   ```
   git clone git@github.com:AdarshZapcom/COD-RTO-frontend.git
   cd COD-RTO-frontend
   npm install && npm run build
   # serve dist/ via nginx, or `npx serve dist -l 80`
   ```
5. **Env vars**: only `.env` values already in use locally
   (`SUPABASE_DB_URL`, `SUPABASE_SECRET_KEY`, `OPENAI_API_KEY`) — nothing
   new to configure.

## Fallback plan (important)

Live reliability is explicitly scored ("Engineering quality: ... runs
live without failure"). Have the laptop able to run the full stack
locally as a fallback (`uvicorn` + `npm run dev`, pointed at the same
Supabase project) in case venue wifi or the EC2 instance has an issue
mid-pitch — decide this before demo day, not during it.

## Acceptance check

From a phone on a different network (not the venue wifi, to catch
security-group misconfiguration early), load the EC2 frontend URL and
complete one full investigation end to end.
