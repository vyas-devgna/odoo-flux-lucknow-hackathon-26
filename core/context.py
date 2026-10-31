from django.conf import settings


def shell(request):
    from .models import CompanySettings
    try:
        company = CompanySettings.get()
    except Exception:  # before first migrate
        company = None
    return {"COMPANY": company.name if company else settings.COMPANY_NAME, "CUR": settings.CURRENCY_SYMBOL, "company": company}
