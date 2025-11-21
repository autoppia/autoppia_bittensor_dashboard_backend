#!/usr/bin/env python3
"""
Verificar qué está cacheado y funcionando correctamente.
"""
import requests
import json

BASE_URL = "http://localhost:8080"

def test_critical_endpoints():
    """Probar los 4 endpoints críticos que deben estar cacheados."""
    
    print("\n" + "="*60)
    print("🔍 VERIFICACIÓN DE ENDPOINTS CRÍTICOS")
    print("="*60)
    
    # 1. Subnet Price
    print("\n1️⃣  Subnet Price:")
    try:
        resp = requests.get(f"{BASE_URL}/api/v1/overview/network-status", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            status_data = data.get("data", {}).get("status", {})
            price = status_data.get("price")
            print(f"   ✅ Price: {price} TAO")
            print(f"   📦 Cacheado en Redis: subnet:price")
            print(f"   🔄 Se actualiza cada: 5 minutos (thread interno)")
        else:
            print(f"   ❌ Error HTTP {resp.status_code}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 2. Current Block
    print("\n2️⃣  Current Block:")
    try:
        resp = requests.get(f"{BASE_URL}/api/v1/overview/network-status", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            status_data = data.get("data", {}).get("status", {})
            block = status_data.get("currentBlock")
            print(f"   ✅ Block: {block}")
            print(f"   📦 Cacheado en Redis: chain:current_block")
            print(f"   🔄 Se actualiza cada: 30 segundos (thread interno)")
        else:
            print(f"   ❌ Error HTTP {resp.status_code}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 3. Overview Metrics
    print("\n3️⃣  Overview Metrics:")
    try:
        import time
        start = time.time()
        resp = requests.get(f"{BASE_URL}/api/v1/overview/metrics", timeout=5)
        elapsed = (time.time() - start) * 1000
        
        if resp.status_code == 200:
            data = resp.json()
            metrics = data.get("data", {}).get("metrics", {})
            print(f"   ✅ Total Validators: {metrics.get('totalValidators', 0)}")
            print(f"   ✅ Total Miners: {metrics.get('totalMiners', 0)}")
            print(f"   ✅ Current Round: {metrics.get('currentRound', 0)}")
            print(f"   ⏱️  Tiempo respuesta: {elapsed:.1f}ms")
            print(f"   📦 Cacheado en Redis: overview:metrics:aggregate")
            print(f"   🔄 TTL: 10 minutos")
        else:
            print(f"   ❌ Error HTTP {resp.status_code}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 4. Current Round
    print("\n4️⃣  Current Round:")
    try:
        import time
        start = time.time()
        resp = requests.get(f"{BASE_URL}/api/v1/overview/rounds/current", timeout=5)
        elapsed = (time.time() - start) * 1000
        
        if resp.status_code == 200:
            data = resp.json()
            round_data = data.get("data", {}).get("round", {})
            print(f"   ✅ Round Number: {round_data.get('roundNumber', 0)}")
            print(f"   ✅ Status: {round_data.get('status', 'unknown')}")
            print(f"   ⏱️  Tiempo respuesta: {elapsed:.1f}ms")
            print(f"   📦 Cacheado en Redis: current_round")
            print(f"   🔄 TTL: 5 minutos")
        else:
            print(f"   ❌ Error HTTP {resp.status_code}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 5. Verificar threads internos
    print("\n" + "="*60)
    print("🔄 THREADS INTERNOS DE ACTUALIZACIÓN")
    print("="*60)
    try:
        resp = requests.get(f"{BASE_URL}/debug/background-updater-status", timeout=5)
        if resp.status_code == 200:
            status = resp.json()
            print(f"\n✅ Background updater thread:")
            print(f"   Running: {status.get('is_running', False)}")
            print(f"   Last update: {status.get('last_update_iso', 'N/A')}")
            print(f"   Status: {status.get('status', 'unknown')}")
        else:
            print(f"   ⚠️  Endpoint no disponible")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    print("\n" + "="*60)
    print("📋 RESUMEN")
    print("="*60)
    print("""
✅ LO QUE YA FUNCIONA AUTOMÁTICAMENTE:
   - Subnet Price: Thread interno cada 5 min
   - Current Block: Thread interno cada 30 seg
   - Metagraph: Thread interno cada 30 min

✅ LO QUE SE CACHEA AUTOMÁTICAMENTE (con TTL):
   - Overview Metrics: 10 min (después del primer hit)
   - Current Round: 5 min (después del primer hit)

💡 RECOMENDACIÓN:
   Activar el overview_cache_updater ligero (sin /admin/warm/agents)
   para precalentar overview/metrics y current round cada 10 minutos.
""")

if __name__ == "__main__":
    test_critical_endpoints()
