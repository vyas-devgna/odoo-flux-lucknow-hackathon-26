from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def _guard(test):
    def deco(view):
        @wraps(view)
        def wrapped(request, *a, **kw):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not test(request.user):
                raise PermissionDenied
            return view(request, *a, **kw)
        return wrapped
    return deco


internal_required = _guard(lambda u: u.is_internal)
admin_required = _guard(lambda u: u.is_admin_role)
customer_required = _guard(lambda u: u.role == "customer" and hasattr(u, "customer"))


def cap_required(*caps):
    """Staff need at least one of the capability flags; admins always pass. Enforced server-side on every request."""
    return _guard(lambda u: u.is_internal and any(u.has_cap(c) for c in caps))
