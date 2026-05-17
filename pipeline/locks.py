"""
Shared concurrency controls for the content pipeline.
Import `heavy_op` anywhere a memory-intensive operation runs.
Only one heavy operation runs at a time — keeps Render Starter within 512 MB.
"""

import threading

# Single global permit: download + ffmpeg + upload
heavy_op = threading.Semaphore(1)
