"""WebSocket ASR server for Canary multilingual models.

This server provides Spanish (and other language) ASR support using NVIDIA Canary models.
It uses the same WebSocket protocol as server.py for Pipecat compatibility.

Key differences from server.py (Nemotron-Speech/Parakeet):
- Uses encoder-decoder architecture instead of CTC
- Minimum chunk size is 1 second (vs 160ms for Parakeet)
- Higher latency (~1.5s) but supports multilingual input
- Uses AlignAtt streaming policy for lowest achievable latency

Supported models:
- nvidia/canary-1b-flash: 4 languages (en, de, es, fr), lower latency
- nvidia/canary-1b-v2: 25 languages, higher accuracy
"""

import asyncio
import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Optional, List

import numpy as np
import torch
from aiohttp import web, WSMsgType
from loguru import logger

# Enable debug logging with DEBUG_ASR=1
DEBUG_ASR = os.environ.get("DEBUG_ASR", "0") == "1"


def _hash_audio(audio: np.ndarray) -> str:
    """Get short hash of audio array for debugging."""
    if audio is None or len(audio) == 0:
        return "empty"
    return hashlib.md5(audio.tobytes()).hexdigest()[:8]


# Default model - canary-1b-flash for lower latency
DEFAULT_MODEL = "nvidia/canary-1b-flash"

# Supported languages for canary-1b-flash
CANARY_FLASH_LANGUAGES = ["en", "de", "es", "fr"]


@dataclass
class CanaryASRSession:
    """Per-connection session state for Canary streaming ASR."""

    id: str
    websocket: Any

    # Accumulated audio buffer (all audio received so far)
    accumulated_audio: Optional[np.ndarray] = None

    # Number of samples already processed
    processed_samples: int = 0

    # Current transcription (cumulative)
    current_text: str = ""

    # Last text emitted to client on hard reset (for server-side deduplication)
    last_emitted_text: str = ""

    # Source and target language for this session
    source_lang: str = "es"
    target_lang: str = "es"


class CanaryASRServer:
    """WebSocket server for Canary multilingual ASR with chunked processing."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = "0.0.0.0",
        port: int = 8080,
        source_lang: str = "es",
        target_lang: str = "es",
        chunk_secs: float = 1.0,
    ):
        self.model_name = model
        self.host = host
        self.port = port
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.chunk_secs = chunk_secs
        self.model = None
        self.sample_rate = 16000

        # Calculate chunk size in samples
        self.chunk_samples = int(chunk_secs * self.sample_rate)

        # Inference lock (Canary is not thread-safe)
        self.inference_lock = asyncio.Lock()

        # Active sessions
        self.sessions: dict[str, CanaryASRSession] = {}

        # Model loaded flag for health check
        self.model_loaded = False

    def load_model(self):
        """Load the Canary model."""
        import nemo.collections.asr as nemo_asr

        logger.info(f"Loading Canary model: {self.model_name}")
        logger.info(f"  Source language: {self.source_lang}")
        logger.info(f"  Target language: {self.target_lang}")
        logger.info(f"  Chunk size: {self.chunk_secs}s ({self.chunk_samples} samples)")

        # Load Canary model (encoder-decoder multi-task model)
        self.model = nemo_asr.models.EncDecMultiTaskModel.from_pretrained(
            self.model_name, map_location='cpu'
        )
        self.model = self.model.cuda()
        self.model.eval()

        logger.info(f"Canary model loaded: {type(self.model).__name__}")

        # Warmup inference
        self._warmup()

    def _warmup(self):
        """Run warmup inference to claim GPU memory."""
        import time

        logger.info("Running warmup inference...")
        start = time.perf_counter()

        # Generate 2 seconds of silence for warmup
        warmup_samples = 2 * self.sample_rate
        warmup_audio = np.zeros(warmup_samples, dtype=np.float32)

        # Run transcription to force CUDA kernels to compile
        with torch.inference_mode():
            audio_tensor = torch.from_numpy(warmup_audio).unsqueeze(0).cuda()
            audio_len = torch.tensor([len(warmup_audio)], device='cuda')

            # Use the transcribe method for warmup
            _ = self.model.transcribe(
                audio=warmup_audio,
                batch_size=1,
                source_lang=self.source_lang,
                target_lang=self.target_lang,
            )

        elapsed = (time.perf_counter() - start) * 1000
        logger.info(f"Warmup complete in {elapsed:.0f}ms - GPU memory claimed")

    def _init_session(self, session: CanaryASRSession):
        """Initialize a fresh session."""
        session.accumulated_audio = np.array([], dtype=np.float32)
        session.processed_samples = 0
        session.current_text = ""

    async def websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Handle a WebSocket client connection."""
        import uuid

        ws = web.WebSocketResponse(max_msg_size=10 * 1024 * 1024)
        await ws.prepare(request)

        session_id = str(uuid.uuid4())[:8]
        session = CanaryASRSession(
            id=session_id,
            websocket=ws,
            source_lang=self.source_lang,
            target_lang=self.target_lang,
        )
        self.sessions[session_id] = session

        logger.info(f"Client {session_id} connected (lang: {self.source_lang})")

        try:
            # Initialize session
            self._init_session(session)

            await ws.send_str(json.dumps({"type": "ready"}))
            logger.debug(f"Client {session_id}: sent ready")

            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    await self._handle_audio(session, msg.data)
                elif msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        msg_type = data.get("type")

                        if msg_type == "reset" or msg_type == "end":
                            finalize = data.get("finalize", True)
                            await self._reset_session(session, finalize=finalize)
                        elif msg_type == "config":
                            # Allow runtime language configuration
                            if "source_lang" in data:
                                session.source_lang = data["source_lang"]
                            if "target_lang" in data:
                                session.target_lang = data["target_lang"]
                            logger.info(
                                f"Client {session_id}: config update - "
                                f"source={session.source_lang}, target={session.target_lang}"
                            )
                        else:
                            logger.warning(f"Client {session_id}: unknown message type: {msg_type}")

                    except json.JSONDecodeError:
                        logger.warning(f"Client {session_id}: invalid JSON")
                elif msg.type == WSMsgType.ERROR:
                    logger.error(f"Client {session_id} WebSocket error: {ws.exception()}")
                    break

            logger.info(f"Client {session_id} disconnected")

        except Exception as e:
            logger.error(f"Client {session_id} error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            try:
                await ws.send_str(json.dumps({
                    "type": "error",
                    "message": str(e)
                }))
            except:
                pass
        finally:
            if session_id in self.sessions:
                del self.sessions[session_id]

        return ws

    async def _handle_audio(self, session: CanaryASRSession, audio_bytes: bytes):
        """Accumulate audio and process when enough samples available."""
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        if DEBUG_ASR:
            chunk_hash = hashlib.md5(audio_bytes).hexdigest()[:8]
            logger.debug(f"Session {session.id}: recv chunk {len(audio_bytes)}B hash={chunk_hash}")

        session.accumulated_audio = np.concatenate([session.accumulated_audio, audio_np])

        # Process when we have enough new audio (chunk_samples worth)
        new_samples = len(session.accumulated_audio) - session.processed_samples
        while new_samples >= self.chunk_samples:
            async with self.inference_lock:
                text = await asyncio.get_event_loop().run_in_executor(
                    None, self._process_chunk, session
                )

            if text is not None and text != session.current_text:
                session.current_text = text
                logger.debug(f"Session {session.id} interim: {text[-50:] if len(text) > 50 else text}")
                await session.websocket.send_str(json.dumps({
                    "type": "transcript",
                    "text": text,
                    "is_final": False
                }))

            # Update for next iteration
            new_samples = len(session.accumulated_audio) - session.processed_samples

    def _process_chunk(self, session: CanaryASRSession) -> Optional[str]:
        """Process accumulated audio with Canary model."""
        try:
            if len(session.accumulated_audio) == 0:
                return session.current_text

            # Process all accumulated audio (Canary works best with full context)
            audio_to_process = session.accumulated_audio

            if DEBUG_ASR:
                audio_hash = _hash_audio(audio_to_process)
                logger.debug(
                    f"Session {session.id}: process audio={len(audio_to_process)} "
                    f"samples hash={audio_hash}"
                )

            with torch.inference_mode():
                # Use transcribe method (simpler than manual encoding for Canary)
                results = self.model.transcribe(
                    audio=audio_to_process,
                    batch_size=1,
                    source_lang=session.source_lang,
                    target_lang=session.target_lang,
                )

                # Update processed position
                session.processed_samples = len(session.accumulated_audio)

                # Extract text from results
                if results and len(results) > 0:
                    if isinstance(results[0], str):
                        return results[0]
                    elif hasattr(results[0], 'text'):
                        return results[0].text

                return session.current_text

        except Exception as e:
            logger.error(f"Session {session.id} chunk processing error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    async def _reset_session(self, session: CanaryASRSession, finalize: bool = True):
        """Handle reset with soft or hard finalization.

        Args:
            finalize: If True (hard reset), process all remaining audio and emit final text.
                      If False (soft reset), just return current text without processing.
        """
        audio_samples = len(session.accumulated_audio) if session.accumulated_audio is not None else 0
        audio_duration_ms = (audio_samples * 1000) // self.sample_rate
        logger.debug(
            f"Session {session.id} {'hard' if finalize else 'soft'} reset: "
            f"accumulated={audio_samples} samples ({audio_duration_ms}ms)"
        )

        if not finalize:
            # SOFT RESET: Return current text without processing
            text = session.current_text

            await session.websocket.send_str(json.dumps({
                "type": "transcript",
                "text": text,
                "is_final": True,
                "finalize": False
            }))

            logger.debug(f"Session {session.id} soft reset: '{text[-50:] if len(text) > 50 else text}'")
            return

        # HARD RESET: Process all remaining audio
        final_text = session.current_text

        if session.accumulated_audio is not None and len(session.accumulated_audio) > 0:
            async with self.inference_lock:
                text = await asyncio.get_event_loop().run_in_executor(
                    None, self._process_final, session
                )
                if text is not None:
                    final_text = text
                    session.current_text = text

        # Server-side deduplication: only send the delta
        if final_text.startswith(session.last_emitted_text):
            delta_text = final_text[len(session.last_emitted_text):].lstrip()
        else:
            delta_text = final_text
            logger.debug(
                f"Session {session.id}: ASR correction detected, "
                f"last='{session.last_emitted_text[-30:]}', new='{final_text[-30:]}'"
            )

        session.last_emitted_text = final_text

        await session.websocket.send_str(json.dumps({
            "type": "transcript",
            "text": delta_text,
            "is_final": True,
            "finalize": True
        }))

        logger.debug(
            f"Session {session.id} hard reset: delta='{delta_text}' "
            f"(cumulative='{final_text[-50:] if len(final_text) > 50 else final_text}')"
        )

        # Reset session state for next utterance
        session.last_emitted_text = ""
        self._init_session(session)

        logger.debug(f"Session {session.id} hard reset complete")

    def _process_final(self, session: CanaryASRSession) -> Optional[str]:
        """Process all accumulated audio for final transcription."""
        try:
            if len(session.accumulated_audio) == 0:
                return session.current_text

            with torch.inference_mode():
                results = self.model.transcribe(
                    audio=session.accumulated_audio,
                    batch_size=1,
                    source_lang=session.source_lang,
                    target_lang=session.target_lang,
                )

                if results and len(results) > 0:
                    if isinstance(results[0], str):
                        return results[0]
                    elif hasattr(results[0], 'text'):
                        return results[0].text

                return session.current_text

        except Exception as e:
            logger.error(f"Session {session.id} final processing error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    async def health_handler(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "healthy" if self.model_loaded else "loading",
            "model": "canary",
            "model_name": self.model_name,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "model_loaded": self.model_loaded,
        })

    async def start(self):
        """Start the HTTP + WebSocket server."""
        self.load_model()
        self.model_loaded = True

        logger.info(f"Starting Canary ASR server on ws://{self.host}:{self.port}")

        app = web.Application()
        app.router.add_get("/health", self.health_handler)
        app.router.add_get("/", self.websocket_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()

        logger.info(f"Canary ASR server listening on ws://{self.host}:{self.port}")
        logger.info(f"Health check available at http://{self.host}:{self.port}/health")
        await asyncio.Future()  # Run forever


def main():
    parser = argparse.ArgumentParser(description="Canary Multilingual ASR WebSocket Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Canary model name (nvidia/canary-1b-flash or nvidia/canary-1b-v2)"
    )
    parser.add_argument(
        "--source-lang",
        default="es",
        help="Source language code (default: es for Spanish)"
    )
    parser.add_argument(
        "--target-lang",
        default="es",
        help="Target language code (default: es for Spanish, same as source for ASR)"
    )
    parser.add_argument(
        "--chunk-secs",
        type=float,
        default=1.0,
        help="Audio chunk size in seconds (minimum 1.0 for Canary)"
    )
    args = parser.parse_args()

    # Validate chunk size
    if args.chunk_secs < 1.0:
        logger.warning(f"Chunk size {args.chunk_secs}s is below minimum, using 1.0s")
        args.chunk_secs = 1.0

    server = CanaryASRServer(
        model=args.model,
        host=args.host,
        port=args.port,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        chunk_secs=args.chunk_secs,
    )

    asyncio.run(server.start())


if __name__ == "__main__":
    main()
