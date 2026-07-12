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


@mcp.prompt()
def start_a_track(genre: str = "lofi") -> str:
    """Guided workflow: scaffold a genre starter fast, then build on it."""
    return f"""Start a {genre} track in the connected Ableton Live set.

1. list_recipes, then apply_recipe (e.g. "lofi_beat" or "house_groove") to lay down
   tempo, Drums/Bass/Chords tracks, and generated parts in one call.
2. detect_session_key to confirm the key, then generate more parts in it:
   generate_melody / generate_walking_bass / generate_voiced_progression (pass the
   detected key and a progression), or generate_genre_progression for the genre.
3. Add rhythmic variation with generate_euclidean_drums, and humanize_clip for feel.
4. Audition with fire_clip; iterate the generators (they take a seed for repeatable
   results) before arranging."""


@mcp.prompt()
def sound_design(target: str = "warm lofi Rhodes") -> str:
    """Guided workflow: shape an instrument and its effect chain."""
    return f"""Design a "{target}" sound in the connected Ableton Live set.

1. search_browser for a fitting instrument and load_instrument_or_effect onto a
   MIDI track; play a test clip to hear it.
2. Shape the tone with effects: search_browser + load for EQ Eight, Saturator,
   Auto Filter, Reverb (put Reverb on a return via load_device_to_return).
3. Before setting any parameter, call describe_device for guidance (note: `curve`
   units are perceptual, so read the display value after each move), then
   get_device_parameters for the live indices/ranges, then set_device_parameter.
4. For sample-based sounds, load a sample into Simpler and use
   set_simpler_playback_mode (2 = Slicing) to chop it.
5. For rack sound-design, use rack_variation ("randomize") to explore, then "store"
   the ones you like."""


@mcp.prompt()
def analyze_and_improve() -> str:
    """Guided workflow: use the read-back tools to judge and improve the set."""
    return """Review and improve the current Ableton Live set using the analysis tools:

1. get_session_snapshot for a one-call overview; analyze_mix to flag likely problems
   (no headroom, muted/empty tracks, silent MIDI tracks).
2. detect_session_key to check tonal consistency; fix any out-of-key parts.
3. To actually HEAR a section, record_section(start_beat, end_beat) bounces it to a
   WAV without the export dialog; analyze that file for loudness/spectral balance.
4. Make fixes (set_track_volume, add EQ/limiter, edit_notes), calling session_diff
   before and after so you can confirm each change took effect.
5. For ducking, set_device_routing to sidechain a compressor to the kick, then
   enable its Sidechain parameter."""
