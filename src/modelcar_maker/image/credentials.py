import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from pydantic import PrivateAttr

from ..util import logger
from ..util import remove_path

SA_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")


def write_sa_authfile(authfile: Path, registry: str) -> None:
    """Build a skopeo authfile from the projected service-account token, atomically.

    modelcar-maker owns the whole file: no merge, no activation rules.
    """
    if not registry:
        raise ValueError("registry must be set to construct a service-account authfile")
    token = SA_TOKEN_PATH.read_text().strip()
    auth = base64.b64encode(f"serviceaccount:{token}".encode()).decode()
    config = json.dumps({"auths": {registry: {"auth": auth}}})
    fd, temp_name = tempfile.mkstemp(dir=authfile.parent)
    try:
        with os.fdopen(fd, "w") as temp_file:
            os.fchmod(fd, 0o600)
            temp_file.write(config)
        os.replace(temp_name, authfile)
    except OSError:
        remove_path(Path(temp_name))
        raise


class SaCredentials(BaseModel):
    authfile: Path
    registry: str

    _fallback_dir: Optional[Path] = PrivateAttr(default=None)

    @property
    def target(self) -> Path:
        """The authfile target in sa mode: the configured path, or a per-instance fallback dir.

        # ponytail: fallback file lives for process lifetime (no cleanup) — fine in the ephemeral
        # Job container; add context-managed cleanup if Skopeo is ever reused in a long-lived process.
        """
        if self.authfile.parent.is_dir():
            return self.authfile
        if self._fallback_dir is None:
            self._fallback_dir = Path(tempfile.mkdtemp(prefix="modelcar-authfile-"))
        return self._fallback_dir / "config.json"

    def auth_args(self) -> list[str]:
        if not SA_TOKEN_PATH.exists():
            return []
        target = self.target
        try:
            write_sa_authfile(target, self.registry)
        except OSError as error:
            logger.warning(f"Unable to refresh service-account authfile at {target}: {error}")
            return []
        return ["--authfile", str(target)]
