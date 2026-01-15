# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a voice agent demo using NVIDIA open-source models (Nemotron Speech ASR, Nemotron-3 Nano LLM, Magpie TTS) built with Pipecat. The project is optimized for low voice-to-voice latency (~500-700ms) through streaming pipeline architecture.

Target platforms: DGX Spark, RTX 5090 (Blackwell), Ampere GPUs (A100, A10, RTX 30xx), or cloud deployment via Modal and Pipecat Cloud.

## Common Commands

### Local Development (GPU Required)

```bash
# Build unified container (2-3 hours first time)
# For Blackwell GPUs (DGX Spark, RTX 5090)
docker build -f Dockerfile.unified -t nemotron-unified:blackwell .

# For Ampere GPUs (A100, A10, RTX 30xx)
docker build -f Dockerfile.unified --build-arg GPU_ARCH=ampere -t nemotron-unified:ampere .

# IMPORTANT: Configuration is now managed via .env file (not command-line args)

# Setup environment
cp .env.example .env

# Edit .env to configure your setup (CRITICAL: use container paths, not host paths)
# Key settings:
#   COMPOSE_PROFILES    - llamacpp or vllm
#   LLM_MODE            - llamacpp-q8, llamacpp-q4, or vllm
#   LLAMA_MODEL         - Path to GGUF model (container path)
#   ASR_MODEL           - nemotron (English) or canary (multilingual)
#   TTS_LANGUAGE        - en, es, de, fr, vi, it, zh
vim .env

# Start AI services container
docker compose up -d

# Container management
docker compose ps                              # Check status
docker compose logs -f                         # View all logs
docker compose logs -f | grep "\[ASR\]"       # View ASR logs
docker compose logs -f | grep "\[TTS\]"       # View TTS logs
docker compose logs -f | grep "\[LLM\]"       # View LLM logs
docker compose exec nemotron-llamacpp bash    # Shell (llamacpp mode)
docker compose exec nemotron-vllm bash        # Shell (vLLM mode)
docker compose down                            # Stop services
docker compose restart                         # Restart services

# Run voice bot (after AI services container is running)

# Option A: Run bot on host
uv run pipecat_bots/bot_interleaved_streaming.py    # Buffered LLM + adaptive TTS
uv run pipecat_bots/bot_simple_vad.py               # Same but simpler VAD
uv run pipecat_bots/bot_vllm.py                     # vLLM variant for cloud

# Option B: Run bot in Docker container
docker compose -f docker-compose.bot.yaml up --build      # Build and run
docker compose -f docker-compose.bot.yaml up -d           # Run detached
docker compose -f docker-compose.bot.yaml logs -f         # View logs
docker compose -f docker-compose.bot.yaml down            # Stop
```

### Cloud Deployment (Modal)

```bash
# Install dependencies and authenticate
uv sync --extra modal --extra bot
modal setup

# Deploy services
modal deploy -m src.nemotron_speech.modal.asr_server_modal
modal deploy -m src.nemotron_speech.modal.tts_server_modal
modal deploy -m src.nemotron_speech.modal.vllm_modal

# Run bot with Modal services
uv run -m pipecat_bots.modal.bot_modal
```

### Pipecat Cloud Deployment

```bash
# Install and authenticate
uv sync --extra bot
pipecat cloud auth login

# Configure secrets
pipecat cloud secrets set gdx-spark-bot-secrets --file .env

# Build and deploy
docker build -f Dockerfile.bot -t gdx-spark-bot:latest .
docker push your-docker-repository/gdx-spark-bot:latest
pipecat cloud deploy gdx-spark-bot your-docker-repository/gdx-spark-bot:latest \
  --credentials gdx-spark-bot-pull-secret \
  --secrets gdx-spark-bot-secrets \
  --profile agent-1x
```

### Tailscale HTTPS Deployment

Serve the bot over HTTPS on your Tailscale network using `tailscale serve`. This provides secure access to the bot from any device on your Tailscale network without exposing it to the internet.

```bash
# Prerequisites
# 1. Tailscale installed on host (https://tailscale.com/download)
# 2. Tailscale authenticated (run `tailscale up` to connect)

# Start AI services (unified container)
cp .env.example .env
vim .env                           # Configure model path and settings
docker compose up -d

# Run the bot (listens on localhost:7860)
# Option A: On host
uv run pipecat_bots/bot_interleaved_streaming.py

# Option B: In Docker container
docker compose -f docker-compose.bot.yaml up -d

# In another terminal, enable HTTPS serving via Tailscale
tailscale serve https / http://localhost:7860

# Check what's being served
tailscale serve status

# Test HTTPS access from any Tailscale-connected device
# (replace with your actual hostname from `tailscale serve status`)
curl https://your-machine-name.your-tailnet.ts.net/health

# Management commands
tailscale serve status          # Check current serve configuration
tailscale serve reset           # Stop serving (removes HTTPS exposure)
tailscale status                # View Tailscale connection status
```

**Architecture:**
- Tailscale provides VPN connectivity, automatic TLS certificates, and HTTPS reverse proxy
- Bot service runs on host or in Docker container, accesses AI services on localhost (ports 8080, 8001, 8000)
- When using `docker-compose.bot.yaml`, the container uses host networking to access localhost services
- Accessible only to devices on your Tailscale network (not internet-exposed)

**Configuration:**
- No configuration files needed
- Bot uses default localhost URLs for AI services:
  - `NVIDIA_ASR_URL`: `ws://localhost:8080`
  - `NVIDIA_TTS_URL`: `http://localhost:8001`
  - `NVIDIA_LLAMA_CPP_URL`: `http://localhost:8000`

**Troubleshooting:**
- **Certificate errors**: Verify MagicDNS is enabled in Tailscale admin console (Settings > DNS)
- **Bot unreachable**: Check AI services are running (`docker compose ps`) and bot is listening on port 7860 (`curl http://localhost:7860/health`)
- **WebSocket failures**: `tailscale serve` automatically handles WebSocket upgrades

### Development Tools

```bash
# Install dependencies
uv sync                      # Core dependencies
uv sync --extra dev          # Include dev tools (pytest, ruff, mypy)

# Testing (requires services running)
pytest tests/                # Run all tests
pytest tests/test_streaming_tts.py    # Specific test file

# Code quality
ruff check .                 # Lint
ruff format .                # Format
mypy src/                    # Type checking
```

## Architecture

### High-Level Pipeline Flow

The voice agent uses a **pipelined streaming architecture** to minimize latency:

```
Audio In → STT (streaming) → LLM (buffered, sentence boundaries) → TTS (adaptive) → Audio Out
```

**Key optimizations:**
1. **Streaming STT**: Continuous audio streaming with frame ordering fix to prevent 500ms timeout
2. **Buffered LLM**: Single-slot operation with 100% KV cache reuse, emits at sentence boundaries
3. **Adaptive TTS**: First segment uses streaming mode (~370ms TTFB), subsequent segments use batch mode for quality

### Directory Structure

```
├── src/nemotron_speech/         # Inference services (ASR, TTS)
│   ├── server.py                # WebSocket ASR server (Nemotron-Speech/Parakeet, English)
│   ├── canary_server.py         # WebSocket ASR server (Canary, multilingual/Spanish)
│   ├── tts_server.py            # WebSocket TTS server (Magpie model)
│   ├── streaming_tts.py         # Frame-by-frame TTS generation
│   ├── adaptive_stream.py       # TTS stream state management
│   └── modal/                   # Modal deployment modules
│       ├── asr_server_modal.py  # ASR service on Modal
│       ├── tts_server_modal.py  # TTS service on Modal
│       └── vllm_modal.py        # vLLM service on Modal
│
├── pipecat_bots/                # Pipecat bot implementations
│   ├── bot_interleaved_streaming.py  # Main buffered LLM + adaptive TTS bot
│   ├── bot_simple_vad.py             # Simple VAD variant
│   ├── bot_vllm.py                   # vLLM variant for cloud
│   ├── nvidia_stt.py                 # WebSocket STT client with frame ordering fix
│   ├── llama_cpp_buffered_llm.py     # Buffered LLM with sentence boundaries
│   ├── sentence_buffer.py            # Sentence boundary detection
│   ├── magpie_websocket_tts.py       # WebSocket TTS client with adaptive mode
│   ├── v2v_metrics.py                # Voice-to-voice latency metrics
│   └── frames.py                     # Shared frame types (ChunkedLLMContinueGenerationFrame)
│
├── scripts/                     # Container scripts
│   └── start_unified.sh        # Container startup logic (runs inside container)
│
├── docker-compose.bot.yaml     # Docker Compose for running bot in container
├── Dockerfile.bot              # Bot container image
├── Dockerfile.unified          # Unified container (ASR, TTS, LLM services)
│
├── tests/                       # Service tests and benchmarks
├── docs/                        # Architecture documentation
│   └── streaming-pipeline-architecture.md  # Detailed pipeline docs
└── vllm_plugins/               # Custom vLLM plugins for Nemotron
```

### Key Components

**STT Service - English** (`src/nemotron_speech/server.py`):
- NVIDIA Nemotron-Speech (Parakeet) model with true incremental streaming
- Processes audio in 160ms chunks with encoder/decoder cache
- Configurable right context (0-13 frames) for latency/accuracy tradeoff
- Default: 1 frame (~160ms latency, recommended)
- English only

**STT Service - Multilingual** (`src/nemotron_speech/canary_server.py`):
- NVIDIA Canary model for Spanish, German, French, English
- Encoder-decoder architecture with 1-second minimum chunk size
- Higher latency (~1.5s) but supports multiple languages
- Same WebSocket protocol as server.py for Pipecat compatibility
- Select with `ASR_MODEL=canary` in .env

**LLM Service** (`pipecat_bots/llama_cpp_buffered_llm.py`):
- Run-to-completion buffered approach (no mid-stream cancellation)
- Single-slot operation with `--parallel 1` for 100% KV cache reuse
- Emits text at sentence boundaries via `SentenceBuffer`
- First segment: 24 tokens (fast TTFB), subsequent: 32-96 tokens
- Waits for TTS to complete each segment before generating next

**TTS Service** (`src/nemotron_speech/tts_server.py` + `pipecat_bots/magpie_websocket_tts.py`):
- Adaptive mode: streaming for first segment, batch for quality thereafter
- Sentence splitting to prevent GPU OOM on long text
- WebSocket protocol with binary audio frames (16-bit PCM, 22kHz)
- Streaming presets: aggressive (~185ms), balanced (~280ms), conservative (~370ms)

**Frame Ordering Fix** (`pipecat_bots/nvidia_stt.py`):
- Holds `UserStoppedSpeakingFrame` until final `TranscriptionFrame` arrives
- Prevents 500ms aggregation timeout in LLM context aggregator
- Critical for maintaining low latency

### Service Endpoints (Local Container)

| Service | Port | Protocol | Health Check |
|---------|------|----------|--------------|
| ASR | 8080 | WebSocket | `http://localhost:8080/health` |
| TTS | 8001 | HTTP + WebSocket | `http://localhost:8001/health` |
| LLM | 8000 | HTTP (llama.cpp or vLLM) | `http://localhost:8000/health` |

### Bot Variants

| Bot | Description | Use Case |
|-----|-------------|----------|
| `bot_interleaved_streaming.py` | Buffered LLM + adaptive TTS + SmartTurn | Optimized for single GPU, low latency |
| `bot_simple_vad.py` | Same as above, simple VAD | Fixed silence threshold |
| `bot_vllm.py` | vLLM + SentenceAggregator + SmartTurn | Multi-GPU cloud deployment |

All bots support WebRTC (default), Daily, and Twilio transports via `-t` flag.

## Important Implementation Details

### Single-Slot LLM Operation
The buffered LLM uses `--parallel 1` with llama.cpp for 100% KV cache reuse. This means:
- Only ONE slot is available for inference
- Context is never evicted between turns
- First token latency on subsequent turns is near-zero (~0ms)
- Do NOT use concurrent requests - the slot will be locked

### Sentence Boundary Detection
The `SentenceBuffer` extracts at sentence boundaries (`.!?` followed by space/quotes). When modifying:
- Maintain the pattern: `r'[.!?]["\'\)]*\s'`
- Handle edge cases: abbreviations (Dr., Mr.), decimal numbers, URLs
- The buffer keeps incomplete tail text for next extraction

### TTS Streaming Mode
Streaming TTS uses overlap-add to blend chunk boundaries:
- Correlation-based blending: high correlation = Hann window (COLA), low = equal-power crossfade
- Overlap region: 220 samples (10ms at 22kHz)
- Do NOT reduce overlap below 5ms - will cause audible clicks

### VAD Alignment
The VAD `stop_secs=0.2` is aligned with ASR trailing context requirements:
- ASR needs `(right_context + 1) * 160ms` of silence
- Default: 320ms total, VAD fires at 200ms, server pads remaining 120ms
- If changing VAD timing, verify ASR gets sufficient trailing context

### Frame Ordering
The frame ordering fix in `nvidia_stt.py` is CRITICAL:
- Without it: `UserStoppedSpeakingFrame` arrives before `TranscriptionFrame` → 500ms timeout
- With it: `TranscriptionFrame` arrives first → immediate LLM processing
- Do NOT remove the `_waiting_for_final` logic

## Model Requirements

| Model | Source | Size | Used With |
|-------|--------|------|-----------|
| Nemotron Speech ASR | HuggingFace `nvidia/nemotron-speech-streaming-en-0.6b` | ~2.4GB | Auto-downloaded (English) |
| Canary-1B-Flash | HuggingFace `nvidia/canary-1b-flash` | ~3.5GB | Multilingual ASR (es, de, fr, en) |
| Canary-1B-V2 | HuggingFace `nvidia/canary-1b-v2` | ~4GB | 25-language ASR |
| Nemotron-3-Nano Q8 | HuggingFace `unsloth/Nemotron-3-Nano-30B-A3B-GGUF` | ~32GB | llama.cpp (DGX Spark) |
| Nemotron-3-Nano Q4 | HuggingFace `unsloth/Nemotron-3-Nano-30B-A3B-GGUF` | ~16GB | llama.cpp (RTX 5090) |
| Nemotron-3-Nano BF16 | HuggingFace `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` | ~72GB | vLLM (cloud/multi-GPU) |
| Magpie TTS | HuggingFace `nvidia/magpie_tts_multilingual_357m` | ~1.4GB | Auto-downloaded |

Download LLM models:
```bash
# GGUF quantized (for llama.cpp)
huggingface-cli download unsloth/Nemotron-3-Nano-30B-A3B-GGUF

# BF16 full precision (for vLLM)
huggingface-cli download nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16
```

## Environment Variables

### ASR Configuration (Container)
- `ASR_MODEL` - ASR backend: `nemotron` (English, ~160ms latency) or `canary` (multilingual, ~1.5s latency)
- `CANARY_MODEL` - Canary model name (default: `nvidia/canary-1b-flash`)
- `ASR_SOURCE_LANG` - Source language code for Canary (default: `es`)
- `ASR_TARGET_LANG` - Target language code for Canary (default: `es`)

### TTS Configuration (Bot)
- `TTS_LANGUAGE` - TTS output language (default: `en`). Supported: en, es, de, fr, vi, it, zh

### bot_interleaved_streaming.py / bot_simple_vad.py
- `NVIDIA_ASR_URL` - ASR WebSocket endpoint (default: `ws://localhost:8080`)
- `NVIDIA_LLAMA_CPP_URL` - llama.cpp API endpoint (default: `http://localhost:8000`)
- `NVIDIA_TTS_URL` - Magpie TTS endpoint (default: `http://localhost:8001`)
- `ENABLE_RECORDING` - Enable stereo audio recording (default: `false`)

### bot_vllm.py
- `NVIDIA_ASR_URL` - ASR WebSocket endpoint (default: `ws://localhost:8080`)
- `NVIDIA_LLM_URL` - vLLM OpenAI-compatible endpoint (default: `http://localhost:8000/v1`)
- `NVIDIA_LLM_MODEL` - Model name/path (default: `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16`)
- `NVIDIA_LLM_API_KEY` - API key if required (default: `not-needed`)
- `NVIDIA_TTS_URL` - Magpie TTS endpoint (default: `http://localhost:8001`)

## Common Issues

**LLM crashes or stalls:**
- Verify adequate VRAM for context size (default 16384 tokens)
- Check for httpx connection issues if generation hangs
- Ensure single-slot operation (`--parallel 1`)

**vLLM takes 10-15 minutes to start:**
- Normal for first startup (model loading, kernel compilation)
- Set `SERVICE_TIMEOUT=900` if needed

**vLLM DNS resolution issues:**
- Container uses `--network=host` in vLLM mode to avoid HuggingFace DNS issues

**Audio clicks/pops in TTS:**
- Check overlap-add region (should be 220 samples minimum)
- Verify streaming preset (conservative = 12 frames, more stable)

**High voice-to-voice latency (>1s):**
- Check VAD `stop_secs` (should be ~0.2)
- Verify frame ordering fix in STT client
- Check LLM cache hit ratio (should be >90% after first turn)
- Monitor TTS TTFB (should be ~370ms for first segment)

**Spanish ASR has higher latency (~1.5s):**
- Expected behavior - Canary uses encoder-decoder architecture requiring 1-second minimum chunks
- Nemotron-Speech (English) achieves ~160ms chunks via CTC-based streaming
- For lowest latency voice agents, use English with `ASR_MODEL=nemotron`

## Additional Documentation

For detailed architecture documentation including frame flow, protocols, and timing diagrams, see:
- `docs/streaming-pipeline-architecture.md` - Complete pipeline architecture
- README.md - Setup and deployment guide
