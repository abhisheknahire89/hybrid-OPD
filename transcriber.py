import abc
import os
import torch
import torchaudio
import numpy as np
from transformers import AutoModel

# Tunable buffer window for live transcription.
# 1.0s provides a good balance between low lag and high transcription accuracy.
CHUNK_SECONDS = 1.0
BYTES_PER_CHUNK = int(CHUNK_SECONDS * 16000 * 2) # 16kHz, 16-bit mono

class Transcriber(abc.ABC):
    @abc.abstractmethod
    def start_session(self, language_code: str):
        pass

    @abc.abstractmethod
    def feed_audio(self, pcm_chunk: bytes):
        pass

    @abc.abstractmethod
    def on_partial(self, callback):
        pass

    @abc.abstractmethod
    def stop_session(self) -> str:
        pass


class IndicConformerTranscriber(Transcriber):
    def __init__(self):
        # Preload the model once at backend startup
        # We use the ai4bharat model directly via transformers
        self.model = AutoModel.from_pretrained("ai4bharat/indic-conformer-600m-multilingual", trust_remote_code=True)
        self.session_active = False
        self.language_code = 'hi'
        self.audio_buffer = bytearray()
        self.partial_callback = None

    def start_session(self, language_code: str):
        self.session_active = True
        self.language_code = language_code
        self.audio_buffer.clear()

    def feed_audio(self, pcm_chunk: bytes):
        if not self.session_active:
            return
        
        self.audio_buffer.extend(pcm_chunk)
        
        # Process every CHUNK_SECONDS (e.g. 32000 bytes for 1.0s)
        if len(self.audio_buffer) >= BYTES_PER_CHUNK:
            self._process_buffer()

    def on_partial(self, callback):
        self.partial_callback = callback

    def _process_buffer(self):
        if not self.audio_buffer:
            return

        # Convert accumulated PCM bytes directly into a float32 tensor
        audio_array = np.frombuffer(self.audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0
        wav_tensor = torch.from_numpy(audio_array).unsqueeze(0) # shape: [1, seq_len]
        
        # We clear buffer here so next chunk starts fresh.
        self.audio_buffer.clear()

        try:
            # Transcribe with RNNT decoding
            transcription_rnnt = self.model(wav_tensor, self.language_code, "rnnt")
            text = transcription_rnnt[0] if isinstance(transcription_rnnt, list) else transcription_rnnt
            if self.partial_callback and text:
                self.partial_callback(text)
        except Exception as e:
            print(f"Transcription error: {e}")

    def stop_session(self) -> str:
        self.session_active = False
        
        # Process any remaining audio
        if len(self.audio_buffer) > 0:
            self._process_buffer()
            
        return ""
