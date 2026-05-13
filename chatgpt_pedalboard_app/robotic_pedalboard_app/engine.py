from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pedalboard import (
    Bitcrush,
    Chorus,
    Clipping,
    Compressor,
    Delay,
    Distortion,
    Gain,
    HighpassFilter,
    Limiter,
    LowpassFilter,
    NoiseGate,
    Pedalboard,
    Phaser,
    PitchShift,
    Reverb,
)
from pedalboard.io import AudioFile
from pydantic import BaseModel, Field, field_validator

SAMPLE_RATE = 44_100
MAX_DURATION_SECONDS = 45.0


class EffectSpec(BaseModel):
    type: str = Field(description="Pedalboard effect name, such as Reverb, Delay, or Distortion.")
    params: dict[str, Any] = Field(default_factory=dict, description="Keyword parameters for the effect.")


class NoteSpec(BaseModel):
    midi: int = Field(ge=24, le=96, description="MIDI pitch number.")
    start_beat: float = Field(ge=0, description="Beat offset from the beginning.")
    beats: float = Field(gt=0, le=16, description="Duration in beats.")
    velocity: float = Field(default=0.7, ge=0, le=1)


class TrackSpec(BaseModel):
    name: str = Field(default="Track")
    waveform: Literal["sine", "square", "saw", "triangle", "noise"] = "sine"
    gain_db: float = Field(default=-10, ge=-60, le=12)
    pan: float = Field(default=0, ge=-1, le=1)
    notes: list[NoteSpec] = Field(default_factory=list)


class CompositionSpec(BaseModel):
    title: str = Field(default="Pedalboard sketch")
    prompt: str = Field(default="", description="Creative prompt used to seed default tracks if none are given.")
    bpm: float = Field(default=92, ge=40, le=220)
    duration_seconds: float = Field(default=12, gt=0.25, le=MAX_DURATION_SECONDS)
    key_midi: int = Field(default=48, ge=24, le=72, description="Root MIDI note for generated material.")
    tracks: list[TrackSpec] = Field(default_factory=list)
    effects: list[EffectSpec] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def title_must_have_visible_text(cls, value: str) -> str:
        clean = value.strip()
        return clean or "Pedalboard sketch"


@dataclass(frozen=True)
class EffectDescriptor:
    name: str
    purpose: str
    defaults: dict[str, Any]
    ranges: dict[str, str]


EFFECTS: dict[str, EffectDescriptor] = {
    "Gain": EffectDescriptor("Gain", "Level trim or boost.", {"gain_db": 0}, {"gain_db": "-60..24"}),
    "Compressor": EffectDescriptor(
        "Compressor",
        "Dynamic range control.",
        {"threshold_db": -18, "ratio": 3},
        {"threshold_db": "-60..0", "ratio": "1..20"},
    ),
    "Chorus": EffectDescriptor(
        "Chorus",
        "Modulated doubling and widening.",
        {"rate_hz": 1, "depth": 0.25, "mix": 0.35},
        {"rate_hz": "0.01..100", "depth": "0..1", "mix": "0..1"},
    ),
    "Delay": EffectDescriptor(
        "Delay",
        "Echo repeats.",
        {"delay_seconds": 0.25, "feedback": 0.25, "mix": 0.35},
        {"delay_seconds": "0..2", "feedback": "0..1", "mix": "0..1"},
    ),
    "Distortion": EffectDescriptor(
        "Distortion",
        "Harmonic saturation.",
        {"drive_db": 12},
        {"drive_db": "0..48"},
    ),
    "Reverb": EffectDescriptor(
        "Reverb",
        "Room and space.",
        {"room_size": 0.35, "wet_level": 0.25, "dry_level": 0.8},
        {"room_size": "0..1", "wet_level": "0..1", "dry_level": "0..1"},
    ),
    "Limiter": EffectDescriptor(
        "Limiter",
        "Final peak control.",
        {"threshold_db": -1.0, "release_ms": 80},
        {"threshold_db": "-20..0", "release_ms": "1..1000"},
    ),
    "HighpassFilter": EffectDescriptor(
        "HighpassFilter",
        "Remove low frequencies.",
        {"cutoff_frequency_hz": 80},
        {"cutoff_frequency_hz": "20..20000"},
    ),
    "LowpassFilter": EffectDescriptor(
        "LowpassFilter",
        "Remove high frequencies.",
        {"cutoff_frequency_hz": 12000},
        {"cutoff_frequency_hz": "20..20000"},
    ),
    "Phaser": EffectDescriptor(
        "Phaser",
        "Sweeping phase movement.",
        {"rate_hz": 0.5, "depth": 0.5, "mix": 0.35},
        {"rate_hz": "0.01..100", "depth": "0..1", "mix": "0..1"},
    ),
    "PitchShift": EffectDescriptor(
        "PitchShift",
        "Transpose audio.",
        {"semitones": 7},
        {"semitones": "-24..24"},
    ),
    "Bitcrush": EffectDescriptor(
        "Bitcrush",
        "Digital resolution reduction.",
        {"bit_depth": 8},
        {"bit_depth": "1..32"},
    ),
    "Clipping": EffectDescriptor(
        "Clipping",
        "Hard clip peaks.",
        {"threshold_db": -6},
        {"threshold_db": "-60..0"},
    ),
    "NoiseGate": EffectDescriptor(
        "NoiseGate",
        "Suppress quiet tails.",
        {"threshold_db": -60, "ratio": 10},
        {"threshold_db": "-100..0", "ratio": "1..20"},
    ),
}


def effect_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": effect.name,
            "purpose": effect.purpose,
            "defaults": effect.defaults,
            "ranges": effect.ranges,
        }
        for effect in EFFECTS.values()
    ]


def render_composition(spec: CompositionSpec, output_dir: Path, public_base_url: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    tracks = spec.tracks or _default_tracks(spec)
    audio = np.zeros((2, int(spec.duration_seconds * SAMPLE_RATE)), dtype=np.float32)

    for track in tracks:
        rendered = _render_track(track, spec.bpm, spec.duration_seconds)
        gain = 10 ** (track.gain_db / 20)
        left = math.cos((track.pan + 1) * math.pi / 4)
        right = math.sin((track.pan + 1) * math.pi / 4)
        audio[0] += rendered * gain * left
        audio[1] += rendered * gain * right

    peak = float(np.max(np.abs(audio))) or 1.0
    if peak > 0.92:
        audio *= 0.92 / peak

    board = build_pedalboard(spec.effects or _default_effects(spec.prompt))
    processed = board(audio, SAMPLE_RATE)
    processed = _finalize(processed)

    stem = _safe_stem(spec.title)
    digest = hashlib.sha1(spec.model_dump_json().encode("utf-8")).hexdigest()[:10]
    filename = f"{stem}-{digest}.wav"
    output_path = output_dir / filename
    with AudioFile(str(output_path), "w", SAMPLE_RATE, processed.shape[0]) as output_file:
        output_file.write(processed)

    return {
        "title": spec.title,
        "duration_seconds": round(processed.shape[1] / SAMPLE_RATE, 3),
        "sample_rate": SAMPLE_RATE,
        "channels": int(processed.shape[0]),
        "tracks": [_track_summary(track) for track in tracks],
        "effects": [_effect_summary(effect) for effect in (spec.effects or _default_effects(spec.prompt))],
        "file": {
            "download_url": f"{public_base_url.rstrip('/')}/outputs/{filename}",
            "file_name": filename,
            "mime_type": "audio/wav",
        },
        "local_path": str(output_path),
    }


def process_audio_file(
    input_path: Path,
    effects: list[EffectSpec],
    output_dir: Path,
    public_base_url: str,
    output_name: str | None = None,
) -> dict[str, Any]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input audio file does not exist: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    with AudioFile(str(input_path)) as audio_file:
        audio = audio_file.read(audio_file.frames)
        sample_rate = int(audio_file.samplerate)

    board = build_pedalboard(effects)
    processed = _finalize(board(audio, sample_rate))
    filename = _safe_stem(output_name or f"{input_path.stem}-pedalboard") + ".wav"
    output_path = output_dir / filename
    with AudioFile(str(output_path), "w", sample_rate, processed.shape[0]) as output_file:
        output_file.write(processed)

    return {
        "input_path": str(input_path),
        "effects": [_effect_summary(effect) for effect in effects],
        "duration_seconds": round(processed.shape[1] / sample_rate, 3),
        "sample_rate": sample_rate,
        "channels": int(processed.shape[0]),
        "file": {
            "download_url": f"{public_base_url.rstrip('/')}/outputs/{filename}",
            "file_name": filename,
            "mime_type": "audio/wav",
        },
        "local_path": str(output_path),
    }


def build_pedalboard(effects: list[EffectSpec]) -> Pedalboard:
    plugins = []
    constructors = {
        "Gain": Gain,
        "Compressor": Compressor,
        "Chorus": Chorus,
        "Delay": Delay,
        "Distortion": Distortion,
        "Reverb": Reverb,
        "Limiter": Limiter,
        "HighpassFilter": HighpassFilter,
        "LowpassFilter": LowpassFilter,
        "Phaser": Phaser,
        "PitchShift": PitchShift,
        "Bitcrush": Bitcrush,
        "Clipping": Clipping,
        "NoiseGate": NoiseGate,
    }
    for effect in effects:
        if effect.type not in constructors:
            raise ValueError(f"Unsupported effect type: {effect.type}")
        defaults = EFFECTS[effect.type].defaults
        params = {**defaults, **effect.params}
        plugins.append(constructors[effect.type](**params))
    return Pedalboard(plugins)


def _render_track(track: TrackSpec, bpm: float, duration_seconds: float) -> np.ndarray:
    total = int(duration_seconds * SAMPLE_RATE)
    audio = np.zeros(total, dtype=np.float32)
    beat_seconds = 60 / bpm
    notes = track.notes or [
        NoteSpec(midi=48, start_beat=0, beats=1),
        NoteSpec(midi=55, start_beat=1, beats=1),
        NoteSpec(midi=58, start_beat=2, beats=1),
        NoteSpec(midi=60, start_beat=3, beats=1),
    ]

    for note in notes:
        start = min(total, int(note.start_beat * beat_seconds * SAMPLE_RATE))
        length = min(total - start, int(note.beats * beat_seconds * SAMPLE_RATE))
        if length <= 0:
            continue
        t = np.arange(length, dtype=np.float32) / SAMPLE_RATE
        freq = 440 * (2 ** ((note.midi - 69) / 12))
        tone = _waveform(track.waveform, t, freq)
        envelope = _adsr(length)
        audio[start : start + length] += tone * envelope * note.velocity
    return audio


def _waveform(waveform: str, t: np.ndarray, freq: float) -> np.ndarray:
    phase = 2 * np.pi * freq * t
    if waveform == "square":
        return np.sign(np.sin(phase)).astype(np.float32) * 0.55
    if waveform == "saw":
        return (2 * ((freq * t) % 1) - 1).astype(np.float32) * 0.45
    if waveform == "triangle":
        return (2 * np.abs(2 * ((freq * t) % 1) - 1) - 1).astype(np.float32) * 0.55
    if waveform == "noise":
        rng = np.random.default_rng(int(freq * 1000) % 65535)
        return rng.uniform(-0.45, 0.45, size=t.shape).astype(np.float32)
    return np.sin(phase).astype(np.float32) * 0.7


def _adsr(length: int) -> np.ndarray:
    attack = max(1, int(length * 0.04))
    decay = max(1, int(length * 0.08))
    release = max(1, int(length * 0.18))
    sustain = max(0, length - attack - decay - release)
    return np.concatenate(
        [
            np.linspace(0, 1, attack, dtype=np.float32),
            np.linspace(1, 0.72, decay, dtype=np.float32),
            np.full(sustain, 0.72, dtype=np.float32),
            np.linspace(0.72, 0, release, dtype=np.float32),
        ]
    )[:length]


def _default_tracks(spec: CompositionSpec) -> list[TrackSpec]:
    seed = int(hashlib.sha1(spec.prompt.encode("utf-8")).hexdigest()[:8] or "0", 16)
    root = spec.key_midi + (seed % 12)
    scale = [0, 2, 3, 5, 7, 10]
    beats = max(8, int(spec.duration_seconds * spec.bpm / 60))

    bass_notes = [
        NoteSpec(midi=root + scale[(i // 2) % len(scale)] - 12, start_beat=i, beats=0.85, velocity=0.75)
        for i in range(0, beats, 2)
    ]
    lead_notes = [
        NoteSpec(midi=root + 12 + scale[(i + seed) % len(scale)], start_beat=i * 0.5, beats=0.4, velocity=0.45)
        for i in range(beats * 2)
    ]
    pad_notes = [
        NoteSpec(midi=root + interval, start_beat=0, beats=min(beats, 16), velocity=0.35)
        for interval in (0, 7, 10)
    ]
    return [
        TrackSpec(name="Bass", waveform="triangle", gain_db=-9, pan=-0.15, notes=bass_notes),
        TrackSpec(name="Lead", waveform="saw", gain_db=-15, pan=0.2, notes=lead_notes),
        TrackSpec(name="Pad", waveform="sine", gain_db=-18, pan=0, notes=pad_notes),
    ]


def _default_effects(prompt: str) -> list[EffectSpec]:
    prompt_lower = prompt.lower()
    effects = [EffectSpec(type="Compressor", params={"threshold_db": -18, "ratio": 2.5})]
    if any(word in prompt_lower for word in ("grit", "distort", "rock", "industrial")):
        effects.append(EffectSpec(type="Distortion", params={"drive_db": 9}))
    if any(word in prompt_lower for word in ("wide", "dream", "lush", "chorus")):
        effects.append(EffectSpec(type="Chorus", params={"depth": 0.35, "mix": 0.3}))
    effects.extend(
        [
            EffectSpec(type="Delay", params={"delay_seconds": 0.22, "feedback": 0.18, "mix": 0.22}),
            EffectSpec(type="Reverb", params={"room_size": 0.42, "wet_level": 0.22, "dry_level": 0.82}),
            EffectSpec(type="Limiter", params={"threshold_db": -1.0}),
        ]
    )
    return effects


def _finalize(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 1:
        audio = np.expand_dims(audio, axis=0)
    audio = np.nan_to_num(audio).astype(np.float32)
    peak = float(np.max(np.abs(audio))) or 1.0
    if peak > 0.98:
        audio *= 0.98 / peak
    return audio


def _track_summary(track: TrackSpec) -> dict[str, Any]:
    return {
        "name": track.name,
        "waveform": track.waveform,
        "gain_db": track.gain_db,
        "pan": track.pan,
        "note_count": len(track.notes),
    }


def _effect_summary(effect: EffectSpec) -> dict[str, Any]:
    return {"type": effect.type, "params": {**EFFECTS[effect.type].defaults, **effect.params}}


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return stem[:64] or "pedalboard-render"
