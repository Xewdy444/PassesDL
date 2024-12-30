"""Utility classes."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from enum import Enum
from http.client import responses
from pathlib import Path
from typing import Annotated, Dict, List, Optional

import annotated_types
from patchright.async_api import Response
from pydantic import BaseModel, FilePath, HttpUrl, PositiveInt

from .errors import PlaywrightResponseError


class ImageSize(Enum):
    """Image sizes available for download."""

    SMALL = "signedUrlSm"
    MEDIUM = "signedUrlMd"
    LARGE = "signedUrlLg"

    def __str__(self) -> str:
        return self.name.lower()


class Args(BaseModel):
    """A class for representing the arguments passed to the program."""

    feed: Optional[str]
    messages: Optional[str]
    all: Optional[str]
    urls: List[HttpUrl]
    file: Optional[FilePath]
    output: Path
    from_timestamp: datetime
    to_timestamp: datetime
    limit: Optional[PositiveInt]
    size: ImageSize
    force_download: bool
    no_creator_folders: bool
    only_images: bool
    only_videos: bool

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> Args:
        """
        Create an instance of Args from an argparse namespace.

        Parameters
        ----------
        namespace : argparse.Namespace
            The namespace to create the Args instance from.

        Returns
        -------
        Args
            An instance of Args created from the namespace.
        """
        return cls(**namespace.__dict__)


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

    domain: str
    api_key: str

    def __bool__(self) -> bool:
        return bool(self.domain and self.api_key)
