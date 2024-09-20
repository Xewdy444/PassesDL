# PassesDL
A tool for downloading media from www.passes.com. This tool can download images and videos from posts on the website, either by providing the URLs of the posts or by retrieving the posts from a user's feed. The requests are asynchronous, so the download process is fast and efficient.

## Installation
    $ pip install -r requirements.txt

This tool requires FFmpeg to be installed on your system to download videos.

|   OS    |        Command         |
| :-----: | :--------------------: |
| Debian  | apt-get install ffmpeg |
|  MacOS  |  brew install ffmpeg   |
| Windows | winget install ffmpeg  |

You can also download the latest static build from [here](https://ffmpeg.org/download.html).

## Usage
```
Usage: main.py [-h] (-u USER | --urls URLS [URLS ...] | --file FILE) [-o OUTPUT] [--from FROM_TIMESTAMP] [-t TO_TIMESTAMP] [--limit LIMIT] [-s {small,medium,large}] [-fd] [-ncf] [-i | -v]

A tool for downloading media from www.passes.com

Options:
  -h, --help            show this help message and exit
  -u, --user USER       The username of the user to download media from posts in their feed
  --urls, --links URLS [URLS ...]
                        A list of post urls to download media from
  --file FILE           A file containing a list of post urls to download media from
  -o, --output OUTPUT   The output directory to save media to
  --from FROM_TIMESTAMP
                        The creation timestamp of posts to start downloading media from
  -t, --to TO_TIMESTAMP
                        The creation timestamp of posts to stop downloading media
  --limit LIMIT         The maximum number of posts in the user's feed to download media from
  -s, --size {small,medium,large}
                        The size of the images to download
  -fd, --force-download
                        Force downloading the media even if it already exists in the output directory
  -ncf, --no-creator-folders
                        Don't create subfolders for each creator
  -i, --images          Only download images
  -v, --videos          Only download videos
  ```

## Examples
Download images and videos from posts in a user's feed:
```bash
$ python main.py --user thebigpodwithshaq
```

Download images and videos from the three most recent accessible posts in a user's feed:
```bash
$ python main.py --user thebigpodwithshaq --limit 3
```

Download images and videos from a list of post URLs:
```bash
$ python main.py --urls https://www.passes.com/thebigpodwithshaq/fb697c54-2f63-41f0-bdbe-9afd95026566 https://www.passes.com/thebigpodwithshaq/619074e2-22e2-4a70-8bf0-13ae0d2da33e https://www.passes.com/texasonefund/71d233df-0091-4149-8cdf-2f3b6789c07f
```

Download videos from a file containing a list of post URLs:
```bash
$ python main.py --file urls.txt --videos
```

```
urls.txt:

https://www.passes.com/thebigpodwithshaq/fb697c54-2f63-41f0-bdbe-9afd95026566
https://www.passes.com/thebigpodwithshaq/619074e2-22e2-4a70-8bf0-13ae0d2da33e
https://www.passes.com/thebigpodwithshaq/d189dcaf-b069-4fa1-b6bd-fa2d54172059
https://www.passes.com/texasonefund/71d233df-0091-4149-8cdf-2f3b6789c07f
```

Download images from posts in a user's feed from a specific time range:
```bash
$ python main.py --user texasonefund --images --from 2024-07-01T00:00:00 --to 2024-08-31T23:59:59
```