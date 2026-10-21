"""Run the API server with the configured (default: localhost-only) binding:  python -m app"""

import uvicorn

from app.core.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port, proxy_headers=False, log_config=None)
