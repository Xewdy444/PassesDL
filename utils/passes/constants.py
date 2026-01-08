"""Constants for the Passes client."""

from typing import Any, Dict, Final

RECAPTCHA_SITEKEY: Final[str] = "6LdZUY4qAAAAAEX-6hC26gsQoQK3VgmCOVLxR7Cz"

CAPTCHA_TASK_JSON: Final[Dict[str, Dict[str, Any]]] = {
    "api.capsolver.com": {"type": "ReCaptchaV3EnterpriseTaskProxyLess"},
    "api.anti-captcha.com": {
        "type": "RecaptchaV3TaskProxyless",
        "minScore": 0.9,
        "isEnterprise": True,
    },
}
