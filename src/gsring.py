"""Ядро GSRing — конвертація будь-якого аудіо/відео у ringtone .bin для Grandstream.

Формат .bin відновлено реверс-інжинірингом ringtool.exe; реконструкція еталонного
ring1.bin збіглася з оригіналом байт-у-байт.

    0x000  u32 BE   розмір усього файлу в 16-бітних словах (total // 2)
    0x004  u16 BE   контрольна сума: сума всіх u16 BE слів файлу == 0 (mod 2^16)
    0x006  char[4]  "1.1."  — версія формату
    0x00A  u16 BE   рік
    0x00C  u8       місяць
    0x00D  u8       день
    0x00E  u8       година
    0x00F  u8       хвилина
    0x010  char[]   "ring.bin", доповнене нулями
    0x027  u8       0xC8
    0x104  u32 BE   розмір у словах (дублікат поля 0x000)
    0x150  u8       0x20
    0x200  payload  G.711 mu-law, 8000 Гц, моно, 1 байт = 1 семпл
"""

from __future__ import annotations

import array
import datetime as _dt
import json
import os
import shutil
import struct
import subprocess
import sys

HEADER_SIZE = 0x200
SAMPLE_RATE = 8000
INTERNAL_NAME = b"ring.bin"
VERSION_TAG = b"1.1."
LENGTH_LIMITS = (8, 16, 24)  # секунди, як у оригінальному ringtool.exe
OUTPUT_NAMES = ("ring1.bin", "ring2.bin", "ring3.bin")

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


# ---------------------------------------------------------------- G.711 mu-law

_SEG_END = (0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF)


def _build_lin2ulaw_table() -> bytes:
    table = bytearray(65536)
    for raw in range(65536):
        pcm = raw - 65536 if raw >= 32768 else raw
        pcm >>= 2  # mu-law працює з 14-бітним значенням
        if pcm < 0:
            pcm = -pcm
            sign = 0x7F
        else:
            sign = 0xFF
        if pcm > 8159:
            pcm = 8159
        pcm += 33  # BIAS (0x84) у 14-бітному масштабі
        for seg, end in enumerate(_SEG_END):
            if pcm <= end:
                break
        else:
            table[raw] = 0x7F ^ sign
            continue
        table[raw] = ((seg << 4) | ((pcm >> (seg + 1)) & 0x0F)) ^ sign
    return bytes(table)


_LIN2ULAW = _build_lin2ulaw_table()


def _build_ulaw2lin_table() -> array.array:
    out = array.array("h", [0] * 256)
    for u in range(256):
        u_inv = ~u & 0xFF
        t = ((u_inv & 0x0F) << 3) + 0x84
        t <<= (u_inv & 0x70) >> 4
        t = t - 0x84
        out[u] = -t if (u_inv & 0x80) else t
    return out


_ULAW2LIN = _build_ulaw2lin_table()


def ulaw_encode(pcm: bytes) -> bytes:
    """PCM s16le -> G.711 mu-law (1 байт на семпл)."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    tbl = _LIN2ULAW
    return bytes(tbl[s & 0xFFFF] for s in samples)


def ulaw_decode(data: bytes) -> bytes:
    """G.711 mu-law -> PCM s16le (для прослуховування «як на телефоні»)."""
    out = array.array("h", [_ULAW2LIN[b] for b in data])
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


# ------------------------------------------------------------------ .bin файл


def _sum16(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    return sum(struct.unpack(">%dH" % (len(data) // 2), data)) & 0xFFFF


def build_bin(ulaw: bytes, when: _dt.datetime | None = None) -> bytes:
    """Зібрати готовий ringtone .bin з mu-law даних."""
    if len(ulaw) % 2:
        ulaw += b"\xff"  # mu-law 0xFF == тиша; тримаємо парний розмір
    when = when or _dt.datetime.now()
    total = HEADER_SIZE + len(ulaw)

    h = bytearray(HEADER_SIZE)
    struct.pack_into(">I", h, 0x00, total // 2)  # 32 біти: 24 с не влазять у 16
    h[0x06:0x0A] = VERSION_TAG
    struct.pack_into(">HBBBB", h, 0x0A, when.year, when.month, when.day, when.hour, when.minute)
    h[0x10:0x10 + len(INTERNAL_NAME)] = INTERNAL_NAME
    h[0x27] = 0xC8
    struct.pack_into(">I", h, 0x104, total // 2)
    h[0x150] = 0x20

    checksum = (-_sum16(bytes(h) + ulaw)) & 0xFFFF
    struct.pack_into(">H", h, 0x04, checksum)

    blob = bytes(h) + ulaw
    assert _sum16(blob) == 0, "контрольна сума заголовка не зійшлася"
    return blob


def parse_bin(blob: bytes) -> dict:
    """Розібрати .bin для перевірки/інспекції."""
    if len(blob) < HEADER_SIZE:
        raise ValueError("файл замалий для ringtone .bin")
    words, checksum = struct.unpack_from(">IH", blob, 0)
    year, month, day, hour, minute = struct.unpack_from(">HBBBB", blob, 0x0A)
    name = blob[0x10:0x28].split(b"\x00", 1)[0].decode("latin1")
    payload = blob[HEADER_SIZE:]
    return {
        "declared_words": words,
        "declared_bytes": words * 2,
        "actual_bytes": len(blob),
        "size_ok": words * 2 == len(blob),
        "checksum": checksum,
        "checksum_ok": _sum16(blob) == 0,
        "version": blob[0x06:0x0A].decode("latin1"),
        "timestamp": f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}",
        "internal_name": name,
        "samples": len(payload),
        "seconds": len(payload) / SAMPLE_RATE,
        "payload": payload,
    }


# ------------------------------------------------------------------- ffmpeg


class FFmpegMissing(RuntimeError):
    pass


def _tool(name: str) -> str:
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".exe")
    if os.path.isfile(local):
        return local
    found = shutil.which(name)
    if not found:
        raise FFmpegMissing(
            f"Не знайдено {name}. Встанови FFmpeg і додай його в PATH "
            f"(або поклади {name}.exe поруч із цим скриптом)."
        )
    return found


def probe(path: str) -> dict:
    """Тривалість і опис аудіодоріжки вхідного файлу."""
    out = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-show_format", "-show_streams",
         "-select_streams", "a:0", "-of", "json", path],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe не зміг прочитати файл")
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError("У файлі немає аудіодоріжки")
    st = streams[0]
    duration = st.get("duration") or data.get("format", {}).get("duration")
    return {
        "duration": float(duration) if duration else 0.0,
        "codec": st.get("codec_name", "?"),
        "rate": int(st.get("sample_rate", 0) or 0),
        "channels": int(st.get("channels", 0) or 0),
        "format": data.get("format", {}).get("format_name", "?"),
    }


def decode_pcm(path: str, start: float = 0.0, duration: float | None = None,
               telephone_filter: bool = False) -> bytes:
    """Витягти моно PCM s16le 8000 Гц із заданого відрізка файлу."""
    cmd = [_tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-map", "a:0", "-ac", "1", "-ar", str(SAMPLE_RATE)]
    if telephone_filter:
        cmd += ["-af", "highpass=f=300,lowpass=f=3400"]
    cmd += ["-f", "s16le", "-acodec", "pcm_s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()
                           or "ffmpeg не зміг декодувати аудіо")
    return proc.stdout


# ------------------------------------------------------- обробка PCM у Python


def apply_gain(pcm: bytes, factor: float) -> bytes:
    if abs(factor - 1.0) < 1e-6:
        return pcm
    s = array.array("h")
    s.frombytes(pcm)
    for i, v in enumerate(s):
        x = int(v * factor)
        s[i] = 32767 if x > 32767 else (-32768 if x < -32768 else x)
    return s.tobytes()


def peak(pcm: bytes) -> int:
    s = array.array("h")
    s.frombytes(pcm)
    return max((abs(v) for v in s), default=0)


def normalize(pcm: bytes, target_dbfs: float = -1.0) -> bytes:
    p = peak(pcm)
    if p == 0:
        return pcm
    target = 32767 * (10 ** (target_dbfs / 20.0))
    return apply_gain(pcm, target / p)


def apply_fades(pcm: bytes, fade_in_ms: int, fade_out_ms: int) -> bytes:
    s = array.array("h")
    s.frombytes(pcm)
    n = len(s)
    fi = min(int(fade_in_ms * SAMPLE_RATE / 1000), n)
    fo = min(int(fade_out_ms * SAMPLE_RATE / 1000), n)
    for i in range(fi):
        s[i] = int(s[i] * i / fi)
    for i in range(fo):
        s[n - 1 - i] = int(s[n - 1 - i] * i / fo)
    return s.tobytes()


def write_wav(path: str, pcm: bytes) -> None:
    """Записати моно 8 кГц 16-біт WAV (той самий, що йде в кодер)."""
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)


# ----------------------------------------------------------------- pipeline


def make_ringtone(path: str, start: float = 0.0, max_seconds: int = 16,
                  do_normalize: bool = True, gain_db: float = 0.0,
                  telephone_filter: bool = False,
                  fade_in_ms: int = 50, fade_out_ms: int = 300) -> dict:
    """Повний конвеєр: файл -> обрізка -> 8 кГц моно -> обробка -> mu-law -> .bin.

    Повертає dict із ключами: bin, pcm (8 кГц s16le), seconds, peak_dbfs.
    """
    pcm = decode_pcm(path, start=start, duration=float(max_seconds),
                     telephone_filter=telephone_filter)
    limit = max_seconds * SAMPLE_RATE * 2
    pcm = pcm[:limit]
    if do_normalize:
        pcm = normalize(pcm)
    if abs(gain_db) > 1e-6:
        pcm = apply_gain(pcm, 10 ** (gain_db / 20.0))
    if fade_in_ms or fade_out_ms:
        pcm = apply_fades(pcm, fade_in_ms, fade_out_ms)

    ulaw = ulaw_encode(pcm)
    blob = build_bin(ulaw)
    p = peak(pcm)
    import math
    return {
        "bin": blob,
        "pcm": pcm,
        "seconds": len(ulaw) / SAMPLE_RATE,
        "peak_dbfs": (20 * math.log10(p / 32767)) if p else float("-inf"),
    }


# --------------------------------------------------------------------- CLI


def _cli(argv: list[str]) -> int:
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="gsring",
        description="Конвертер аудіо/відео у ringtone .bin для телефонів Grandstream")
    ap.add_argument("input", help="вхідний файл (mp3/mp4/wav/flac/m4a/ogg/…) або .bin для --inspect")
    ap.add_argument("-o", "--output", default="ring1.bin", help="вихідний .bin (типово ring1.bin)")
    ap.add_argument("-s", "--start", type=float, default=0.0, help="початок відрізка, секунди")
    ap.add_argument("-l", "--length", type=int, default=16, choices=LENGTH_LIMITS,
                    help="максимальна довжина: 8, 16 або 24 с")
    ap.add_argument("--no-normalize", action="store_true", help="не нормалізувати гучність")
    ap.add_argument("--gain", type=float, default=0.0, help="додаткове підсилення, дБ")
    ap.add_argument("--phone-filter", action="store_true", help="смуговий фільтр 300–3400 Гц")
    ap.add_argument("--fade-in", type=int, default=50, help="наростання, мс")
    ap.add_argument("--fade-out", type=int, default=300, help="згасання, мс")
    ap.add_argument("--wav", help="додатково зберегти проміжний 8 кГц WAV")
    ap.add_argument("--inspect", action="store_true", help="показати вміст існуючого .bin")
    a = ap.parse_args(argv)

    if a.inspect:
        info = parse_bin(open(a.input, "rb").read())
        info.pop("payload")
        for k, v in info.items():
            print(f"{k:16} {v}")
        return 0

    r = make_ringtone(a.input, start=a.start, max_seconds=a.length,
                      do_normalize=not a.no_normalize, gain_db=a.gain,
                      telephone_filter=a.phone_filter,
                      fade_in_ms=a.fade_in, fade_out_ms=a.fade_out)
    with open(a.output, "wb") as f:
        f.write(r["bin"])
    if a.wav:
        write_wav(a.wav, r["pcm"])
    print(f"OK  {a.output}  {len(r['bin'])} байт  {r['seconds']:.2f} с  пік {r['peak_dbfs']:.1f} dBFS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
