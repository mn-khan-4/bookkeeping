# AI Bookkeeping Agent Platform

Backend: FastAPI + async SQLAlchemy + Xero OAuth2 integration. Frontend: single-page vanilla dashboard.

## Quick Start

```powershell
cd "d:\Bookkeeping Agent\Backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` from `.env.template` and set `DATABASE_URL` plus Xero credentials.

Run the API:

```powershell
uvicorn app.main:app --reload
```

Open the dashboard:

```powershell
start "" "d:\Bookkeeping Agent\Frontend\index.html"
```

## Database Setup

Run Alembic init/migrations (manual step):

```powershell
cd "d:\Bookkeeping Agent\Backend"
alembic init app/db/migrations
alembic revision --autogenerate -m "initial_schema"
alembic upgrade head
```
