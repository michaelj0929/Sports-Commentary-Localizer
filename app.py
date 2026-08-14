"""
SportsVoice Global — Enterprise Commentary Localization Platform
Streamlit Web Dashboard
"""

import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import pipeline as pl

load_dotenv()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="SportsVoice Global | Commentary Localization",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------

def init_state():
    defaults = {
        "transcript": None,
        "timestamp_view": None,
        "total_duration": None,
        "segments": None,        # list of dicts: index/start/end/text/word_count/target_window/adapted
        "segment_results": None,  # output of synthesize_segments_timed
        "audio_bytes": None,
        "video_bytes": None,
        "has_video": None,
        "has_video_file_id": None,
        "glossary": None,
        "voice_options": None,
        "voice_cache_key": None,
        "voice_fetch_error": None,
        "pipeline_step": 0,  # 0=upload, 1=transcribed, 2=adapted, 3=synthesized, 4=video
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_glossary_cached():
    if st.session_state.glossary is None:
        try:
            st.session_state.glossary = pl.load_glossary("glossary.json")
        except FileNotFoundError:
            st.session_state.glossary = {"player_names": {}, "football_terms": {}}
    return st.session_state.glossary


def save_and_refresh_glossary(data: dict):
    pl.save_glossary(data, "glossary.json")
    st.session_state.glossary = data


def reset_pipeline():
    for key in [
        "transcript", "timestamp_view", "total_duration",
        "segments", "segment_results", "audio_bytes", "video_bytes",
    ]:
        st.session_state[key] = None
    st.session_state.pipeline_step = 0


def fetch_voices_cached(api_key: str, force: bool = False):
    """Fetch the ElevenLabs voice library once per API key, cached in session state."""
    if force or st.session_state.voice_cache_key != api_key or st.session_state.voice_options is None:
        try:
            st.session_state.voice_options = pl.list_voices(api_key)
            st.session_state.voice_cache_key = api_key
            st.session_state.voice_fetch_error = None
        except Exception as e:
            st.session_state.voice_options = None
            st.session_state.voice_fetch_error = str(e)
    return st.session_state.voice_options, st.session_state.voice_fetch_error

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image(
        "https://elevenlabs.io/favicon.ico",
        width=32,
    )
    st.title("⚽ SportsVoice Global")
    st.caption("Enterprise Commentary Localization Platform")
    st.divider()

    # --- API Keys ---
    st.subheader("🔑 API Keys")
    el_key = st.text_input(
        "ElevenLabs API Key",
        value=os.getenv("ELEVENLABS_API_KEY", ""),
        type="password",
        placeholder="sk_...",
        help="Your ElevenLabs API key from elevenlabs.io/settings",
    )
    gemini_key = st.text_input(
        "Gemini API Key",
        value=os.getenv("GEMINI_API_KEY", ""),
        type="password",
        placeholder="AIza...",
        help="Google AI Studio API key for script adaptation",
    )

    st.divider()

    # --- Language & Style ---
    st.subheader("🌍 Localization Settings")
    cultural_preset = st.selectbox(
        "Cultural Commentary Style",
        options=pl.SUPPORTED_PRESETS,
        index=0,
        help="Each style uses a region-specific LLM prompt and TTS voice model",
    )

    preset_info = pl.CULTURAL_PRESETS[cultural_preset]
    st.info(
        f"**Target Language:** {preset_info['language']}\n\n"
        f"**TTS Model:** `{preset_info['model_id']}`"
    )

    st.markdown("**Voice**")
    default_voice_id = preset_info["default_voice_id"]
    picker_voice_id = default_voice_id

    if not el_key:
        st.info("Enter your ElevenLabs API key above to browse your voice library.")
    else:
        header_col, refresh_col = st.columns([4, 1])
        header_col.caption("Choose a voice from your ElevenLabs library")
        if refresh_col.button("🔄", help="Refresh voice list", key="refresh_voices"):
            fetch_voices_cached(el_key, force=True)

        voices, voice_error = fetch_voices_cached(el_key)
        if voice_error:
            st.warning(f"Couldn't load your voice library: {voice_error}")
        elif voices:
            default_index = next(
                (i for i, v in enumerate(voices) if v["voice_id"] == default_voice_id), 0
            )
            selected_voice = st.selectbox(
                "Voice",
                options=voices,
                index=default_index,
                format_func=lambda v: f"{v['name']} ({v['category']})" if v["category"] else v["name"],
                label_visibility="collapsed",
            )
            picker_voice_id = selected_voice["voice_id"]
        else:
            st.warning("No voices found in your ElevenLabs account.")

    with st.expander("Use a custom Voice ID instead"):
        custom_voice_id = st.text_input(
            "Voice ID",
            value="",
            placeholder="Paste a Voice ID not listed above",
            help="Overrides the picker above if filled in. Find voices at elevenlabs.io/voice-library",
            label_visibility="collapsed",
        )

    voice_id = custom_voice_id.strip() if custom_voice_id.strip() else picker_voice_id

    st.divider()

    # --- Phonetic Dictionary Manager ---
    with st.expander("📖 Phonetic Dictionary Manager", expanded=False):
        glossary = load_glossary_cached()
        all_entries = {
            **glossary.get("player_names", {}),
            **glossary.get("football_terms", {}),
        }

        st.caption(f"{len(all_entries)} entries loaded")

        if all_entries:
            search = st.text_input("Filter entries", placeholder="Search...")
            filtered = {
                k: v for k, v in all_entries.items()
                if search.lower() in k.lower() or search.lower() in v.lower()
            } if search else all_entries

            for original, phonetic in list(filtered.items())[:15]:
                col1, col2, col3 = st.columns([3, 3, 1])
                col1.text(original)
                col2.text(phonetic)
                if col3.button("✕", key=f"del_{original}", help="Remove"):
                    for section in ["player_names", "football_terms"]:
                        if original in glossary.get(section, {}):
                            del glossary[section][original]
                    save_and_refresh_glossary(glossary)
                    st.rerun()

            if len(filtered) > 15:
                st.caption(f"...and {len(filtered) - 15} more")

        st.markdown("**Add New Entry**")
        new_orig = st.text_input("Original name / term", key="new_orig")
        new_phon = st.text_input("Phonetic spelling", key="new_phon")
        entry_type = st.radio(
            "Category", ["player_names", "football_terms"], horizontal=True
        )
        if st.button("Add Entry", use_container_width=True):
            if new_orig and new_phon:
                if entry_type not in glossary:
                    glossary[entry_type] = {}
                glossary[entry_type][new_orig] = new_phon
                save_and_refresh_glossary(glossary)
                st.success(f"Added: {new_orig} → {new_phon}")
                st.rerun()
            else:
                st.warning("Both fields required.")

# ---------------------------------------------------------------------------
# Main Panel
# ---------------------------------------------------------------------------

st.title("🎙️ SportsVoice Global")
st.caption(
    "Enterprise-grade sports commentary localization — "
    "cultural adaptation, regional slang, and accurate player name pronunciation at scale."
)

# Pipeline progress bar — filled in below, once has_video is known for this run
progress_placeholder = st.container()

st.divider()

# ---------------------------------------------------------------------------
# Step 1: Upload
# ---------------------------------------------------------------------------

col_upload, col_info = st.columns([3, 2])

with col_upload:
    st.subheader("Step 1 — Upload Commentary Clip")
    uploaded_file = st.file_uploader(
        "Drag & drop your sports audio or video clip",
        type=["mp3", "mp4", "m4a", "wav", "webm"],
        on_change=reset_pipeline,
        help="Supports: MP3, MP4, M4A, WAV, WebM. Max 25MB recommended.",
    )

with col_info:
    st.subheader("Pipeline Overview")
    st.markdown(
        """
        **How it works:**
        1. **Scribe STT** extracts a timed English transcript, line by line
        2. **Gemini** adapts each line to your regional style, budgeted to fit its own original timing
        3. **ElevenLabs TTS** synthesizes each line, correcting pace natively when needed

        > Phonetic corrections are injected automatically before synthesis
        > using your Phonetic Dictionary.
        """
    )

if uploaded_file:
    file_bytes = uploaded_file.read()
    file_size_mb = len(file_bytes) / (1024 * 1024)
    st.success(
        f"Loaded: **{uploaded_file.name}** ({file_size_mb:.1f} MB)"
    )

    if st.session_state.has_video_file_id != uploaded_file.file_id:
        st.session_state.has_video = pl.has_video_stream(file_bytes, uploaded_file.name)
        st.session_state.has_video_file_id = uploaded_file.file_id
    has_video = st.session_state.has_video

    with progress_placeholder:
        step_labels = ["Upload", "Transcribe", "Adapt", "Synthesize"]
        if has_video:
            step_labels.append("Video")
        progress_cols = st.columns(len(step_labels))
        for i, (col, label) in enumerate(zip(progress_cols, step_labels)):
            done = st.session_state.pipeline_step > i
            active = st.session_state.pipeline_step == i
            icon = "✅" if done else ("🔄" if active else "⏳")
            col.metric(label=f"{icon} Step {i+1}", value=label)

    st.divider()

    # -----------------------------------------------------------------------
    # Step 2: Transcribe
    # -----------------------------------------------------------------------

    st.subheader("Step 2 — Transcribe English Commentary")

    if st.session_state.transcript is None:
        if not el_key:
            st.warning("Add your ElevenLabs API key in the sidebar to transcribe.")
        else:
            if st.button("🎤 Transcribe with ElevenLabs Scribe", use_container_width=True, type="primary"):
                with st.spinner("Transcribing audio with ElevenLabs Scribe..."):
                    try:
                        result = pl.transcribe_audio(file_bytes, uploaded_file.name, el_key)
                        total_duration = pl.ffprobe_duration_of_upload(file_bytes, uploaded_file.name)
                        segments = pl.segment_transcript(result["words"], total_duration)
                        for seg in segments:
                            seg["adapted"] = None

                        st.session_state.transcript = result["text"]
                        st.session_state.timestamp_view = pl.format_transcript_with_timestamps(result)
                        st.session_state.total_duration = total_duration
                        st.session_state.segments = segments
                        st.session_state.pipeline_step = 1
                        st.rerun()
                    except Exception as e:
                        st.error(f"Transcription failed: {e}")

    if st.session_state.transcript:
        tab_clean, tab_timed = st.tabs(["Clean Transcript", "Timestamped View"])
        with tab_clean:
            st.text_area(
                "English Transcript",
                value=st.session_state.transcript,
                height=180,
                key="transcript_display",
            )
        with tab_timed:
            st.text_area(
                "Word-level Timestamps",
                value=st.session_state.timestamp_view or st.session_state.transcript,
                height=180,
                key="timestamp_display",
            )

        st.divider()

        # -------------------------------------------------------------------
        # Step 3: Cultural Script Adaptation
        # -------------------------------------------------------------------

        st.subheader("Step 3 — Cultural Script Adaptation")
        st.caption(
            "Each line is budgeted against its own original time window (shown below) "
            "so the dubbed speech naturally lands close to where it should — no audio "
            "speed-warping needed later to force a fit."
        )

        segments = st.session_state.segments
        already_adapted = bool(segments) and segments[0].get("adapted") is not None

        if not already_adapted:
            if not gemini_key:
                st.warning("Add your Gemini API key in the sidebar.")
            else:
                if st.button(
                    f"✨ Adapt {len(segments)} line(s) for {cultural_preset}",
                    use_container_width=True,
                    type="primary",
                ):
                    with st.spinner(f"Adapting {len(segments)} line(s) with Gemini..."):
                        try:
                            adapted_texts = pl.adapt_script_segments(segments, cultural_preset, gemini_key)
                            for seg, text in zip(segments, adapted_texts):
                                seg["adapted"] = text
                            st.session_state.segments = segments
                            st.session_state.pipeline_step = 2
                            st.rerun()
                        except Exception as e:
                            st.error(f"Adaptation failed: {e}")
        else:
            table = pd.DataFrame([
                {
                    "Start": f"{seg['start']:.1f}s",
                    "Original (EN)": seg["text"],
                    "Window (s)": round(seg["target_window"], 1),
                    "Adapted": seg["adapted"],
                }
                for seg in segments
            ])
            edited = st.data_editor(
                table,
                use_container_width=True,
                hide_index=True,
                disabled=["Start", "Original (EN)", "Window (s)"],
                column_config={
                    "Adapted": st.column_config.TextColumn(width="large"),
                    "Original (EN)": st.column_config.TextColumn(width="large"),
                },
                key="segments_editor",
            )
            for seg, adapted_text in zip(segments, edited["Adapted"].tolist()):
                seg["adapted"] = adapted_text
            st.session_state.segments = segments

            if st.button("🔄 Re-adapt All Lines", use_container_width=False):
                for seg in st.session_state.segments:
                    seg["adapted"] = None
                st.session_state.audio_bytes = None
                st.session_state.video_bytes = None
                st.session_state.segment_results = None
                st.session_state.pipeline_step = 1
                st.rerun()

        st.divider()

    # -----------------------------------------------------------------------
    # Step 4: Synthesize Localized Audio
    # -----------------------------------------------------------------------

    segments_adapted = bool(st.session_state.segments) and st.session_state.segments[0].get("adapted") is not None

    if segments_adapted:
        st.subheader("Step 4 — Synthesize Localized Commentary")
        st.caption(
            "Each line is synthesized against its own time budget, with timing corrected "
            "via ElevenLabs' native speed control (not audio stretching) when needed."
        )

        gloss = load_glossary_cached()

        col_synth, col_prev = st.columns([2, 3])

        with col_synth:
            st.markdown("**Synthesis Settings**")
            apply_phonetics_flag = st.checkbox(
                "Apply Phonetic Dictionary",
                value=True,
                help="Replace player names with phonetic spellings before sending to TTS",
            )

            if apply_phonetics_flag and gloss:
                preview_text = pl.apply_phonetics(
                    st.session_state.segments[0]["adapted"], gloss
                )
                with st.expander("Preview phonetic corrections (first line)"):
                    st.text(preview_text)

            if not el_key:
                st.warning("ElevenLabs API key required.")
            else:
                if st.button(
                    "🔊 Generate Localized Commentary",
                    use_container_width=True,
                    type="primary",
                    disabled=not voice_id,
                ):
                    status_placeholder = st.empty()
                    try:
                        with st.spinner("Synthesizing timed commentary with ElevenLabs TTS..."):
                            segment_results = pl.synthesize_segments_timed(
                                segments=st.session_state.segments,
                                texts=[seg["adapted"] for seg in st.session_state.segments],
                                voice_id=voice_id,
                                api_key=el_key,
                                cultural_preset=cultural_preset,
                                gemini_api_key=gemini_key,
                                apply_phonetic_correction=apply_phonetics_flag,
                                glossary=gloss if apply_phonetics_flag else None,
                                progress_callback=status_placeholder.caption,
                            )
                            status_placeholder.caption("Assembling final dub track...")
                            audio_out = pl.assemble_dub_track(segment_results)

                        status_placeholder.empty()
                        st.session_state.segment_results = segment_results
                        st.session_state.audio_bytes = audio_out
                        st.session_state.video_bytes = None
                        st.session_state.pipeline_step = 3
                        st.rerun()
                    except Exception as e:
                        st.error(f"Synthesis failed: {e}")

        with col_prev:
            if st.session_state.audio_bytes:
                st.markdown("**🎧 Localized Commentary Preview**")
                st.audio(st.session_state.audio_bytes, format="audio/mp3")

                st.markdown("**Original Upload (for alignment check)**")
                st.audio(file_bytes, format="audio/mpeg")

                st.download_button(
                    label="⬇️ Download Localized Audio",
                    data=st.session_state.audio_bytes,
                    file_name=f"localized_{cultural_preset.replace(' ', '_').lower()}.mp3",
                    mime="audio/mpeg",
                    use_container_width=True,
                )

                if st.session_state.segment_results:
                    n = len(st.session_state.segment_results)
                    n_speed_corrected = sum(
                        1 for r in st.session_state.segment_results if r["speed_used"] != 1.0
                    )
                    st.caption(
                        f"{n} line(s) synthesized · {n_speed_corrected} needed native timing correction"
                    )

                st.success(
                    f"{cultural_preset} commentary generated "
                    f"({len(st.session_state.audio_bytes) / 1024:.0f} KB)"
                    + (" — continue to Step 5 for the dubbed video." if has_video else " Pipeline complete!")
                )
            else:
                st.info("Generate audio to preview it here alongside your original clip.")

        st.divider()

        # ---------------------------------------------------------------
        # Step 5: Generate Dubbed Video
        # ---------------------------------------------------------------

        if has_video and st.session_state.audio_bytes:
            st.subheader("Step 5 — Generate Dubbed Video")
            st.caption(
                "Mux the timed dub track into the original footage. The dub is already "
                "correctly paced, so this just closes any small remaining gap: silence "
                "padding if it finished early, or a held final frame if it ran slightly long."
            )

            col_video_gen, col_video_prev = st.columns([2, 3])

            with col_video_gen:
                if st.button(
                    "🎬 Generate Dubbed Video",
                    use_container_width=True,
                    type="primary",
                ):
                    with st.spinner("Muxing dubbed audio into the original video..."):
                        try:
                            video_out = pl.mux_video_with_dubbed_audio(
                                video_bytes=file_bytes,
                                video_filename=uploaded_file.name,
                                audio_bytes=st.session_state.audio_bytes,
                            )
                            st.session_state.video_bytes = video_out
                            st.session_state.pipeline_step = 4
                            st.rerun()
                        except Exception as e:
                            st.error(f"Video muxing failed: {e}")

            with col_video_prev:
                if st.session_state.video_bytes:
                    st.markdown("**🎬 Dubbed Video Preview**")
                    st.video(st.session_state.video_bytes)

                    st.download_button(
                        label="⬇️ Download Dubbed Video",
                        data=st.session_state.video_bytes,
                        file_name=f"dubbed_{cultural_preset.replace(' ', '_').lower()}.mp4",
                        mime="video/mp4",
                        use_container_width=True,
                    )

                    st.success("Pipeline complete! Dubbed video ready to download.")
                else:
                    st.info("Generate the dubbed video to preview it here.")

else:
    with progress_placeholder:
        step_labels = ["Upload", "Transcribe", "Adapt", "Synthesize"]
        progress_cols = st.columns(len(step_labels))
        for i, (col, label) in enumerate(zip(progress_cols, step_labels)):
            icon = "🔄" if st.session_state.pipeline_step == i else "⏳"
            col.metric(label=f"{icon} Step {i+1}", value=label)

    # Landing state — no file uploaded yet
    st.markdown("")
    st.markdown(
        """
        <div style="
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            border-radius: 12px;
            padding: 2.5rem;
            text-align: center;
            border: 1px solid #0f3460;
        ">
            <h2 style="color: #e94560; margin-bottom: 0.5rem;">
                🏆 SportsVoice Global
            </h2>
            <p style="color: #a8b2d8; font-size: 1.1rem;">
                Upload a sports commentary clip to begin the localization pipeline.
            </p>
            <br/>
            <div style="display: flex; justify-content: space-around; flex-wrap: wrap; gap: 1rem;">
                <div style="background: rgba(255,255,255,0.05); border-radius: 8px; padding: 1rem 1.5rem;">
                    <div style="color: #e94560; font-size: 1.8rem;">🎤</div>
                    <div style="color: #ccd6f6; font-weight: bold;">Scribe STT</div>
                    <div style="color: #8892b0; font-size: 0.85rem;">Word-level timestamps</div>
                </div>
                <div style="background: rgba(255,255,255,0.05); border-radius: 8px; padding: 1rem 1.5rem;">
                    <div style="color: #e94560; font-size: 1.8rem;">✨</div>
                    <div style="color: #ccd6f6; font-weight: bold;">Gemini</div>
                    <div style="color: #8892b0; font-size: 0.85rem;">Cultural adaptation</div>
                </div>
                <div style="background: rgba(255,255,255,0.05); border-radius: 8px; padding: 1rem 1.5rem;">
                    <div style="color: #e94560; font-size: 1.8rem;">🗣️</div>
                    <div style="color: #ccd6f6; font-weight: bold;">ElevenLabs TTS</div>
                    <div style="color: #8892b0; font-size: 0.85rem;">Expressive multilingual voice</div>
                </div>
                <div style="background: rgba(255,255,255,0.05); border-radius: 8px; padding: 1rem 1.5rem;">
                    <div style="color: #e94560; font-size: 1.8rem;">📖</div>
                    <div style="color: #ccd6f6; font-weight: bold;">Phonetic Dictionary</div>
                    <div style="color: #8892b0; font-size: 0.85rem;">Correct name pronunciation</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
