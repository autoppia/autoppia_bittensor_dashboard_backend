#!/usr/bin/env python3
"""
Script de pruebas completo para /api/v1/tasks/with-solutions
Migrado desde scripts/bash/test_tasks_with_solutions.sh
"""

import json
import sys
import urllib.parse
from typing import Dict, Any, Optional

import httpx

# Colores ANSI
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
NC = "\033[0m"  # No Color

API_URL = "http://localhost:8080/api/v1/tasks/with-solutions"
API_KEY = "AIagent2025"


def print_header(text: str) -> None:
    """Imprime un encabezado con formato."""
    print(f"{GREEN}{'=' * 50}{NC}")
    print(f"{GREEN}{text}{NC}")
    print(f"{GREEN}{'=' * 50}{NC}")
    print()


def print_test(num: str, description: str) -> None:
    """Imprime el número y descripción de un test."""
    print(f"{YELLOW}{num}  {description}{NC}")
    print("-" * 50)


def print_success(message: str) -> None:
    """Imprime un mensaje de éxito."""
    print(f"{GREEN}✓{NC} {message}")


def print_error(message: str) -> None:
    """Imprime un mensaje de error."""
    print(f"{RED}✗{NC} {message}")


def print_warning(message: str) -> None:
    """Imprime un mensaje de advertencia."""
    print(f"{YELLOW}⚠{NC} {message}")


def make_request(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Hace una petición HTTP al endpoint."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(API_URL, params=params)
            if response.status_code == 200:
                return response.json()
            return None
    except Exception as e:
        print_error(f"Error en la petición: {e}")
        return None


def test_no_filters() -> bool:
    """Test 1: Sin filtros (todas las tareas)."""
    print_test("1️⃣", "Sin filtros (todas las tareas)")
    
    params = {"key": API_KEY, "limit": 5}
    data = make_request(params)
    
    if data and data.get("success") and data.get("data", {}).get("total") is not None:
        total = data["data"]["total"]
        print_success(f"OK - Total: {total} tareas")
        print("   Primeros 200 caracteres:")
        response_text = json.dumps(data, indent=2)
        print(f"   {response_text[:200]}...")
        print()
        return True
    else:
        print_error("Error")
        print()
        return False


def test_successful_tasks() -> bool:
    """Test 2: Tareas exitosas (success=true)."""
    print_test("2️⃣", "Tareas exitosas (success=true)")
    
    params = {"key": API_KEY, "success": "true", "limit": 5}
    data = make_request(params)
    
    if data and data.get("success") and data.get("data", {}).get("total") is not None:
        total = data["data"]["total"]
        print_success(f"OK - Total: {total} tareas exitosas")
        print()
        return True
    else:
        print_error("Error")
        print()
        return False


def test_failed_tasks() -> bool:
    """Test 3: Tareas fallidas (success=false)."""
    print_test("3️⃣", "Tareas fallidas (success=false)")
    
    params = {"key": API_KEY, "success": "false", "limit": 5}
    data = make_request(params)
    
    if data and data.get("success") and data.get("data", {}).get("total") is not None:
        total = data["data"]["total"]
        print_success(f"OK - Total: {total} tareas fallidas")
        print()
        return True
    else:
        print_error("Error")
        print()
        return False


def test_website_filter() -> bool:
    """Test 4: Filtrar por website (autocinema)."""
    print_test("4️⃣", "Filtro por website (autocinema)")
    
    params = {"key": API_KEY, "website": "autocinema", "limit": 5}
    data = make_request(params)
    
    if data and data.get("success") and data.get("data", {}).get("total") is not None:
        total = data["data"]["total"]
        print_success(f"OK - Total: {total} tareas de autocinema")
        print()
        return True
    else:
        print_error("Error")
        print()
        return False


def test_website_and_success() -> bool:
    """Test 5: Website + success (autocinema exitosas)."""
    print_test("5️⃣", "Website + success (autocinema exitosas)")
    
    params = {"key": API_KEY, "website": "autocinema", "success": "true", "limit": 5}
    data = make_request(params)
    
    if data and data.get("success") and data.get("data", {}).get("total") is not None:
        total = data["data"]["total"]
        print_success(f"OK - Total: {total} tareas exitosas de autocinema")
        print()
        return True
    else:
        print_error("Error")
        print()
        return False


def test_web_version_filter() -> bool:
    """Test 5b: Filtro por webVersion."""
    print_test("5️⃣b", "Filtro por webVersion")
    
    # Primero obtenemos una tarea para ver qué webVersion tiene
    sample_params = {"key": API_KEY, "limit": 1}
    sample_data = make_request(sample_params)
    
    if not sample_data or not sample_data.get("data", {}).get("tasks"):
        print_warning("No se encontró webVersion en las tareas de muestra, saltando test")
        print()
        return False
    
    web_version = sample_data["data"]["tasks"][0].get("task", {}).get("webVersion")
    
    if not web_version or web_version in ("null", "None"):
        print_warning("No se encontró webVersion en las tareas de muestra, saltando test")
        print()
        return False
    
    # Hacer petición con el filtro webVersion
    params = {"key": API_KEY, "webVersion": web_version, "limit": 5}
    data = make_request(params)
    
    if data and data.get("success") and data.get("data", {}).get("total") is not None:
        total = data["data"]["total"]
        print_success(f"OK - Total: {total} tareas con webVersion={web_version}")
        
        # Verificar que todas las tareas tienen el mismo webVersion
        tasks = data.get("data", {}).get("tasks", [])
        versions = [
            t.get("task", {}).get("webVersion")
            for t in tasks
            if t.get("task", {}).get("webVersion")
        ]
        
        if all(v == web_version for v in versions):
            print_success(f"  Todas las tareas tienen webVersion={web_version}")
        else:
            print_warning("  Algunas tareas no tienen el webVersion esperado")
        
        print()
        return True
    else:
        print_error("Error")
        print()
        return False


def test_website_and_web_version() -> bool:
    """Test 6: Website + webVersion (filtro combinado)."""
    print_test("6️⃣", "Website + webVersion (filtro combinado)")
    
    # Primero obtenemos una tarea de autozone para ver qué webVersion tiene
    sample_params = {"key": API_KEY, "website": "autozone", "limit": 1}
    sample_data = make_request(sample_params)
    
    if not sample_data or not sample_data.get("data", {}).get("tasks"):
        print_warning("No se encontró webVersion en las tareas de autozone, saltando test")
        print()
        return False
    
    web_version = sample_data["data"]["tasks"][0].get("task", {}).get("webVersion")
    
    if not web_version or web_version in ("null", "None"):
        print_warning("No se encontró webVersion en las tareas de autozone, saltando test")
        print()
        return False
    
    # Hacer petición con ambos filtros
    params = {"key": API_KEY, "website": "autozone", "webVersion": web_version, "limit": 5}
    data = make_request(params)
    
    if data and data.get("success") and data.get("data", {}).get("total") is not None:
        total = data["data"]["total"]
        print_success(f"OK - Total: {total} tareas de autozone con webVersion={web_version}")
        print()
        return True
    else:
        print_error("Error")
        print()
        return False


def test_sorting() -> bool:
    """Test 7: Con ordenamiento (created_at_desc)."""
    print_test("7️⃣", "Con ordenamiento (created_at_desc)")
    
    params = {"key": API_KEY, "sort": "created_at_desc", "limit": 3}
    data = make_request(params)
    
    if data and data.get("success") and data.get("data", {}).get("total") is not None:
        total = data["data"]["total"]
        print_success(f"OK - Total: {total} tareas (ordenadas por fecha desc)")
        print()
        return True
    else:
        print_error("Error")
        print()
        return False


def test_full_structure() -> bool:
    """Test 8: Estructura completa (1 tarea)."""
    print_test("8️⃣", "Estructura completa (1 tarea)")
    
    params = {"key": API_KEY, "success": "true", "limit": 1}
    data = make_request(params)
    
    if data:
        print(json.dumps(data, indent=2)[:2000])  # Primeros 2000 caracteres
        print()
        return True
    else:
        print_error("Error")
        print()
        return False


def test_no_api_key() -> bool:
    """Test 9: Sin API key (debe devolver 422)."""
    print_test("9️⃣", "Sin API key (debe devolver 422)")
    
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(API_URL, params={"limit": 1})
            if response.status_code == 422:
                print_success("OK - Devuelve 422 como esperado")
                print()
                return True
            else:
                print_error(f"Error: Esperaba 422, recibió {response.status_code}")
                print()
                return False
    except Exception as e:
        print_error(f"Error en la petición: {e}")
        print()
        return False


def main() -> None:
    """Ejecuta todos los tests."""
    print_header("🚀 Testing /with-solutions endpoint")
    
    results = []
    
    results.append(("Test 1", test_no_filters()))
    results.append(("Test 2", test_successful_tasks()))
    results.append(("Test 3", test_failed_tasks()))
    results.append(("Test 4", test_website_filter()))
    results.append(("Test 5", test_website_and_success()))
    results.append(("Test 5b", test_web_version_filter()))
    results.append(("Test 6", test_website_and_web_version()))
    results.append(("Test 7", test_sorting()))
    results.append(("Test 8", test_full_structure()))
    results.append(("Test 9", test_no_api_key()))
    
    print_header("✅ Tests completados")
    
    # Resumen
    passed = sum(1 for _, result in results if result)
    total = len(results)
    print(f"Tests pasados: {passed}/{total}")
    
    if passed == total:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
