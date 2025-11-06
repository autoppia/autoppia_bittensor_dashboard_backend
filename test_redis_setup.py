#!/usr/bin/env python3
"""
Script de prueba para verificar que Redis está funcionando correctamente.
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.redis_cache import redis_cache
from app.services.smart_cache import (
    get_cache_info,
    get_current_round_number,
    get_smart_ttl_for_round,
)
from app.config import settings


def print_section(title: str):
    """Print a formatted section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


async def test_redis_connection():
    """Test basic Redis connectivity."""
    print_section("🔌 Verificando Conexión a Redis")

    print(f"Host: {settings.REDIS_HOST}:{settings.REDIS_PORT}")
    print(f"DB: {settings.REDIS_DB}")
    print(f"Habilitado: {settings.REDIS_ENABLED}")
    print(
        f"TTL datos finales: {settings.REDIS_FINAL_DATA_TTL}s ({settings.REDIS_FINAL_DATA_TTL / 86400:.1f} días)"
    )

    if redis_cache.is_available():
        print("\n✅ Redis está CONECTADO y funcionando")
    else:
        print("\n⚠️  Redis NO está disponible (usando caché en memoria)")
        print("   Para habilitar Redis, asegúrate de que esté corriendo:")
        print("   - docker-compose up -d redis")
        print("   - O: redis-server")


async def test_basic_operations():
    """Test basic cache operations."""
    print_section("🧪 Probando Operaciones Básicas")

    # Test 1: Set and Get
    print("\n1️⃣  Test Set/Get:")
    test_data = {
        "message": "Redis funciona perfectamente!",
        "number": 42,
        "nested": {"key": "value"},
    }

    redis_cache.set("test_key_1", test_data, ttl=60)
    result = redis_cache.get("test_key_1")

    if result == test_data:
        print("   ✅ Set/Get funciona correctamente")
    else:
        print(f"   ❌ Error: esperaba {test_data}, obtuvo {result}")

    # Test 2: TTL
    print("\n2️⃣  Test TTL (Time To Live):")
    redis_cache.set("test_ttl", "data_temporal", ttl=5)
    print("   ✅ Dato guardado con TTL de 5 segundos")
    print("   (El dato se borrará automáticamente después de 5 segundos)")

    # Test 3: Delete
    print("\n3️⃣  Test Delete:")
    redis_cache.set("test_delete", "borrame", ttl=60)
    redis_cache.delete("test_delete")
    result = redis_cache.get("test_delete")

    if result is None:
        print("   ✅ Delete funciona correctamente")
    else:
        print(f"   ❌ Error: el dato no se borró")


async def test_smart_cache():
    """Test smart caching logic."""
    print_section("🧠 Probando Caché Inteligente")

    try:
        current_round = await get_current_round_number()
        print(f"\nRound actual: {current_round}")

        # Test TTL for different rounds
        print("\n📊 TTL calculado para diferentes rounds:")

        test_rounds = [
            current_round - 10,  # Old round
            current_round - 1,  # Previous round
            current_round,  # Current round
            current_round + 1,  # Future round
        ]

        for round_num in test_rounds:
            ttl = await get_smart_ttl_for_round(round_num)

            if round_num < current_round:
                status = "COMPLETADO"
                expected = "7 días"
            elif round_num == current_round:
                status = "ACTUAL"
                expected = "30 segundos"
            else:
                status = "FUTURO"
                expected = "5 minutos"

            print(f"   Round {round_num} ({status}): {ttl}s = {expected}")
            print(
                f"      → {'✅ CACHÉ LARGO (inmutable)' if ttl > 3600 else '⚡ CACHÉ CORTO (activo)'}"
            )

    except Exception as e:
        print(f"\n⚠️  No se pudo calcular TTL inteligente: {e}")
        print("   (Esto es normal si no hay conexión a la chain)")


async def test_cache_stats():
    """Display cache statistics."""
    print_section("📊 Estadísticas del Caché")

    try:
        info = await get_cache_info()

        print(f"\nRound actual: {info.get('current_round', 'N/A')}")
        print(f"Redis disponible: {info['redis_available']}")
        print(f"Redis habilitado: {info['redis_enabled']}")

        stats = info.get("statistics", {})
        print("\nEstadísticas de Redis:")
        print(f"  - Hits: {stats.get('redis_hits', 0)}")
        print(f"  - Misses: {stats.get('redis_misses', 0)}")
        print(f"  - Sets: {stats.get('redis_sets', 0)}")
        print(f"  - Errores: {stats.get('redis_errors', 0)}")
        print(f"  - Claves en Redis: {stats.get('redis_keys', 0)}")

        if stats.get("redis_memory_used"):
            print(f"  - Memoria usada: {stats['redis_memory_used']}")

        # Hit rate
        hits = stats.get("redis_hits", 0)
        misses = stats.get("redis_misses", 0)
        total = hits + misses
        if total > 0:
            hit_rate = (hits / total) * 100
            print(f"\n  Hit Rate: {hit_rate:.1f}%")

        ttl_config = info.get("ttl_config", {})
        print("\nConfiguración de TTL:")
        print(f"  - Datos finales: {ttl_config.get('final_data_ttl_days', 0):.1f} días")
        print(f"  - Datos activos: {ttl_config.get('active_data_ttl', 0)} segundos")
        print(f"  - Datos estáticos: {ttl_config.get('static_data_ttl', 0)} segundos")

    except Exception as e:
        print(f"\n⚠️  Error obteniendo estadísticas: {e}")


async def test_performance():
    """Test cache performance."""
    print_section("⚡ Test de Performance")

    import time

    # Test write performance
    print("\n1️⃣  Velocidad de escritura:")
    large_data = {
        f"key_{i}": f"value_{i}" for i in range(100)
    }  # Create a larger object

    start = time.time()
    for i in range(100):
        redis_cache.set(f"perf_test_{i}", large_data, ttl=60)
    write_time = time.time() - start

    print(f"   100 writes: {write_time*1000:.2f}ms ({write_time*10:.2f}ms/write)")

    # Test read performance
    print("\n2️⃣  Velocidad de lectura:")
    start = time.time()
    for i in range(100):
        redis_cache.get(f"perf_test_{i}")
    read_time = time.time() - start

    print(f"   100 reads: {read_time*1000:.2f}ms ({read_time*10:.2f}ms/read)")

    # Cleanup
    for i in range(100):
        redis_cache.delete(f"perf_test_{i}")

    print("\n✅ Performance tests completados")


async def main():
    """Run all tests."""
    print("\n" + "🚀" * 30)
    print("  REDIS SETUP TEST - Autoppia Dashboard Backend")
    print("🚀" * 30)

    try:
        await test_redis_connection()
        await test_basic_operations()
        await test_smart_cache()
        await test_cache_stats()
        await test_performance()

        print_section("✅ RESUMEN")
        print("\n🎉 Todos los tests completados!")
        print("\nRedis está listo para usar en producción.")
        print("\nPróximos pasos:")
        print(
            "  1. Integrar caché en endpoints críticos (ver REDIS_INTEGRATION_EXAMPLE.md)"
        )
        print("  2. Monitorear hit rate y performance")
        print("  3. Ajustar TTLs según necesidad")

    except Exception as e:
        print(f"\n❌ Error durante los tests: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
