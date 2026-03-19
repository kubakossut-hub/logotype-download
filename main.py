import logging
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api.routes import logos, export

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Logo Downloader")

app.include_router(logos.router, prefix="/api/logos")
app.include_router(export.router, prefix="/api/export")

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
