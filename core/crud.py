"""A tiny CRUD kit so catalogue screens stay declarative: list + search + pagination, create, edit, delete."""
from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from . import audit
from .permissions import admin_required, internal_required

PAGE_SIZE = 25


def paginate(request, qs, size=PAGE_SIZE):
    return Paginator(qs, size).get_page(request.GET.get("page"))


class Resource:
    def __init__(self, ns, slug, model, form, title, columns, search=(), write="admin", inline=None, queryset=None,
                 empty="Nothing here yet.", filters=(), blurb=""):
        self.ns, self.slug, self.model, self.form, self.title = ns, slug, model, form, title
        self.columns, self.search, self.inline, self.empty, self.filters, self.blurb = columns, search, inline, empty, filters, blurb
        self.write_guard = admin_required if write == "admin" else internal_required
        self.write = write
        self.queryset = queryset or (lambda: model.objects.all())

    def url(self, name, *args):
        return reverse(f"{self.ns}:{self.slug}_{name}", args=args)

    @property
    def url_list(self):
        return self.url("list")

    @property
    def url_new(self):
        return self.url("new")

    def can_write(self, user):
        return user.is_admin_role if self.write == "admin" else user.is_internal

    # --- views ---
    def list_view(self):
        @internal_required
        def view(request):
            qs = self.queryset()
            q = request.GET.get("q", "").strip()
            if q and self.search:
                cond = Q()
                for f in self.search:
                    cond |= Q(**{f"{f}__icontains": q})
                qs = qs.filter(cond)
            active = {}
            for key, field, choices in self.filters:
                val = request.GET.get(key, "")
                if val:
                    qs = qs.filter(**{field: val})
                active[key] = val
            return render(request, "crud/list.html", {
                "res": self, "page": paginate(request, qs), "q": q, "can_write": self.can_write(request.user),
                "filters": [(k, c, active[k]) for k, _, c in self.filters], "title": self.title})
        return view

    def form_view(self, creating):
        def view(request, pk=None):
            obj = None if creating else get_object_or_404(self.queryset(), pk=pk)
            form = self.form(request.POST or None, instance=obj)
            fs = self.inline(request.POST or None, instance=obj or self.model()) if self.inline else None
            if request.method == "POST" and form.is_valid() and (fs is None or fs.is_valid()):
                saved = form.save()
                audit.log(request.user, f"{self.slug}.{'create' if creating else 'update'}", saved)
                if fs is not None:
                    fs.instance = saved
                    fs.save()
                messages.success(request, f"{self.model._meta.verbose_name.title()} saved.")
                return redirect(self.url("list"))
            return render(request, "crud/form.html", {"res": self, "form": form, "formset": fs, "obj": obj,
                                                      "title": ("New " if creating else "Edit ") + self.model._meta.verbose_name})
        return self.write_guard(view)

    def delete_view(self):
        @self.write_guard
        def view(request, pk):
            obj = get_object_or_404(self.queryset(), pk=pk)
            if request.method == "POST":
                try:
                    label = f"{self.slug}.delete"
                    snapshot = type("Gone", (), {"pk": obj.pk, "__str__": lambda s, o=obj: str(o)})()
                    obj.delete()
                    audit.log(request.user, label, snapshot)
                    messages.success(request, "Deleted.")
                except ProtectedError:
                    messages.error(request, "It is in use elsewhere, so it can't be deleted. Mark it inactive instead.")
            return redirect(self.url("list"))
        return view

    def urls(self):
        s = self.slug
        return [
            path(f"{s}/", self.list_view(), name=f"{s}_list"),
            path(f"{s}/new/", self.form_view(True), name=f"{s}_new"),
            path(f"{s}/<int:pk>/", self.form_view(False), name=f"{s}_edit"),
            path(f"{s}/<int:pk>/delete/", self.delete_view(), name=f"{s}_delete"),
        ]


class StyledFormMixin:
    """Gives every widget a consistent class; checkboxes get the switch style."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs["class"] = "switch"
            elif isinstance(w, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                pass
            elif isinstance(w, (forms.DateInput,)):
                w.input_type = "date"
                w.format = "%Y-%m-%d"
                w.attrs["class"] = "input"
            elif isinstance(w, forms.SelectMultiple):
                w.attrs["class"] = "input multi"
            else:
                w.attrs["class"] = "input"


class StyledModelForm(StyledFormMixin, forms.ModelForm):
    pass


def apply_sort(request, qs, allowed, default):
    """?sort=field or ?sort=-field against a whitelist (key -> orm field)."""
    key = request.GET.get("sort", default)
    desc = key.startswith("-")
    field = allowed.get(key.lstrip("-"))
    if not field:
        return qs, default
    return qs.order_by(("-" if desc else "") + field, "-id"), key
