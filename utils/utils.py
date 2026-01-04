"""Utility classes."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from pydantic import BaseModel, FilePath, HttpUrl, PositiveInt

from .passes.utils import ImageType, VideoType


class Args(BaseModel):
    """A class for representing the arguments passed to the program."""

    gallery: Union[bool, str]
    feed: Optional[str]
    messages: Optional[str]
    all: Optional[str]
    urls: List[HttpUrl]
    file: Optional[FilePath]
    output: Path
    from_timestamp: datetime
    to_timestamp: datetime
    limit: Optional[PositiveInt]
    image_type: ImageType
    video_type: VideoType
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
