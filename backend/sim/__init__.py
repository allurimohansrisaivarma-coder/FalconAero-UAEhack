"""FalconAero Simulator: an independent event source for the engine.

It talks to the engine only through an `EngineClient` (in-process or HTTP) that
exposes exactly what a real deployment would: event ingestion and read access
to the published (approved) plan that aircraft and ground handlers follow.
"""
