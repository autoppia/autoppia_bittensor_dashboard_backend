"""
Overview Cache Updater - Background Thread

Pre-calienta el caché de Redis cada 3 minutos con los endpoints
críticos de overview (metrics + current round + validators limit 6),
garantizando que los usuarios vean respuestas instantáneas.

OPTIMIZADO: Ya no precalienta /admin/warm/agents (deprecado).
Solo precalienta endpoints ligeros. Similar al metagraph_updater, 
usa HTTP requests simples sin asyncio.
"""

import logging
import os
import threading
import time
from typing import Optional
import requests
from datetime import datetime

from app.services.redis_cache import redis_cache

logger = logging.getLogger(__name__)

# Update interval: 3 minutos para mantener el caché caliente
OVERVIEW_UPDATE_INTERVAL = 3 * 60  # 180 segundos


def _get_api_base_url() -> str:
    """
    Determina la URL base que debe usar el cache warmer.

    Permite override via CACHE_WARMER_BASE_URL y, si no existe,
    usa el mismo puerto configurado para FastAPI (settings.PORT).
    """
    override = os.getenv("CACHE_WARMER_BASE_URL")
    if override:
        return override.rstrip("/")

    from app.config import settings

    return f"http://localhost:{settings.PORT}"

# ADMIN_ENDPOINTS eliminados - /admin/warm/agents está deprecado
# Ya no se precalienta porque las queries pesadas fueron optimizadas
ADMIN_ENDPOINTS = []

# Endpoints a pre-calentar - SOLO los que realmente necesitan precalentamiento
# Los demás endpoints ya son suficientemente rápidos (< 200ms) con Redis automático
ENDPOINTS_TO_WARM = [
    # Overview metrics (el más pesado sin caché: ~800ms)
    "/api/v1/overview/metrics",
    # Current round (crítico para homepage)
    "/api/v1/overview/rounds/current",
    # Lista de validadores (limit=6) usada en el overview
    "/api/v1/overview/validators?limit=6&sortBy=weight&sortOrder=desc",
]

# Global state
_updater_thread: Optional[threading.Thread] = None
_should_stop = threading.Event()
_is_running = False


def _warm_cache_endpoints() -> dict:
    """
    Pre-calienta el caché haciendo requests a los endpoints principales.

    Similar a como metagraph_updater hace requests a subtensor,
    este hace requests HTTP simples a localhost. NO usa asyncio,
    solo requests síncronos que funcionan perfectamente en un thread.
    """
    results = {}

    base_url = _get_api_base_url()

    # Trigger admin warmers first (POST requests)
    for endpoint in ADMIN_ENDPOINTS:
        try:
            url = f"{base_url}{endpoint}"
            start = time.time()
            response = requests.post(url, timeout=60)
            elapsed = time.time() - start
            if response.status_code == 200:
                results[endpoint] = {
                    "success": True,
                    "elapsed": round(elapsed, 3),
                    "status": response.status_code,
                }
                logger.debug(f"✅ Admin warm triggered: {endpoint} ({elapsed:.3f}s)")
            else:
                results[endpoint] = {
                    "success": False,
                    "elapsed": round(elapsed, 3),
                    "status": response.status_code,
                    "error": f"HTTP {response.status_code}",
                }
                logger.warning(
                    "⚠️  Admin warm endpoint %s failed: HTTP %s",
                    endpoint,
                    response.status_code,
                )
        except Exception as exc:  # noqa: BLE001
            results[endpoint] = {"success": False, "error": str(exc)}
            logger.error(f"❌ Error warming admin endpoint {endpoint}: {exc}")

    for endpoint in ENDPOINTS_TO_WARM:
        try:
            url = f"{base_url}{endpoint}"
            start = time.time()

            # Request simple con timeout
            response = requests.get(url, timeout=30)
            elapsed = time.time() - start

            if response.status_code == 200:
                results[endpoint] = {
                    "success": True,
                    "elapsed": round(elapsed, 3),
                    "status": response.status_code,
                }
                logger.debug(f"✅ Warmed cache: {endpoint} ({elapsed:.3f}s)")
            else:
                results[endpoint] = {
                    "success": False,
                    "elapsed": round(elapsed, 3),
                    "status": response.status_code,
                    "error": f"HTTP {response.status_code}",
                }
                logger.warning(
                    f"⚠️  Failed to warm {endpoint}: HTTP {response.status_code}"
                )

        except Exception as exc:
            results[endpoint] = {"success": False, "error": str(exc)}
            logger.error(f"❌ Error warming cache for {endpoint}: {exc}")

    return results


def _updater_worker():
    """Background worker que pre-calienta el caché cada 3 minutos."""
    global _is_running

    logger.info("=" * 80)
    logger.info("🚀 Overview Cache Warmer Starting (OPTIMIZED)")
    logger.info(f"   - Update interval: {OVERVIEW_UPDATE_INTERVAL / 60:.0f} minutes")
    logger.info(f"   - Endpoints to warm: {len(ENDPOINTS_TO_WARM)} (only critical ones)")
    logger.info(f"   - Admin warmers (deprecated): {len(ADMIN_ENDPOINTS)}")
    logger.info("=" * 80)

    _is_running = True

    # Esperar unos segundos para que el servidor esté completamente iniciado
    logger.info("⏳ Waiting 10s for server to be fully ready...")
    for _ in range(10):
        if _should_stop.is_set():
            return
        time.sleep(1)

    # Primera actualización inmediata
    logger.info("🔄 Performing initial cache warming...")
    results = _warm_cache_endpoints()
    successful = sum(1 for r in results.values() if r.get("success"))
    logger.info(
        f"✅ Initial warming complete: {successful}/{len(ENDPOINTS_TO_WARM)} endpoints successful"
    )

    last_update = time.time()
    update_count = 1

    # Loop principal
    while not _should_stop.is_set():
        now = time.time()
        time_since_update = now - last_update

        if time_since_update >= OVERVIEW_UPDATE_INTERVAL:
            update_count += 1
            logger.info(f"🔄 Warming cache (update #{update_count})...")

            results = _warm_cache_endpoints()
            successful = sum(1 for r in results.values() if r.get("success"))

            logger.info(
                f"✅ Cache warming #{update_count} complete: "
                f"{successful}/{len(ENDPOINTS_TO_WARM)} endpoints successful"
            )

            # Log any failures
            for endpoint, result in results.items():
                if not result.get("success"):
                    logger.warning(
                        f"⚠️  Endpoint {endpoint} failed: {result.get('error', 'unknown')}"
                    )

            last_update = now

        # Dormir en intervalos pequeños para poder detener rápido
        time_until_next = OVERVIEW_UPDATE_INTERVAL - time_since_update
        sleep_time = min(time_until_next, 10)  # Max 10s sleep

        if sleep_time > 0:
            if _should_stop.wait(timeout=sleep_time):
                break

    logger.info("=" * 80)
    logger.info("🛑 Overview Cache Warmer Stopped")
    logger.info(f"   - Total updates performed: {update_count}")
    logger.info("=" * 80)

    _is_running = False


def start_overview_updater() -> None:
    """Inicia el background thread que pre-calienta el caché."""
    global _updater_thread, _should_stop
    
    # Verificar si está habilitado en configuración
    from app.config import settings
    if not settings.ENABLE_OVERVIEW_CACHE_WARMER:
        logger.info("⏹️  Overview cache warmer disabled (ENABLE_OVERVIEW_CACHE_WARMER=false)")
        return

    if _updater_thread and _updater_thread.is_alive():
        logger.warning("Overview cache warmer already running")
        return

    _should_stop.clear()
    _updater_thread = threading.Thread(target=_updater_worker, daemon=True)
    _updater_thread.start()
    logger.info("✅ Overview cache warmer thread started")


def stop_overview_updater() -> None:
    """Detiene el background thread."""
    global _should_stop, _updater_thread, _is_running

    if not _updater_thread or not _updater_thread.is_alive():
        logger.info("Overview cache warmer not running, nothing to stop")
        return

    logger.info("🛑 Stopping overview cache warmer...")
    _should_stop.set()

    if _updater_thread:
        _updater_thread.join(timeout=5)

    _is_running = False
    logger.info("✅ Overview cache warmer stopped")


def is_running() -> bool:
    """Check si el warmer está activo."""
    return _is_running
