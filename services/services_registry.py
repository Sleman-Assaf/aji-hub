# services/services_registry.py
"""Catalog of 'Services' menu items and the permission group required to see each.

To add a new service later: add an entry here (and its view/urls) — the left
Services drawer in base.html picks entries up automatically via
services.context_processors.services_context.
"""

SERVICE_CATALOG = [
    {
        'key': 'job_requisition',
        'label': 'Job Requisition',
        'description': 'Request approval to open a new position',
        'url_name': 'services:job_requisition_new',
        'group': 'Hiring Managers',
        'icon': 'briefcase',
    },
]
