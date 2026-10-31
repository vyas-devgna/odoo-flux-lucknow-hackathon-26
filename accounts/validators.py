import re

from django.core.exceptions import ValidationError


class BrightloopPasswordValidator:
    """More than eight characters, with upper, lower and a special character."""

    def validate(self, password, user=None):
        errors = []
        if len(password) <= 8:
            errors.append("Use more than 8 characters.")
        if not re.search(r"[A-Z]", password):
            errors.append("Add an uppercase letter.")
        if not re.search(r"[a-z]", password):
            errors.append("Add a lowercase letter.")
        if not re.search(r"[^A-Za-z0-9]", password):
            errors.append("Add a special character.")
        if errors:
            raise ValidationError(errors)

    def get_help_text(self):
        return "More than 8 characters with an uppercase letter, a lowercase letter and a special character."
