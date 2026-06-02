"""Entry point: uvicorn run_web:app --reload"""

from dotenv import load_dotenv
load_dotenv(override=True)

import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=True)
