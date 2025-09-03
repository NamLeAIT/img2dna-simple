# app/main.py
from fastapi import FastAPI, HTTPException, Request, Response, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any
import base64, io, os, uuid, time
import httpx
from PIL import Image, ImageFile

from .utils_dna import (
    load_image_bytes_from_pil, load_image_from_bytes,
    iter_bits_chunks, bits_to_bytes, bytes_to_bits,
    bits_to_dna, dna_to_bits
)

ImageFile.LOAD_TRUNCATED_IMAGES = True

app = FastAPI(title="img2dna-simple", version="1.0.0", docs_url="/docs", redoc_url="/redoc")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "12"))
API_KEY = os.getenv("API_KEY", "")  # optional
JOB_TTL = int(os.getenv("JOB_TTL_SEC", "900"))  # 15min

# In-memory job store: { job_id: {created, bytes_png, meta} }
JOBS: Dict[str, Dict[str, Any]] = {}

def _enforce_api_key(req: Request):
    if API_KEY and req.headers.get("x-api-key") != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

@app.head("/")
async def head_root():
    return Response(status_code=200)

@app.get("/health")
async def health():
    # cleanup old jobs
    now = time.time()
    del_ids = [jid for jid, v in JOBS.items() if now - v.get("created", now) > JOB_TTL]
    for jid in del_ids:
        JOBS.pop(jid, None)
    return {"status": "ok", "active_jobs": len(JOBS)}

def _decode_b64_any(s: str) -> bytes:
    s = s.strip()
    if "," in s and s.lower().startswith("data:"):
        s = s.split(",", 1)[1]
    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        # try urlsafe padding
        pad = "=" * (-len(s) % 4)
        try:
            return base64.urlsafe_b64decode(s + pad)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 input")

# =============== ENCODE ===============
@app.post("/encode_dna_start")
async def encode_dna_start(
    request: Request,
    payload: dict = Body(...)
):
    """Start an encode job: load image (b64 or URL), downscale, store PNG bytes, return job_id + meta."""
    _enforce_api_key(request)
    img_b64 = payload.get("image_b64")
    img_url = payload.get("image_url")
    max_side = int(payload.get("max_side", 256))
    mapping_order = payload.get("mapping_order", "ACGT")
    chunk_bits = int(payload.get("chunk_bits", 16384))  # ~2KB per chunk (bits->dna ~ 8192 bases)

    if not img_b64 and not img_url:
        raise HTTPException(status_code=400, detail="Provide image_b64 or image_url")

    # load bytes
    if img_b64:
        raw = _decode_b64_any(img_b64)
    else:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(img_url)
                r.raise_for_status()
                raw = r.content
        except Exception:
            raise HTTPException(status_code=400, detail="Cannot fetch image_url")

    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")

    # Validate & normalize to PNG bytes (downscale)
    try:
        pil = Image.open(io.BytesIO(raw))
        png_bytes = load_image_bytes_from_pil(pil, max_side=max_side)
        pil2 = Image.open(io.BytesIO(png_bytes))
        w, h = pil2.size
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image data")

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "created": time.time(),
        "bytes_png": png_bytes,
        "meta": {"width": w, "height": h, "mapping_order": mapping_order, "chunk_bits": chunk_bits},
    }

    # Preview small (first chunk) without computing all
    # Compute first chunk of bits and dna:
    chunks = []
    for ch in iter_bits_chunks(png_bytes, chunk_bits):
        chunks.append(ch)
        break
    preview_bits = chunks[0] if chunks else ""
    preview_dna = bits_to_dna(preview_bits, mapping_order=mapping_order) if preview_bits else ""

    total_bits = len(png_bytes) * 8
    num_chunks = (total_bits + chunk_bits - 1) // chunk_bits
    total_bases = (total_bits + 1) // 2  # 2 bits per base

    return {
        "job_id": job_id,
        "meta": {"width": w, "height": h, "mapping_order": mapping_order, "chunk_bits": chunk_bits},
        "size_bytes": len(png_bytes),
        "total_bits": total_bits,
        "total_bases": total_bases,
        "num_chunks": num_chunks,
        "preview": {"bits": preview_bits[:256], "dna": preview_dna[:128]}
    }

@app.get("/encode_dna_chunk")
async def encode_dna_chunk(
    request: Request,
    job_id: str,
    kind: str = "dna",            # "dna" or "binary"
    index: int = 0,
    encoding: str = "text"        # "text" or "gzip_b64" (gzip is optional, left as future)
):
    """Get chunk i (0-based) of DNA or binary bits for a job_id."""
    _enforce_api_key(request)
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id not found or expired")

    data = job["bytes_png"]
    chunk_bits = job["meta"]["chunk_bits"]
    mapping_order = job["meta"]["mapping_order"]
    total_bits = len(data) * 8
    num_chunks = (total_bits + chunk_bits - 1) // chunk_bits
    if index < 0 or index >= num_chunks:
        raise HTTPException(status_code=400, detail="index out of range")

    # iterate to the target chunk
    i = 0
    chosen = None
    for ch in iter_bits_chunks(data, chunk_bits):
        if i == index:
            chosen = ch
            break
        i += 1

    if chosen is None:
        raise HTTPException(status_code=500, detail="internal chunk error")

    if kind == "binary":
        out = chosen
    elif kind == "dna":
        out = bits_to_dna(chosen, mapping_order=mapping_order)
    else:
        raise HTTPException(status_code=400, detail="kind must be 'dna' or 'binary'")

    return {"job_id": job_id, "kind": kind, "index": index, "last": index == num_chunks - 1, "chunk": out, "encoding": encoding}

# =============== DECODE ===============
@app.post("/decode_dna")
async def decode_dna(
    request: Request,
    payload: dict = Body(...)
):
    """
    Decode back to image from:
    - 'binary_bits' (text) or
    - 'dna_text' (text) with optional 'mapping_order' (default 'ACGT').
    Returns: { image_png_b64 }
    """
    _enforce_api_key(request)
    binary_bits = payload.get("binary_bits")
    dna_text = payload.get("dna_text")
    mapping_order = payload.get("mapping_order", "ACGT")

    if not binary_bits and not dna_text:
        raise HTTPException(status_code=400, detail="Provide binary_bits or dna_text")

    if dna_text and not binary_bits:
        try:
            binary_bits = dna_to_bits(dna_text, mapping_order=mapping_order)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"DNA parse error: {e}")

    # build bytes
    try:
        img_bytes = bits_to_bytes(binary_bits)
        # Optional: you may validate it's a PNG we created.
        # But the bitstream is exactly the PNG file saved in encode stage.
        b64 = base64.b64encode(img_bytes).decode("ascii")
        return {"image_png_b64": b64}
    except Exception:
        raise HTTPException(status_code=400, detail="Cannot rebuild image from bits")

@app.post("/decode_from_job")
async def decode_from_job(
    request: Request,
    payload: dict = Body(...)
):
    """Shortcut decode using stored original PNG (no need to send big sequences)."""
    _enforce_api_key(request)
    job_id = payload.get("job_id")
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id not found or expired")
    b64 = base64.b64encode(job["bytes_png"]).decode("ascii")
    return {"image_png_b64": b64}
