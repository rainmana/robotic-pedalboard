from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .engine import CompositionSpec, EffectSpec, NoteSpec, TrackSpec


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class SectionSpec(BaseModel):
    name: str
    start_bar: int = Field(ge=1)
    bars: int = Field(ge=1)


class ProjectTrackSpec(BaseModel):
    track_id: str = Field(default_factory=lambda: new_id("track"))
    name: str = "Track"
    type: Literal["instrument", "audio"] = "instrument"
    waveform: Literal["sine", "square", "saw", "triangle", "noise"] = "sine"
    gain_db: float = Field(default=-10, ge=-60, le=12)
    pan: float = Field(default=0, ge=-1, le=1)
    muted: bool = False
    effects: list[EffectSpec] = Field(default_factory=list)
    clips: list[str] = Field(default_factory=list)


class ProjectClipSpec(BaseModel):
    clip_id: str = Field(default_factory=lambda: new_id("clip"))
    track_id: str
    name: str = "Clip"
    start_bar: int = Field(default=1, ge=1)
    length_bars: int = Field(default=1, ge=1)
    notes: list[NoteSpec] = Field(default_factory=list)


class RevisionSpec(BaseModel):
    revision_id: str = Field(default_factory=lambda: new_id("rev"))
    parent_revision_id: str | None = None
    operation: str
    changed_objects: list[str] = Field(default_factory=list)
    description: str = ""
    render_url: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ProjectSpec(BaseModel):
    project_id: str = Field(default_factory=lambda: new_id("project"))
    title: str = "Untitled"
    prompt: str = ""
    bpm: float = Field(default=92, ge=40, le=220)
    duration_seconds: float = Field(default=12, gt=0.25, le=300)
    key_midi: int = Field(default=48, ge=24, le=72)
    time_signature: str = "4/4"
    key: str | None = None
    sections: list[SectionSpec] = Field(default_factory=list)
    tracks: list[ProjectTrackSpec] = Field(default_factory=list)
    clips: list[ProjectClipSpec] = Field(default_factory=list)
    master_effects: list[EffectSpec] = Field(default_factory=list)
    revisions: list[RevisionSpec] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CreateProjectRequest(BaseModel):
    title: str = "Untitled"
    prompt: str = ""
    bpm: float = Field(default=92, ge=40, le=220)
    duration_seconds: float = Field(default=12, gt=0.25, le=300)
    key_midi: int = Field(default=48, ge=24, le=72)
    time_signature: str = "4/4"
    key: str | None = None
    sections: list[SectionSpec] = Field(default_factory=list)
    tracks: list[ProjectTrackSpec] = Field(default_factory=list)
    clips: list[ProjectClipSpec] = Field(default_factory=list)
    master_effects: list[EffectSpec] = Field(default_factory=list)


class UpdateProjectRequest(BaseModel):
    project_id: str
    title: str | None = None
    prompt: str | None = None
    bpm: float | None = Field(default=None, ge=40, le=220)
    duration_seconds: float | None = Field(default=None, gt=0.25, le=300)
    key_midi: int | None = Field(default=None, ge=24, le=72)
    time_signature: str | None = None
    key: str | None = None
    sections: list[SectionSpec] | None = None
    master_effects: list[EffectSpec] | None = None
    description: str = "Updated project metadata."


class RenderProjectRequest(BaseModel):
    project_id: str


class RenderRegionRequest(BaseModel):
    project_id: str
    start_bar: int = Field(ge=1)
    end_bar: int = Field(ge=1)


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, request: CreateProjectRequest) -> ProjectSpec:
        project = ProjectSpec(
            title=request.title,
            prompt=request.prompt,
            bpm=request.bpm,
            duration_seconds=request.duration_seconds,
            key_midi=request.key_midi,
            time_signature=request.time_signature,
            key=request.key,
            sections=request.sections,
            tracks=request.tracks,
            clips=request.clips,
            master_effects=request.master_effects,
        )
        self._sync_track_clips(project)
        project.revisions.append(
            RevisionSpec(
                operation="create_project",
                changed_objects=[project.project_id],
                description=f"Created project {project.title}.",
            )
        )
        self._save(project)
        return project

    def get(self, project_id: str) -> ProjectSpec:
        path = self._path(project_id)
        if not path.exists():
            raise FileNotFoundError(f"Project does not exist: {project_id}")
        return ProjectSpec.model_validate_json(path.read_text(encoding="utf-8"))

    def update(self, request: UpdateProjectRequest) -> ProjectSpec:
        project = self.get(request.project_id)
        for field in (
            "title",
            "prompt",
            "bpm",
            "duration_seconds",
            "key_midi",
            "time_signature",
            "key",
            "sections",
            "master_effects",
        ):
            value = getattr(request, field)
            if value is not None:
                setattr(project, field, value)
        self._touch(project, "update_project", [project.project_id], request.description)
        return project

    def add_track(self, project_id: str, track: ProjectTrackSpec) -> ProjectSpec:
        project = self.get(project_id)
        if any(existing.track_id == track.track_id for existing in project.tracks):
            raise ValueError(f"Track already exists: {track.track_id}")
        project.tracks.append(track)
        self._touch(project, "add_track", [track.track_id], f"Added track {track.name}.")
        return project

    def update_track(self, project_id: str, track: ProjectTrackSpec) -> ProjectSpec:
        project = self.get(project_id)
        for index, existing in enumerate(project.tracks):
            if existing.track_id == track.track_id:
                project.tracks[index] = track
                self._touch(project, "update_track", [track.track_id], f"Updated track {track.name}.")
                return project
        raise FileNotFoundError(f"Track does not exist: {track.track_id}")

    def add_clip(self, project_id: str, clip: ProjectClipSpec) -> ProjectSpec:
        project = self.get(project_id)
        track = self._find_track(project, clip.track_id)
        if any(existing.clip_id == clip.clip_id for existing in project.clips):
            raise ValueError(f"Clip already exists: {clip.clip_id}")
        project.clips.append(clip)
        if clip.clip_id not in track.clips:
            track.clips.append(clip.clip_id)
        self._touch(project, "add_clip", [clip.clip_id, clip.track_id], f"Added clip {clip.name}.")
        return project

    def update_clip(self, project_id: str, clip: ProjectClipSpec) -> ProjectSpec:
        project = self.get(project_id)
        self._find_track(project, clip.track_id)
        for index, existing in enumerate(project.clips):
            if existing.clip_id == clip.clip_id:
                project.clips[index] = clip
                self._sync_track_clips(project)
                self._touch(project, "update_clip", [clip.clip_id], f"Updated clip {clip.name}.")
                return project
        raise FileNotFoundError(f"Clip does not exist: {clip.clip_id}")

    def attach_render(self, project_id: str, render_url: str, operation: str) -> ProjectSpec:
        project = self.get(project_id)
        parent = project.revisions[-1].revision_id if project.revisions else None
        project.revisions.append(
            RevisionSpec(
                parent_revision_id=parent,
                operation=operation,
                changed_objects=[project_id],
                render_url=render_url,
                description=f"Rendered {project.title}.",
            )
        )
        project.updated_at = datetime.now(UTC).isoformat()
        self._save(project)
        return project

    def to_composition(
        self,
        project: ProjectSpec,
        start_bar: int | None = None,
        end_bar: int | None = None,
    ) -> CompositionSpec:
        region_start_beat = ((start_bar or 1) - 1) * 4
        region_end_beat = (end_bar * 4) if end_bar else None
        region_beats = (region_end_beat - region_start_beat) if region_end_beat else None
        duration_seconds = (
            (region_beats * 60 / project.bpm)
            if region_beats is not None
            else project.duration_seconds
        )
        tracks = []
        clip_lookup = {clip.clip_id: clip for clip in project.clips}

        for project_track in project.tracks:
            if project_track.muted:
                continue
            notes: list[NoteSpec] = []
            for clip_id in project_track.clips:
                clip = clip_lookup.get(clip_id)
                if clip is None:
                    continue
                clip_start_beat = (clip.start_bar - 1) * 4
                for note in clip.notes:
                    absolute_start = clip_start_beat + note.start_beat
                    absolute_end = absolute_start + note.beats
                    if region_end_beat is not None and (
                        absolute_end <= region_start_beat or absolute_start >= region_end_beat
                    ):
                        continue
                    shifted_start = max(0, absolute_start - region_start_beat)
                    clipped_end = absolute_end
                    if region_end_beat is not None:
                        clipped_end = min(clipped_end, region_end_beat)
                    clipped_beats = max(0.01, clipped_end - max(absolute_start, region_start_beat))
                    notes.append(
                        NoteSpec(
                            midi=note.midi,
                            start_beat=shifted_start,
                            beats=clipped_beats,
                            velocity=note.velocity,
                        )
                    )
            tracks.append(
                TrackSpec(
                    name=project_track.name,
                    waveform=project_track.waveform,
                    gain_db=project_track.gain_db,
                    pan=project_track.pan,
                    notes=notes,
                )
            )

        if not tracks:
            tracks.append(
                TrackSpec(
                    name="Silence",
                    notes=[NoteSpec(midi=60, start_beat=0, beats=0.01, velocity=0)],
                )
            )

        return CompositionSpec(
            title=project.title if start_bar is None else f"{project.title} bars {start_bar}-{end_bar}",
            prompt=project.prompt,
            bpm=project.bpm,
            duration_seconds=min(duration_seconds, 45),
            key_midi=project.key_midi,
            tracks=tracks,
            effects=project.master_effects,
        )

    def stem_compositions(self, project: ProjectSpec) -> list[tuple[str, CompositionSpec]]:
        stems = []
        for track in project.tracks:
            if track.muted:
                continue
            stem_project = project.model_copy(deep=True)
            stem_project.title = f"{project.title} - {track.name}"
            stem_project.tracks = [t for t in stem_project.tracks if t.track_id == track.track_id]
            stem_project.clips = [clip for clip in stem_project.clips if clip.track_id == track.track_id]
            stems.append((track.track_id, self.to_composition(stem_project)))
        return stems

    def _touch(
        self,
        project: ProjectSpec,
        operation: str,
        changed_objects: list[str],
        description: str,
    ) -> None:
        parent = project.revisions[-1].revision_id if project.revisions else None
        project.revisions.append(
            RevisionSpec(
                parent_revision_id=parent,
                operation=operation,
                changed_objects=changed_objects,
                description=description,
            )
        )
        project.updated_at = datetime.now(UTC).isoformat()
        self._save(project)

    def _save(self, project: ProjectSpec) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._path(project.project_id).write_text(
            json.dumps(project.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _path(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", project_id):
            raise ValueError(f"Invalid project_id: {project_id}")
        return self.root / f"{project_id}.json"

    def _find_track(self, project: ProjectSpec, track_id: str) -> ProjectTrackSpec:
        for track in project.tracks:
            if track.track_id == track_id:
                return track
        raise FileNotFoundError(f"Track does not exist: {track_id}")

    def _sync_track_clips(self, project: ProjectSpec) -> None:
        clip_ids_by_track: dict[str, list[str]] = {track.track_id: [] for track in project.tracks}
        for clip in project.clips:
            clip_ids_by_track.setdefault(clip.track_id, []).append(clip.clip_id)
        for track in project.tracks:
            track.clips = clip_ids_by_track.get(track.track_id, [])
