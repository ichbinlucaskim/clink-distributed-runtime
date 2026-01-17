# Poetry Dependency Management & Docker Development Strategy

This guide outlines best practices for managing Python dependencies with Poetry and optimizing Docker-based development workflows. It addresses common misconceptions, establishes clear principles, and provides actionable strategies for efficient development.

## Section 1: Solving the "Dependency Hell" in Poetry

### Context: Why Poetry Appears "Sensitive"

Poetry is not inherently "sensitive"—it enforces strict dependency resolution to maintain reproducibility and stability in the Python ecosystem. The Python package ecosystem has complex interdependencies where different libraries may require conflicting versions of the same dependency. Poetry's strictness prevents silent failures and ensures that your application's dependency graph is mathematically consistent.

When conflicts arise, it indicates a fundamental incompatibility that would surface at runtime if not caught during dependency resolution. Poetry's role is to surface these issues early, not to create them.

### Principle 1: Explicit Constraints (Upper/Lower Bounds)

#### Problem Statement

Using loose version specifications (e.g., `*`, `>=`, or simply `poetry add package`) creates ambiguity in dependency resolution. Consider a scenario:

- **Library A** requires `numpy < 2.0.0` (for legacy compatibility)
- **Library B** requires `numpy >= 2.0.0` (uses new APIs)

Without explicit constraints in your `pyproject.toml`, Poetry may select a version that satisfies immediate dependencies but breaks indirect ones, or it may fail to resolve at all. Loose constraints also make your build non-reproducible across time—what works today may break tomorrow when new package versions are released.

#### Solution: Version Constraints in pyproject.toml

Always specify version constraints using Poetry's caret (`^`) or tilde (`~`) operators, or explicit range syntax. This communicates intent and allows Poetry to make informed decisions.

**Caret Syntax (`^`):**
- `^2.1.0` allows `>=2.1.0, <3.0.0`
- Appropriate for accepting minor and patch updates within a major version

**Tilde Syntax (`~`):**
- `~2.1.0` allows `>=2.1.0, <2.2.0`
- Appropriate for accepting only patch updates within a minor version

**Explicit Ranges:**
- `>=2.1.0,<2.5.0` for precise control
- Use when you have specific compatibility requirements

#### Command Example

```bash
# Good: Explicit version constraint
poetry add "pandas^2.1"

# Better: Even more specific
poetry add "pandas^2.1.0"

# Avoid: Loose or no constraint
poetry add pandas
```

#### Verification

After adding dependencies, inspect `pyproject.toml` to verify constraints are present. Run `poetry lock --no-update` to validate that all constraints can be satisfied without updating packages.

### Principle 2: Environment Isolation (Groups)

#### Problem Statement

Mixing production and development dependencies in a single environment leads to:

1. **Conflict Risk**: Development tools (e.g., `pytest`, `black`, `mypy`) often have different dependency requirements than production libraries (e.g., `fastapi`, `uvicorn`, `redis`). Combining them increases the resolution surface area and potential conflicts.

2. **Blown Image Sizes**: Production Docker images include unnecessary development tooling, increasing build time, transfer time, and attack surface.

3. **Deployment Complexity**: Production environments receive packages they don't need, complicating dependency auditing and security scanning.

#### Solution: Dependency Groups

Poetry's dependency groups (introduced in Poetry 1.2.0) allow you to categorize dependencies:

- **Production dependencies**: Core runtime requirements (`fastapi`, `uvicorn`, `redis`, `pydantic`)
- **Development dependencies**: Tooling and testing frameworks (`pytest`, `black`, `isort`, `mypy`)
- **Optional groups**: Additional categories like `docs`, `benchmarking`, or `monitoring`

#### Implementation

**Adding Development Dependencies:**

```bash
# Add to the dev group
poetry add --group dev pytest
poetry add --group dev black
poetry add --group dev mypy

# Or add multiple at once
poetry add --group dev pytest black mypy
```

**pyproject.toml Structure:**

```toml
[tool.poetry.dependencies]
python = "^3.11"
fastapi = "^0.115.0"
uvicorn = "^0.32.1"
redis = "^5.2.0"

[tool.poetry.group.dev.dependencies]
pytest = "^9.0.2"
black = "^25.12.0"
mypy = "^1.0.0"
```

**Installation Commands:**

```bash
# Install only production dependencies
poetry install --only main

# Install production + development dependencies (default)
poetry install

# Install specific groups
poetry install --with dev,docs
```

#### Dockerfile Optimization

In your Dockerfile, use group filtering to exclude development dependencies:

```dockerfile
# Production build
RUN poetry install --only=main --no-root

# Development build (if needed in Docker)
RUN poetry install --with dev --no-root
```

### Principle 3: The Sanctity of the Lock File

#### Context: poetry.lock as Single Source of Truth

The `poetry.lock` file is a complete, deterministic snapshot of your dependency tree. It contains:

- Exact versions of all direct dependencies
- Exact versions of all transitive (indirect) dependencies
- Hash digests for each package
- Source repository information

This file ensures that every developer and every deployment environment uses identical package versions, eliminating "works on my machine" scenarios.

#### The Resolution Process

When you run `poetry install`, Poetry:

1. Reads `pyproject.toml` to understand your declared dependencies
2. Reads `poetry.lock` to find the exact versions to install
3. If `poetry.lock` is missing or incompatible, Poetry resolves dependencies from scratch

#### When to Update the Lock File

**Scenario A: Adding a New Dependency**

```bash
# Step 1: Add the package (updates pyproject.toml)
poetry add "pandas^2.1"

# Step 2: Lock file is automatically updated
# Step 3: Commit both pyproject.toml and poetry.lock
```

**Scenario B: Updating Existing Dependencies**

```bash
# Dry-run first to see what would change
poetry update --dry-run

# Update specific package
poetry update pandas

# Or update all packages (use with caution)
poetry update
```

**Scenario C: Resolving Conflicts Locally**

```bash
# When you suspect lock file issues, resolve locally
poetry lock --no-update

# This re-resolves dependencies using current pyproject.toml constraints
# without updating package versions
```

#### Best Practices for Lock File Management

1. **Always Commit poetry.lock**: Treat it as a source file, not a generated artifact. It ensures reproducibility.

2. **Resolve Locally Before Committing**: Run `poetry lock --no-update` before committing `pyproject.toml` changes to ensure the lock file is in sync. This prevents breaking team builds.

3. **Never Manually Edit poetry.lock**: It's a generated file. Use Poetry commands to modify it.

4. **Version Control Both Files**: Commit both `pyproject.toml` and `poetry.lock` together as an atomic unit.

5. **Use CI/CD to Validate**: In your CI pipeline, run `poetry lock --check` to ensure the lock file is up-to-date and hasn't been manually modified.

#### Common Pitfalls

- **Forgetting to commit poetry.lock**: Other developers or CI/CD will resolve from scratch, potentially getting different versions.
- **Updating lock file without testing**: Always run your test suite after updating dependencies.
- **Ignoring lock file conflicts**: If `poetry install` fails, don't delete `poetry.lock`—investigate and resolve the conflict.

## Section 2: Docker Development Efficiency Strategy

### Core Question: Do We Need to Rebuild Every Time?

The answer is **no**. Understanding when to rebuild versus when to rely on volume mounts is crucial for efficient development workflows. Rebuilding Docker images is expensive in terms of time and resources; it should be reserved for infrastructure-level changes.

### Scenario A: Library/Dependency Changes → Rebuild Required

#### Why Rebuild is Necessary

When you modify `pyproject.toml` (adding, removing, or updating dependencies), you're changing the Docker image's filesystem at the system level. Specifically:

- Packages are installed to `/usr/local/lib/python3.x/site-packages/`
- System-level configuration files may be modified
- Native extensions and compiled libraries are installed
- The Python interpreter's import path changes

These changes are baked into the Docker image layers and cannot be achieved through volume mounting alone.

#### Docker Layer Caching Efficiency

While rebuilding is necessary for dependency changes, Docker's layer caching makes it efficient:

**Typical Dockerfile Structure:**

```dockerfile
FROM python:3.11-slim

# Copy dependency files (changes infrequently)
COPY pyproject.toml poetry.lock ./

# Install dependencies (cached unless pyproject.toml/poetry.lock change)
RUN pip install poetry && \
    poetry config virtualenvs.create false && \
    poetry install --only=main --no-root

# Copy application code (changes frequently)
COPY . /app
WORKDIR /app
```

**Caching Behavior:**

1. **If only `src/main.py` changes**: Docker reuses all cached layers up to `COPY . /app`, rebuilding only the final layer. This is still a rebuild, but it's minimal.

2. **If `pyproject.toml` changes**: Docker invalidates the cache from the `COPY pyproject.toml poetry.lock ./` step onward. It re-executes `poetry install`, which is where the real time cost lies.

3. **If nothing changes**: Docker uses the cached image entirely, resulting in near-instant startup.

**Why This Is Faster Than Manual Installation:**

- Docker layer caching means `poetry install` only runs when dependencies actually change
- The installation happens in an isolated, reproducible environment
- The resulting layer can be shared across multiple containers

#### Frequency and Workflow

Dependency changes are relatively infrequent—typically 1-2 times per day, or even less in mature projects. The workflow should be:

```bash
# 1. Update dependencies locally (test first)
poetry add "new-package^1.0"
poetry lock --no-update
poetry install

# 2. Test locally
pytest

# 3. Rebuild Docker image
docker-compose build clink-runtime

# 4. Restart services
docker-compose up -d
```

### Scenario B: Code Changes → No Rebuild (Volume Mount)

#### Why Rebuild is Unnecessary

Changing application code (e.g., `src/main.py`, `src/core/checkpointer.py`) does not require a rebuild because:

1. **Code is Not Baked into Image**: Application code can be mounted as a volume, making it external to the image.

2. **No System-Level Changes**: Code changes don't affect installed packages, system libraries, or the Python interpreter.

3. **Instant Updates**: Volume mounts provide "mirror mode"—changes on the host filesystem are immediately reflected in the container.

#### Mechanism: Docker Compose Volume Mounts

**docker-compose.yml Configuration:**

```yaml
services:
  clink-runtime:
    build: .
    container_name: clink_core_runtime
    ports:
      - "8000:8000"
    volumes:
      # Mount source code for hot-reloading
      - ./src:/app/src
      # Mount tests for development
      - ./tests:/app/tests
      # Optional: Mount configuration files
      - ./pyproject.toml:/app/pyproject.toml:ro
    environment:
      - PYTHONPATH=/app
    command: python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

**How It Works:**

1. **Host → Container Mapping**: `./src:/app/src` creates a bidirectional bind mount. Files in `./src` on your host machine are accessible at `/app/src` in the container.

2. **Real-Time Synchronization**: When you save `src/main.py` on your host, the change is immediately visible in the container's `/app/src/main.py`.

3. **Application Auto-Reload**: Using `--reload` flag with Uvicorn (or similar for other frameworks) watches for file changes and automatically restarts the application server.

#### Result: Mirror Mode Development

With volume mounts configured, your development workflow becomes:

1. **Edit code locally** in your IDE (e.g., `src/api/main.py`)
2. **Save the file** (Ctrl+S / Cmd+S)
3. **Uvicorn detects the change** (via file system watcher)
4. **Application reloads automatically** (typically within 1-2 seconds)
5. **Test the change** by hitting your API endpoint

No Docker commands required. No rebuild. No manual restart.

#### Best Practices for Volume Mounts

1. **Mount Only What's Necessary**: Avoid mounting the entire project root if you only need `src/` and `tests/`. This reduces overhead and prevents mounting sensitive files.

2. **Read-Only Mounts for Config**: Mark configuration files as read-only (`:ro`) if they shouldn't be modified by the container:
   ```yaml
   - ./config.yml:/app/config.yml:ro
   ```

3. **Exclude Build Artifacts**: Use `.dockerignore` to prevent unnecessary files from being copied during build, and ensure volume mounts don't expose build artifacts.

4. **Watch for Permission Issues**: On Linux, volume mounts may create files owned by root. Use user mapping if needed:
   ```yaml
   user: "${UID}:${GID}"
   ```

5. **Performance Considerations**: On macOS and Windows, Docker Desktop uses a VM with file system translation, which can be slower than native Linux. Consider using Docker's "cached" or "delegated" volume options for better performance.

#### Example: Daily Development Workflow

```bash
# Morning: Start services (rebuild only if dependencies changed)
docker-compose up -d

# During the day: Edit code in IDE
# - Save src/api/main.py
# - Uvicorn auto-reloads
# - Test immediately

# No Docker commands needed for code changes

# End of day: Stop services
docker-compose down
```

### Scenario C: The Hybrid Shortcut (Manual Edit + Lock Generation)

#### Use Case

When you know the specific version needed or want to avoid the overhead of installing packages on the host machine, this hybrid approach provides the most efficient workflow for Docker-centric development. This method is particularly useful when:

- You're working primarily in Docker and don't need packages installed locally
- You know the exact package name and version constraint you want
- You want to minimize local environment pollution
- Your team's workflow is Docker-first

#### Workflow

**Step 1: Manual Edit**

Open `pyproject.toml` and manually add or edit the dependency line in the appropriate section. For production dependencies, add to `[tool.poetry.dependencies]`; for development dependencies, add to `[tool.poetry.group.dev.dependencies]`.

**Example:**

```toml
[tool.poetry.dependencies]
python = "^3.11"
fastapi = "^0.115.0"
uvicorn = "^0.32.1"
redis = "^5.2.0"
langchain-ollama = "^0.1.0"  # Newly added
```

**Step 2: Generate Lock File (Host Side)**

Run `poetry lock` in your terminal from the project root:

```bash
poetry lock
```

**Why this step is critical:**

- `poetry lock` updates `poetry.lock` to match your `pyproject.toml` changes without installing any packages locally
- This ensures the lock file committed to Git is accurate and reflects the dependency resolution
- Poetry resolves the full dependency tree (including transitive dependencies) and records exact versions in the lock file
- If there are dependency conflicts, they're caught at this stage before Docker build time

**Step 3: Rebuild Docker**

Run `docker-compose up -d --build` to rebuild and restart the service:

```bash
docker-compose up -d --build
```

**What happens:**

1. Docker copies the updated `pyproject.toml` and `poetry.lock` into the container
2. The `poetry install` step in your Dockerfile reads the lock file
3. Poetry installs the new packages (and any required transitive dependencies) inside the container
4. The container restarts with the new dependencies available

#### Benefit

This method is significantly faster than `poetry add` because it skips the local installation step. The `poetry add` command performs three operations:

1. Updates `pyproject.toml`
2. Generates/updates `poetry.lock`
3. Installs packages in your local environment

In a Docker-centric workflow, step 3 is unnecessary overhead. The hybrid approach performs only steps 1 and 2 on the host, then lets Docker handle installation in the container environment where it will actually run.

**Performance Comparison:**

- **`poetry add`**: Updates files + installs locally (~30-60 seconds for a typical package)
- **Hybrid approach**: Updates files + generates lock (~5-10 seconds) + Docker rebuild (~30-60 seconds, but parallel with other tasks)

While the total time may be similar, the hybrid approach keeps your local environment clean and aligns with a containerized workflow philosophy.

#### When to Use This Approach

**Favorable conditions:**

- Docker-first development workflow
- Minimal local Python environment setup
- Team standardizes on containerized environments
- Adding multiple dependencies in quick succession

**Less favorable conditions:**

- You need packages installed locally for IDE autocomplete or type checking
- You're actively developing against the new package's API locally
- Your local environment is already set up and maintained

#### Complete Workflow Example

```bash
# 1. Edit pyproject.toml manually
# Add: langchain-ollama = "^0.1.0" under [tool.poetry.dependencies]

# 2. Generate lock file (no installation)
poetry lock

# 3. Rebuild and restart Docker
docker-compose up -d --build

# 4. Verify in container (optional)
docker-compose exec clink-runtime python -c "import langchain_ollama; print('OK')"

# 5. Commit both files
git add pyproject.toml poetry.lock
git commit -m "Add langchain-ollama dependency"
```

#### Important Considerations

1. **Always commit both files**: After using this workflow, commit both `pyproject.toml` and `poetry.lock` together. The lock file ensures reproducibility.

2. **Test in container**: Since you're not installing locally, test your code changes in the Docker container to ensure the new dependency works as expected.

3. **Dependency conflicts**: If `poetry lock` fails due to conflicts, resolve them before proceeding with the Docker build. The error messages will guide you to incompatible constraints.

4. **IDE support**: If you need IDE autocomplete or type checking for the new package, you may need to run `poetry install` locally or configure your IDE to use the Docker container's Python interpreter.

## Summary

### Poetry Best Practices

1. **Use explicit version constraints**: Always specify versions in `pyproject.toml` (e.g., `^2.1.0`) to avoid ambiguity and ensure reproducibility.

2. **Isolate dependencies with groups**: Separate production and development dependencies using `poetry add --group dev` to reduce conflicts and optimize Docker images.

3. **Respect the lock file**: Always commit `poetry.lock`, resolve conflicts locally with `poetry lock --no-update` before committing, and never manually edit the lock file.

4. **Validate changes**: Use `poetry lock --check` in CI/CD and `poetry update --dry-run` to preview changes.

### Docker Development Strategy

1. **Rebuild only when `pyproject.toml` changes**: Dependency modifications require a rebuild because packages are installed at the system level in the Docker image.

2. **Rely on volume mounts for code changes**: Mount `src/` and `tests/` directories as volumes to enable instant updates without rebuilding. Configure your application server (e.g., Uvicorn) with auto-reload.

3. **Leverage Docker layer caching**: Structure your Dockerfile so dependency installation is a separate layer that only rebuilds when `pyproject.toml` or `poetry.lock` changes.

4. **Optimize your workflow**: Rebuild infrequently (1-2 times per day for dependencies), code continuously with volume mounts, and use auto-reload for instant feedback.

### Quick Reference Commands

```bash
# Poetry: Add production dependency
poetry add "package^version"

# Poetry: Add development dependency
poetry add --group dev "package^version"

# Poetry: Update lock file (dry-run first)
poetry update --dry-run
poetry lock --no-update

# Docker: Rebuild after dependency changes
docker-compose build clink-runtime

# Docker: Start services (with volume mounts for code)
docker-compose up -d

# Docker: View logs to verify auto-reload
docker-compose logs -f clink-runtime

# Hybrid: Manual edit + lock generation (Docker-first workflow)
# 1. Edit pyproject.toml manually
# 2. poetry lock
# 3. docker-compose up -d --build
```

By following these principles, you'll maintain a stable, reproducible dependency environment while maximizing development velocity through efficient Docker workflows.