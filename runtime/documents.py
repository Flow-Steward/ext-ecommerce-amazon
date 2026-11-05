"""Amazon documents (feed and report files) and platform artifacts.

Amazon hands out feed and report contents through presigned Amazon S3 URLs. A
presigned URL is a time-limited credential: whoever holds it can read the
report or, for a feed document, write the feed. This module is the only place
that sees those URLs, and nothing it returns contains one. Downloads and uploads
go through ``SpApiTransport.download_document`` / ``upload_document``, which
accept only ``https`` URLs on an Amazon S3 host and never send the access token
there.

Decompression is bounded twice: the compressed download is capped by the
transport, and the gunzip output is capped here while it is produced, chunk by
chunk, so a small archive that would expand to gigabytes (a "zip bomb") is
refused after at most the cap plus one chunk has been inflated.

Memory: content is collected in a ``BytesIO`` and returned with ``getvalue()``,
which hands over the buffer itself instead of copying it, and the SDK's artifact
writer takes a ``bytes`` object without copying it again. A 200 MB report is
therefore held once (plus its compressed download), not twice.
"""

from __future__ import annotations

import io
import zlib
from collections.abc import Mapping
from typing import Any

from . import errors
from .errors import ExtensionError
from .registry import ArtifactBinding, Context
from .values import dig

_GZIP_WBITS = 16 + zlib.MAX_WBITS
_INFLATE_CHUNK = 256 * 1024
#: An absurd number of concatenated gzip members is treated as hostile.
_MAX_GZIP_MEMBERS = 64


def gunzip_bounded(data: bytes, *, max_bytes: int) -> bytes:
    """Inflate a gzip stream (one or more members) to at most ``max_bytes``."""
    output = io.BytesIO()
    size = 0
    remaining = data
    members = 0
    while remaining:
        members += 1
        if members > _MAX_GZIP_MEMBERS:
            raise _too_large()
        inflater = zlib.decompressobj(_GZIP_WBITS)
        pending = remaining
        try:
            while True:
                # max_length bounds every step, so output is checked before it can grow
                # past the cap by more than one chunk.
                chunk = inflater.decompress(pending, _INFLATE_CHUNK)
                size += len(chunk)
                if size > max_bytes:
                    raise _too_large()
                output.write(chunk)
                pending = inflater.unconsumed_tail
                if inflater.eof or (not chunk and not pending):
                    break
            if not inflater.eof:
                raise _corrupt()
        except zlib.error as exc:
            raise _corrupt() from exc
        # Bytes after the end of one member are either another member or padding.
        remaining = inflater.unused_data.lstrip(b"\x00")
    # getvalue() returns the BytesIO's own buffer: no second full copy.
    return output.getvalue()


def fetch_document(
    context: Context,
    document: Any,
    *,
    max_download_bytes: int,
    max_content_bytes: int,
    purpose: str,
) -> tuple[bytes, bool]:
    """Download one feed/report document described by Amazon's document object.

    Returns the (decompressed) content and whether it arrived gzip-compressed.
    The document object's ``url`` is used here and nowhere else.
    """
    if not isinstance(document, Mapping):
        raise ExtensionError(errors.DOCUMENT_UNAVAILABLE, "Amazon returned no document details")
    compression = dig(document, "compressionAlgorithm")
    if compression not in (None, "", "GZIP"):
        raise ExtensionError(
            errors.DOCUMENT_UNAVAILABLE,
            "Amazon returned the document in an unsupported compression",
        )
    raw = context.transport.download_document(
        str(document.get("url") or ""), max_bytes=max_download_bytes, purpose=purpose
    )
    if compression == "GZIP":
        return gunzip_bounded(raw, max_bytes=max_content_bytes), True
    if len(raw) > max_content_bytes:
        raise _too_large()
    return raw, False


def read_artifact(context: Context, binding: ArtifactBinding) -> bytes:
    """The bytes of the operation's input artifact, within the binding's size limit."""
    reader = context.artifact_reader
    if reader is None:
        raise ExtensionError(errors.ARTIFACT_INPUT_UNAVAILABLE, "The input file is not available")
    try:
        body = reader(dict(context.payload), binding_key=binding.binding_key)
    except ExtensionError:
        raise
    except Exception as exc:  # the SDK raises ArtifactAccessError and transport errors
        raise ExtensionError(
            errors.ARTIFACT_INPUT_UNAVAILABLE,
            "The input file could not be read from the platform; pass a file from an earlier step",
        ) from exc
    if not isinstance(body, (bytes, bytearray)):
        raise ExtensionError(errors.ARTIFACT_INPUT_UNAVAILABLE, "The input file could not be read")
    if len(body) > binding.max_size_bytes:
        raise ExtensionError(
            errors.INVALID_PAYLOAD,
            f"The input file is larger than {binding.max_size_bytes} bytes",
        )
    return bytes(body)


def write_artifact(context: Context, binding: ArtifactBinding, body: bytes) -> dict[str, Any]:
    """Write ``body`` as the operation's output artifact and return its safe facts.

    The content type is the binding's own: the platform's upload grant is issued
    for exactly that type and refuses any other.
    """
    writer = context.artifact_writer
    if writer is None:
        raise ExtensionError(errors.ARTIFACT_OUTPUT_UNAVAILABLE, "No output file can be written")
    if len(body) > binding.max_size_bytes:
        raise _too_large()
    try:
        written = writer(
            dict(context.payload),
            body,
            binding_key=binding.binding_key,
            content_type=binding.content_type,
        )
    except ExtensionError:
        raise
    except Exception as exc:
        raise ExtensionError(
            errors.ARTIFACT_OUTPUT_UNAVAILABLE,
            "The file could not be stored on the platform; the step needs an output file grant",
        ) from exc
    if not isinstance(written, Mapping):
        raise ExtensionError(
            errors.ARTIFACT_OUTPUT_UNAVAILABLE, "The platform did not confirm the file"
        )
    handle = str(written.get("artifact_handle") or written.get("artifact_id") or "")
    if not handle:
        raise ExtensionError(
            errors.ARTIFACT_OUTPUT_UNAVAILABLE, "The platform did not confirm the file"
        )
    return {
        "artifact_handle": handle,
        "size_bytes": int(written.get("size_bytes") or len(body)),
        "sha256": str(written.get("sha256") or ""),
        "content_type": str(written.get("content_type") or binding.content_type),
    }


def _too_large() -> ExtensionError:
    return ExtensionError(
        errors.RESPONSE_TOO_LARGE, "The Amazon document is larger than this step's safe limit"
    )


def _corrupt() -> ExtensionError:
    return ExtensionError(
        errors.DOCUMENT_UNAVAILABLE,
        "The Amazon document is not a valid gzip file; request it again",
    )


__all__ = ["fetch_document", "gunzip_bounded", "read_artifact", "write_artifact"]
