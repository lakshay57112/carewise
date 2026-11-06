"""Turns Timbre's WAV output into MP3, which Telegram plays as a voice message."""
import io
import wave


def wav_to_mp3(wav: bytes) -> bytes | None:
    """Returns MP3 bytes, or None if it can't be converted (the caller then sends text)."""
    try:
        import lameenc
    except ImportError:
        return None
    try:
        with wave.open(io.BytesIO(wav)) as w:
            channels, rate, width = w.getnchannels(), w.getframerate(), w.getsampwidth()
            pcm = w.readframes(w.getnframes())
        if width != 2 or channels not in (1, 2) or not pcm:
            return None
        enc = lameenc.Encoder()
        enc.set_bit_rate(64)
        enc.set_in_sample_rate(rate)
        enc.set_channels(channels)
        enc.set_quality(5)
        return bytes(enc.encode(pcm) + enc.flush())
    except (wave.Error, EOFError, ValueError):
        return None
