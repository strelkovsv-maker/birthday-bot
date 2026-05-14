"""SSL trust-store setup.

Imported FIRST in every entry point so HTTPS calls (Anthropic, Telegram,
Google) trust the OS-native certificate store instead of certifi's bundled
CA list. This is essential when running on a corporate network that does
TLS inspection (the corporate root CA lives in macOS Keychain / Linux
system store, not in certifi).

On Railway the system store is the standard public CA bundle, so this is a
safe no-op there.
"""
from __future__ import annotations

try:
    import truststore  # type: ignore[import-not-found]
    truststore.inject_into_ssl()
except ImportError:
    # truststore not installed — fall back to certifi's bundled CAs.
    # HTTPS will fail behind a TLS-inspecting proxy without it.
    import logging
    logging.getLogger(__name__).debug(
        "truststore not installed — using certifi defaults"
    )
