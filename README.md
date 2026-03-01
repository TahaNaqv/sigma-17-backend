# Sigma 17 Backend

Django REST API backend for the Sigma 17 actuarial calculation engine. Provides authentication (JWT), RBAC (users, roles, permissions), and core APIs for the web application.

## Requirements

- **Python** 3.12+
- **PostgreSQL** 16+ (or use Docker)
- **Poetry** (recommended) or pip

## Project Structure

```
sigma-17-backend/
├── config/           # Django project settings
├── accounts/         # Auth, users, roles, permissions (RBAC)
├── core/             # Core views (health, registration)
├── manage.py
├── pyproject.toml    # Dependencies (Poetry)
├── .env.example      # Environment template
├── Dockerfile
└── docker-compose.yml
```

## Setup

### 1. Clone and enter the project

```bash
cd sigma-17-backend
```

### 2. Create and activate a virtual environment

**With Poetry (recommended):**

```bash
poetry install
poetry shell
```

**With venv + pip:**

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# or: .venv\Scripts\activate   # Windows

pip install -e .
```

### 3. Environment configuration

Copy the example env file and edit as needed:

```bash
cp .env.example .env
```

Edit `.env` with your settings. Default PostgreSQL connection:

```env
DATABASE_URL=postgres://postgres:postgres@localhost:5432/sigma17
```

### 4. PostgreSQL

**Option A: Run PostgreSQL via Docker**

```bash
docker compose up -d db
```

**Option B: Use a local PostgreSQL instance**

Ensure a database named `sigma17` exists:

```bash
createdb sigma17
```

### 5. Run migrations

```bash
python manage.py migrate
```

### 6. Seed RBAC (optional but recommended)

Creates default permissions and roles (Super Admin, Admin, Actuary, Analyst, Viewer):

```bash
python manage.py seed_rbac
# To force-update existing roles:
python manage.py seed_rbac --force
```

### 7. Create a superuser (optional)

```bash
python manage.py createsuperuser
```

### 8. Run the development server

```bash
python manage.py runserver
```

API base URL: **http://127.0.0.1:8000**

---

## Run with Docker Compose

Starts PostgreSQL and the Django app:

```bash
docker compose up --build
```

- **API:** http://localhost:8000
- **PostgreSQL:** localhost:5432

To run only the database:

```bash
docker compose up -d db
```

Then run migrations and the server locally:

```bash
python manage.py wait_for_db
python manage.py migrate
python manage.py runserver
```

---

## Commands Reference

| Command | Description |
|---------|-------------|
| `python manage.py runserver` | Start development server (port 8000) |
| `python manage.py migrate` | Apply database migrations |
| `python manage.py makemigrations` | Create migrations after model changes |
| `python manage.py createsuperuser` | Create admin user |
| `python manage.py seed_rbac` | Seed permissions and roles |
| `python manage.py seed_rbac --force` | Force-update role permissions |
| `python manage.py wait_for_db` | Wait until PostgreSQL is ready |
| `python manage.py shell` | Django shell |
| `python manage.py test` | Run tests |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health/` | Health check |
| `POST` | `/api/auth/token/` | Obtain JWT (login) |
| `POST` | `/api/auth/token/refresh/` | Refresh JWT |
| `GET` | `/api/auth/me/` | Current user (authenticated) |
| `GET` | `/api/auth/profile/` | User profile (authenticated) |
| `POST` | `/api/auth/change-password/` | Change password (authenticated) |
| `POST` | `/api/register/` | Register new user |
| `GET` | `/api/users/` | List users (admin) |
| `POST` | `/api/users/` | Create user (admin) |
| `GET` | `/api/users/{id}/` | User detail (admin) |
| `PUT` | `/api/users/{id}/` | Update user (admin) |
| `DELETE` | `/api/users/{id}/` | Delete user (admin) |
| `GET` | `/api/roles/` | List roles |
| `GET` | `/api/permissions/` | List permissions |
| `GET` | `/admin/` | Django admin |

---

## Authentication

Uses **JWT** (Simple JWT). Include the access token in requests:

```
Authorization: Bearer <access_token>
```

- **Access token lifetime:** 60 minutes  
- **Refresh token lifetime:** 7 days  

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgres://postgres:postgres@localhost:5432/sigma17` | PostgreSQL connection URL |
| `DB_NAME` | `sigma17` | Database name |
| `DB_USER` | `postgres` | Database user |
| `DB_PASSWORD` | `postgres` | Database password |
| `DB_HOST` | `localhost` | Database host |
| `DB_PORT` | `5432` | Database port |
| `SECRET_KEY` | (insecure default) | Django secret key; **set in production** |
| `DEBUG` | `True` | Debug mode; **False in production** |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,0.0.0.0` | Allowed hosts (comma-separated) |
| `CORS_ALLOW_ALL_ORIGINS` | `True` (when DEBUG) | Allow all CORS origins; restrict in production |

---

## Development Notes

- The backend expects the **sigma-17-dashboard** frontend to run (e.g. on port 5173). CORS is configured for cross-origin requests when `DEBUG=True`.
- For production, use **gunicorn**:  
  `gunicorn config.wsgi:application --bind 0.0.0.0:8000`
- Set `SECRET_KEY`, `DEBUG=False`, and restrict `ALLOWED_HOSTS` and CORS in production.
