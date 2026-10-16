r"""Run the Threshold MCP server: .\.venv\Scripts\python.exe -m core.mcp_server"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from core.engine.service import Engine
from core.mcp_server.server import build_app, build_server
from core.store import db
from core.store.models import Household

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # per-request lines include full device ids
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8000"))
    household_id = os.environ.get("THRESHOLD_HOUSEHOLD", "home")

    conn = db.connect(ROOT / os.environ.get("THRESHOLD_DB", "data/threshold.db"))
    engine = Engine(conn)
    engine.ensure_household(Household(
        id=household_id,
        parent_name=os.environ.get("THRESHOLD_PARENT_NAME", "Dad"),
        tz=os.environ.get("THRESHOLD_TZ", "Asia/Kolkata"),
        checkin_by=os.environ.get("THRESHOLD_CHECKIN_BY", "10:00"),
        grace_min=int(os.environ.get("THRESHOLD_GRACE_MIN", "30")),
        reprompt_min=int(os.environ.get("THRESHOLD_REPROMPT_MIN", "10")),
        secondary_after_min=int(os.environ.get("THRESHOLD_SECONDARY_AFTER_MIN", "20")),
    ))

    mcp = build_server(engine, household_id, snapshot_dir=ROOT / "data" / "snapshots", web_dir=ROOT / "web",
                       public_base_url=os.environ.get("PUBLIC_BASE_URL", f"http://{host}:{port}"))
    print(f"\n  Threshold web client: http://{host}:{port}/\n  MCP endpoint:          http://{host}:{port}/mcp\n")
    uvicorn.run(build_app(mcp, engine, household_id), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
