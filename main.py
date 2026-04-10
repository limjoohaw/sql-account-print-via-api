"""Entry point for SQL Account Print web app.

Handles:
- sys.stdout/stderr shim for PyInstaller --windowed builds
- Favicon resolution
- NiceGUI server startup
- Log cleanup on startup
"""

import io
import os
import sys

# PyInstaller --windowed: sys.stdout/sys.stderr are None.
# uvicorn calls sys.stdout.isatty() during logging setup — crashes with AttributeError.
# This shim MUST run before any import that transitively loads uvicorn.
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

from logger import cleanup_old_logs
from nicegui_app import create_app
from nicegui import ui, app
from config import settings
from starlette.middleware.base import BaseHTTPMiddleware

_UNSAFE_SECRETS = {'change-me-to-64-random-hex-chars', 'dev-secret-change-me',
                   'dev-testing-secret-not-for-production-1234567890abcdef'}


class CookieSecurityMiddleware(BaseHTTPMiddleware):
    """Add SameSite=Strict to all Set-Cookie headers."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if 'set-cookie' in response.headers:
            cookies = response.headers.getlist('set-cookie')
            response.headers._list = [
                (k, v) for k, v in response.headers._list if k != b'set-cookie'
            ]
            for cookie in cookies:
                if 'SameSite' not in cookie:
                    cookie += '; SameSite=Strict'
                response.headers.append('set-cookie', cookie)
        return response


def _validate_session_secret():
    """Refuse to start if SESSION_SECRET is missing, default, or too short."""
    secret = settings.session_secret
    if secret in _UNSAFE_SECRETS:
        print("\n" + "=" * 60)
        print("SECURITY ERROR: SESSION_SECRET is set to an unsafe default!")
        print("Generate a secure secret:")
        print("  python -c \"import secrets; print(secrets.token_hex(32))\"")
        print("Then set it in your .env file.")
        print("=" * 60 + "\n")
        sys.exit(1)
    if len(secret) < 32:
        print("\n" + "=" * 60)
        print("SECURITY ERROR: SESSION_SECRET is too short (need 32+ chars).")
        print("Generate a secure secret:")
        print("  python -c \"import secrets; print(secrets.token_hex(32))\"")
        print("=" * 60 + "\n")
        sys.exit(1)


def _write_startup_error(error):
    """Write startup error to file next to the executable (for --windowed debugging)."""
    try:
        if getattr(sys, 'frozen', False):
            err_path = os.path.join(os.path.dirname(sys.executable), "startup_error.log")
        else:
            err_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startup_error.log")
        with open(err_path, "w") as f:
            import traceback
            traceback.print_exc(file=f)
    except Exception:
        pass


def main():
    try:
        # Security: validate session secret before anything else
        _validate_session_secret()

        # Clean up old log files
        cleanup_old_logs(keep=90)

        # Resolve favicon
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        icon_path = os.path.join(base_dir, "icon.png")
        favicon = icon_path if os.path.exists(icon_path) else None

        # Register all NiceGUI pages
        create_app()

        # Add cookie security middleware
        app.add_middleware(CookieSecurityMiddleware)

        # Start NiceGUI server
        ui.run(
            title="SQL Account Print",
            port=8090,       # Fixed port (consol-sync uses 8080)
            show=True,       # Auto-open browser
            reload=False,    # No hot-reload in production
            favicon=favicon,
            storage_secret=settings.session_secret,
        )

    except Exception:
        _write_startup_error(sys.exc_info())
        raise


if __name__ == "__main__":
    main()
