"""Parser for Rhythia .rhm map files.

A .rhm file is a plain zip archive with exactly three entries:
  - "map"   -> UTF-8 JSON with metadata + notes
  - "audio" -> the song audio (mp3)
  - "cover" -> cover art (jpg)
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field


@dataclass
class Note:
    time_ms: int
    x: float
    y: float


@dataclass
class MapMetadata:
    online_id: int
    online_status: str
    legacy_id: str
    song_name: str
    mappers: list[str]
    title: str
    duration_ms: int
    difficulty: int
    custom_difficulty_name: str
    star_rating: float


@dataclass
class Map:
    metadata: MapMetadata
    notes: list[Note] = field(default_factory=list)
    audio_bytes: bytes = b""
    cover_bytes: bytes = b""

    @property
    def duration_ms(self) -> int:
        if self.notes:
            return max(self.metadata.duration_ms, self.notes[-1].time_ms)
        return self.metadata.duration_ms


def load(path: str) -> Map:
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        # Only "map" is mandatory; the game ships maps without audio and/or
        # cover (confirmed against yo-ru/rhmParse, which mirrors the game's
        # own encoder).
        if "map" not in names:
            raise ValueError(f"{path}: missing 'map' entry in .rhm zip")

        raw = zf.read("map")
        audio_bytes = zf.read("audio") if "audio" in names else b""
        cover_bytes = zf.read("cover") if "cover" in names else b""

    doc = json.loads(raw)

    metadata = MapMetadata(
        online_id=doc["OnlineId"],
        online_status=doc.get("OnlineStatus", ""),
        legacy_id=doc.get("LegacyId", ""),
        song_name=doc.get("SongName", ""),
        mappers=doc.get("Mappers", []),
        title=doc.get("Title", ""),
        duration_ms=doc.get("Duration", 0),
        difficulty=doc.get("Difficulty", 0),
        custom_difficulty_name=doc.get("CustomDifficultyName", ""),
        star_rating=doc.get("StarRating", 0.0),
    )

    notes = [
        Note(time_ms=n["Time"], x=float(n["X"]), y=float(n["Y"]))
        for n in doc.get("Notes", [])
    ]
    notes.sort(key=lambda n: n.time_ms)

    return Map(metadata=metadata, notes=notes, audio_bytes=audio_bytes, cover_bytes=cover_bytes)


def save(map_: Map, path: str) -> None:
    """Writes a Map back out as an .rhm zip (e.g. to convert an .sspm into
    the game's own import format). Grid coordinates are kept as ints when
    they are whole (like the game's encoder) and floats for quantum notes."""
    meta = map_.metadata
    doc = {
        "OnlineId": meta.online_id,
        "OnlineStatus": meta.online_status,
        "LegacyId": meta.legacy_id,
        "SongName": meta.song_name,
        "Mappers": meta.mappers,
        "Title": meta.title,
        "Duration": meta.duration_ms,
        "Difficulty": meta.difficulty,
        "CustomDifficultyName": meta.custom_difficulty_name,
        "StarRating": meta.star_rating,
        "Notes": [
            {"Time": n.time_ms,
             "X": int(n.x) if float(n.x).is_integer() else n.x,
             "Y": int(n.y) if float(n.y).is_integer() else n.y}
            for n in map_.notes
        ],
        "AudioFileName": "",
        "ImagePath": "",
        "TimingPoints": [],
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("map", json.dumps(doc))
        if map_.audio_bytes:
            zf.writestr("audio", map_.audio_bytes)
        if map_.cover_bytes:
            zf.writestr("cover", map_.cover_bytes)
