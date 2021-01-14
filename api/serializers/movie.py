from django.utils import timezone
from django.db.models.functions import ExtractMonth, ExtractYear
from rest_framework.exceptions import ValidationError
from api.models.movie import TopCreator, TopCurator
from logging import getLogger
import hashlib
import os
import re

from django.conf import settings
from django.db.models import Avg, Count
from django.db import transaction
from django.core.files.storage import default_storage
from rest_framework import serializers
from collections import defaultdict
import razorpay

from api.constants import MOVIE_STATE, CREW_MEMBER_REQUEST_STATE, RECOMMENDATION
from api.models import (
    User,
    Movie,
    Genre,
    MovieLanguage,
    MoviePoster,
    Order,
    Package,
    Profile,
    Role,
    CrewMember,
    MovieRateReview,
    MovieList,
    CrewMemberRequest,
    Contest,
)
from .profile import ProfileSerializer, UserSerializer

logger = getLogger(__name__)

rzp_client = razorpay.Client(
    auth=(settings.RAZORPAY_API_KEY, settings.RAZORPAY_API_SECRET)
)


class GenreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genre
        fields = ["id", "name"]
        extra_kwargs = {
            "name": {"validators": []},
        }

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation["name"] = representation["name"].title()
        return representation

    def validate_name(self, name):
        return name and name.lower()


class MovieLanguageSerializer(serializers.ModelSerializer):
    name = serializers.CharField(max_length=50)

    class Meta:
        model = MovieLanguage
        fields = ["id", "name"]

    def validate_name(self, name):
        name = name.strip().lower()
        return name

    def create(self, validated_data):
        name = validated_data.get("name")
        try:
            lang = MovieLanguage.objects.get(name=name)
            logger.debug(f"language `{name}` exists")
        except MovieLanguage.DoesNotExist:
            lang = MovieLanguage.objects.create(name=name)
            logger.debug(f"New language `{name}` added")
        return lang

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation["name"] = representation["name"].title()
        return representation


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["name"]

    def validate_name(self, name):
        if not Role.objects.filter(name=name).exists():
            raise ValidationError(f"Unknown role '{name}'")
        return name


def create_rzp_order(package, owner):
    if not all([package, owner]):
        return
    existing_orders = Order.objects.filter(owner=owner).count()
    receipt_number = hashlib.md5(
        f"{owner.email}:{existing_orders}".encode()
    ).hexdigest()
    try:
        rp_order_res = rzp_client.order.create(
            {
                "amount": package.amount,
                "currency": "INR",
                "receipt": receipt_number,
                "payment_capture": 1,
                "notes": {"email": owner.email},
            }
        )
    except Exception:
        logger.error("Exception in creating Razorpay order")
        raise
    else:
        if rp_order_res.get("status") != "created":
            logger.error(f"Error response from razorpay: {rp_order_res}")
            raise Exception("Error creating Razorpay order")
        return rp_order_res


class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = ["id", "owner", "order_id", "amount", "payment_id"]


class PackageSerializer(serializers.ModelSerializer):
    name = serializers.CharField()
    amount = serializers.FloatField()

    class Meta:
        model = Package
        fields = ["name", "amount"]

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation["name"] = representation.get("name", "").title()
        return representation


class DirectorSerializer(serializers.Serializer):
    first_name = serializers.CharField()
    last_name = serializers.CharField(allow_blank=True)
    email = serializers.EmailField()
    contact = serializers.CharField(min_length=10, required=False)


class GroupedCrewMemberSerializer(serializers.Serializer):
    roles = RoleSerializer(many=True)
    profile = ProfileSerializer()


class CrewMemberSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source="role.name")
    profile_id = serializers.IntegerField(source="profile.id")
    name = serializers.CharField(source="profile.user.get_full_name")

    class Meta:
        model = CrewMember
        fields = ["role", "profile_id", "name"]


class SubmissionEntrySerializer(serializers.ModelSerializer):
    order = OrderSerializer()
    package = serializers.CharField(source="package.name", read_only=True)

    class Meta:
        model = Movie
        fields = ["id", "title", "poster", "state", "order", "created_at", "package"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        package = data.get("package")
        data["package"] = package and package.title()
        return data


class MovieSerializerSummary(serializers.ModelSerializer):
    contest = serializers.SerializerMethodField()
    crew = CrewMemberSerializer(source="crewmember_set", many=True)

    class Meta:
        model = Movie
        fields = [
            "id",
            "title",
            "poster",
            "about",
            "is_live",
            "contest",
            "crew",
            "state",
            "score",
            "created_at",
            "recommend_count",
            "publish_on",
            "runtime",
        ]

    def get_contest(self, obj):
        contest = obj.contest
        if contest:
            return contest.name


class ContestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contest
        fields = ["id", "name", "is_live", "start", "end"]


class MovieSerializer(serializers.ModelSerializer):
    order = OrderSerializer(required=False)
    lang = MovieLanguageSerializer()
    genres = GenreSerializer(many=True)
    package = PackageSerializer(required=False)
    director = DirectorSerializer(write_only=True, required=False)
    # Roles of the user(Profile) who submitted the movie (Creator roles)
    roles = RoleSerializer(write_only=True, many=True)
    crew = serializers.SerializerMethodField()
    requestor_rating = serializers.SerializerMethodField(read_only=True)
    # is watch listed by the requestor if he is authenticated
    is_watchlisted = serializers.SerializerMethodField(read_only=True)
    # is recommended by the requestor if he is authenticated
    is_recommended = serializers.SerializerMethodField(read_only=True)
    contest = ContestSerializer(read_only=True)

    class Meta:
        model = Movie
        fields = [
            "id",
            "order",
            "title",
            "link",
            "state",
            "runtime",
            "genres",
            "lang",
            "poster",
            "package",
            "crew",
            "director",
            "roles",
            "jury_rating",
            "audience_rating",
            "requestor_rating",
            "is_watchlisted",
            "is_recommended",
            "publish_on",
            "contest",
            "about",
            "approved",
            "recommend_count",
        ]
        read_only_fields = ["about", "state"]

    def get_requestor_rating(self, movie):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            review = MovieRateReview.objects.filter(
                movie=movie, author=request.user
            ).first()
            return MovieReviewSerializer(instance=review).data

    def get_is_recommended(self, movie):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            recommendation_list = MovieList.objects.filter(
                owner=request.user, name=RECOMMENDATION
            ).first()
            if recommendation_list:
                return recommendation_list.movies.filter(id=movie.id).exists()
        return False

    def get_is_watchlisted(self, movie):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            return movie.watchlisted_by.filter(user=request.user).exists()
        return False

    def get_crew(self, movie):
        # since one user can have multiple roles, we can
        # reduce the number of items returned by grouping the crew relationship by user
        group_by_user = defaultdict(list)
        for crewmember in movie.crewmember_set.all():
            group_by_user[crewmember.profile].append(crewmember.role)
        data = [
            {"profile": profile, "roles": roles}
            for profile, roles in group_by_user.items()
        ]
        serializer = GroupedCrewMemberSerializer(many=True, instance=data)
        return serializer.data

    def validate_package(self, package):
        logger.debug(f"validate_package::{package}")

        if package and self.instance.package:
            logger.warn("Cannot update package")
            raise serializers.ValidationError("Cannot update package")

        package_name = package.get("name")
        package_name = package_name.strip().lower()
        if not Package.objects.filter(name=package_name).exists():
            logger.warn("Invalid package selected")
            raise serializers.ValidationError("Invalid Package Selected")
        return package

    def validate_title(self, title):
        title = re.sub("[\n\t\s]+", " ", title)
        if not title.isascii():
            raise ValidationError(
                "Movie title should be in English i.e., characters [A-Z] and [0-9]"
            )
            pass
        return title.title()

    def create(self, validated_data: dict):
        logger.debug(f"create::{validated_data}")
        user = validated_data.pop("user")
        genres_data = validated_data.pop("genres")
        creator_roles = validated_data.pop("roles", [])
        lang_data = validated_data.pop("lang")
        director_data = validated_data.pop("director", {})

        validated_data["lang"] = MovieLanguageSerializer().create(lang_data)
        # creating dummy order here so that the movie entry can be tracked
        # back to the creator using movie.order.owner
        validated_data["order"] = Order.objects.create(owner=user)
        validated_data["state"] = MOVIE_STATE.CREATED
        creator_is_director = any(
            role.get("name") == "Director" for role in creator_roles
        )
        if creator_is_director:
            validated_data["approved"] = creator_is_director
        logger.debug("before movie created")
        movie = super().create(validated_data)
        logger.debug("after movie created")
        movie.genres.set(self._get_or_create_genres(genres_data))
        self._attach_creator_roles(creator_roles, user, movie, creator_is_director)
        self._attach_director_role(director_data, user, movie, creator_is_director)
        if not self._is_director_present(movie):
            raise ValidationError("Director must be provided")
        return movie

    def update(self, movie, validated_data):
        logger.debug(f"update::{validated_data}")
        user = validated_data.pop("user", None)
        package_data = None
        if "package" in validated_data:
            package_data = validated_data.pop("package")
            # If package was not earlier set
            package_name = package_data.get("name").strip().lower()
            movie.package = Package.objects.get(name=package_name)
            rzp_order = create_rzp_order(movie.package, movie.order.owner)
            self._update_order_details(movie, rzp_order)
        if "lang" in validated_data:
            lang_data = validated_data.pop("lang")
            validated_data["lang"] = MovieLanguageSerializer().create(lang_data)

        director_data = validated_data.pop("director", {})
        creator_roles = validated_data.pop("roles", [])
        creator_is_director = any(
            role.get("name") == "Director" for role in creator_roles
        )
        if creator_is_director and "approved" not in validated_data:
            validated_data["approved"] = creator_is_director

        if creator_roles:
            self._attach_creator_roles(creator_roles, user, movie, creator_is_director)
        if director_data:
            self._attach_director_role(director_data, user, movie, creator_is_director)

        genres_data = None
        if "genres" in validated_data:
            genres_data = validated_data.pop("genres")

        movie = super().update(movie, validated_data)

        if genres_data:
            movie.genres.set(self._get_or_create_genres(genres_data))

        if not self._is_director_present(movie):
            raise ValidationError("Director must be provided")
        return movie

    def _is_director_present(self, movie):
        director_role = Role.objects.get(name="Director")
        return CrewMember.objects.filter(movie=movie, role=director_role).exists()

    def _attach_director_role(
        self,
        director_data: dict,
        creator: User,
        movie: Movie,
        creator_is_director: bool,
    ):
        if creator_is_director:
            director_data["email"] = creator.email

        if director_data:
            # creator is not the director
            contact = director_data.pop("contact", None)
            email = director_data.get("email")
            director_data["username"] = email
            try:
                # check if the director is already registered
                director_profile = Profile.objects.get(user__email=email)
            except Profile.DoesNotExist:
                # creating a new profile, will be onboarded later
                # prevents the welcome and verification email
                user = User.objects.create(**director_data)
                director_profile = Profile.objects.create(
                    user=user, mobile=contact, onboarded=False
                )

            # remove existing director relation on movie
            director_role = Role.objects.get(name="Director")
            CrewMember.objects.filter(role=director_role, movie=movie).delete()
            CrewMember.objects.create(
                profile=director_profile, movie=movie, role=director_role
            )

    def _attach_creator_roles(
        self,
        creator_roles_data: list,
        creator: User,
        movie: Movie,
        creator_is_director: bool,
    ):
        """Attach all non-director roles to the creator
        if creator is the director then add CrewMember otherwise add as CrewMemeberRequest
        """
        director_role = Role.objects.get(name="Director")
        creator_role_names = [
            role.get("name")
            for role in creator_roles_data
            if role.get("name") != "Director"
        ]
        creator_roles = Role.objects.filter(name__in=creator_role_names).all()
        logger.debug(f"creator_roles:{creator_roles}")
        creator_profile = Profile.objects.get(user__id=creator.id)
        # clear all roles of creator
        if creator_is_director:
            CrewMember.objects.filter(movie=movie, profile=creator_profile).exclude(
                role__in=[director_role]
            ).delete()
        else:
            CrewMemberRequest.objects.filter(movie=movie).delete()
        # add all roles of creator
        for creator_role in creator_roles:
            if creator_is_director:
                CrewMember.objects.create(
                    profile=creator_profile, movie=movie, role=creator_role
                )
                logger.debug(f"crew::{movie.crewmember_set.all()}")
            else:
                CrewMemberRequest.objects.create(
                    requestor=creator, movie=movie, user=creator, role=creator_role
                )
                logger.debug(f"crew::{movie.crewmemberrequest_set.all()}")

    def _update_order_details(self, movie, rzp_order):
        logger.debug(f"_update_order::{rzp_order}")
        if not movie or not rzp_order:
            return
        order = movie.order
        order.order_id = rzp_order.get("id")
        order.amount = rzp_order.get("amount")
        order.receipt_number = rzp_order.get("receipt")
        order.save()

    def _get_or_create_genres(self, genres_data):
        names = [name.get("name") for name in genres_data]
        names = [name.strip().lower() for name in names if name]
        existing_genres = list(Genre.objects.filter(name__in=names).all())
        logger.info(f"existing genres: {existing_genres}")
        return existing_genres

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for float_key in ["audience_rating", "jury_rating"]:
            value = data.get(float_key)
            if value:
                data[float_key] = "{:.1f}".format(value)
        return data


class SubmissionSerializer(serializers.Serializer):
    poster = serializers.ImageField(required=False)
    payload = serializers.JSONField()

    def validate_payload(self, payload):
        logger.debug(f"validate_payload::{payload}")
        serializer = MovieSerializer(
            data=payload, partial=self.partial, instance=self.instance
        )
        serializer.is_valid(raise_exception=True)
        logger.debug("validate_payload:: is valid")
        return payload

    def create(self, validated_data):
        logger.debug(f"create::{validated_data}")
        payload = validated_data["payload"]
        payload["user"] = validated_data["user"]
        movie = MovieSerializer().create(payload)
        movie.poster = self._save_poster(validated_data, movie.id)
        movie.save()
        logger.debug("create::end")
        return movie

    def update(self, instance, validated_data):
        logger.debug(f"update::{validated_data}")
        payload = validated_data.get("payload")
        payload["user"] = validated_data["user"]
        movie = MovieSerializer().update(instance, payload)
        movie.poster = self._save_poster(validated_data, movie.id)
        logger.debug("update::end")
        return movie

    def to_representation(self, instance):
        return MovieSerializer().to_representation(instance)

    def _save_poster(self, validated_data, movie_id):
        if not validated_data:
            return
        if "poster" in validated_data:
            poster_file = validated_data.get("poster")
            return self._write_poster(poster_file, movie_id)

    def _write_poster(self, poster, movie_id):
        if not poster:
            return
        ext = poster.name.split(".")[-1]
        poster_filename = f"{movie_id:010d}.{ext}"
        poster_path = os.path.join(settings.MEDIA_POSTERS, poster_filename)
        if default_storage.exists(poster_path):
            default_storage.delete(poster_path)
        poster_filename = default_storage.save(poster_path, poster)
        url = default_storage.url(poster_filename)
        logger.debug(f"poster saved at: {url}")
        return url


class MoviePosterSerializer(serializers.ModelSerializer):
    class Meta:
        model = MoviePoster
        fields = ["link", "primary", "movie"]


class MovieReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = MovieRateReview
        fields = ["id", "content", "rating", "published_at", "rated_at"]


class MinUserSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="get_full_name")

    class Meta:
        model = User
        fields = ["id", "name"]


class MovieReviewDetailSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    liked_by = MinUserSerializer(read_only=True, many=True)
    # serializers.IntegerField(source="liked_by.count", read_only=True)
    published_at = serializers.DateTimeField(read_only=True)
    rated_at = serializers.DateTimeField(read_only=True)
    # added for my reviews page, normal reviews for a movie don't use this,
    # can be moved to a new serializer and new view
    movie = MovieSerializerSummary(read_only=True)
    movie_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = MovieRateReview
        fields = [
            "id",
            "author",
            "content",
            "liked_by",
            "published_at",
            "rated_at",
            "rating",
            "movie",
            "movie_id",
        ]

    def validate(self, validated_data):
        if all([key not in validated_data for key in ["content", "rating"]]):
            raise serializers.ValidationError(
                "At least one of `content` or `rating` should be provided"
            )
        return validated_data

    def _update_movie_audience_rating(self, movie):
        if movie is not None:
            # FIXME: this average audience rating update might get into concurrency issue
            # a better way(IMO) will be to cache the average rating of movies via a job periodically
            movie.audience_rating = (
                MovieRateReview.objects.filter(movie=movie)
                .exclude(rating__isnull=True)
                .aggregate(Avg("rating"))
            ).get("rating__avg")
            movie.save()

    def create(self, validated_data):
        validated_data["author"] = validated_data.pop("user")
        if validated_data.get("rating") is not None:
            validated_data["rated_at"] = timezone.now()
        instance = super().create(validated_data)
        self._update_movie_audience_rating(instance.movie)
        return instance

    def update(self, instance, validated_data):
        rating = validated_data.get("rating")
        if rating is not None and instance.rating != rating:
            if (
                instance.rating is not None
                and timezone.now() > instance.rated_at + timezone.timedelta(seconds=9)
            ):
                raise ValidationError("Rating is now freezed")
            else:
                validated_data["rated_at"] = timezone.now()
        instance = super().update(instance, validated_data)
        self._update_movie_audience_rating(instance.movie)
        return instance


class MovieListSerializer(serializers.ModelSerializer):
    like_count = serializers.IntegerField(source="liked_by.count", read_only=True)
    owner = UserSerializer(read_only=True)
    movies_count = serializers.IntegerField(source="movies.count", read_only=True)
    pages = serializers.SerializerMethodField()

    class Meta:
        model = MovieList
        fields = [
            "id",
            "owner",
            "name",
            "movies",
            "movies_count",
            "like_count",
            "frozen",
            "pages",
        ]

    def create(self, validated_data: dict):
        user = validated_data.pop("user")
        return MovieList.objects.create(**validated_data, owner=user)

    def get_pages(self, movie_list):
        return (
            movie_list.movies.values(
                pub_month=ExtractMonth("publish_on"), pub_year=ExtractYear("publish_on")
            ).annotate(movies=Count("pub_month"))
            # order_by NULL is removing the default ordering set via meta class in
            .order_by("-pub_year", "-pub_month")
        )


class CrewMemberRequestSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    role = RoleSerializer(read_only=True)
    requestor = UserSerializer(read_only=True)
    movie_title = serializers.CharField(source="movie.title", read_only=True)

    # write only
    name = serializers.CharField(write_only=True)
    email = serializers.EmailField(write_only=True)
    roles = serializers.PrimaryKeyRelatedField(
        queryset=Role.objects.all(), many=True, write_only=True
    )

    class Meta:
        model = CrewMemberRequest
        fields = [
            "id",
            "name",
            "email",
            "roles",
            "movie",
            "requestor",
            "user",
            "role",
            "movie_title",
            "state",
        ]
        read_only_fields = [
            "id",
            "requestor",
            "role",
            "user",
            "movie_title",
        ]

    def _create_new_user(self, name, email):
        email = email.strip().lower()
        name_segs = [seg.strip() for seg in name.split(" ")]
        first_name = name_segs[0]
        last_name = " ".join(name_segs[1:])
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            user = User.objects.create_user(
                first_name=first_name,
                last_name=last_name,
                username=email,
                email=email,
            )
        return user

    def create(self, validated_data):
        requestor = validated_data.pop("logged_in_user")
        movie = validated_data.get("movie")
        validated_data["requestor"] = requestor
        email = validated_data.pop("email")
        name = validated_data.pop("name")
        instance = None
        director = Role.objects.get(name="Director")
        requestor_is_director_of_movie = CrewMember.objects.filter(
            profile__user=requestor, role=director, movie=movie
        ).exists()
        state = (
            CREW_MEMBER_REQUEST_STATE.APPROVED
            if requestor_is_director_of_movie
            else CREW_MEMBER_REQUEST_STATE.SUBMITTED
        )
        with transaction.atomic():
            user = self._create_new_user(name, email)
            validated_data["user"] = user
            for role in validated_data.pop("roles"):
                validated_data["role"] = role
                instance = CrewMemberRequest.objects.create(
                    **validated_data, state=state
                )
        return instance


class TopCreatorSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer()

    class Meta:
        model = TopCreator
        fields = [
            "score",
            "recommend_count",
            "profile",
        ]

    def to_representation(self, value):
        value = super().to_representation(value)
        value.update(value.pop("profile"))
        return value


class TopCuratorSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer()

    class Meta:
        model = TopCurator
        fields = [
            "match",
            "recommend_count",
            "profile",
        ]

    def to_representation(self, value):
        value = super().to_representation(value)
        value.update(value.pop("profile"))
        return value
