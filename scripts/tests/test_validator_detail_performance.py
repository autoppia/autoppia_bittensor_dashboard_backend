#!/usr/bin/env python3
"""
Script para probar el rendimiento del endpoint /api/v1/validators/{id}/details
"""

import asyncio
import sys
import time
from pathlib import Path

# Agregar el directorio raíz del proyecto al path
backend_root = Path(__file__).parent.parent
sys.path.insert(0, str(backend_root))

# Cambiar el directorio de trabajo al raíz del backend
import os  # noqa: E402

os.chdir(backend_root)

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.services.ui.overview_service import OverviewService  # noqa: E402


async def test_validator_detail_performance(validator_id: str = "83"):
    """Prueba el rendimiento del método validator_detail"""

    # Ajustar el formato del ID si es necesario
    if validator_id.isdigit():
        validator_key = f"validator-{validator_id}"
    else:
        validator_key = validator_id

    print(f"\n{'=' * 60}")
    print(f"Probando validator_detail para validador: {validator_id}")
    print(f"   Clave a buscar: {validator_key}")
    print(f"{'=' * 60}\n")

    async with AsyncSessionLocal() as session:
        service = OverviewService(session)

        # Primero, listar validadores disponibles para verificar
        print("📋 Listando validadores disponibles...")
        try:
            validators = await service._aggregate_validators()
            print(f"   ✅ Encontrados {len(validators)} validadores")
            if validators:
                print(f"   Primeros 5: {list(validators.keys())[:5]}")
                if validator_key not in validators:
                    print(f"   ⚠️  '{validator_key}' no encontrado en la lista")
                    # Intentar buscar variaciones
                    for key in validators.keys():
                        if validator_id in key:
                            print(f"   💡 Clave similar encontrada: {key}")
                            validator_key = key
                            break
                else:
                    print(f"   ✅ '{validator_key}' encontrado en la lista")
        except Exception as e:
            print(f"   ❌ Error al listar validadores: {e}")
            import traceback

            traceback.print_exc()
            return

        print()

        # Primera llamada (sin cache)
        print(f"🔄 Primera llamada (sin cache) con clave '{validator_key}'...")
        start_time = time.time()
        try:
            result = await service.validator_detail(validator_key)
            elapsed = time.time() - start_time
            print(f"✅ Primera llamada completada en {elapsed:.3f} segundos")
            print(f"   Resultado tiene {len(result)} campos")
            if "lastRoundWinner" in result:
                print(f"   ✅ lastRoundWinner encontrado: UID {result['lastRoundWinner'].get('uid')}")
            else:
                print("   ⚠️  lastRoundWinner no encontrado")
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"❌ Error después de {elapsed:.3f} segundos: {e}")
            import traceback

            traceback.print_exc()
            return

        # Segunda llamada (con cache de _aggregate_validators)
        print("\n🔄 Segunda llamada (con cache de _aggregate_validators)...")
        start_time = time.time()
        try:
            result = await service.validator_detail(validator_key)
            elapsed = time.time() - start_time
            print(f"✅ Segunda llamada completada en {elapsed:.3f} segundos")
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"❌ Error después de {elapsed:.3f} segundos: {e}")
            import traceback

            traceback.print_exc()
            return

        # Medición detallada de cada paso
        print(f"\n{'=' * 60}")
        print("Medición detallada de cada paso:")
        print(f"{'=' * 60}\n")

        # Paso 1: _aggregate_validators
        print("1️⃣  Probando _aggregate_validators()...")
        start_time = time.time()
        try:
            validators = await service._aggregate_validators()
            elapsed = time.time() - start_time
            print(f"   ⏱️  Tiempo: {elapsed:.3f} segundos")
            print(f"   📊 Validadores encontrados: {len(validators)}")
            if validator_key in validators:
                print(f"   ✅ Validador {validator_key} encontrado")
            else:
                print(f"   ⚠️  Validador {validator_key} NO encontrado")
                print(f"   Claves disponibles: {list(validators.keys())[:10]}...")
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"   ❌ Error después de {elapsed:.3f} segundos: {e}")
            import traceback

            traceback.print_exc()

        # Paso 2: Query de última ronda y ganador
        print("\n2️⃣  Probando queries de última ronda y ganador...")
        try:
            validator_uid = int(validator_id.split("-")[-1]) if "-" in validator_id else int(validator_id)

            from sqlalchemy import select

            from app.db.models import (
                AgentEvaluationRunORM,
                RoundORM,
                ValidatorRoundMinerORM,
                ValidatorRoundSummaryORM,
                ValidatorRoundValidatorORM,
            )

            # Query optimizada para ganador finalizado
            print("   🔍 Query 1: Ganador de ronda finalizada...")
            start_time = time.time()
            winner_query = (
                select(
                    RoundORM.validator_round_id,
                    RoundORM.round_number,
                    ValidatorRoundSummaryORM.miner_uid,
                    ValidatorRoundSummaryORM.miner_hotkey,
                    ValidatorRoundSummaryORM.post_consensus_avg_reward,
                    ValidatorRoundSummaryORM.weight,
                    ValidatorRoundMinerORM.name,
                    ValidatorRoundMinerORM.image_url,
                )
                .select_from(
                    RoundORM.__table__.join(ValidatorRoundValidatorORM.__table__, RoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
                    .join(ValidatorRoundSummaryORM.__table__, RoundORM.validator_round_id == ValidatorRoundSummaryORM.validator_round_id)
                    .outerjoin(
                        ValidatorRoundMinerORM.__table__,
                        (RoundORM.validator_round_id == ValidatorRoundMinerORM.validator_round_id) & (ValidatorRoundSummaryORM.miner_uid == ValidatorRoundMinerORM.miner_uid),
                    )
                )
                .where(ValidatorRoundValidatorORM.validator_uid == validator_uid, ValidatorRoundSummaryORM.post_consensus_rank == 1)
                .order_by(RoundORM.round_number.desc())
                .limit(1)
            )
            winner_result = await session.execute(winner_query)
            winner_row = winner_result.first()
            elapsed = time.time() - start_time
            print(f"   ⏱️  Tiempo: {elapsed:.3f} segundos")
            if winner_row:
                print(f"   ✅ Ganador encontrado: UID {winner_row.miner_uid}")
            else:
                print("   ⚠️  No se encontró ganador de ronda finalizada")

                # Query alternativa para ronda activa
                print("   🔍 Query 2: Top miner de ronda activa...")
                start_time = time.time()
                top_run_query = (
                    select(
                        RoundORM.validator_round_id,
                        AgentEvaluationRunORM.miner_uid,
                        AgentEvaluationRunORM.miner_hotkey,
                        AgentEvaluationRunORM.average_reward,
                        ValidatorRoundMinerORM.name,
                        ValidatorRoundMinerORM.image_url,
                    )
                    .select_from(
                        RoundORM.__table__.join(ValidatorRoundValidatorORM.__table__, RoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
                        .join(AgentEvaluationRunORM.__table__, RoundORM.validator_round_id == AgentEvaluationRunORM.validator_round_id)
                        .outerjoin(
                            ValidatorRoundMinerORM.__table__,
                            (RoundORM.validator_round_id == ValidatorRoundMinerORM.validator_round_id) & (AgentEvaluationRunORM.miner_uid == ValidatorRoundMinerORM.miner_uid),
                        )
                    )
                    .where(ValidatorRoundValidatorORM.validator_uid == validator_uid)
                    .order_by(RoundORM.round_number.desc(), AgentEvaluationRunORM.average_reward.desc())
                    .limit(1)
                )
                top_run_result = await session.execute(top_run_query)
                top_run_row = top_run_result.first()
                elapsed = time.time() - start_time
                print(f"   ⏱️  Tiempo: {elapsed:.3f} segundos")
                if top_run_row:
                    print(f"   ✅ Top miner encontrado: UID {top_run_row.miner_uid}")
                else:
                    print("   ⚠️  No se encontró top miner")
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"   ❌ Error después de {elapsed:.3f} segundos: {e}")
            import traceback

            traceback.print_exc()

        print(f"\n{'=' * 60}")
        print("✅ Prueba completada")
        print(f"{'=' * 60}\n")


if __name__ == "__main__":
    validator_id = sys.argv[1] if len(sys.argv) > 1 else "83"
    asyncio.run(test_validator_detail_performance(validator_id))
