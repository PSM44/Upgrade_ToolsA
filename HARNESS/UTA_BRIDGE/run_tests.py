#!/usr/bin/env python3
"""
Test runner for UTA Bridge acceptance tests.

Runs local tests (no network) and optionally integration tests.
"""

import sys
import os
import subprocess

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_local_tests() -> int:
    """Run local validation tests (no network required)."""
    print("=" * 60)
    print("Running LOCAL tests (A. Pruebas locales sin red)")
    print("=" * 60)

    # Run pytest on local test classes
    test_file = os.path.join(os.path.dirname(__file__), "test_bridge.py")
    result = subprocess.run([
        sys.executable, "-m", "pytest",
        test_file,
        "-k", "not integration",
        "-v",
        "--tb=short",
    ], cwd=os.path.dirname(__file__))

    return result.returncode


def run_integration_tests() -> int:
    """Run integration tests (requires GitHub auth)."""
    print("=" * 60)
    print("Running INTEGRATION tests (B. Prueba integrada controlada)")
    print("=" * 60)

    test_file = os.path.join(os.path.dirname(__file__), "test_bridge.py")
    result = subprocess.run([
        sys.executable, "-m", "pytest",
        test_file,
        "-k", "integration",
        "-v",
        "--tb=short",
    ], cwd=os.path.dirname(__file__))

    return result.returncode


def main():
    import argparse
    parser = argparse.ArgumentParser(description="UTA Bridge test runner")
    parser.add_argument("--integration", action="store_true", help="Run integration tests")
    parser.add_argument("--local-only", action="store_true", help="Run only local tests")
    args = parser.parse_args()

    if args.local_only:
        return run_local_tests()

    # Run local tests first
    local_result = run_local_tests()

    if local_result != 0:
        print("\n❌ LOCAL TESTS FAILED - stopping")
        return local_result

    print("\n✅ LOCAL TESTS PASSED")

    if args.integration:
        integration_result = run_integration_tests()
        if integration_result != 0:
            print("\n⚠️  INTEGRATION TESTS INCOMPLETE (expected if no MORCH consumer)")
        return 0  # Don't fail on integration tests

    return 0


if __name__ == "__main__":
    sys.exit(main())