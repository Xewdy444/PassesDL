import argparse
import asyncio
import logging
from datetime import datetime

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
    ImageSize,
    MFARequiredError,
    PassesAPI,
    PostFilter,
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
        "--feed",
        default=None,
        type=str,
        help="The username of the user to download media from posts in their feed",
        metavar="USERNAME",
    )

    download_mode_group.add_argument(
        "-m",
        "--messages",
        default=None,
        type=str,
        help="The username of the user to download media from posts in their messages",
        metavar="USERNAME",
    )

    download_mode_group.add_argument(
        "--urls",
        "--links",
        nargs="+",
        default=[],
        type=str,
        help="A list of post urls to download media from",
    )

    download_mode_group.add_argument(
        "--file",
        default=None,
        type=str,
        help="A file containing a list of post urls to download media from",
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
        help="The maximum number of posts in the user's feed to download media from",
    )

    parser.add_argument(
        "-s",
        "--size",
        default=ImageSize.LARGE,
        type=lambda size: ImageSize[size.upper()],
        help="The size of the images to download",
        choices=list(ImageSize),
    )

    parser.add_argument(
        "-fd",
        "--force-download",
        action="store_true",
        help="Force downloading the media even if it already exists in the output directory",
    )

    parser.add_argument(
        "-ncf",
        "--no-creator-folders",
        action="store_true",
        help="Don't create subfolders for each creator",
    )

    media_type_group = parser.add_mutually_exclusive_group()

    media_type_group.add_argument(
        "-i",
        "--images",
        action="store_true",
        help="Only download images",
        dest="only_images",
    )

    media_type_group.add_argument(
        "-v",
        "--videos",
        action="store_true",
        help="Only download videos",
        dest="only_videos",
    )

    args = Args.from_namespace(parser.parse_args())
    config = toml.load("config.toml")

    refresh_token, email, password = (
        config["authorization"]["refresh_token"],
        config["authorization"]["credentials"]["email"],
        config["authorization"]["credentials"]["password"],
    )

    logging.basicConfig(
        format="%(message)s",
        datefmt="%H:%M:%S",
        level=logging.INFO,
        handlers=[RichHandler(show_path=False)],
    )

    passes = PassesAPI()

    if not refresh_token and all((email, password)):
        logger.info("Obtaining refresh token...")
        refresh_token, mfa_required = await passes.login(email, password)

        if mfa_required:
            logger.info("Multi-factor authentication is required")

            mfa_token = Prompt.ask(
                "[blue]>>>[/blue] Enter the multi-factor authentication code"
            )

            refresh_token = await passes.submit_mfa_token(refresh_token, mfa_token)

        config["authorization"]["refresh_token"] = refresh_token

        with open("config.toml", "w", encoding="utf-8") as file:
            toml.dump(config, file)

        logger.info("Refresh token saved to config.toml")

    if not refresh_token:
        logger.error("A refresh token or login credentials are required")
        await passes.close()
        return

    logger.info("Obtaining access token with refresh token...")

    try:
        access_token = await passes.get_access_token(refresh_token)
    except AuthorizationError:
        logger.warning("Refresh token is invalid or expired")

        if not all((email, password)):
            logger.error(
                "Please rovide login credentials or manually update the refresh token"
            )

            await passes.close()
            return

        logger.info("Obtaining a new refresh token with provided credentials...")
        refresh_token, mfa_required = await passes.login(email, password)

        if mfa_required:
            logger.info("Multi-factor authentication is required")

            mfa_token = Prompt.ask(
                "[blue]>>>[/blue] Enter the multi-factor authentication code"
            )

            refresh_token = await passes.submit_mfa_token(refresh_token, mfa_token)

        config["authorization"]["refresh_token"] = refresh_token

        with open("config.toml", "w", encoding="utf-8") as file:
            toml.dump(config, file)

        logger.info("Refresh token saved to config.toml")
        logger.info("Obtaining access token with new refresh token...")
        access_token = await passes.get_access_token(refresh_token)

    passes.set_access_token(access_token)
    logger.info("Set access token")

    if args.feed is not None:
        logger.info("Fetching posts from user's feed...")

        posts = await passes.get_feed(
            args.feed,
            limit=args.limit,
            post_filter=PostFilter(
                images=not args.only_videos,
                videos=not args.only_images,
                accessible_only=True,
                from_timestamp=args.from_timestamp,
                to_timestamp=args.to_timestamp,
            ),
        )
    elif args.messages is not None:
        logger.info("Fetching posts from user's messages...")

        posts = await passes.get_messages(
            args.messages,
            limit=args.limit,
            post_filter=PostFilter(
                images=not args.only_videos,
                videos=not args.only_images,
                accessible_only=True,
                from_timestamp=args.from_timestamp,
                to_timestamp=args.to_timestamp,
            ),
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

    media_urls = [
        url
        for post in posts
        for url in passes.get_media_urls(
            post,
            images=not args.only_videos,
            videos=not args.only_images,
            image_size=args.size,
        )
    ]

    if not media_urls:
        logger.warning("No downloadable media found")
        await passes.close()
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
            "Downloading media[logging.keyword]...", total=len(media_urls)
        )

        tasks = [
            asyncio.create_task(
                passes.download_media(
                    url,
                    args.output,
                    force_download=args.force_download,
                    creator_folder=not args.no_creator_folders,
                    done_callback=lambda: progress.update(progress_task, advance=1),
                )
            )
            for url in media_urls
        ]

        await asyncio.gather(*tasks)

    await passes.close()


if __name__ == "__main__":
    asyncio.run(main())
