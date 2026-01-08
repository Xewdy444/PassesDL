"""Utility classes for the Passes DRM client."""

from enum import Enum

from pywidevine import PSSH


class SecurityLevel(Enum):
    """Content decryption module security levels."""

    SW_SECURE_CRYPTO = "8fd9a7ea-73de-49bc-8b9e-ec1e73d325d3"
    SW_SECURE_DECODE = "d597b3c2-7827-4047-9ee4-c10e49f7bc14"
    HW_SECURE_CRYPTO = "585cc599-8421-49bf-9287-d1e824cceadf"
    HW_SECURE_DECODE = "61614631-f825-48f5-8497-b9b9c503b6e5"
    HW_SECURE_ALL = "debd9c88-9d4d-4e28-937e-79b3d4d6cae2"


class HashablePSSH(PSSH):
    """A hashable version of the pywidevine PSSH class."""

    def __hash__(self) -> int:
        return hash((self.version, self.flags, self.system_id, tuple(self.key_ids)))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PSSH):
            return False

        return (
            self.version == other.version
            and self.flags == other.flags
            and self.system_id == other.system_id
            and self.key_ids == other.key_ids
        )
