import uvicorn
from src.app import app
from src.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=3090,
        reload=False,
        access_log=True,
        log_level="debug",
    )