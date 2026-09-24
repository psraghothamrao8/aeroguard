"""
AeroGuard - Configuration Settings
Validated Pydantic v2 settings loading environment variables, AssemblyAI endpoints,
and airfield lexicon optimization parameters.
"""

from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # AssemblyAI Configuration
    assemblyai_api_key: str = Field(
        default="mock_assemblyai_dev_key",
        description="AssemblyAI API Key. Handshake header: 'Authorization: <KEY>' (Strictly NO 'Bearer')"
    )
    assemblyai_streaming_url: str = Field(
        default="wss://streaming.assemblyai.com/v3/ws",
        description="AssemblyAI Streaming v3 WebSocket URL"
    )
    assemblyai_llm_gateway_url: str = Field(
        default="https://llm-gateway.assemblyai.com/v1/chat/completions",
        description="AssemblyAI LLM Gateway endpoint for Part 139 digital audit extraction"
    )
    llm_model: str = Field(
        default="claude-3-5-sonnet",
        description="LLM Model for FAA Part 139 post-incident structured report"
    )

    # Audio & Streaming Parameters
    sample_rate: int = Field(default=16000, description="PCM Sample Rate in Hz (Standard 16000)")
    audio_encoding: str = Field(default="pcm_s16le", description="Audio encoding: pcm_s16le")
    min_turn_silence: int = Field(default=400, description="Turn detection silence threshold (ms)")
    max_turn_silence: int = Field(default=1200, description="Maximum silence before turn is ended (ms)")
    format_turns: bool = Field(default=True, description="Enable automatic formatting and punctuation")

    # Airfield & Server Settings
    airport_icao: str = Field(default="KORD", description="Airport identifier (Default: Chicago O'Hare KORD)")
    host: str = Field(default="0.0.0.0", description="Bind host")
    port: int = Field(default=8000, description="Bind port")
    debug: bool = Field(default=True, description="Debug mode")

    # AssemblyAI Keyterms Lexicon Optimization
    # Critical Technical Requirement: Keyterms prompt boosting ICAO phonetic alphabet & runway designations
    keyterms: List[str] = Field(
        default=[
            "Runway 09L", "Runway 27R", "Runway 04R", "Runway 22L", "Runway 10C",
            "Taxiway Alpha", "Taxiway Bravo", "Taxiway Charlie", "Taxiway Delta", "Taxiway Echo",
            "Hold Short", "Hold Short of Runway", "Cleared to Cross", "Cross Runway 09L",
            "Pushback approved", "Pushback and start", "Line Up and Wait",
            "Tug 42", "Tug Forty-Two", "Tug 18", "Tug Eighteen", "Ground Tug",
            "Boeing 777", "Boeing 737", "Airbus A321", "Flight AA104", "United 220",
            "Ground Control", "Tower", "Ramp Control", "Wilco", "Roger",
            "Readback Correct", "Negative", "Immediate Stop", "Runway Incursion",
            "Gate B12", "Gate C10", "Concourse B", "Concourse C", "Heavy"
        ],
        description="Up to 100 domain-specific terms passed to AssemblyAI streaming session"
    )


settings = Settings()
