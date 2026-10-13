import sys, subprocess
sys.stdout.reconfigure(encoding="utf-8")

for meas in ["stock", "theme", "top30_stocks"]:
    result = subprocess.run([
        "docker", "exec", "monitor_influxdb",
        "influx", "delete",
        "--bucket", "intraday",
        "--org", "stocks",
        "--token", "mytoken123",
        "--start", "2026-05-15T00:00:00Z",
        "--stop",  "2026-05-16T00:00:00Z",
        "--predicate", f'_measurement="{meas}"',
    ], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"{meas} 已刪除")
    else:
        print(f"{meas} 刪除失敗: {result.stderr.strip()}")
