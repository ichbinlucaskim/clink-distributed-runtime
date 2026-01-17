"""
CPE-10: Chaos Testing Fixtures for Docker Container Management.

Provides reusable pytest fixtures for killing and restarting containers
during chaos testing scenarios.
"""
import time
import logging
import httpx
from typing import Optional
import pytest
import docker
from docker.errors import NotFound, APIError

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def docker_client():
    """
    Provides a Docker client instance for container management.
    """
    try:
        client = docker.from_env()
        client.ping()  # Verify connection
        yield client
    except Exception as e:
        pytest.skip(f"Docker client unavailable: {e}")


class ChaosFixture:
    """
    Reusable fixture for chaos testing container operations.
    
    Provides methods to kill, start, and wait for container health.
    """
    
    def __init__(self, docker_client: docker.DockerClient, base_url: str = "http://localhost:8000"):
        """
        Initialize the chaos fixture.
        
        Args:
            docker_client: Docker SDK client instance
            base_url: Base URL of the FastAPI application (for health checks)
        """
        self.client = docker_client
        self.base_url = base_url
    
    def kill_container(self, container_name: str, signal: str = "SIGKILL") -> None:
        """
        Kill a running container by name.
        
        Args:
            container_name: Name of the container to kill
            signal: Signal to send (default: SIGKILL for immediate termination)
        
        Raises:
            NotFound: If container doesn't exist
            APIError: If Docker API call fails
        """
        try:
            container = self.client.containers.get(container_name)
            container.kill(signal=signal)
            logger.info(f"Killed container: {container_name} with signal {signal}")
            
            # Wait for container to stop
            container.wait(timeout=5)
            logger.info(f"Container {container_name} has stopped")
            
        except NotFound:
            logger.warning(f"Container {container_name} not found")
            raise
        except APIError as e:
            logger.error(f"Failed to kill container {container_name}: {e}")
            raise
    
    def start_container(self, container_name: str, wait_healthy: bool = True, timeout: int = 30) -> None:
        """
        Start a stopped container by name.
        
        Args:
            container_name: Name of the container to start
            wait_healthy: If True, wait for the container/app to be healthy before returning
            timeout: Maximum seconds to wait for health check
        
        Raises:
            NotFound: If container doesn't exist
            APIError: If Docker API call fails
        """
        try:
            container = self.client.containers.get(container_name)
            container.start()
            logger.info(f"Started container: {container_name}")
            
            if wait_healthy:
                self.wait_for_health(container_name, timeout=timeout)
                
        except NotFound:
            logger.warning(f"Container {container_name} not found")
            raise
        except APIError as e:
            logger.error(f"Failed to start container {container_name}: {e}")
            raise
    
    def wait_for_health(self, container_name: str, timeout: int = 30) -> bool:
        """
        Wait for container to be healthy and FastAPI app to be ready.
        
        Checks both:
        1. Docker container status (must be 'running')
        2. FastAPI /health endpoint response (must return 200 with status='healthy')
        
        Args:
            container_name: Name of the container
            timeout: Maximum seconds to wait
        
        Returns:
            bool: True if healthy, False if timeout exceeded
        
        Raises:
            NotFound: If container doesn't exist
        """
        start_time = time.time()
        
        try:
            container = self.client.containers.get(container_name)
        except NotFound:
            logger.error(f"Container {container_name} not found during health check")
            raise
        
        logger.info(f"Waiting for {container_name} to be healthy (timeout: {timeout}s)...")
        
        consecutive_healthy_checks = 0
        required_healthy_checks = 2  # Require 2 consecutive successful checks
        
        while (time.time() - start_time) < timeout:
            # Check Docker container status first
            try:
                container.reload()
                if container.status != "running":
                    consecutive_healthy_checks = 0  # Reset on status change
                    time.sleep(0.5)
                    continue
            except Exception as e:
                logger.debug(f"Error checking container status: {e}")
                time.sleep(0.5)
                continue
            
            # Check FastAPI health endpoint
            try:
                response = httpx.get(f"{self.base_url}/health", timeout=2.0)
                if response.status_code == 200:
                    health_data = response.json()
                    if health_data.get("status") == "healthy":
                        consecutive_healthy_checks += 1
                        if consecutive_healthy_checks >= required_healthy_checks:
                            elapsed = time.time() - start_time
                            logger.info(f"Container {container_name} is healthy (took {elapsed:.2f}s, {consecutive_healthy_checks} checks)")
                            return True
                    else:
                        consecutive_healthy_checks = 0  # Reset if status is not healthy
            except (httpx.RequestError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
                consecutive_healthy_checks = 0  # Reset on any HTTP error
                logger.debug(f"Health endpoint not ready yet: {type(e).__name__}")
            
            time.sleep(0.5)
        
        elapsed = time.time() - start_time
        logger.warning(f"Health check timeout for {container_name} after {elapsed:.2f}s")
        return False
    
    def get_container_status(self, container_name: str) -> Optional[dict]:
        """
        Get current container status.
        
        Args:
            container_name: Name of the container
        
        Returns:
            dict: Container status info or None if not found
        """
        try:
            container = self.client.containers.get(container_name)
            container.reload()
            return {
                "id": container.id,
                "status": container.status,
                "name": container.name,
                "image": container.image.tags[0] if container.image.tags else None
            }
        except NotFound:
            return None
    
    def get_container_logs(self, container_name: str, tail: int = 20) -> str:
        """
        Get container logs for debugging.
        
        Args:
            container_name: Name of the container
            tail: Number of lines to retrieve from the end
        
        Returns:
            str: Container logs
        """
        try:
            container = self.client.containers.get(container_name)
            logs = container.logs(tail=tail, timestamps=True).decode('utf-8')
            return logs
        except Exception as e:
            logger.error(f"Failed to get logs for {container_name}: {e}")
            return f"Error retrieving logs: {e}"
    
    def is_container_running(self, container_name: str) -> bool:
        """
        Check if container is currently running.
        
        Refreshes container state from Docker API to ensure accurate status.
        
        Args:
            container_name: Name of the container
        
        Returns:
            bool: True if running, False otherwise
        """
        try:
            container = self.client.containers.get(container_name)
            container.reload()  # Refresh state from Docker API
            return container.status == "running"
        except NotFound:
            return False
        except Exception as e:
            logger.debug(f"Error checking container status: {e}")
            return False


@pytest.fixture(scope="function")
def chaos(docker_client):
    """
    Pytest fixture providing ChaosFixture instance for chaos testing.
    
    Usage:
        def test_chaos_scenario(chaos):
            chaos.kill_container("clink_core_runtime")
            chaos.start_container("clink_core_runtime")
    """
    return ChaosFixture(docker_client, base_url="http://localhost:8000")
