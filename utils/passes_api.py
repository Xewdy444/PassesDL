"""A module for interacting with the www.passes.com API."""

from __future__ import annotations

import asyncio
import functools
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import aiofiles
import aiohttp
from async_lru import alru_cache
from ffmpeg.asyncio import FFmpeg
from tenacity import AsyncRetrying, retry_if_exception

from .errors import AuthorizationError, InvalidURLError
from .utils import ImageSize

Post = Dict[str, Any]

logger = logging.getLogger(__name__)


class PostFilter:
    """A class for filtering Passes posts."""

    @classmethod
    def from_arguments(cls, **kwargs: Any) -> Callable[[Post], bool]:
        """
        Create a post filter callable with predefined arguments.

        Parameters
        ----------
        kwargs : Any
            The arguments to create the post filter with.

        Returns
        -------
        Callable[[Post], bool]
            The post filter function.
        """
        return functools.partial(cls.__call__, **kwargs)

    @staticmethod
    def __call__(
        post: Post,
        *,
        images: bool = False,
        videos: bool = False,
        accessible_only: bool = False,
        from_timestamp: datetime = datetime.min,
        to_timestamp: datetime = datetime.max,
    ) -> bool:
        """
        Filter a post based on criteria.

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

        Returns
        -------
        bool
            Whether the post meets the filter criteria.
        """
        if any((images, videos)) and not (
            images
            and any(content["contentType"] == "image" for content in post["contents"])
            or videos
            and any(content["contentType"] == "video" for content in post["contents"])
        ):
            return False

        if accessible_only and not any(
            "signedContent" in content for content in post["contents"]
        ):
            return False

        post_timestamp = datetime.fromisoformat(post["createdAt"].rstrip("Z"))

        if not from_timestamp <= post_timestamp <= to_timestamp:
            return False

        return True


class PassesAPI:
    """A class for interacting with the www.passes.com API."""

    def __init__(self) -> None:
        self._session = aiohttp.ClientSession(raise_for_status=True)
        self._username_mapping: Dict[str, str] = {}
        self._ffmpeg_semaphore = asyncio.Semaphore()

        self._retry = AsyncRetrying(
            retry=retry_if_exception(
                lambda err: isinstance(err, aiohttp.ClientResponseError)
                and 500 <= err.status <= 599
            )
        )

    async def __aenter__(self) -> PassesAPI:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    @property
    def _user_id_mapping(self) -> Dict[str, str]:
        return {
            user_id: username for username, user_id in self._username_mapping.items()
        }

    @staticmethod
    def get_media_urls(
        post: Post,
        *,
        images: bool = True,
        videos: bool = True,
        image_size: ImageSize = ImageSize.LARGE,
    ) -> List[str]:
        """
        Get the media URLs from a post.

        Parameters
        ----------
        post : Post
            The post to get the media URLs from.
        images : bool, optional
            Whether to get image URLs, by default True.
        videos : bool, optional
            Whether to get video URLs, by default True.
        image_size : ImageSize, optional
            The image size to get URLs for, by default ImageSize.LARGE.

        Returns
        -------
        List[str]
            The list of media URLs from the post.
        """
        media_urls: List[str] = []

        for content in post["contents"]:
            if (
                not images
                and content["contentType"] == "image"
                or not videos
                and content["contentType"] == "video"
            ):
                continue

            signed_content = content.get("signedContent")

            if signed_content is None:
                continue

            url = (
                signed_content[image_size.value]
                if image_size.value in signed_content
                else signed_content["signedUrl"]
            )

            media_urls.append(url)

        return media_urls

    def set_access_token(self, access_token: str) -> None:
        """
        Set the access token to use for authentication.

        Parameters
        ----------
        access_token : str
            The access token.
        """
        self._session.headers["Authorization"] = f"Bearer {access_token}"

    async def close(self) -> None:
        """Close the aiohttp session."""
        await self._session.close()

    async def get_refresh_token(self, email: str, password: str) -> str:
        """
        Get a refresh token for obtaining an access token.

        Parameters
        ----------
        email : str
            The email address to log in with.
        password : str
            The password to log in with.

        Returns
        -------
        str
            The refresh token.

        Raises
        ------
        AuthorizationError
            If the login credentials are invalid.
        """
        response = await self._session.post(
            "https://www.passes.com/api/auth/password/login",
            json={"email": email, "password": password},
            raise_for_status=False,
        )

        if response.status in (400, 401):
            raise AuthorizationError("Invalid login credentials.")

        response.raise_for_status()
        response_json = await response.json()
        return response_json["refreshToken"]

    async def get_access_token(self, refresh_token: str) -> str:
        """
        Get an access token for authentication.

        Parameters
        ----------
        refresh_token : str
            The refresh token to use for obtaining an access token.

        Returns
        -------
        str
            The access token.

        Raises
        ------
        AuthorizationError
            If the refresh token is invalid or expired.
        """
        response = await self._session.post(
            "https://www.passes.com/api/auth/refresh",
            headers={"Authorization": f"Bearer {refresh_token}"},
            raise_for_status=False,
        )

        if response.status == 401:
            raise AuthorizationError("Invalid or expired refresh token.")

        response.raise_for_status()
        response_json = await response.json()
        return response_json["accessToken"]

    @alru_cache
    async def get_user_id(self, username: str) -> str:
        """
        Get the user ID associated with a username.

        Parameters
        ----------
        username : str
            The username to get the ID for.

        Returns
        -------
        str
            The user ID associated with the username.
        """
        if username in self._username_mapping:
            return self._username_mapping[username]

        response = await self._session.post(
            "https://www.passes.com/api/profile/get", json={"username": username}
        )

        response_json = await response.json()
        user_id = response_json["user"]["userId"]
        self._username_mapping[username] = user_id

        logger.info("User ID for %s: %s", username, user_id)
        return user_id

    @alru_cache
    async def get_username(self, user_id: str) -> str:
        """
        Get the username associated with a user ID.

        Parameters
        ----------
        user_id : str
            The user ID to get the username for.

        Returns
        -------
        str
            The username associated with the user ID.
        """
        if user_id in self._user_id_mapping:
            return self._user_id_mapping[user_id]

        response = await self._session.post(
            "https://www.passes.com/api/profile/get", json={"creatorId": user_id}
        )

        response_json = await response.json()
        username = response_json["user"]["username"]
        self._username_mapping[username] = user_id

        logger.info("Username for %s: %s", user_id, username)
        return username

    async def get_post_from_url(self, post_url: str) -> Post:
        """
        Get the information for a post from its URL.

        Parameters
        ----------
        post_url : str
            The URL of the post.

        Returns
        -------
        Post
            The post information.

        Raises
        ------
        InvalidURLError
            If the post URL is invalid.
        """
        url_match = re.match(
            r"https://www\.passes\.com/([a-zA-Z0-9_.]+)/"
            r"([a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12})$",
            post_url,
        )

        if url_match is None:
            raise InvalidURLError("Invalid post URL.")

        return await self.get_post(url_match.group(1), url_match.group(2))

    async def get_post(self, username: str, post_id: str) -> Post:
        """
        Get the information for a post.

        Parameters
        ----------
        username : str
            The username of the post creator.
        post_id : str
            The ID of the post.

        Returns
        -------
        Post
            The post information.
        """
        response = await self._session.post(
            "https://www.passes.com/api/post/get",
            json={"username": username, "postId": post_id},
        )

        return await response.json()

    async def get_feed(
        self,
        username: str,
        *,
        limit: Optional[int] = None,
        post_filter: Callable[[Post], bool] = PostFilter(),
    ) -> List[Post]:
        """
        Get the feed for a user.

        Parameters
        ----------
        username : str
            The username of the user to get the feed for.
        limit : int, optional
            The maximum number of posts to get, by default None.
        post_filter : Callable[[Post], bool], optional
            A function to filter posts, by default PostFilter().

        Returns
        -------
        List[Post]
            The list of posts in the user's feed.
        """
        json_data = {"creatorId": await self.get_user_id(username)}
        posts: List[Post] = []

        while True:
            response = await self._session.post(
                "https://www.passes.com/api/feed/profile", json=json_data
            )

            response_json = await response.json()

            for post in response_json["data"]:
                if not post_filter(post):
                    continue

                posts.append(post)

                if limit is not None and limit == len(posts):
                    return posts

            if not response_json["hasMore"]:
                break

            json_data.update(
                {
                    "createdAt": response_json["createdAt"],
                    "lastId": response_json["lastId"],
                }
            )

        return posts

    async def download_media(
        self,
        media_url: str,
        output_dir: Path,
        *,
        force_download: bool = False,
        creator_folder: bool = True,
        done_callback: Optional[Callable[[], Any]] = None,
    ) -> Path:
        """
        Download media from a URL.

        Parameters
        ----------
        media_url : str
            The URL of the media to download.
        output_dir : Path
            The directory to save the downloaded media to.
        force_download : bool, optional
            Whether to force downloading the media even if it already exists,
            by default False.
        creator_folder : bool, optional
            Whether to save the media in a subfolder named after the creator,
            by default True.
        done_callback : Optional[Callable[[], Any]], optional
            A callback to run when the download is complete, by default None.

        Returns
        -------
        Path
            The path to the downloaded media.

        Raises
        ------
        InvalidURLError
            If the media URL is invalid.
        """
        url_match = re.match(
            r"https://cdn\.passes\.com/(fan-)?media/"
            r"(([a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12})/){1,2}"
            r"([a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12})"
            r"(-[a-z]{2})?(\.[a-z0-9]+)",
            media_url,
        )

        if url_match is None:
            raise InvalidURLError("Invalid media URL.")

        if creator_folder:
            username = await self.get_username(url_match.group(3))
            output_dir = output_dir / username

        output_dir.mkdir(parents=True, exist_ok=True)
        incomplete_media_path = output_dir / url_match.group(5)

        if url_match.group(8) == ".m3u8":
            media_path = incomplete_media_path.with_suffix(".mp4")

            if media_path.exists() and not force_download:
                if done_callback is not None:
                    done_callback()

                return media_path

            ffmpeg = FFmpeg().option("y").input(media_url).output(media_path)

            async with self._ffmpeg_semaphore:
                await ffmpeg.execute()

            if done_callback is not None:
                done_callback()

            return media_path

        media_path = incomplete_media_path.with_suffix(url_match.group(8))

        if media_path.exists() and not force_download:
            if done_callback is not None:
                done_callback()

            return media_path

        response: aiohttp.ClientResponse = await self._retry(
            self._session.get, media_url
        )

        async with aiofiles.open(media_path, "wb") as file:
            async for data in response.content.iter_any():
                await file.write(data)

        if done_callback is not None:
            done_callback()

        return media_path
