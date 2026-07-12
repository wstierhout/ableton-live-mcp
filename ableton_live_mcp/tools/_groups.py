"""Single source of truth for the toolset groups (ABLETON_TOOLSETS).

A group is a *domain*, which may span several tool modules (e.g. the `generators`
domain covers the basic, advanced, and motif generators). Both the module loader
(`tools/__init__.py`) and `describe_capabilities` (`analysis.py`) read from here,
so adding a module means editing one place and the two stay in sync.
"""

# group -> the tool modules that implement it.
GROUP_MODULES = {
    "session": ["session"],
    "tracks": ["tracks"],
    "clips": ["clips"],
    "devices": ["devices", "device_kb"],
    "browser": ["browser"],
    "arrangement": ["arrangement"],
    "generators": ["generators", "generators_advanced", "motif"],
    "audio": ["audio"],
    "analysis": ["analysis", "keys"],
    "offline": ["offline", "offline_racks"],
    "recipes": ["recipes"],
    "prompts": ["prompts"],
}

# group -> one-line summary for agent orientation (describe_capabilities).
GROUP_DESCRIPTIONS = {
    "session": "transport, tempo, tap tempo, groove/swing, scenes, locators, record modes, song scale and tuning, Ableton Link, one-call snapshot",
    "tracks": "create/delete MIDI/audio/return tracks, delete devices, take lanes, group fold, volume/pan/mute/solo/arm/sends, routing, meters, crossfader",
    "clips": "create clips, write/edit MIDI notes (probability), quantize with strength, groove, loop, warp, pitch/gain",
    "devices": "browse/search and load devices onto any track incl Master and Returns, read/set any param, sidechain routing, rack macro variations, Simpler slicing, per-pad drum control, device knowledge base",
    "browser": "navigate the browser, load, and preview samples and presets",
    "arrangement": "place/read/delete arrangement clips, write clip automation",
    "generators": "drum patterns, euclidean rhythms, chord progressions, jazz voicings, voice-leading melodies, walking bass, genre progressions, humanize, motif transforms, minimalist processes, session setup",
    "audio": "record a section to a WAV without the export dialog",
    "analysis": "mix heuristics, key/scale detection, session diff, and a toolset map",
    "offline": "parse, diff, lint, and detect key in saved .als files, and parse .adg/.adv racks, all with Live closed",
    "recipes": "scaffold a genre starter (lofi, house) in one call",
}
