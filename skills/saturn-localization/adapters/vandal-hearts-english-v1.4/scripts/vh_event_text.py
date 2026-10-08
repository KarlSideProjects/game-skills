#!/usr/bin/env python3
"""Decode and safely re-encode confirmed Vandal Hearts event records.

The NUL-delimited EVDEMO0 records are retained byte-for-byte outside explicitly
translated text nodes. Unknown bytes remain opaque, never guessed into text.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vh_native_dialogue_font import encode_text, slot_from_pair

SINGLE = {0xA0: " ", **{c: chr(65 + c - 0xA1) for c in range(0xA1, 0xBB)},
          **{c: chr(97 + c - 0xC1) for c in range(0xC1, 0xDB)},
          0xBB: "!", 0xBC: '"', 0xBD: "#", 0xC0: "=", 0xDC: "'",
          0xDD: "(", 0xDE: ")", 0xEC: ",", 0xED: "-", 0xEE: ".",
          0xEF: "/", 0xF0: "0", 0xF1: "1", 0xF2: "2", 0xF3: "3", 0xF5: "5",
          0xF6: "6", 0xF7: "7", 0xF8: "8", 0xFA: ":", 0xFB: "?"}
# Static event-parser branches: 46/66 share the one-byte action handler;
# 57 is a one-byte action with a non-consuming LF peek. The decimal controls
# use their following 30-39 run as the lookup/format value.
KNOWN_ACTION = {0x46, 0x57, 0x66}
DECIMAL_CONTROLS = {0x23, 0x54, 0x6F}
KNOWN_LINEBREAK = {0x0A}


def decode_nodes(raw: bytes) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = []
    i = 0
    while i < len(raw):
        b = raw[i]
        if b in SINGLE:
            start = i
            chars = []
            while i < len(raw) and raw[i] in SINGLE:
                chars.append(SINGLE[raw[i]])
                i += 1
            nodes.append({"kind": "text", "start": start, "end": i,
                          "raw_hex": raw[start:i].hex(), "text": "".join(chars)})
        elif b in KNOWN_LINEBREAK:
            nodes.append({"kind": "linebreak", "start": i, "end": i + 1,
                          "raw_hex": raw[i:i+1].hex()})
            i += 1
        elif b in DECIMAL_CONTROLS:
            start = i
            i += 1
            param_start = i
            while i < len(raw) and 0x30 <= raw[i] <= 0x39:
                i += 1
            node = {"kind": "parameter_control", "start": start, "end": i,
                    "raw_hex": raw[start:i].hex(), "opcode": b,
                    "parameter_hex": raw[param_start:i].hex()}
            if i > param_start:
                node["decimal_parameter"] = int(raw[param_start:i].decode("ascii"))
            nodes.append(node)
        elif b in KNOWN_ACTION:
            nodes.append({"kind": "opaque_action", "start": i, "end": i + 1,
                          "raw_hex": raw[i:i+1].hex(), "opcode": b})
            i += 1
        elif i + 1 < len(raw) and (0x81 <= b <= 0x9F or 0xE0 <= b <= 0xEA):
            pair = raw[i:i+2]
            slot = slot_from_pair(pair)
            nodes.append({"kind": "custom_glyph", "start": i, "end": i + 2,
                          "raw_hex": pair.hex(), "slot": slot})
            i += 2
        else:
            nodes.append({"kind": "opaque_byte", "start": i, "end": i + 1,
                          "raw_hex": raw[i:i+1].hex()})
            i += 1
    return nodes


def encode_nodes(nodes: list[dict[str, object]], translations: dict[str, str],
                 mapping: dict[str, int]) -> bytes:
    out = bytearray()
    for n in nodes:
        kind = n["kind"]
        if kind == "text":
            key = str(n["id"])
            text = translations.get(key, str(n["text"]))
            out.extend(encode_text(text, mapping))
        elif kind == "custom_glyph":
            out.extend(bytes.fromhex(str(n["raw_hex"])))
        elif kind in {"linebreak", "opaque_action", "parameter_control",
                      "opaque_byte", "opaque_control"}:
            out.extend(bytes.fromhex(str(n["raw_hex"])))
        else:
            raise ValueError(f"unsupported node kind: {kind}")
    return bytes(out)


def split_nul_records(data: bytes) -> list[tuple[int, bytes]]:
    rows = []
    start = 0
    while start < len(data):
        end = data.find(b"\0", start)
        if end < 0:
            break
        if end > start:
            rows.append((start, data[start:end]))
        start = end + 1
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--limit", type=int, default=0, help="optional cap for a controlled validation run")
    a = p.parse_args()
    data = a.input.read_bytes()
    rows = split_nul_records(data)
    if a.limit:
        rows = rows[:a.limit]
    with a.output.open("x", encoding="utf-8") as f:
        for ordinal, (offset, raw) in enumerate(rows):
            nodes = decode_nodes(raw)
            for idx, node in enumerate(nodes):
                if node["kind"] == "text":
                    node["id"] = f"{a.input.name}:{offset:08x}:{idx:03d}"
            f.write(json.dumps({"id": f"{a.input.name}:{offset:08x}", "file": a.input.name,
                                "offset": offset, "record_length": len(raw),
                                "source_hex": raw.hex(), "nodes": nodes,
                                "status": "confirmed-container; semantics include opaque preserved nodes"},
                               ensure_ascii=False) + "\n")
    print(json.dumps({"records": len(rows), "output": str(a.output),
                      "status": "decoded; re-encoding helper preserves raw opaque nodes"}))


def _self_check() -> None:
    sample = bytes.fromhex("b3cfcc c4c9c5d2fa 0a b7c8c1d4a0c9d3a0d4c8c9d3a0d0ccc1c3c5fb 46 0a 00".replace(" ", ""))
    body = sample[:-1]
    nodes = decode_nodes(body)
    assert [x["kind"] for x in nodes] == ["text", "linebreak", "text", "opaque_action", "linebreak"]
    assert nodes[0]["text"] == "Soldier:"
    assert nodes[2]["text"] == "What is this place?"
    assert b"".join(bytes.fromhex(str(x["raw_hex"])) if x["kind"] != "text"
                    else encode_text(str(x["text"]), {}) for x in nodes) == body
    assert len(nodes) == 5
    punctuation = decode_nodes(bytes.fromhex("c0ecdddeedf1f2f3"))
    assert len(punctuation) == 1 and punctuation[0]["text"] == "=,()-123"
    action = decode_nodes(bytes.fromhex("b3 57 0a b4".replace(" ", "")))
    assert [n["kind"] for n in action] == ["text", "opaque_action", "linebreak", "text"]
    variable = decode_nodes(bytes.fromhex("23 33 32 a1".replace(" ", "")))
    assert variable[0]["kind"] == "parameter_control"
    assert variable[0]["raw_hex"] == "233332" and variable[0]["decimal_parameter"] == 32
    digit = decode_nodes(bytes.fromhex("54 31 35 a1".replace(" ", "")))
    assert digit[0]["kind"] == "parameter_control" and digit[0]["decimal_parameter"] == 15
    assert digit[1]["text"] == "A"
    assert decode_nodes(bytes.fromhex("f0"))[0]["text"] == "0"


if __name__ == "__main__":
    if "--self-check" in __import__("sys").argv:
        _self_check()
        print("PASS: confirmed text/control tokens round-trip")
    else:
        main()
