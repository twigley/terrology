import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Terrology web interface")
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to listen on (default: 8000)"
    )
    parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload (development)"
    )
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("ERROR: web dependencies not installed.")
        print(
            'Install with: uv tool install "git+https://github.com/twigley/terrology[web]"'
        )
        raise SystemExit(1)

    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=args.reload)
