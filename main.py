import argparse
import asyncio
import logging
from datetime import datetime

import asyncio_atexit
import toml
from rich import traceback
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.prompt import Prompt
from rich_argparse import RichHelpFormatter

from utils import (
    Args,
    AuthorizationError,
    Config,
    ImageType,
    MediaType,
    PassesClient,
    PostFilter,
    VideoType,
)

logger = logging.getLogger(__name__)
traceback.install(show_locals=True)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="A tool for downloading media from www.passes.com",
        formatter_class=RichHelpFormatter,
    )

    download_mode_group = parser.add_mutually_exclusive_group(required=True)

    download_mode_group.add_argument(
        "-a",
        "--all",
        default=None,
        type=str,
        help="Download media from posts in a user's feed and messages",
        metavar="USERNAME",
    )

    download_mode_group.add_argument(
        "--feed",
        default=None,
        type=str,
        help="Download media from posts in a user's feed",
        metavar="USERNAME",
    )

    download_mode_group.add_argument(
        "-m",
        "--messages",
        default=None,
        type=str,
        help="Download media from posts in a user's messages",
        metavar="USERNAME",
    )

    download_mode_group.add_argument(
        "-g",
        "--gallery",
        nargs="?",
        const=True,
        default=False,
        type=str,
        help="Download media from your gallery",
        metavar="USERNAME",
    )

    download_mode_group.add_argument(
        "--urls",
        "--links",
        nargs="+",
        default=[],
        type=str,
        help="A list of post URLs to download media from",
    )

    download_mode_group.add_argument(
        "--file",
        default=None,
        type=str,
        help="A file containing a list of post URLs to download media from",
    )

    parser.add_argument(
        "-o",
        "--output",
        default="media",
        type=str,
        help="The output directory to save media to",
    )

    parser.add_argument(
        "--from",
        default=datetime.min,
        type=str,
        help="The creation timestamp of posts to start downloading media from",
        dest="from_timestamp",
    )

    parser.add_argument(
        "-t",
        "--to",
        default=datetime.max,
        type=str,
        help="The creation timestamp of posts to stop downloading media",
        dest="to_timestamp",
    )

    parser.add_argument(
        "--limit",
        default=None,
        type=int,
        help=(
            "The maximum number of posts in the user's feed or messages "
            "to download media from"
        ),
    )

    parser.add_argument(
        "-mt",
        "--media-types",
        nargs="+",
        default=list(MediaType),
        type=lambda media_type: MediaType[media_type.upper()],
        help="The types of media to download, by default all types",
        choices=list(MediaType),
    )

    parser.add_argument(
        "-it",
        "--image-type",
        default=ImageType.ORIGINAL,
        type=lambda size: ImageType[size.upper()],
        help="The type of the images to download, by default original",
        choices=list(ImageType),
    )

    parser.add_argument(
        "-vt",
        "--video-type",
        default=VideoType.LARGE,
        type=lambda size: VideoType[size.upper()],
        help="The type of the videos to download, by default large",
        choices=list(VideoType),
    )

    parser.add_argument(
        "-fd",
        "--force-download",
        action="store_true",
        help=(
            "Force downloading the media even if it already exists in the "
            "output directory"
        ),
    )

    parser.add_argument(
        "-ncf",
        "--no-creator-folders",
        action="store_true",
        help="Don't create subfolders for each creator",
    )

    args = Args.from_namespace(parser.parse_args())
    config = Config()

    logging.basicConfig(
        format="%(message)s",
        datefmt="%H:%M:%S",
        level=logging.INFO,
        handlers=[RichHandler(show_path=False)],
    )

    passes = PassesClient(
        widevine_device_path=config.widevine.device_path if config.widevine else None
    )

    asyncio_atexit.register(passes.close)

    if not config.authorization.refresh_token and config.authorization.credentials:
        logger.info("Obtaining refresh token...")

        config.authorization.refresh_token, mfa_required = await passes.login(
            config.authorization.credentials.email,
            config.authorization.credentials.password,
            captcha_solver_config=config.captcha_solver,
        )

        if mfa_required:
            logger.info("Multi-factor authentication is required")

            mfa_token = Prompt.ask(
                "[blue]>>>[/blue] Enter the multi-factor authentication code"
            )

            config.authorization.refresh_token = await passes.submit_mfa_token(
                config.authorization.refresh_token, mfa_token
            )

        with open("config.toml", "w", encoding="utf-8") as file:
            toml.dump(config.model_dump(), file)

        logger.info("Refresh token saved to config.toml")

    if not config.authorization.refresh_token:
        logger.error("A refresh token or login credentials are required")
        return

    logger.info("Obtaining access token with refresh token...")

    try:
        access_token = await passes.get_access_token(config.authorization.refresh_token)
    except AuthorizationError:
        logger.warning("Refresh token is invalid or expired")

        if not config.authorization.credentials:
            logger.error(
                "Please provide login credentials or manually update the refresh token"
            )

            return

        logger.info("Obtaining a new refresh token with provided credentials...")

        config.authorization.refresh_token, mfa_required = await passes.login(
            config.authorization.credentials.email,
            config.authorization.credentials.password,
            captcha_solver_config=config.captcha_solver,
        )

        if mfa_required:
            logger.info("Multi-factor authentication is required")

            mfa_token = Prompt.ask(
                "[blue]>>>[/blue] Enter the multi-factor authentication code"
            )

            config.authorization.refresh_token = await passes.submit_mfa_token(
                config.authorization.refresh_token, mfa_token
            )

        with open("config.toml", "w", encoding="utf-8") as file:
            toml.dump(config.model_dump(), file)

        logger.info("Refresh token saved to config.toml")
        logger.info("Obtaining access token with new refresh token...")
        access_token = await passes.get_access_token(config.authorization.refresh_token)

    passes.set_access_token(access_token)
    logger.info("Set access token")

    post_filter = PostFilter(
        media_types=args.media_types,
        accessible_only=True,
        from_timestamp=args.from_timestamp,
        to_timestamp=args.to_timestamp,
    )

    if args.all is not None:
        logger.info("Fetching posts from user's feed and messages...")

        feed_task = asyncio.create_task(
            passes.get_feed(args.all, limit=args.limit, post_filter=post_filter)
        )

        messages_task = asyncio.create_task(
            passes.get_messages(args.all, limit=args.limit, post_filter=post_filter)
        )

        results = await asyncio.gather(feed_task, messages_task, return_exceptions=True)

        posts = [
            item for result in results if isinstance(result, list) for item in result
        ]
    elif args.feed is not None:
        logger.info("Fetching posts from user's feed...")

        posts = await passes.get_feed(
            args.feed, limit=args.limit, post_filter=post_filter
        )
    elif args.messages is not None:
        logger.info("Fetching posts from user's messages...")

        posts = await passes.get_messages(
            args.messages, limit=args.limit, post_filter=post_filter
        )
    elif args.gallery is True:
        logger.info("Fetching posts from your gallery...")
        posts = await passes.get_gallery(limit=args.limit, post_filter=post_filter)
    elif isinstance(args.gallery, str):
        logger.info("Fetching posts from user in your gallery...")

        posts = await passes.get_gallery(
            username=args.gallery, limit=args.limit, post_filter=post_filter
        )
    elif args.file is not None:
        logger.info("Fetching posts from URLs in file...")

        tasks = [
            asyncio.create_task(passes.get_post_from_url(str(url)))
            for url in args.file.read_text().splitlines()
        ]

        posts = await asyncio.gather(*tasks)
    elif args.urls:
        logger.info("Fetching posts from URLs...")

        tasks = [
            asyncio.create_task(passes.get_post_from_url(str(url))) for url in args.urls
        ]

        posts = await asyncio.gather(*tasks)

    post_media = [
        media
        for post in posts
        for media in passes.get_media(
            post,
            media_types=args.media_types,
            image_type=args.image_type,
            video_type=args.video_type,
        )
    ]

    if not post_media:
        logger.warning("No downloadable media found")
        return

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(compact=True, elapsed_when_finished=True),
    )

    with progress:
        progress_task = progress.add_task(
            "Downloading media[logging.keyword]...", total=len(post_media)
        )

        tasks = [
            asyncio.create_task(
                passes.download_media(
                    media,
                    args.output,
                    force_download=args.force_download,
                    creator_folder=not args.no_creator_folders,
                    done_callback=lambda: progress.update(progress_task, advance=1),
                )
            )
            for media in post_media
        ]

        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
