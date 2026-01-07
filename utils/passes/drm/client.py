"""The client for handling DRM-protected content on www.passes.com."""

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import aiohttp
import xmltodict
from async_lru import alru_cache
from pywidevine import PSSH, Cdm, Device, Key
from pywidevine.utils import get_binary_path

from .constants import (
    BUYDRM_SERVICE_CERTIFICATE,
    DEFAULT_DEVICE,
    DEFAULT_KEY,
    DEFAULT_PSSH,
)
from .utils import HashablePSSH, SecurityLevel

logger = logging.getLogger(__name__)


class PassesDRM:
    """
    A client for handling DRM-protected content on www.passes.com.

    Parameters
    ----------
    session : aiohttp.ClientSession
        An aiohttp client session for making HTTP requests.
    device : Optional[Device], optional
        A pywidevine Device instance.
        If not provided, a default device will be used.
    """

    def __init__(
        self, session: aiohttp.ClientSession, *, device: Optional[Device] = None
    ) -> None:
        self._session = session
        self._cdm = Cdm.from_device(device or Device.loads(DEFAULT_DEVICE))

    @staticmethod
    def _get_shaka_packager_path() -> Optional[Path]:
        """
        Get the path to the Shaka Packager binary based on the current platform.

        Returns
        -------
        Optional[Path]
            The path to the Shaka Packager binary, or None if not found.
        """
        platform = {"win32": "win", "darwin": "osx"}.get(sys.platform, sys.platform)

        return get_binary_path(
            f"packager-{platform}-x64",
            f"packager-{platform}-arm64",
            f"packager-{platform}",
            "shaka-packager",
            "packager",
        )

    @alru_cache
    async def get_widevine_pssh(self, mpd_url: str) -> Optional[HashablePSSH]:
        """
        Extract the Widevine PSSH from the given MPEG-DASH manifest URL.

        Parameters
        ----------
        mpd_url : str
            The URL of the MPEG-DASH manifest.

        Returns
        -------
        Optional[HashablePSSH]
            The Widevine PSSH if found, otherwise None.
        """
        response = await self._session.get(mpd_url)
        mpd_dict = xmltodict.parse(await response.text())

        for adaptation_set in mpd_dict["MPD"]["Period"]["AdaptationSet"]:
            content_protections = adaptation_set.get("ContentProtection")

            if not isinstance(content_protections, list):
                continue

            for content_protection in content_protections:
                pssh_base64 = content_protection.get("cenc:pssh")

                if pssh_base64 is None:
                    continue

                pssh = HashablePSSH(pssh_base64)

                if pssh.system_id != PSSH.SystemId.Widevine:
                    continue

                return pssh

        return None

    @alru_cache
    async def get_decryption_key(self, pssh: HashablePSSH) -> Optional[Key]:
        """
        Obtain the decryption key for the given Widevine PSSH.

        Parameters
        ----------
        pssh : HashablePSSH
            The Widevine PSSH.

        Returns
        -------
        Optional[Key]
            The decryption key if obtained, otherwise None.
        """
        if pssh == DEFAULT_PSSH:
            logger.info(
                "Using default decryption key: %s:%s",
                DEFAULT_KEY.kid.hex,
                DEFAULT_KEY.key.hex(),
                extra={"highlighter": None},
            )

            return DEFAULT_KEY

        session_id = self._cdm.open()
        self._cdm.set_service_certificate(session_id, BUYDRM_SERVICE_CERTIFICATE)
        challenge = self._cdm.get_license_challenge(session_id, pssh)

        response = await self._session.post(
            "https://www.passes.com/api/content/drm/license-request",
            params={
                "drm-type": "widevine",
                "drm-code": SecurityLevel.SW_SECURE_CRYPTO.value,
            },
            data=challenge,
        )

        license_message = await response.read()
        self._cdm.parse_license(session_id, license_message)

        decryption_keys = self._cdm.get_keys(session_id)
        self._cdm.close(session_id)

        if not decryption_keys:
            return None

        decryption_key = decryption_keys[0]

        logger.info(
            "Obtained decryption key: %s:%s",
            decryption_key.kid.hex,
            decryption_key.key.hex(),
            extra={"highlighter": None},
        )

        return decryption_key

    async def decrypt_file(self, encrypted_file: Path, decryption_key: Key) -> None:
        """
        Decrypt the given encrypted media file using Shaka Packager.

        Parameters
        ----------
        encrypted_file : Path
            The path to the encrypted media file.
        decryption_key : Key
            The decryption key to use.

        Raises
        ------
        FileNotFoundError
            If the Shaka Packager binary is not found.
        """
        with tempfile.TemporaryDirectory(dir=encrypted_file.parent) as temp_dir:
            output_file = Path(temp_dir) / f"decrypted_{encrypted_file.name}"

            args = [
                f"input={encrypted_file},stream=0,output={output_file}",
                f"--temp_dir={temp_dir}",
                "--enable_raw_key_decryption",
                "--keys",
                f"key_id={decryption_key.kid.hex}:key={decryption_key.key.hex()}",
            ]

            shaka_packager_path = self._get_shaka_packager_path()

            if shaka_packager_path is None:
                raise FileNotFoundError("Shaka Packager binary not found.")

            process = await asyncio.create_subprocess_exec(
                shaka_packager_path,
                *args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            await process.wait()
            os.replace(output_file, encrypted_file)
