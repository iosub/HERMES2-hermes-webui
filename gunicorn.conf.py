def when_ready(server):
    server.log.info(
        "Gunicorn ready pid=%s workers=%s worker_class=%s timeout=%ss graceful_timeout=%ss keepalive=%ss max_requests=%s jitter=%s",
        server.pid,
        server.cfg.workers,
        server.cfg.worker_class_str,
        server.cfg.timeout,
        server.cfg.graceful_timeout,
        server.cfg.keepalive,
        server.cfg.max_requests,
        server.cfg.max_requests_jitter,
    )


def worker_abort(worker):
    worker.log.warning(
        "Worker abort pid=%s. Gunicorn timed out waiting for the worker to respond. "
        "This usually means a stalled client upload/connection or a request that exceeded the configured timeout (%ss).",
        worker.pid,
        worker.cfg.timeout,
    )


def worker_exit(server, worker):
    if getattr(worker, "aborted", False):
        server.log.warning(
            "Worker exit pid=%s after abort. The master replaced it because the worker stopped responding before the request completed.",
            worker.pid,
        )
