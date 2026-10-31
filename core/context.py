from django.conf import settings
from django.db import OperationalError, ProgrammingError


def shell(request):
    from .models import CompanySettings
    try:
        company = CompanySettings.get()
    except (OperationalError, ProgrammingError):  # before the first migrate
        company = None
    return {"COMPANY": company.name if company else settings.COMPANY_NAME, "CUR": settings.CURRENCY_SYMBOL, "company": company}
