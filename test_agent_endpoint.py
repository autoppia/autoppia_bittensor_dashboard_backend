#!/usr/bin/env python3
"""
Test endpoint optimizado para /agents/{uid}?round={round_number}
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.db.session import AsyncSessionLocal
from sqlalchemy import select
from app.db.models import AgentStatsORM, RoundSnapshotORM


async def get_agent_with_round(agent_uid: int, round_number: int = None):
    """
    Endpoint optimizado que devuelve:
    1. Perfil del agente (agent_stats)
    2. Datos de la round específica (round_snapshots filtrado)
    3. Historical (lista de rounds)
    """
    async with AsyncSessionLocal() as session:
        # 1. Get agent stats (perfil global)
        stmt = select(AgentStatsORM).where(AgentStatsORM.uid == agent_uid)
        agent_stats = await session.scalar(stmt)
        
        if not agent_stats:
            print(f"❌ Agent {agent_uid} not found in agent_stats")
            return None
        
        print(f"\n✅ Agent {agent_uid} found:")
        print(f"   Name: {agent_stats.name}")
        print(f"   Total rounds: {agent_stats.total_rounds}")
        print(f"   Avg score: {agent_stats.avg_score:.3f}")
        print(f"   Best rank: {agent_stats.best_rank}")
        
        # 2. Get round data if specified
        if round_number:
            stmt = select(RoundSnapshotORM).where(RoundSnapshotORM.round_number == round_number)
            round_snapshot = await session.scalar(stmt)
            
            if not round_snapshot:
                print(f"\n⚠️  Round {round_number} snapshot not found")
                return None
            
            snapshot_json = round_snapshot.snapshot_json
            validator_rounds = snapshot_json.get("validatorRounds", [])
            
            print(f"\n✅ Round {round_number} snapshot found:")
            print(f"   Validators: {len(validator_rounds)}")
            
            # Extract agent data from all validators
            agent_data_in_round = []
            for vr in validator_rounds:
                agent_runs = vr.get("agentEvaluationRuns", [])
                for run in agent_runs:
                    if run.get("miner_uid") == agent_uid:
                        agent_data_in_round.append({
                            "validator_uid": vr.get("validatorUid"),
                            "validator_name": vr.get("validatorName"),
                            "score": run.get("average_score", 0),
                            "rank": run.get("rank"),
                            "tasks": run.get("total_tasks", 0),
                            "completed": run.get("completed_tasks", 0),
                        })
            
            print(f"\n📊 Agent {agent_uid} in round {round_number}:")
            for data in agent_data_in_round:
                print(f"   Validator {data['validator_uid']} ({data['validator_name']}): score={data['score']:.3f}, rank={data['rank']}")
            
            return {
                "agent": agent_stats,
                "round_data": agent_data_in_round,
                "round_number": round_number
            }
        
        # 3. Get historical (all rounds where agent participated)
        # Query all snapshots and check if agent is in them
        stmt = select(RoundSnapshotORM.round_number).order_by(RoundSnapshotORM.round_number.desc()).limit(10)
        recent_round_numbers = list(await session.scalars(stmt))
        
        print(f"\n📜 Recent rounds in DB: {recent_round_numbers}")
        
        return {
            "agent": agent_stats,
            "recent_rounds": recent_round_numbers
        }


async def main():
    print("="*60)
    print("🧪 Testing Agent Endpoint")
    print("="*60)
    
    # Test 1: Agent without round
    print("\n1️⃣  Testing: GET /agents/105")
    result1 = await get_agent_with_round(105)
    
    # Test 2: Agent with round
    print("\n\n2️⃣  Testing: GET /agents/105?round=60")
    result2 = await get_agent_with_round(105, 60)
    
    print("\n" + "="*60)
    print("✅ Tests complete!")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())


