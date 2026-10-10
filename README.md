# AJi Hub

**A modular Django intranet with employee services, company communication and a RAG-powered knowledge assistant.**

AJi Hub combines an employee directory, news, policies and brand resources, messaging, service requests, bookings, recognition and configurable content pages in one server-rendered web application. Its Retrieval-Augmented Generation (RAG) system retrieves relevant information from the company's knowledge base to help the AI assistant answer employee questions using internal knowledge.

**بالعربية:** بوابة داخلية تجمع دليل الموظفين، الأخبار والسياسات، المحادثات، طلبات الخدمات والحجوزات وإدارة محتوى الشركة في مكان واحد، وتضم نظام RAG يسترجع المعلومات من قاعدة معرفة الشركة لمساعدة الموظفين في الحصول على إجابات مبنية على المعرفة الداخلية.

## Features

- Employee profiles, skills and directory search.
- News, events, project portfolio and culture pages.
- Policies, onboarding, brand resources and controlled document access.
- Direct/group messaging, notifications and voice-call signalling.
- Service requests, job requisitions, meeting-room and vehicle bookings.
- Cafeteria requests, training and employee recognition.
- Content management for navigation and reusable page sections.
- RAG-powered AI assistant that retrieves relevant company knowledge to support employee questions and answers.
- Configurable external AI/knowledge-base services, LDAP and IIS SSO integrations.

## Technology

| Area | Stack |
| --- | --- |
| Application | Django 5.0.6, Python, Django templates, JavaScript/CSS |
| Database | SQLite locally; optional SQL Server via mssql-django/pyodbc |
| Serving | Waitress, WhiteNoise; optional IIS gateway |
| Documents | openpyxl, python-docx, ReportLab, pypdfium2 |
| Communication | Application messaging, browser WebRTC, optional Python media bridge |
| Authentication | Django accounts; optional LDAP and trusted IIS SSO |

## Windows setup

Use **Python 3.12** for the prepared local setup. Open the project folder in VS Code and use its PowerShell terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-local.txt
$env:DJANGO_SETTINGS_MODULE = "aji_hub.settings_local"
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_service_categories
python manage.py check
python manage.py runserver 127.0.0.1:8000
```

Open **http://127.0.0.1:8000**. Sign in at **http://127.0.0.1:8000/auth/login/** with your new account; Django administration is at **http://127.0.0.1:8000/admin/**.

The database starts empty. Create directory/content records through the management screens or Django admin. Optional `seed_meeting_rooms` and `seed_cafeteria` commands can populate those modules after you review their sample data.

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` instead of `python`. Set `DJANGO_SETTINGS_MODULE` again in each new terminal.

## Linux/macOS setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-local.txt
export DJANGO_SETTINGS_MODULE=aji_hub.settings_local
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_service_categories
python manage.py runserver 127.0.0.1:8000
```

The local settings use SQLite and Django accounts without company LDAP, SQL Server or IIS. They disable the company knowledge-base endpoint and use local static-file storage. No GPU or AI server is required to start the intranet.

## Configuration and integrations

`.env.example` is a reference, **not an automatically loaded file**. Set environment variables through your shell or process manager:

```powershell
$env:AJI_ALLOWED_HOSTS = "localhost,127.0.0.1"
$env:AJIBRAIN_API_BASE = "http://127.0.0.1:8001"
```

The assistant needs a compatible external AI service. The optional RAG endpoint can be enabled with `AJIPROP_API_BASE` in the local settings. Model weights and knowledge-base documents are not included.

For the full deployment stack, install `requirements.txt` in a separate environment. SQL Server needs an ODBC driver and your own `mssql.json`; IIS SSO needs a trusted configured gateway. Production database/SSO configuration is excluded.

Voice calls require browser microphone permissions and a secure origin (localhost has a browser exception). To use the optional media bridge, install its `aiortc`, `aiohttp` and `numpy` dependencies, then run:

```powershell
python manage.py run_media_bridge
```

Cross-network calls may need your own STUN/TURN services. This is separate from starting the base intranet.

## Structure

| Folder | Purpose |
| --- | --- |
| `aji_hub/` | Settings, routes and WSGI |
| `core/`, `people/` | Authentication, audit hooks, configuration and employee directory |
| `content/`, `portal/` | News, events, policies, onboarding and brand resources |
| `messaging/`, `voice/` | Conversations, notifications and calls |
| `services/`, `cafeteria/` | Requests, rooms, fleet and catering |
| `cms/` | Page content and navigation |
| `training/`, `taqdeer/` | Training and recognition |
| `assist/` | AI assistant integration |
| `dam/` | Retained asset-management code; `/library/` is not mounted |
| `templates/`, `static/`, `tools/` | Interface, assets and optional deployment tooling |

## Verification and tests

Preparation checks use Python 3.12: Django system checks, migrations against a new SQLite database, service-category seeding and page-rendering smoke checks. LDAP, IIS, SQL Server, AI and voice/media services need separate deployment testing.

Run the existing application tests with:

```powershell
python manage.py test
```

## Contents and configuration

Employee spreadsheets/photos, message attachments, production databases, secrets, certificates/private keys and generated static output are excluded. Populate a new installation with your own authorized data. No open-source license is granted unless the rights holder adds one.
