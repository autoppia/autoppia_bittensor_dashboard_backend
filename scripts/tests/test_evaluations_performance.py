#!/usr/bin/env python3
"""
Script de prueba de rendimiento para consultas de evaluations.

Este script mide el tiempo de ejecución de las consultas más comunes
para verificar la efectividad de las optimizaciones aplicadas.

Uso:
    python scripts/tests/test_evaluations_performance.py
"""

import asyncio
import os
import sys
import time
from typing import Dict, List, Tuple

# Agregar el directorio raíz al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload, sessionmaker

from app.config import settings
from app.db.models import (
    EvaluationORM,
    TaskORM,
)


class PerformanceTester:
    """Probador de rendimiento de consultas de evaluations."""

    def __init__(self):
        self.engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
        )
        self.async_session = sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self.results: List[Dict] = []

    async def close(self):
        """Cerrar conexión a la base de datos."""
        await self.engine.dispose()

    async def run_query(self, name: str, query_func, *args, **kwargs) -> Tuple[float, int]:
        """
        Ejecutar una query y medir su tiempo de ejecución.

        Returns:
            Tuple de (tiempo_segundos, num_resultados)
        """
        async with self.async_session() as session:
            start = time.time()
            result = await query_func(session, *args, **kwargs)
            elapsed = time.time() - start

            # Obtener número de resultados
            if isinstance(result, list):
                count = len(result)
            elif hasattr(result, "all"):
                items = result.all()
                count = len(items)
            elif isinstance(result, int):
                count = result
            else:
                count = 1

            self.results.append(
                {
                    "name": name,
                    "time": elapsed,
                    "count": count,
                    "time_per_item": elapsed / count if count > 0 else 0,
                }
            )

            return elapsed, count

    def print_results(self):
        """Imprimir resultados de todas las pruebas."""
        print("\n" + "=" * 80)
        print("RESULTADOS DE PRUEBAS DE RENDIMIENTO")
        print("=" * 80)
        print(f"{'Prueba':<50} {'Tiempo':<12} {'Registros':<12} {'ms/reg':<12}")
        print("-" * 80)

        for result in self.results:
            time_str = f"{result['time']:.3f}s"
            count_str = str(result["count"])
            per_item_str = f"{result['time_per_item'] * 1000:.2f}ms"

            print(f"{result['name']:<50} {time_str:<12} {count_str:<12} {per_item_str:<12}")

        print("=" * 80)

        # Calcular tiempo total
        total_time = sum(r["time"] for r in self.results)
        print(f"\nTiempo total: {total_time:.3f}s")
        print(f"Promedio por query: {total_time / len(self.results):.3f}s")

        # Identificar queries más lentas
        sorted_results = sorted(self.results, key=lambda x: x["time"], reverse=True)
        print("\n🐌 Top 3 queries más lentas:")
        for i, result in enumerate(sorted_results[:3], 1):
            print(f"  {i}. {result['name']}: {result['time']:.3f}s ({result['count']} registros)")

        # Identificar queries más rápidas
        print("\n🚀 Top 3 queries más rápidas:")
        for i, result in enumerate(reversed(sorted_results[-3:]), 1):
            print(f"  {i}. {result['name']}: {result['time']:.3f}s ({result['count']} registros)")


# =============================================================================
# QUERIES DE PRUEBA
# =============================================================================


async def query_count_all(session: AsyncSession) -> int:
    """Contar todas las evaluaciones."""
    result = await session.scalar(select(func.count()).select_from(EvaluationORM))
    return result or 0


async def query_recent_evaluations(session: AsyncSession, limit: int = 100):
    """Obtener evaluaciones recientes (caso común)."""
    stmt = select(EvaluationORM).order_by(EvaluationORM.created_at.desc()).limit(limit)
    result = await session.scalars(stmt)
    return result.all()


async def query_evaluations_with_joins(session: AsyncSession, limit: int = 100):
    """Obtener evaluaciones con JOINs (caso común en list_evaluations)."""
    stmt = (
        select(EvaluationORM)
        .options(
            selectinload(EvaluationORM.task),
            selectinload(EvaluationORM.task_solution),
            selectinload(EvaluationORM.agent_run),
        )
        .order_by(EvaluationORM.created_at.desc())
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return result.all()


async def query_by_validator(session: AsyncSession, validator_uid: int, limit: int = 1000):
    """Filtrar por validator_uid (caso común en /validators/{uid}/details)."""
    stmt = select(EvaluationORM).where(EvaluationORM.validator_uid == validator_uid).limit(limit)
    result = await session.scalars(stmt)
    return result.all()


async def query_by_validator_with_task(session: AsyncSession, validator_uid: int, limit: int = 1000):
    """Filtrar por validator con JOIN a tasks."""
    stmt = select(EvaluationORM).join(TaskORM, EvaluationORM.task_id == TaskORM.task_id).where(EvaluationORM.validator_uid == validator_uid).options(selectinload(EvaluationORM.task)).limit(limit)
    result = await session.scalars(stmt)
    return result.all()


async def query_by_task(session: AsyncSession, task_id: str):
    """Filtrar por task_id."""
    stmt = select(EvaluationORM).where(EvaluationORM.task_id == task_id)
    result = await session.scalars(stmt)
    return result.all()


async def query_by_score_range(session: AsyncSession, limit: int = 1000):
    """Filtrar por rango de score (completed evaluations)."""
    stmt = select(EvaluationORM).where(EvaluationORM.evaluation_score >= 0.7).limit(limit)
    result = await session.scalars(stmt)
    return result.all()


async def query_paginated(session: AsyncSession, page: int = 1, limit: int = 50):
    """Query paginado (caso optimizado)."""
    skip = (page - 1) * limit
    stmt = select(EvaluationORM).order_by(EvaluationORM.id.desc()).offset(skip).limit(limit)
    result = await session.scalars(stmt)
    return result.all()


async def query_count_by_validator(session: AsyncSession, validator_uid: int) -> int:
    """Contar evaluaciones de un validator."""
    result = await session.scalar(select(func.count()).select_from(EvaluationORM).where(EvaluationORM.validator_uid == validator_uid))
    return result or 0


async def query_aggregation(session: AsyncSession, validator_uid: int):
    """Agregación por validator (stats)."""
    stmt = select(
        func.count(EvaluationORM.id).label("total"),
        func.avg(EvaluationORM.evaluation_score).label("avg_score"),
        func.sum(func.cast(EvaluationORM.evaluation_score >= 0.7, type_=func.Integer())).label("success_count"),
    ).where(EvaluationORM.validator_uid == validator_uid)

    result = await session.execute(stmt)
    return result.first()


# =============================================================================
# MAIN
# =============================================================================


async def main():
    """Ejecutar todas las pruebas de rendimiento."""
    print("🧪 Iniciando pruebas de rendimiento de evaluations...")
    print(f"Base de datos: {settings.database_url.split('@')[-1]}")

    tester = PerformanceTester()

    try:
        # Obtener información básica
        async with tester.async_session() as session:
            total_evals = await session.scalar(select(func.count()).select_from(EvaluationORM))

            # Obtener un validator_uid válido
            validator_uid = await session.scalar(select(EvaluationORM.validator_uid).where(EvaluationORM.validator_uid.isnot(None)).limit(1))

            # Obtener un task_id válido
            task_id = await session.scalar(select(EvaluationORM.task_id).limit(1))

        print(f"\n📊 Total de evaluaciones: {total_evals:,}")
        print(f"🎯 Validator UID de prueba: {validator_uid}")
        print(f"📝 Task ID de prueba: {task_id}")
        print("\nEjecutando queries...\n")

        # =================================================================
        # EJECUTAR PRUEBAS
        # =================================================================

        # 1. Count simple
        elapsed, count = await tester.run_query("1. Count total de evaluaciones", query_count_all)
        print(f"✓ Count total: {count:,} registros en {elapsed:.3f}s")

        # 2. Recent evaluations sin JOINs
        elapsed, count = await tester.run_query("2. Evaluaciones recientes (100, sin JOINs)", query_recent_evaluations, 100)
        print(f"✓ Evaluaciones recientes: {count} registros en {elapsed:.3f}s")

        # 3. Recent evaluations con JOINs
        elapsed, count = await tester.run_query("3. Evaluaciones recientes (100, con JOINs)", query_evaluations_with_joins, 100)
        print(f"✓ Evaluaciones con JOINs: {count} registros en {elapsed:.3f}s")

        # 4. Filtro por validator (sin JOINs)
        elapsed, count = await tester.run_query("4. Por validator_uid (1000, sin JOINs)", query_by_validator, validator_uid, 1000)
        print(f"✓ Por validator: {count} registros en {elapsed:.3f}s")

        # 5. Filtro por validator (con JOIN a tasks)
        elapsed, count = await tester.run_query("5. Por validator_uid (1000, con JOIN tasks)", query_by_validator_with_task, validator_uid, 1000)
        print(f"✓ Por validator + tasks: {count} registros en {elapsed:.3f}s")

        # 6. Filtro por task_id
        elapsed, count = await tester.run_query("6. Por task_id", query_by_task, task_id)
        print(f"✓ Por task_id: {count} registros en {elapsed:.3f}s")

        # 7. Filtro por score range
        elapsed, count = await tester.run_query("7. Por score range (evaluation_score >= 0.7)", query_by_score_range, 1000)
        print(f"✓ Por score range: {count} registros en {elapsed:.3f}s")

        # 8. Query paginado
        elapsed, count = await tester.run_query("8. Paginado (página 1, 50 por página)", query_paginated, 1, 50)
        print(f"✓ Paginado: {count} registros en {elapsed:.3f}s")

        # 9. Count por validator
        elapsed, count = await tester.run_query("9. Count por validator_uid", query_count_by_validator, validator_uid)
        print(f"✓ Count por validator: {count} registros en {elapsed:.3f}s")

        # 10. Agregación
        elapsed, count = await tester.run_query("10. Agregación (stats) por validator", query_aggregation, validator_uid)
        print(f"✓ Agregación: completada en {elapsed:.3f}s")

        # =================================================================
        # IMPRIMIR RESULTADOS
        # =================================================================

        tester.print_results()

        # =================================================================
        # RECOMENDACIONES
        # =================================================================

        print("\n" + "=" * 80)
        print("💡 RECOMENDACIONES")
        print("=" * 80)

        avg_time = sum(r["time"] for r in tester.results) / len(tester.results)

        if avg_time > 2.0:
            print("⚠️  Rendimiento MALO: Tiempo promedio > 2s")
            print("   Acciones recomendadas:")
            print("   1. Verificar que los índices estén creados (optimize_evaluations_performance.sql)")
            print("   2. Ejecutar ANALYZE en las tablas")
            print("   3. Considerar archivado de datos antiguos")
        elif avg_time > 1.0:
            print("⚠️  Rendimiento MEDIO: Tiempo promedio > 1s")
            print("   Acciones recomendadas:")
            print("   1. Verificar uso de índices con EXPLAIN ANALYZE")
            print("   2. Considerar archivado de datos antiguos")
        else:
            print("✅ Rendimiento BUENO: Tiempo promedio < 1s")
            print("   Las optimizaciones están funcionando correctamente.")

        # Verificar queries muy lentas
        slow_queries = [r for r in tester.results if r["time"] > 5.0]
        if slow_queries:
            print(f"\n⚠️  Atención: {len(slow_queries)} queries tardaron más de 5s:")
            for q in slow_queries:
                print(f"   - {q['name']}: {q['time']:.3f}s")

        print("\n" + "=" * 80)

    except Exception as e:
        print(f"\n❌ Error durante las pruebas: {e}")
        raise
    finally:
        await tester.close()


if __name__ == "__main__":
    asyncio.run(main())
