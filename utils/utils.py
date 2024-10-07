"""Utility classes."""

from __future__ import annotations

import argparse
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, FilePath, HttpUrl, PositiveInt


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
