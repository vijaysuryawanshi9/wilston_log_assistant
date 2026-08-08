"""
make_sample_data.py
--------------------
Generates a small synthetic wilston_logs.zip (a handful of lines per source)
so the pipeline can be smoke-tested end-to-end without the real ~30k-line
dataset. NOT a substitute for the real assignment data -- just a dev aid.

Run: python make_sample_data.py
"""
import zipfile
from pathlib import Path

from config import settings

APP_LINES = """2024-05-01 08:00:01,001 INFO [OrderService] Order 1001 received
2024-05-01 08:00:02,102 INFO [OrderService] Order 1001 processed successfully
2024-05-01 08:03:10,554 ERROR [InventoryService] Database connection failed: could not connect to server
2024-05-01 08:03:11,003 ERROR [InventoryService] Retry 1/3 failed: could not connect to server
2024-05-01 08:03:12,880 ERROR [InventoryService] Retry 2/3 failed: could not connect to server
2024-05-01 08:03:14,220 CRITICAL [InventoryService] All retries exhausted, marking service DEGRADED
2024-05-01 08:04:00,010 WARN [OrderService] Order 1002 delayed due to InventoryService degraded state
2024-05-01 08:10:22,441 ERROR [ReportingService] FATAL: remaining connection slots are reserved for non-replication superuser connections
2024-05-01 09:00:00,000 INFO [OrderService] Order 1003 received
2024-05-01 09:15:44,102 ERROR [OrderService] NullReferenceException at ScheduleRunner.Execute
"""

DOCKER_LINES = """2024-05-01T08:03:09.100Z container=inventory-db level=warn msg=connection pool at 95 percent capacity
2024-05-01T08:03:13.400Z container=inventory-db level=error msg=too many clients already
2024-05-01T08:03:20.000Z container=vision-inspection level=error msg=OOMKilled container exited with code 137
2024-05-01T08:03:25.000Z container=vision-inspection level=info msg=container restarted by orchestrator
2024-05-01T09:16:00.000Z container=order-processing level=warn msg=circuit breaker OPEN state detected
"""

PLC_LINES = """[2024-05-01 08:02:55] PLC-LINE3 WARN Modbus TCP read timeout after 1 retries
[2024-05-01 08:03:05] PLC-LINE3 ERROR Modbus TCP read timeout after 3 retries no response from slave device
[2024-05-01 08:03:06] PLC-LINE3 CRITICAL Watchdog reset triggered unexpected controller reboot
[2024-05-01 08:05:00] PLC-LINE3 INFO Controller back online after reboot
[2024-05-01 09:20:00] PLC-LINE1 WARN Safety interlock triggered sensor value out of calibrated range
"""


def main() -> None:
    settings.base_dir.mkdir(parents=True, exist_ok=True)
    sample_dir = settings.base_dir / "sample_logs"
    sample_dir.mkdir(exist_ok=True)

    files = {
        "wilston_application.log": APP_LINES,
        "wilston_docker.log": DOCKER_LINES,
        "wilston_plc.log": PLC_LINES,
    }
    for name, content in files.items():
        (sample_dir / name).write_text(content, encoding="utf-8")

    zip_path = settings.zip_path
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in files:
            zf.write(sample_dir / name, arcname=name)

    print(f"Sample archive written to: {zip_path}")


if __name__ == "__main__":
    main()
