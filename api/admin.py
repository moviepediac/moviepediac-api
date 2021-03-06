from django.contrib import admin
from django import forms
from import_export.admin import ExportMixin
from api.models import (
    Profile,
    Role,
    Order,
    Movie,
    Genre,
    MpGenre,
    MovieLanguage,
    MovieRateReview,
    MovieList,
    Package,
    CrewMember,
    CrewMemberRequest,
    ContestType,
    Contest,
    Notification,
)


class ProfileAdmin(ExportMixin, admin.ModelAdmin):
    search_fields = ["user__first_name", "user__last_name", "user__username", "city"]
    exclude = ["follows"]
    list_display = [
        "user",
        "city",
        "mobile",
        "gender",
        "dob",
        "curator_rank",
        "creator_rank",
        "mcoins",
    ]
    list_filter = ["is_celeb", "gender"]
    readonly_fields = ["image"]


class RoleAdmin(admin.ModelAdmin):
    list_display = ["name"]


class OrderAdmin(ExportMixin, admin.ModelAdmin):
    search_fields = [
        "owner__first_name",
        "owner__last_name",
        "owner__email",
        "order_id",
        "payment_id",
    ]
    list_display = ["owner", "order_id", "payment_id"]


class MovieModelForm(forms.ModelForm):

    poster = forms.ImageField(required=False)

    fields = "__all__"

    def clean_poster(self):
        print("clean poster called")
        print(self.cleaned_data)
        # save poster for the film and return the URL for the same
        return self.cleaned_data["poster"]

    def save(self, commit=True):
        print("save called")
        poster = self.cleaned_data.pop("poster", None)
        print(poster)
        return super().save(commit=commit)

    class Meta:
        model = Movie
        exclude = []


class MovieAdmin(ExportMixin, admin.ModelAdmin):
    search_fields = ["title", "link"]
    list_filter = [
        "approved",
        "state",
        "created_at",
        ("order__payment_id", admin.EmptyFieldListFilter),
    ]
    list_display = [
        "title",
        "state",
        "link",
        "approved",
        "is_paid",
        "created_at",
        "director",
        "director_name",
        "submited_by",
        "poster",
    ]
    ordering = ["-created_at", "title"]
    readonly_fields = ["poster"]
    filter_horizontal = ["contests"]

    def submited_by(self, movie):
        return movie.order.owner

    def director(self, movie):
        return movie.crewmember_set.get(role__name="Director").profile.user

    def director_name(self, movie):
        return movie.crewmember_set.get(
            role__name="Director"
        ).profile.user.get_full_name()

    def is_paid(self, movie):
        return movie.order.payment_id is not None


class GenreAdmin(admin.ModelAdmin):
    list_display = ["name"]


class MpGenreAdmin(admin.ModelAdmin):
    list_display = ["name"]


class MovieLanguageAdmin(admin.ModelAdmin):
    list_display = ["name"]


class MovieRateReviewAdmin(ExportMixin, admin.ModelAdmin):
    list_display = ["id", "author", "content", "movie", "published_at", "rating"]


class MovieListAdmin(ExportMixin, admin.ModelAdmin):
    search_fields = ["owner__email", "owner__first_name", "owner__last_name", "name"]
    list_display = ["name", "owner", "contest", "frozen"]
    list_filter = ["contest"]


class PackageAdmin(admin.ModelAdmin):
    list_display = ["name", "amount"]


class CrewMemberAdmin(ExportMixin, admin.ModelAdmin):
    search_fields = ["profile__user__email", "movie__title"]
    list_filter = ["role"]
    list_display = [
        "user",
        "movie",
        "role",
    ]

    def user(self, membership):
        return membership.profile.user.email


class CrewMemberRequestAdmin(ExportMixin, admin.ModelAdmin):
    search_fields = ["user__user__email", "movie__title"]
    list_filter = ["role", "state"]
    list_display = ["requestor", "movie", "user", "role", "state", "reason"]


class ContestTypeAdmin(admin.ModelAdmin):
    list_display = ["name"]


class MoviesInContest(ExportMixin, admin.TabularInline):
    model = Contest.movies.through


class ContestAdmin(ExportMixin, admin.ModelAdmin):
    list_display = [
        "name",
        "start",
        "end",
        "days_per_movie",
        "state",
    ]
    inlines = [
        MoviesInContest,
    ]


class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "profile", "content"]


admin.site.register(Profile, ProfileAdmin)
admin.site.register(Role, RoleAdmin)
admin.site.register(Order, OrderAdmin)
admin.site.register(Movie, MovieAdmin)
admin.site.register(Genre, GenreAdmin)
admin.site.register(MpGenre, MpGenreAdmin)
admin.site.register(MovieLanguage, MovieLanguageAdmin)
admin.site.register(MovieRateReview, MovieRateReviewAdmin)
admin.site.register(Package, PackageAdmin)
admin.site.register(MovieList, MovieListAdmin)
admin.site.register(CrewMember, CrewMemberAdmin)
admin.site.register(CrewMemberRequest, CrewMemberRequestAdmin)
admin.site.register(ContestType, ContestTypeAdmin)
admin.site.register(Contest, ContestAdmin)
admin.site.register(Notification, NotificationAdmin)
