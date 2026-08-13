"""
Core pipeline functions for the Sports Commentary Dubbing & Localization system.
Handles transcription, script adaptation, phonetic correction, and audio synthesis.
"""

import concurrent.futures
import json
import random
import re
import io
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError
from google import genai as google_genai

load_dotenv()

# ---------------------------------------------------------------------------
# Cultural commentary presets
# ---------------------------------------------------------------------------

CULTURAL_PRESETS = {
    "Latin American Spanish": {
        "language": "Spanish",
        "language_code": "es",
        "model_id": "eleven_multilingual_v2",
        "default_voice_id": "cgSgspJ2msm6clMCkdW9",  # replace with preferred ES voice
        "system_prompt": (
            "You are an electrifying Latin American football commentator broadcasting live. "
            "Transform the following English football commentary into passionate Latin American Spanish. "
            "Rules:\n"
            "- Use explosive goal calls: '¡GOOOOOOOOL!' with extended vowels\n"
            "- Inject regional phrases: '¡Qué golazo!', '¡Qué joya!', '¡Increíble!', '¡Espectacular!', '¡Qué barbaridad!'\n"
            "- Use local football vocabulary: 'pelota' (ball), 'arquero' (goalkeeper), 'delantero' (striker), "
            "'volante' (midfielder), 'gambeta' (dribble), 'tiro al arco' (shot on goal)\n"
            "- Fast-paced delivery — short punchy sentences during action, breathless when describing attacks\n"
            "- Reference the crowd and stadium atmosphere\n"
            "- Keep player names as-is (phonetic correction is applied separately)\n"
            "Output only the translated commentary, no explanations."
        ),
    },
    "South Korean": {
        "language": "Korean",
        "language_code": "ko",
        "model_id": "eleven_multilingual_v2",
        "default_voice_id": "pFZP5JQG7iQjIQuC4Bku",  # replace with preferred KO voice
        "system_prompt": (
            "You are a South Korean football broadcaster. "
            "Transform the following English football commentary into Korean. "
            "Rules:\n"
            "- During shots, goals, and big moments: use high-energy exclamations like '와!', '아!', '골이다!', "
            "'대박!', '환상적인 골!'\n"
            "- During tactical build-up play: be analytical and composed, explain positioning and formations\n"
            "- Use polite formal broadcasting Korean (존댓말)\n"
            "- Include Korean football terminology: '골키퍼' (goalkeeper), '미드필더' (midfielder), '공격수' (striker)\n"
            "- Reference the team's strategy and effort\n"
            "- Keep player names phonetically rendered in Korean where natural\n"
            "Output only the translated commentary, no explanations."
        ),
    },
    "UK Tactical": {
        "language": "English",
        "language_code": "en",
        "model_id": "eleven_flash_v2_5",
        "default_voice_id": "JBFqnCBsd6RMkjVDRZzb",  # replace with preferred UK voice
        "system_prompt": (
            "You are a composed, analytical British football broadcaster in the style of Sky Sports. "
            "Transform or rewrite the following English commentary into a tactically insightful style. "
            "Rules:\n"
            "- Analytical and composed tone throughout — no hysteria\n"
            "- Reference formations, pressing triggers, defensive shape, transitions\n"
            "- Use tactical terminology: 'half-space', 'overload', 'press trigger', 'defensive block', "
            "'third-man runs', 'playing through the lines'\n"
            "- Channel the voice of Gary Neville or Cesc Fabregas: measured insight with authority\n"
            "- Add context: why a move worked, what the defending team got wrong\n"
            "- Occasional dry British understatement for big moments ('And that, quite simply, is outstanding')\n"
            "Output only the rewritten commentary, no explanations."
        ),
    },
}

SUPPORTED_PRESETS = list(CULTURAL_PRESETS.keys())

# ---------------------------------------------------------------------------
# Glossary / phonetic dictionary
# ---------------------------------------------------------------------------

def load_glossary(path: str = "glossary.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_glossary(data: dict, path: str = "glossary.json") -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def apply_phonetics(text: str, glossary: dict) -> str:
    """
    Replace player names and football terms with phonetic equivalents.
    Uses word-boundary-aware replacement, longest match first to avoid partial hits.
    """
    all_terms = {}
    all_terms.update(glossary.get("player_names", {}))
    all_terms.update(glossary.get("football_terms", {}))

    # Sort by length descending so longer phrases match before substrings
    sorted_terms = sorted(all_terms.items(), key=lambda x: len(x[0]), reverse=True)

    for original, phonetic in sorted_terms:
        pattern = re.compile(re.escape(original), re.IGNORECASE)
        text = pattern.sub(phonetic, text)

    return text


# ---------------------------------------------------------------------------
# ffmpeg/ffprobe helpers (used by segment timing correction, assembly, and mux)
# ---------------------------------------------------------------------------

def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError(
            "ffmpeg not found — install via `brew install ffmpeg` "
            "(macOS) and make sure it's on your PATH."
        )


def _ffprobe_duration(path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _duration_of_bytes(audio_bytes: bytes, suffix: str = ".mp3") -> float:
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        return _ffprobe_duration(tmp.name)


def ffprobe_duration_of_upload(file_bytes: bytes, filename: str) -> float:
    """Total duration of an original upload (audio or video), by extension."""
    suffix = Path(filename).suffix or ".bin"
    return _duration_of_bytes(file_bytes, suffix=suffix)


def has_video_stream(file_bytes: bytes, filename: str) -> bool:
    """Detect whether the uploaded file actually contains a video stream."""
    if shutil.which("ffprobe") is None:
        return False

    suffix = Path(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp.flush()
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v",
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                tmp.name,
            ],
            capture_output=True, text=True,
        )
    return bool(result.stdout.strip())


# ---------------------------------------------------------------------------
# Step 1: Transcription via ElevenLabs Scribe
# ---------------------------------------------------------------------------

def transcribe_audio(
    audio_bytes: bytes,
    filename: str,
    api_key: str,
) -> dict:
    """
    Transcribe audio using ElevenLabs Scribe STT API.
    Returns a dict with 'text' and 'words' (list of timed word objects).
    """
    client = ElevenLabs(api_key=api_key)

    mime_type = "audio/mpeg"
    ext = Path(filename).suffix.lower()
    mime_map = {
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".webm": "audio/webm",
    }
    mime_type = mime_map.get(ext, "audio/mpeg")

    file_tuple = (filename, io.BytesIO(audio_bytes), mime_type)

    result = client.speech_to_text.convert(
        file=file_tuple,
        model_id="scribe_v1",
        language_code="en",
        timestamps_granularity="word",
        tag_audio_events=False,
    )

    words = []
    if hasattr(result, "words") and result.words:
        for w in result.words:
            words.append({
                "text": w.text if hasattr(w, "text") else str(w),
                "start": w.start if hasattr(w, "start") else 0.0,
                "end": w.end if hasattr(w, "end") else 0.0,
            })

    return {
        "text": result.text if hasattr(result, "text") else str(result),
        "words": words,
    }


def format_transcript_with_timestamps(transcript: dict) -> str:
    """Format word-level timestamps into a readable string."""
    words = transcript.get("words", [])
    if not words:
        return transcript.get("text", "")

    lines = []
    current_line = []
    line_start = None

    for word in words:
        if line_start is None:
            line_start = word["start"]
        current_line.append(word["text"])

        # Break into ~10-word segments for readability
        if len(current_line) >= 10:
            timestamp = f"[{line_start:.1f}s]"
            lines.append(f"{timestamp} {' '.join(current_line)}")
            current_line = []
            line_start = None

    if current_line:
        timestamp = f"[{line_start:.1f}s]" if line_start is not None else "[0.0s]"
        lines.append(f"{timestamp} {' '.join(current_line)}")

    return "\n".join(lines)


_SENTENCE_END_CHARS = (".", "!", "?")
_PAUSE_GAP_SECONDS = 0.6
_MAX_SEGMENT_WORDS = 25
_MAX_SEGMENT_SECONDS = 12.0
_MIN_WORDS_FOR_SENTENCE_BREAK = 3


def segment_transcript(words: list[dict], total_duration: float) -> list[dict]:
    """
    Split word-level timestamps into natural, timed lines for per-line dubbing.

    A segment closes on a sentence-ending word, a pause to the next word, or a
    hard size cap (whichever comes first). Each segment's `target_window` is
    the time available before the *next* segment starts (or before
    `total_duration` for the last one) — using the gap to the next line, not
    just this line's own word span, means the original pause between lines is
    budgeted in as slack rather than wasted.
    """
    if not words:
        return []

    raw_segments = []
    current = []

    def close_segment():
        start = current[0]["start"]
        end = current[-1]["end"]
        text = " ".join(w["text"] for w in current)
        raw_segments.append({"start": start, "end": end, "text": text, "word_count": len(current)})

    for i, word in enumerate(words):
        current.append(word)
        is_last_word = i == len(words) - 1
        ends_sentence = (
            word["text"].rstrip().endswith(_SENTENCE_END_CHARS)
            and len(current) >= _MIN_WORDS_FOR_SENTENCE_BREAK
        )
        gap_to_next = (words[i + 1]["start"] - word["end"]) if not is_last_word else 0.0
        hit_cap = len(current) >= _MAX_SEGMENT_WORDS or (word["end"] - current[0]["start"]) >= _MAX_SEGMENT_SECONDS

        if is_last_word or ends_sentence or gap_to_next > _PAUSE_GAP_SECONDS or hit_cap:
            close_segment()
            current = []

    segments = []
    for i, seg in enumerate(raw_segments):
        next_start = raw_segments[i + 1]["start"] if i + 1 < len(raw_segments) else total_duration
        target_window = max(next_start - seg["start"], 0.1)
        segments.append({
            "index": i,
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
            "word_count": seg["word_count"],
            "target_window": target_window,
        })

    return segments


# ---------------------------------------------------------------------------
# Step 2: Cultural script adaptation via Gemini
# ---------------------------------------------------------------------------

def _format_segments_for_prompt(segments: list[dict], texts: Optional[list[str]] = None) -> str:
    lines = []
    for i, seg in enumerate(segments):
        text = texts[i] if texts is not None else seg["text"]
        lines.append(
            f"[{seg['index']}] window={seg['target_window']:.1f}s "
            f"original_words={seg['word_count']}: \"{text}\""
        )
    return "\n".join(lines)


def adapt_script_segments(
    segments: list[dict],
    cultural_preset: str,
    gemini_api_key: str,
) -> list[str]:
    """
    Adapt every segment of the English transcript in a single batched Gemini
    call, budgeting each line's length against its own original time window
    so the synthesized speech naturally lands close to its original timing —
    instead of adapting the whole script as one blob and force-fitting the
    resulting audio to the clip afterward.
    """
    preset = CULTURAL_PRESETS[cultural_preset]
    system_prompt = preset["system_prompt"]

    client = google_genai.Client(api_key=gemini_api_key)

    full_prompt = (
        f"{system_prompt}\n\n"
        "You are adapting a live sports commentary transcript into timed lines for dubbing. "
        "Below are the segments of the original English commentary in order, each with its "
        "original word count and the time window (in seconds) available for the dubbed line "
        "before the next one must begin. Adapt EACH segment individually following the style "
        "rules above. Keep each adapted line short enough to be spoken naturally within "
        "roughly its target window — use the original word count and window as a pacing guide. "
        f"Return a JSON array of exactly {len(segments)} strings, one adapted line per segment, "
        "in the same order. Do not merge or split segments.\n\n"
        "--- SEGMENTS ---\n"
        f"{_format_segments_for_prompt(segments)}\n"
        "--- END ---"
    )

    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=full_prompt,
        config=google_genai.types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=list[str],
        ),
    )

    adapted = json.loads(response.text)
    if len(adapted) != len(segments):
        raise RuntimeError(
            f"Gemini returned {len(adapted)} adapted lines for {len(segments)} segments — "
            "counts must match."
        )
    return adapted


def shorten_segments_batch(
    segments: list[dict],
    current_texts: list[str],
    overflow_ratios: list[float],
    cultural_preset: str,
    gemini_api_key: str,
) -> list[str]:
    """
    Ask Gemini to shorten a specific set of lines that still overflow their
    time window even at ElevenLabs' native max speed, given how much shorter
    each needs to be. Batched into one call for however many lines need it.
    """
    preset = CULTURAL_PRESETS[cultural_preset]
    system_prompt = preset["system_prompt"]

    client = google_genai.Client(api_key=gemini_api_key)

    lines = []
    for seg, text, ratio in zip(segments, current_texts, overflow_ratios):
        pct_shorter = max((ratio - 1.0) * 100, 5.0)
        lines.append(
            f"[{seg['index']}] window={seg['target_window']:.1f}s "
            f"(needs to be about {pct_shorter:.0f}% shorter): \"{text}\""
        )

    full_prompt = (
        f"{system_prompt}\n\n"
        "The following dubbed lines are running too long for their original time windows "
        "even at maximum natural speaking speed. Rewrite EACH one to be shorter while keeping "
        "the same meaning and style. Return a JSON array of exactly "
        f"{len(segments)} strings, one shortened line per input, in the same order.\n\n"
        "--- LINES TO SHORTEN ---\n" + "\n".join(lines) + "\n--- END ---"
    )

    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=full_prompt,
        config=google_genai.types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=list[str],
        ),
    )

    shortened = json.loads(response.text)
    if len(shortened) != len(segments):
        raise RuntimeError(
            f"Gemini returned {len(shortened)} shortened lines for {len(segments)} segments — "
            "counts must match."
        )
    return shortened


# ---------------------------------------------------------------------------
# Step 3: Audio synthesis via ElevenLabs TTS
# ---------------------------------------------------------------------------

def _retry_on_rate_limit(fn: Callable[[], bytes], max_attempts: int = 5) -> bytes:
    """
    Retry `fn` with exponential backoff when ElevenLabs returns 429
    concurrent_limit_exceeded (a tier-based concurrency cap, not a usage/credit
    limit). Any other error is re-raised immediately — no point retrying an
    auth or bad-request failure.
    """
    for attempt in range(max_attempts):
        try:
            return fn()
        except ApiError as e:
            if e.status_code != 429 or attempt == max_attempts - 1:
                raise
            delay = min(1.0 * (2 ** attempt), 16.0) + random.uniform(0, 0.5)
            time.sleep(delay)


def synthesize_audio(
    text: str,
    voice_id: str,
    api_key: str,
    cultural_preset: str,
    apply_phonetic_correction: bool = True,
    glossary: Optional[dict] = None,
    previous_text: Optional[str] = None,
    next_text: Optional[str] = None,
    speed: float = 1.0,
) -> bytes:
    """
    Generate localized commentary audio using ElevenLabs TTS.
    Applies phonetic correction to the text before synthesis if requested.

    `previous_text`/`next_text` give neighboring-segment context for
    prosodic continuity when synthesizing one line of a longer dub.
    `speed` (0.7-1.2, native ElevenLabs generation-time pacing control,
    default 1.0) nudges delivery pace without any post-hoc audio stretching.
    """
    if apply_phonetic_correction and glossary:
        text = apply_phonetics(text, glossary)

    preset = CULTURAL_PRESETS[cultural_preset]
    model_id = preset["model_id"]
    language_code = preset["language_code"]

    client = ElevenLabs(api_key=api_key)

    def _do_synthesize() -> bytes:
        audio_stream = client.text_to_speech.convert(
            text=text,
            voice_id=voice_id,
            model_id=model_id,
            output_format="mp3_44100_128",
            language_code=language_code if language_code != "en" else None,
            previous_text=previous_text,
            next_text=next_text,
            voice_settings=VoiceSettings(speed=speed) if speed != 1.0 else None,
        )
        return b"".join(audio_stream)

    return _retry_on_rate_limit(_do_synthesize)


# ElevenLabs' documented native speed range (default 1.0); 0.9-1.1 stays clearly
# natural per their guidance, with quality degrading toward the extremes.
_SPEED_MIN = 0.7
_SPEED_MAX = 1.2
_SPEED_TOLERANCE = 0.10

# Conservative default: ElevenLabs caps concurrent requests per subscription
# tier (as low as 2-3 on lower tiers), so this stays safe across accounts.
# _retry_on_rate_limit in synthesize_audio is the backstop for the rest.
_MAX_CONCURRENT_TTS = 2


def _clamp_speed(actual_duration: float, target_window: float) -> float:
    if actual_duration <= 0 or target_window <= 0:
        return 1.0
    needed = actual_duration / target_window
    return max(_SPEED_MIN, min(_SPEED_MAX, needed))


def synthesize_segments_timed(
    segments: list[dict],
    texts: list[str],
    voice_id: str,
    api_key: str,
    cultural_preset: str,
    gemini_api_key: str,
    apply_phonetic_correction: bool = True,
    glossary: Optional[dict] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Synthesize every segment, correcting timing with ElevenLabs' native
    generation-time speed control rather than stretching audio afterward.
    Segments still overflowing their window even at max native speed get one
    batched rewrite from Gemini and a final resynthesis. Parallelized since
    segments only depend on each other's *text* (already known upfront), not
    on each other's audio.

    Returns one dict per segment: index, start, target_window, text (final,
    possibly shortened), audio_bytes, actual_duration, speed_used.
    """
    def report(msg: str):
        if progress_callback:
            progress_callback(msg)

    n = len(segments)

    def prev_next_text(i: int):
        prev_text = texts[i - 1] if i > 0 else None
        next_text = texts[i + 1] if i + 1 < n else None
        return prev_text, next_text

    def synth_one(i: int, text: str, speed: float = 1.0):
        prev_text, next_text = prev_next_text(i)
        audio = synthesize_audio(
            text=text,
            voice_id=voice_id,
            api_key=api_key,
            cultural_preset=cultural_preset,
            apply_phonetic_correction=apply_phonetic_correction,
            glossary=glossary,
            previous_text=prev_text,
            next_text=next_text,
            speed=speed,
        )
        return audio, _duration_of_bytes(audio)

    results = [
        {
            "index": segments[i]["index"],
            "start": segments[i]["start"],
            "target_window": segments[i]["target_window"],
            "text": texts[i],
            "audio_bytes": None,
            "actual_duration": None,
            "speed_used": 1.0,
        }
        for i in range(n)
    ]

    # Pass 1: synthesize every segment at natural pace, measure duration.
    report(f"Synthesizing {n} line(s)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_TTS) as pool:
        futures = {pool.submit(synth_one, i, texts[i]): i for i in range(n)}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            i = futures[future]
            audio, duration = future.result()
            results[i]["audio_bytes"] = audio
            results[i]["actual_duration"] = duration
            done += 1
            report(f"Synthesized {done}/{n} line(s)...")

    # Pass 2: native speed correction for segments off by more than tolerance.
    needs_speed_fix = [
        i for i in range(n)
        if abs(results[i]["actual_duration"] / results[i]["target_window"] - 1.0) > _SPEED_TOLERANCE
    ]
    if needs_speed_fix:
        report(f"Applying timing correction to {len(needs_speed_fix)} line(s)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_TTS) as pool:
            futures = {}
            for i in needs_speed_fix:
                speed = _clamp_speed(results[i]["actual_duration"], results[i]["target_window"])
                futures[pool.submit(synth_one, i, texts[i], speed)] = (i, speed)
            for future in concurrent.futures.as_completed(futures):
                i, speed = futures[future]
                audio, duration = future.result()
                results[i]["audio_bytes"] = audio
                results[i]["actual_duration"] = duration
                results[i]["speed_used"] = speed

    # Pass 3: segments still overflowing even at max native speed get one
    # batched shorten-and-retry from Gemini, then a final synthesis pass.
    still_overflowing = [
        i for i in range(n)
        if results[i]["actual_duration"] > results[i]["target_window"] * (1 + _SPEED_TOLERANCE)
        and results[i]["speed_used"] >= _SPEED_MAX - 1e-6
    ]
    if still_overflowing:
        report(f"Rewriting {len(still_overflowing)} line(s) still too long...")
        overflow_ratios = [
            results[i]["actual_duration"] / results[i]["target_window"] for i in still_overflowing
        ]
        shortened = shorten_segments_batch(
            segments=[segments[i] for i in still_overflowing],
            current_texts=[results[i]["text"] for i in still_overflowing],
            overflow_ratios=overflow_ratios,
            cultural_preset=cultural_preset,
            gemini_api_key=gemini_api_key,
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_TTS) as pool:
            futures = {
                pool.submit(synth_one, i, shortened[pos]): i
                for pos, i in enumerate(still_overflowing)
            }
            shortened_by_index = {i: shortened[pos] for pos, i in enumerate(still_overflowing)}
            for future in concurrent.futures.as_completed(futures):
                i = futures[future]
                audio, duration = future.result()
                results[i]["text"] = shortened_by_index[i]
                results[i]["audio_bytes"] = audio
                results[i]["actual_duration"] = duration
                results[i]["speed_used"] = 1.0
                # One more native-speed nudge if the shortened line is still
                # slightly off — never a second content rewrite.
                if abs(duration / results[i]["target_window"] - 1.0) > _SPEED_TOLERANCE:
                    speed = _clamp_speed(duration, results[i]["target_window"])
                    audio2, duration2 = synth_one(i, results[i]["text"], speed)
                    results[i]["audio_bytes"] = audio2
                    results[i]["actual_duration"] = duration2
                    results[i]["speed_used"] = speed

    report("Synthesis complete.")
    return results


def list_voices(api_key: str) -> list[dict]:
    """
    List voices available to the ElevenLabs account (premade + any added/cloned),
    for populating a voice picker instead of requiring a hand-typed Voice ID.
    """
    client = ElevenLabs(api_key=api_key)
    response = client.voices.search(page_size=100, sort="name", sort_direction="asc")

    voices = []
    for v in getattr(response, "voices", []) or []:
        voices.append({
            "voice_id": v.voice_id if hasattr(v, "voice_id") else str(v),
            "name": v.name if hasattr(v, "name") else "Unknown",
            "category": v.category if hasattr(v, "category") else "",
        })
    return voices


# ---------------------------------------------------------------------------
# Assemble the timed dub track from synthesized segments
# ---------------------------------------------------------------------------

def assemble_dub_track(segment_results: list[dict]) -> bytes:
    """
    Place each segment's synthesized audio at its planned start time and
    concatenate into one continuous track, silence-padding any gaps. A
    segment only starts later than its original timestamp if an earlier
    segment overflowed its own window — audio is never sped up or cut to
    force a fit; only the *placement* drifts.
    """
    _require_ffmpeg()

    if not segment_results:
        raise ValueError("No segments to assemble.")

    ordered = sorted(segment_results, key=lambda s: s["index"])

    planned_start = []
    cursor = 0.0
    for i, seg in enumerate(ordered):
        start = seg["start"] if i == 0 else max(seg["start"], cursor)
        planned_start.append(start)
        cursor = start + seg["actual_duration"]

    with tempfile.TemporaryDirectory() as tmpdir:
        # Use the concat *filter*, not the concat demuxer: each input is
        # decoded via its own -i, so silence (generated inline) and mp3
        # segments can mix freely. The concat demuxer instead byte-concatenates
        # raw streams and expects them to be one homogeneous format — mixing
        # raw .mp3 and raw .aac there corrupts the result.
        cmd = ["ffmpeg", "-y"]
        input_labels = []
        cursor = 0.0

        for i, seg in enumerate(ordered):
            gap = planned_start[i] - cursor
            if gap > 0.02:
                cmd += ["-f", "lavfi", "-t", f"{gap:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
                input_labels.append(f"[{len(input_labels)}:a]")

            seg_path = os.path.join(tmpdir, f"segment_{i}.mp3")
            with open(seg_path, "wb") as f:
                f.write(seg["audio_bytes"])
            cmd += ["-i", seg_path]
            input_labels.append(f"[{len(input_labels)}:a]")

            cursor = planned_start[i] + seg["actual_duration"]

        concat_filter = "".join(input_labels) + f"concat=n={len(input_labels)}:v=0:a=1[aout]"
        out_path = os.path.join(tmpdir, "assembled.m4a")
        cmd += [
            "-filter_complex", concat_filter,
            "-map", "[aout]",
            "-c:a", "aac", "-b:a", "192k",
            out_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            stderr_tail = "\n".join(result.stderr.strip().splitlines()[-20:])
            raise RuntimeError(f"ffmpeg failed to assemble dub track:\n{stderr_tail}")

        with open(out_path, "rb") as f:
            return f.read()


# ---------------------------------------------------------------------------
# Step 4: Mux dubbed audio into the original video
# ---------------------------------------------------------------------------

def mux_video_with_dubbed_audio(
    video_bytes: bytes,
    video_filename: str,
    audio_bytes: bytes,
) -> bytes:
    """
    Replace the original video's audio with the dubbed commentary. The dub
    track is already correctly timed by construction (assemble_dub_track), so
    this never speeds up or stretches audio — it only closes the (typically
    small) gap between the dub's total length and the source video's:
    trailing silence pads a shorter dub, and a frozen last frame covers a
    longer one.
    """
    _require_ffmpeg()

    suffix = Path(video_filename).suffix or ".mp4"
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, f"source{suffix}")
        audio_path = os.path.join(tmpdir, "dub.m4a")
        out_path = os.path.join(tmpdir, "dubbed.mp4")

        with open(video_path, "wb") as f:
            f.write(video_bytes)
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        video_duration = _ffprobe_duration(video_path)
        audio_duration = _ffprobe_duration(audio_path)

        if audio_duration <= video_duration + 0.05:
            # Dub finishes at or before the source — pad with trailing
            # silence and keep the original video untouched (no re-encode).
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex", f"[1:a]apad=whole_dur={video_duration:.3f}[aout]",
                "-map", "0:v:0",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                out_path,
            ]
        else:
            # Dub runs longer than the source — hold the last frame to cover
            # the gap rather than cutting the dub short or speeding it up.
            overflow = audio_duration - video_duration
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex", f"[0:v]tpad=stop_mode=clone:stop_duration={overflow:.3f}[vout]",
                "-map", "[vout]",
                "-map", "1:a:0",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-c:a", "aac",
                "-b:a", "192k",
                out_path,
            ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            stderr_tail = "\n".join(result.stderr.strip().splitlines()[-20:])
            raise RuntimeError(f"ffmpeg failed to mux video and audio:\n{stderr_tail}")

        with open(out_path, "rb") as f:
            return f.read()


# ---------------------------------------------------------------------------
# Full pipeline (single call convenience wrapper)
# ---------------------------------------------------------------------------

def run_full_pipeline(
    audio_bytes: bytes,
    filename: str,
    cultural_preset: str,
    voice_id: str,
    elevenlabs_api_key: str,
    gemini_api_key: str,
    glossary: Optional[dict] = None,
) -> dict:
    """
    Run the complete end-to-end pipeline. Each line of the adapted script is
    budgeted and synthesized against its own original timestamp window
    (see segment_transcript/synthesize_segments_timed) rather than the whole
    script being adapted and synthesized as one blob and stretched to fit
    afterward.

    Returns a dict with transcript, adapted_script (lines joined with
    newlines, for display/back-compat), segments, and audio_bytes.
    """
    # Step 1: Transcribe
    transcript = transcribe_audio(audio_bytes, filename, elevenlabs_api_key)
    transcript_text = transcript["text"]
    timestamp_view = format_transcript_with_timestamps(transcript)

    total_duration = ffprobe_duration_of_upload(audio_bytes, filename)
    segments = segment_transcript(transcript["words"], total_duration)

    # Step 2: Adapt, budgeted per segment
    adapted_texts = adapt_script_segments(segments, cultural_preset, gemini_api_key)

    # Step 3: Synthesize each segment with native timing correction
    segment_results = synthesize_segments_timed(
        segments=segments,
        texts=adapted_texts,
        voice_id=voice_id,
        api_key=elevenlabs_api_key,
        cultural_preset=cultural_preset,
        gemini_api_key=gemini_api_key,
        apply_phonetic_correction=True,
        glossary=glossary,
    )
    audio_out = assemble_dub_track(segment_results)

    # Step 4: Mux into the original video, if the source file has a video stream
    video_out = None
    if has_video_stream(audio_bytes, filename):
        video_out = mux_video_with_dubbed_audio(audio_bytes, filename, audio_out)

    return {
        "transcript": transcript_text,
        "timestamp_view": timestamp_view,
        "adapted_script": "\n".join(r["text"] for r in sorted(segment_results, key=lambda r: r["index"])),
        "segments": segment_results,
        "audio_bytes": audio_out,
        "video_bytes": video_out,
    }
