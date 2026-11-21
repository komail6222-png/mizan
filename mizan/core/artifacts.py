"""Immutable bytes. Only the local coordinator holds the signing key."""
import base64
import os
import re
import tempfile
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from .contract import canonical, digest, require, Refused, strict_load

DOMAIN = b"MIZAN/local-publication/v1\0"


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def install(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".stage-")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            require(path.read_bytes() == data, "CAS_COLLISION", "Immutable path contains different bytes")
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


class Artifacts:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.blobs = self.root / "objects"
        self.blobs.mkdir(mode=0o700, exist_ok=True)
        keypath = self.root / "keys" / "release.key"
        publicpath = keypath.with_name("release.pub")
        if not keypath.exists():
            require(not (self.root / "state.db").exists(), "KEY_MISSING", "Existing store lost its release key")
            key = Ed25519PrivateKey.generate()
            install(keypath, key.private_bytes_raw())
        self.key = Ed25519PrivateKey.from_private_bytes(keypath.read_bytes())
        public = self.key.public_key().public_bytes_raw()
        if not publicpath.exists():
            require(not (self.root / "state.db").exists(), "TRUST_MISSING", "Missing public trust root")
            install(publicpath, public)
        require(publicpath.read_bytes() == public, "TRUST_MISMATCH", "Release key does not match pinned root")
        self.public = Ed25519PublicKey.from_public_bytes(public)
        self.key_id = digest(public)

    def path(self, key):
        require(isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key), "BAD_DIGEST", "Bad object identifier")
        return self.blobs / key

    def put(self, value):
        blob = canonical(value)
        key = digest(blob)
        install(self.path(key), blob)
        return key

    def read(self, key):
        try:
            data = self.path(key).read_bytes()
        except FileNotFoundError as exc:
            raise Refused("OBJECT_MISSING", "A referenced object is missing") from exc
        require(digest(data) == key, "OBJECT_CHANGED", "Object digest mismatch")
        return strict_load(data)

    def seal(self, tenant, kind, object_id, context):
        # The signed schema has ASCII keys and string values only; no floating-point canonicalization.
        body = dict(tenant=tenant, kind=kind, object=object_id, context=context, key_id=self.key_id, schema="1")
        require(all(isinstance(v,str) for v in body.values()),"MANIFEST","Invalid manifest field")
        signature = self.key.sign(DOMAIN + canonical(body))
        return {"body": body, "signature": base64.b64encode(signature).decode()}

    def open(self, seal, tenant, kind, context):
        body = seal["body"]
        require(body.get("tenant")==tenant and body.get("kind")==kind and body.get("context")==context
                and body.get("key_id")==self.key_id and body.get("schema")=="1", "MANIFEST_SCOPE",
                "Artifact role, tenant, context or signing key mismatch")
        try:
            self.public.verify(base64.b64decode(seal["signature"],validate=True), DOMAIN + canonical(body))
        except (ValueError,InvalidSignature) as exc:
            raise Refused("BAD_SIGNATURE", "Artifact signature did not verify") from exc
        return self.read(body["object"])
