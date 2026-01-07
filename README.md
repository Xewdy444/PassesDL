# PassesDL

A tool for downloading media from www.passes.com. This tool allows you to download images and videos from your gallery or posts in a user's feed or messages with some convenient filtering options. The download process is fast and efficient as the requests and file writes are made asynchronously.

## Installation

    $ pip install -r requirements.txt

If you plan on logging in with credentials without a CAPTCHA solving service (only required if you don't have Google Chrome installed):

    $ python -m patchright install chrome --with-deps

### FFmpeg

To download videos, you'll need to install [FFmpeg](https://github.com/FFmpeg/FFmpeg).

|   OS    |        Command         |
| :-----: | :--------------------: |
| Debian  | apt-get install ffmpeg |
|  MacOS  |  brew install ffmpeg   |
| Windows | winget install ffmpeg  |

You can also download the latest static build from [here](https://ffmpeg.org/download.html).

### Shaka Packager

To decrypt encrypted media, you'll need to install [Shaka Packager](https://github.com/shaka-project/shaka-packager). Get the [latest release](https://github.com/shaka-project/shaka-packager/releases) for your OS (e.g., `packager-win-x64.exe`) and add it to a directory listed in your system’s PATH, or place it in the project directory.

## Authorization

You will need to provide a refresh token or account credentials in the `config.toml` file. If you sign in directly with the website, you can provide your email and password in the configuration file, and the tool will automatically obtain a new refresh token when needed. If you use a third-party service to sign in to your account (Google, Facebook, X, Twitch), you'll need to do the following to obtain a refresh token:

1. Open the developer tools in your browser (F12)
2. Go to the "Network" tab
3. Sign in to your account with the third-party service
4. In the "Network" tab, find the request with the URL `https://www.passes.com/auth/success`
5. Within the query parameters of the request, you'll find the `refreshToken` parameter. Copy the value of this parameter and paste it into the `refresh_token` field in the configuration file

### Retrieving Refresh Token From Local Storage

You can also find the refresh token stored in your browser's local storage after signing in.

#### Google Chrome

1. Open **Developer Tools**
2. Go to the **Application** tab
3. In the left sidebar, under **Storage**, expand **Local Storage**
4. Click **`https://www.passes.com`**
5. Look for the key named **`refresh-token`** in the key/value list

#### Mozilla Firefox

1. Open **Developer Tools**
2. Go to the **Storage** tab
3. In the left sidebar, expand **Local Storage**
4. Click **`https://www.passes.com`**
5. Locate the key named **`refresh-token`** in the key/value list

Refresh tokens expire after two weeks, so you'll need to update it periodically.

## CAPTCHA Solving

Passes uses reCAPTCHA v3 Enterprise to protect against bots on their login page. The default method to solve this CAPTCHA is to use a Playwright browser to automatically solve it. If you don't want to use this method or it doesn't work for you, you can also use a CAPTCHA solving service by providing the API domain and API key in the `config.toml` file.

### Supported Services

- [CapSolver](https://www.capsolver.com/) (api.capsolver.com)
- [AntiCaptcha](https://anti-captcha.com/) (api.anti-captcha.com)

2Captcha and CapMonster were also tested and did not work for this website, so support for these services was not added.

## Widevine DRM

This tool is able to decrypt media protected with Widevine DRM. Encrypted media does not contain watermarks and is labeled as `original` for the image and video types. You can still choose to download unencrypted media, which does not require Shaka Packager, by specifying values other than `original` for the `--image-type` and `--video-type` options. Currently, unencrypted videos seem to never contain watermarks, therefore the default video type is `large` rather than `original`.

You can also provide your own Widevine device (.wvd) by specifying the path to the file in the `device_path` field under the `widevine` section in the `config.toml`. This is optional however as the tool will provide a device if one is not specified.

## Usage

```
Usage: main.py [-h] (-a USERNAME | --feed USERNAME | -m USERNAME | -g [USERNAME] | --urls URLS [URLS ...] | --file FILE) [-o OUTPUT] [--from FROM_TIMESTAMP] [-t TO_TIMESTAMP] [--limit LIMIT] [-it {small,medium,large,original}] [-vt {large,original}] [-fd] [-ncf] [-i | -v]

A tool for downloading media from www.passes.com

Options:
  -h, --help            show this help message and exit
  -a, --all USERNAME    Download media from posts in a user's feed and messages
  --feed USERNAME       Download media from posts in a user's feed
  -m, --messages USERNAME
                        Download media from posts in a user's messages
  -g, --gallery [USERNAME]
                        Download media from your gallery
  --urls, --links URLS [URLS ...]
                        A list of post URLs to download media from
  --file FILE           A file containing a list of post URLs to download media from
  -o, --output OUTPUT   The output directory to save media to
  --from FROM_TIMESTAMP
                        The creation timestamp of posts to start downloading media from
  -t, --to TO_TIMESTAMP
                        The creation timestamp of posts to stop downloading media
  --limit LIMIT         The maximum number of posts in the user's feed or messages to download media from
  -it, --image-type {small,medium,large,original}
                        The type of the images to download, by default original
  -vt, --video-type {large,original}
                        The type of the videos to download, by default large
  -fd, --force-download
                        Force downloading the media even if it already exists in the output directory
  -ncf, --no-creator-folders
                        Don't create subfolders for each creator
  -i, --images          Only download images
  -v, --videos          Only download videos
```

## Examples

Download images and videos from posts in a user's feed:

    $ python main.py --feed thebigpodwithshaq

Download images and videos from the three most recent accessible posts in a user's messages:

    $ python main.py --messages thebigpodwithshaq --limit 3

Download images and videos from a list of post URLs:

    $ python main.py --urls https://www.passes.com/thebigpodwithshaq/fb697c54-2f63-41f0-bdbe-9afd95026566 https://www.passes.com/thebigpodwithshaq/619074e2-22e2-4a70-8bf0-13ae0d2da33e https://www.passes.com/texasonefund/71d233df-0091-4149-8cdf-2f3b6789c07f

Download videos from a file containing a list of post URLs:

    $ python main.py --file urls.txt --videos

```
urls.txt:

https://www.passes.com/thebigpodwithshaq/fb697c54-2f63-41f0-bdbe-9afd95026566
https://www.passes.com/thebigpodwithshaq/619074e2-22e2-4a70-8bf0-13ae0d2da33e
https://www.passes.com/thebigpodwithshaq/d189dcaf-b069-4fa1-b6bd-fa2d54172059
https://www.passes.com/texasonefund/71d233df-0091-4149-8cdf-2f3b6789c07f
```

Download images from posts in a user's feed from a specific time range:

    $ python main.py --feed texasonefund --images --from 2024-07-01T00:00:00 --to 2024-08-31T23:59:59

Download all media from your gallery:

    $ python main.py --gallery

Download media from a specific user in your gallery:

    $ python main.py --gallery thebigpodwithshaq
