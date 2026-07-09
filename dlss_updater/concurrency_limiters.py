"""
Shared anyio concurrency primitives, sized from Concurrency (config.py).

Two distinct kinds of limiter are exposed here, matching the two distinct
things the old asyncio.Semaphore / ThreadPoolExecutor(max_workers=...) call
sites were actually bounding:

- thread_* limiters bound REAL OS THREADS dispatched via
  ``anyio.to_thread.run_sync(func, limiter=...)``. These replace both the
  bare ``asyncio.to_thread()`` calls (which silently used asyncio's default
  executor, ignoring Concurrency entirely) and the several ad-hoc
  ``ThreadPoolExecutor(max_workers=...)`` instances scattered across
  dll_repository.py, scanner.py, updater.py, high_performance_updater.py,
  and hyper_parallel_loader.py. Sized like THREADPOOL_CPU/THREADPOOL_IO
  (capped, since OS threads are expensive).

- io_* limiters bound CONCURRENT ASYNC OPERATIONS (e.g. how many in-flight
  aiohttp requests or PE-parsing tasks run at once), replacing
  ``asyncio.Semaphore(Concurrency.IO_HEAVY / IO_EXTREME)``. These are not
  threads at all, so they can be sized much higher (CPU_THREADS * 32/64).
"""

import anyio

from .config import Concurrency

# Real OS thread pools (anyio.to_thread.run_sync(..., limiter=...))
thread_cpu = anyio.CapacityLimiter(Concurrency.THREADPOOL_CPU)
thread_io = anyio.CapacityLimiter(Concurrency.THREADPOOL_IO)

# Async-task concurrency gates (async with io_heavy: / io_extreme:)
io_heavy = anyio.CapacityLimiter(Concurrency.IO_HEAVY)
io_extreme = anyio.CapacityLimiter(Concurrency.IO_EXTREME)
