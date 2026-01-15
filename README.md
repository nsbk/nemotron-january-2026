# Voice Agent With NVIDIA Open Models

[![Demo Video](https://img.youtube.com/vi/8Fkz2PC54BI/maxresdefault.jpg)](https://www.youtube.com/watch?v=8Fkz2PC54BI)

This repo is sample code for building voice agents with three NVIDIA open source models:
  - Nemotron Speech ASR
  - Nemotron 3 Nano LLM
  - Magpie TTS (Preview)

Run locally on an NVIDIA DGX Spark, RTX 5090 (Blackwell), or Ampere GPUs (A100, A10, RTX 30xx). Or deploy to the cloud with Modal and Pipecat Cloud.

Accompanying blog posts:
- [Nemotron Speech ASR Open Source Model Launch Post](https://huggingface.co/blog/nvidia/nemotron-speech-asr-scaling-voice-agents)
- [More About Voice Agent Architectures and This Agent's Design](https://www.daily.co/blog/building-voice-agents-with-nvidia-open-models/)

## Quick start - Run everything locally (DGX Spark, RTX 5090, or Ampere GPUs)

### 1. Build the Unified Container

```bash
# For Blackwell GPUs (DGX Spark, RTX 5090) - default
docker build -f Dockerfile.unified -t nemotron-unified:blackwell .

# For Ampere GPUs (A100, A10, A30, A40, RTX 30xx)
docker build -f Dockerfile.unified --build-arg GPU_ARCH=ampere -t nemotron-unified:ampere .
```

Build time: 2-3 hours (builds PyTorch, NeMo, vLLM, llama.cpp from source).

### 2. Start the Container

Configuration is managed via `.env` file:

```bash
# Copy the example configuration
cp .env.example .env

# Edit .env to configure your setup:
#   - COMPOSE_PROFILES: llamacpp or vllm
#   - LLM_MODE: llamacpp-q8, llamacpp-q4, or vllm
#   - LLAMA_MODEL: path to GGUF model (container path)
#   - ASR_MODEL: nemotron (English) or canary (multilingual)
#   - TTS_LANGUAGE: en, es, de, fr, vi, it, zh
vim .env

# Start AI services
docker compose up -d
```

### 3. Run the Voice Bot

```bash
uv run pipecat_bots/bot_interleaved_streaming.py
```

Open `http://localhost:7860/client` in your browser.

## Quick start - Deploy to Cloud with Modal and Pipecat Cloud

### Modal (Services)

#### 1. Prerequisites

Create a [Modal](https://www.modal.com) account if you don't have one. 

Then, install the necessary dependencies using `uv` with optional dependency group `modal` and authenticate your account.

```bash
# Install Modal and Pipecat Cloud dependencies
uv sync --extra modal --extra bot

# Authenticate with Modal
modal setup
```

#### 2. Deploy Services to Modal

```bash
# Deploy ASR service
modal deploy -m src.nemotron_speech.modal.asr_server_modal

# Deploy TTS service
modal deploy -m src.nemotron_speech.modal.tts_server_modal

# Deploy vLLM service
modal deploy -m src.nemotron_speech.modal.vllm_modal
```

The ASR deployment takes about 30 seconds to cold-start, 60 seconds for TTS, and about 3 minutes for vLLM. You can uncomment the `min_containers = 1` input to the Modal `Function` and `Cls` decorators to ensure that bots can start up quickly for production or development.

#### 3. Run the bot locally or using Pipecat Cloud (see below)

```bash
uv run -m pipecat_bots.modal.bot_modal
```

### Pipecat Cloud (Bot)

> [!NOTE]
> Sign up for a [Pipecat Cloud](https://docs.pipecat.ai/deployment/pipecat-cloud/introduction) account [here](https://pipecat.daily.co/)

#### 1. Login to your Pipecat Cloud account using the CLI

```bash
# Install Pipecat Cloud package
uv sync --extra bot
# Or Pipecat Cloud and Modal
uv sync --extra bot --extra modal

# Login
pipecat cloud auth login
```

#### 2. Create a new secret set with the necessary API keys

```bash
pipecat cloud secrets set gdx-spark-bot-secrets \
  NVIDIA_ASR_URL=wss:// \
  NVIDIA_LLM_URL=https:// \
  NVIDIA_TTS_URL=wss://
```

_Alternatively, create your secret set from a `.env` file:_

```bash
pipecat cloud secrets set gdx-spark-bot-secrets --file .env
```

#### 3. Create image pull secret

Image pull secrets are used to authenticate with private Docker registries when deploying agents. [See docs](https://docs.pipecat.ai/deployment/pipecat-cloud/fundamentals/secrets#image-pull-secrets).

```bash
pipecat cloud secrets image-pull-secret gdx-spark-bot-pull-secret https://index.docker.io/v1/
```

___Optional: Create a PCC deploy toml___:

To speed up deployment you can create a `pcc-deploy.toml` in the project root. This file is read by the Pipecat CLI to pre-fill command arguments:

```bash
agent_name = "gdx-spark-bot"
image = "your-docker-repository/gdx-spark-bot:latest"
secret_set = "gdx-spark-bot-secrets"
image_credentials = "gdx-spark-bot-pull-secret"
agent_profile = "agent-1x"

[scaling]
	min_agents = 1
```

#### 4. Build and push Docker image

```bash
docker build -f Dockerfile.bot -t gdx-spark-bot:latest .

# Optional: tag image
docker tag gdx-spark-bot:latest your-docker-repository/gdx-spark-bot:latest

# Push to image repository e.g. Docker Hub
docker push your-docker-repository/gdx-spark-bot:latest
```

#### 5. Deploy

Run `deploy` command:

```bash
pipecat cloud deploy

# ...or if not using pcc-deploy.toml

pipecat cloud deploy gdx-spark-bot your-docker-repository/gdx-spark-bot:latest \
--credentials gdx-spark-bot-pull-secret \
--secrets gdx-spark-bot-secrets \
--profile agent-1x
```

#### 6. Start bot using CLI

Create a public access key for Pipecat Cloud. Set this is a the default key when prompted:

```bash
pipecat cloud organizations keys create
```

Start an active session with your deployed bot:

```bash
pipecat cloud agent start gdx-spark-bot --use-daily
```

[See docs](https://docs.pipecat.ai/deployment/pipecat-cloud/fundamentals/active-sessions) for REST and Python usage.

## Quick start - HTTPS on Tailscale Network

Serve the bot over HTTPS on your Tailscale network using `tailscale serve`. The bot will be accessible at `https://your-hostname.your-tailnet.ts.net` from any device on your Tailscale network without exposing it to the internet.

### Prerequisites

1. **Tailscale installed on host**: Install from [tailscale.com](https://tailscale.com/download)
2. **Tailscale authenticated**: Run `tailscale up` to connect your machine to your tailnet
3. **AI services running**: Start unified container with AI services first (see local deployment above)

### 1. Start AI Services

Start the unified container with AI services (ASR, TTS, LLM):

```bash
# Copy and configure environment
cp .env.example .env
vim .env                    # Set LLAMA_MODEL path and other settings

# Start services
docker compose up -d
```

### 2. Run the Bot

**Option A: Run bot locally on host**

```bash
uv run pipecat_bots/bot_interleaved_streaming.py
```

**Option B: Run bot in Docker container**

```bash
# Build and start the bot container
docker compose -f docker-compose.bot.yaml up --build

# Or run detached (in background)
docker compose -f docker-compose.bot.yaml up -d

# View logs
docker compose -f docker-compose.bot.yaml logs -f

# Stop the bot
docker compose -f docker-compose.bot.yaml down
```

Both options will listen on localhost:7860.

### 3. Enable Tailscale HTTPS

Use `tailscale serve` to expose the bot over HTTPS on your Tailscale network:

```bash
tailscale serve https / http://localhost:7860
```

This command:
- Automatically provisions TLS certificates from Tailscale
- Makes the bot accessible at `https://your-machine-name.your-tailnet.ts.net`
- Only accessible to devices on your Tailscale network (not internet-exposed)

### 4. Access the Bot

From any device on your Tailscale network:

```bash
# Check what's being served
tailscale serve status

# Test health endpoint (replace with your actual hostname)
curl https://your-machine-name.your-tailnet.ts.net/health

# Open in browser
open https://your-machine-name.your-tailnet.ts.net/client
```

### Management Commands

```bash
# Check Tailscale serve status
tailscale serve status

# Stop serving (removes HTTPS exposure)
tailscale serve reset

# View Tailscale connection status
tailscale status
```

### Architecture

- **Tailscale**: Provides VPN connectivity, automatic TLS certificates, and HTTPS reverse proxy
- **Bot Service**: Runs on host, accesses AI services on localhost
- **Security**: Accessible only to Tailscale network (not internet-exposed)

### Troubleshooting

**Certificate errors**: Verify MagicDNS is enabled in Tailscale admin console (Settings > DNS)

**Bot unreachable**:
- Check AI services are running: `docker compose ps`
- Verify bot is running on port 7860: `curl http://localhost:7860/health`
- Check Tailscale serve status: `tailscale serve status`

**WebSocket/WebRTC failures**: `tailscale serve` automatically handles WebSocket upgrades


## Spanish Language Support

The voice agent supports Spanish using NVIDIA Canary ASR models. Canary provides multilingual support including Spanish, German, French, and English.

### Quick Start - Spanish

```bash
# Copy and configure environment
cp .env.example .env

# Edit .env to enable Spanish:
#   ASR_MODEL=canary
#   ASR_SOURCE_LANG=es
#   ASR_TARGET_LANG=es
#   TTS_LANGUAGE=es
vim .env

# Start AI services
docker compose up -d

# Run the bot
uv run pipecat_bots/bot_interleaved_streaming.py
```

### Important: Latency Trade-off

Spanish uses Canary ASR which has **higher latency (~1.5s)** compared to English with Nemotron-Speech (~160ms). This is due to architectural differences:

| ASR Model | Languages | Latency | Architecture |
|-----------|-----------|---------|--------------|
| Nemotron-Speech (default) | English only | ~160ms | CTC-based streaming |
| Canary | es, de, fr, en | ~1.5s | Encoder-decoder |

For lowest latency voice agents, use English with `ASR_MODEL=nemotron` (the default).

### ASR and TTS Configuration

Configure ASR and TTS in your `.env` file:

```bash
# Select ASR model
ASR_MODEL=canary                          # Use Canary for Spanish
# ASR_MODEL=nemotron                      # Use Nemotron-Speech for English (default)

# Canary model options (only used when ASR_MODEL=canary)
CANARY_MODEL=nvidia/canary-1b-flash       # 4 languages, lower latency (recommended)
# CANARY_MODEL=nvidia/canary-1b-v2        # 25 languages, higher accuracy

# Language settings
ASR_SOURCE_LANG=es                        # What the user speaks
ASR_TARGET_LANG=es                        # Output language (usually same as source)

# TTS output language (must match ASR for natural conversation)
TTS_LANGUAGE=es                           # Supported: en, es, de, fr, vi, it, zh
```

## Bot Variants

Three bot implementations are available:

| Bot | Description | Use Case |
|-----|-------------|----------|
| `bot_interleaved_streaming.py` | Buffered LLM (single-slot, 100% KV cache) + adaptive TTS + SmartTurn | Optimized for voice-to-voice latency on a single GPU |
| `bot_simple_vad.py` | Same as above, but simple VAD (fixed silence threshold) | When fixed silence detection is sufficient |
| `bot_vllm.py` | vLLM + SentenceAggregator + SmartTurn | Production multi-GPU cloud deployment |

### Transport Options

All bots support multiple transport backends via the `-t` flag:

| Transport | Description |
|-----------|-------------|
| `webrtc` | Native WebRTC (default) - opens browser at localhost:7860 |
| `daily` | Daily.co rooms - requires Daily API key |
| `twilio` | Twilio WebSocket - for telephony integration |

### bot_interleaved_streaming.py / bot_simple_vad.py

| Variable | Default | Description |
|----------|---------|-------------|
| `NVIDIA_ASR_URL` | `ws://localhost:8080` | ASR WebSocket endpoint |
| `NVIDIA_LLAMA_CPP_URL` | `http://localhost:8000` | llama.cpp API endpoint |
| `NVIDIA_TTS_URL` | `http://localhost:8001` | Magpie TTS endpoint |
| `ENABLE_RECORDING` | `false` | Enable stereo audio recording (user left, bot right) |

### bot_vllm.py

| Variable | Default | Description |
|----------|---------|-------------|
| `NVIDIA_ASR_URL` | `ws://localhost:8080` | ASR WebSocket endpoint |
| `NVIDIA_LLM_URL` | `http://localhost:8000/v1` | vLLM OpenAI-compatible endpoint |
| `NVIDIA_LLM_MODEL` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` | Model name/path |
| `NVIDIA_LLM_API_KEY` | `not-needed` | API key (if required) |
| `NVIDIA_TTS_URL` | `http://localhost:8001` | Magpie TTS endpoint |

## Pipecat Bot Components

Custom services in `pipecat_bots/`:

| Service | File | Description |
|---------|------|-------------|
| `LlamaCppBufferedLLMService` | `llama_cpp_buffered_llm.py` | Single-slot operation with SentenceBuffer for 100% KV cache reuse |
| `MagpieWebSocketTTSService` | `magpie_websocket_tts.py` | Adaptive streaming (fast TTFB first chunk, batch quality after) |
| `NVidiaWebSocketSTTService` | `nvidia_stt.py` | Real-time streaming ASR with soft/hard reset support |
| `SentenceBuffer` | `sentence_buffer.py` | Accumulates LLM output and extracts at sentence boundaries |
| `V2VMetricsProcessor` | `v2v_metrics.py` | Voice-to-voice response time metrics |

## Local Container Management

Use Docker Compose to manage the unified container. Configuration is managed via `.env` file.

### Quick Start

```bash
# 1. Copy the example configuration
cp .env.example .env

# 2. Edit .env to configure your setup (see comments in file)
vim .env

# 3. Start services
docker compose up -d
```

### Management Commands

```bash
# Check status
docker compose ps

# View logs (all services)
docker compose logs -f

# View logs for specific service patterns
docker compose logs -f | grep "\[ASR\]"
docker compose logs -f | grep "\[TTS\]"
docker compose logs -f | grep "\[LLM\]"

# Open shell in container
docker compose exec nemotron-llamacpp bash  # For llamacpp mode
docker compose exec nemotron-vllm bash      # For vLLM mode

# Stop services
docker compose down

# Restart services
docker compose restart
```

### Configuration Examples

**llama.cpp Q8 mode (most common)**:
```bash
# .env
COMPOSE_PROFILES=llamacpp
LLM_MODE=llamacpp-q8
LLAMA_MODEL=/root/.cache/huggingface/hub/models--unsloth--Nemotron-3-Nano-30B-A3B-GGUF/snapshots/HASH/Q8_0.gguf
```

**vLLM mode**:
```bash
# .env
COMPOSE_PROFILES=vllm
LLM_MODE=vllm
VLLM_MODEL=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16
SERVICE_TIMEOUT=900
```

**ASR + TTS only (no LLM)**:
```bash
# .env
COMPOSE_PROFILES=llamacpp
ENABLE_LLM=false
```

See `.env.example` for all configuration options and [MIGRATION.md](MIGRATION.md) for detailed migration guide from `scripts/nemotron.sh`.

### Service Endpoints

| Service | Port | Protocol | Health Check |
|---------|------|----------|--------------|
| ASR | 8080 | WebSocket | `http://localhost:8080/health` |
| TTS | 8001 | HTTP + WebSocket | `http://localhost:8001/health` |
| LLM | 8000 | HTTP | `http://localhost:8000/health` |

## Building the Container

```bash
# Build for Blackwell GPUs (default) - CUDA 13.x, sm_120/121
docker build -f Dockerfile.unified -t nemotron-unified:blackwell .

# Build for Ampere GPUs - CUDA 13.0, sm_80/86
docker build -f Dockerfile.unified --build-arg GPU_ARCH=ampere -t nemotron-unified:ampere .
```

The build compiles from source (2-3 hours):
- PyTorch (with NVRTC support)
- torchaudio
- NeMo ASR/TTS
- vLLM
- llama.cpp

| GPU_ARCH | GPUs | CUDA | SM Codes |
|----------|------|------|----------|
| `blackwell` (default) | DGX Spark, RTX 5090 | 13.0/13.1 | sm_120, sm_121 |
| `ampere` | A100, A10, A30, A40, RTX 30xx | 13.0 | sm_80, sm_86 |

## Model Requirements

| Model | Source | Size | Used With |
|-------|--------|------|-----------|
| Nemotron Speech ASR | HuggingFace `nvidia/nemotron-speech-streaming-en-0.6b` (auto-downloaded) | ~2.4GB | English ASR (default) |
| Canary-1B-Flash | HuggingFace `nvidia/canary-1b-flash` (auto-downloaded) | ~3.5GB | Multilingual ASR (es, de, fr, en) |
| Canary-1B-V2 | HuggingFace `nvidia/canary-1b-v2` (auto-downloaded) | ~4GB | 25-language ASR |
| Nemotron-3-Nano Q8 | HuggingFace `unsloth/Nemotron-3-Nano-30B-A3B-GGUF` | ~32GB | llama.cpp on DGX Spark |
| Nemotron-3-Nano Q4 | HuggingFace `unsloth/Nemotron-3-Nano-30B-A3B-GGUF` | ~16GB | llama.cpp on RTX 5090 |
| Nemotron-3-Nano BF16 | HuggingFace `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` | ~72GB | vLLM (cloud/multi-GPU) |
| Magpie TTS | HuggingFace `nvidia/magpie_tts_multilingual_357m` (auto-downloaded) | ~1.4GB | All configurations |

Download LLM models (ASR and TTS are auto-downloaded on first run):

```bash
# GGUF quantized models (Q8 and Q4 variants for llama.cpp)
huggingface-cli download unsloth/Nemotron-3-Nano-30B-A3B-GGUF

# BF16 full precision (for vLLM)
huggingface-cli download nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16
```

## Architecture

For detailed architecture documentation including frame flow, protocols, and timing diagrams, see [docs/streaming-pipeline-architecture.md](docs/streaming-pipeline-architecture.md).

## Troubleshooting

**LLM crashes or stalls**:
- The buffered LLM service uses single-slot operation (`--parallel 1`)
- Ensure adequate VRAM for context size (default 16384 tokens)
- Check for httpx connection issues if generation hangs

**vLLM takes 10-15 minutes to start**:
- This is normal for first startup (model loading, kernel compilation)
- Set `SERVICE_TIMEOUT=900` if needed

**vLLM DNS resolution issues**:
- The container uses `--network=host` in vLLM mode to avoid DNS issues with HuggingFace

