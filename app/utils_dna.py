# app/utils_dna.py
import base64, io, math
from PIL import Image

# ===== Image helpers =====
def load_image_bytes_from_pil(pil_img, max_side=256) -> bytes:
    """Convert a PIL image to PNG bytes with optional max_side resize."""
    if max_side and max_side > 0:
        w, h = pil_img.size
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            pil_img = pil_img.resize((max(1, int(w*scale)), max(1, int(h*scale))), Image.BICUBIC)
    bio = io.BytesIO()
    pil_img.save(bio, format="PNG")
    return bio.getvalue()

def load_image_from_bytes(img_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(img_bytes)).convert("RGBA")

# ===== Bytes ↔ Bits =====
def bytes_to_bits(data: bytes) -> str:
    # WARNING: for large data, prefer generator/chunking
    return "".join(f"{b:08b}" for b in data)

def iter_bits_chunks(data: bytes, chunk_bits: int):
    """Yield bit-string chunks of length chunk_bits from bytes."""
    buf = []
    bits_in_buf = 0
    for b in data:
        bs = f"{b:08b}"
        buf.append(bs)
        bits_in_buf += 8
        while bits_in_buf >= chunk_bits:
            # emit one chunk
            joined = "".join(buf)
            yield joined[:chunk_bits]
            # keep the remainder
            rem = joined[chunk_bits:]
            buf = [rem] if rem else []
            bits_in_buf = len(rem)
    if bits_in_buf > 0:
        yield "".join(buf)

def bits_to_bytes(bits: str) -> bytes:
    # pad to multiple of 8
    if len(bits) % 8 != 0:
        bits = bits + "0" * (8 - (len(bits) % 8))
    out = bytearray()
    for i in range(0, len(bits), 8):
        out.append(int(bits[i:i+8], 2))
    return bytes(out)

# ===== Bits ↔ DNA (simple mapping) =====
# mapping_order defines 2-bit→base: "ACGT" means 00->A, 01->C, 10->G, 11->T
def bits_to_dna(bits: str, mapping_order="ACGT") -> str:
    lut = {
        "00": mapping_order[0],
        "01": mapping_order[1],
        "10": mapping_order[2],
        "11": mapping_order[3],
    }
    # pad to multiple of 2
    if len(bits) % 2 != 0:
        bits += "0"
    bases = []
    for i in range(0, len(bits), 2):
        bases.append(lut[bits[i:i+2]])
    return "".join(bases)

def dna_to_bits(dna: str, mapping_order="ACGT") -> str:
    rev = {
        mapping_order[0]: "00",
        mapping_order[1]: "01",
        mapping_order[2]: "10",
        mapping_order[3]: "11",
    }
    bits = []
    for ch in dna:
        if ch not in rev:
            raise ValueError(f"Invalid base: {ch}")
        bits.append(rev[ch])
    return "".join(bits)
