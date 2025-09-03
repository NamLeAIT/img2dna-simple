from fastapi import FastAPI, Body, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, Optional
from PIL import Image, ImageFile
import base64, io, os, time, uuid, httpx

# Tăng khả năng chịu ảnh "lỗi nhẹ"
ImageFile.LOAD_TRUNCATED_IMAGES = True

app = FastAPI(title="Simple DNA Mapping (Action 1)", version="1.0.0", docs_url="/docs", redoc_url="/redoc")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True
)

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "12"))
JOB_TTL_SEC = int(os.getenv("JOB_TTL_SEC", "900"))  # 15 phút
API_KEY = os.getenv("API_KEY")  # nếu đặt, sẽ yêu cầu header x-api-key

# In-memory store: { job_id: {created, dna, png_bytes, recon_png_bytes?, meta } }
JOBS: Dict[str, Dict[str, Any]] = {}

# ---------- tiện ích ----------
def _enforce_api_key(request: Request):
    if API_KEY and request.headers.get("x-api-key") != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

def _cleanup_jobs():
    now = time.time()
    expired = [jid for jid, v in JOBS.items() if now - v.get("created", now) > JOB_TTL_SEC]
    for jid in expired:
        JOBS.pop(jid, None)

def _decode_b64_any(s: str) -> bytes:
    s = s.strip()
    if s.lower().startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    # chuẩn
    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        # urlsafe fallback
        pad = "=" * (-len(s) % 4)
        try:
            return base64.urlsafe_b64decode(s + pad)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 image")

def _image_to_png_bytes(raw: bytes, max_side: int = 256) -> bytes:
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image data")
    if max_side and max(im.size) > max_side:
        w, h = im.size
        scale = max_side / max(w, h)
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BICUBIC)
    bio = io.BytesIO()
    im.save(bio, format="PNG")
    return bio.getvalue()

def _bytes_to_bits(b: bytes) -> str:
    return "".join(f"{x:08b}" for x in b)

def _bits_to_bytes(bits: str) -> bytes:
    bits = "".join(ch for ch in bits if ch in "01")
    if len(bits) % 8 != 0:
        bits += "0" * (8 - (len(bits) % 8))
    return bytes(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))

def _bits_to_dna(bits: str, order: str = "ACGT") -> str:
    bits = "".join(ch for ch in bits if ch in "01")
    lut = {"00": order[0], "01": order[1], "10": order[2], "11": order[3]}
    if len(bits) % 2 != 0:
        bits += "0"
    out = []
    for i in range(0, len(bits), 2):
        out.append(lut[bits[i:i+2]])
    return "".join(out)

def _dna_to_bits(dna: str, order: str = "ACGT") -> str:
    dna = "".join(ch for ch in dna.upper() if ch in "ACGT")
    rev = {order[0]:"00", order[1]:"01", order[2]:"10", order[3]:"11"}
    try:
        return "".join(rev[ch] for ch in dna)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Invalid base {e}")

# ---------- endpoints ----------
@app.head("/")
async def head_root():
    return Response(status_code=200)

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse("<h2>Simple DNA Mapping (Action 1)</h2><p>Use <a href='/docs'>/docs</a>.</p>")

@app.get("/health")
async def health():
    _cleanup_jobs()
    return {"status": "ok", "active_jobs": len(JOBS)}

@app.post("/encode_simple_json")
async def encode_simple_json(
    request: Request,
    payload: dict = Body(..., description="Provide image_b64 or image_url"),
):
    """
    Ảnh → PNG → bits → DNA (simple mapping).
    Trả về: job_id, 50 nt đầu, độ dài, và URL tải .txt DNA (kèm PNG chuẩn hoá).
    """
    _enforce_api_key(request)
    _cleanup_jobs()

    image_b64 = payload.get("image_b64")
    image_url = payload.get("image_url")
    mapping_order = payload.get("mapping_order", "ACGT")
    max_side = int(payload.get("max_side", 256))

    if not image_b64 and not image_url:
        raise HTTPException(status_code=400, detail="Provide image_b64 or image_url")

    # lấy bytes ảnh
    if image_b64:
        raw = _decode_b64_any(image_b64)
    else:
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r = await client.get(image_url)
                r.raise_for_status()
                raw = r.content
        except Exception:
            raise HTTPException(status_code=400, detail="Cannot fetch image_url")

    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")

    png_bytes = _image_to_png_bytes(raw, max_side=max_side)
    bits = _bytes_to_bits(png_bytes)
    dna = _bits_to_dna(bits, order=mapping_order)

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "created": time.time(),
        "png_bytes": png_bytes,
        "dna": dna,
        "meta": {"mapping_order": mapping_order, "max_side": max_side}
    }

    # URL tải file
    dna_url = str(request.url_for("download_dna", job_id=job_id))
    image_url_norm = str(request.url_for("download_image", job_id=job_id))

    return {
        "job_id": job_id,
        "mapping_order": mapping_order,
        "max_side": max_side,
        "dna_len": len(dna),
        "dna_head_50": dna[:50],
        "downloads": {
            "dna_txt": dna_url,
            "normalized_png": image_url_norm
        }
    }

@app.get("/job/{job_id}/dna.txt")
async def download_dna(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found or expired")
    return PlainTextResponse(
        content=job["dna"],
        headers={"Content-Disposition": "attachment; filename=\"dna.txt\""}
    )

@app.get("/job/{job_id}/image.png")
async def download_image(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found or expired")
    return Response(
        content=job["png_bytes"],
        media_type="image/png",
        headers={"Content-Disposition": "inline; filename=\"image.png\""}
    )

@app.post("/decode_simple_json")
async def decode_simple_json(
    request: Request,
    payload: dict = Body(..., description="Provide dna_text or dna_url"),
):
    """
    DNA (A/C/G/T) → Ảnh PNG (base64) + link tải file PNG.
    Hỗ trợ: dna_text trực tiếp hoặc dna_url.
    """
    _enforce_api_key(request)

    dna_text: Optional[str] = payload.get("dna_text")
    dna_url: Optional[str] = payload.get("dna_url")
    mapping_order = payload.get("mapping_order", "ACGT")

    if not dna_text and not dna_url:
        raise HTTPException(status_code=400, detail="Provide dna_text or dna_url")

    async def fetch_text(url: str) -> str:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.text

    if dna_text is None and dna_url:
        try:
            dna_text = await fetch_text(dna_url)
        except Exception:
            raise HTTPException(status_code=400, detail="Cannot fetch dna_url")

    # Clean DNA & chuyển về bits
    dna_clean = "".join(ch for ch in (dna_text or "").upper() if ch in "ACGT")
    if not dna_clean:
        raise HTTPException(status_code=400, detail="Invalid DNA text")
    bits = _dna_to_bits(dna_clean, order=mapping_order)

    # bits -> bytes -> ảnh
    img_bytes = _bits_to_bytes(bits)
    # xác thực ảnh PNG
    try:
        _ = Image.open(io.BytesIO(img_bytes)).size
    except Exception:
        raise HTTPException(status_code=400, detail="Rebuilt bytes are not a valid image")

    # Lưu job decode để tải file PNG
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"created": time.time(), "recon_png_bytes": img_bytes, "meta": {"mapping_order": mapping_order}}

    png_b64 = base64.b64encode(img_bytes).decode("ascii")
    recon_url = str(request.url_for("download_reconstructed", job_id=job_id))

    return {
        "job_id": job_id,
        "image_png_b64": png_b64,
        "downloads": { "reconstructed_png": recon_url }
    }

@app.get("/job/{job_id}/reconstructed.png")
async def download_reconstructed(job_id: str):
    job = JOBS.get(job_id)
    if not job or "recon_png_bytes" not in job:
        raise HTTPException(status_code=404, detail="reconstructed image not found or expired")
    return Response(
        content=job["recon_png_bytes"],
        media_type="image/png",
        headers={"Content-Disposition": "attachment; filename=\"reconstructed.png\""}
    )
