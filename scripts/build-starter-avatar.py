#!/usr/bin/env python3
"""Build the deterministic PawFlow starter avatar GLB."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path


BONES = {
    "Armature": (None, (0.0, 0.0, 0.0)),
    "Hips": ("Armature", (0.0, 1.0, 0.0)),
    "Spine": ("Hips", (0.0, 0.16, 0.0)),
    "Spine1": ("Spine", (0.0, 0.16, 0.0)),
    "Spine2": ("Spine1", (0.0, 0.16, 0.0)),
    "Neck": ("Spine2", (0.0, 0.12, 0.0)),
    "Head": ("Neck", (0.0, 0.18, 0.0)),
    "LeftEye": ("Head", (0.09, 0.04, 0.22)),
    "RightEye": ("Head", (-0.09, 0.04, 0.22)),
    "LeftShoulder": ("Spine2", (0.22, 0.06, 0.0)),
    "LeftArm": ("LeftShoulder", (0.18, -0.05, 0.0)),
    "LeftForeArm": ("LeftArm", (0.26, -0.12, 0.0)),
    "LeftHand": ("LeftForeArm", (0.22, -0.08, 0.0)),
    "RightShoulder": ("Spine2", (-0.22, 0.06, 0.0)),
    "RightArm": ("RightShoulder", (-0.18, -0.05, 0.0)),
    "RightForeArm": ("RightArm", (-0.26, -0.12, 0.0)),
    "RightHand": ("RightForeArm", (-0.22, -0.08, 0.0)),
    "LeftUpLeg": ("Hips", (0.12, -0.12, 0.0)),
    "LeftLeg": ("LeftUpLeg", (0.0, -0.42, 0.0)),
    "LeftFoot": ("LeftLeg", (0.0, -0.4, 0.03)),
    "LeftToeBase": ("LeftFoot", (0.0, -0.05, 0.13)),
    "RightUpLeg": ("Hips", (-0.12, -0.12, 0.0)),
    "RightLeg": ("RightUpLeg", (0.0, -0.42, 0.0)),
    "RightFoot": ("RightLeg", (0.0, -0.4, 0.03)),
    "RightToeBase": ("RightFoot", (0.0, -0.05, 0.13)),
}

for side in ("Left", "Right"):
    for finger in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
        parent = f"{side}Hand"
        for joint in range(1, 4):
            name = f"{side}Hand{finger}{joint}"
            BONES[name] = (parent, (0.0, 0.0, 0.0))
            parent = name

MORPHS = [
    "viseme_sil", "viseme_PP", "viseme_FF", "viseme_TH", "viseme_DD",
    "viseme_kk", "viseme_CH", "viseme_SS", "viseme_nn", "viseme_RR",
    "viseme_aa", "viseme_E", "viseme_I", "viseme_O", "viseme_U",
    "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft",
    "browOuterUpRight", "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "eyeBlinkLeft", "eyeBlinkRight", "eyeLookDownLeft", "eyeLookDownRight",
    "eyeLookInLeft", "eyeLookInRight", "eyeLookOutLeft", "eyeLookOutRight",
    "eyeLookUpLeft", "eyeLookUpRight", "eyeSquintLeft", "eyeSquintRight",
    "eyeWideLeft", "eyeWideRight", "jawForward", "jawLeft", "jawOpen",
    "jawRight", "mouthClose", "mouthDimpleLeft", "mouthDimpleRight",
    "mouthFrownLeft", "mouthFrownRight", "mouthFunnel", "mouthLeft",
    "mouthLowerDownLeft", "mouthLowerDownRight", "mouthPressLeft",
    "mouthPressRight", "mouthPucker", "mouthRight", "mouthRollLower",
    "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper", "mouthSmileLeft",
    "mouthSmileRight", "mouthStretchLeft", "mouthStretchRight",
    "mouthUpperUpLeft", "mouthUpperUpRight", "noseSneerLeft",
    "noseSneerRight", "tongueOut", "eyesLookDown", "eyesLookUp",
]


class Builder:
    def __init__(self):
        self.binary = bytearray()
        self.views = []
        self.accessors = []

    def add_view(self, payload: bytes, target: int) -> int:
        while len(self.binary) % 4:
            self.binary.append(0)
        offset = len(self.binary)
        self.binary.extend(payload)
        self.views.append({
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(payload),
            "target": target,
        })
        return len(self.views) - 1

    def floats(self, values, kind: str, count: int, *, bounds=False) -> int:
        flat = [value for item in values for value in item]
        view = self.add_view(struct.pack("<" + "f" * len(flat), *flat), 34962)
        accessor = {
            "bufferView": view,
            "componentType": 5126,
            "count": count,
            "type": kind,
        }
        if bounds:
            columns = len(values[0])
            accessor["min"] = [
                min(row[index] for row in values) for index in range(columns)]
            accessor["max"] = [
                max(row[index] for row in values) for index in range(columns)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1

    def indices(self, values) -> int:
        view = self.add_view(
            struct.pack("<" + "H" * len(values), *values), 34963)
        self.accessors.append({
            "bufferView": view,
            "componentType": 5123,
            "count": len(values),
            "type": "SCALAR",
            "min": [min(values)],
            "max": [max(values)],
        })
        return len(self.accessors) - 1


def build(output: Path) -> None:
    builder = Builder()
    cube = [
        (-0.5, -0.5, -0.5), (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5),
    ]
    cube_indices = [
        0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
        0, 1, 5, 0, 5, 4, 2, 3, 7, 2, 7, 6,
        1, 2, 6, 1, 6, 5, 3, 0, 4, 3, 4, 7,
    ]
    cube_position = builder.floats(cube, "VEC3", len(cube), bounds=True)
    cube_index = builder.indices(cube_indices)

    mouth = [
        (-0.09, -0.015, 0.0), (0.09, -0.015, 0.0),
        (0.09, 0.015, 0.0), (-0.09, 0.015, 0.0),
    ]
    mouth_position = builder.floats(
        mouth, "VEC3", len(mouth), bounds=True)
    mouth_index = builder.indices([0, 1, 2, 0, 2, 3])
    targets = []
    for index, name in enumerate(MORPHS):
        strength = 0.0 if name == "viseme_sil" else 0.012 + (
            index % 5) * 0.003
        delta = [
            (0.0, -strength, 0.0), (0.0, -strength, 0.0),
            (0.0, strength, 0.0), (0.0, strength, 0.0),
        ]
        targets.append({
            "POSITION": builder.floats(delta, "VEC3", len(delta)),
        })

    materials = [
        {
            "name": "PawFlow blue",
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.18, 0.42, 0.86, 1.0],
                "metallicFactor": 0.15,
                "roughnessFactor": 0.55,
            },
        },
        {
            "name": "PawFlow dark",
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.04, 0.07, 0.14, 1.0],
                "metallicFactor": 0.05,
                "roughnessFactor": 0.7,
            },
        },
        {
            "name": "PawFlow glow",
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.2, 0.95, 0.9, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.35,
            },
            "emissiveFactor": [0.08, 0.45, 0.42],
        },
    ]
    meshes = []
    for name, material in (
            ("Blue cube", 0), ("Dark cube", 1), ("Glow cube", 2)):
        meshes.append({
            "name": name,
            "primitives": [{
                "attributes": {"POSITION": cube_position},
                "indices": cube_index,
                "material": material,
            }],
        })
    meshes.append({
        "name": "Viseme mouth",
        "primitives": [{
            "attributes": {"POSITION": mouth_position},
            "indices": mouth_index,
            "material": 1,
            "targets": targets,
        }],
        "weights": [0.0] * len(MORPHS),
        "extras": {"targetNames": MORPHS},
    })

    nodes = []
    node_ids = {}

    def node(name, **values):
        node_ids[name] = len(nodes)
        nodes.append({"name": name, **values})

    for name, (_parent, translation) in BONES.items():
        node(name, translation=list(translation), children=[])

    for name, (parent, _translation) in BONES.items():
        if parent:
            nodes[node_ids[parent]]["children"].append(node_ids[name])

    def visual(parent, name, mesh, translation, scale):
        index = len(nodes)
        nodes.append({
            "name": name,
            "mesh": mesh,
            "translation": list(translation),
            "scale": list(scale),
        })
        nodes[node_ids[parent]]["children"].append(index)

    visual("Hips", "PelvisShell", 1, (0, 0.03, 0), (0.23, 0.14, 0.13))
    visual("Spine1", "TorsoShell", 0, (0, 0.05, 0), (0.28, 0.32, 0.14))
    visual("Head", "HeadShell", 0, (0, 0.02, 0), (0.25, 0.27, 0.22))
    visual("Head", "VisemeMouth", 3, (0, -0.05, 0.225), (1, 1, 1))
    for eye in ("LeftEye", "RightEye"):
        visual(eye, eye + "Glow", 2, (0, 0, 0), (0.045, 0.055, 0.025))
    for side in ("Left", "Right"):
        visual(side + "Arm", side + "UpperArmShell", 0,
               (0, -0.12, 0), (0.09, 0.28, 0.09))
        visual(side + "ForeArm", side + "ForeArmShell", 1,
               (0, -0.1, 0), (0.075, 0.24, 0.075))
        visual(side + "Hand", side + "HandShell", 2,
               (0, -0.04, 0), (0.09, 0.12, 0.06))
        visual(side + "UpLeg", side + "ThighShell", 0,
               (0, -0.18, 0), (0.12, 0.35, 0.12))
        visual(side + "Leg", side + "ShinShell", 1,
               (0, -0.18, 0), (0.1, 0.34, 0.1))
        visual(side + "Foot", side + "FootShell", 2,
               (0, -0.02, 0.08), (0.12, 0.08, 0.22))

    document = {
        "asset": {"version": "2.0", "generator": "PawFlow starter avatar builder"},
        "scene": 0,
        "scenes": [{"name": "PawFlow starter avatar", "nodes": [node_ids["Armature"]]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": builder.accessors,
        "bufferViews": builder.views,
        "buffers": [{"byteLength": len(builder.binary)}],
    }
    json_bytes = json.dumps(
        document, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    while len(builder.binary) % 4:
        builder.binary.append(0)
    total = 12 + 8 + len(json_bytes) + 8 + len(builder.binary)
    payload = (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(json_bytes), b"JSON") + json_bytes
        + struct.pack("<I4s", len(builder.binary), b"BIN\x00")
        + bytes(builder.binary)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: build-starter-avatar.py OUTPUT.glb")
    build(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
