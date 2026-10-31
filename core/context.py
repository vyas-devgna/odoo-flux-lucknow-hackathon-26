from django.conf import settings


def shell(request):
    return {"COMPANY": settings.COMPANY_NAME, "CUR": settings.CURRENCY_SYMBOL}
