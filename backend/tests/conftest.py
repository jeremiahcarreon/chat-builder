import base64
import os

# Settings needed to import the app; tests that need a database use their own.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://unused@localhost/unused")
os.environ.setdefault("MASTER_KEY", base64.b64encode(b"k" * 32).decode())
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-password-123")
