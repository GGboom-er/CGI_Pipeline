"""Immutable managed observation sidecars; hashes provide integrity, not authenticity."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

from cgi_pipeline.paths import REPOSITORY_ROOT

MAX_IMAGE_BYTES = 128 * 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def managed_path(value):
    path = Path(value).resolve(strict=True)
    root = Path(REPOSITORY_ROOT).resolve()
    if not path.is_file() or not any(path.is_relative_to(root / name)
                                     for name in ("projects", "runs")):
        raise ValueError("Observation files must be regular files inside managed projects or runs")
    return path


def checked_bytes(path, maximum):
    if path.stat().st_size > maximum:
        raise ValueError("Observation file exceeds its byte budget")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Observation file grew beyond its byte budget")
    return data


def png_identity(value):
    path = managed_path(value)
    data = checked_bytes(path, MAX_IMAGE_BYTES)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Observation requires the original PNG")
    position, size, saw_data, ended = 8, None, False, False
    while position < len(data):
        if position + 12 > len(data):
            raise ValueError("Truncated PNG chunk")
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        end = position + 12 + length
        if end > len(data):
            raise ValueError("Truncated PNG payload")
        payload = data[position + 8:end - 4]
        crc = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise ValueError("PNG chunk CRC mismatch")
        if position == 8:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG first chunk must be IHDR")
            size = list(struct.unpack(">II", payload[:8]))
        saw_data = saw_data or kind == b"IDAT"
        position = end
        if kind == b"IEND":
            ended = length == 0 and position == len(data)
            break
    if not ended or not saw_data or not size or min(size) < 1:
        raise ValueError("PNG structure is incomplete")
    return {"path": path.as_posix(), "sha256": hashlib.sha256(data).hexdigest(),
            "width": size[0], "height": size[1]}


def publish(state, png_path, width, height):
    image = png_identity(png_path)
    expected = state["view"]["port_size"]
    mapping(state, expected)
    if [width, height] != expected or [image["width"], image["height"]] != expected:
        raise ValueError("Grounding requires original PNG and port dimensions to match exactly")
    record = {"schema_version": 1, "state": state, "image": image}
    data = canonical(record)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("Observation sidecar exceeds its byte budget")
    destination = Path(image["path"]).with_suffix(".observation.json")
    with destination.open("xb") as stream:
        stream.write(data)
    return {"path": destination.as_posix(), "sha256": hashlib.sha256(data).hexdigest()}


def mapping(state, dimensions):
    view = state["view"]
    if view.get("scope") != "viewport" or view.get("fit") != "full_port_no_crop":
        raise ValueError("Window screenshots or unknown crop cannot certify viewport pixel mapping")
    if view.get("viewport") != [0, 0] + list(dimensions):
        raise ValueError("Grounding requires the complete zero-origin native viewport")


def read(reference):
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise ValueError("Observation reference requires exactly path and sha256")
    digest = reference["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(
            c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid observation SHA-256")
    path = managed_path(reference["path"])
    data = checked_bytes(path, MAX_RECORD_BYTES)
    if hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError("Observation sidecar SHA-256 mismatch")
    def invalid_constant(value):
        raise ValueError("Nonfinite JSON value: " + value)
    record = json.loads(data, parse_constant=invalid_constant)
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise ValueError("Unsupported observation schema")
    image = record.get("image")
    if not isinstance(image, dict) or png_identity(image.get("path")) != image:
        raise RuntimeError("Observation original PNG bytes or dimensions changed")
    state = record.get("state")
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("Unsupported observation state schema")
    if [image["width"], image["height"]] != state["view"]["port_size"]:
        raise ValueError("Observation image/port mapping is not 1:1")
    mapping(state, state["view"]["port_size"])
    return record
