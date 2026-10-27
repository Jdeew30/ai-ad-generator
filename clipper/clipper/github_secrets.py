from __future__ import annotations

import base64
import logging

from .http import Http, HttpError

log = logging.getLogger(__name__)

API_URL = "https://api.github.com"


def update_secret(http: Http, token: str, repository: str, name: str, value: str, *, api_url: str = API_URL) -> None:
    """Stores an Actions secret (sealed with the repository's public key, as GitHub requires).

    Needs a fine-grained token with "Secrets: read and write" on this repository: the
    workflow's own GITHUB_TOKEN cannot write secrets.
    """
    from nacl import encoding, public

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base = f"{api_url.rstrip('/')}/repos/{repository}/actions/secrets"
    key = http.get(f"{base}/public-key", headers=headers).json()
    sealed = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder())).encrypt(value.encode())
    http.put(
        f"{base}/{name}",
        headers=headers,
        json={"encrypted_value": base64.b64encode(sealed).decode(), "key_id": key["key_id"]},
        ok=(201, 204),
    )


def secret_saver(http: Http, token: str | None, repository: str | None, name: str):
    """Callback for rotated credentials: saves them as a secret, or explains what to do by hand."""

    def save(value: str) -> None:
        if not token or not repository:
            log.warning("Nouveau %s reçu : ajoutez GH_SECRETS_TOKEN pour qu'il soit enregistré automatiquement, "
                        "sinon mettez à jour le secret à la main si les publications échouent.", name)
            return
        try:
            update_secret(http, token, repository, name, value)
            log.info("Secret %s mis à jour automatiquement.", name)
        except HttpError as exc:
            log.error("Impossible d'enregistrer le nouveau %s (%s) : mettez-le à jour à la main.", name, exc)

    return save
