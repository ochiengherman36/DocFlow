from fastapi import FastAPI

from config import get_settings

settings = get_settings()

app = FastAPI(title="DocFlow API")


@app.get("/health")
def health():
    return {"status": "ok"}