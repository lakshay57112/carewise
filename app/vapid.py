"""Makes the key pair that phone notifications need.  Run:  python -m app.vapid

Put the two lines it prints into .env (and into Render's Environment tab). Keep the private one secret.
"""
import base64

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_pair() -> tuple[str, str]:
    v = Vapid()
    v.generate_keys()
    public = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    private = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    return _b64(public), _b64(private)


if __name__ == "__main__":
    pub, priv = make_pair()
    print(f"VAPID_PUBLIC_KEY={pub}")
    print(f"VAPID_PRIVATE_KEY={priv}")
