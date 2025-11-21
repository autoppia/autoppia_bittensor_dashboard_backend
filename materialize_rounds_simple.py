#!/usr/bin/env python3
"""
Script simple para materializar todas las rounds usando el endpoint.
"""
import requests
import time
import sys

BASE_URL = "http://localhost:8080"

def materialize_all():
    # Obtener la última round
    try:
        resp = requests.get(f"{BASE_URL}/api/v1/rounds?page=1&limit=1", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        last_round = data.get("data", {}).get("rounds", [{}])[0].get("roundNumber", 0)
    except Exception as e:
        print(f"❌ Error obteniendo última round: {e}")
        return
    
    if not last_round:
        print("❌ No se pudo obtener la última round")
        return
    
    print(f"📊 Última round: {last_round}")
    print(f"🎯 Materializando desde round 1 hasta {last_round}")
    print("")
    
    success = 0
    skipped = 0
    failed = 0
    
    for round_num in range(1, last_round + 1):
        try:
            resp = requests.post(
                f"{BASE_URL}/admin/materialize-round/{round_num}",
                timeout=30
            )
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("already_existed"):
                    print(f"Round {round_num:3d}: ⏭️  Ya existía")
                    skipped += 1
                else:
                    size_kb = data.get("data_size_kb", 0)
                    print(f"Round {round_num:3d}: ✅ Materializada ({size_kb:.1f}KB)")
                    success += 1
            elif resp.status_code == 404:
                print(f"Round {round_num:3d}: ⚠️  No encontrada o no completada")
                failed += 1
            else:
                print(f"Round {round_num:3d}: ❌ Error HTTP {resp.status_code}")
                failed += 1
                
        except requests.exceptions.Timeout:
            print(f"Round {round_num:3d}: ❌ Timeout")
            failed += 1
        except Exception as e:
            print(f"Round {round_num:3d}: ❌ {type(e).__name__}: {str(e)[:50]}")
            failed += 1
        
        # Pausa para no saturar
        time.sleep(0.05)
    
    print("")
    print("=" * 50)
    print(f"Resumen:")
    print(f"  ✅ Materializadas: {success}")
    print(f"  ⏭️  Ya existían:    {skipped}")
    print(f"  ❌ Fallidas:       {failed}")
    print(f"  📊 Total:          {last_round}")
    print("=" * 50)

if __name__ == "__main__":
    materialize_all()

