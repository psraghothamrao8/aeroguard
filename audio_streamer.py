"""
AeroGuard - Audio Streamer
Asynchronous WebSocket pipeline bridging browser audio to AssemblyAI Streaming STT (v3 API).
Implements strict technical contract:
1. Endpoint: wss://streaming.assemblyai.com/v3/ws?sample_rate=16000&format_turns=true
2. Header: Authorization: <API_KEY> (Strictly NO 'Bearer' prefix)
3. Lexicon Optimization: keyterms_prompt with ICAO phonetics & airfield identifiers
4. Resilient handling of partial/final Turn events and barge-in buffer clearing
"""

import asyncio
import json
import logging
import urllib.parse
from typing import AsyncGenerator, Callable, Dict, List, Optional
import time
import websockets
from websockets.client import WebSocketClientProtocol

from config import settings

logger = logging.getLogger("aeroguard.audio_streamer")


class TranscriptTurn:
    def __init__(
        self,
        transcript: str,
        end_of_turn: bool = False,
        speaker: Optional[str] = "RAMP_OPERATOR",
        confidence: float = 1.0,
        words: Optional[List[dict]] = None,
        source: str = "ASSEMBLYAI_STREAMING"
    ):
        self.transcript = transcript
        self.end_of_turn = end_of_turn
        self.speaker = speaker
        self.confidence = confidence
        self.words = words or []
        self.source = source
        self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            "transcript": self.transcript,
            "end_of_turn": self.end_of_turn,
            "speaker": self.speaker,
            "confidence": self.confidence,
            "source": self.source,
            "timestamp": self.timestamp
        }


class AssemblyAIAudioStreamer:
    """
    Manages bidirectional streaming audio bridge between client WebSocket and AssemblyAI Streaming STT.
    """

    def __init__(self, on_turn_callback: Optional[Callable[[TranscriptTurn], None]] = None):
        self.on_turn_callback = on_turn_callback
        self.aai_ws: Optional[WebSocketClientProtocol] = None
        self.is_running = False
        self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.receive_task: Optional[asyncio.Task] = None
        self.send_task: Optional[asyncio.Task] = None
        self.is_simulated = False
        self.api_key = settings.assemblyai_api_key

    def _build_ws_url(self) -> str:
        """
        Builds AssemblyAI v3 streaming URL with sample_rate, format_turns,
        and JSON-serialized keyterms_prompt for phonetic lexicon boosting.
        """
        params = {
            "sample_rate": settings.sample_rate,
            "format_turns": "true",
            "keyterms_prompt": json.dumps(settings.keyterms)
        }
        query_string = urllib.parse.urlencode(params)
        return f"{settings.assemblyai_streaming_url}?{query_string}"

    async def connect(self) -> bool:
        """
        Establishes connection to AssemblyAI v3 Streaming WebSocket.
        Strict Contract: Authorization header contains raw API key without 'Bearer'.
        """
        # If API key is mock or unset, enable simulation mode
        if not self.api_key or "mock" in self.api_key.lower():
            logger.info("Using AeroGuard Autonomous Voice Engine in local simulated mode (Mock/Dev Key detected).")
            self.is_simulated = True
            self.is_running = True
            return True

        url = self._build_ws_url()
        # Strict AssemblyAI Technical Contract:
        # Header: 'Authorization: <ASSEMBLYAI_API_KEY>' (Strictly NO 'Bearer' prefix)
        headers = {
            "Authorization": self.api_key
        }

        try:
            logger.info(f"Connecting to AssemblyAI Streaming WebSocket: {url[:60]}... with custom keyterms.")
            self.aai_ws = await websockets.connect(
                url,
                extra_headers=headers,
                ping_interval=10,
                ping_timeout=20
            )
            self.is_running = True
            self.receive_task = asyncio.create_task(self._receive_loop())
            self.send_task = asyncio.create_task(self._send_loop())
            logger.info("Connected to AssemblyAI Streaming STT v3.")
            return True
        except Exception as e:
            logger.warning(f"AssemblyAI Live WebSocket connection failed ({e}). Falling back to Autonomous Simulation Engine.")
            self.is_simulated = True
            self.is_running = True
            return True

    async def push_audio_chunk(self, chunk: bytes):
        """Pushes raw PCM 16kHz audio chunk into the streaming pipeline."""
        if not self.is_running:
            return
        await self.audio_queue.put(chunk)

    async def flush_buffer(self):
        """
        Barge-in Buffer Flushing:
        Clears pending audio queue immediately when an incursion alert triggers,
        ensuring audio playback and synthetic alerts have zero latency.
        """
        cleared_chunks = 0
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
                cleared_chunks += 1
            except asyncio.QueueEmpty:
                break
        logger.info(f"Barge-in buffer flushed: cleared {cleared_chunks} pending audio frames.")

    async def _send_loop(self):
        """Forwards binary PCM chunks to AssemblyAI Streaming WebSocket."""
        try:
            while self.is_running and self.aai_ws:
                chunk = await self.audio_queue.get()
                try:
                    await self.aai_ws.send(chunk)
                finally:
                    self.audio_queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in AssemblyAI send loop: {e}")

    async def _receive_loop(self):
        """
        Receives messages from AssemblyAI Streaming WebSocket and extracts partial/final turns.
        """
        try:
            while self.is_running and self.aai_ws:
                msg = await self.aai_ws.recv()
                if isinstance(msg, str):
                    data = json.loads(msg)
                    turn = self._parse_assemblyai_message(data)
                    if turn and self.on_turn_callback:
                        if asyncio.iscoroutinefunction(self.on_turn_callback):
                            await self.on_turn_callback(turn)
                        else:
                            self.on_turn_callback(turn)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in AssemblyAI receive loop: {e}")

    def _parse_assemblyai_message(self, data: dict) -> Optional[TranscriptTurn]:
        """
        Parses AssemblyAI v3 / v2 responses into a unified TranscriptTurn object.
        Supports:
        - v3: message_type == 'Turn' with 'transcript' and 'end_of_turn'
        - v2: message_type == 'PartialTranscript' or 'FinalTranscript'
        """
        msg_type = data.get("message_type") or data.get("type")

        # AssemblyAI v3 Turn schema
        if msg_type == "Turn":
            transcript = data.get("transcript", "").strip()
            end_of_turn = bool(data.get("end_of_turn", False))
            words = data.get("words", [])
            if transcript:
                return TranscriptTurn(
                    transcript=transcript,
                    end_of_turn=end_of_turn,
                    words=words,
                    source="AssemblyAI_v3_Streaming"
                )

        # Legacy v2 schema compatibility
        elif msg_type == "PartialTranscript":
            text = data.get("text", "").strip()
            if text:
                return TranscriptTurn(
                    transcript=text,
                    end_of_turn=False,
                    source="AssemblyAI_v2_Partial"
                )
        elif msg_type == "FinalTranscript":
            text = data.get("text", "").strip()
            if text:
                return TranscriptTurn(
                    transcript=text,
                    end_of_turn=True,
                    confidence=data.get("confidence", 1.0),
                    source="AssemblyAI_v2_Final"
                )

        # Connection / Session lifecycle messages
        elif msg_type in ["SessionBegins", "SpeechStarted", "SessionTerminated"]:
            logger.debug(f"AssemblyAI Streaming lifecycle event: {msg_type}")

        return None

    async def update_keyterms(self, new_keyterms: List[str]):
        """Dynamically updates keyterms prompt mid-stream."""
        if self.aai_ws and not self.is_simulated:
            update_msg = {
                "type": "UpdateConfiguration",
                "keyterms_prompt": new_keyterms
            }
            await self.aai_ws.send(json.dumps(update_msg))
            logger.info(f"Updated AssemblyAI keyterms mid-stream ({len(new_keyterms)} terms).")

    async def close(self):
        """Closes all background streaming tasks and WebSocket connection."""
        self.is_running = False
        if self.receive_task:
            self.receive_task.cancel()
        if self.send_task:
            self.send_task.cancel()
        if self.aai_ws:
            try:
                # AssemblyAI streaming session termination signal
                await self.aai_ws.send(json.dumps({"type": "Terminate"}))
                await self.aai_ws.close()
            except Exception:
                pass
        logger.info("AssemblyAI streaming bridge closed.")
