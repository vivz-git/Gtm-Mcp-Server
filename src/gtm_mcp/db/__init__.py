"""Database engine and session management for the mock CRM."""

from gtm_mcp.db.engine import build_engine, build_session_factory, check_connection

__all__ = ["build_engine", "build_session_factory", "check_connection"]
