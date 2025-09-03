
# Image ↔ Text Encoder (v3) — FastAPI on Render

A tiny web service to **encode images into text manifests** and **decode text manifests back to images**, supporting 3 levels:

1. **Lossless** — bit-perfect base64 manifest with hashes.
2. **Lossy-ALGO** — deterministic quantization (palette + RLE) + optional resize.
3. **Lossy-NLP** — high-level color/layout description with a proxy renderer.

## Quickstart (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# Open http://127.0.0.1:8000/  (UI) or http://127.0.0.1:8000/docs
```

## Deploy to Render

1. Push this repo to GitHub.
2. On Render, create a **Web Service** connected to the repo.
3. Render will read `render.yaml`:
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Once deployed, visit the service URL; the landing page has a simple UI.

## REST API

- `POST /encode`
  - Form-data fields:
    - `file` (image/*), required
    - `mode`: `lossless` | `lossy-algo` | `lossy-nlp` (default: `lossy-algo`)
    - **Lossy-ALGO** params: `lock_dims` (bool), `max_side` (int), `palette_size` (int), `resample` (`nearest|bilinear|bicubic|lanczos`), `dither` (bool)
    - **Lossy-NLP** params: `preserve_dims` (bool), `target_short_side` (int), `palette_probe` (int)
  - Returns: `text/plain` manifest (downloadable).

- `POST /decode`
  - Form-data fields:
    - `file` (`text/plain`), required — manifest text produced by `/encode`
  - Returns: `image/png` (decoded image or proxy).

- `GET /` — minimal HTML UI (encode/decode forms)
- `GET /docs` — OpenAPI docs

## Examples

```bash
# Encode (lossless)
curl -s -F "file=@/path/in.png" -F "mode=lossless" https://YOUR-RENDER-URL/encode -o manifest.txt

# Decode
curl -s -F "file=@manifest.txt" https://YOUR-RENDER-URL/decode -o decoded.png
```

## Notes
- The repository includes `app/image_to_text_full_v3.py` (the core library).
- For **large images**, consider increasing instance resources or adding server-side limits.
- If you need strict reproducibility between environments, pin your Pillow version.
