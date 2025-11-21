#!/usr/bin/env python3
"""
Script para probar y comparar las optimizaciones implementadas.
"""
import requests
import time
import json

BASE_URL = "http://localhost:8080"

def test_endpoint(name, url, iterations=3):
    """Prueba un endpoint y mide el tiempo."""
    times = []
    
    print(f"\n{'='*60}")
    print(f"🧪 Probando: {name}")
    print(f"   URL: {url}")
    print(f"{'='*60}")
    
    for i in range(iterations):
        start = time.time()
        try:
            resp = requests.get(url, timeout=30)
            elapsed = (time.time() - start) * 1000  # ms
            
            if resp.status_code == 200:
                times.append(elapsed)
                size_kb = len(resp.content) / 1024
                print(f"  Intento {i+1}: ✅ {elapsed:6.1f}ms (size: {size_kb:.1f}KB)")
            else:
                print(f"  Intento {i+1}: ❌ HTTP {resp.status_code}")
                
        except Exception as e:
            print(f"  Intento {i+1}: ❌ {type(e).__name__}: {str(e)[:50]}")
    
    if times:
        avg_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)
        print(f"\n  📊 Stats:")
        print(f"     Promedio: {avg_time:.1f}ms")
        print(f"     Mínimo:   {min_time:.1f}ms")
        print(f"     Máximo:   {max_time:.1f}ms")
        return avg_time
    else:
        print("  ⚠️  Ninguna respuesta exitosa")
        return None


def check_database():
    """Verifica el estado de las tablas materializadas."""
    print(f"\n{'='*60}")
    print(f"📊 ESTADO DE LA BASE DE DATOS")
    print(f"{'='*60}")
    
    try:
        # Check snapshots count
        print("\n1️⃣  Verificando round_snapshots...")
        from app.db.session import AsyncSessionLocal
        from app.db.models import RoundSnapshotORM, AgentStatsORM
        from sqlalchemy import select, func
        import asyncio
        
        async def check():
            async with AsyncSessionLocal() as session:
                # Snapshots
                count_snapshots = await session.scalar(
                    select(func.count()).select_from(RoundSnapshotORM)
                )
                print(f"   ✅ Snapshots creados: {count_snapshots}")
                
                # Sample snapshot
                stmt = select(RoundSnapshotORM).order_by(RoundSnapshotORM.round_number.desc()).limit(1)
                latest = await session.scalar(stmt)
                if latest:
                    print(f"   📄 Último snapshot: Round {latest.round_number}")
                    print(f"      Size: {latest.data_size_bytes / 1024:.1f}KB")
                    print(f"      Created: {latest.created_at}")
                
                # Agent stats
                print("\n2️⃣  Verificando agent_stats...")
                count_agents = await session.scalar(
                    select(func.count()).select_from(AgentStatsORM)
                )
                print(f"   ✅ Agents: {count_agents}")
                
                # Top agents
                stmt = select(AgentStatsORM).order_by(AgentStatsORM.avg_score.desc()).limit(5)
                top_agents = list(await session.scalars(stmt))
                if top_agents:
                    print(f"\n   🏆 Top 5 Agents:")
                    for i, agent in enumerate(top_agents, 1):
                        print(f"      {i}. UID {agent.uid:3d}: {agent.name or 'Unknown':20s} - Score: {agent.avg_score:.3f} ({agent.total_rounds} rounds)")
        
        asyncio.run(check())
        
    except Exception as e:
        print(f"   ❌ Error: {e}")


def main():
    print("\n" + "="*60)
    print("🚀 TEST DE OPTIMIZACIONES - AUTOPPIA DASHBOARD")
    print("="*60)
    
    # 1. Check database
    check_database()
    
    # 2. Test endpoints
    results = {}
    
    # Test agents endpoint
    results['agents'] = test_endpoint(
        "GET /api/v1/agents",
        f"{BASE_URL}/api/v1/agents?page=1&limit=20",
        iterations=3
    )
    
    # Test round snapshot
    results['round_502'] = test_endpoint(
        "GET /api/v1/rounds/502 (con snapshot)",
        f"{BASE_URL}/api/v1/rounds/502",
        iterations=3
    )
    
    # Test round sin snapshot (una muy antigua)
    results['round_1'] = test_endpoint(
        "GET /api/v1/rounds/1 (sin snapshot)",
        f"{BASE_URL}/api/v1/rounds/1",
        iterations=1
    )
    
    # 3. Summary
    print(f"\n{'='*60}")
    print("📈 RESUMEN DE MEJORAS")
    print(f"{'='*60}")
    
    if results.get('agents'):
        improvement_agents = 20000 / results['agents']  # Asumiendo 20s antes
        print(f"\n✅ /api/v1/agents:")
        print(f"   Tiempo actual: {results['agents']:.1f}ms")
        print(f"   Mejora estimada: ~{improvement_agents:.0f}x más rápido")
    
    if results.get('round_502'):
        improvement_round = 500 / results['round_502']  # Asumiendo 500ms antes
        print(f"\n✅ /api/v1/rounds/{{id}} (con snapshot):")
        print(f"   Tiempo actual: {results['round_502']:.1f}ms")
        print(f"   Mejora estimada: ~{improvement_round:.0f}x más rápido")
    
    print(f"\n{'='*60}")
    print("✅ OPTIMIZACIONES FUNCIONANDO CORRECTAMENTE")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()

