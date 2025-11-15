"""
Overview Metrics Cache Updater - Background Thread

Actualiza las métricas de overview cada 5 minutos en background,
así los usuarios SIEMPRE ven datos desde Redis (súper rápido).
"""

import logging
import threading
import time
from typing import Optional
from datetime import datetime

from app.services.redis_cache import redis_cache
from app.config import settings

logger = logging.getLogger(__name__)

# Update interval: 5 minutos
OVERVIEW_UPDATE_INTERVAL = 5 * 60  # 300 segundos

# Redis key
REDIS_KEY_OVERVIEW_METRICS = "overview_metrics_bg"
REDIS_KEY_OVERVIEW_LAST_UPDATE = "overview:last_update"

# Global state
_updater_thread: Optional[threading.Thread] = None
_should_stop = threading.Event()
_is_running = False


def _fetch_and_cache_overview_metrics() -> bool:
    """
    Calcula overview metrics y las guarda en Redis.
    Esto se ejecuta en background cada 5 minutos.
    """
    try:
        # Import aquí para evitar circular imports
        from app.db.session import AsyncSessionLocal
        from app.services.ui.overview_service import OverviewService
        import asyncio
        
        async def _fetch():
            async with AsyncSessionLocal() as session:
                service = OverviewService(session=session)
                metrics = await service.overview_metrics()
                return metrics
        
        # Ejecutar la función async usando asyncio.run()
        # que crea y limpia el event loop automáticamente
        try:
            metrics = asyncio.run(_fetch())
        except RuntimeError:
            # Fallback si ya hay un loop activo (no debería pasar en un thread)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                metrics = loop.run_until_complete(_fetch())
            finally:
                loop.close()

        # Guardar en Redis con TTL de 10 minutos (por si el updater falla)
        redis_cache.set(REDIS_KEY_OVERVIEW_METRICS, metrics, ttl=600)
        redis_cache.set(
            REDIS_KEY_OVERVIEW_LAST_UPDATE, datetime.utcnow().isoformat(), ttl=600
        )

        logger.info("✅ Overview metrics cached successfully in background")
        return True

    except Exception as exc:
        logger.error(f"❌ Failed to update overview metrics: {exc}", exc_info=True)
        return False


def _updater_worker():
    """Background worker que actualiza overview metrics cada 5 minutos."""
    global _is_running

    logger.info("=" * 80)
    logger.info("🚀 Overview Metrics Cache Updater Starting")
    logger.info(f"   - Update interval: {OVERVIEW_UPDATE_INTERVAL / 60:.0f} minutes")
    logger.info("=" * 80)

    _is_running = True

    # Esperar a que Redis esté disponible
    max_retries = 30
    retry_count = 0
    while retry_count < max_retries and not _should_stop.is_set():
        if redis_cache.is_available():
            logger.info("✅ Redis available, starting overview metrics updates")
            break
        retry_count += 1
        logger.warning(
            f"⏳ Waiting for Redis ({retry_count}/{max_retries}), "
            f"retrying in 5 seconds..."
        )
        time.sleep(5)

    if not redis_cache.is_available():
        logger.error("❌ Redis not available, overview updates disabled")
        _is_running = False
        return

    # Primera actualización inmediata
    logger.info("🔄 Performing initial overview metrics update...")
    _fetch_and_cache_overview_metrics()

    last_update = time.time()
    update_count = 1

    # Loop principal
    while not _should_stop.is_set():
        now = time.time()
        time_since_update = now - last_update

        if time_since_update >= OVERVIEW_UPDATE_INTERVAL:
            update_count += 1
            logger.info(f"🔄 Updating overview metrics (update #{update_count})...")
            _fetch_and_cache_overview_metrics()
            last_update = now

        # Dormir en intervalos pequeños para poder detener rápido
        time_until_next = OVERVIEW_UPDATE_INTERVAL - time_since_update
        sleep_time = min(time_until_next, 10)  # Max 10s sleep

        if sleep_time > 0:
            if _should_stop.wait(timeout=sleep_time):
                break

    logger.info("=" * 80)
    logger.info("🛑 Overview Metrics Cache Updater Stopped")
    logger.info(f"   - Total updates performed: {update_count}")
    logger.info("=" * 80)

    _is_running = False


def start_overview_updater() -> None:
    """Inicia el background thread que actualiza overview metrics."""
    global _updater_thread, _should_stop

    if _updater_thread and _updater_thread.is_alive():
        logger.warning("Overview updater already running")
        return

    _should_stop.clear()
    _updater_thread = threading.Thread(target=_updater_worker, daemon=True)
    _updater_thread.start()
    logger.info("✅ Overview metrics updater thread started")


def stop_overview_updater() -> None:
    """Detiene el background thread."""
    global _should_stop, _updater_thread, _is_running

    if not _updater_thread or not _updater_thread.is_alive():
        logger.info("Overview updater not running, nothing to stop")
        return

    logger.info("🛑 Stopping overview metrics updater...")
    _should_stop.set()

    if _updater_thread:
        _updater_thread.join(timeout=5)

    _is_running = False
    logger.info("✅ Overview metrics updater stopped")


def is_running() -> bool:
    """Check si el updater está activo."""
    return _is_running
