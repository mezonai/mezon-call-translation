# MongoDB to PostgreSQL Migration Guide

This document provides a detailed guide on how to execute the data migration process from MongoDB to PostgreSQL for the Orchestrator Service, specifically on a Production environment using a Virtual Environment (`venv`).

## Prerequisites
1. Both MongoDB and PostgreSQL instances must be running.
2. An empty database must be created in PostgreSQL (e.g., `orchestrator_service`).
3. The `.env` file must be configured with connection details for both databases.

---

## Steps to Execute on Production

### Step 1: Activate the Virtual Environment
On your production server, navigate to the project directory and activate the `venv`:

```bash
cd /path/to/Architect_MultiClient_Server/orchestrator_service

# Activate venv (Linux/Ubuntu)
source venv/bin/activate

# (Or if using Windows PowerShell)
# .\venv\Scripts\Activate.ps1
```

### Step 2: Install/Update Dependencies
Install the latest packages and drivers supporting PostgreSQL and migration tools:

```bash
pip install -r requirements-orchestrator.txt
```
*(Ensure packages like `asyncpg`, `sqlalchemy`, and `alembic` are correctly installed)*

### Step 3: Apply Schema Migration (Alembic)
Before migrating the data, you must initialize the tables in PostgreSQL. We use `alembic` to automatically construct the schema based on our predefined models:

```bash
# Apply schema changes to the database
alembic -c alembic.ini upgrade head
```
*(After this step, 8 empty tables will be created in your PostgreSQL database: `users`, `rooms`, `tracks`, `transcript_chunks`, `rooms_summary`, `metadata_events`, `refresh_tokens`, and `token_blacklist`)*

### Step 4: Run Data Migration Script
You can execute the data copy script using one of the two methods below, depending on your environment:

#### Method 1: Using Jupyter Notebook (Recommended for better control)
If your production server supports VSCode Remote or if you have Jupyter installed, you can open `scripts/migrate_mongo_to_pg.ipynb` and run it cell by cell. This allows you to monitor logs visually and ensure data integrity per table.

If you want to run the `.ipynb` file from start to finish via the command line without opening a UI, install `nbconvert` and execute:
```bash
pip install nbconvert ipykernel
jupyter nbconvert --to notebook --execute scripts/migrate_mongo_to_pg.ipynb
```

#### Method 2: Using standard Python Script
If the production server restricts Notebook usage, you can export the Notebook to a python file or run it directly:

```bash
# Export the notebook file to a standard python script
jupyter nbconvert --to script scripts/migrate_mongo_to_pg.ipynb

# Execute the migration script
python scripts/migrate_mongo_to_pg.py
```

### Step 5: Verification and Acceptance
After the script completes, use a Database Management tool (like DBeaver, pgAdmin) to connect to PostgreSQL:
- Verify that the number of records matches the original MongoDB collections.
- Ensure that the MongoDB `_id` strings were correctly converted into valid PostgreSQL UUIDs.
- Restart the `orchestrator_service` and test your API endpoints to confirm smooth operation.

---

## Troubleshooting
- **Database Connection Error**: Double-check your `.env` parameters (note that PostgreSQL config variables usually start with `POSTGRES_...`).
- **Duplicate ID Errors**: The migration script utilizes `ON CONFLICT DO NOTHING`, so you can run the script multiple times safely. It will skip already existing records without causing duplicate primary key conflicts.
