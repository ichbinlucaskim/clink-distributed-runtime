"""Pytest configuration and fixtures."""
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Explicitly import fixtures from chaos_fixture module
# This ensures they are registered and available to all tests
from tests.chaos_fixture import chaos, docker_client


def pytest_configure(config):
    """
    Configure pytest markers for test categorization.
    
    Registers custom markers to avoid warnings when using markers like '@pytest.mark.integration'.
    """
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test (requires Docker)"
    )
