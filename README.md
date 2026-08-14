# SportsVoice Global

**Enterprise Commentary Localization Platform**  
Automate sports commentary dubbing across every market — with cultural adaptation, regional slang, and accurate player name pronunciation powered by AI.

---

## Quick Start

```bash
# 0. Install ffmpeg (required for dubbed video output)
brew install ffmpeg             # macOS
# apt install ffmpeg            # Debian/Ubuntu

# 1. Create and activate virtual environment
python -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure API keys
cp .env.example .env
# Edit .env with your ElevenLabs and Gemini API keys

# 4. Launch the app
streamlit run app.py
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SPORTSVOICE GLOBAL                           │
│              Enterprise Commentary Localization Platform            │
└─────────────────────────────────────────────────────────────────────┘

  INPUT                STEP 1              STEP 2              STEP 3              STEP 4
  ─────                ──────              ──────              ──────              ──────
                    ┌──────────┐       ┌──────────┐       ┌──────────┐        ┌──────────┐
  .mp3 / .mp4  ──► │  Scribe  │──────►│  Gemini  │──────►│ ElevenLabs│──────►│  ffmpeg  │
  Football clip     │   STT    │       │  per-line │       │    TTS    │       │   mux    │
                    │ (segments│       │ Adaption,│       │per line,  │       │(pad, or  │
                    │  by      │       │  time-   │       │ natively  │       │hold last │
                    │  pause)  │       │ budgeted │       │pace-fixed │       │frame, to │
                    └──────────┘       └──────────┘       └──────────┘       │close gap)│
                         │                  │                  │             └──────────┘
                         ▼                  ▼                  ▼                  │
                    English text,     Adapted lines,      Localized              ▼
                    segmented by       each budgeted       dub track,       Dubbed Video
                    natural pauses    to its own original  already timed    (.mp4, original
                    + word timestamps    time window          to fit         footage, full
                                           │                                original runtime)
                                  ┌────────┴────────┐
                              Phonetic Dictionary
                              (glossary.json)
                              Applied per line before TTS:
                              "De Bruyne" → "Duh Broy-nuh"
                              "Haaland"   → "Hoh-land"

  No step speeds up or stretches audio to force a timing fit — lines that still
  run long after adaptation + native pace correction simply drift later in the
  timeline rather than being cut or sped up (see Development Notes below).
  Step 4 (video muxing) only runs when the source upload actually contains a
  video stream — audio-only uploads (.mp3/.wav/.m4a) stop at Step 3.

  ┌─────────────────────────────────────┐
  │        CULTURAL PRESETS             │
  │                                     │
  │  🇧🇷  Latin American Spanish        │
  │       • High energy, fast-paced     │
  │       • "¡GOLAZO!", "¡Qué joya!"    │
  │       • eleven_multilingual_v2      │
  │                                     │
  │  🇰🇷  South Korean                  │
  │       • High-pitch goal moments     │
  │       • Analytical build-up style  │
  │       • eleven_multilingual_v2      │
  │                                     │
  │  🇬🇧  UK Tactical                   │
  │       • Analytical, composed        │
  │       • Tactical terminology        │
  │       • eleven_flash_v2_5           │
  └─────────────────────────────────────┘
```

---

## File Structure

```
sports-dubbing-pipeline/
├── app.py              # Streamlit dashboard (full UI)
├── pipeline.py         # Core API functions (transcribe, adapt, synthesize)
├── glossary.json       # Phonetic dictionary (player names + football terms)
├── requirements.txt    # Python dependencies
├── .env.example        # API key template
└── README.md           # This file
```

---

## Configuration

### API Keys

| Key | Source |
|-----|--------|
| `ELEVENLABS_API_KEY` | [elevenlabs.io/settings/api-keys](https://elevenlabs.io/settings/api-keys) |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |

Set these in `.env` or enter them directly in the Streamlit sidebar.

### Voice IDs

The sidebar includes a **voice picker** that lists the voices available in your
ElevenLabs account (premade + any you've added/cloned) once your API key is
entered — no need to hand-type a Voice ID for everyday use. Use the "Use a
custom Voice ID instead" expander to paste an ID for a voice not shown in the
picker (e.g. one from the [shared voice library](https://elevenlabs.io/voice-library)
you haven't added to your account yet).

The `default_voice_id` values in `pipeline.py` are just the picker's starting
selection per cultural preset — replace them with your own voices' IDs if you
want different defaults:

```python
CULTURAL_PRESETS = {
    "Latin American Spanish": {
        "default_voice_id": "YOUR_SPANISH_VOICE_ID",   # ← replace
    },
    "South Korean": {
        "default_voice_id": "YOUR_KOREAN_VOICE_ID",    # ← replace
    },
    "UK Tactical": {
        "default_voice_id": "YOUR_UK_VOICE_ID",        # ← replace
    },
}
```

### Phonetic Dictionary

Edit `glossary.json` directly or use the **Phonetic Dictionary Manager** in the sidebar:

```json
{
  "player_names": {
    "De Bruyne": "Duh Broy-nuh",
    "Haaland": "Hoh-land"
  },
  "football_terms": {
    "Bundesliga": "BOON-des-lee-gah"
  }
}
```

---

## Supported Input Formats

| Format | Extension | Notes |
|--------|-----------|-------|
| MP3 Audio | `.mp3` | Best for pure commentary tracks |
| MP4 Video | `.mp4` | Video stream detected automatically → dubbed video output |
| M4A Audio | `.m4a` | Common from mobile recordings |
| WAV Audio | `.wav` | Uncompressed, highest quality |
| WebM | `.webm` | Browser-recorded clips; dubbed video output if it contains video |

Any upload with a video stream (detected via `ffprobe`, not just file
extension) unlocks Step 5 in the UI: muxing the localized commentary back into
the original footage as a downloadable `.mp4`. Audio-only uploads produce a
localized `.mp3` only, same as before.

---

## Enterprise ROI Summary

### The Problem

Top-tier sports rights holders operate **20+ regional YouTube channels** targeting audiences across LATAM, East Asia, Middle East, and Europe. Today, producing localized commentary requires:

- Hiring regional voice talent (costly, slow)
- Manual script translation and cultural review
- Recording sessions and audio sync (days per video)
- Inconsistent player name pronunciation across markets

**Result:** Most clubs only localize 1–2 markets due to cost. High-value markets (220M Korean fans, 400M Spanish-speaking LATAM fans) are underserved.

### The Solution

SportsVoice Global reduces localization from **days to minutes** and from **thousands of dollars per clip to cents**:

| Metric | Traditional | SportsVoice Global |
|--------|-------------|---------------|
| Time per clip | 2–5 days | ~3 minutes |
| Cost per clip | $500–$2,000 | ~$0.30–$1.50 |
| Markets served | 1–2 | Unlimited |
| Consistency | Variable | 100% repeatable |
| Phonetic accuracy | Depends on talent | Dictionary-enforced |

### Technology Stack

| Feature | Use Case |
|---------|----------|
| **Scribe v1 STT** | Accurate timestamp extraction from live broadcast audio |
| **Eleven Multilingual v2** | Production-quality Spanish and Korean synthesis |
| **Eleven Flash v2.5** | Ultra-low latency for English-market rapid publishing |
| **Voice Library** | Consistent regional presenter personas per market, browsable from the sidebar |
| **Per-line timing budget + native TTS pace control** | Dubbed speech lands close to its original moment without speed-warped audio |
| **ffmpeg mux (pad / freeze-frame)** | Delivers a ready-to-publish dubbed video, not just an audio track |

### Scaling Potential

- **YouTube API integration**: Auto-upload dubbed commentary alongside highlight reels
- **Match-day automation**: Process 90-minute match highlights within 30 minutes of final whistle
- **Multi-club deployment**: One platform serves all regional channels across an entire rights portfolio
- **Brand voice consistency**: Each market maintains a dedicated, persistent voice persona

---

## Development Notes

### Adding a New Cultural Preset

In `pipeline.py`, add an entry to `CULTURAL_PRESETS`:

```python
"Brazilian Portuguese": {
    "language": "Portuguese",
    "language_code": "pt",
    "model_id": "eleven_multilingual_v2",
    "default_voice_id": "YOUR_PT_VOICE_ID",
    "system_prompt": "You are a passionate Brazilian football commentator...",
}
```

### CLI Usage

```python
from pipeline import run_full_pipeline, load_glossary

with open("match_highlight.mp4", "rb") as f:
    audio_bytes = f.read()  # can be audio or video bytes — video is auto-detected

glossary = load_glossary("glossary.json")

result = run_full_pipeline(
    audio_bytes=audio_bytes,
    filename="match_highlight.mp4",
    cultural_preset="Latin American Spanish",
    voice_id="YOUR_VOICE_ID",
    elevenlabs_api_key="sk_...",
    gemini_api_key="AIza...",
    glossary=glossary,
)

with open("output.mp3", "wb") as f:
    f.write(result["audio_bytes"])

if result["video_bytes"]:
    with open("output.mp4", "wb") as f:
        f.write(result["video_bytes"])

print(result["transcript"])
print(result["adapted_script"])
```
