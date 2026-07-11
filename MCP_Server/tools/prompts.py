"""MCP prompts: guided workflows that encode the server's conventions."""

from ..app import mcp


@mcp.prompt()
def make_a_beat(genre: str = "lofi hip hop", bpm: str = "80") -> str:
    """Guided workflow: compose a beat from scratch in the current Live set."""
    return f"""Compose a {genre} beat at {bpm} BPM in the connected Ableton Live set.

Follow this workflow:
1. get_session_info to see the current state; set_tempo to {bpm}.
2. Create tracks with create_midi_track and name them (drums, bass, chords, ...).
3. Find sounds with search_browser (e.g. "drum kit", "bass", "piano"), then
   load_instrument_or_effect with the returned URIs.
4. For each part: create_clip, then add_notes_to_clip (pitch 0-127 where 60=C3,
   times in beats, velocity 1-127; vary velocities +-6 and delay offbeats
   ~0.05-0.09 beats for human feel, or apply set_clip_groove).
5. Drum map for Live kits: 36 kick, 38 snare, 37 rim, 42 closed hat, 46 open hat.
6. Use batch_commands for repetitive steps (one round-trip, one undo).
7. fire_clip to audition; iterate on velocities/notes before arranging.
8. Arrange with duplicate_to_arrangement (it overwrites the destination range),
   then start_playback to review; confirm state via get_session_info."""


@mcp.prompt()
def mix_and_master() -> str:
    """Guided workflow: balance, bus processing, and in-Live mastering."""
    return """Mix and master the current Ableton Live set:

1. get_session_info + get_track_info for every track (volumes, sends, devices).
2. Balance: set_track_volume (0.85 = 0 dB unity; keep peaks headroom by staying
   below unity on busy tracks), set_track_pan for width.
3. Shared space: create_return_track if needed, load_device_to_return (e.g.
   query:AudioFx#Reverb), then set_send per track (0.0-1.0).
4. Character: load devices on tracks (search_browser for EQ Eight, Compressor,
   Saturator), then get_device_parameters BEFORE set_device_parameter - values
   use each parameter's native range; read min/max/display first.
5. Master: load_device_to_master (Limiter last in chain),
   get_master_device_parameters, set ceiling ~-1 dB equivalent (read display).
6. A/B: set_device_enabled to bypass/compare. undo reverts mistakes."""
