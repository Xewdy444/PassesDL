"""A client for interacting with the www.passes.com API."""

from __future__ import annotations

import asyncio
import logging
import re
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import aiofiles
import aiohttp
import yt_dlp
from async_lru import alru_cache
from ffmpeg.asyncio import FFmpeg
from patchright.async_api import async_playwright
from pywidevine import Device
from tenacity import AsyncRetrying, retry_if_exception

from .constants import CAPTCHA_TASK_JSON, RECAPTCHA_SITEKEY
from .drm import PassesDRM
from .errors import (
    AuthorizationError,
    CaptchaError,
    ChannelNotFoundError,
    InvalidURLError,
    MediaDecryptionError,
    UserNotFoundError,
)
from .utils import (
    CaptchaSolverConfig,
    ImageType,
    Media,
    Post,
    PostFilter,
    StaticResponse,
    VideoType,
)

logger = logging.getLogger(__name__)


class PassesClient:
    """
    A class for interacting with the www.passes.com API.

    Parameters
    ----------
    widevine_device_path : Union[Path, str, None], optional
        The path to a Widevine device file (.wvd),
        by default None. If None, a default device will be used.
    """

    def __init__(self, widevine_device_path: Union[Path, str, None] = None) -> None:
        self._session = aiohttp.ClientSession(raise_for_status=True)
        self._username_mapping: Dict[str, str] = {}
        self._video_semaphore = asyncio.Semaphore()

        self._drm = PassesDRM(
            self._session,
            device=Device.load(widevine_device_path) if widevine_device_path else None,
        )

        self._retry = AsyncRetrying(
            retry=retry_if_exception(
                lambda err: isinstance(err, aiohttp.ClientResponseError)
                and 500 <= err.status <= 599
            )
        )

    async def __aenter__(self) -> PassesClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    @property
    def _user_id_mapping(self) -> Dict[str, str]:
        return {
            user_id: username for username, user_id in self._username_mapping.items()
        }

    @staticmethod
    def get_media(
        post: Post,
        *,
        images: bool = True,
        videos: bool = True,
        image_type: ImageType = ImageType.ORIGINAL,
        video_type: VideoType = VideoType.ORIGINAL,
    ) -> List[Media]:
        """
        Get the media from a post.

        Parameters
        ----------
        post : Post
            The post to get the media from.
        images : bool, optional
            Whether to get images, by default True.
        videos : bool, optional
            Whether to get videos, by default True.
        image_type : ImageType, optional
            The type of the images to get, by default ImageType.ORIGINAL.
        video_type : VideoType, optional
            The type of the videos to get, by default VideoType.ORIGINAL.

        Returns
        -------
        List[Media]
            The list of media from the post.
        """
        media: List[Media] = []
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

            if content["contentType"] == "video":
                signed_url = (
                    signed_content.get(video_type.value)
                    or signed_content.get(VideoType.LARGE.value)
                    or signed_content.get("signedUrl")
                )

                extension = content.get("extension") or "mp4"
            else:
                signed_url = signed_content.get(image_type.value)

                fallback_signed_url = signed_content.get(
                    ImageType.LARGE.value
                ) or signed_content.get("signedUrl")

                extension = content.get("extension") or re.search(
                    r"\.([a-z0-9]+)\?", fallback_signed_url
                ).group(1)

                signed_url = signed_url or fallback_signed_url

            media.append(
                Media(
                    user_id=content["userId"],
                    signed_url=signed_url,
                    content_id=content["contentId"],
                    content_type=content["contentType"],
                    extension=extension,
                )
            )

        return media

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

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{browser.version} Safari/537.36"
            )

            page = await context.new_page()

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
                    "websiteKey": RECAPTCHA_SITEKEY,
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

    async def _decrypt_and_merge_media(self, media: Media, media_path: Path) -> Path:
        """
        Decrypt and merge encrypted media files.

        Parameters
        ----------
        media : Media
            The media to decrypt and merge.
        media_path : Path
            The path to the media file.

        Returns
        -------
        Path
            The path to the decrypted and merged media file.

        Raises
        ------
        MediaDecryptionError
            If the media could not be decrypted.
        """
        ffmpeg_command = FFmpeg().option("y").output(media_path)

        for file in media_path.parent.glob(f"{media.content_id}.*.*"):
            pssh = await self._drm.get_widevine_pssh(media.signed_url)

            if pssh is None:
                raise MediaDecryptionError("Widevine PSSH not found in manifest.")

            decryption_key = await self._drm.get_decryption_key(pssh)

            if decryption_key is None:
                raise MediaDecryptionError("Decryption key could not be obtained.")

            await self._drm.decrypt_file(file, decryption_key)
            ffmpeg_command = ffmpeg_command.input(file)

        await ffmpeg_command.execute()

        for file in media_path.parent.glob(f"{media.content_id}.*.*"):
            file.unlink()

        if media.content_type == "video":
            return media_path

        output_path = media_path.with_suffix(f".{media.extension}")

        ffmpeg_image_command = (
            FFmpeg()
            .option("y")
            .input(media_path)
            .output(output_path, options={"vframes": "1"})
        )

        await ffmpeg_image_command.execute()
        media_path.unlink()
        return output_path

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
        media: Media,
        output_dir: Path,
        *,
        force_download: bool = False,
        creator_folder: bool = True,
        done_callback: Optional[Callable[[], Any]] = None,
    ) -> Path:
        """
        Download media content.

        Parameters
        ----------
        media : Media
            The media to download.
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
        """
        if creator_folder:
            username = await self.get_username(media.user_id)
            output_dir = output_dir / username

        output_dir.mkdir(parents=True, exist_ok=True)
        base_media_path = Path(output_dir / media.content_id)

        media_path = (
            base_media_path.with_suffix(".mp4")
            if media.content_type == "video"
            else base_media_path.with_suffix(f".{media.extension}")
        )

        if media_path.exists() and not force_download:
            if done_callback is not None:
                done_callback()

            return media_path

        if media.content_type != "video":
            if not media.is_encrypted:
                response: aiohttp.ClientResponse = await self._retry(
                    self._session.get, media.signed_url
                )

                async with aiofiles.open(media_path, "wb") as file:
                    async for data in response.content.iter_any():
                        await file.write(data)

                if done_callback is not None:
                    done_callback()

                return media_path

            media_path = media_path.with_suffix(".mp4")

        async with self._video_semaphore:
            options = {
                "outtmpl": str(media_path),
                "fixup": "never",
                "quiet": True,
                "no_warnings": True,
                "overwrites": True,
                "allow_unplayable_formats": True,
            }

            downloader = yt_dlp.YoutubeDL(options)
            await asyncio.to_thread(downloader.download, [media.signed_url])

            if media.is_encrypted:
                media_path = await self._decrypt_and_merge_media(media, media_path)

        if done_callback is not None:
            done_callback()

        return media_path
