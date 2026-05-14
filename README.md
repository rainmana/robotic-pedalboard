# Robotic Pedalboard

Robotic Pedalboard is an experiment in giving ChatGPT a real music-rendering backend.
The current working version is a ChatGPT Apps SDK / MCP app that lets ChatGPT compose
simple note-based arrangements, synthesize them, run them through Spotify's
[`pedalboard`](https://github.com/spotify/pedalboard) audio effects engine, and return
downloadable WAV files directly in a normal ChatGPT conversation.

The short version:

```text
ChatGPT prompt -> structured composition -> synth tracks -> Pedalboard effects -> WAV URL
```

## Working Release

The first known-good release is tagged:

```text
v0.1.0-working
```

That release was verified through a public ngrok HTTPS tunnel from ChatGPT. It can:

- expose MCP tools to regular ChatGPT
- list available Pedalboard effects
- render original note-based compositions
- render dense explicit note arrays
- apply global Pedalboard effect chains
- return valid stereo 44.1 kHz WAV output
- serve generated audio through public HTTPS URLs
- report useful schema validation errors when tool input is malformed

The working release is intentionally tool-first. ChatGPT does not need a human-facing UI
to use it. An optional widget exists, but the main product surface is the regular
ChatGPT conversation.

## What Exists Today

The app lives in:

```text
chatgpt_pedalboard_app/
```

It exposes these tools:

```text
inspect_pedalboard_effects
open_pedalboard_studio
render_pedalboard_composition
process_audio_with_pedalboard
```

The renderer supports:

- beat-based note timing
- MIDI pitch numbers
- per-note velocity
- track gain and pan
- simple synthetic waveforms: `sine`, `square`, `saw`, `triangle`, `noise`
- global/master effects
- WAV output serving

Currently exposed effects:

```text
Gain
Compressor
Chorus
Delay
Distortion
Reverb
Limiter
HighpassFilter
LowpassFilter
Phaser
PitchShift
Bitcrush
Clipping
NoiseGate
```

## Why It Exists

Most AI music tools hide the musical state inside an opaque generator. This project goes
the other way: it gives the model a legible symbolic control surface.

ChatGPT can reason about:

- notes
- beats
- tracks
- velocities
- effect chains
- render metadata
- downloadable artifacts
- schema errors

That matters because the goal is not only "generate a WAV." The goal is an AI-native DAW
backend where a model can make targeted, reversible musical edits:

```text
Keep the bass.
Rewrite bars 9-16.
Render only the chorus.
Give me stems.
Go back to version 3 but keep the new drums.
```

The current app proves the core loop works. The next phase is making the music editable.

## Run It Locally

Install dependencies and start the MCP server:

```bash
cd chatgpt_pedalboard_app
uv run python -m robotic_pedalboard_app.app
```

If port `8000` is busy:

```bash
PORT=8017 PUBLIC_BASE_URL=http://localhost:8017 uv run python -m robotic_pedalboard_app.app
```

Health check:

```bash
curl http://127.0.0.1:8017/health
```

See [chatgpt_pedalboard_app/README.md](chatgpt_pedalboard_app/README.md) for the full
local runbook, validation commands, and ChatGPT Developer Mode setup.

## Test In ChatGPT

ChatGPT needs a public HTTPS MCP endpoint. During development, use ngrok:

```bash
ngrok http 8017
```

Then start the app with the public origin:

```bash
cd chatgpt_pedalboard_app
PORT=8017 PUBLIC_BASE_URL=https://YOUR-NGROK-DOMAIN uv run python -m robotic_pedalboard_app.app
```

In ChatGPT:

1. Enable Developer Mode in Settings.
2. Create a new app/connector.
3. Use the MCP URL:

```text
https://YOUR-NGROK-DOMAIN/mcp
```

4. Start a new chat and select `Robotic Pedalboard`.
5. Ask it to render a short piece.

## Current Limits

This is still a renderer, not yet a DAW.

Current missing pieces include:

- persistent project/session state
- durable track IDs
- clip objects
- arrangement sections and markers
- render regions
- render stems
- per-track effects
- sends, returns, and buses
- automation lanes
- MIDI import/export
- instrument registry
- sampled instruments
- curated VST3/AU plugin registry
- audio and MIDI analysis
- revision history
- high-level musical edit operations

## What Comes Next

The next milestone is persistent project state.

The project should evolve from:

```text
composition -> render
```

into:

```text
project -> tracks -> clips -> revisions -> render artifacts
```

Recommended near-term tools:

```text
create_project
get_project
update_project
create_track
update_track
create_clip
update_clip
render_project
render_region
render_stems
export_midi
```

The smallest "AI DAW" milestone:

- persistent project JSON
- track IDs
- clip IDs
- timeline sections
- per-track effects
- render region
- render stems
- MIDI export
- revision history

The larger "holy wow, this is a DAW backend" milestone:

- sends and buses
- nested/parallel routing
- automation lanes
- richer instruments with ADSR
- convolution and EQ filters
- curated VST3/AU registry
- audio/MIDI analysis
- A/B render comparison
- semantic musical operations like transpose, humanize, reharmonize, generate bassline,
  make variation, and rewrite chorus

## Design Principle

Composition state is not audio.

Audio is a render artifact of editable symbolic state.

That separation is what lets ChatGPT become a collaborator instead of a one-shot sample
generator.
