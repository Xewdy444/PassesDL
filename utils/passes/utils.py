"""Utility classes for the Passes client."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from http.client import responses
from typing import Annotated, Any, Dict

import annotated_types
from patchright.async_api import Response
from pydantic import BaseModel, HttpUrl

from .errors import PlaywrightResponseError

Post = Dict[str, Any]


class ImageType(Enum):
    """Image types available for download."""

    SMALL = "signedUrlSm"
    MEDIUM = "signedUrlMd"
    LARGE = "signedUrlLg"
    ORIGINAL = "signedUrlDash"

    def __str__(self) -> str:
        return self.name.lower()


class VideoType(Enum):
    """Video types available for download."""

    LARGE = "signedUrl"
    ORIGINAL = "signedUrlDash"

    def __str__(self) -> str:
        return self.name.lower()


class Media(BaseModel):
    """A class representing media content."""

    user_id: str
    signed_url: str
    content_id: str
    content_type: str
    extension: str

    @property
    def is_encrypted(self) -> bool:
        return "/drm2/" in self.signed_url


class PostFilter:
    """
    A class for filtering Passes posts.

    Parameters
    ----------
    post : Post
        The post to filter.
    images : bool, optional
        Whether to filter posts with images, by default False.
    videos : bool, optional
        Whether to filter posts with videos, by default False.
    accessible_only : bool, optional
        Whether to filter posts with accessible media only, by default False.
    from_timestamp : datetime, optional
        The minimum timestamp for posts to filter, by default datetime.min.
    to_timestamp : datetime, optional
        The maximum timestamp for posts to filter, by default datetime.max.
    """

    def __init__(
        self,
        *,
        images: bool = False,
        videos: bool = False,
        accessible_only: bool = False,
        from_timestamp: datetime = datetime.min,
        to_timestamp: datetime = datetime.max,
    ) -> None:
        self.images = images
        self.videos = videos
        self.accessible_only = accessible_only
        self.from_timestamp = from_timestamp
        self.to_timestamp = to_timestamp

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(images={self.images!r}, "
            f"videos={self.videos!r}, "
            f"accessible_only={self.accessible_only!r}, "
            f"from_timestamp={self.from_timestamp!r}, "
            f"to_timestamp={self.to_timestamp!r})"
        )

    def __call__(self, post: Post) -> bool:
        """
        Determine whether a post meets the filter criteria.

        Parameters
        ----------
        post : Post
            The post to filter.

        Returns
        -------
        bool
            Whether the post meets the filter criteria.
        """
        contents = post.get("contents", [post])

        if any((self.images, self.videos)) and not (
            self.images
            and any(content["contentType"] == "image" for content in contents)
            or self.videos
            and any(content["contentType"] == "video" for content in contents)
        ):
            return False

        if self.accessible_only and not any(
            "signedContent" in content for content in contents
        ):
            return False

        post_timestamp_string = post.get("createdAt") or post.get("sentAt")
        post_timestamp = datetime.fromisoformat(post_timestamp_string.rstrip("Z"))

        if not self.from_timestamp <= post_timestamp <= self.to_timestamp:
            return False

        return True


class StaticResponse(BaseModel):
    """A static version of an asynchronous Playwright response."""

    url: HttpUrl
    status: Annotated[int, annotated_types.Ge(100), annotated_types.Le(599)]
    headers: Dict[str, str]
    body: bytes

    def raise_for_status(self) -> None:
        """Raise an exception if the response status is not OK."""
        if not self.ok:
            raise PlaywrightResponseError(self.status, self.status_text, self.url)

    @property
    def ok(self) -> bool:
        """Whether the response status is OK."""
        return self.status < 400

    @property
    def status_text(self) -> str:
        """The status text of the response status."""
        return responses.get(self.status, "Unknown")

    @classmethod
    async def from_response(cls, response: Response) -> StaticResponse:
        """
        Create a StaticResponse from an asynchronous Playwright response.

        Parameters
        ----------
        response : Response
            The Playwright response to create a StaticResponse from.

        Returns
        -------
        StaticResponse
            The StaticResponse created from the Playwright response.
        """
        return cls(
            url=response.url,
            status=response.status,
            headers=await response.all_headers(),
            body=await response.body(),
        )

    async def text(self) -> str:
        """Get the response body as text."""
        return self.body.decode("utf-8")

    async def json(self) -> str:
        """Get the response body as JSON."""
        return json.loads(self.body)


class CaptchaSolverConfig(BaseModel):
    """A class for representing the configuration for a CAPTCHA solving service."""

    api_domain: str
    api_key: str

    def __bool__(self) -> bool:
        return bool(self.api_domain and self.api_key)
