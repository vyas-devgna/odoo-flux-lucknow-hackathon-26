import uuid

from django import forms

from core.crud import StyledFormMixin

from .models import Payment

STAFF_METHODS = [(k, l) for k, l in Payment.METHODS if k != "razorpay"]


class PaymentForm(StyledFormMixin, forms.Form):
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01)
    method = forms.ChoiceField(choices=STAFF_METHODS)
    date = forms.DateField(widget=forms.DateInput())
    reference = forms.CharField(max_length=120, required=False, label="Reference", help_text="Bank/UPI transaction id or cheque number.")
    note = forms.CharField(max_length=240, required=False)
    idem = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if not self.is_bound:
            self.fields["idem"].initial = uuid.uuid4().hex  # a double click or browser retry posts the same key


class ReportPaymentForm(StyledFormMixin, forms.Form):
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01)
    method = forms.ChoiceField(choices=[("bank", "Bank transfer"), ("upi", "UPI"), ("cheque", "Cheque")])
    reference = forms.CharField(max_length=120, label="Transaction reference")
    date = forms.DateField(widget=forms.DateInput())


class CreditNoteForm(StyledFormMixin, forms.Form):
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01, label="Credit amount (incl. tax)")
    reason = forms.CharField(max_length=200)


class RefundForm(StyledFormMixin, forms.Form):
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01)
    method = forms.ChoiceField(choices=STAFF_METHODS)
    date = forms.DateField(widget=forms.DateInput())
    note = forms.CharField(max_length=200, required=False, label="Reason")
