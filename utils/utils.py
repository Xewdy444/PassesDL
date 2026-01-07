"""Utility classes."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple, Type, Union

from pydantic import BaseModel, FilePath, HttpUrl, PositiveInt
from pydantic_settings import BaseSettings, TomlConfigSettingsSource

from .passes.utils import BoolMixin, CaptchaSolverConfig, ImageType, VideoType


class Args(BaseModel):
    """A class for representing the arguments passed to the program."""

    all: Optional[str]
    feed: Optional[str]
    messages: Optional[str]
    gallery: Union[bool, str]
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


class CredentialsConfig(BaseModel, BoolMixin):
    """Credentials configuration settings."""

    email: str
    password: str


class AuthorizationConfig(BaseModel, BoolMixin):
    """Authorization configuration settings."""

    refresh_token: Optional[str] = None
    credentials: Optional[CredentialsConfig] = None


class WidevineConfig(BaseModel, BoolMixin):
    """Widevine configuration settings."""

    device_path: str


class Config(BaseSettings):
    """Configuration settings for the application."""

    authorization: AuthorizationConfig = AuthorizationConfig()
    captcha_solver: Optional[CaptchaSolverConfig] = None
    widevine: Optional[WidevineConfig] = None

    @classmethod
    def settings_customise_sources(
        cls, settings_cls: Type[BaseSettings], **_: Any
    ) -> Tuple[TomlConfigSettingsSource]:
        """
        Customize the settings sources to load from a TOML file.

        Parameters
        ----------
        settings_cls : Type[BaseSettings]
            The settings class.

        Returns
        -------
        Tuple[TomlConfigSettingsSource]
            A tuple containing the TOML configuration source.
        """
        return (TomlConfigSettingsSource(settings_cls, toml_file="config.toml"),)
