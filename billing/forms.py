from django import forms

from core.crud import StyledFormMixin

from .models import Payment


class PaymentForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["amount", "method", "date", "note"]
        widgets = {"date": forms.DateInput()}
