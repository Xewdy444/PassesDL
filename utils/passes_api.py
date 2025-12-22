"""A module for interacting with the www.passes.com API."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Final, List, Optional, Tuple

import aiofiles
import aiohttp
from async_lru import alru_cache
from ffmpeg.asyncio import FFmpeg
from patchright.async_api import async_playwright
from tenacity import AsyncRetrying, retry_if_exception

from .errors import (
    AuthorizationError,
    CaptchaError,
    ChannelNotFoundError,
    InvalidURLError,
    UserNotFoundError,
)
from .utils import CaptchaSolverConfig, ImageSize, StaticResponse

CAPTCHA_TASK_JSON: Final[Dict[str, Dict[str, Any]]] = {
    "api.capsolver.com": {"type": "ReCaptchaV3EnterpriseTaskProxyLess"},
    "api.anti-captcha.com": {
        "type": "RecaptchaV3TaskProxyless",
        "minScore": 0.9,
        "isEnterprise": True,
    },
}

Post = Dict[str, Any]

logger = logging.getLogger(__name__)


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


class PassesAPI:
    """A class for interacting with the www.passes.com API."""

    RECAPTCHA_SITEKEY: ClassVar[str] = "6LdZUY4qAAAAAEX-6hC26gsQoQK3VgmCOVLxR7Cz"

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
        contents = post.get("contents", [post])

        for content in contents:
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

    @staticmethod
    async def _login_with_browser(email: str, password: str) -> StaticResponse:
        """
        Log in with an email address and password using a browser.

        Parameters
        ----------
        email : str
            The email address to log in with.
        password : str
            The password to log in with.

        Returns
        -------
        StaticResponse
            The response from the login request.
        """
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()

            async with page.expect_response(
                re.compile(r"https://www\.google\.com/recaptcha/enterprise/anchor")
            ) as response_info:
                await page.goto("https://www.passes.com/login")
                enter_email = page.get_by_role("textbox", name="Enter your email")
                enter_password = page.get_by_role("textbox", name="Enter your password")

                await enter_email.fill(email)
                await enter_password.fill(password)

            await response_info.value

            async with page.expect_response(
                "https://www.passes.com/api/auth/password/login"
            ) as response_info:
                await page.get_by_role("button", name="Sign in").click()

            return await StaticResponse.from_response(await response_info.value)

    async def _get_recaptcha_token(
        self, captcha_solver_config: CaptchaSolverConfig
    ) -> str:
        """
        Get a reCAPTCHA token using a CAPTCHA solving service.

        Parameters
        ----------
        captcha_solver_config : CaptchaSolverConfig
            The configuration for the CAPTCHA solving service.

        Returns
        -------
        str
            The reCAPTCHA token.

        Raises
        ------
        CaptchaError
            If the CAPTCHA solving service returns an error or is unsupported.
        """
        if captcha_solver_config.api_domain not in CAPTCHA_TASK_JSON:
            raise CaptchaError("Unsupported CAPTCHA solving service.")

        create_task_response = await self._session.post(
            f"https://{captcha_solver_config.api_domain}/createTask",
            json={
                "clientKey": captcha_solver_config.api_key,
                "task": {
                    **CAPTCHA_TASK_JSON[captcha_solver_config.api_domain],
                    "websiteURL": "https://www.passes.com/login",
                    "websiteKey": self.RECAPTCHA_SITEKEY,
                    "pageAction": "login",
                },
            },
        )

        try:
            task_json = await create_task_response.json()
        except JSONDecodeError as err:
            raise CaptchaError from err

        if task_json["errorId"] != 0:
            raise CaptchaError(task_json["errorDescription"])

        while True:
            task_result = await self._session.post(
                f"https://{captcha_solver_config.api_domain}/getTaskResult",
                json={
                    "clientKey": captcha_solver_config.api_key,
                    "taskId": task_json["taskId"],
                },
            )

            task_result_json = await task_result.json()

            if task_result_json["errorId"] != 0:
                raise CaptchaError(task_result_json["errorDescription"])

            if task_result_json["status"] == "ready":
                break

            await asyncio.sleep(1)

        return task_result_json["solution"]["gRecaptchaResponse"]

    async def close(self) -> None:
        """Close the aiohttp session."""
        await self._session.close()

    async def login(
        self,
        email: str,
        password: str,
        *,
        captcha_solver_config: Optional[CaptchaSolverConfig] = None,
        attempts: int = 3,
    ) -> Tuple[str, bool]:
        """
        Log in with an email address and password.

        Parameters
        ----------
        email : str
            The email address to log in with.
        password : str
            The password to log in with.
        captcha_solver_config : Optional[CaptchaSolverConfig], optional
            The configuration for a CAPTCHA solving service, by default None.
            If provided, the CAPTCHA will be solved using the service,
            otherwise it will be solved using a browser.
        attempts : int, optional
            The number of attempts for logging in, by default 3.

        Returns
        -------
        Tuple[str, bool]
            A tuple containing the temporary access token and True if multi-factor
            authentication is required, or the refresh token and False if not.

        Raises
        ------
        AuthorizationError
            If the login credentials are invalid or the reCAPTCHA score is too low.
        """
        for _ in range(attempts):
            if captcha_solver_config:
                recaptcha_token = await self._get_recaptcha_token(captcha_solver_config)

                response = await self._session.post(
                    "https://www.passes.com/api/auth/password/login",
                    json={
                        "email": email,
                        "password": password,
                        "recaptchaToken": recaptcha_token,
                    },
                    raise_for_status=False,
                )
            else:
                response = await self._login_with_browser(email, password)

            if response.status not in (400, 401):
                break

            logger.warning("Login attempt failed, retrying...")

        if response.status in (400, 401):
            raise AuthorizationError(
                "Invalid login credentials or low reCAPTCHA score."
            )

        response.raise_for_status()
        response_json = await response.json()

        if "refreshToken" in response_json["tokens"]:
            return response_json["tokens"]["refreshToken"], False

        return response_json["tokens"]["accessToken"], True

    async def submit_mfa_token(self, access_token: str, mfa_token: str) -> str:
        """
        Submit a multi-factor authentication token.

        Parameters
        ----------
        access_token : str
            The temporary access token to use for authentication.
        mfa_token : str
            The multi-factor authentication token.

        Returns
        -------
        str
            The refresh token.

        Raises
        ------
        AuthorizationError
            If the multi-factor authentication token is invalid.
        """
        response = await self._session.post(
            "https://www.passes.com/api/auth/check-mfa-token",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"token": mfa_token},
            raise_for_status=False,
        )

        if response.status in (400, 401):
            raise AuthorizationError("Invalid multi-factor authentication token.")

        response.raise_for_status()
        response_json = await response.json()
        return response_json["tokens"]["refreshToken"]

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
    async def get_user_id(self, username: str) -> Optional[str]:
        """
        Get the user ID associated with a username.

        Parameters
        ----------
        username : str
            The username to get the ID for.

        Returns
        -------
        Optional[str]
            The user ID associated with the username.
            Returns None if the user ID could not be found.
        """
        if username in self._username_mapping:
            return self._username_mapping[username]

        response = await self._session.post(
            "https://www.passes.com/api/profile/get",
            json={"username": username},
            raise_for_status=False,
        )

        if response.status == 404:
            return None

        response.raise_for_status()
        response_json = await response.json()

        user_id = response_json["user"]["userId"]
        self._username_mapping[username] = user_id

        logger.info("User ID for %s: %s", username, user_id)
        return user_id

    @alru_cache
    async def get_username(self, user_id: str) -> Optional[str]:
        """
        Get the username associated with a user ID.

        Parameters
        ----------
        user_id : str
            The user ID to get the username for.

        Returns
        -------
        Optional[str]
            The username associated with the user ID.
            Returns None if the username could not be found.
        """
        if user_id in self._user_id_mapping:
            return self._user_id_mapping[user_id]

        response = await self._session.post(
            "https://www.passes.com/api/profile/get",
            json={"creatorId": user_id},
            raise_for_status=False,
        )

        if response.status == 404:
            return None

        response.raise_for_status()
        response_json = await response.json()

        username = response_json["user"]["username"]
        self._username_mapping[username] = user_id

        logger.info("Username for %s: %s", user_id, username)
        return username

    async def get_channel_id(self, username: str) -> Optional[str]:
        """
        Get the message channel ID associated with a username.

        Parameters
        ----------
        username : str
            The username to get the channel ID for.

        Returns
        -------
        Optional[str]
            The message channel ID.
            Returns None if the channel ID could not be found.
        """
        json_data = {"orderType": "recent", "order": "desc"}

        while True:
            response = await self._session.post(
                "https://www.passes.com/api/channel/channels", json=json_data
            )

            response_json = await response.json()

            for channel in response_json["data"]:
                if channel["otherUser"]["username"] != username:
                    continue

                channel_id = channel["channelId"]
                logger.info("Message channel ID for %s: %s", username, channel_id)
                return channel_id

            if not response_json["hasMore"]:
                return None

            json_data.update(
                {
                    "recentAt": response_json["recentAt"],
                    "lastId": response_json["lastId"],
                }
            )

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
            raise InvalidURLError(post_url)

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

    async def get_gallery(
        self,
        *,
        username: Optional[str] = None,
        limit: Optional[int] = None,
        post_filter: Callable[[Post], bool] = PostFilter(),
    ) -> List[Post]:
        """
        Get the gallery of purchased posts.

        Parameters
        ----------
        username : Optional[str], optional
            The username of the user to get posts from in your gallery,
            by default None. If None, posts from all users will be retrieved.
        limit : int, optional
            The maximum number of posts to get, by default None.
        post_filter : Callable[[Post], bool], optional
            A function to filter posts, by default PostFilter().

        Returns
        -------
        List[Post]
            The list of posts in the gallery.

        Raises
        ------
        UserNotFoundError
            If the user is not found.
        """
        user_id = await self.get_user_id(username) if username is not None else None

        if username is not None and user_id is None:
            raise UserNotFoundError(username)

        json_data = {"search": "", "order": "desc"}
        posts: List[Post] = []

        while True:
            response = await self._session.post(
                "https://www.passes.com/api/content/purchased/content", json=json_data
            )

            response_json = await response.json()

            for post in response_json["data"]:
                if (
                    not post_filter(post)
                    or user_id is not None
                    and post["userId"] != user_id
                ):
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

        Raises
        ------
        UserNotFoundError
            If the user is not found.
        """
        user_id = await self.get_user_id(username)

        if user_id is None:
            raise UserNotFoundError(username)

        json_data = {"creatorId": user_id}
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

    async def get_messages(
        self,
        username: str,
        *,
        limit: Optional[int] = None,
        post_filter: Callable[[Post], bool] = PostFilter(),
    ) -> List[Post]:
        """
        Get the messages for a user.

        Parameters
        ----------
        username : str
            The username of the user to get the messages for.
        limit : int, optional
            The maximum number of messages to get, by default None.
        post_filter : Callable[[Post], bool], optional
            A function to filter messages, by default PostFilter().

        Returns
        -------
        List[Post]
            The list of messages in the user's messages.

        Raises
        ------
        ChannelNotFoundError
            If the message channel is not found.
        """
        channel_id = await self.get_channel_id(username)

        if channel_id is None:
            raise ChannelNotFoundError(username)

        json_data = {"channelId": channel_id, "contentOnly": False, "pending": False}
        posts: List[Post] = []

        while True:
            response = await self._session.post(
                "https://www.passes.com/api/messages/messages", json=json_data
            )

            response_json = await response.json()

            for post in response_json["data"]:
                if not post_filter(post):
                    continue

                posts.append(post)

                if limit is not None and limit == len(posts):
                    return posts

            if not response_json["hasNextPage"]:
                break

            json_data.update(
                {
                    "lastSentAt": response_json["lastSentAt"],
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
            r"https://cdnpasses\.com/(fan-)?media/"
            r"(([a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12})/){1,2}"
            r"([a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12})"
            r"(-[a-z]{2})?(\.[a-z0-9]+)",
            media_url,
        )

        if url_match is None:
            raise InvalidURLError(media_url)

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

            ffmpeg = (
                FFmpeg()
                .option("y")
                .option("probesize", "10M")
                .option("analyzeduration", "10M")
                .input(media_url)
                .output(media_path)
            )

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
