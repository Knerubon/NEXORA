"""Start an isolated loopback API process, check HTTP, then stop it."""

import json
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


def main() -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "nexora_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "18000",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("API exited before becoming available (check port 18000)")
            try:
                with urlopen("http://127.0.0.1:18000/health", timeout=1) as response:
                    payload = json.load(response)
                    assert response.status == 200
                    assert payload == {
                        "status": "ok",
                        "mode": "research",
                        "readiness": "/operations/readiness",
                    }
                print("PASS: loopback API HTTP health")
                return
            except URLError:
                time.sleep(0.1)
        raise RuntimeError("API did not become available")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
